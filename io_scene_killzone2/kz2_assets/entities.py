"""Entity and model-resource decoders used by level placements."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Final

from .binary import BinaryReader
from .errors import Kz2AssetError
from .geometry import read_resource_prefix
from .model import (
    AttachmentMover,
    DestructibilityPartState,
    EntityResource,
    Matrix4,
    ModelPart,
    ModelResource,
    StateBasedDestructibility,
)

if TYPE_CHECKING:
    from .core import CoreObject, CoreReference
    from .decoder import CoreDecoder


DESTRUCTIBILITY_LAYOUTS = (
    (90, 86),
    (91, 87),
    (103, 87),
)
DESTRUCTIBILITY_PART_ALLOCATION_SIZE = 144
DESTRUCTIBILITY_PART_SUFFIX_SIZE = 2
HUMANOID_BODY_VARIANT_SIZE = 28
MAX_HUMANOID_BODY_VARIANTS = 64
HUMANOID_ENTITY_TYPES: Final = frozenset(
    {
        "HumanoidResource",
        "SoldierResource",
    }
)

ENTITY_RESOURCE_TYPES: Final = frozenset(
    {
        "ATACResource",
        "AirSupportGrenadeResource",
        "AirVehicleResource",
        "AmmoBoxResource",
        "AmmoEjectorResource",
        "AnimalResource",
        "AnimatedModelEntityResource",
        "ApplyForceEntityResource",
        "AttachableExplosiveResource",
        "AttachableUseLocationResource",
        "BuggyResource",
        "BulletEjectorResource",
        "BulletSystemResource",
        "C4Resource",
        "CameraEntityResource",
        "CaptureAndHoldAreaResource",
        "ContinuousAmmoEjectorResource",
        "ControlledEntityResource",
        "ControlledFollowCameraEntityResource",
        "ControlledVehicleResource",
        "CritterResource",
        "DamageAreaResource",
        "DamageBoxResource",
        "DeathCameraEntityResource",
        "DestructibleSymbolResource",
        "DiscreteAmmoEjectorResource",
        "DoorResource",
        "EffectEntityResource",
        "ElasticVehicleFollowCameraEntityResource",
        "ElectricityEjectorResource",
        "ElectricityEntityResource",
        "ElectricityGrenadeResource",
        "ElectricityGridResource",
        "ElectricityNodeManagerResource",
        "EntityResource",
        "ExoSkeletonResource",
        "ExplosionResource",
        "ExplosiveLocationResource",
        "FirstPersonCameraEntityResource",
        "FirstPersonDeathCameraEntityResource",
        "FlameThrowerEjectorResource",
        "FlyoverCameraEntityResource",
        "FlyoverDeathCameraEntityResource",
        "FollowCameraEntityResource",
        "GrenadeEjectorResource",
        "GrenadeResource",
        "HealthPickUpResource",
        "HumanoidResource",
        "InteractiveEntityResource",
        "InventoryAirSupportResource",
        "InventoryAmmoEjectorResource",
        "InventoryBoostAbilityResource",
        "InventoryC4Resource",
        "InventoryCollectableResource",
        "InventoryDetonatorResource",
        "InventoryDisguiseAbilityResource",
        "InventoryEntityPickUpResource",
        "InventoryEntityResource",
        "InventoryGravityGunResource",
        "InventoryGrenadeResource",
        "InventoryHealAbilityResource",
        "InventoryHealthResource",
        "InventoryPlaceableResource",
        "InventoryPortableSpawnGrenadeResource",
        "InventoryReviveAbilityResource",
        "InventoryRocketLauncherResource",
        "InventorySpotAndMarkAbilityResource",
        "InventoryThrowablePickUpResource",
        "InventoryThrowableResource",
        "InventoryTurretResource",
        "InventoryWeaponPickUpResource",
        "InventoryWeaponResource",
        "LadderResource",
        "LandVehicleResource",
        "LeanAndPeekAreaResource",
        "LiftResource",
        "MineResource",
        "MissileSiloResource",
        "MortarStrikeResource",
        "MountableEntityResource",
        "MountedGunResource",
        "PickUpDropEntityResource",
        "PickUpResource",
        "PivotFollowCameraEntityResource",
        "PlaceableResource",
        "PlayAnimationObjectResource",
        "PortableSpawnGrenadeResource",
        "PortableTurretResource",
        "RepairLocationEntityResource",
        "RespawnEntityResource",
        "RewardPickUpResource",
        "RocketResource",
        "ScriptedPickUpResource",
        "ScriptedWeaponResource",
        "SearchAndRetrieveBaseResource",
        "SearchAndRetrieveObjectResource",
        "SentryBotResource",
        "SequenceCameraEntityResource",
        "SequenceResource",
        "ShotGunEjectorResource",
        "SignalGrenadeResource",
        "SoldierResource",
        "SpawnAreaResource",
        "SpearResource",
        "SpectatorCameraEntityResource",
        "SpiderMineResource",
        "StaticModelEntityResource",
        "SwitchResource",
        "TankResource",
        "ThrowableResource",
        "TurretResource",
        "UniquePickUpResource",
        "UnlockableFeaturePickUpResource",
        "ValveResource",
        "VehicleInternalCameraEntityResource",
        "VehicleRocketLauncherResource",
        "VehicleWeaponPickUpResource",
        "VehicleWeaponResource",
        "WeaponPickUpResource",
    }
)

ATTACHMENT_MOVER_TYPES: Final = frozenset(
    {
        "AttachPhysicsRagdollToBoneMoverResource",
        "AttachToBoneMoverResource",
        "AttachToHelperMoverResource",
    }
)


def parse_entity_resource(
    decoder: CoreDecoder,
    obj: CoreObject,
    reader: BinaryReader,
) -> EntityResource:
    prefix = read_resource_prefix(decoder, reader)
    animator = decoder.core.read_reference(reader)
    lockable = reader.u8()
    mover = decoder.core.read_reference(reader)
    model = decoder.core.read_reference(reader)
    destructibility = decoder.core.read_reference(reader)
    child_count = reader.count("child-entity count")
    child_entities = tuple(
        decoder.core.read_reference(reader) for _ in range(child_count)
    )
    trailing = reader.read(reader.remaining)
    return EntityResource(
        prefix=prefix,
        animator=animator,
        lockable=lockable,
        mover=mover,
        model=model,
        destructibility=destructibility,
        child_entities=child_entities,
        body_variants=(
            _humanoid_body_variant_references(
                decoder,
                obj,
                trailing,
            )
            if obj.type_name in HUMANOID_ENTITY_TYPES
            else ()
        ),
        trailing=trailing,
    )


def parse_attachment_mover(
    decoder: CoreDecoder,
    obj: CoreObject,
    reader: BinaryReader,
) -> AttachmentMover:
    prefix = read_resource_prefix(decoder, reader)
    local_matrix = _read_matrix4(reader)
    post_local_matrix = _read_matrix4(reader)
    align_to_world = reader.u8()
    target_kind = (
        "helper"
        if obj.type_name == "AttachToHelperMoverResource"
        else "bone"
    )
    target_name = decoder.core.read_string_index(reader, "String")
    return AttachmentMover(
        prefix=prefix,
        local_matrix=local_matrix,
        post_local_matrix=post_local_matrix,
        align_to_world=align_to_world,
        target_kind=target_kind,
        target_name=target_name,
        trailing=reader.read(reader.remaining),
    )


def parse_model_resource(
    decoder: CoreDecoder,
    obj: CoreObject,
    reader: BinaryReader,
) -> ModelResource:
    prefix = read_resource_prefix(decoder, reader)
    mesh = decoder.core.read_reference(reader)
    return ModelResource(prefix, mesh, reader.read(reader.remaining))


def parse_model_part(
    decoder: CoreDecoder,
    obj: CoreObject,
    reader: BinaryReader,
) -> ModelPart:
    prefix = read_resource_prefix(decoder, reader)
    mesh = decoder.core.read_reference(reader)
    return ModelPart(prefix, mesh, reader.read(reader.remaining))


def parse_destructibility_part_state(
    decoder: CoreDecoder,
    obj: CoreObject,
    reader: BinaryReader,
) -> DestructibilityPartState:
    prefix = read_resource_prefix(decoder, reader)
    model_part = decoder.core.read_reference(reader)
    header_start = reader.position
    reader.f32()
    reader.f32()
    reaction_count = reader.count("destructibility hit-reaction count")
    for _ in range(reaction_count):
        for _ in range(8):
            flag = reader.u8()
            if flag not in (0, 1):
                reader.fail(
                    "invalid destructibility hit-reaction flag",
                    observed=flag,
                )
        reader.f32()
        decoder.core.read_reference(reader)
        decoder.core.read_reference(reader)
    header = reader.data[header_start : reader.position].tobytes()
    offset_matrix = _read_matrix4(reader)
    has_lock_position = reader.u8()
    if has_lock_position not in (0, 1):
        reader.fail(
            "invalid destructibility lock-position flag",
            observed=has_lock_position,
        )
    return DestructibilityPartState(
        prefix=prefix,
        model_part=model_part,
        header=header,
        offset_matrix=offset_matrix,
        has_lock_position=bool(has_lock_position),
        trailing=reader.read(reader.remaining),
    )


def parse_state_based_destructibility(
    decoder: CoreDecoder,
    obj: CoreObject,
    reader: BinaryReader,
) -> StateBasedDestructibility:
    prefix = read_resource_prefix(decoder, reader)
    body_start = reader.position
    expected_count = _allocated_destructibility_part_count(obj)
    candidates = []
    for layout_index, (header_size, part_prefix_size) in enumerate(
        DESTRUCTIBILITY_LAYOUTS
    ):
        candidate = reader.clone()
        try:
            candidate.skip(header_size)
            part_count = candidate.count(
                "destructibility part-state count"
            )
            if (
                expected_count is not None
                and part_count != expected_count
            ):
                continue
            part_states = []
            part_ids = []
            part_enabled = []
            part_bone_names = []
            part_local_matrices = []
            part_fields = []
            for _ in range(part_count):
                fields_before = candidate.read(part_prefix_size)
                (
                    part_id,
                    enabled,
                    bone_name,
                    local_matrix,
                ) = _parse_destructibility_part_fields(
                    decoder,
                    fields_before,
                )
                reference = decoder.core.read_reference(candidate)
                fields_after = candidate.read(
                    DESTRUCTIBILITY_PART_SUFFIX_SIZE
                )
                if (
                    reference.local_index is not None
                    and decoder.core.objects[
                        reference.local_index
                    ].type_name
                    != "DestructibilityPartStateResource"
                ):
                    raise ValueError("not a destructibility part-state")
                part_states.append(reference)
                part_ids.append(part_id)
                part_enabled.append(enabled)
                part_bone_names.append(bone_name)
                part_local_matrices.append(local_matrix)
                part_fields.append((fields_before, fields_after))
        except (Kz2AssetError, ValueError):
            continue
        candidates.append(
            (
                sum(not reference.is_null for reference in part_states),
                part_count,
                -layout_index,
                header_size,
                candidate,
                tuple(part_states),
                tuple(part_ids),
                tuple(part_enabled),
                tuple(part_bone_names),
                tuple(part_local_matrices),
                tuple(part_fields),
            )
        )
    if not candidates:
        return StateBasedDestructibility(
            prefix=prefix,
            header=b"",
            part_states=(),
            part_ids=(),
            part_enabled=(),
            part_bone_names=(),
            part_local_matrices=(),
            part_fields=(),
            trailing=reader.read(reader.remaining),
            part_states_complete=False,
        )

    (
        _,
        _,
        _,
        header_size,
        parsed_reader,
        selected_part_states,
        selected_part_ids,
        selected_part_enabled,
        selected_part_bone_names,
        selected_part_local_matrices,
        selected_part_fields,
    ) = max(candidates, key=lambda candidate: candidate[:3])
    header = reader.data[
        body_start : body_start + header_size
    ].tobytes()
    reader.seek(parsed_reader.position)
    return StateBasedDestructibility(
        prefix=prefix,
        header=header,
        part_states=selected_part_states,
        part_ids=selected_part_ids,
        part_enabled=selected_part_enabled,
        part_bone_names=selected_part_bone_names,
        part_local_matrices=selected_part_local_matrices,
        part_fields=selected_part_fields,
        trailing=reader.read(reader.remaining),
        part_states_complete=True,
    )


def _allocated_destructibility_part_count(
    obj: CoreObject,
) -> int | None:
    counts = [
        record.fields[0] // DESTRUCTIBILITY_PART_ALLOCATION_SIZE
        for record in obj.allocation_lists[0]
        if record.fields[0] > 0
        and record.fields[0] % DESTRUCTIBILITY_PART_ALLOCATION_SIZE == 0
    ]
    return max(counts) if counts else None


def _read_matrix4(reader: BinaryReader) -> Matrix4:
    return (
        (reader.f32(), reader.f32(), reader.f32(), reader.f32()),
        (reader.f32(), reader.f32(), reader.f32(), reader.f32()),
        (reader.f32(), reader.f32(), reader.f32(), reader.f32()),
        (reader.f32(), reader.f32(), reader.f32(), reader.f32()),
    )


def _parse_destructibility_part_fields(
    decoder: CoreDecoder,
    fields: bytes,
) -> tuple[int, bool, str, Matrix4]:
    reader = BinaryReader.from_bytes(
        fields,
        source=f"{decoder.core.source}: destructibility part",
    )
    part_id = reader.i32()
    enabled = reader.u8()
    if enabled not in (0, 1):
        reader.fail(
            "invalid destructibility enabled flag",
            observed=enabled,
        )
    reader.f32()
    reader.f32()
    damage_pass_count = reader.count("damage-pass-on count")
    for _ in range(damage_pass_count):
        reader.i32()
        reader.f32()
    bone_name = decoder.core.read_string_index(reader, "String")
    reader.i32()
    local_matrix = _read_matrix4(reader)
    decoder.core.read_reference(reader)
    padding = reader.read(reader.remaining)
    if len(padding) > 1 or any(padding):
        reader.fail(
            "unsupported destructibility part fields",
            observed=padding.hex(),
        )
    if not all(
        math.isfinite(value)
        for row in local_matrix
        for value in row
    ):
        reader.fail("non-finite destructibility part matrix")
    return part_id, bool(enabled), bone_name, local_matrix


def _humanoid_body_variant_references(
    decoder: CoreDecoder,
    obj: CoreObject,
    trailing: bytes,
) -> tuple[CoreReference, ...]:
    expected_counts = {
        record.fields[0] // HUMANOID_BODY_VARIANT_SIZE
        for record in obj.allocation_lists[0]
        if record.fields[0] > 0
        and record.fields[0] % HUMANOID_BODY_VARIANT_SIZE == 0
        and record.fields[0] // HUMANOID_BODY_VARIANT_SIZE
        <= MAX_HUMANOID_BODY_VARIANTS
    }
    if not expected_counts:
        return ()

    candidates = []
    for offset in range(len(trailing)):
        probe = BinaryReader.from_bytes(
            trailing,
            source=(
                f"{decoder.core.source}: object {obj.index} "
                f"{obj.type_name} body variants"
            ),
        )
        probe.seek(offset)
        try:
            count = probe.count(
                "humanoid body-variant count",
                maximum=MAX_HUMANOID_BODY_VARIANTS,
            )
            if not count or count not in expected_counts:
                continue
            references = []
            for _ in range(count):
                reference = decoder.core.read_reference(probe)
                if reference.is_null or (
                    reference.local_index is not None
                    and decoder.core.objects[
                        reference.local_index
                    ].type_name
                    != "ModelPartResource"
                ):
                    raise ValueError("invalid humanoid body model part")
                eye_translations = tuple(probe.f32() for _ in range(6))
                if not all(
                    math.isfinite(value) and abs(value) <= 100.0
                    for value in eye_translations
                ):
                    raise ValueError("invalid humanoid eye translation")
                references.append(reference)

            blood_puddle = decoder.core.read_reference(probe)
            if (
                blood_puddle.local_index is not None
                and decoder.core.objects[
                    blood_puddle.local_index
                ].type_name
                != "BloodPuddleSettings"
            ):
                raise ValueError("invalid humanoid body-variant context")
            if probe.u8() not in (0, 1):
                raise ValueError("invalid humanoid face flag")
            decoder.core.read_string_index(probe, "String")
            if not math.isfinite(probe.f32()):
                raise ValueError("invalid humanoid facial blend time")
        except (Kz2AssetError, ValueError):
            continue
        candidates.append(tuple(references))

    if len(candidates) > 1:
        probe = BinaryReader.from_bytes(
            trailing,
            source=f"{decoder.core.source}: object {obj.index}",
        )
        probe.fail(
            "humanoid body-variant layout is ambiguous",
            observed=len(candidates),
        )
    return candidates[0] if candidates else ()
