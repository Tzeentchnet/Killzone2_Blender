from __future__ import annotations

import struct
import unittest

from io_scene_killzone2.kz2_assets.animation_graph import (
    animation_state_clip_names,
)
from io_scene_killzone2.kz2_assets.core import CORE_SIGNATURE, CoreFile
from io_scene_killzone2.kz2_assets.decoder import CoreDecoder
from io_scene_killzone2.kz2_assets.model import (
    AnimationState,
    DirectionalAnimation,
    SkeletonAnimationBoneMap,
    TimeMappedSkeletonAnimation,
)


def compact(value: int) -> bytes:
    if -128 <= value <= 127 and value not in (-128, -127):
        return struct.pack(">b", value)
    if -32768 <= value <= 32767:
        return b"\x81" + struct.pack(">h", value)
    return b"\x80" + struct.pack(">i", value)


def local_reference(index: int | None) -> bytes:
    return b"\x00" + bytes((0 if index is None else index + 1,))


def synthetic_animation_graph_core() -> bytes:
    types = (
        b"SkeletonAnimationBoneMap",
        b"AnimationState",
        b"TimeMappedSkeletonAnimationResource",
        b"DirectionalAnimationResource",
        b"SkeletonAnimationResource",
        b"String",
    )
    strings = (b"", b"Hover", b"root", b"child")
    payloads = (
        compact(2) + bytes((2, 3)),
        bytes((1, 0)) + local_reference(3) + b"state-tail",
        (
            bytes((0, 0))
            + struct.pack(">I", 2)
            + bytes((0, 0))
            + local_reference(4)
            + b"time-tail"
        ),
        (
            bytes((0, 0))
            + struct.pack(">II", 1, 1)
            + local_reference(2)
            + struct.pack(">ff", 1.0, -90.0)
            + bytes((0,))
            + b"direction-tail"
        ),
        b"",
    )

    data = bytearray(CORE_SIGNATURE)
    data.append(1)
    data.extend(bytes(16))
    data.extend(compact(len(types)))
    for type_name in types:
        data.extend(compact(len(type_name)))
        data.extend(type_name)
    data.extend(compact(len(payloads)))
    data.extend(range(len(payloads)))
    for index, payload in enumerate(payloads):
        data.extend(bytes((index + 1,)) + bytes(15))
        data.extend(struct.pack(">I", len(payload)))
        data.extend(bytes(12))
    data.extend(compact(0))
    data.extend(compact(1))
    data.append(5)
    data.extend(compact(len(strings)))
    for value in strings:
        data.extend(compact(1))
        data.extend(compact(len(value)))
        data.extend(value)
    data.extend(compact(0))
    data.extend(compact(0))
    for payload in payloads:
        data.extend(compact(0))
        data.extend(compact(0))
        data.extend(payload)
    return bytes(data)


class AnimationGraphTests(unittest.TestCase):
    def setUp(self) -> None:
        self.decoder = CoreDecoder(
            CoreFile.from_bytes(
                synthetic_animation_graph_core(),
                source="animation-graph.core",
            )
        )

    def test_decodes_bone_map_and_animation_wrappers(self) -> None:
        bone_map = self.decoder.decode(0)
        self.assertIsInstance(bone_map, SkeletonAnimationBoneMap)
        self.assertEqual(("root", "child"), bone_map.bone_names)

        state = self.decoder.decode(1)
        self.assertIsInstance(state, AnimationState)
        self.assertEqual("Hover", state.prefix.name)
        self.assertEqual(3, state.animation.local_index)

        time_mapped = self.decoder.decode(2)
        self.assertIsInstance(time_mapped, TimeMappedSkeletonAnimation)
        self.assertEqual(4, time_mapped.animation.local_index)

        directional = self.decoder.decode(3)
        self.assertIsInstance(directional, DirectionalAnimation)
        self.assertEqual(1, len(directional.entries))
        self.assertEqual(2, directional.entries[0].animation.local_index)
        self.assertEqual(-90.0, directional.entries[0].heading_degrees)

    def test_resolves_state_name_through_nested_wrappers(self) -> None:
        self.assertEqual(
            {4: ("Hover",)},
            animation_state_clip_names(self.decoder),
        )


if __name__ == "__main__":
    unittest.main()
