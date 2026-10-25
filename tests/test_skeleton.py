from __future__ import annotations

import struct
import unittest

from io_scene_killzone2.kz2_assets.skeleton import _parse_edge_skeleton


def two_joint_edge_skeleton() -> bytes:
    blob = bytearray(0xA4)
    blob[0:4] = b"ES00"
    struct.pack_into(">I", blob, 8, len(blob))
    struct.pack_into(">H", blob, 0x0C, 2)
    struct.pack_into(">I", blob, 0x14, 0x40 - 0x14)
    struct.pack_into(">I", blob, 0x18, 0xA0 - 0x18)
    transform = (
        0.0,
        0.0,
        0.0,
        1.0,
        0.0,
        0.0,
        0.0,
        0.0,
        1.0,
        1.0,
        1.0,
        0.0,
    )
    struct.pack_into(">12f", blob, 0x40, *transform)
    struct.pack_into(">12f", blob, 0x70, *transform)
    struct.pack_into(">2H", blob, 0xA0, 0xFFFF, 0)
    return bytes(blob)


class EdgeSkeletonTests(unittest.TestCase):
    def test_fallback_transforms_and_parents_decode(self) -> None:
        header, joints = _parse_edge_skeleton(
            two_joint_edge_skeleton(),
            "synthetic.core",
        )

        self.assertEqual(2, header.joint_lookup_count)
        self.assertEqual(2, len(joints))
        self.assertEqual((0.0, 0.0, 0.0, 1.0), joints[0].rotation_xyzw)
        self.assertEqual((0.0, 0.0, 0.0), joints[0].translation)
        self.assertEqual((1.0, 1.0, 1.0), joints[0].scale)
        self.assertIsNone(joints[0].parent_index)
        self.assertEqual(0, joints[1].parent_index)


if __name__ == "__main__":
    unittest.main()
