"""Entity and model-resource decoders used by level placements."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .binary import BinaryReader
from .errors import Kz2AssetError
from .geometry import read_resource_prefix
from .model import (
    DestructibilityPartState,
    EntityResource,
    ModelPart,
    ModelResource,
    StateBasedDestructibility,
)

if TYPE_CHECKING:
    from .core import CoreObject
    from .decoder import CoreDecoder


DESTRUCTIBILITY_LAYOUTS = (
    (90, 86),
    (91, 87),
    (103, 87),
)
DESTRUCTIBILITY_PART_ALLOCATION_SIZE = 144
DESTRUCTIBILITY_PART_SUFFIX_SIZE = 2


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
    return EntityResource(
        prefix=prefix,
        animator=animator,
        lockable=lockable,
        mover=mover,
        model=model,
        destructibility=destructibility,
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
    return DestructibilityPartState(
        prefix,
        model_part,
        reader.read(reader.remaining),
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
            part_fields = []
            for _ in range(part_count):
                fields_before = candidate.read(part_prefix_size)
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
                tuple(part_fields),
            )
        )
    if not candidates:
        return StateBasedDestructibility(
            prefix=prefix,
            header=b"",
            part_states=(),
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
        part_states,
        part_fields,
    ) = max(candidates, key=lambda candidate: candidate[:3])
    header = reader.data[
        body_start : body_start + header_size
    ].tobytes()
    reader.seek(parsed_reader.position)
    return StateBasedDestructibility(
        prefix=prefix,
        header=header,
        part_states=part_states,
        part_fields=part_fields,
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
