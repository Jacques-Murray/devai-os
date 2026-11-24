#!/usr/bin/env python3
"""Automated headless QEMU boot, toolchain, and privilege verifier for DevAI-OS."""

from __future__ import annotations

import argparse
import os
import select
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_REQUIRED_BINARIES: tuple[str, ...] = (
    "python3",
    "dotnet",
    "rustc",
    "flutter",
    "node",
    "adb",
    "sdkmanager",
    "code",
    "claude",
    "codex",
    "agy",
)

KVM_DEVICE_PATH: Path = Path("/dev/kvm")


@dataclass(frozen=True)
class BinaryCheckResult:
    """Represents the resolution and version check outcome for a single binary."""

    name: str
    resolved_path: str | None
    version_output: str | None
    passed: bool
    failure_reason: str | None = None


@dataclass(frozen=True)
class RuntimeCheckResult:
    """Represents an unprivileged runtime or SDK health check inside the live guest."""

    name: str
    passed: bool
    detail: str


@dataclass(frozen=True)
class SmokeTestSpec:
    """Credentials, binary list, and timeout settings for live guest verification."""

    username: str = "user"
    password: str = "live"
    binaries: tuple[str, ...] = DEFAULT_REQUIRED_BINARIES
    require_kvm_device: bool = True
    login_timeout_seconds: int = 30
    command_timeout_seconds: int = 150


@dataclass(frozen=True)
class BootTestConfig:
    """Immutable configuration for a QEMU ISO boot and smoke-test run."""

    iso_path: Path
    log_path: Path
    memory_mb: int = 4096
    cpus: int = 2
    boot_timeout_seconds: int = 240
    ovmf_code_path: Path = Path("/usr/share/OVMF/OVMF_CODE_4M.fd")
    boot_markers: tuple[str, ...] = (
        "Linux version",
        "systemd[1]:",
        "login:",
    )
    smoke_test: SmokeTestSpec = field(default_factory=SmokeTestSpec)


@dataclass
class SerialTranscript:
    """Buffers raw serial console bytes and exposes a rolling window for matching."""

    raw_chunks: list[str] = field(default_factory=list)
    tail_buffer: str = ""

    def append(self, text: str) -> None:
        """Record incoming serial text and maintain a bounded tail buffer."""
        self.raw_chunks.append(text)
        self.tail_buffer = (self.tail_buffer + text)[-24576:]

    def clear_tail(self) -> None:
        """Reset rolling match buffer before sending a new interactive command."""
        self.tail_buffer = ""

    def full_text(self) -> str:
        """Return the complete captured serial console output."""
        return "".join(self.raw_chunks)


class QemuCommandBuilder:
    """Constructs QEMU command-line arguments with automatic KVM detection."""

    def __init__(self, config: BootTestConfig, scratch_disk: Path) -> None:
        self._config = config
        self._scratch_disk = scratch_disk

    def build(self) -> list[str]:
        """Assemble the complete qemu-system-x86_64 invocation."""
        cmd = [
            "qemu-system-x86_64",
            "-m",
            str(self._config.memory_mb),
            "-smp",
            str(self._config.cpus),
            "-cdrom",
            str(self._config.iso_path),
            "-drive",
            f"file={self._scratch_disk},format=qcow2,if=virtio",
            "-boot",
            "d",
            "-nographic",
            "-no-reboot",
        ]

        if KVM_DEVICE_PATH.exists() and os.access(KVM_DEVICE_PATH, os.R_OK | os.W_OK):
            cmd.extend(["-enable-kvm", "-cpu", "host"])
        else:
            # qemu64 lacks SSE4.2/AVX2, which crashes Bun/Rust CLIs (claude, agy) with SIGILL.
            cmd.extend(["-cpu", "max"])

        if self._config.ovmf_code_path.exists():
            cmd.extend(
                [
                    "-drive",
                    f"if=pflash,format=raw,readonly=on,file={self._config.ovmf_code_path}",
                ]
            )

        return cmd


class SerialConsoleSession:
    """Manages bidirectional byte-stream I/O with a QEMU serial console."""

    def __init__(
        self,
        process: subprocess.Popen[bytes],
        transcript: SerialTranscript,
    ) -> None:
        if process.stdin is None or process.stdout is None:
            raise RuntimeError("QEMU process must be started with stdin and stdout pipes.")
        self._process = process
        self._stdin = process.stdin
        self._stdout_fd = process.stdout.fileno()
        self._transcript = transcript

    def send_line(self, text: str) -> None:
        """Write a command or credential terminated by carriage return to serial stdin."""
        payload = f"{text}\r".encode()
        self._stdin.write(payload)
        self._stdin.flush()

    def clear_tail(self) -> None:
        """Reset the underlying transcript rolling buffer before a new prompt."""
        self._transcript.clear_tail()

    def wait_for_all(self, markers: tuple[str, ...], timeout_seconds: int) -> bool:
        """Read serial output until all target substrings appear or timeout expires."""
        deadline = time.monotonic() + timeout_seconds
        seen: set[str] = set()

        while time.monotonic() < deadline:
            for marker in markers:
                if marker in self._transcript.tail_buffer:
                    seen.add(marker)
            if len(seen) == len(markers):
                return True

            if not self._read_available_chunk(deadline) and self._process.poll() is not None:
                break

        missing = sorted(set(markers) - seen)
        print(f"Timed out waiting for serial markers: {missing}", file=sys.stderr)
        return False

    def wait_for_any(self, candidates: tuple[str, ...], timeout_seconds: int) -> str | None:
        """Read serial output until any candidate substring appears."""
        deadline = time.monotonic() + timeout_seconds

        while time.monotonic() < deadline:
            for candidate in candidates:
                if candidate in self._transcript.tail_buffer:
                    return candidate

            if not self._read_available_chunk(deadline) and self._process.poll() is not None:
                break

        return None

    def _read_available_chunk(self, deadline: float) -> bool:
        """Poll stdout file descriptor and append decoded bytes to transcript."""
        remaining = max(0.05, min(0.5, deadline - time.monotonic()))
        ready, _, _ = select.select([self._stdout_fd], [], [], remaining)
        if not ready:
            return False

        chunk = os.read(self._stdout_fd, 4096)
        if not chunk:
            return False

        decoded = chunk.decode("utf-8", errors="replace")
        sys.stdout.write(decoded)
        sys.stdout.flush()
        self._transcript.append(decoded)
        return True


class GuestProbeScriptBuilder:
    """Builds a deterministic POSIX shell script to inspect guest binaries, SDKs, and access."""

    def __init__(self, binaries: tuple[str, ...]) -> None:
        self._binaries = binaries

    def build(self) -> str:
        """Construct a single-line shell command emitting structured check markers."""
        tools_arg = " ".join(self._binaries)
        return (
            "stty cols 250 >/dev/null 2>&1 || true; "
            "export CI=true DOTNET_NOLOGO=1 LC_ALL=C.UTF-8 LANG=C.UTF-8; "
            "source /etc/profile >/dev/null 2>&1 || true; "
            f"for bin in {tools_arg}; do "
            'resolved=$(command -v "$bin" 2>/dev/null || true); '
            'if [ -z "$resolved" ]; then '
            'echo "DEVAI_BIN:${bin}:MISSING::"; '
            "else "
            # Capture the binary's own exit status; a pipeline here would report tr's.
            'ver_out=$("$bin" --version </dev/null 2>&1); '
            "ver_status=$?; "
            "ver_raw=$(printf '%s\\n' \"$ver_out\" | head -n 1 | tr -d '\\r'); "
            'if [ "$ver_status" -ne 0 ]; then '
            'echo "DEVAI_BIN:${bin}:VERSION_FAIL:${resolved}:exit_${ver_status} ${ver_raw}"; '
            'elif [ -n "$ver_raw" ]; then '
            'echo "DEVAI_BIN:${bin}:OK:${resolved}:${ver_raw}"; '
            "else "
            'echo "DEVAI_BIN:${bin}:OK:${resolved}:(exit 0, no output)"; '
            "fi; "
            "fi; "
            "done; "
            "if command -v flutter >/dev/null 2>&1; then "
            "doctor_out=$(flutter doctor -v </dev/null 2>&1 | tr -d '\\r' || true); "
            "android_line=$(printf '%s\\n' \"$doctor_out\" | grep -E 'Android toolchain' | head -n 1 || true); "
            "if printf '%s\\n' \"$android_line\" | grep -q '\\[✓\\]'; then "
            'echo "DEVAI_RUNTIME:flutter_android:OK:${android_line}"; '
            'elif [ -z "$android_line" ]; then '
            "doctor_head=$(printf '%s\\n' \"$doctor_out\" | head -n 5 | tr '\\n' ' '); "
            'echo "DEVAI_RUNTIME:flutter_android:FAIL:Android toolchain section missing from flutter doctor: ${doctor_head}"; '
            "else "
            "license_line=$(printf '%s\\n' \"$doctor_out\" | grep -iE 'license|android-sdk' | head -n 1 || true); "
            'echo "DEVAI_RUNTIME:flutter_android:FAIL:${android_line} | ${license_line}"; '
            "fi; "
            "else "
            'echo "DEVAI_RUNTIME:flutter_android:FAIL:flutter binary not found"; '
            "fi; "
            "if docker info >/dev/null 2>&1; then "
            'echo "DEVAI_RUNTIME:docker:OK:docker daemon reachable without sudo"; '
            "else "
            'echo "DEVAI_RUNTIME:docker:FAIL:docker info failed for $(id -un) (groups: $(id -nG))"; '
            "fi; "
            "if [ -c /dev/kvm ] && [ -r /dev/kvm ] && [ -w /dev/kvm ]; then "
            'echo "DEVAI_RUNTIME:kvm:OK:/dev/kvm read-write accessible"; '
            "elif [ ! -e /dev/kvm ]; then "
            'echo "DEVAI_RUNTIME:kvm:MISSING:/dev/kvm device node not present in guest"; '
            "else "
            'echo "DEVAI_RUNTIME:kvm:FAIL:permission denied on /dev/kvm for $(id -un) (groups: $(id -nG))"; '
            "fi; "
            # Split so the serial echo of this command never contains the sentinel.
            'echo "DEVAI_SMOKE_""DONE"'
        )


class GuestSmokeResultParser:
    """Parses structured serial markers into typed binary and runtime results."""

    def __init__(self, spec: SmokeTestSpec) -> None:
        self._spec = spec

    def parse(
        self,
        serial_output: str,
    ) -> tuple[list[BinaryCheckResult], list[RuntimeCheckResult]]:
        """Extract binary and runtime outcomes from raw serial console output."""
        binary_map: dict[str, BinaryCheckResult] = {}
        runtime_map: dict[str, RuntimeCheckResult] = {}

        for raw_line in serial_output.splitlines():
            line = raw_line.strip()
            if line.startswith("DEVAI_BIN:"):
                parsed_bin = self._parse_binary_line(line)
                if parsed_bin is not None:
                    binary_map[parsed_bin.name] = parsed_bin
            elif line.startswith("DEVAI_RUNTIME:"):
                parsed_runtime = self._parse_runtime_line(line)
                if parsed_runtime is not None:
                    runtime_map[parsed_runtime.name] = parsed_runtime

        ordered_binaries = [
            binary_map.get(
                name,
                BinaryCheckResult(
                    name=name,
                    resolved_path=None,
                    version_output=None,
                    passed=False,
                    failure_reason="No check marker emitted",
                ),
            )
            for name in self._spec.binaries
        ]

        expected_runtime_checks = ("flutter_android", "docker", "kvm")
        ordered_runtime = [
            runtime_map.get(
                check_name,
                RuntimeCheckResult(
                    name=check_name,
                    passed=False,
                    detail=f"No {check_name} check marker emitted",
                ),
            )
            for check_name in expected_runtime_checks
        ]

        return ordered_binaries, ordered_runtime

    @staticmethod
    def _parse_binary_line(line: str) -> BinaryCheckResult | None:
        """Parse a single DEVAI_BIN:<name>:<status>:<path>:<version> line."""
        parts = line.split(":", maxsplit=4)
        if len(parts) < 5:
            return None

        _, name, status, resolved_path, detail = parts
        if status == "OK":
            return BinaryCheckResult(
                name=name,
                resolved_path=resolved_path,
                version_output=detail,
                passed=True,
            )
        if status == "MISSING":
            return BinaryCheckResult(
                name=name,
                resolved_path=None,
                version_output=None,
                passed=False,
                failure_reason="Not found in PATH",
            )
        return BinaryCheckResult(
            name=name,
            resolved_path=resolved_path or None,
            version_output=None,
            passed=False,
            failure_reason=f"--version failed ({detail})",
        )

    def _parse_runtime_line(self, line: str) -> RuntimeCheckResult | None:
        """Parse a single DEVAI_RUNTIME:<name>:<status>:<detail> line."""
        parts = line.split(":", maxsplit=3)
        if len(parts) < 4:
            return None

        _, name, status, detail = parts
        if status == "OK":
            return RuntimeCheckResult(name=name, passed=True, detail=detail)
        if name == "kvm" and status == "MISSING" and not self._spec.require_kvm_device:
            return RuntimeCheckResult(
                name=name,
                passed=True,
                detail=f"{detail} (allowed by --allow-missing-kvm)",
            )
        return RuntimeCheckResult(name=name, passed=False, detail=detail)


class GuestToolchainVerifier:
    """Logs into the live guest over serial and verifies binaries, versions, and access."""

    def __init__(self, session: SerialConsoleSession, spec: SmokeTestSpec) -> None:
        self._session = session
        self._spec = spec
        self._probe_builder = GuestProbeScriptBuilder(spec.binaries)
        self._parser = GuestSmokeResultParser(spec)

    def authenticate(self) -> bool:
        """Complete interactive username and password login on ttyS0."""
        self._session.send_line(self._spec.username)
        prompt = self._session.wait_for_any(
            ("Password:", "$ ", "# "),
            self._spec.login_timeout_seconds,
        )
        if prompt is None:
            print(
                "Failed to receive password or shell prompt after username.",
                file=sys.stderr,
            )
            return False

        if prompt == "Password:":
            self._session.clear_tail()
            self._session.send_line(self._spec.password)
            shell = self._session.wait_for_any(
                ("$ ", "# ", "Login incorrect"),
                self._spec.login_timeout_seconds,
            )
            if shell in (None, "Login incorrect"):
                print("Serial console authentication failed.", file=sys.stderr)
                return False

        return True

    def verify_environment(self, transcript: SerialTranscript) -> bool:
        """Run CLI version probes, flutter doctor, and Docker/KVM checks inside the guest."""
        transcript.clear_tail()
        self._session.send_line(self._probe_builder.build())

        if not self._session.wait_for_all(
            ("DEVAI_SMOKE_DONE",), self._spec.command_timeout_seconds
        ):
            print("Guest smoke test did not finish before timeout.", file=sys.stderr)
            return False

        binary_results, runtime_results = self._parser.parse(transcript.tail_buffer)
        self._print_summary(binary_results, runtime_results)

        all_binaries_ok = all(item.passed for item in binary_results)
        all_runtime_ok = all(item.passed for item in runtime_results)
        return all_binaries_ok and all_runtime_ok

    @staticmethod
    def _print_summary(
        binary_results: list[BinaryCheckResult],
        runtime_results: list[RuntimeCheckResult],
    ) -> None:
        """Print a readable pass/fail report for CI logs."""
        print("\n=== DevAI-OS CLI and Version Verification ===")
        for item in binary_results:
            if item.passed:
                print(
                    f"  [PASS] {item.name:<15} -> {item.resolved_path} ({item.version_output})",
                )
            else:
                print(f"  [FAIL] {item.name:<15} -> {item.failure_reason}")

        print("\n=== DevAI-OS SDK and Runtime Checks ===")
        for check in runtime_results:
            status = "PASS" if check.passed else "FAIL"
            print(f"  [{status}] {check.name:<15} -> {check.detail}")


class IsoBootVerifier:
    """Orchestrates QEMU execution, boot verification, and live toolchain checks."""

    def __init__(self, config: BootTestConfig) -> None:
        self._config = config

    def run(self) -> bool:
        """Boot the ISO, log in over serial console, and verify all required tools."""
        if not self._config.iso_path.is_file():
            raise FileNotFoundError(f"ISO not found: {self._config.iso_path}")

        with tempfile.TemporaryDirectory(prefix="devai-qemu-") as temp_dir:
            scratch_disk = Path(temp_dir) / "target.qcow2"
            self._create_scratch_disk(scratch_disk)
            cmd = QemuCommandBuilder(self._config, scratch_disk).build()
            return self._execute_session(cmd)

    @staticmethod
    def _create_scratch_disk(disk_path: Path) -> None:
        """Create a disposable 20 GB QCOW2 virtual disk for live-boot tests."""
        subprocess.run(
            ["qemu-img", "create", "-f", "qcow2", str(disk_path), "20G"],
            check=True,
            stdout=subprocess.DEVNULL,
        )

    def _execute_session(self, cmd: list[str]) -> bool:
        """Run QEMU and execute boot plus authenticated smoke tests."""
        transcript = SerialTranscript()
        process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=0,
        )

        try:
            session = SerialConsoleSession(process, transcript)
            if not session.wait_for_all(
                self._config.boot_markers,
                self._config.boot_timeout_seconds,
            ):
                return False

            guest_verifier = GuestToolchainVerifier(session, self._config.smoke_test)
            if not guest_verifier.authenticate():
                return False

            if not guest_verifier.verify_environment(transcript):
                return False

            session.send_line("sudo poweroff")
            return True
        finally:
            self._terminate_process(process)
            self._write_log(transcript.full_text())

    @staticmethod
    def _terminate_process(process: subprocess.Popen[bytes]) -> None:
        """Stop QEMU cleanly after verification completes."""
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)

    def _write_log(self, content: str) -> None:
        """Persist captured serial console logs for CI artifact upload."""
        self._config.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._config.log_path.write_text(content, encoding="utf-8")


def parse_args() -> BootTestConfig:
    """Parse CLI flags into a BootTestConfig instance."""
    parser = argparse.ArgumentParser(
        description="Verify DevAI-OS ISO boot, CLI versions, Flutter doctor, and Docker/KVM access in QEMU.",
    )
    parser.add_argument("--iso", required=True, type=Path, help="Path to built .iso image")
    parser.add_argument(
        "--log",
        default=Path("artifacts/qemu-boot.log"),
        type=Path,
        help="Destination file for captured serial console output",
    )
    parser.add_argument(
        "--timeout",
        default=240,
        type=int,
        help="Boot timeout in seconds before login prompt",
    )
    parser.add_argument("--username", default="user", help="Live session username")
    parser.add_argument("--password", default="live", help="Live session password")
    parser.add_argument(
        "--binaries",
        nargs="+",
        default=list(DEFAULT_REQUIRED_BINARIES),
        help="Space-separated list of binaries to verify in guest PATH",
    )
    parser.add_argument(
        "--allow-missing-kvm",
        action="store_true",
        help="Pass KVM check if /dev/kvm is absent on non-nested virtualization runners",
    )
    args = parser.parse_args()

    smoke_spec = SmokeTestSpec(
        username=args.username,
        password=args.password,
        binaries=tuple(args.binaries),
        require_kvm_device=not args.allow_missing_kvm,
    )

    return BootTestConfig(
        iso_path=args.iso,
        log_path=args.log,
        boot_timeout_seconds=args.timeout,
        smoke_test=smoke_spec,
    )


def main() -> int:
    """Entrypoint for CI execution."""
    if shutil.which("qemu-system-x86_64") is None:
        print("Error: qemu-system-x86_64 is not installed.", file=sys.stderr)
        return 1

    config = parse_args()
    verifier = IsoBootVerifier(config)
    return 0 if verifier.run() else 1


if __name__ == "__main__":
    sys.exit(main())
