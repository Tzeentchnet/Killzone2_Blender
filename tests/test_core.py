from __future__ import annotations

import struct
import unittest

from io_scene_killzone2.kz2_assets.core import CORE_SIGNATURE, CoreFile
from io_scene_killzone2.kz2_assets.errors import FormatError
from io_scene_killzone2.kz2_assets.level import (
    MeshHierarchyShaderOverrides,
    StaticMeshInstance,
)


def compact(value: int) -> bytes:
    if -128 <= value <= 127 and value not in (-128, -127):
        return struct.pack(">b", value)
    if -32768 <= value <= 32767:
        return b"\x81" + struct.pack(">h", value)
    return b"\x80" + struct.pack(">i", value)


def synthetic_core(payload: bytes = b"abc") -> bytes:
    data = bytearray(CORE_SIGNATURE)
    data.append(1)
    data.extend(bytes(16))
    types = (b"Dummy", b"String")
    data.extend(compact(len(types)))
    for type_name in types:
        data.extend(compact(len(type_name)))
        data.extend(type_name)
    data.extend(compact(1))
    data.append(0)
    data.extend(bytes.fromhex("00112233445566778899aabbccddeeff"))
    data.extend(struct.pack(">I", len(payload)))
    data.extend(bytes(12))
    data.extend(compact(0))
    data.extend(compact(1))
    data.append(1)
    data.extend(compact(1))
    data.extend(compact(1))
    data.append(0)
    data.extend(compact(0))
    data.extend(compact(0))
    data.extend(compact(0))
    data.extend(compact(0))
    data.extend(payload)
    return bytes(data)


def synthetic_direct_core(
    type_name: bytes,
    direct_payload: bytes,
    *,
    buffered_payload: bytes = b"abc",
) -> bytes:
    types = (type_name, b"String", b"Dummy")
    strings = (b"", b"LMUVShift", b"inSampler_LM")
    data = bytearray(CORE_SIGNATURE)
    data.append(1)
    data.extend(bytes(16))
    data.extend(compact(len(types)))
    for reflected_type in types:
        data.extend(compact(len(reflected_type)))
        data.extend(reflected_type)
    data.extend(compact(2))
    data.extend((0, 2))
    data.extend(bytes(16))
    data.extend(struct.pack(">I", 0))
    data.extend(bytes(12))
    data.extend(bytes.fromhex("00112233445566778899aabbccddeeff"))
    data.extend(struct.pack(">I", len(buffered_payload)))
    data.extend(bytes(12))
    data.extend(compact(0))
    data.extend(compact(1))
    data.append(1)
    data.extend(compact(len(strings)))
    for value in strings:
        data.extend(compact(1))
        data.append(len(value))
        data.extend(value)
    data.extend(compact(0))
    data.extend(compact(0))
    data.extend(compact(0))
    data.extend(compact(0))
    data.extend(direct_payload)
    data.extend(compact(0))
    data.extend(compact(0))
    data.extend(buffered_payload)
    return bytes(data)


def external_reference(value: bytes) -> bytes:
    return b"\x02" + compact(len(value)) + value


def static_mesh_instance_payload() -> bytes:
    identity = (
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
        0.0,
        0.0,
        0.0,
        1.0,
    )
    data = bytearray(b"\x00\x00")
    data.extend(struct.pack(">16f", *identity))
    data.extend(bytes(range(12)))
    data.append(0)
    data.append(7)
    data.extend(external_reference(b"model-id"))
    data.extend(compact(1))
    data.extend(external_reference(b"mesh-id"))
    data.extend(compact(2))
    data.extend(struct.pack(">2I", 1, 2))
    data.extend(compact(1))
    data.extend(external_reference(b"effect-id"))
    data.extend(compact(1))
    data.append(2)
    data.extend(external_reference(b"lightmap-id"))
    data.extend(struct.pack(">HH", 0, 0x1003))
    data.extend(compact(1))
    data.append(1)
    data.extend(struct.pack(">I", 4))
    data.extend(bytes(range(16)))
    data.extend(b"\x00\x00")
    data.extend(bytes(range(8)))
    return bytes(data)


class CoreTests(unittest.TestCase):
    def test_minimal_object_graph(self) -> None:
        core = CoreFile.from_bytes(synthetic_core(), source="synthetic.core")
        self.assertEqual(("Dummy", "String"), core.type_names)
        self.assertEqual(("",), core.strings())
        self.assertEqual(1, len(core.objects))
        obj = core.objects[0]
        self.assertEqual("Dummy", obj.type_name)
        self.assertEqual("3322110077665544bbaa9988ffeeddcc", obj.display_id)
        self.assertEqual(b"abc", core.reader_for(obj).read(3))
        self.assertEqual(0, core.trailing_bytes)

    def test_direct_object_preserves_following_buffered_payload(self) -> None:
        payload = static_mesh_instance_payload()
        core = CoreFile.from_bytes(
            synthetic_direct_core(b"StaticMeshInstance", payload),
            source="direct.core",
            parse_direct_objects=True,
        )
        direct = core.objects[0]
        self.assertEqual(0, direct.payload_size)
        self.assertEqual(len(payload), direct.direct_size)
        self.assertIsInstance(direct.direct_value, StaticMeshInstance)
        instance = direct.direct_value
        self.assertEqual("model-id", instance.static_model.external_id)
        self.assertIsInstance(
            instance.shader_overrides,
            MeshHierarchyShaderOverrides,
        )
        mesh_override = instance.shader_overrides.mesh_overrides[0]
        self.assertEqual(
            (1, 2),
            mesh_override.hierarchy_path,
        )
        effect_override = mesh_override.render_effect_overrides[0]
        self.assertEqual(
            "lightmap-id",
            effect_override.texture_overrides[0].texture.external_id,
        )
        self.assertEqual(0x1003, effect_override.texture_overrides[0].packed_sampler_state)
        self.assertEqual(4, effect_override.variable_overrides[0].variable_type)
        self.assertEqual(b"abc", core.reader_for(1).read(3))
        self.assertEqual(0, core.trailing_bytes)

    def test_unknown_direct_object_fails_closed(self) -> None:
        with self.assertRaisesRegex(
            FormatError,
            "unsupported zero-sized direct serializer",
        ):
            CoreFile.from_bytes(
                synthetic_direct_core(b"FutureDirectType", b""),
                source="unknown-direct.core",
                parse_direct_objects=True,
            )


if __name__ == "__main__":
    unittest.main()
