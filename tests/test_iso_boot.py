"""Unit tests for DevAI-OS QEMU command construction, serial I/O, and output parsing."""

from __future__ import annotations

import contextlib
import io
import os
import threading
import time
from collections.abc import Generator
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from verify_iso_boot import (
    BootTestConfig,
    GuestProbeScriptBuilder,
    GuestSmokeResultParser,
    GuestToolchainVerifier,
    QemuCommandBuilder,
    SerialConsoleSession,
    SerialTranscript,
    SmokeTestSpec,
)


@pytest.fixture
def sample_config(tmp_path: Path) -> BootTestConfig:
    """Create a deterministic BootTestConfig pointing to temporary paths."""
    iso_file = tmp_path / "devai-os.iso"
    iso_file.write_bytes(b"mock-iso-header")
    ovmf_file = tmp_path / "OVMF_CODE_4M.fd"
    ovmf_file.write_bytes(b"mock-ovmf")

    return BootTestConfig(
        iso_path=iso_file,
        log_path=tmp_path / "qemu-boot.log",
        memory_mb=4096,
        cpus=2,
        ovmf_code_path=ovmf_file,
    )


@pytest.fixture
def mock_serial_pipe() -> Generator[tuple[SerialConsoleSession, SerialTranscript, io.BytesIO, int]]:
    """Provide a SerialConsoleSession wired to an in-memory stdin and real OS stdout pipe."""
    read_fd, write_fd = os.pipe()
    stdin_buffer = io.BytesIO()
    stdout_stream = os.fdopen(read_fd, "rb", buffering=0)

    mock_process = MagicMock()
    mock_process.stdin = stdin_buffer
    mock_process.stdout = stdout_stream
    mock_process.poll.return_value = None

    transcript = SerialTranscript()
    session = SerialConsoleSession(mock_process, transcript)

    try:
        yield session, transcript, stdin_buffer, write_fd
    finally:
        stdout_stream.close()
        with contextlib.suppress(OSError):
            os.close(write_fd)


class TestQemuCommandBuilder:
    """Tests QEMU command-line generation across KVM and UEFI configurations."""

    def test_build_enables_kvm_and_uefi_when_available(
        self,
        sample_config: BootTestConfig,
        tmp_path: Path,
    ) -> None:
        """Verify KVM acceleration and OVMF pflash flags appear when supported."""
        scratch_disk = tmp_path / "scratch.qcow2"
        builder = QemuCommandBuilder(sample_config, scratch_disk)

        with (
            patch.object(Path, "exists", return_value=True),
            patch("verify_iso_boot.os.access", return_value=True),
        ):
            cmd = builder.build()

        assert cmd[0] == "qemu-system-x86_64"
        assert "-enable-kvm" in cmd
        assert "host" in cmd
        assert "qemu64" not in cmd
        assert str(sample_config.iso_path) in cmd
        assert f"file={scratch_disk},format=qcow2,if=virtio" in cmd
        assert f"if=pflash,format=raw,readonly=on,file={sample_config.ovmf_code_path}" in cmd

    def test_build_falls_back_to_qemu64_without_kvm_or_ovmf(
        self,
        tmp_path: Path,
    ) -> None:
        """Verify software TCG CPU fallback and omission of missing OVMF firmware."""
        config = BootTestConfig(
            iso_path=tmp_path / "devai-os.iso",
            log_path=tmp_path / "boot.log",
            ovmf_code_path=tmp_path / "nonexistent-ovmf.fd",
        )
        builder = QemuCommandBuilder(config, tmp_path / "scratch.qcow2")

        with (
            patch.object(Path, "exists", return_value=False),
            patch("verify_iso_boot.os.access", return_value=False),
        ):
            cmd = builder.build()

        assert "-enable-kvm" not in cmd
        assert "qemu64" in cmd
        assert not any("if=pflash" in arg for arg in cmd)


class TestSerialConsoleSession:
    """Tests bidirectional serial console I/O and rolling transcript matching."""

    def test_send_line_writes_carriage_return_terminated_bytes(
        self,
        mock_serial_pipe: tuple[SerialConsoleSession, SerialTranscript, io.BytesIO, int],
    ) -> None:
        """Verify send_line encodes commands with carriage return and flushes."""
        session, _, stdin_buffer, _ = mock_serial_pipe

        session.send_line("uname -a")
        session.send_line("exit")

        assert stdin_buffer.getvalue() == b"uname -a\rexit\r"

    def test_wait_for_all_succeeds_across_chunked_serial_writes(
        self,
        mock_serial_pipe: tuple[SerialConsoleSession, SerialTranscript, io.BytesIO, int],
    ) -> None:
        """Verify wait_for_all accumulates chunks until every marker appears."""
        session, transcript, _, write_fd = mock_serial_pipe

        def emit_boot_stream() -> None:
            os.write(write_fd, b"[    0.000000] Linux version 6.12.0-amd64\r\n")
            time.sleep(0.05)
            os.write(
                write_fd,
                b"[    1.420000] systemd[1]: Reached target Graphical Interface.\r\n",
            )
            time.sleep(0.05)
            os.write(write_fd, b"devai-os login: ")

        writer = threading.Thread(target=emit_boot_stream, daemon=True)
        writer.start()

        matched = session.wait_for_all(
            ("Linux version", "systemd[1]:", "login:"),
            timeout_seconds=5,
        )
        writer.join(timeout=2)

        assert matched is True
        assert "devai-os login: " in transcript.full_text()

    def test_wait_for_any_and_clear_tail_reset_matching_window(
        self,
        mock_serial_pipe: tuple[SerialConsoleSession, SerialTranscript, io.BytesIO, int],
    ) -> None:
        """Verify clear_tail prevents stale markers from matching subsequent waits."""
        session, _, _, write_fd = mock_serial_pipe

        os.write(write_fd, b"Password: ")
        first_match = session.wait_for_any(("Password:", "$ "), timeout_seconds=2)
        assert first_match == "Password:"

        session.clear_tail()
        os.write(write_fd, b"\r\nuser@devai-os:~$ ")
        second_match = session.wait_for_any(("Password:", "$ "), timeout_seconds=2)
        assert second_match == "$ "

    def test_wait_for_all_returns_false_on_timeout(
        self,
        mock_serial_pipe: tuple[SerialConsoleSession, SerialTranscript, io.BytesIO, int],
    ) -> None:
        """Verify wait_for_all returns False when expected markers never arrive."""
        session, _, _, write_fd = mock_serial_pipe

        os.write(write_fd, b"Linux version 6.12.0\r\n")
        matched = session.wait_for_all(("Linux version", "login:"), timeout_seconds=1)

        assert matched is False


class TestGuestToolchainVerifierAuth:
    """Tests interactive serial console login flows inside GuestToolchainVerifier."""

    def test_authenticate_succeeds_with_username_and_password(
        self,
        mock_serial_pipe: tuple[SerialConsoleSession, SerialTranscript, io.BytesIO, int],
    ) -> None:
        """Verify standard username -> Password: -> shell prompt authentication."""
        session, _, stdin_buffer, write_fd = mock_serial_pipe
        spec = SmokeTestSpec(username="user", password="live", login_timeout_seconds=3)
        verifier = GuestToolchainVerifier(session, spec)

        def simulate_getty() -> None:
            time.sleep(0.05)
            os.write(write_fd, b"Password: ")
            time.sleep(0.05)
            os.write(write_fd, b"\r\nuser@devai-os:~$ ")

        responder = threading.Thread(target=simulate_getty, daemon=True)
        responder.start()

        authenticated = verifier.authenticate()
        responder.join(timeout=2)

        assert authenticated is True
        assert stdin_buffer.getvalue() == b"user\rlive\r"

    def test_authenticate_succeeds_when_shell_opens_without_password(
        self,
        mock_serial_pipe: tuple[SerialConsoleSession, SerialTranscript, io.BytesIO, int],
    ) -> None:
        """Verify immediate shell prompt after username succeeds without sending password."""
        session, _, stdin_buffer, write_fd = mock_serial_pipe
        spec = SmokeTestSpec(username="root", password="ignored", login_timeout_seconds=3)
        verifier = GuestToolchainVerifier(session, spec)

        os.write(write_fd, b"root@devai-os:~# ")
        authenticated = verifier.authenticate()

        assert authenticated is True
        assert stdin_buffer.getvalue() == b"root\r"

    def test_authenticate_fails_on_login_incorrect(
        self,
        mock_serial_pipe: tuple[SerialConsoleSession, SerialTranscript, io.BytesIO, int],
    ) -> None:
        """Verify Login incorrect response fails authentication immediately."""
        session, _, _, write_fd = mock_serial_pipe
        spec = SmokeTestSpec(username="user", password="wrong", login_timeout_seconds=3)
        verifier = GuestToolchainVerifier(session, spec)

        def simulate_rejected_login() -> None:
            time.sleep(0.05)
            os.write(write_fd, b"Password: ")
            time.sleep(0.05)
            os.write(write_fd, b"\r\nLogin incorrect\r\n")

        responder = threading.Thread(target=simulate_rejected_login, daemon=True)
        responder.start()

        authenticated = verifier.authenticate()
        responder.join(timeout=2)

        assert authenticated is False

    def test_authenticate_fails_when_password_prompt_times_out(
        self,
        mock_serial_pipe: tuple[SerialConsoleSession, SerialTranscript, io.BytesIO, int],
    ) -> None:
        """Verify silent console after sending username returns False on timeout."""
        session, _, _, _ = mock_serial_pipe
        spec = SmokeTestSpec(username="user", password="live", login_timeout_seconds=1)
        verifier = GuestToolchainVerifier(session, spec)

        assert verifier.authenticate() is False


class TestGuestProbeScriptBuilder:
    """Tests POSIX shell probe generation for live guest inspection."""

    def test_build_includes_all_binaries_and_runtime_checks(self) -> None:
        """Verify generated shell one-liner probes all requested tools and services."""
        builder = GuestProbeScriptBuilder(("python3", "flutter", "agy"))
        script = builder.build()

        assert "stty cols 250" in script
        assert "export CI=true DOTNET_NOLOGO=1" in script
        assert "source /etc/profile" in script
        assert "for bin in python3 flutter agy; do" in script
        assert "flutter doctor -v" in script
        assert "docker info" in script
        assert "/dev/kvm" in script
        assert script.endswith('echo "DEVAI_SMOKE_""DONE"')
        # The console echoes the typed command; the sentinel must only appear in output.
        assert "DEVAI_SMOKE_DONE" not in script


class TestGuestSmokeResultParser:
    """Tests structured marker parsing for binaries, Flutter doctor, Docker, and KVM."""

    def test_parse_all_passing_checks_with_colons_in_version(self) -> None:
        """Verify valid markers pass even when --version output contains colons."""
        spec = SmokeTestSpec(binaries=("rustc", "flutter"), require_kvm_device=True)
        parser = GuestSmokeResultParser(spec)

        serial_transcript = "\n".join(
            [
                "DEVAI_BIN:rustc:OK:/opt/rust/cargo/bin/rustc:rustc 1.85.0 (2026-01-10 14:22:00)",
                "DEVAI_BIN:flutter:OK:/opt/flutter/bin/flutter:Flutter 3.29.0 • channel stable",
                "DEVAI_RUNTIME:flutter_android:OK:[✓] Android toolchain - develop for Android devices (Android SDK version 35.0.0)",
                "DEVAI_RUNTIME:docker:OK:docker daemon reachable without sudo",
                "DEVAI_RUNTIME:kvm:OK:/dev/kvm read-write accessible",
                "DEVAI_SMOKE_DONE",
            ]
        )

        binaries, runtimes = parser.parse(serial_transcript)

        assert len(binaries) == 2
        assert all(item.passed for item in binaries)
        assert binaries[0].version_output == "rustc 1.85.0 (2026-01-10 14:22:00)"
        assert len(runtimes) == 3
        assert all(check.passed for check in runtimes)

    def test_parse_detects_missing_and_broken_binaries(self) -> None:
        """Verify missing binaries, failed --version exits, and unemitted markers fail."""
        spec = SmokeTestSpec(binaries=("python3", "codex", "agy"))
        parser = GuestSmokeResultParser(spec)

        serial_transcript = "\n".join(
            [
                "DEVAI_BIN:python3:MISSING::",
                "DEVAI_BIN:codex:VERSION_FAIL:/usr/local/bin/codex:exit_127",
                "DEVAI_RUNTIME:flutter_android:OK:[✓] Android toolchain - develop for Android devices",
                "DEVAI_RUNTIME:docker:OK:docker daemon reachable without sudo",
                "DEVAI_RUNTIME:kvm:OK:/dev/kvm read-write accessible",
            ]
        )

        binaries, _ = parser.parse(serial_transcript)
        by_name = {item.name: item for item in binaries}

        assert by_name["python3"].passed is False
        assert by_name["python3"].failure_reason == "Not found in PATH"

        assert by_name["codex"].passed is False
        assert by_name["codex"].resolved_path == "/usr/local/bin/codex"
        assert by_name["codex"].failure_reason == "--version failed (exit_127)"

        assert by_name["agy"].passed is False
        assert by_name["agy"].failure_reason == "No check marker emitted"

    def test_parse_detects_flutter_license_and_docker_failures(self) -> None:
        """Verify unaccepted Android licenses and Docker socket errors fail parsing."""
        spec = SmokeTestSpec(binaries=("flutter",), require_kvm_device=True)
        parser = GuestSmokeResultParser(spec)

        serial_transcript = "\n".join(
            [
                "DEVAI_BIN:flutter:OK:/opt/flutter/bin/flutter:Flutter 3.29.0",
                "DEVAI_RUNTIME:flutter_android:FAIL:[!] Android toolchain - develop for Android devices | Some Android licenses not accepted.",
                "DEVAI_RUNTIME:docker:FAIL:docker info failed for user (groups: user)",
                "DEVAI_RUNTIME:kvm:FAIL:permission denied on /dev/kvm for user (groups: user)",
            ]
        )

        _, runtimes = parser.parse(serial_transcript)
        by_name = {check.name: check for check in runtimes}

        assert by_name["flutter_android"].passed is False
        assert "licenses not accepted" in by_name["flutter_android"].detail
        assert by_name["docker"].passed is False
        assert by_name["kvm"].passed is False

    def test_parse_allows_missing_kvm_only_when_configured(self) -> None:
        """Verify --allow-missing-kvm permits absent /dev/kvm without masking permission errors."""
        transcript = "\n".join(
            [
                "DEVAI_BIN:python3:OK:/usr/bin/python3:Python 3.13.2",
                "DEVAI_RUNTIME:flutter_android:OK:[✓] Android toolchain",
                "DEVAI_RUNTIME:docker:OK:docker daemon reachable without sudo",
                "DEVAI_RUNTIME:kvm:MISSING:/dev/kvm device node not present in guest",
            ]
        )

        strict_parser = GuestSmokeResultParser(
            SmokeTestSpec(binaries=("python3",), require_kvm_device=True),
        )
        _, strict_runtimes = strict_parser.parse(transcript)
        strict_kvm = next(c for c in strict_runtimes if c.name == "kvm")
        assert strict_kvm.passed is False

        relaxed_parser = GuestSmokeResultParser(
            SmokeTestSpec(binaries=("python3",), require_kvm_device=False),
        )
        _, relaxed_runtimes = relaxed_parser.parse(transcript)
        relaxed_kvm = next(c for c in relaxed_runtimes if c.name == "kvm")
        assert relaxed_kvm.passed is True
        assert "--allow-missing-kvm" in relaxed_kvm.detail
