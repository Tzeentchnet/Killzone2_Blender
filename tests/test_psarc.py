from __future__ import annotations

import hashlib
import struct
import tempfile
import unittest
import zlib
from pathlib import Path

from io_scene_killzone2.kz2_assets.errors import FormatError
from io_scene_killzone2.kz2_assets.psarc import Psarc


def u40(value: int) -> bytes:
    return value.to_bytes(5, "big")


def build_archive(
    path: Path,
    *,
    payload: bytes | None = None,
) -> tuple[str, bytes]:
    logical_path = "folder/test.core"
    manifest = (logical_path + "\n").encode()
    if payload is None:
        payload = b"RTTIBin<1.58>  " + bytes(512)
    block_size = 256

    entries_data: list[list[bytes]] = []
    block_lengths: list[int] = []
    for content in (manifest, payload):
        stored_blocks = []
        for start in range(0, len(content), block_size):
            raw = content[start : start + block_size]
            compressed = zlib.compress(raw, level=9)
            stored = compressed if len(compressed) < len(raw) else raw
            stored_blocks.append(stored)
            block_lengths.append(0 if len(stored) == block_size else len(stored))
        entries_data.append(stored_blocks)

    entry_count = 2
    toc_length = 0x20 + entry_count * 30 + len(block_lengths)
    data_offsets = []
    cursor = toc_length
    for blocks in entries_data:
        data_offsets.append(cursor)
        cursor += sum(len(block) for block in blocks)

    header = (
        b"PSAR"
        + struct.pack(">I", 0x00010003)
        + b"zlib"
        + struct.pack(">IIIII", toc_length, 30, entry_count, block_size, 1)
    )
    first_second_block = len(entries_data[0])
    toc = bytearray()
    toc.extend(bytes(16))
    toc.extend(struct.pack(">I", 0))
    toc.extend(u40(len(manifest)))
    toc.extend(u40(data_offsets[0]))
    toc.extend(hashlib.md5(logical_path.upper().encode()).digest())
    toc.extend(struct.pack(">I", first_second_block))
    toc.extend(u40(len(payload)))
    toc.extend(u40(data_offsets[1]))

    with path.open("wb") as stream:
        stream.write(header)
        stream.write(toc)
        stream.write(bytes(block_lengths))
        for blocks in entries_data:
            for block in blocks:
                stream.write(block)
    return logical_path, payload


def both_endian_u16(value: int) -> bytes:
    return value.to_bytes(2, "little") + value.to_bytes(2, "big")


def both_endian_u32(value: int) -> bytes:
    return value.to_bytes(4, "little") + value.to_bytes(4, "big")


def iso_directory_record(
    identifier: bytes,
    extent_sector: int,
    size: int,
    flags: int,
) -> bytes:
    record_length = 33 + len(identifier)
    if record_length % 2:
        record_length += 1
    record = bytearray(record_length)
    record[0] = record_length
    record[2:10] = both_endian_u32(extent_sector)
    record[10:18] = both_endian_u32(size)
    record[25] = flags
    record[28:32] = both_endian_u16(1)
    record[32] = len(identifier)
    record[33 : 33 + len(identifier)] = identifier
    return bytes(record)


def build_iso(path: Path, psarc_path: Path) -> tuple[int, int]:
    sector_size = 2048
    psarc = psarc_path.read_bytes()
    padded_size = max(
        2 * sector_size,
        (
            (len(psarc) + sector_size - 1) // sector_size
        ) * sector_size,
    )
    psarc += bytes(padded_size - len(psarc))
    first_extent = psarc[:sector_size]
    second_extent = psarc[sector_size:]

    root_sector = 20
    game_sector = 21
    usrdir_sector = 22
    first_psarc_sector = 40
    second_psarc_sector = 50
    total_sectors = (
        second_psarc_sector
        + len(second_extent) // sector_size
        + 1
    )
    image = bytearray(total_sectors * sector_size)

    primary = bytearray(sector_size)
    primary[0] = 1
    primary[1:6] = b"CD001"
    primary[6] = 1
    primary[80:88] = both_endian_u32(total_sectors)
    primary[120:124] = both_endian_u16(1)
    primary[124:128] = both_endian_u16(1)
    primary[128:132] = both_endian_u16(sector_size)
    root_record = iso_directory_record(
        b"\x00",
        root_sector,
        sector_size,
        0x02,
    )
    primary[156 : 156 + len(root_record)] = root_record
    image[16 * sector_size : 17 * sector_size] = primary

    terminator = bytearray(sector_size)
    terminator[0] = 255
    terminator[1:6] = b"CD001"
    terminator[6] = 1
    image[17 * sector_size : 18 * sector_size] = terminator

    directories = {
        root_sector: (
            iso_directory_record(
                b"\x00",
                root_sector,
                sector_size,
                0x02,
            )
            + iso_directory_record(
                b"\x01",
                root_sector,
                sector_size,
                0x02,
            )
            + iso_directory_record(
                b"PS3_GAME",
                game_sector,
                sector_size,
                0x02,
            )
        ),
        game_sector: (
            iso_directory_record(
                b"\x00",
                game_sector,
                sector_size,
                0x02,
            )
            + iso_directory_record(
                b"\x01",
                root_sector,
                sector_size,
                0x02,
            )
            + iso_directory_record(
                b"USRDIR",
                usrdir_sector,
                sector_size,
                0x02,
            )
        ),
        usrdir_sector: (
            iso_directory_record(
                b"\x00",
                usrdir_sector,
                sector_size,
                0x02,
            )
            + iso_directory_record(
                b"\x01",
                game_sector,
                sector_size,
                0x02,
            )
            + iso_directory_record(
                b"DATA_PS3.PSARC;1",
                first_psarc_sector,
                len(first_extent),
                0x80,
            )
            + iso_directory_record(
                b"DATA_PS3.PSARC;1",
                second_psarc_sector,
                len(second_extent),
                0,
            )
        ),
    }
    for sector, directory in directories.items():
        start = sector * sector_size
        image[start : start + len(directory)] = directory

    first_start = first_psarc_sector * sector_size
    image[first_start : first_start + len(first_extent)] = first_extent
    second_start = second_psarc_sector * sector_size
    image[second_start : second_start + len(second_extent)] = second_extent
    path.write_bytes(image)
    return first_start, padded_size


class PsarcTests(unittest.TestCase):
    def test_catalog_and_extract(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "test.psarc"
            logical_path, payload = build_archive(path)
            archive = Psarc.open(path)
            self.assertEqual(2, len(archive.entries))
            self.assertEqual(logical_path, archive.get(logical_path).path)
            self.assertEqual(payload, archive.read_entry(logical_path))
            self.assertEqual(payload[:20], archive.read_prefix(logical_path, 20))

    def test_catalog_and_extract_from_decrypted_iso(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            psarc_path = root / "test.psarc"
            iso_path = root / "test.dec.iso"
            payload = b"RTTIBin<1.58>  " + b"".join(
                hashlib.sha256(index.to_bytes(4, "big")).digest()
                for index in range(256)
            )
            logical_path, payload = build_archive(
                psarc_path,
                payload=payload,
            )
            _, embedded_size = build_iso(iso_path, psarc_path)

            archive = Psarc.open(iso_path)

            self.assertEqual(
                "PS3_GAME/USRDIR/data_ps3.psarc",
                archive.embedded_path,
            )
            self.assertEqual(embedded_size, archive.archive_size)
            self.assertEqual(2, len(archive._source_extents))
            self.assertEqual(logical_path, archive.get(logical_path).path)
            self.assertEqual(payload, archive.read_entry(logical_path))

    def test_rejects_encrypted_iso_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            psarc_path = root / "test.psarc"
            iso_path = root / "test.iso"
            build_archive(psarc_path)
            first_extent, _ = build_iso(iso_path, psarc_path)
            with iso_path.open("r+b") as stream:
                stream.seek(first_extent)
                stream.write(b"\x00\x00\x00\x00")

            with self.assertRaisesRegex(
                FormatError,
                "select a decrypted Killzone 2 ISO",
            ):
                Psarc.open(iso_path)


if __name__ == "__main__":
    unittest.main()
