from __future__ import annotations

import struct
import unittest

from io_scene_killzone2.kz2_assets.core import CORE_SIGNATURE, CoreFile
from io_scene_killzone2.kz2_assets.decoder import CoreDecoder
from io_scene_killzone2.kz2_assets.model import (
    ResourcePrefix,
    RsxTextureDescriptor,
    Texture,
    TextureFormat,
    TextureType,
)
from io_scene_killzone2.kz2_assets.textures import (
    STATE_FILL,
    apply_argb_remap,
    decode_texture_surface,
    rectangular_morton,
    texture_layout,
)


def compact(value: int) -> bytes:
    if -128 <= value <= 127 and value not in (-128, -127):
        return struct.pack(">b", value)
    if -32768 <= value <= 32767:
        return b"\x81" + struct.pack(">h", value)
    return b"\x80" + struct.pack(">i", value)


def synthetic_core(payload: bytes) -> bytes:
    data = bytearray(CORE_SIGNATURE)
    data.append(1)
    data.extend(bytes(16))
    types = (b"Texture", b"String")
    data.extend(compact(len(types)))
    for type_name in types:
        data.extend(compact(len(type_name)))
        data.extend(type_name)
    data.extend(compact(1))
    data.append(0)
    data.extend(bytes.fromhex("00112233445566778899aabbccddeeff"))
    data.extend(struct.pack(">I", len(payload)))
    data.extend(bytes(12))
    data.extend(compact(0))
    data.extend(compact(1))
    data.append(1)
    data.extend(compact(1))
    data.extend(compact(1))
    data.append(0)
    data.extend(compact(0))
    data.extend(compact(0))
    data.extend(compact(0))
    data.extend(compact(0))
    data.extend(payload)
    return bytes(data)


def make_texture(
    pixel_format: TextureFormat,
    width: int,
    height: int,
    payload: bytes,
    *,
    texture_type: TextureType = TextureType.TEXTURE_2D,
    mip_count: int = 1,
    rsx_format: int = 0x86,
    remap: int = 0x0000AAE4,
    pitch: int = 0,
) -> Texture:
    descriptor = RsxTextureDescriptor(
        format=rsx_format,
        mipmap=mip_count,
        dimension=2,
        cubemap=int(texture_type is TextureType.CUBEMAP),
        remap=remap,
        width=width,
        height=height,
        depth=1,
        location=0,
        padding=0,
        pitch=pitch,
        offset=0,
    )
    return Texture(
        prefix=ResourcePrefix("", 0),
        texture_type=texture_type,
        width=width,
        height=height,
        pixel_format=pixel_format,
        memory_type=0,
        mip_count=mip_count,
        color_space=0,
        texcoord_type=0,
        creation_flags=1,
        allocation_size=len(payload) + 128,
        allocation_constant=0x01050000,
        payload_size=len(payload),
        state_words=(1, 0, 128, len(payload)),
        descriptor=descriptor,
        state_fill=STATE_FILL,
        payload=payload,
    )


class TextureTests(unittest.TestCase):
    def test_rectangular_morton_covers_each_texel(self) -> None:
        indexes = [
            rectangular_morton(x, y, 8, 2)
            for y in range(2)
            for x in range(8)
        ]
        self.assertEqual(list(range(16)), sorted(indexes))
        self.assertEqual([0, 1, 4, 5, 8, 9, 12, 13], indexes[:8])

    def test_measured_argb_remaps(self) -> None:
        stored = (0.1, 0.2, 0.3, 0.4)
        self.assertEqual((0.4, 0.3, 0.2, 0.1), apply_argb_remap(stored, 0xAA6C))
        self.assertEqual((0.1, 0.2, 0.3, 0.4), apply_argb_remap(stored, 0xAA93))
        self.assertEqual((0.2, 0.3, 0.4, 0.1), apply_argb_remap(stored, 0xAAE4))

    def test_static_rgba8_rectangular_deswizzle(self) -> None:
        width = 4
        height = 2
        swizzled = bytearray(width * height * 4)
        expected: list[tuple[int, int, int, int]] = []
        for y in range(height):
            for x in range(width):
                rgba = (x * 40, y * 80, 30, 255)
                expected.append(rgba)
                source = rectangular_morton(x, y, width, height) * 4
                swizzled[source : source + 4] = bytes(
                    (rgba[3], rgba[2], rgba[1], rgba[0])
                )
        texture = make_texture(
            TextureFormat.RGBA_8888,
            width,
            height,
            bytes(swizzled),
            rsx_format=0x85,
            remap=0xAA6C,
        )
        decoded = decode_texture_surface(texture)
        for index, rgba in enumerate(expected):
            actual = tuple(decoded.pixels[index * 4 : index * 4 + 4])
            for observed, channel in zip(actual, rgba, strict=True):
                self.assertAlmostEqual(channel / 255.0, observed)

    def test_dynamic_rgba8_stays_linear(self) -> None:
        texture = make_texture(
            TextureFormat.RGBA_8888,
            2,
            1,
            bytes((255, 0, 0, 255, 0, 255, 0, 128)),
            rsx_format=0xA5,
            remap=0xAA93,
            pitch=8,
        )
        pixels = decode_texture_surface(texture).pixels
        self.assertEqual((1.0, 0.0, 0.0, 1.0), tuple(pixels[:4]))
        self.assertAlmostEqual(128 / 255.0, pixels[7])

    def test_dxt1_color_indexes(self) -> None:
        indexes = sum(index << (pixel * 2) for pixel, index in enumerate(range(4)))
        block = struct.pack("<HHI", 0xF800, 0x001F, indexes)
        texture = make_texture(TextureFormat.S3TC1, 4, 4, block)
        pixels = decode_texture_surface(texture).pixels
        self.assertEqual((1.0, 0.0, 0.0, 1.0), tuple(pixels[0:4]))
        self.assertEqual((0.0, 0.0, 1.0, 1.0), tuple(pixels[4:8]))
        self.assertAlmostEqual(2.0 / 3.0, pixels[8])
        self.assertAlmostEqual(1.0 / 3.0, pixels[10])

    def test_dxt3_explicit_alpha(self) -> None:
        alpha_bits = sum(
            value << (index * 4) for index, value in enumerate((0, 5, 10, 15))
        )
        block = alpha_bits.to_bytes(8, "little") + struct.pack(
            "<HHI", 0xFFFF, 0, 0
        )
        texture = make_texture(TextureFormat.S3TC3, 4, 4, block, rsx_format=0x87)
        pixels = decode_texture_surface(texture).pixels
        self.assertEqual(0.0, pixels[3])
        self.assertAlmostEqual(1.0 / 3.0, pixels[7])
        self.assertAlmostEqual(2.0 / 3.0, pixels[11])
        self.assertEqual(1.0, pixels[15])

    def test_dxt5_interpolated_alpha(self) -> None:
        alpha_indexes = sum(index << (pixel * 3) for pixel, index in enumerate(range(8)))
        alpha = bytes((255, 0)) + alpha_indexes.to_bytes(6, "little")
        block = alpha + struct.pack("<HHI", 0xFFFF, 0, 0)
        texture = make_texture(TextureFormat.S3TC5, 4, 4, block, rsx_format=0x88)
        pixels = decode_texture_surface(texture).pixels
        self.assertEqual(1.0, pixels[3])
        self.assertEqual(0.0, pixels[7])
        self.assertAlmostEqual(6.0 / 7.0, pixels[11])
        self.assertAlmostEqual(1.0 / 7.0, pixels[31])

    def test_big_endian_half_float_ramp(self) -> None:
        payload = b"".join(
            struct.pack(">4e", *rgba)
            for rgba in ((0.0, 0.5, 1.0, 1.0), (1.0, 0.25, 0.0, 0.5))
        )
        texture = make_texture(
            TextureFormat.RGBA_FLOAT_16,
            2,
            1,
            payload,
            rsx_format=0x9A,
        )
        self.assertEqual(
            (0.0, 0.5, 1.0, 1.0, 1.0, 0.25, 0.0, 0.5),
            tuple(decode_texture_surface(texture).pixels),
        )

    def test_cubemap_face_chain_alignment(self) -> None:
        texture = make_texture(
            TextureFormat.S3TC1,
            128,
            128,
            bytes(66_048),
            texture_type=TextureType.CUBEMAP,
            mip_count=6,
        )
        layout = texture_layout(texture)
        self.assertEqual(10_920, layout.logical_face_size)
        self.assertEqual(11_008, layout.face_stride)
        self.assertEqual(66_048, layout.payload_size)
        self.assertEqual(65_960, layout.logical_storage_end)

    def test_texture_resource_parser(self) -> None:
        pixels = bytes((255, 0, 0, 255, 0, 255, 0, 255))
        padded = pixels + bytes([0xEE]) * 120
        descriptor = (
            bytes((0xA5, 1, 2, 0))
            + struct.pack(">IHHHBBII", 0xAA93, 2, 1, 1, 0, 0, 8, 0)
        )
        body = (
            bytes((0, 0))
            + struct.pack(
                ">8i4I",
                0,
                2,
                1,
                TextureFormat.RGBA_8888,
                0,
                1,
                0,
                0,
                3,
                len(padded) + 128,
                0x01050000,
                len(padded),
            )
            + struct.pack(">4I", 1, 0, 128, len(pixels))
            + descriptor
            + STATE_FILL
            + padded
        )
        core = CoreFile.from_bytes(synthetic_core(body), source="texture.core")
        texture = CoreDecoder(core).decode(0)
        self.assertIsInstance(texture, Texture)
        self.assertEqual((2, 1), (texture.width, texture.height))
        self.assertEqual(0xAA93, texture.descriptor.remap)
        self.assertEqual((1.0, 0.0, 0.0, 1.0), tuple(
            decode_texture_surface(texture).pixels[:4]
        ))


if __name__ == "__main__":
    unittest.main()
