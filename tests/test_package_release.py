"""Unit tests for DevAI-OS release ISO splitting, checksums, and notes generation."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from package_release import (
    IsoFileSplitter,
    ReleaseNotesFormatter,
    ReleasePackageConfig,
    ReleasePackager,
    Sha256Calculator,
)


class TestSha256Calculator:
    """Tests streaming SHA256 digest computation."""

    def test_hash_file_matches_standard_sha256(self, tmp_path: Path) -> None:
        """Verify chunked hashing produces the exact SHA256 digest."""
        payload = b"devai-os-kernel-and-rootfs-bytes" * 1024
        target = tmp_path / "sample.bin"
        target.write_bytes(payload)

        calculator = Sha256Calculator(buffer_size=512)
        actual = calculator.hash_file(target)
        expected = hashlib.sha256(payload).hexdigest()

        assert actual == expected


class TestIsoFileSplitter:
    """Tests binary ISO splitting into sequential .part-XX chunks."""

    def test_split_creates_exact_chunks_and_reassembles_identically(
        self,
        tmp_path: Path,
    ) -> None:
        """Verify splitting and concatenating parts restores the exact original bytes."""
        original_bytes = bytes(range(256)) * 20  # 5120 bytes
        iso_path = tmp_path / "devai-os-v1.0.0.iso"
        iso_path.write_bytes(original_bytes)

        output_dir = tmp_path / "parts"
        splitter = IsoFileSplitter(chunk_size_bytes=2000, buffer_size=512)
        parts = splitter.split(iso_path, output_dir)

        assert [p.name for p in parts] == [
            "devai-os-v1.0.0.iso.part-00",
            "devai-os-v1.0.0.iso.part-01",
            "devai-os-v1.0.0.iso.part-02",
        ]
        assert [p.stat().st_size for p in parts] == [2000, 2000, 1120]

        reassembled = b"".join(p.read_bytes() for p in parts)
        assert reassembled == original_bytes

    def test_split_rejects_empty_iso(self, tmp_path: Path) -> None:
        """Verify zero-byte ISO files raise ValueError and leave no empty part files."""
        empty_iso = tmp_path / "empty.iso"
        empty_iso.write_bytes(b"")
        output_dir = tmp_path / "parts"

        splitter = IsoFileSplitter(chunk_size_bytes=1024)
        with pytest.raises(ValueError, match="empty"):
            splitter.split(empty_iso, output_dir)

        assert list(output_dir.iterdir()) == []

    def test_split_rejects_non_positive_chunk_size(self) -> None:
        """Verify constructor enforces positive chunk sizes."""
        with pytest.raises(ValueError, match="greater than zero"):
            IsoFileSplitter(chunk_size_bytes=0)


class TestReleaseNotesFormatter:
    """Tests Markdown release notes rendering."""

    def test_render_includes_tag_part_count_and_verification_commands(self) -> None:
        """Verify release notes contain reassembly and sha256sum commands."""
        rendered = ReleaseNotesFormatter.render(
            release_tag="v1.2.0",
            iso_name="devai-os-v1.2.0.iso",
            part_count=3,
        )

        assert "## DevAI-OS v1.2.0" in rendered
        assert "split into 3 archive part(s)" in rendered
        assert "cat devai-os-v1.2.0.iso.part-* > devai-os-v1.2.0.iso" in rendered
        assert "sha256sum -c SHA256SUMS.txt --ignore-missing" in rendered


class TestReleasePackager:
    """Tests end-to-end release asset packaging."""

    def test_package_writes_parts_checksums_and_removes_source_when_requested(
        self,
        tmp_path: Path,
    ) -> None:
        """Verify full packaging workflow produces valid checksums and cleans up source."""
        raw_data = b"DevAI-OS-ISO-BLOCK-" * 500
        iso_path = tmp_path / "staging" / "devai-os-v1.0.0.iso"
        iso_path.parent.mkdir(parents=True)
        iso_path.write_bytes(raw_data)

        expected_full_hash = hashlib.sha256(raw_data).hexdigest()
        output_dir = tmp_path / "release-assets"
        notes_path = tmp_path / "release-notes.md"

        config = ReleasePackageConfig(
            iso_path=iso_path,
            output_dir=output_dir,
            release_tag="v1.0.0",
            notes_path=notes_path,
            chunk_size_bytes=4096,
            remove_source_iso=True,
        )

        result = ReleasePackager().package(config)

        assert not iso_path.exists()
        assert result.full_iso_sha256 == expected_full_hash
        assert len(result.part_paths) == 3
        assert result.checksums_path.is_file()
        assert result.notes_path.is_file()

        checksum_lines = result.checksums_path.read_text(encoding="utf-8").splitlines()
        assert len(checksum_lines) == 4
        assert checksum_lines[-1] == f"{expected_full_hash}  devai-os-v1.0.0.iso"

        for line, part_path in zip(checksum_lines[:-1], result.part_paths, strict=True):
            recorded_hash, recorded_name = line.split("  ")
            assert recorded_name == part_path.name
            assert recorded_hash == hashlib.sha256(part_path.read_bytes()).hexdigest()
