"""Streaming reader for the supported Killzone 2 PSARC revision."""

from __future__ import annotations

import hashlib
import io
import math
import zlib
from dataclasses import dataclass, field
from pathlib import Path

from .errors import FormatError

MAX_ENTRY_COUNT = 10_000_000
MAX_MANIFEST_SIZE = 64 * 1024 * 1024


@dataclass(slots=True)
class PsarcEntry:
    index: int
    digest: bytes
    first_block_index: int
    uncompressed_size: int
    data_offset: int
    path: str | None = None
    aliases: list[str] = field(default_factory=list)

    @property
    def physical_key(self) -> tuple[int, int, int]:
        return (self.data_offset, self.uncompressed_size, self.first_block_index)


@dataclass(slots=True)
class Psarc:
    path: Path
    version: int
    compression: bytes
    toc_length: int
    entry_size: int
    block_size: int
    flags: int
    entries: tuple[PsarcEntry, ...]
    block_lengths: tuple[int, ...]
    archive_size: int
    by_path: dict[str, PsarcEntry] = field(default_factory=dict)
    entries_by_path: dict[str, list[PsarcEntry]] = field(default_factory=dict)

    @classmethod
    def open(cls, path: str | Path, *, load_manifest: bool = True) -> Psarc:
        archive_path = Path(path)
        archive_size = archive_path.stat().st_size
        with archive_path.open("rb") as stream:
            header = _read_exact(stream, 0x20, str(archive_path), 0)
            if header[:4] != b"PSAR":
                raise FormatError(
                    "invalid PSARC magic",
                    source=str(archive_path),
                    offset=0,
                    expected=b"PSAR",
                    observed=header[:4],
                )
            version = int.from_bytes(header[4:8], "big")
            compression = header[8:12]
            toc_length = int.from_bytes(header[12:16], "big")
            entry_size = int.from_bytes(header[16:20], "big")
            entry_count = int.from_bytes(header[20:24], "big")
            block_size = int.from_bytes(header[24:28], "big")
            flags = int.from_bytes(header[28:32], "big")

            if version != 0x00010003:
                raise FormatError(
                    "unsupported PSARC version",
                    source=str(archive_path),
                    offset=4,
                    expected=0x00010003,
                    observed=version,
                )
            if compression != b"zlib":
                raise FormatError(
                    "unsupported PSARC compression",
                    source=str(archive_path),
                    offset=8,
                    expected=b"zlib",
                    observed=compression,
                )
            if entry_size != 30:
                raise FormatError(
                    "unsupported PSARC entry size",
                    source=str(archive_path),
                    offset=0x10,
                    expected=30,
                    observed=entry_size,
                )
            if not (1 <= entry_count <= MAX_ENTRY_COUNT):
                raise FormatError(
                    "invalid PSARC entry count",
                    source=str(archive_path),
                    offset=0x14,
                    expected=f"1..{MAX_ENTRY_COUNT}",
                    observed=entry_count,
                )
            fixed_toc_end = 0x20 + entry_count * entry_size
            if (
                block_size <= 0
                or toc_length < fixed_toc_end
                or toc_length > archive_size
            ):
                raise FormatError(
                    "invalid PSARC TOC bounds",
                    source=str(archive_path),
                    offset=0x0C,
                    observed=toc_length,
                )

            records = _read_exact(
                stream,
                entry_count * entry_size,
                str(archive_path),
                0x20,
            )
            entries: list[PsarcEntry] = []
            for index in range(entry_count):
                offset = index * entry_size
                record = records[offset : offset + entry_size]
                entries.append(
                    PsarcEntry(
                        index=index,
                        digest=record[:16],
                        first_block_index=int.from_bytes(record[16:20], "big"),
                        uncompressed_size=int.from_bytes(record[20:25], "big"),
                        data_offset=int.from_bytes(record[25:30], "big"),
                    )
                )

            width = max(1, math.ceil(math.log2(block_size) / 8))
            block_table_bytes = toc_length - fixed_toc_end
            if block_table_bytes % width:
                raise FormatError(
                    "misaligned PSARC block-length table",
                    source=str(archive_path),
                    offset=fixed_toc_end,
                    observed=block_table_bytes,
                )
            raw_lengths = _read_exact(
                stream,
                block_table_bytes,
                str(archive_path),
                fixed_toc_end,
            )
            block_lengths = tuple(
                int.from_bytes(raw_lengths[index : index + width], "big")
                for index in range(0, len(raw_lengths), width)
            )

        archive = cls(
            path=archive_path,
            version=version,
            compression=compression,
            toc_length=toc_length,
            entry_size=entry_size,
            block_size=block_size,
            flags=flags,
            entries=tuple(entries),
            block_lengths=block_lengths,
            archive_size=archive_size,
        )
        archive._validate_entry_ranges()
        if load_manifest:
            archive._load_manifest()
        return archive

    def _validate_entry_ranges(self) -> None:
        for entry in self.entries:
            if entry.data_offset > self.archive_size:
                raise FormatError(
                    "PSARC entry starts outside the archive",
                    source=str(self.path),
                    offset=entry.data_offset,
                    observed=entry.index,
                )
            block_count = (
                entry.uncompressed_size + self.block_size - 1
            ) // self.block_size
            if entry.first_block_index + block_count > len(self.block_lengths):
                raise FormatError(
                    "PSARC entry block range is out of bounds",
                    source=str(self.path),
                    observed=entry.index,
                )

    def _load_manifest(self) -> None:
        manifest_entry = self.entries[0]
        if manifest_entry.uncompressed_size > MAX_MANIFEST_SIZE:
            raise FormatError(
                "PSARC manifest is too large",
                source=str(self.path),
                observed=manifest_entry.uncompressed_size,
            )
        raw = self.read_entry(manifest_entry)
        try:
            paths = raw.decode("utf-8").splitlines()
        except UnicodeDecodeError as exc:
            raise FormatError(
                "PSARC manifest is not UTF-8",
                source=str(self.path),
                observed=str(exc),
            ) from exc
        if len(paths) != len(self.entries) - 1:
            raise FormatError(
                "PSARC manifest entry count mismatch",
                source=str(self.path),
                expected=len(self.entries) - 1,
                observed=len(paths),
            )

        physical: dict[tuple[int, int, int], PsarcEntry] = {}
        for entry, logical_path in zip(self.entries[1:], paths, strict=True):
            digest = hashlib.md5(logical_path.upper().encode("utf-8")).digest()
            if digest != entry.digest:
                raise FormatError(
                    "PSARC manifest path digest mismatch",
                    source=str(self.path),
                    expected=entry.digest.hex(),
                    observed=digest.hex(),
                )
            entry.path = logical_path
            key = logical_path.casefold()
            self.by_path.setdefault(key, entry)
            self.entries_by_path.setdefault(key, []).append(entry)
            canonical = physical.setdefault(entry.physical_key, entry)
            canonical.aliases.append(logical_path)

    def get(self, logical_path: str) -> PsarcEntry:
        try:
            return self.by_path[logical_path.casefold()]
        except KeyError as exc:
            raise KeyError(f"PSARC path not found: {logical_path}") from exc

    def read_entry(self, entry: PsarcEntry | str) -> bytes:
        record = self.get(entry) if isinstance(entry, str) else entry
        output = io.BytesIO()
        self.extract_to(record, output)
        return output.getvalue()

    def read_prefix(self, entry: PsarcEntry | str, size: int) -> bytes:
        if size < 0:
            raise ValueError("size cannot be negative")
        record = self.get(entry) if isinstance(entry, str) else entry
        output = io.BytesIO()
        self.extract_to(record, output, limit=size)
        return output.getvalue()

    def extract_to(
        self,
        entry: PsarcEntry,
        output: io.BufferedIOBase | io.BytesIO,
        *,
        limit: int | None = None,
    ) -> None:
        remaining = entry.uncompressed_size
        archive_offset = entry.data_offset
        block_index = entry.first_block_index
        written = 0

        with self.path.open("rb") as stream:
            while remaining:
                expected = min(self.block_size, remaining)
                if block_index >= len(self.block_lengths):
                    raise FormatError(
                        "PSARC block index is out of range",
                        source=str(self.path),
                        observed=block_index,
                    )
                encoded_size = self.block_lengths[block_index]
                stored_size = self.block_size if encoded_size == 0 else encoded_size
                if archive_offset + stored_size > self.archive_size:
                    raise FormatError(
                        "truncated PSARC block",
                        source=str(self.path),
                        offset=archive_offset,
                        expected=stored_size,
                        observed=max(0, self.archive_size - archive_offset),
                    )
                stream.seek(archive_offset)
                stored = _read_exact(
                    stream,
                    stored_size,
                    str(self.path),
                    archive_offset,
                )
                if encoded_size == 0 or stored_size == expected:
                    decoded = stored
                else:
                    try:
                        decoded = zlib.decompress(stored)
                    except zlib.error as exc:
                        raise FormatError(
                            "invalid zlib PSARC block",
                            source=str(self.path),
                            offset=archive_offset,
                            observed=str(exc),
                        ) from exc
                if len(decoded) != expected:
                    raise FormatError(
                        "invalid PSARC block output size",
                        source=str(self.path),
                        offset=archive_offset,
                        expected=expected,
                        observed=len(decoded),
                    )

                if limit is None:
                    output.write(decoded)
                elif written < limit:
                    chunk = decoded[: limit - written]
                    output.write(chunk)
                    written += len(chunk)
                    if written >= limit:
                        return

                remaining -= expected
                archive_offset += stored_size
                block_index += 1


def _read_exact(
    stream: io.BufferedReader,
    size: int,
    source: str,
    offset: int,
) -> bytes:
    data = stream.read(size)
    if len(data) != size:
        raise FormatError(
            "truncated file",
            source=source,
            offset=offset,
            expected=size,
            observed=len(data),
        )
    return data
