"""Sony Edge EA02 transform decoding for Killzone 2 animation clips."""

from __future__ import annotations

import bisect
import itertools
import math
import struct
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, cast

from .binary import BinaryReader, BitReader
from .errors import FormatError, UnsupportedFeatureError
from .geometry import read_resource_prefix
from .model import SkeletonAnimation

if TYPE_CHECKING:
    from .core import CoreObject, CoreReference
    from .decoder import CoreDecoder


ChannelKind = Literal["rotation", "translation", "scale", "user"]
Quaternion = tuple[float, float, float, float]
Vector3 = tuple[float, float, float]
SizeTable = tuple[int, int, int, int, int, int, int, int]
CHANNEL_KINDS: tuple[ChannelKind, ...] = (
    "rotation",
    "translation",
    "scale",
    "user",
)
TRANSFORM_KINDS: tuple[ChannelKind, ...] = (
    "rotation",
    "translation",
    "scale",
)


@dataclass(frozen=True, slots=True)
class PackingComponent:
    sign_bits: int
    exponent_bits: int
    mantissa_bits: int

    @property
    def bit_count(self) -> int:
        return self.sign_bits + self.exponent_bits + self.mantissa_bits


@dataclass(frozen=True, slots=True)
class PackingSpec:
    components: tuple[PackingComponent, PackingComponent, PackingComponent]
    omitted_component: int

    @classmethod
    def from_u32(cls, value: int) -> PackingSpec:
        components = (
            PackingComponent((value >> 31) & 1, (value >> 27) & 0xF, (value >> 22) & 0x1F),
            PackingComponent((value >> 21) & 1, (value >> 17) & 0xF, (value >> 12) & 0x1F),
            PackingComponent((value >> 11) & 1, (value >> 7) & 0xF, (value >> 2) & 0x1F),
        )
        return cls(components, value & 0x3)

    @property
    def key_bits(self) -> int:
        return sum(component.bit_count for component in self.components)


@dataclass(frozen=True, slots=True)
class FrameSet:
    base_frame: int
    intra_frame_count: int
    dma_word: int
    data_offset: int


@dataclass(frozen=True, slots=True)
class JointSample:
    rotation_xyzw: Quaternion | None = None
    translation: Vector3 | None = None
    scale: Vector3 | None = None


@dataclass(slots=True)
class EdgeAnimation:
    source: str
    blob: bytes
    duration: float
    sample_frequency: float
    header_size: int
    joint_count: int
    frame_count: int
    flags: int
    channel_tables: dict[ChannelKind, tuple[int, ...]]
    constant_tables: dict[ChannelKind, tuple[int, ...]]
    frame_sets: tuple[FrameSet, ...]
    constant_values: dict[ChannelKind, tuple[tuple[float, ...], ...]]
    constant_specs: dict[ChannelKind, PackingSpec]
    animated_specs: dict[ChannelKind, tuple[PackingSpec, ...]]
    counts: tuple[int, int, int, int, int, int, int, int]
    packing_offset: int = 0
    locomotion_delta: JointSample | None = None
    decode_supported: bool = False
    unsupported_reason: str = ""

    def sample_frame(self, frame: int) -> tuple[JointSample, ...]:
        if not self.decode_supported:
            raise UnsupportedFeatureError(
                self.unsupported_reason
                or f"{self.source}: KZ2 EA02 transform packing is unsupported"
            )
        if frame < 0 or frame >= self.frame_count:
            raise IndexError(f"frame {frame} is outside 0..{self.frame_count - 1}")
        samples: list[dict[ChannelKind, tuple[float, ...] | None]] = [
            {
                "rotation": None,
                "translation": None,
                "scale": None,
            }
            for _ in range(self.joint_count)
        ]
        for kind in TRANSFORM_KINDS:
            for joint_index, value in zip(
                self.constant_tables[kind],
                self.constant_values[kind],
                strict=True,
            ):
                samples[joint_index][kind] = value

        if any(
            self.channel_tables[kind]
            for kind in TRANSFORM_KINDS
        ):
            frame_set, local_integer, fraction = self._locate_frame(frame)
            layout = _FrameSetLayout.parse(
                self.blob,
                frame_set,
                sum(len(self.channel_tables[kind]) for kind in self.channel_tables),
                self.source,
            )
            bit_base_by_kind = self._intra_bit_bases(frame_set.intra_frame_count)
            for kind in TRANSFORM_KINDS:
                values = self._sample_animated_group(
                    kind,
                    frame_set,
                    layout,
                    local_integer,
                    fraction,
                    bit_base_by_kind[kind],
                )
                for joint_index, value in zip(
                    self.channel_tables[kind],
                    values,
                    strict=True,
                ):
                    samples[joint_index][kind] = value

        return tuple(
            JointSample(
                rotation_xyzw=cast(Quaternion | None, value["rotation"]),
                translation=cast(Vector3 | None, value["translation"]),
                scale=cast(Vector3 | None, value["scale"]),
            )
            for value in samples
        )

    def iter_frames(self):
        for frame in range(self.frame_count):
            yield frame, self.sample_frame(frame)

    def _locate_frame(self, frame: int) -> tuple[FrameSet, int, float]:
        usable = self.frame_sets[:-1] if len(self.frame_sets) > 1 else self.frame_sets
        bases = [frame_set.base_frame for frame_set in usable]
        index = max(0, bisect.bisect_right(bases, frame) - 1)
        frame_set = usable[index]
        local = frame - frame_set.base_frame
        if local > frame_set.intra_frame_count:
            return frame_set, frame_set.intra_frame_count, 1.0
        return frame_set, local, 0.0

    def _intra_bit_bases(self, intra_count: int) -> dict[ChannelKind, int]:
        result: dict[ChannelKind, int] = {}
        cursor = 0
        for kind in CHANNEL_KINDS:
            result[kind] = cursor
            cursor += len(self.channel_tables[kind]) * intra_count
        return result

    def _sample_animated_group(
        self,
        kind: ChannelKind,
        frame_set: FrameSet,
        layout: _FrameSetLayout,
        frame_integer: int,
        frame_fraction: float,
        group_bit_base: int,
    ) -> tuple[tuple[float, ...], ...]:
        channels = self.channel_tables[kind]
        specs = self.animated_specs[kind]
        enabled_counts = tuple(
            _count_bits(
                self.blob,
                layout.intra_bits,
                group_bit_base + index * frame_set.intra_frame_count,
                frame_set.intra_frame_count,
            )
            for index in range(len(channels))
        )
        results = []
        for channel_index in range(len(channels)):
            bit_offset = group_bit_base + channel_index * frame_set.intra_frame_count
            enabled_for_channel = enabled_counts[channel_index]
            enabled_before_frame = _count_bits(
                self.blob,
                layout.intra_bits,
                bit_offset,
                frame_integer,
            )

            previous = _last_set_bit(
                self.blob,
                layout.intra_bits,
                bit_offset,
                frame_integer,
            )
            following = _first_set_bit(
                self.blob,
                layout.intra_bits,
                bit_offset + frame_integer,
                frame_set.intra_frame_count - frame_integer,
            )
            left_distance = frame_integer - (previous + 1 if previous is not None else 0)
            right_distance = (
                following + 1
                if following is not None
                else frame_set.intra_frame_count - frame_integer + 1
            )
            denominator = left_distance + right_distance
            alpha = (
                (left_distance + frame_fraction) / denominator
                if denominator
                else 0.0
            )

            initial = self._decode_group_key(
                kind,
                layout.initial[kind],
                channel_index,
                specs,
            )
            final = self._decode_group_key(
                kind,
                layout.final[kind],
                channel_index,
                specs,
            )
            if enabled_before_frame == 0:
                left = initial
            else:
                left = self._decode_intra_key(
                    kind,
                    layout.intra[kind],
                    channel_index,
                    enabled_before_frame - 1,
                    enabled_counts,
                    specs,
                )
            if enabled_before_frame == enabled_for_channel:
                right = final
            else:
                right = self._decode_intra_key(
                    kind,
                    layout.intra[kind],
                    channel_index,
                    enabled_before_frame,
                    enabled_counts,
                    specs,
                )
            results.append(_interpolate(kind, left, right, alpha))
        return tuple(results)

    def _decode_group_key(
        self,
        kind: ChannelKind,
        data_offset: int,
        channel_index: int,
        specs: tuple[PackingSpec, ...],
    ) -> tuple[float, ...]:
        preceding_bits = sum(spec.key_bits for spec in specs[:channel_index])
        return _decode_packed_key(
            self.blob,
            data_offset * 8 + preceding_bits,
            specs[channel_index],
            rotation=kind == "rotation",
            source=self.source,
        )

    def _decode_intra_key(
        self,
        kind: ChannelKind,
        data_offset: int,
        channel_index: int,
        channel_key_index: int,
        enabled_counts: tuple[int, ...],
        specs: tuple[PackingSpec, ...],
    ) -> tuple[float, ...]:
        spec = specs[channel_index]
        bit_offset = sum(
            enabled_counts[index] * specs[index].key_bits
            for index in range(channel_index)
        )
        bit_offset += channel_key_index * spec.key_bits
        return _decode_packed_key(
            self.blob,
            data_offset * 8 + bit_offset,
            spec,
            rotation=kind == "rotation",
            source=self.source,
        )


@dataclass(frozen=True, slots=True)
class _FrameSetLayout:
    sizes: SizeTable
    initial: dict[ChannelKind, int]
    intra_bits: int
    intra: dict[ChannelKind, int]
    next_frame_set_offset: int
    final_sizes: SizeTable
    final: dict[ChannelKind, int]

    @classmethod
    def parse(
        cls,
        blob: bytes,
        frame_set: FrameSet,
        animated_channel_count: int,
        source: str,
        expected_next_offset: int | None = None,
    ) -> _FrameSetLayout:
        reader = BinaryReader.from_bytes(blob, source=source)
        reader.seek(frame_set.data_offset)
        sizes = cast(SizeTable, tuple(reader.u16() for _ in range(8)))
        initial: dict[ChannelKind, int] = {}
        for index, kind in enumerate(CHANNEL_KINDS):
            initial[kind] = reader.position
            reader.skip(sizes[index])
        intra_bits = reader.position
        bitmap_size = (
            animated_channel_count * frame_set.intra_frame_count + 7
        ) // 8
        reader.skip(bitmap_size)
        intra: dict[ChannelKind, int] = {}
        for index, kind in enumerate(TRANSFORM_KINDS):
            intra[kind] = reader.position
            reader.skip(sizes[4 + index])
        reader.align(4)
        intra["user"] = reader.position
        reader.skip(sizes[7])
        reader.align(16)
        next_frame_set_offset = reader.position
        if (
            expected_next_offset is not None
            and next_frame_set_offset != expected_next_offset
        ):
            reader.fail(
                "EA02 frame-set payload does not end at the next frame set",
                expected=expected_next_offset,
                observed=next_frame_set_offset,
            )
        final_sizes = cast(SizeTable, tuple(reader.u16() for _ in range(8)))
        final: dict[ChannelKind, int] = {}
        for index, kind in enumerate(CHANNEL_KINDS):
            final[kind] = reader.position
            reader.skip(final_sizes[index])
        return cls(
            sizes,
            initial,
            intra_bits,
            intra,
            next_frame_set_offset,
            final_sizes,
            final,
        )


def parse_skeleton_animation(
    decoder: CoreDecoder,
    obj: CoreObject,
    reader: BinaryReader,
) -> SkeletonAnimation:
    prefix = read_resource_prefix(decoder, reader)
    wrapper_version = reader.u32()
    wrapper_flags = reader.u8()
    wrapper_index = reader.adaptive_reference_index(len(decoder.core.objects))
    duration_hint = reader.f32()
    skeleton_reference = decoder.core.read_reference(reader)
    byte_count = reader.count(
        "EA02 byte count",
        maximum=512 * 1024 * 1024,
    )
    blob = reader.read(byte_count)
    if not blob.startswith(b"EA02"):
        reader.fail(
            "animation byte array does not begin with EA02",
            expected=b"EA02",
            observed=blob[:4],
        )
    clip = parse_ea02(blob, source=reader.source)
    if not math.isclose(
        duration_hint,
        clip.duration,
        rel_tol=1e-6,
        abs_tol=1e-6,
    ):
        reader.fail(
            "animation wrapper duration does not match EA02 duration",
            expected=clip.duration,
            observed=duration_hint,
        )
    return SkeletonAnimation(
        prefix,
        wrapper_version,
        wrapper_flags,
        wrapper_index,
        duration_hint,
        skeleton_reference,
        reader.read(reader.remaining),
        clip,
    )


def read_skeleton_animation_bone_map_reference(
    decoder: CoreDecoder,
    obj: CoreObject,
) -> CoreReference:
    reader = decoder.core.reader_for(obj)
    read_resource_prefix(decoder, reader)
    reader.u32()
    reader.u8()
    reader.adaptive_reference_index(len(decoder.core.objects))
    reader.f32()
    return decoder.core.read_reference(reader)


def parse_ea02(blob: bytes, *, source: str = "<EA02>") -> EdgeAnimation:
    reader = BinaryReader.from_bytes(blob, source=source)
    if reader.read(4) != b"EA02":
        reader.fail("invalid Edge animation tag", offset=0, expected=b"EA02")
    duration = reader.f32()
    frequency = reader.f32()
    header_size = reader.u16()
    joint_count = reader.u16()
    frame_count = reader.u16()
    frame_set_count = reader.u16()
    reader.u16()  # Evaluation-buffer size.
    counts = tuple(reader.u16() for _ in range(8))
    flags = reader.u16()
    reader.seek(0x34)

    dma_offset = _self_relative_offset(reader, 0x34, len(blob), required=True)
    info_offset = _self_relative_offset(reader, 0x38, len(blob), required=True)
    const_offsets = {
        "rotation": _self_relative_offset(reader, 0x3C, len(blob), required=counts[0] > 0),
        "translation": _self_relative_offset(reader, 0x40, len(blob), required=counts[1] > 0),
        "scale": _self_relative_offset(reader, 0x44, len(blob), required=counts[2] > 0),
        "user": _self_relative_offset(reader, 0x48, len(blob), required=counts[3] > 0),
    }
    locomotion_offset = _self_relative_offset(reader, 0x4C, len(blob), required=False)
    packing_offset = _self_relative_offset(
        reader,
        0x50,
        len(blob),
        required=any(counts),
    )

    if not math.isfinite(duration) or duration < 0:
        reader.fail("invalid animation duration", offset=4, observed=duration)
    if not math.isfinite(frequency) or frequency <= 0:
        reader.fail("invalid animation sample frequency", offset=8, observed=frequency)
    if frame_count == 0 or frame_set_count == 0:
        reader.fail(
            "animation has no frames or frame sets",
            observed=(frame_count, frame_set_count),
        )
    expected_frequency = (frame_count - 1) / duration if duration else 0.0
    if duration and not math.isclose(
        frequency,
        expected_frequency,
        rel_tol=1e-4,
        abs_tol=1e-4,
    ):
        reader.fail(
            "animation duration/frequency/frame relation is inconsistent",
            expected=expected_frequency,
            observed=frequency,
        )
    reader.seek(0x60)
    constant_tables: dict[ChannelKind, tuple[int, ...]] = {}
    animated_tables: dict[ChannelKind, tuple[int, ...]] = {}
    for index, kind in enumerate(CHANNEL_KINDS):
        constant_tables[kind] = _read_channel_table(
            reader,
            counts[index],
            8 if index == 0 else 4,
            joint_count if kind != "user" else None,
            joint_count if kind != "user" else None,
            f"constant {kind}",
        )
    for index, kind in enumerate(CHANNEL_KINDS):
        animated_tables[kind] = _read_channel_table(
            reader,
            counts[4 + index],
            4,
            joint_count if kind != "user" else None,
            joint_count if kind != "user" else None,
            f"animated {kind}",
        )
    for kind in CHANNEL_KINDS:
        duplicate_channels = set(constant_tables[kind]) & set(animated_tables[kind])
        if duplicate_channels:
            reader.fail(
                f"{kind} channels occur in both constant and animated tables",
                observed=sorted(duplicate_channels),
            )

    constant_specs, animated_specs, packing_end = _read_packing_specs(
        blob,
        source,
        packing_offset,
        counts,  # type: ignore[arg-type]
    )
    first_table_offset = min(dma_offset, info_offset, header_size)
    if packing_end > first_table_offset:
        reader.fail(
            "EA02 packing specifications overlap frame-set tables",
            offset=packing_offset,
            expected=f"at most 0x{first_table_offset:x}",
            observed=packing_end,
        )

    frame_sets = _read_frame_sets(
        blob,
        source,
        info_offset,
        dma_offset,
        frame_set_count,
    )
    _validate_frame_sets(frame_sets, frame_count, len(blob), source)
    if header_size != frame_sets[0].data_offset:
        reader.fail(
            "EA02 header size does not match the first frame-set offset",
            offset=0x0C,
            expected=frame_sets[0].data_offset,
            observed=header_size,
        )
    _validate_frame_set_payloads(
        blob,
        source,
        frame_sets,
        animated_tables,
        animated_specs,
    )

    constant_values = {
        kind: (
            ()
            if kind == "user"
            else _read_constant_group(
                blob,
                source,
                kind,
                const_offsets[kind],
                counts[index],
                constant_specs[kind],
            )
        )
        for index, kind in enumerate(CHANNEL_KINDS)
    }
    locomotion_delta = _read_locomotion_delta(
        blob,
        source,
        locomotion_offset,
    )

    decode_supported = flags == 0
    unsupported_reason = ""
    if flags:
        unsupported_reason = (
            f"{source}: unsupported KZ2 EA02 flags 0x{flags:x} "
            f"(counts {counts})"
        )

    return EdgeAnimation(
        source=source,
        blob=blob,
        duration=duration,
        sample_frequency=frequency,
        header_size=header_size,
        joint_count=joint_count,
        frame_count=frame_count,
        flags=flags,
        channel_tables=animated_tables,
        constant_tables=constant_tables,
        frame_sets=frame_sets,
        constant_values=constant_values,
        constant_specs=constant_specs,
        animated_specs=animated_specs,
        counts=counts,  # type: ignore[arg-type]
        packing_offset=packing_offset,
        locomotion_delta=locomotion_delta,
        decode_supported=decode_supported,
        unsupported_reason=unsupported_reason,
    )


def _read_channel_table(
    reader: BinaryReader,
    count: int,
    lane_count: int,
    index_limit: int | None,
    padding_value: int | None,
    label: str,
) -> tuple[int, ...]:
    result = tuple(reader.u16() for _ in range(count))
    if len(set(result)) != len(result):
        reader.fail(
            f"{label} channel table contains duplicate indices",
            observed=result,
        )
    if index_limit is not None:
        for value in result:
            if value < index_limit:
                continue
            reader.fail(
                f"{label} channel joint index is out of range",
                expected=f"less than {index_limit}",
                observed=value,
            )
    padded = (count + lane_count - 1) // lane_count * lane_count
    padding = tuple(reader.u16() for _ in range(padded - count))
    if padding_value is not None and any(value != padding_value for value in padding):
        reader.fail(
            f"{label} channel padding is invalid",
            expected=padding_value,
            observed=padding,
        )
    if (
        padding_value is None
        and padding
        and (len(set(padding)) != 1 or (result and padding[0] <= max(result)))
    ):
        reader.fail(
            f"{label} channel padding sentinel is invalid",
            expected="one repeated value greater than every channel index",
            observed=padding,
        )
    return result


def _self_relative_offset(
    reader: BinaryReader,
    field_offset: int,
    blob_size: int,
    *,
    required: bool,
) -> int:
    reader.seek(field_offset)
    stored = reader.u32()
    if stored == 0:
        if required:
            reader.fail("required EA02 offset is null", offset=field_offset)
        return 0
    target = field_offset + stored
    if target > blob_size or (required and target == blob_size):
        reader.fail(
            "EA02 self-relative offset is out of bounds",
            offset=field_offset,
            expected=(
                f"less than {blob_size}"
                if required
                else f"at most {blob_size}"
            ),
            observed=target,
        )
    return target


def _read_frame_sets(
    blob: bytes,
    source: str,
    info_offset: int,
    dma_offset: int,
    count: int,
) -> tuple[FrameSet, ...]:
    info_reader = BinaryReader.from_bytes(blob, source=source)
    dma_reader = BinaryReader.from_bytes(blob, source=source)
    info_reader.seek(info_offset)
    dma_reader.seek(dma_offset)
    infos = tuple((info_reader.u16(), info_reader.u16()) for _ in range(count))
    dmas = tuple((dma_reader.u32(), dma_reader.u32()) for _ in range(count))
    return tuple(
        FrameSet(base, intra, dma_word, data_offset)
        for (base, intra), (dma_word, data_offset) in zip(infos, dmas, strict=True)
    )


def _validate_frame_sets(
    frame_sets: tuple[FrameSet, ...],
    frame_count: int,
    blob_size: int,
    source: str,
) -> None:
    previous = -1
    previous_data_offset = -1
    for index, frame_set in enumerate(frame_sets):
        if frame_set.base_frame <= previous:
            raise FormatError(
                "EA02 frame sets are not strictly ordered",
                source=source,
                observed=frame_set.base_frame,
            )
        previous = frame_set.base_frame
        if frame_set.base_frame >= frame_count:
            raise FormatError(
                "EA02 frame-set base frame is out of range",
                source=source,
                expected=f"less than {frame_count}",
                observed=frame_set.base_frame,
            )
        if frame_set.data_offset <= previous_data_offset:
            raise FormatError(
                "EA02 frame-set data offsets are not strictly ordered",
                source=source,
                observed=frame_set.data_offset,
            )
        previous_data_offset = frame_set.data_offset
        if frame_set.data_offset >= blob_size:
            raise FormatError(
                "EA02 frame-set data offset is out of range",
                source=source,
                expected=f"less than {blob_size}",
                observed=frame_set.data_offset,
            )
    if frame_sets[-1].base_frame != frame_count - 1:
        raise FormatError(
            "EA02 sentinel frame set does not target the final frame",
            source=source,
            expected=frame_count - 1,
            observed=frame_sets[-1].base_frame,
        )


def _read_packing_specs(
    blob: bytes,
    source: str,
    offset: int,
    counts: tuple[int, ...],
) -> tuple[
    dict[ChannelKind, PackingSpec],
    dict[ChannelKind, tuple[PackingSpec, ...]],
    int,
]:
    default = PackingSpec(
        (
            PackingComponent(0, 0, 0),
            PackingComponent(0, 0, 0),
            PackingComponent(0, 0, 0),
        ),
        3,
    )
    constant: dict[ChannelKind, PackingSpec] = {
        kind: default for kind in CHANNEL_KINDS
    }
    animated: dict[ChannelKind, tuple[PackingSpec, ...]] = {
        kind: () for kind in CHANNEL_KINDS
    }
    if not offset:
        if any(counts):
            raise FormatError(
                "EA02 channels have no packing specifications",
                source=source,
            )
        return constant, animated, 0

    reader = BinaryReader.from_bytes(blob, source=source)
    reader.seek(offset)
    for kind in CHANNEL_KINDS:
        constant[kind] = PackingSpec.from_u32(reader.u32())
    for index, kind in enumerate(CHANNEL_KINDS):
        animated[kind] = tuple(
            PackingSpec.from_u32(reader.u32())
            for _ in range(counts[4 + index])
        )

    for index, kind in enumerate(CHANNEL_KINDS):
        if kind != "user" and counts[index]:
            _validate_packing_spec(
                constant[kind],
                source,
                f"constant {kind}",
            )
        for channel_index, spec in enumerate(animated[kind]):
            _validate_packing_spec(
                spec,
                source,
                f"animated {kind} channel {channel_index}",
            )
    return constant, animated, reader.position


def _validate_packing_spec(
    spec: PackingSpec,
    source: str,
    label: str,
) -> None:
    for component in spec.components:
        if component.exponent_bits and component.mantissa_bits > 23:
            raise FormatError(
                f"{label} floating-point mantissa is too wide",
                source=source,
                expected="at most 23 bits",
                observed=component.mantissa_bits,
            )


def _validate_frame_set_payloads(
    blob: bytes,
    source: str,
    frame_sets: tuple[FrameSet, ...],
    animated_tables: dict[ChannelKind, tuple[int, ...]],
    animated_specs: dict[ChannelKind, tuple[PackingSpec, ...]],
) -> None:
    channel_count = sum(len(animated_tables[kind]) for kind in CHANNEL_KINDS)
    if channel_count == 0:
        return
    if len(frame_sets) < 2:
        raise FormatError(
            "animated EA02 channels require a sentinel frame set",
            source=source,
        )

    initial_payload_sizes = tuple(
        _bits_to_bytes(sum(spec.key_bits for spec in animated_specs[kind]))
        for kind in CHANNEL_KINDS
    )
    for frame_set, next_frame_set in itertools.pairwise(frame_sets):
        layout = _FrameSetLayout.parse(
            blob,
            frame_set,
            channel_count,
            source,
            expected_next_offset=next_frame_set.data_offset,
        )
        expected_initial = _with_scale_padding(
            initial_payload_sizes,
            frame_set.data_offset + 16,
        )
        observed_initial = layout.sizes[:4]
        if observed_initial != expected_initial:
            raise FormatError(
                "EA02 initial-frame section sizes do not match packing specs",
                source=source,
                offset=frame_set.data_offset,
                expected=expected_initial,
                observed=observed_initial,
            )
        expected_final = _with_scale_padding(
            initial_payload_sizes,
            next_frame_set.data_offset + 16,
        )
        if layout.final_sizes[:4] != expected_final:
            raise FormatError(
                "EA02 final-frame section sizes do not match packing specs",
                source=source,
                offset=layout.next_frame_set_offset,
                expected=expected_final,
                observed=layout.final_sizes[:4],
            )

        bit_base = 0
        intra_sizes: list[int] = []
        for kind in CHANNEL_KINDS:
            packed_bits = 0
            for channel_index, spec in enumerate(animated_specs[kind]):
                enabled = _count_bits(
                    blob,
                    layout.intra_bits,
                    bit_base + channel_index * frame_set.intra_frame_count,
                    frame_set.intra_frame_count,
                )
                packed_bits += enabled * spec.key_bits
            intra_sizes.append(_bits_to_bytes(packed_bits))
            bit_base += (
                len(animated_tables[kind]) * frame_set.intra_frame_count
            )
        observed_intra = layout.sizes[4:]
        expected_intra = _with_scale_padding(
            tuple(intra_sizes),
            layout.intra["rotation"],
        )
        padded_user_size = expected_intra[3] + (
            -(layout.intra["user"] + expected_intra[3])
        ) % 16
        expected_padded_intra = (
            expected_intra[0],
            expected_intra[1],
            expected_intra[2],
            padded_user_size,
        )
        if observed_intra != expected_padded_intra:
            raise FormatError(
                "EA02 intra-frame section sizes do not match sparse keys",
                source=source,
                offset=frame_set.data_offset,
                expected=expected_padded_intra,
                observed=observed_intra,
            )


def _bits_to_bytes(bit_count: int) -> int:
    return (bit_count + 7) // 8


def _with_scale_padding(
    sizes: tuple[int, ...],
    start_offset: int,
) -> tuple[int, int, int, int]:
    rotation, translation, scale, user = sizes
    padding = (-(start_offset + rotation + translation + scale)) % 4
    return rotation, translation, scale + padding, user


def _read_locomotion_delta(
    blob: bytes,
    source: str,
    offset: int,
) -> JointSample | None:
    if not offset:
        return None
    if offset + 32 > len(blob):
        raise FormatError(
            "truncated EA02 locomotion delta",
            source=source,
            offset=offset,
            expected=32,
            observed=max(0, len(blob) - offset),
        )
    values = struct.unpack_from(">8f", blob, offset)
    if not all(math.isfinite(value) for value in values):
        raise FormatError(
            "EA02 locomotion delta is not finite",
            source=source,
            offset=offset,
        )
    rotation = _normalize_quaternion(values[:4])  # type: ignore[arg-type]
    return JointSample(
        rotation_xyzw=rotation,
        translation=(values[4], values[5], values[6]),
    )


def _read_constant_group(
    blob: bytes,
    source: str,
    kind: ChannelKind,
    offset: int,
    count: int,
    spec: PackingSpec,
) -> tuple[tuple[float, ...], ...]:
    if count == 0:
        return ()
    if offset == 0:
        raise FormatError(
            f"constant {kind} channels have no data offset",
            source=source,
        )
    return tuple(
        _decode_packed_key(
            blob,
            offset * 8 + index * spec.key_bits,
            spec,
            rotation=kind == "rotation",
            source=source,
        )
        for index in range(count)
    )


def _decode_packed_key(
    blob: bytes,
    start_bit: int,
    spec: PackingSpec,
    *,
    rotation: bool,
    source: str,
) -> tuple[float, ...]:
    reader = BitReader(
        blob,
        start_bit=start_bit,
        bit_count=spec.key_bits,
        source=source,
    )
    decoded = []
    for component in spec.components:
        sign = reader.read(component.sign_bits) if component.sign_bits else 0
        exponent = (
            reader.read(component.exponent_bits) if component.exponent_bits else 0
        )
        mantissa = (
            reader.read(component.mantissa_bits) if component.mantissa_bits else 0
        )
        if component.exponent_bits == 0:
            if component.mantissa_bits == 0:
                decoded.append(0.0)
                continue
            signed = (
                mantissa - (1 << component.mantissa_bits)
                if sign
                else mantissa
            )
            decoded.append(signed / ((1 << component.mantissa_bits) - 1))
            continue
        exponent_field = exponent + 128 - (1 << (component.exponent_bits - 1))
        exponent_field = max(0, min(255, exponent_field))
        mantissa_field = (
            mantissa << (23 - component.mantissa_bits)
            if component.mantissa_bits <= 23
            else 0
        )
        bits = (sign << 31) | (exponent_field << 23) | mantissa_field
        decoded.append(struct.unpack(">f", bits.to_bytes(4, "big"))[0])

    if not rotation:
        return (decoded[0], decoded[1], decoded[2])
    values = [0.0, 0.0, 0.0, 0.0]
    source_index = 0
    for index in range(4):
        if index == spec.omitted_component:
            continue
        values[index] = decoded[source_index]
        source_index += 1
    remainder = 1.0 - sum(value * value for value in decoded)
    values[spec.omitted_component] = math.sqrt(max(0.0, min(1.0, remainder)))
    return _normalize_quaternion(cast(Quaternion, tuple(values)))


def _interpolate(
    kind: ChannelKind,
    left: tuple[float, ...],
    right: tuple[float, ...],
    alpha: float,
) -> tuple[float, ...]:
    if kind != "rotation":
        return tuple(
            left[index] * (1.0 - alpha) + right[index] * alpha
            for index in range(3)
        )
    return _slerp(left, right, alpha)


def _slerp(
    left: tuple[float, ...],
    right: tuple[float, ...],
    alpha: float,
) -> Quaternion:
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    if dot < 0:
        right = tuple(-value for value in right)
        dot = -dot
    dot = max(-1.0, min(1.0, dot))
    if dot > 0.9995:
        return _normalize_quaternion(
            cast(
                Quaternion,
                tuple(
                    left[index] + alpha * (right[index] - left[index])
                    for index in range(4)
                ),
            )
        )
    theta = math.acos(dot)
    sin_theta = math.sin(theta)
    left_weight = math.sin((1.0 - alpha) * theta) / sin_theta
    right_weight = math.sin(alpha * theta) / sin_theta
    return _normalize_quaternion(
        cast(
            Quaternion,
            tuple(
                left_weight * left[index] + right_weight * right[index]
                for index in range(4)
            ),
        )
    )


def _normalize_quaternion(value: Quaternion) -> Quaternion:
    length = math.sqrt(sum(component * component for component in value))
    if not math.isfinite(length) or length <= 1e-12:
        raise FormatError("decoded quaternion is not finite or has zero length")
    return cast(Quaternion, tuple(component / length for component in value))


def _get_bit(blob: bytes, byte_offset: int, bit_offset: int) -> bool:
    absolute = byte_offset * 8 + bit_offset
    if absolute < 0 or absolute >= len(blob) * 8:
        raise FormatError(
            "EA02 intra-frame bitmap is out of bounds",
            offset=max(0, absolute // 8),
        )
    return bool((blob[absolute // 8] >> (7 - absolute % 8)) & 1)


def _count_bits(
    blob: bytes,
    byte_offset: int,
    bit_offset: int,
    count: int,
) -> int:
    return sum(
        _get_bit(blob, byte_offset, bit_offset + index)
        for index in range(count)
    )


def _last_set_bit(
    blob: bytes,
    byte_offset: int,
    bit_offset: int,
    before: int,
) -> int | None:
    for index in range(before - 1, -1, -1):
        if _get_bit(blob, byte_offset, bit_offset + index):
            return index
    return None


def _first_set_bit(
    blob: bytes,
    byte_offset: int,
    bit_offset: int,
    count: int,
) -> int | None:
    for index in range(count):
        if _get_bit(blob, byte_offset, bit_offset + index):
            return index
    return None
