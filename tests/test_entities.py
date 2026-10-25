from __future__ import annotations

import struct
import unittest

from io_scene_killzone2.kz2_assets.core import CORE_SIGNATURE, CoreFile
from io_scene_killzone2.kz2_assets.decoder import CoreDecoder
from io_scene_killzone2.kz2_assets.model import (
    DestructibilityPartState,
    EntityResource,
    ModelPart,
    ModelResource,
    StateBasedDestructibility,
)
from io_scene_killzone2.kz2_assets.psarc import PsarcEntry
from io_scene_killzone2.kz2_assets.resolver import (
    _Definition,
    _parse_object_metadata,
    _select_named_definitions,
)


def compact(value: int) -> bytes:
    if -128 <= value <= 127 and value not in (-128, -127):
        return struct.pack(">b", value)
    if -32768 <= value <= 32767:
        return b"\x81" + struct.pack(">h", value)
    return b"\x80" + struct.pack(">i", value)


def local_reference(index: int | None) -> bytes:
    return b"\x00" + bytes((0 if index is None else index + 1,))


def synthetic_entity_core(
    *,
    destructibility_layout: tuple[int, int] = (90, 86),
) -> bytes:
    types = (
        b"EntityResource",
        b"StaticModelResource",
        b"ModelPartResource",
        b"DestructibilityPartStateResource",
        b"StateBasedDestructibilityResource",
        b"StaticMeshResource",
        b"String",
    )
    strings = (
        b"TestEntity",
        b"TestModel",
        b"TestPart",
        b"TestState",
        b"TestDestructibility",
    )
    payloads = (
        (
            b"\x00\x01"
            + local_reference(None)
            + b"\x00"
            + local_reference(None)
            + local_reference(1)
            + local_reference(4)
            + b"entity-tail"
        ),
        b"\x01\x01" + local_reference(5) + b"model-tail",
        b"\x02\x01" + local_reference(5) + b"part-tail",
        b"\x03\x01" + local_reference(2) + b"state-tail",
        (
            b"\x04\x01"
            + bytes(destructibility_layout[0])
            + compact(1)
            + bytes(destructibility_layout[1])
            + local_reference(3)
            + bytes(2)
            + bytes(11)
        ),
        b"unused-static-mesh-payload",
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
        opaque_id = bytes(16) if index == 0 else bytes((index + 1,)) + bytes(15)
        data.extend(opaque_id)
        data.extend(struct.pack(">I", len(payload)))
        data.extend(bytes(12))
    data.extend(compact(0))
    data.extend(compact(1))
    data.append(6)
    data.extend(compact(len(strings)))
    for value in strings:
        data.extend(compact(1))
        data.append(len(value))
        data.extend(value)
    data.extend(compact(0))
    data.extend(compact(0))
    for payload in payloads:
        data.extend(compact(0))
        data.extend(compact(0))
        data.extend(payload)
    return bytes(data)


def definition(
    path: str,
    object_index: int,
    opaque_id: bytes,
) -> _Definition:
    entry = PsarcEntry(
        index=object_index,
        digest=bytes(16),
        first_block_index=object_index,
        uncompressed_size=1,
        data_offset=object_index + 1,
        path=path,
    )
    return _Definition(entry, object_index, "EntityResource", opaque_id)


class EntityTests(unittest.TestCase):
    def test_entity_model_and_destructibility_graph(self) -> None:
        core = CoreFile.from_bytes(
            synthetic_entity_core(),
            source="entities.core",
        )
        decoder = CoreDecoder(core)

        entity = decoder.decode(0)
        self.assertIsInstance(entity, EntityResource)
        self.assertEqual("TestEntity", entity.prefix.name)
        self.assertEqual(1, entity.model.local_index)
        self.assertEqual(4, entity.destructibility.local_index)
        self.assertEqual(b"entity-tail", entity.trailing)

        model = decoder.decode(1)
        self.assertIsInstance(model, ModelResource)
        self.assertEqual(5, model.mesh.local_index)

        model_part = decoder.decode(2)
        self.assertIsInstance(model_part, ModelPart)
        self.assertEqual(5, model_part.mesh.local_index)

        part_state = decoder.decode(3)
        self.assertIsInstance(part_state, DestructibilityPartState)
        self.assertEqual(2, part_state.model_part.local_index)

        destructibility = decoder.decode(4)
        self.assertIsInstance(destructibility, StateBasedDestructibility)
        self.assertEqual((3,), tuple(
            reference.local_index
            for reference in destructibility.part_states
        ))
        self.assertEqual(bytes(11), destructibility.trailing)
        self.assertTrue(destructibility.part_states_complete)

    def test_shifted_destructibility_layout(self) -> None:
        core = CoreFile.from_bytes(
            synthetic_entity_core(destructibility_layout=(91, 87)),
            source="shifted-entities.core",
        )

        destructibility = CoreDecoder(core).decode(4)

        self.assertIsInstance(destructibility, StateBasedDestructibility)
        self.assertEqual(
            (3,),
            tuple(
                reference.local_index
                for reference in destructibility.part_states
            ),
        )
        self.assertTrue(destructibility.part_states_complete)

    def test_metadata_catalog_retains_names_for_zero_id_objects(self) -> None:
        metadata = _parse_object_metadata(
            synthetic_entity_core(),
            "entities.core",
        )
        self.assertEqual(6, len(metadata.objects))
        self.assertIn("TestEntity", metadata.strings)
        self.assertEqual("EntityResource", metadata.objects[0].type_name)

    def test_named_resolution_prefers_same_level_then_shared_base(self) -> None:
        shared = definition(
            "LocalCachePS3/lumps/levels.single_player.corinth_river."
            "section_shared.baselumps.0001.core",
            1,
            bytes.fromhex("01" * 16),
        )
        cruiser = definition(
            "LocalCachePS3/lumps/levels.single_player.the_cruiser."
            "section_mall.assets_optimized_props.core",
            2,
            bytes.fromhex("02" * 16),
        )
        definitions = (shared, cruiser)

        self.assertEqual(
            (cruiser,),
            _select_named_definitions(
                definitions,
                source_path=(
                    "LocalCachePS3/lumps/levels.single_player.the_cruiser."
                    "section_mall.assets_optimized_room.core"
                ),
            ),
        )
        self.assertEqual(
            (shared,),
            _select_named_definitions(
                definitions,
                source_path=(
                    "LocalCachePS3/lumps/levels.single_player.the_new_sun."
                    "section_01.assets_optimized_room.core"
                ),
            ),
        )


if __name__ == "__main__":
    unittest.main()
