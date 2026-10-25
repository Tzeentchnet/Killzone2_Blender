"""Animation-state wrappers and skeleton animation bone maps."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .core import CoreReference
from .geometry import read_resource_prefix
from .model import (
    AnimationState,
    DirectionalAnimation,
    DirectionalAnimationEntry,
    SkeletonAnimationBoneMap,
    TimeMappedSkeletonAnimation,
)

if TYPE_CHECKING:
    from .binary import BinaryReader
    from .core import CoreObject
    from .decoder import CoreDecoder


MAX_ANIMATION_ENTRIES = 65_535


def animation_state_clip_names(
    decoder: CoreDecoder,
) -> dict[int, tuple[str, ...]]:
    names: dict[int, list[str]] = {}
    for _, decoded in decoder.iter_type("AnimationState"):
        if not isinstance(decoded, AnimationState) or not decoded.prefix.name:
            continue
        for animation_index in _animation_leaf_indices(
            decoder,
            decoded.animation,
        ):
            values = names.setdefault(animation_index, [])
            if decoded.prefix.name not in values:
                values.append(decoded.prefix.name)
    return {
        animation_index: tuple(values)
        for animation_index, values in names.items()
    }


def _animation_leaf_indices(
    decoder: CoreDecoder,
    reference: CoreReference,
    *,
    visiting: frozenset[int] = frozenset(),
) -> tuple[int, ...]:
    if reference.local_index is None:
        return ()
    index = reference.local_index
    if index in visiting:
        return ()
    record = decoder.core.objects[index]
    if record.type_name == "SkeletonAnimationResource":
        return (index,)

    decoded = decoder.decode(record)
    if isinstance(decoded, TimeMappedSkeletonAnimation):
        references = (decoded.animation,)
    elif isinstance(decoded, DirectionalAnimation):
        references = tuple(entry.animation for entry in decoded.entries)
    else:
        return ()

    leaves = []
    next_visiting = visiting | {index}
    for child in references:
        for leaf in _animation_leaf_indices(
            decoder,
            child,
            visiting=next_visiting,
        ):
            if leaf not in leaves:
                leaves.append(leaf)
    return tuple(leaves)


def parse_skeleton_animation_bone_map(
    decoder: CoreDecoder,
    obj: CoreObject,
    reader: BinaryReader,
) -> SkeletonAnimationBoneMap:
    count = reader.count(
        "skeleton animation bone-map count",
        maximum=MAX_ANIMATION_ENTRIES,
    )
    bone_names = tuple(
        decoder.core.read_string_index(reader, "String")
        for _ in range(count)
    )
    return SkeletonAnimationBoneMap(
        bone_names=bone_names,
        trailing=reader.read(reader.remaining),
    )


def parse_animation_state(
    decoder: CoreDecoder,
    obj: CoreObject,
    reader: BinaryReader,
) -> AnimationState:
    return AnimationState(
        prefix=read_resource_prefix(decoder, reader),
        animation=decoder.core.read_reference(reader),
        trailing=reader.read(reader.remaining),
    )


def parse_time_mapped_skeleton_animation(
    decoder: CoreDecoder,
    obj: CoreObject,
    reader: BinaryReader,
) -> TimeMappedSkeletonAnimation:
    prefix = read_resource_prefix(decoder, reader)
    version = reader.u32()
    wrapper_flags = reader.u8()
    wrapper_index = reader.adaptive_reference_index(
        len(decoder.core.objects)
    )
    animation = decoder.core.read_reference(reader)
    return TimeMappedSkeletonAnimation(
        prefix=prefix,
        version=version,
        wrapper_flags=wrapper_flags,
        wrapper_index=wrapper_index,
        animation=animation,
        trailing=reader.read(reader.remaining),
    )


def parse_directional_animation(
    decoder: CoreDecoder,
    obj: CoreObject,
    reader: BinaryReader,
) -> DirectionalAnimation:
    prefix = read_resource_prefix(decoder, reader)
    version = reader.u32()
    count = reader.u32()
    if count > MAX_ANIMATION_ENTRIES:
        reader.fail(
            "directional animation entry count is too large",
            expected=f"at most {MAX_ANIMATION_ENTRIES}",
            observed=count,
        )
    entries = tuple(
        DirectionalAnimationEntry(
            animation=decoder.core.read_reference(reader),
            weight=reader.f32(),
            heading_degrees=reader.f32(),
            unknown=reader.u8(),
        )
        for _ in range(count)
    )
    return DirectionalAnimation(
        prefix=prefix,
        version=version,
        entries=entries,
        trailing=reader.read(reader.remaining),
    )
