"""Rest-skeleton and KZ2 Skeleton resource decoding."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from .binary import BinaryReader
from .geometry import read_resource_prefix
from .model import EdgeJoint, EdgeSkeletonHeader, IkControl, Joint, Skeleton

if TYPE_CHECKING:
    from .core import CoreObject
    from .decoder import CoreDecoder


MAX_JOINTS = 65_535


def parse_skeleton(
    decoder: CoreDecoder,
    obj: CoreObject,
    reader: BinaryReader,
) -> Skeleton:
    prefix = read_resource_prefix(decoder, reader)

    bone_set_count = reader.count("bone-set count", maximum=MAX_JOINTS)
    bone_sets = tuple(
        decoder.core.read_string_index(reader, "String")
        for _ in range(bone_set_count)
    )

    reference_count = reader.count("skeleton reference count", maximum=MAX_JOINTS)
    references = tuple(
        decoder.core.read_reference(reader) for _ in range(reference_count)
    )

    unknown_count = reader.count("24-byte skeleton record count", maximum=MAX_JOINTS)
    unknown_records = tuple(reader.read(24) for _ in range(unknown_count))

    ik_count = reader.count("IK control count", maximum=MAX_JOINTS)
    ik_controls = tuple(_read_ik_control(decoder, reader) for _ in range(ik_count))

    animation_channel_count = reader.count(
        "animation-channel count",
        maximum=MAX_JOINTS,
    )
    animation_channels = tuple(
        decoder.core.read_string_index(reader, "String")
        for _ in range(animation_channel_count)
    )

    edge_size = reader.count("ES00 byte count", maximum=64 * 1024 * 1024)
    edge_blob = reader.read(edge_size)
    edge_header, edge_joints = _parse_edge_skeleton(edge_blob, reader.source)

    reader.align(4)
    block_start = reader.position
    reader.require(64)
    fixed = reader.read(64)
    auxiliary_count = fixed[0x0C]
    joint_count = fixed[0x0D]
    secondary_count = fixed[0x0E]
    string_chunk_length = int.from_bytes(fixed[0x12:0x14], "big")
    if joint_count > MAX_JOINTS:
        reader.fail(
            "expanded skeleton has too many joints",
            observed=joint_count,
        )

    reader.align(64, origin=block_start)
    reader.skip(24 * joint_count)
    if auxiliary_count:
        reader.align(16, origin=block_start)
        reader.skip(80 * auxiliary_count)

    reader.align(16, origin=block_start)
    raw_joints = []
    for _ in range(joint_count):
        rotation = (reader.f32(), reader.f32(), reader.f32(), reader.f32())
        translation = (reader.f32(), reader.f32(), reader.f32())
        name_offset = reader.u32()
        parent_offset = reader.u32()
        unknown = reader.read(12)
        raw_joints.append(
            (rotation, translation, name_offset, parent_offset, unknown)
        )

    reader.align(4, origin=block_start)
    unknown_array_1 = tuple(reader.u32() for _ in range(joint_count))
    reader.align(4, origin=block_start)
    second_length = joint_count + secondary_count + joint_count
    unknown_array_2 = tuple(reader.u32() for _ in range(second_length))
    string_chunk_offset = reader.position
    string_chunk = reader.read(string_chunk_length)

    joints = tuple(
        Joint(
            name=_read_chunk_string(
                string_chunk,
                name_offset,
                reader,
                string_chunk_offset,
            ),
            parent_name=_read_chunk_string(
                string_chunk,
                parent_offset,
                reader,
                string_chunk_offset,
            ),
            rotation_xyzw=rotation,
            translation=translation,
            unknown=unknown,
        )
        for rotation, translation, name_offset, parent_offset, unknown in raw_joints
    )
    _validate_hierarchy(joints, reader)
    if edge_header.joint_lookup_count not in (0, len(joints)):
        reader.fail(
            "ES00 and expanded skeleton joint counts differ",
            expected=len(joints),
            observed=edge_header.joint_lookup_count,
        )
    if edge_joints and len(edge_joints) != len(joints):
        reader.fail(
            "ES00 and expanded skeleton joint counts differ",
            expected=len(joints),
            observed=len(edge_joints),
        )

    return Skeleton(
        prefix=prefix,
        bone_sets=bone_sets,
        references=references,
        unknown_records=unknown_records,
        ik_controls=ik_controls,
        animation_channels=animation_channels,
        edge_blob=edge_blob,
        edge_header=edge_header,
        edge_joints=edge_joints,
        joints=joints,
        auxiliary_count=auxiliary_count,
        secondary_count=secondary_count,
        unknown_arrays=(unknown_array_1, unknown_array_2),
    )


def _read_ik_control(
    decoder: CoreDecoder,
    reader: BinaryReader,
) -> IkControl:
    name = decoder.core.read_string_index(reader, "String")
    channel_count = reader.count("IK channel-name count", maximum=MAX_JOINTS)
    channel_names = tuple(
        decoder.core.read_string_index(reader, "String")
        for _ in range(channel_count)
    )
    return IkControl(
        name=name,
        channel_names=channel_names,
        start_joint_index=reader.u8(),
        mid_joint_index=reader.u8(),
        end_joint_index=reader.u8(),
        hinge_axis=(reader.f32(), reader.f32(), reader.f32()),
        hinge_max_limit=reader.f32(),
        hinge_min_limit=reader.f32(),
    )


def _parse_edge_skeleton(
    blob: bytes,
    source: str,
) -> tuple[EdgeSkeletonHeader, tuple[EdgeJoint, ...]]:
    reader = BinaryReader.from_bytes(blob, source=f"{source}: ES00")
    magic = reader.read(4)
    if magic != b"ES00":
        reader.fail(
            "invalid Edge skeleton tag",
            offset=0,
            expected=b"ES00",
            observed=magic,
        )
    reader.u32()
    total_size = reader.u32()
    if total_size != len(blob):
        reader.fail(
            "ES00 total size mismatch",
            offset=8,
            expected=len(blob),
            observed=total_size,
        )
    joint_count = reader.u16()
    user_count = reader.u16()
    unknown_count = reader.u16()
    reserved = reader.u16()
    offsets = tuple(reader.u32() for _ in range(6))
    for index, value in enumerate(offsets):
        if value:
            field_offset = 0x14 + index * 4
            target = field_offset + value
            if target > len(blob):
                reader.fail(
                    "ES00 self-relative offset is out of bounds",
                    offset=field_offset,
                    expected=f"at most {len(blob)}",
                    observed=target,
                )
    header = EdgeSkeletonHeader(
        total_size=total_size,
        joint_lookup_count=joint_count,
        user_channel_lookup_count=user_count,
        unknown_count=unknown_count,
        unknown_reserved=reserved,
        offsets=offsets,  # type: ignore[arg-type]
    )
    if joint_count == 0:
        return header, ()

    pose_offset = _edge_offset_target(reader, 0x14, offsets[0], required=True)
    parent_offset = _edge_offset_target(reader, 0x18, offsets[1], required=True)
    pose_reader = BinaryReader.from_bytes(blob, source=f"{source}: ES00 pose")
    pose_reader.seek(pose_offset)
    transforms = []
    for _ in range(joint_count):
        rotation = (
            pose_reader.f32(),
            pose_reader.f32(),
            pose_reader.f32(),
            pose_reader.f32(),
        )
        translation = (
            pose_reader.f32(),
            pose_reader.f32(),
            pose_reader.f32(),
        )
        translation_padding = pose_reader.f32()
        scale = (
            pose_reader.f32(),
            pose_reader.f32(),
            pose_reader.f32(),
        )
        scale_padding = pose_reader.f32()
        values = (*rotation, *translation, translation_padding, *scale, scale_padding)
        if not all(math.isfinite(value) for value in values):
            pose_reader.fail(
                "ES00 fallback transform is not finite",
                observed=values,
            )
        quaternion_norm = sum(value * value for value in rotation)
        if not math.isclose(
            quaternion_norm,
            1.0,
            rel_tol=1e-4,
            abs_tol=1e-4,
        ):
            pose_reader.fail(
                "ES00 fallback rotation is not normalized",
                expected=1.0,
                observed=quaternion_norm,
            )
        transforms.append((rotation, translation, scale))

    parent_reader = BinaryReader.from_bytes(
        blob,
        source=f"{source}: ES00 parents",
    )
    parent_reader.seek(parent_offset)
    parents: list[int | None] = []
    for index in range(joint_count):
        parent = parent_reader.u16()
        if parent == 0xFFFF:
            parents.append(None)
        elif parent >= joint_count or parent == index:
            parent_reader.fail(
                "ES00 parent joint index is invalid",
                expected=f"0..{joint_count - 1} or 65535",
                observed=parent,
            )
        else:
            parents.append(parent)
    _validate_edge_hierarchy(parents, parent_reader)

    return header, tuple(
        EdgeJoint(rotation, translation, scale, parent)
        for (rotation, translation, scale), parent in zip(
            transforms,
            parents,
            strict=True,
        )
    )


def _edge_offset_target(
    reader: BinaryReader,
    field_offset: int,
    stored: int,
    *,
    required: bool,
) -> int:
    if stored == 0:
        if required:
            reader.fail(
                "required ES00 offset is null",
                offset=field_offset,
            )
        return 0
    target = field_offset + stored
    if target >= len(reader.data):
        reader.fail(
            "ES00 self-relative offset is out of bounds",
            offset=field_offset,
            expected=f"less than {len(reader.data)}",
            observed=target,
        )
    return target


def _validate_edge_hierarchy(
    parents: list[int | None],
    reader: BinaryReader,
) -> None:
    resolved: set[int] = set()

    def resolve(index: int, visiting: set[int]) -> None:
        if index in resolved:
            return
        if index in visiting:
            reader.fail(
                "ES00 parent hierarchy contains a cycle",
                observed=index,
            )
        visiting.add(index)
        parent = parents[index]
        if parent is not None:
            resolve(parent, visiting)
        visiting.remove(index)
        resolved.add(index)

    for index in range(len(parents)):
        resolve(index, set())


def _read_chunk_string(
    chunk: bytes,
    offset: int,
    reader: BinaryReader,
    absolute_chunk_offset: int,
) -> str:
    if offset >= len(chunk):
        reader.fail(
            "skeleton string offset is out of bounds",
            offset=absolute_chunk_offset,
            expected=f"less than {len(chunk)}",
            observed=offset,
        )
    end = chunk.find(b"\0", offset)
    if end < 0:
        reader.fail(
            "unterminated skeleton string",
            offset=absolute_chunk_offset + offset,
        )
    try:
        return chunk[offset:end].decode("utf-8")
    except UnicodeDecodeError as exc:
        reader.fail(
            "invalid UTF-8 skeleton string",
            offset=absolute_chunk_offset + offset,
            observed=str(exc),
        )
    raise AssertionError("unreachable")


def _validate_hierarchy(joints: tuple[Joint, ...], reader: BinaryReader) -> None:
    names = {joint.name for joint in joints}
    if len(names) != len(joints):
        reader.fail("expanded skeleton contains duplicate joint names")
    for joint in joints:
        if joint.parent_name and joint.parent_name not in names:
            reader.fail(
                "skeleton parent name is unresolved",
                expected="a decoded joint name",
                observed=joint.parent_name,
            )
