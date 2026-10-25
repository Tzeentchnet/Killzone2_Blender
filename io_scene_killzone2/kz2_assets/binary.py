"""Bounded big-endian binary readers used by all KZ2 formats."""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Final

from .errors import FormatError

MAX_COUNT: Final = 10_000_000
MAX_STRING_BYTES: Final = 64 * 1024 * 1024


@dataclass(slots=True, init=False)
class BinaryReader:
    data: memoryview
    source: str
    start: int
    end: int
    position: int

    def __init__(
        self,
        data: bytes | bytearray | memoryview,
        source: str = "<memory>",
        start: int = 0,
        end: int | None = None,
        position: int | None = None,
    ) -> None:
        self.data = data if isinstance(data, memoryview) else memoryview(data)
        self.source = source
        self.start = start
        self.end = len(self.data) if end is None else end
        self.position = start if position is None else position
        if not (0 <= self.start <= self.position <= self.end <= len(self.data)):
            raise ValueError("invalid reader bounds")

    @classmethod
    def from_bytes(cls, data: bytes, source: str = "<memory>") -> BinaryReader:
        return cls(memoryview(data), source=source)

    def clone(self) -> BinaryReader:
        return BinaryReader(
            self.data,
            source=self.source,
            start=self.start,
            end=self.end,
            position=self.position,
        )

    def fork(self, start: int, size: int) -> BinaryReader:
        end = start + size
        if start < self.start or size < 0 or end > self.end:
            self.fail("sub-reader is out of bounds", offset=start, observed=size)
        return BinaryReader(
            self.data,
            source=self.source,
            start=start,
            end=end,
            position=start,
        )

    @property
    def remaining(self) -> int:
        return self.end - self.position

    @property
    def relative_offset(self) -> int:
        return self.position - self.start

    def fail(
        self,
        message: str,
        *,
        offset: int | None = None,
        expected: object | None = None,
        observed: object | None = None,
    ) -> None:
        raise FormatError(
            message,
            source=self.source,
            offset=self.position if offset is None else offset,
            expected=expected,
            observed=observed,
        )

    def require(self, size: int) -> None:
        if size < 0 or self.position + size > self.end:
            self.fail(
                "truncated binary data",
                expected=size,
                observed=max(0, self.end - self.position),
            )

    def seek(self, position: int) -> None:
        if position < self.start or position > self.end:
            self.fail("seek is out of bounds", offset=position)
        self.position = position

    def skip(self, size: int) -> None:
        self.require(size)
        self.position += size

    def align(self, alignment: int, *, origin: int = 0) -> int:
        if alignment <= 0 or alignment & (alignment - 1):
            raise ValueError("alignment must be a positive power of two")
        relative = self.position - origin
        aligned = origin + ((relative + alignment - 1) & -alignment)
        padding = aligned - self.position
        self.skip(padding)
        return padding

    def read(self, size: int) -> bytes:
        self.require(size)
        start = self.position
        self.position += size
        return self.data[start : start + size].tobytes()

    def read_view(self, size: int) -> memoryview:
        self.require(size)
        start = self.position
        self.position += size
        return self.data[start : start + size]

    def _unpack(self, fmt: str, size: int) -> int | float:
        self.require(size)
        value = struct.unpack_from(fmt, self.data, self.position)[0]
        self.position += size
        return value

    def u8(self) -> int:
        return int(self._unpack(">B", 1))

    def i8(self) -> int:
        return int(self._unpack(">b", 1))

    def u16(self) -> int:
        return int(self._unpack(">H", 2))

    def i16(self) -> int:
        return int(self._unpack(">h", 2))

    def u32(self) -> int:
        return int(self._unpack(">I", 4))

    def i32(self) -> int:
        return int(self._unpack(">i", 4))

    def u40(self) -> int:
        return int.from_bytes(self.read(5), "big", signed=False)

    def f16(self) -> float:
        return float(self._unpack(">e", 2))

    def f32(self) -> float:
        return float(self._unpack(">f", 4))

    def compact_scalar(self) -> int:
        first = self.u8()
        if first == 0x80:
            return self.i32()
        if first == 0x81:
            return self.i16()
        return first - 0x100 if first & 0x80 else first

    def count(self, label: str = "count", *, maximum: int = MAX_COUNT) -> int:
        offset = self.position
        value = self.compact_scalar()
        if value < 0 or value > maximum:
            self.fail(
                f"invalid {label}",
                offset=offset,
                expected=f"0..{maximum}",
                observed=value,
            )
        return value

    def adaptive_index(self, cardinality: int, label: str = "index") -> int:
        if cardinality < 0:
            raise ValueError("cardinality cannot be negative")
        offset = self.position
        if cardinality <= 0x100:
            value = self.u8()
        elif cardinality <= 0x10000:
            value = self.u16()
        else:
            value = self.u32()
        if value >= cardinality:
            self.fail(
                f"{label} is out of range",
                offset=offset,
                expected=f"less than {cardinality}",
                observed=value,
            )
        return value

    def adaptive_reference_index(self, object_count: int) -> int:
        cardinality = object_count + 1
        if cardinality <= 0x100:
            return self.u8()
        if cardinality <= 0x10000:
            return self.u16()
        return self.u32()

    def typed_string_length(self) -> int:
        offset = self.position
        first = self.u8()
        if first < 0xFE:
            value = first
        elif first == 0xFE:
            value = self.u16()
        else:
            value = self.u32()
        if value > MAX_STRING_BYTES:
            self.fail(
                "typed string is too large",
                offset=offset,
                expected=f"at most {MAX_STRING_BYTES}",
                observed=value,
            )
        return value

    def compact_bytes(self, label: str = "byte array") -> bytes:
        size = self.count(f"{label} length", maximum=MAX_STRING_BYTES)
        return self.read(size)

    def compact_utf8(self, label: str = "string") -> str:
        offset = self.position
        raw = self.compact_bytes(label)
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            self.fail(f"invalid UTF-8 {label}", offset=offset, observed=str(exc))
        raise AssertionError("unreachable")


class BitReader:
    """MSB-first reader over a bounded bit range."""

    __slots__ = ("_data", "_end_bit", "_position", "_source", "_start_bit")

    def __init__(
        self,
        data: memoryview | bytes,
        *,
        start_bit: int,
        bit_count: int,
        source: str,
    ) -> None:
        self._data = data if isinstance(data, memoryview) else memoryview(data)
        self._start_bit = start_bit
        self._end_bit = start_bit + bit_count
        self._position = start_bit
        self._source = source
        if start_bit < 0 or bit_count < 0 or self._end_bit > len(self._data) * 8:
            raise FormatError(
                "bit range is out of bounds",
                source=source,
                offset=max(0, start_bit // 8),
            )

    @property
    def consumed(self) -> int:
        return self._position - self._start_bit

    def read(self, count: int) -> int:
        if count < 0 or self._position + count > self._end_bit:
            raise FormatError(
                "truncated packed bit stream",
                source=self._source,
                offset=self._position // 8,
                expected=count,
                observed=max(0, self._end_bit - self._position),
            )
        value = 0
        for _ in range(count):
            byte_index, bit_index = divmod(self._position, 8)
            value = (value << 1) | ((self._data[byte_index] >> (7 - bit_index)) & 1)
            self._position += 1
        return value
