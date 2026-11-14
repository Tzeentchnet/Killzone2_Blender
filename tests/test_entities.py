from __future__ import annotations

import struct
import unittest

from io_scene_killzone2.kz2_assets.core import CORE_SIGNATURE, CoreFile
from io_scene_killzone2.kz2_assets.decoder import CoreDecoder
from io_scene_killzone2.kz2_assets.model import (
    AttachmentMover,
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
    entity_type: bytes = b"EntityResource",
    child_indices: tuple[int, ...] = (),
) -> bytes:
    identity_matrix = struct.pack(
        ">16f",
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
    part_prefix = (
        struct.pack(">iBff", 0, 1, 10.0, 0.0)
        + compact(0)
        + struct.pack(">H", 5)
        + struct.pack(">i", 10)
        + identity_matrix
        + local_reference(None)
    )
    part_prefix += bytes(destructibility_layout[1] - len(part_prefix))
    types = (
        entity_type,
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
        b"neck",
    ) + tuple(f"Unused_{index}".encode() for index in range(251))

    def resource_prefix(index: int) -> bytes:
        return struct.pack(">H", index) + b"\x01"

    payloads = (
        (
            resource_prefix(0)
            + local_reference(None)
            + b"\x00"
            + local_reference(None)
            + local_reference(1)
            + local_reference(4)
            + compact(len(child_indices))
            + b"".join(local_reference(index) for index in child_indices)
            + b"entity-tail"
        ),
        resource_prefix(1) + local_reference(5) + b"model-tail",
        resource_prefix(2) + local_reference(5) + b"part-tail",
        (
            resource_prefix(3)
            + local_reference(2)
            + struct.pack(">2f", 0.0, 100.0)
            + compact(0)
            + identity_matrix
            + b"\x00"
            + b"state-tail"
        ),
        (
            resource_prefix(4)
            + bytes(destructibility_layout[0])
            + compact(1)
            + part_prefix
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


def synthetic_attachment_mover_core() -> bytes:
    types = (b"AttachToHelperMoverResource", b"String")
    strings = (b"TestMover", b"Turret_Right")
    local_matrix = tuple(float(value) for value in range(16))
    post_local_matrix = (
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
    payload = (
        b"\x00\x01"
        + struct.pack(">16f", *local_matrix)
        + struct.pack(">16f", *post_local_matrix)
        + b"\x01"
        + b"\x01"
        + b"mover-tail"
    )

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
    data.extend(compact(len(strings)))
    for value in strings:
        data.extend(compact(1))
        data.append(len(value))
        data.extend(value)
    data.extend(compact(0))
    data.extend(compact(0))
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
            synthetic_entity_core(child_indices=(1,)),
            source="entities.core",
        )
        decoder = CoreDecoder(core)

        entity = decoder.decode(0)
        self.assertIsInstance(entity, EntityResource)
        self.assertEqual("TestEntity", entity.prefix.name)
        self.assertEqual(1, entity.model.local_index)
        self.assertEqual(4, entity.destructibility.local_index)
        self.assertEqual(
            (1,),
            tuple(
                reference.local_index
                for reference in entity.child_entities
            ),
        )
        self.assertEqual((), entity.body_variants)
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
        self.assertEqual(
            (1.0, 0.0, 0.0, 0.0),
            part_state.offset_matrix[0],
        )
        self.assertFalse(part_state.has_lock_position)
        self.assertEqual(b"state-tail", part_state.trailing)

        destructibility = decoder.decode(4)
        self.assertIsInstance(destructibility, StateBasedDestructibility)
        self.assertEqual((3,), tuple(
            reference.local_index
            for reference in destructibility.part_states
        ))
        self.assertEqual(bytes(11), destructibility.trailing)
        self.assertTrue(destructibility.part_states_complete)
        self.assertEqual((0,), destructibility.part_ids)
        self.assertEqual((True,), destructibility.part_enabled)
        self.assertEqual(("neck",), destructibility.part_bone_names)
        self.assertEqual(
            (1.0, 0.0, 0.0, 0.0),
            destructibility.part_local_matrices[0][0],
        )

    def test_entity_subclass_decodes_base_fields(self) -> None:
        core = CoreFile.from_bytes(
            synthetic_entity_core(entity_type=b"SoldierResource"),
            source="soldier.core",
        )

        entity = CoreDecoder(core).decode(0)

        self.assertIsInstance(entity, EntityResource)
        self.assertEqual("TestEntity", entity.prefix.name)
        self.assertEqual(1, entity.model.local_index)
        self.assertEqual(4, entity.destructibility.local_index)
        self.assertEqual((), entity.child_entities)
        self.assertEqual((), entity.body_variants)
        self.assertEqual(b"entity-tail", entity.trailing)

    def test_attachment_mover_decodes_target_and_transforms(self) -> None:
        core = CoreFile.from_bytes(
            synthetic_attachment_mover_core(),
            source="mover.core",
        )

        mover = CoreDecoder(core).decode(0)

        self.assertIsInstance(mover, AttachmentMover)
        self.assertEqual("TestMover", mover.prefix.name)
        self.assertEqual("helper", mover.target_kind)
        self.assertEqual("Turret_Right", mover.target_name)
        self.assertEqual(1, mover.align_to_world)
        self.assertEqual((0.0, 1.0, 2.0, 3.0), mover.local_matrix[0])
        self.assertEqual((0.0, 0.0, 0.0, 1.0), mover.post_local_matrix[3])
        self.assertEqual(b"mover-tail", mover.trailing)

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
