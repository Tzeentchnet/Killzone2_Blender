from __future__ import annotations

import unittest

from io_scene_killzone2.kz2_assets.binary import BinaryReader
from io_scene_killzone2.kz2_assets.errors import FormatError


class BinaryReaderTests(unittest.TestCase):
    def test_compact_signed_scalar_forms(self) -> None:
        reader = BinaryReader.from_bytes(
            bytes.fromhex("ff 7f 81 00 ff 80 00 01 00 00")
        )
        self.assertEqual(-1, reader.compact_scalar())
        self.assertEqual(127, reader.compact_scalar())
        self.assertEqual(255, reader.compact_scalar())
        self.assertEqual(65536, reader.compact_scalar())

    def test_negative_count_is_rejected(self) -> None:
        reader = BinaryReader.from_bytes(b"\xff")
        with self.assertRaisesRegex(FormatError, "invalid item count"):
            reader.count("item count")

    def test_adaptive_index_width_boundary(self) -> None:
        reader = BinaryReader.from_bytes(bytes.fromhex("ff 01 00"))
        self.assertEqual(255, reader.adaptive_index(256))
        self.assertEqual(256, reader.adaptive_index(257))


if __name__ == "__main__":
    unittest.main()
