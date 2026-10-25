"""Texture resource parsing and retail image-format decoding."""

from __future__ import annotations

import struct
from array import array
from dataclasses import dataclass

from .binary import BinaryReader
from .errors import UnsupportedFeatureError
from .geometry import read_resource_prefix
from .model import (
    RsxTextureDescriptor,
    Texture,
    TextureFormat,
    TextureType,
)

MAX_TEXTURE_DIMENSION = 16_384
TEXTURE_ALIGNMENT = 128
STATE_FILL = bytes([0x50]) * 80


@dataclass(frozen=True, slots=True)
class TextureLayout:
    mip_sizes: tuple[int, ...]
    logical_face_size: int
    face_stride: int
    face_count: int

    @property
    def payload_size(self) -> int:
        return self.face_stride * self.face_count

    @property
    def logical_storage_end(self) -> int:
        return (
            (self.face_count - 1) * self.face_stride + self.logical_face_size
        )


@dataclass(slots=True)
class DecodedTextureSurface:
    width: int
    height: int
    pixels: array


def parse_texture(decoder, obj, reader: BinaryReader) -> Texture:
    prefix = read_resource_prefix(decoder, reader)
    texture_type = reader.i32()
    width = reader.i32()
    height = reader.i32()
    pixel_format = reader.i32()
    memory_type = reader.i32()
    mip_count = reader.i32()
    color_space = reader.i32()
    texcoord_type = reader.i32()
    creation_flags = reader.u32()
    allocation_size = reader.u32()
    allocation_constant = reader.u32()
    payload_size = reader.u32()
    state_words = (reader.u32(), reader.u32(), reader.u32(), reader.u32())
    descriptor = RsxTextureDescriptor(
        format=reader.u8(),
        mipmap=reader.u8(),
        dimension=reader.u8(),
        cubemap=reader.u8(),
        remap=reader.u32(),
        width=reader.u16(),
        height=reader.u16(),
        depth=reader.u16(),
        location=reader.u8(),
        padding=reader.u8(),
        pitch=reader.u32(),
        offset=reader.u32(),
    )
    state_fill = reader.read(80)
    payload = reader.read(payload_size)

    if reader.remaining:
        reader.fail(
            "texture payload has trailing bytes",
            expected=0,
            observed=reader.remaining,
        )
    if texture_type not in (TextureType.TEXTURE_2D, TextureType.CUBEMAP):
        reader.fail(
            "unsupported texture type",
            expected="2D (0) or cubemap (2)",
            observed=texture_type,
        )
    if not 1 <= width <= MAX_TEXTURE_DIMENSION:
        reader.fail(
            "invalid texture width",
            expected=f"1..{MAX_TEXTURE_DIMENSION}",
            observed=width,
        )
    if not 1 <= height <= MAX_TEXTURE_DIMENSION:
        reader.fail(
            "invalid texture height",
            expected=f"1..{MAX_TEXTURE_DIMENSION}",
            observed=height,
        )
    max_mip_count = max(width, height).bit_length()
    if not 1 <= mip_count <= max_mip_count:
        reader.fail(
            "invalid texture mip count",
            expected=f"1..{max_mip_count}",
            observed=mip_count,
        )
    if allocation_size != payload_size + TEXTURE_ALIGNMENT:
        reader.fail(
            "texture allocation size mismatch",
            expected=payload_size + TEXTURE_ALIGNMENT,
            observed=allocation_size,
        )
    if allocation_constant != 0x01050000:
        reader.fail(
            "unexpected texture allocation constant",
            expected=0x01050000,
            observed=allocation_constant,
        )
    if state_words[:3] != (1, 0, TEXTURE_ALIGNMENT):
        reader.fail(
            "unexpected texture state header",
            expected=(1, 0, TEXTURE_ALIGNMENT),
            observed=state_words[:3],
        )
    if state_fill != STATE_FILL:
        reader.fail("unexpected texture state fill bytes")
    expected_cubemap = int(texture_type == TextureType.CUBEMAP)
    descriptor_values = (
        descriptor.mipmap,
        descriptor.dimension,
        descriptor.cubemap,
        descriptor.width,
        descriptor.height,
        descriptor.depth,
        descriptor.location,
        descriptor.padding,
        descriptor.offset,
    )
    expected_descriptor_values = (
        mip_count,
        2,
        expected_cubemap,
        width,
        height,
        1,
        0,
        0,
        0,
    )
    if descriptor_values != expected_descriptor_values:
        reader.fail(
            "texture descriptor does not match its serialized header",
            expected=expected_descriptor_values,
            observed=descriptor_values,
        )

    texture = Texture(
        prefix=prefix,
        texture_type=texture_type,
        width=width,
        height=height,
        pixel_format=pixel_format,
        memory_type=memory_type,
        mip_count=mip_count,
        color_space=color_space,
        texcoord_type=texcoord_type,
        creation_flags=creation_flags,
        allocation_size=allocation_size,
        allocation_constant=allocation_constant,
        payload_size=payload_size,
        state_words=state_words,
        descriptor=descriptor,
        state_fill=state_fill,
        payload=payload,
    )
    _validate_texture_profile(reader, texture)
    if is_supported_texture_format(pixel_format):
        layout = texture_layout(texture)
        if payload_size != layout.payload_size:
            reader.fail(
                "texture payload size mismatch",
                expected=layout.payload_size,
                observed=payload_size,
            )
        if state_words[3] != layout.logical_storage_end:
            reader.fail(
                "texture logical storage end mismatch",
                expected=layout.logical_storage_end,
                observed=state_words[3],
            )
    return texture


def _validate_texture_profile(reader: BinaryReader, texture: Texture) -> None:
    if texture.memory_type != 0:
        reader.fail(
            "unsupported texture memory type",
            expected=0,
            observed=texture.memory_type,
        )
    if texture.color_space not in (0, 1):
        reader.fail(
            "unsupported texture color space",
            expected="Linear (0) or sRGB (1)",
            observed=texture.color_space,
        )
    if texture.texcoord_type != 0:
        reader.fail(
            "unsupported texture coordinate type",
            expected=0,
            observed=texture.texcoord_type,
        )
    if texture.creation_flags not in (1, 3):
        reader.fail(
            "unsupported texture creation flags",
            expected="static (1) or dynamic (3)",
            observed=texture.creation_flags,
        )
    if not is_supported_texture_format(texture.pixel_format):
        return

    format_value = TextureFormat(texture.pixel_format)
    if (
        texture.creation_flags == 3
        and format_value is not TextureFormat.RGBA_8888
    ):
        reader.fail(
            "dynamic layout is unsupported for this texture format",
            expected=TextureFormat.RGBA_8888,
            observed=format_value,
        )
    expected: tuple[int, int, int]
    if format_value is TextureFormat.RGBA_8888:
        if texture.creation_flags == 3:
            if texture.mip_count != 1:
                reader.fail(
                    "dynamic texture must have one mip",
                    expected=1,
                    observed=texture.mip_count,
                )
            expected = (0xA5, 0x0000AA93, texture.width * 4)
        else:
            expected = (0x85, 0x0000AA6C, 0)
    elif format_value is TextureFormat.RGBA_8888_REV:
        expected = (0x85, 0x0000AAE4, 0)
    elif format_value is TextureFormat.S3TC1:
        expected = (0x86, 0x0000AAE4, 0)
    elif format_value is TextureFormat.S3TC3:
        expected = (0x87, 0x0000AAE4, 0)
    elif format_value is TextureFormat.S3TC5:
        expected = (0x88, 0x0000AAE4, 0)
    elif format_value is TextureFormat.RGBA_FLOAT_16:
        expected = (0x9A, 0x0000AAE4, 0)
    else:
        raise AssertionError("unreachable")

    observed = (
        texture.descriptor.format,
        texture.descriptor.remap,
        texture.descriptor.pitch,
    )
    if observed != expected:
        reader.fail(
            "texture RSX descriptor does not match its pixel format",
            expected=expected,
            observed=observed,
        )


def is_supported_texture_format(pixel_format: int) -> bool:
    try:
        TextureFormat(pixel_format)
    except ValueError:
        return False
    return True


def texture_layout(texture: Texture) -> TextureLayout:
    mip_sizes = tuple(
        mip_byte_size(
            texture.pixel_format,
            max(1, texture.width >> mip),
            max(1, texture.height >> mip),
        )
        for mip in range(texture.mip_count)
    )
    logical_face_size = sum(mip_sizes)
    face_stride = align_up(logical_face_size, TEXTURE_ALIGNMENT)
    face_count = 6 if texture.texture_type == TextureType.CUBEMAP else 1
    return TextureLayout(mip_sizes, logical_face_size, face_stride, face_count)


def mip_byte_size(pixel_format: int, width: int, height: int) -> int:
    try:
        format_value = TextureFormat(pixel_format)
    except ValueError as exc:
        raise UnsupportedFeatureError(
            f"Texture pixel format {pixel_format} is not supported"
        ) from exc
    if format_value is TextureFormat.S3TC1:
        return max(1, (width + 3) // 4) * max(1, (height + 3) // 4) * 8
    if format_value in (TextureFormat.S3TC3, TextureFormat.S3TC5):
        return max(1, (width + 3) // 4) * max(1, (height + 3) // 4) * 16
    if format_value in (TextureFormat.RGBA_8888, TextureFormat.RGBA_8888_REV):
        return width * height * 4
    if format_value is TextureFormat.RGBA_FLOAT_16:
        return width * height * 8
    raise AssertionError("unreachable")


def texture_mip_bytes(texture: Texture, face: int, mip: int) -> bytes:
    layout = texture_layout(texture)
    if not 0 <= face < layout.face_count:
        raise IndexError(f"texture face {face} is out of range")
    if not 0 <= mip < len(layout.mip_sizes):
        raise IndexError(f"texture mip {mip} is out of range")
    offset = face * layout.face_stride + sum(layout.mip_sizes[:mip])
    size = layout.mip_sizes[mip]
    return texture.payload[offset : offset + size]


def decode_texture_surface(
    texture: Texture,
    *,
    face: int = 0,
    mip: int = 0,
) -> DecodedTextureSurface:
    width = max(1, texture.width >> mip)
    height = max(1, texture.height >> mip)
    data = texture_mip_bytes(texture, face, mip)
    try:
        format_value = TextureFormat(texture.pixel_format)
    except ValueError as exc:
        raise UnsupportedFeatureError(
            f"Texture pixel format {texture.pixel_format} is not supported"
        ) from exc

    if format_value in (
        TextureFormat.S3TC1,
        TextureFormat.S3TC3,
        TextureFormat.S3TC5,
    ):
        pixels = _decode_s3tc(data, width, height, format_value)
    elif format_value in (
        TextureFormat.RGBA_8888,
        TextureFormat.RGBA_8888_REV,
    ):
        pixels = _decode_rgba8(texture, data, width, height)
    elif format_value is TextureFormat.RGBA_FLOAT_16:
        pixels = _decode_rgba16f(texture, data, width, height)
    else:
        raise AssertionError("unreachable")
    return DecodedTextureSurface(width, height, pixels)


def rectangular_morton(x: int, y: int, width: int, height: int) -> int:
    x_bits = (width - 1).bit_length()
    y_bits = (height - 1).bit_length()
    result = 0
    output_bit = 0
    for bit in range(max(x_bits, y_bits)):
        if bit < x_bits:
            result |= ((x >> bit) & 1) << output_bit
            output_bit += 1
        if bit < y_bits:
            result |= ((y >> bit) & 1) << output_bit
            output_bit += 1
    return result


def apply_argb_remap(
    stored: tuple[float, float, float, float],
    remap: int,
) -> tuple[float, float, float, float]:
    selector_byte = remap & 0xFF
    mode_byte = (remap >> 8) & 0xFF
    argb: list[float] = []
    for output_index in range(4):
        selector = (selector_byte >> (output_index * 2)) & 0x3
        mode = (mode_byte >> (output_index * 2)) & 0x3
        if mode == 0:
            argb.append(0.0)
        elif mode == 1:
            argb.append(1.0)
        elif mode == 2:
            argb.append(stored[selector])
        else:
            raise UnsupportedFeatureError(
                f"Unsupported RSX texture remap mode in 0x{remap:08x}"
            )
    return (argb[1], argb[2], argb[3], argb[0])


def align_up(value: int, alignment: int) -> int:
    if alignment <= 0 or alignment & (alignment - 1):
        raise ValueError("alignment must be a positive power of two")
    return (value + alignment - 1) & -alignment


def _decode_rgba8(
    texture: Texture,
    data: bytes,
    width: int,
    height: int,
) -> array:
    pixels = array("f", [0.0]) * (width * height * 4)
    linear = texture.descriptor.is_linear or texture.descriptor.pitch != 0
    for y in range(height):
        for x in range(width):
            source_pixel = (
                y * width + x
                if linear
                else rectangular_morton(x, y, width, height)
            )
            source = source_pixel * 4
            stored = (
                data[source] / 255.0,
                data[source + 1] / 255.0,
                data[source + 2] / 255.0,
                data[source + 3] / 255.0,
            )
            rgba = apply_argb_remap(stored, texture.descriptor.remap)
            destination = (y * width + x) * 4
            pixels[destination : destination + 4] = array("f", rgba)
    return pixels


def _decode_rgba16f(
    texture: Texture,
    data: bytes,
    width: int,
    height: int,
) -> array:
    pixels = array("f", [0.0]) * (width * height * 4)
    linear = texture.descriptor.is_linear or texture.descriptor.pitch != 0
    for y in range(height):
        for x in range(width):
            source_pixel = (
                y * width + x
                if linear
                else rectangular_morton(x, y, width, height)
            )
            stored = struct.unpack_from(">4e", data, source_pixel * 8)
            destination = (y * width + x) * 4
            pixels[destination : destination + 4] = array("f", stored)
    return pixels


def _decode_s3tc(
    data: bytes,
    width: int,
    height: int,
    pixel_format: TextureFormat,
) -> array:
    pixels = array("f", [0.0]) * (width * height * 4)
    block_size = 8 if pixel_format is TextureFormat.S3TC1 else 16
    blocks_x = max(1, (width + 3) // 4)
    blocks_y = max(1, (height + 3) // 4)
    expected = blocks_x * blocks_y * block_size
    if len(data) != expected:
        raise ValueError(f"S3TC data size {len(data)} does not match {expected}")

    for block_y in range(blocks_y):
        for block_x in range(blocks_x):
            offset = (block_y * blocks_x + block_x) * block_size
            block = data[offset : offset + block_size]
            if pixel_format is TextureFormat.S3TC1:
                colors = _decode_dxt_colors(block, force_four_color=False)
                alphas = None
            elif pixel_format is TextureFormat.S3TC3:
                colors = _decode_dxt_colors(block[8:], force_four_color=True)
                alpha_bits = int.from_bytes(block[:8], "little")
                alphas = tuple(
                    ((alpha_bits >> (index * 4)) & 0xF) / 15.0
                    for index in range(16)
                )
            else:
                colors = _decode_dxt_colors(block[8:], force_four_color=True)
                alphas = _decode_dxt5_alphas(block[:8])

            color_indices = int.from_bytes(block[-4:], "little")
            for local_y in range(4):
                y = block_y * 4 + local_y
                if y >= height:
                    continue
                for local_x in range(4):
                    x = block_x * 4 + local_x
                    if x >= width:
                        continue
                    local_index = local_y * 4 + local_x
                    color = colors[(color_indices >> (local_index * 2)) & 0x3]
                    alpha = color[3] if alphas is None else alphas[local_index]
                    destination = (y * width + x) * 4
                    pixels[destination : destination + 4] = array(
                        "f",
                        (color[0], color[1], color[2], alpha),
                    )
    return pixels


def _decode_dxt_colors(
    data: bytes,
    *,
    force_four_color: bool,
) -> tuple[
    tuple[float, float, float, float],
    tuple[float, float, float, float],
    tuple[float, float, float, float],
    tuple[float, float, float, float],
]:
    color_0, color_1 = struct.unpack_from("<HH", data)
    first = _decode_rgb565(color_0)
    second = _decode_rgb565(color_1)
    if color_0 > color_1 or force_four_color:
        third = (
            (2.0 * first[0] + second[0]) / 3.0,
            (2.0 * first[1] + second[1]) / 3.0,
            (2.0 * first[2] + second[2]) / 3.0,
        )
        fourth = (
            (first[0] + 2.0 * second[0]) / 3.0,
            (first[1] + 2.0 * second[1]) / 3.0,
            (first[2] + 2.0 * second[2]) / 3.0,
        )
        return (
            (*first, 1.0),
            (*second, 1.0),
            (*third, 1.0),
            (*fourth, 1.0),
        )
    third = (
        (first[0] + second[0]) / 2.0,
        (first[1] + second[1]) / 2.0,
        (first[2] + second[2]) / 2.0,
    )
    return (
        (*first, 1.0),
        (*second, 1.0),
        (*third, 1.0),
        (0.0, 0.0, 0.0, 0.0),
    )


def _decode_rgb565(value: int) -> tuple[float, float, float]:
    return (
        ((value >> 11) & 0x1F) / 31.0,
        ((value >> 5) & 0x3F) / 63.0,
        (value & 0x1F) / 31.0,
    )


def _decode_dxt5_alphas(data: bytes) -> tuple[float, ...]:
    alpha_0 = data[0]
    alpha_1 = data[1]
    table: list[float] = [alpha_0 / 255.0, alpha_1 / 255.0]
    if alpha_0 > alpha_1:
        table.extend(
            ((7 - index) * alpha_0 + index * alpha_1) / (7.0 * 255.0)
            for index in range(1, 7)
        )
    else:
        table.extend(
            ((5 - index) * alpha_0 + index * alpha_1) / (5.0 * 255.0)
            for index in range(1, 5)
        )
        table.extend((0.0, 1.0))
    indices = int.from_bytes(data[2:8], "little")
    return tuple(table[(indices >> (index * 3)) & 0x7] for index in range(16))
