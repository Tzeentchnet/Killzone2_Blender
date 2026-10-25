"""Parser for Killzone 2 RTTIBin<1.58> CORE object graphs."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from .binary import MAX_COUNT, MAX_STRING_BYTES, BinaryReader
from .errors import FormatError

CORE_SIGNATURE: Final = b"RTTIBin<1.58>  "
MAX_OBJECT_PAYLOAD: Final = 512 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class AllocationRecord:
    fields: tuple[int, int, int, int, int]


@dataclass(frozen=True, slots=True)
class CoreReference:
    local_index: int | None = None
    external_id: str | None = None

    @property
    def is_null(self) -> bool:
        return self.local_index is None and self.external_id is None


@dataclass(slots=True)
class CoreObject:
    index: int
    type_name: str
    opaque_id: bytes
    payload_size: int
    metadata_tail: bytes
    payload_offset: int = 0
    direct_size: int = 0
    direct_value: object | None = None
    allocation_lists: tuple[
        tuple[AllocationRecord, ...], tuple[AllocationRecord, ...]
    ] = ((), ())

    @property
    def display_id(self) -> str:
        return b"".join(
            self.opaque_id[index : index + 4][::-1] for index in range(0, 16, 4)
        ).hex()

    @property
    def data_size(self) -> int:
        return self.payload_size or self.direct_size


@dataclass(slots=True)
class CoreFile:
    source: str
    data: memoryview
    marker: int
    header_fields: tuple[int, int, int, int, int]
    type_names: tuple[str, ...]
    objects: tuple[CoreObject, ...]
    string_banks: dict[str, tuple[str, ...]]
    allocation_lists: tuple[tuple[AllocationRecord, ...], ...]
    unnamed_scalar: int
    trailing_bytes: int = 0
    _opaque_id_index: dict[bytes, int] = field(default_factory=dict, repr=False)

    @classmethod
    def from_path(
        cls,
        path: str | Path,
        *,
        parse_direct_objects: bool = False,
    ) -> CoreFile:
        source = str(path)
        return cls.from_bytes(
            Path(path).read_bytes(),
            source=source,
            parse_direct_objects=parse_direct_objects,
        )

    @classmethod
    def from_bytes(
        cls,
        data: bytes,
        *,
        source: str = "<memory>",
        parse_direct_objects: bool = False,
    ) -> CoreFile:
        view = memoryview(data)
        reader = BinaryReader(view, source=source)
        signature = reader.read(len(CORE_SIGNATURE))
        if signature != CORE_SIGNATURE:
            reader.fail(
                "unsupported CORE signature",
                offset=0,
                expected=CORE_SIGNATURE,
                observed=signature,
            )

        marker = reader.u8()
        if marker != 1:
            reader.fail(
                "unsupported CORE endian marker",
                offset=0x0F,
                expected=1,
                observed=marker,
            )

        header_fields = (
            reader.u32(),
            reader.u32(),
            reader.u32(),
            reader.u16(),
            reader.u16(),
        )

        type_count = reader.count("type-name count")
        type_names = tuple(
            _read_compact_utf8(reader, "type name") for _ in range(type_count)
        )

        object_count = reader.count("object count")
        object_types = [
            reader.adaptive_index(type_count, "object type index")
            for _ in range(object_count)
        ]

        objects: list[CoreObject] = []
        for index, type_index in enumerate(object_types):
            opaque_id = reader.read(16)
            payload_size_offset = reader.position
            payload_size = reader.u32()
            if payload_size > MAX_OBJECT_PAYLOAD:
                reader.fail(
                    "object payload is too large",
                    offset=payload_size_offset,
                    expected=f"at most {MAX_OBJECT_PAYLOAD}",
                    observed=payload_size,
                )
            objects.append(
                CoreObject(
                    index=index,
                    type_name=type_names[type_index],
                    opaque_id=opaque_id,
                    payload_size=payload_size,
                    metadata_tail=reader.read(12),
                )
            )

        global_allocations = _read_allocation_list(reader)

        bank_count = reader.count("string-bank count")
        string_banks: dict[str, tuple[str, ...]] = {}
        for _ in range(bank_count):
            type_index = reader.adaptive_index(type_count, "string-bank type index")
            bank_type = type_names[type_index]
            if bank_type in string_banks:
                reader.fail("duplicate string bank", observed=bank_type)
            string_count = reader.count("string count")
            string_banks[bank_type] = tuple(
                _read_typed_string(reader, bank_type) for _ in range(string_count)
            )

        unnamed_scalar = reader.compact_scalar()
        second_global_allocations = _read_allocation_list(reader)

        parsed = cls(
            source=source,
            data=view,
            marker=marker,
            header_fields=header_fields,
            type_names=type_names,
            objects=tuple(objects),
            string_banks=string_banks,
            allocation_lists=(global_allocations, second_global_allocations),
            unnamed_scalar=unnamed_scalar,
        )
        parsed._opaque_id_index = {
            obj.opaque_id: obj.index for obj in objects if obj.opaque_id != bytes(16)
        }

        from .level import LEVEL_DIRECT_TYPES, parse_direct_object

        zero_sized_types = {
            obj.type_name for obj in objects if obj.payload_size == 0
        }
        has_level_direct_objects = bool(zero_sized_types) and (
            zero_sized_types <= LEVEL_DIRECT_TYPES
        )
        decode_direct_objects = parse_direct_objects or has_level_direct_objects

        for obj in objects:
            first = _read_allocation_list(reader)
            second = _read_allocation_list(reader)
            obj.allocation_lists = (first, second)
            obj.payload_offset = reader.position
            if obj.payload_size:
                reader.skip(obj.payload_size)
            elif decode_direct_objects:
                obj.direct_value = parse_direct_object(parsed, obj, reader)
                obj.direct_size = reader.position - obj.payload_offset

        parsed.trailing_bytes = reader.remaining
        return parsed

    def reader_for(self, obj: CoreObject | int) -> BinaryReader:
        record = self.objects[obj] if isinstance(obj, int) else obj
        return BinaryReader(
            self.data,
            source=(
                f"{self.source}: object {record.index} "
                f"{record.type_name} {record.display_id}"
            ),
            start=record.payload_offset,
            end=record.payload_offset + record.data_size,
            position=record.payload_offset,
        )

    def strings(self, bank_type: str = "String") -> tuple[str, ...]:
        try:
            return self.string_banks[bank_type]
        except KeyError as exc:
            raise FormatError(
                f"missing {bank_type!r} string bank",
                source=self.source,
            ) from exc

    def read_string_index(
        self, reader: BinaryReader, bank_type: str = "String"
    ) -> str:
        strings = self.strings(bank_type)
        if not strings:
            reader.fail(f"cannot index empty {bank_type!r} string bank")
        index = reader.adaptive_index(len(strings), f"{bank_type} string index")
        return strings[index]

    def read_reference(self, reader: BinaryReader) -> CoreReference:
        discriminator_offset = reader.position
        discriminator = reader.u8()
        if discriminator == 0:
            encoded = reader.adaptive_reference_index(len(self.objects))
            if encoded == 0:
                return CoreReference()
            index = encoded - 1
            if index >= len(self.objects):
                reader.fail(
                    "local object reference is out of range",
                    offset=discriminator_offset,
                    expected=f"0..{len(self.objects)}",
                    observed=encoded,
                )
            return CoreReference(local_index=index)
        if discriminator == 2:
            return CoreReference(external_id=_read_compact_utf8(reader, "external ID"))
        reader.fail(
            "unknown object-reference discriminator",
            offset=discriminator_offset,
            expected="0 or 2",
            observed=discriminator,
        )
        raise AssertionError("unreachable")

    def object_by_opaque_id(self, opaque_id: bytes) -> CoreObject | None:
        index = self._opaque_id_index.get(opaque_id)
        return None if index is None else self.objects[index]


def _read_compact_utf8(reader: BinaryReader, label: str) -> str:
    offset = reader.position
    length = reader.count(f"{label} length", maximum=MAX_STRING_BYTES)
    raw = reader.read(length)
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        reader.fail(f"invalid UTF-8 {label}", offset=offset, observed=str(exc))
    raise AssertionError("unreachable")


def _read_typed_string(reader: BinaryReader, bank_type: str) -> str:
    allocation = reader.compact_scalar()
    if allocation < -1:
        reader.fail(
            "invalid typed-string allocation value",
            observed=allocation,
        )
    length = reader.typed_string_length()
    byte_length = length * 2 if bank_type == "WString" else length
    raw = reader.read(byte_length)
    encoding = "utf-16-be" if bank_type == "WString" else "utf-8"
    try:
        return raw.decode(encoding)
    except UnicodeDecodeError as exc:
        reader.fail(
            f"invalid {bank_type} data",
            offset=reader.position - byte_length,
            observed=str(exc),
        )
    raise AssertionError("unreachable")


def _read_allocation_list(
    reader: BinaryReader,
) -> tuple[AllocationRecord, ...]:
    count = reader.count("allocation-record count", maximum=MAX_COUNT)
    return tuple(
        AllocationRecord(
            (
                reader.compact_scalar(),
                reader.compact_scalar(),
                reader.compact_scalar(),
                reader.compact_scalar(),
                reader.compact_scalar(),
            )
        )
        for _ in range(count)
    )
