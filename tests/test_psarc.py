from __future__ import annotations

import hashlib
import struct
import tempfile
import unittest
import zlib
from pathlib import Path

from io_scene_killzone2.kz2_assets.psarc import Psarc


def u40(value: int) -> bytes:
    return value.to_bytes(5, "big")


def build_archive(path: Path) -> tuple[str, bytes]:
    logical_path = "folder/test.core"
    manifest = (logical_path + "\n").encode()
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


if __name__ == "__main__":
    unittest.main()
