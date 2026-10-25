from __future__ import annotations

import struct
import unittest

from io_scene_killzone2.kz2_assets.edge_animation import parse_ea02
from io_scene_killzone2.kz2_assets.errors import (
    FormatError,
    UnsupportedFeatureError,
)


def zero_channel_clip() -> bytes:
    blob = bytearray(0x80)
    blob[0:4] = b"EA02"
    struct.pack_into(">ff", blob, 4, 0.0, 1.0)
    struct.pack_into(">5H", blob, 0x0C, 0x70, 2, 1, 1, 0x2000)
    struct.pack_into(">I", blob, 0x34, 0x2C)
    struct.pack_into(">I", blob, 0x38, 0x30)
    struct.pack_into(">II", blob, 0x60, 0, 0x70)
    return bytes(blob)


def constant_rotation_clip() -> bytes:
    blob = bytearray(0xE0)
    blob[0:4] = b"EA02"
    struct.pack_into(">ff", blob, 4, 1.0, 1.0)
    struct.pack_into(">5H", blob, 0x0C, 0xB0, 2, 2, 2, 0x2000)
    struct.pack_into(">H", blob, 0x16, 1)
    struct.pack_into(">I", blob, 0x34, 0x5C)
    struct.pack_into(">I", blob, 0x38, 0x68)
    struct.pack_into(">I", blob, 0x3C, 0x34)
    struct.pack_into(">I", blob, 0x50, 0x24)
    struct.pack_into(">8H", blob, 0x60, 1, 2, 2, 2, 2, 2, 2, 2)
    struct.pack_into(">4I", blob, 0x74, 0x80200803, 3, 3, 3)
    struct.pack_into(">4I", blob, 0x90, 0x20, 0xB0, 0, 0xC0)
    struct.pack_into(">4H", blob, 0xA0, 0, 0, 1, 0)
    return bytes(blob)


def zero_bit_constant_translation_clip() -> bytes:
    blob = bytearray(0xD0)
    blob[0:4] = b"EA02"
    struct.pack_into(">ff", blob, 4, 1.0, 1.0)
    struct.pack_into(">5H", blob, 0x0C, 0xB0, 2, 2, 2, 0x2000)
    struct.pack_into(">H", blob, 0x18, 1)
    struct.pack_into(">I", blob, 0x34, 0x5C)
    struct.pack_into(">I", blob, 0x38, 0x68)
    struct.pack_into(">I", blob, 0x40, 0x30)
    struct.pack_into(">I", blob, 0x50, 0x24)
    struct.pack_into(">4H", blob, 0x60, 1, 2, 2, 2)
    struct.pack_into(">4I", blob, 0x74, 3, 3, 3, 3)
    struct.pack_into(">4I", blob, 0x90, 0x20, 0xB0, 0, 0xC0)
    struct.pack_into(">4H", blob, 0xA0, 0, 0, 1, 0)
    return bytes(blob)


def animated_translation_clip() -> bytes:
    blob = bytearray(0x100)
    blob[0:4] = b"EA02"
    struct.pack_into(">ff", blob, 4, 2.0, 1.0)
    struct.pack_into(">5H", blob, 0x0C, 0xC0, 2, 3, 2, 0x2000)
    struct.pack_into(">H", blob, 0x20, 1)
    struct.pack_into(">I", blob, 0x34, 0x5C)
    struct.pack_into(">I", blob, 0x38, 0x68)
    struct.pack_into(">I", blob, 0x50, 0x20)
    struct.pack_into(">4H", blob, 0x60, 1, 2, 2, 2)

    vector_spec = (
        (1 << 31)
        | (2 << 22)
        | (1 << 21)
        | (2 << 12)
        | (1 << 11)
        | (2 << 2)
        | 3
    )
    struct.pack_into(">5I", blob, 0x70, 3, 3, 3, 3, vector_spec)
    struct.pack_into(">4I", blob, 0x90, 0x40, 0xC0, 0, 0xE0)
    struct.pack_into(">4H", blob, 0xA0, 0, 1, 2, 0)

    struct.pack_into(">8H", blob, 0xC0, 0, 2, 2, 0, 0, 2, 1, 8)
    blob[0xD0:0xD2] = _pack_vector(0)
    blob[0xD4] = 0x80
    blob[0xD5:0xD7] = _pack_vector(1)
    struct.pack_into(">8H", blob, 0xE0, 0, 2, 2, 0, 0, 0, 0, 0)
    blob[0xF0:0xF2] = _pack_vector(2)
    return bytes(blob)


def _pack_vector(x_mantissa: int) -> bytes:
    bits = f"0{x_mantissa:02b}" + "000" + "000"
    return (int(bits, 2) << (16 - len(bits))).to_bytes(2, "big")


class EdgeAnimationTests(unittest.TestCase):
    def test_zero_channel_clip_is_safe_to_sample(self) -> None:
        clip = parse_ea02(zero_channel_clip(), source="synthetic.ea02")
        self.assertTrue(clip.decode_supported)
        self.assertEqual(2, len(clip.sample_frame(0)))

    def test_packed_constant_rotation_decodes(self) -> None:
        clip = parse_ea02(constant_rotation_clip(), source="constant.ea02")
        self.assertTrue(clip.decode_supported)
        self.assertEqual((0.0, 0.0, 0.0, 1.0), clip.sample_frame(0)[1].rotation_xyzw)

    def test_zero_bit_constant_rotation_decodes_to_identity(self) -> None:
        blob = bytearray(constant_rotation_clip())
        struct.pack_into(">I", blob, 0x74, 3)
        clip = parse_ea02(bytes(blob), source="zero-rotation.ea02")
        self.assertEqual(
            (0.0, 0.0, 0.0, 1.0),
            clip.sample_frame(0)[1].rotation_xyzw,
        )

    def test_zero_bit_constant_vector_decodes_to_zero(self) -> None:
        clip = parse_ea02(
            zero_bit_constant_translation_clip(),
            source="zero-translation.ea02",
        )
        self.assertEqual(
            (0.0, 0.0, 0.0),
            clip.sample_frame(0)[1].translation,
        )

    def test_sparse_packed_translation_decodes(self) -> None:
        clip = parse_ea02(animated_translation_clip(), source="animated.ea02")
        self.assertTrue(clip.decode_supported)
        self.assertAlmostEqual(0.0, clip.sample_frame(0)[1].translation[0])
        self.assertAlmostEqual(1.0 / 3.0, clip.sample_frame(1)[1].translation[0])
        self.assertAlmostEqual(2.0 / 3.0, clip.sample_frame(2)[1].translation[0])

    def test_section_size_mismatch_is_rejected(self) -> None:
        blob = bytearray(animated_translation_clip())
        struct.pack_into(">H", blob, 0xC2, 1)
        with self.assertRaisesRegex(FormatError, "section sizes"):
            parse_ea02(bytes(blob), source="bad-sizes.ea02")

    def test_unknown_flags_fail_explicitly(self) -> None:
        blob = bytearray(constant_rotation_clip())
        struct.pack_into(">H", blob, 0x26, 1)
        clip = parse_ea02(bytes(blob), source="flags.ea02")
        self.assertFalse(clip.decode_supported)
        self.assertIn("unsupported KZ2 EA02 flags", clip.unsupported_reason)
        with self.assertRaises(UnsupportedFeatureError):
            clip.sample_frame(0)


if __name__ == "__main__":
    unittest.main()
