from __future__ import annotations

import struct
import unittest

from io_scene_killzone2.kz2_assets.core import CORE_SIGNATURE, CoreFile
from io_scene_killzone2.kz2_assets.decoder import CoreDecoder
from io_scene_killzone2.kz2_assets.model import Skeleton
from io_scene_killzone2.kz2_assets.skeleton import _parse_edge_skeleton


def compact(value: int) -> bytes:
    if -128 <= value <= 127 and value not in (-128, -127):
        return struct.pack(">b", value)
    if -32768 <= value <= 32767:
        return b"\x81" + struct.pack(">h", value)
    return b"\x80" + struct.pack(">i", value)


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


def synthetic_skeleton_core() -> bytes:
    strings = b"\0Root\0Body\0Turret_Right\0"
    payload = bytearray(b"\x00\x01")
    payload.extend(compact(0) * 5)
    edge = two_joint_edge_skeleton()
    payload.extend(compact(len(edge)))
    payload.extend(edge)
    while len(payload) % 4:
        payload.append(0)

    block_start = len(payload)
    fixed = bytearray(64)
    fixed[0x0C] = 1
    fixed[0x0D] = 2
    struct.pack_into(">H", fixed, 0x12, len(strings))
    payload.extend(fixed)
    payload.extend(bytes(24 * 2))
    while (len(payload) - block_start) % 16:
        payload.append(0)

    helper_matrix = (
        1.0,
        0.0,
        0.0,
        0.0,
        0.0,
        1.0,
        0.0,
        0.0,
        0.0,
        0.0,
        1.0,
        0.0,
        2.0,
        3.0,
        4.0,
        1.0,
    )
    payload.extend(struct.pack(">16f", *helper_matrix))
    payload.extend(struct.pack(">4I", 11, 1, 0xDEADBEEF, 0xDEADC0DE))
    while (len(payload) - block_start) % 16:
        payload.append(0)

    payload.extend(struct.pack(">7fII", 0, 0, 0, 1, 0, 0, 0, 1, 0))
    payload.extend(bytes(12))
    payload.extend(struct.pack(">7fII", 0, 0, 0, 1, 0, 1, 0, 6, 1))
    payload.extend(bytes(12))
    payload.extend(bytes(4 * 2))
    payload.extend(bytes(4 * 4))
    payload.extend(strings)

    types = (b"Skeleton", b"String")
    data = bytearray(CORE_SIGNATURE)
    data.append(1)
    data.extend(bytes(16))
    data.extend(compact(len(types)))
    for type_name in types:
        data.extend(compact(len(type_name)))
        data.extend(type_name)
    data.extend(compact(1))
    data.append(0)
    data.extend(bytes(16))
    data.extend(struct.pack(">I", len(payload)))
    data.extend(bytes(12))
    data.extend(compact(0))
    data.extend(compact(1))
    data.append(1)
    data.extend(compact(1))
    data.extend(compact(1))
    data.append(len(b"TestSkeletonXYZ"))
    data.extend(b"TestSkeletonXYZ")
    data.extend(compact(0))
    data.extend(compact(0))
    data.extend(compact(0))
    data.extend(compact(0))
    data.extend(payload)
    return bytes(data)


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

    def test_expanded_skeleton_helpers_decode(self) -> None:
        core = CoreFile.from_bytes(
            synthetic_skeleton_core(),
            source="helpers.core",
        )

        skeleton = CoreDecoder(core).decode(0)

        self.assertIsInstance(skeleton, Skeleton)
        self.assertEqual(1, skeleton.auxiliary_count)
        self.assertEqual(1, len(skeleton.helpers))
        helper = skeleton.helpers[0]
        self.assertEqual("Turret_Right", helper.name)
        self.assertEqual(1, helper.parent_index)
        self.assertEqual((2.0, 3.0, 4.0, 1.0), helper.local_transform[3])
        self.assertEqual((0xDEADBEEF, 0xDEADC0DE), helper.unknown)


if __name__ == "__main__":
    unittest.main()
