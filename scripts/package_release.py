#!/usr/bin/env python3
"""Packages a verified DevAI-OS ISO into split release assets with SHA256 checksums."""

from __future__ import annotations

import argparse
import hashlib
import sys
from dataclasses import dataclass
from pathlib import Path

DEFAULT_CHUNK_SIZE_BYTES: int = 1900 * 1024 * 1024
READ_BUFFER_SIZE_BYTES: int = 8 * 1024 * 1024


@dataclass(frozen=True)
class ReleasePackageConfig:
    """Immutable configuration for splitting an ISO and generating release metadata."""

    iso_path: Path
    output_dir: Path
    release_tag: str
    notes_path: Path
    chunk_size_bytes: int = DEFAULT_CHUNK_SIZE_BYTES
    remove_source_iso: bool = False


@dataclass(frozen=True)
class PackagedArtifactSet:
    """Represents the generated split files, checksum manifest, and release notes."""

    iso_name: str
    full_iso_sha256: str
    part_paths: tuple[Path, ...]
    checksums_path: Path
    notes_path: Path


class Sha256Calculator:
    """Computes streaming SHA256 digests without loading large ISOs into memory."""

    def __init__(self, buffer_size: int = READ_BUFFER_SIZE_BYTES) -> None:
        self._buffer_size = buffer_size

    def hash_file(self, file_path: Path) -> str:
        """Return the lowercase hexadecimal SHA256 digest of a file."""
        digest = hashlib.sha256()
        with file_path.open("rb") as handle:
            while chunk := handle.read(self._buffer_size):
                digest.update(chunk)
        return digest.hexdigest()


class IsoFileSplitter:
    """Splits a binary ISO file into fixed-size numbered parts."""

    def __init__(
        self,
        chunk_size_bytes: int = DEFAULT_CHUNK_SIZE_BYTES,
        buffer_size: int = READ_BUFFER_SIZE_BYTES,
    ) -> None:
        if chunk_size_bytes <= 0:
            raise ValueError("chunk_size_bytes must be greater than zero.")
        self._chunk_size_bytes = chunk_size_bytes
        self._buffer_size = buffer_size

    def split(self, iso_path: Path, output_dir: Path) -> tuple[Path, ...]:
        """Write sequential .part-XX files into output_dir and return their paths."""
        if not iso_path.is_file():
            raise FileNotFoundError(f"ISO file does not exist: {iso_path}")

        output_dir.mkdir(parents=True, exist_ok=True)
        part_paths: list[Path] = []
        part_index = 0

        with iso_path.open("rb") as source:
            while True:
                part_path = output_dir / f"{iso_path.name}.part-{part_index:02d}"
                bytes_written = self._write_single_chunk(source, part_path)
                if bytes_written == 0:
                    part_path.unlink(missing_ok=True)
                    break
                part_paths.append(part_path)
                part_index += 1

        if not part_paths:
            raise ValueError(f"ISO file is empty and cannot be split: {iso_path}")

        return tuple(part_paths)

    def _write_single_chunk(self, source: object, destination: Path) -> int:
        """Copy up to chunk_size_bytes from an open binary stream into destination."""
        if not hasattr(source, "read"):
            raise TypeError("source must expose a binary read method.")

        remaining = self._chunk_size_bytes
        total_written = 0

        with destination.open("wb") as target:
            while remaining > 0:
                read_limit = min(self._buffer_size, remaining)
                data: bytes = source.read(read_limit)
                if not data:
                    break
                target.write(data)
                written = len(data)
                total_written += written
                remaining -= written

        return total_written


class ReleaseNotesFormatter:
    """Renders Markdown release notes with verification and reassembly instructions."""

    @staticmethod
    def render(release_tag: str, iso_name: str, part_count: int) -> str:
        """Build formatted Markdown content for GitHub Releases."""
        lines = [
            f"## DevAI-OS {release_tag}",
            "",
            "Verified Debian 13 (Trixie) developer and AI live ISO image.",
            "",
            f"This release is split into {part_count} archive part(s) to stay under GitHub asset limits.",
            "",
            "### Reassemble and Verify",
            "",
            f"Download all `{iso_name}.part-*` files and `SHA256SUMS.txt` into one folder, then run:",
            "",
            "```bash",
            f"cat {iso_name}.part-* > {iso_name}",
            "sha256sum -c SHA256SUMS.txt --ignore-missing",
            "```",
            "",
        ]
        return "\n".join(lines)


class ReleasePackager:
    """Coordinates ISO hashing, splitting, manifest writing, and notes generation."""

    def __init__(
        self,
        hasher: Sha256Calculator | None = None,
        splitter: IsoFileSplitter | None = None,
        formatter: ReleaseNotesFormatter | None = None,
    ) -> None:
        self._hasher = hasher or Sha256Calculator()
        self._splitter = splitter or IsoFileSplitter()
        self._formatter = formatter or ReleaseNotesFormatter()

    def package(self, config: ReleasePackageConfig) -> PackagedArtifactSet:
        """Execute the full release packaging workflow and return generated paths."""
        if not config.iso_path.is_file():
            raise FileNotFoundError(f"ISO not found: {config.iso_path}")

        iso_name = config.iso_path.name
        full_iso_hash = self._hasher.hash_file(config.iso_path)

        splitter = IsoFileSplitter(chunk_size_bytes=config.chunk_size_bytes)
        part_paths = splitter.split(config.iso_path, config.output_dir)

        if config.remove_source_iso:
            config.iso_path.unlink()

        checksums_path = self._write_checksums(
            output_dir=config.output_dir,
            part_paths=part_paths,
            iso_name=iso_name,
            full_iso_hash=full_iso_hash,
        )

        notes_content = self._formatter.render(
            release_tag=config.release_tag,
            iso_name=iso_name,
            part_count=len(part_paths),
        )
        config.notes_path.parent.mkdir(parents=True, exist_ok=True)
        config.notes_path.write_text(notes_content, encoding="utf-8")

        return PackagedArtifactSet(
            iso_name=iso_name,
            full_iso_sha256=full_iso_hash,
            part_paths=part_paths,
            checksums_path=checksums_path,
            notes_path=config.notes_path,
        )

    def _write_checksums(
        self,
        output_dir: Path,
        part_paths: tuple[Path, ...],
        iso_name: str,
        full_iso_hash: str,
    ) -> Path:
        """Write GNU coreutils compatible SHA256SUMS.txt covering parts and full ISO."""
        manifest_lines: list[str] = []
        for part_path in part_paths:
            part_hash = self._hasher.hash_file(part_path)
            manifest_lines.append(f"{part_hash}  {part_path.name}")

        manifest_lines.append(f"{full_iso_hash}  {iso_name}")
        checksums_path = output_dir / "SHA256SUMS.txt"
        checksums_path.write_text("\n".join(manifest_lines) + "\n", encoding="utf-8")
        return checksums_path


def parse_args() -> ReleasePackageConfig:
    """Parse CLI arguments into a ReleasePackageConfig instance."""
    parser = argparse.ArgumentParser(
        description="Split a verified DevAI-OS ISO and generate SHA256 checksums and release notes.",
    )
    parser.add_argument(
        "--iso", required=True, type=Path, help="Path to verified .iso file"
    )
    parser.add_argument(
        "--output-dir",
        default=Path("release-assets"),
        type=Path,
        help="Directory where split ISO parts and SHA256SUMS.txt are written",
    )
    parser.add_argument(
        "--tag",
        required=True,
        help="Release version tag (for example v1.0.0)",
    )
    parser.add_argument(
        "--notes-out",
        default=Path("release-notes.md"),
        type=Path,
        help="Destination path for generated Markdown release notes",
    )
    parser.add_argument(
        "--chunk-mib",
        default=1900,
        type=int,
        help="Maximum size of each split part in MiB (default: 1900)",
    )
    parser.add_argument(
        "--remove-source",
        action="store_true",
        help="Delete the original unsplit ISO after splitting to save runner disk space",
    )
    args = parser.parse_args()

    if args.chunk_mib <= 0:
        parser.error("--chunk-mib must be a positive integer.")

    return ReleasePackageConfig(
        iso_path=args.iso,
        output_dir=args.output_dir,
        release_tag=args.tag,
        notes_path=args.notes_out,
        chunk_size_bytes=args.chunk_mib * 1024 * 1024,
        remove_source_iso=args.remove_source,
    )


def main() -> int:
    """Entrypoint for release asset packaging."""
    config = parse_args()
    packager = ReleasePackager()
    result = packager.package(config)

    print(f"Packaged {result.iso_name} into {len(result.part_paths)} part(s):")
    for part in result.part_paths:
        print(f"  - {part}")
    print(f"Checksums: {result.checksums_path}")
    print(f"Notes:     {result.notes_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
