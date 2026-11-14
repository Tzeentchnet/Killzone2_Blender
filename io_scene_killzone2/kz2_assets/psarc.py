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
ISO_SECTOR_SIZE = 2048
MAX_ISO_DIRECTORY_SIZE = 64 * 1024 * 1024
ISO_DATA_PSARC_PATH = "PS3_GAME/USRDIR/data_ps3.psarc"


@dataclass(frozen=True, slots=True)
class _FileExtent:
    offset: int
    size: int


@dataclass(frozen=True, slots=True)
class _IsoDirectoryRecord:
    name: str
    extent: _FileExtent
    flags: int

    @property
    def is_directory(self) -> bool:
        return bool(self.flags & 0x02)

    @property
    def has_more_extents(self) -> bool:
        return bool(self.flags & 0x80)


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
    fingerprint: str
    _source_extents: tuple[_FileExtent, ...] = field(repr=False)
    embedded_path: str | None = None
    by_path: dict[str, PsarcEntry] = field(default_factory=dict)
    entries_by_path: dict[str, list[PsarcEntry]] = field(default_factory=dict)

    @classmethod
    def open(cls, path: str | Path, *, load_manifest: bool = True) -> Psarc:
        archive_path = Path(path)
        container_size = archive_path.stat().st_size
        with archive_path.open("rb") as stream:
            initial_header = _read_exact(
                stream,
                0x20,
                str(archive_path),
                0,
            )
            embedded_path = None
            source_extents: tuple[_FileExtent, ...]
            if initial_header[:4] == b"PSAR":
                source_extents = (_FileExtent(0, container_size),)
            elif archive_path.suffix.casefold() == ".iso":
                source_extents = _iso9660_file_extents(
                    stream,
                    container_size,
                    str(archive_path),
                    ISO_DATA_PSARC_PATH,
                )
                embedded_path = ISO_DATA_PSARC_PATH
            else:
                raise FormatError(
                    "invalid PSARC magic",
                    source=str(archive_path),
                    offset=0,
                    expected=b"PSAR",
                    observed=initial_header[:4],
                )
            archive_size = sum(extent.size for extent in source_extents)
            source_label = (
                str(archive_path)
                if embedded_path is None
                else f"{archive_path}!/{embedded_path}"
            )
            header = _read_source_exact(
                stream,
                source_extents,
                0,
                0x20,
                source_label,
            )
            if header[:4] != b"PSAR":
                raise FormatError(
                    (
                        "embedded data_ps3.psarc has invalid magic; "
                        "select a decrypted Killzone 2 ISO"
                    ),
                    source=source_label,
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
                    source=source_label,
                    offset=4,
                    expected=0x00010003,
                    observed=version,
                )
            if compression != b"zlib":
                raise FormatError(
                    "unsupported PSARC compression",
                    source=source_label,
                    offset=8,
                    expected=b"zlib",
                    observed=compression,
                )
            if entry_size != 30:
                raise FormatError(
                    "unsupported PSARC entry size",
                    source=source_label,
                    offset=0x10,
                    expected=30,
                    observed=entry_size,
                )
            if not (1 <= entry_count <= MAX_ENTRY_COUNT):
                raise FormatError(
                    "invalid PSARC entry count",
                    source=source_label,
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
                    source=source_label,
                    offset=0x0C,
                    observed=toc_length,
                )

            records = _read_source_exact(
                stream,
                source_extents,
                0x20,
                entry_count * entry_size,
                source_label,
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
                    source=source_label,
                    offset=fixed_toc_end,
                    observed=block_table_bytes,
                )
            raw_lengths = _read_source_exact(
                stream,
                source_extents,
                fixed_toc_end,
                block_table_bytes,
                source_label,
            )
            block_lengths = tuple(
                int.from_bytes(raw_lengths[index : index + width], "big")
                for index in range(0, len(raw_lengths), width)
            )
            fingerprint = hashlib.sha256(
                header + records + raw_lengths
            ).hexdigest()

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
            fingerprint=fingerprint,
            _source_extents=source_extents,
            embedded_path=embedded_path,
        )
        archive._validate_entry_ranges()
        if load_manifest:
            archive._load_manifest()
        return archive

    @property
    def source_label(self) -> str:
        if self.embedded_path is None:
            return str(self.path)
        return f"{self.path}!/{self.embedded_path}"

    def _validate_entry_ranges(self) -> None:
        for entry in self.entries:
            if entry.data_offset > self.archive_size:
                raise FormatError(
                    "PSARC entry starts outside the archive",
                    source=self.source_label,
                    offset=entry.data_offset,
                    observed=entry.index,
                )
            block_count = (
                entry.uncompressed_size + self.block_size - 1
            ) // self.block_size
            if entry.first_block_index + block_count > len(self.block_lengths):
                raise FormatError(
                    "PSARC entry block range is out of bounds",
                    source=self.source_label,
                    observed=entry.index,
                )

    def _load_manifest(self) -> None:
        manifest_entry = self.entries[0]
        if manifest_entry.uncompressed_size > MAX_MANIFEST_SIZE:
            raise FormatError(
                "PSARC manifest is too large",
                source=self.source_label,
                observed=manifest_entry.uncompressed_size,
            )
        raw = self.read_entry(manifest_entry)
        try:
            paths = raw.decode("utf-8").splitlines()
        except UnicodeDecodeError as exc:
            raise FormatError(
                "PSARC manifest is not UTF-8",
                source=self.source_label,
                observed=str(exc),
            ) from exc
        if len(paths) != len(self.entries) - 1:
            raise FormatError(
                "PSARC manifest entry count mismatch",
                source=self.source_label,
                expected=len(self.entries) - 1,
                observed=len(paths),
            )

        physical: dict[tuple[int, int, int], PsarcEntry] = {}
        for entry, logical_path in zip(self.entries[1:], paths, strict=True):
            digest = hashlib.md5(logical_path.upper().encode("utf-8")).digest()
            if digest != entry.digest:
                raise FormatError(
                    "PSARC manifest path digest mismatch",
                    source=self.source_label,
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
                        source=self.source_label,
                        observed=block_index,
                    )
                encoded_size = self.block_lengths[block_index]
                stored_size = self.block_size if encoded_size == 0 else encoded_size
                if archive_offset + stored_size > self.archive_size:
                    raise FormatError(
                        "truncated PSARC block",
                        source=self.source_label,
                        offset=archive_offset,
                        expected=stored_size,
                        observed=max(0, self.archive_size - archive_offset),
                    )
                stored = _read_source_exact(
                    stream,
                    self._source_extents,
                    archive_offset,
                    stored_size,
                    self.source_label,
                )
                if encoded_size == 0 or stored_size == expected:
                    decoded = stored
                else:
                    try:
                        decoded = zlib.decompress(stored)
                    except zlib.error as exc:
                        raise FormatError(
                            "invalid zlib PSARC block",
                            source=self.source_label,
                            offset=archive_offset,
                            observed=str(exc),
                        ) from exc
                if len(decoded) != expected:
                    raise FormatError(
                        "invalid PSARC block output size",
                        source=self.source_label,
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


def _iso9660_file_extents(
    stream: io.BufferedReader,
    container_size: int,
    source: str,
    member_path: str,
) -> tuple[_FileExtent, ...]:
    primary = None
    for sector_index in range(16, 16 + 256):
        descriptor = _read_physical_exact(
            stream,
            sector_index * ISO_SECTOR_SIZE,
            ISO_SECTOR_SIZE,
            container_size,
            source,
        )
        if descriptor[1:6] != b"CD001" or descriptor[6] != 1:
            continue
        descriptor_type = descriptor[0]
        if descriptor_type == 1:
            primary = descriptor
        if descriptor_type == 255:
            break
    if primary is None:
        raise FormatError(
            (
                "ISO9660 primary volume descriptor was not found; "
                "select a decrypted Killzone 2 ISO"
            ),
            source=source,
        )
    logical_block_size = _iso_both_endian_u16(
        primary,
        128,
        source,
    )
    if logical_block_size != ISO_SECTOR_SIZE:
        raise FormatError(
            "unsupported ISO logical block size",
            source=source,
            expected=ISO_SECTOR_SIZE,
            observed=logical_block_size,
        )
    root = _parse_iso_directory_record(
        primary,
        156,
        container_size,
        source,
    )
    if root is None or not root.is_directory:
        raise FormatError(
            "ISO9660 root directory record is invalid",
            source=source,
        )

    current_extents: tuple[_FileExtent, ...] = (root.extent,)
    components = tuple(
        component for component in member_path.replace("\\", "/").split("/")
        if component
    )
    for component_index, component in enumerate(components):
        records = _read_iso_directory(
            stream,
            current_extents,
            container_size,
            source,
        )
        is_directory = component_index < len(components) - 1
        matches = tuple(
            record
            for record in records
            if _normalized_iso_name(record.name) == component.casefold()
            and record.is_directory == is_directory
        )
        if not matches:
            raise FormatError(
                f"ISO does not contain {member_path}",
                source=source,
                observed=component,
            )
        _validate_iso_extent_chain(matches, source)
        current_extents = tuple(record.extent for record in matches)
    return current_extents


def _read_iso_directory(
    stream: io.BufferedReader,
    extents: tuple[_FileExtent, ...],
    container_size: int,
    source: str,
) -> tuple[_IsoDirectoryRecord, ...]:
    directory_size = sum(extent.size for extent in extents)
    if directory_size > MAX_ISO_DIRECTORY_SIZE:
        raise FormatError(
            "ISO directory is too large",
            source=source,
            expected=f"0..{MAX_ISO_DIRECTORY_SIZE}",
            observed=directory_size,
        )
    data = b"".join(
        _read_physical_exact(
            stream,
            extent.offset,
            extent.size,
            container_size,
            source,
        )
        for extent in extents
    )
    records: list[_IsoDirectoryRecord] = []
    offset = 0
    while offset < len(data):
        record_length = data[offset]
        if record_length == 0:
            offset = (
                (offset // ISO_SECTOR_SIZE) + 1
            ) * ISO_SECTOR_SIZE
            continue
        record = _parse_iso_directory_record(
            data,
            offset,
            container_size,
            source,
        )
        if record is None:
            raise FormatError(
                "ISO directory record is invalid",
                source=source,
                offset=offset,
            )
        records.append(record)
        offset += record_length
    return tuple(records)


def _parse_iso_directory_record(
    data: bytes,
    offset: int,
    container_size: int,
    source: str,
) -> _IsoDirectoryRecord | None:
    if offset >= len(data):
        return None
    record_length = data[offset]
    if record_length == 0:
        return None
    end = offset + record_length
    if record_length < 34 or end > len(data):
        raise FormatError(
            "truncated ISO directory record",
            source=source,
            offset=offset,
            expected=record_length,
            observed=max(0, len(data) - offset),
        )
    record = data[offset:end]
    identifier_length = record[32]
    if 33 + identifier_length > len(record):
        raise FormatError(
            "truncated ISO file identifier",
            source=source,
            offset=offset,
            expected=identifier_length,
            observed=max(0, len(record) - 33),
        )
    extent_sector = _iso_both_endian_u32(record, 2, source)
    extent_size = _iso_both_endian_u32(record, 10, source)
    extended_attribute_blocks = record[1]
    extent_offset = (
        extent_sector + extended_attribute_blocks
    ) * ISO_SECTOR_SIZE
    if extent_offset + extent_size > container_size:
        raise FormatError(
            "ISO file extent is outside the image",
            source=source,
            offset=extent_offset,
            expected=extent_size,
            observed=max(0, container_size - extent_offset),
        )
    if record[26] or record[27]:
        raise FormatError(
            "interleaved ISO files are unsupported",
            source=source,
            offset=offset,
        )
    identifier = record[33 : 33 + identifier_length]
    if identifier == b"\x00":
        name = "."
    elif identifier == b"\x01":
        name = ".."
    else:
        try:
            name = identifier.decode("ascii")
        except UnicodeDecodeError as exc:
            raise FormatError(
                "ISO file identifier is not ASCII",
                source=source,
                offset=offset,
                observed=str(exc),
            ) from exc
    return _IsoDirectoryRecord(
        name=name,
        extent=_FileExtent(extent_offset, extent_size),
        flags=record[25],
    )


def _validate_iso_extent_chain(
    records: tuple[_IsoDirectoryRecord, ...],
    source: str,
) -> None:
    if len(records) == 1:
        if records[0].has_more_extents:
            raise FormatError(
                "ISO multi-extent file is incomplete",
                source=source,
                observed=records[0].name,
            )
        return
    if any(not record.has_more_extents for record in records[:-1]):
        raise FormatError(
            "ISO multi-extent file terminates early",
            source=source,
            observed=records[0].name,
        )
    if records[-1].has_more_extents:
        raise FormatError(
            "ISO multi-extent file is incomplete",
            source=source,
            observed=records[-1].name,
        )


def _normalized_iso_name(value: str) -> str:
    base, separator, version = value.rpartition(";")
    if separator and version.isdigit():
        value = base
    return value.rstrip(".").casefold()


def _iso_both_endian_u16(data: bytes, offset: int, source: str) -> int:
    little = int.from_bytes(data[offset : offset + 2], "little")
    big = int.from_bytes(data[offset + 2 : offset + 4], "big")
    if little != big:
        raise FormatError(
            "ISO both-endian 16-bit value does not match",
            source=source,
            offset=offset,
            expected=little,
            observed=big,
        )
    return little


def _iso_both_endian_u32(data: bytes, offset: int, source: str) -> int:
    little = int.from_bytes(data[offset : offset + 4], "little")
    big = int.from_bytes(data[offset + 4 : offset + 8], "big")
    if little != big:
        raise FormatError(
            "ISO both-endian 32-bit value does not match",
            source=source,
            offset=offset,
            expected=little,
            observed=big,
        )
    return little


def _read_physical_exact(
    stream: io.BufferedReader,
    offset: int,
    size: int,
    container_size: int,
    source: str,
) -> bytes:
    if offset < 0 or size < 0 or offset + size > container_size:
        raise FormatError(
            "file range is outside the data source",
            source=source,
            offset=offset,
            expected=size,
            observed=max(0, container_size - offset),
        )
    stream.seek(offset)
    return _read_exact(stream, size, source, offset)


def _read_source_exact(
    stream: io.BufferedReader,
    extents: tuple[_FileExtent, ...],
    offset: int,
    size: int,
    source: str,
) -> bytes:
    archive_size = sum(extent.size for extent in extents)
    if offset < 0 or size < 0 or offset + size > archive_size:
        raise FormatError(
            "file range is outside the embedded PSARC",
            source=source,
            offset=offset,
            expected=size,
            observed=max(0, archive_size - offset),
        )
    remaining = size
    requested_offset = offset
    chunks: list[bytes] = []
    logical_start = 0
    for extent in extents:
        logical_end = logical_start + extent.size
        if offset >= logical_end:
            logical_start = logical_end
            continue
        within_extent = max(0, offset - logical_start)
        chunk_size = min(remaining, extent.size - within_extent)
        stream.seek(extent.offset + within_extent)
        chunks.append(
            _read_exact(
                stream,
                chunk_size,
                source,
                requested_offset + size - remaining,
            )
        )
        remaining -= chunk_size
        offset += chunk_size
        if remaining == 0:
            break
        logical_start = logical_end
    if remaining:
        raise FormatError(
            "truncated embedded PSARC",
            source=source,
            offset=requested_offset,
            expected=size,
            observed=size - remaining,
        )
    return b"".join(chunks)


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
