from __future__ import annotations

import struct
import unittest

from io_scene_killzone2.kz2_assets.geometry import (
    blender_uv_pairs,
    decode_vertex_element,
    reconstruct_static_position,
)
from io_scene_killzone2.kz2_assets.model import VertexDeclaration, VertexStorage


class GeometryTests(unittest.TestCase):
    def test_x11_y11_z10_component_order(self) -> None:
        packed = (511 << 22) | (0 << 11) | 1023
        declaration = VertexDeclaration(
            semantic=6,
            byte_offset=0,
            storage_type=VertexStorage.X11Y11Z10_NORMALIZED,
            component_count=1,
            unknown=0,
        )
        value = decode_vertex_element(struct.pack(">I", packed), 0, declaration)
        self.assertAlmostEqual(1.0, value[0])
        self.assertAlmostEqual(0.0, value[1])
        self.assertAlmostEqual(1.0, value[2])

    def test_signed_short_normalized_clamps_minimum(self) -> None:
        declaration = VertexDeclaration(
            semantic=6,
            byte_offset=0,
            storage_type=VertexStorage.SIGNED_SHORT_NORMALIZED,
            component_count=1,
            unknown=0,
        )
        value = decode_vertex_element(struct.pack(">h", -32768), 0, declaration)
        self.assertEqual((-1.0,), value)

    def test_static_position_uses_local_translate_scale(self) -> None:
        position = reconstruct_static_position(
            (0.25, -1.0, 0.5),
            (202.16, -90.96965, 26.36305, 11.03035),
        )
        expected = (204.9175875, -102.0, 31.878225)
        for observed, wanted in zip(position, expected, strict=True):
            self.assertAlmostEqual(wanted, observed)

    def test_two_component_uv_uses_xy_for_both_layers(self) -> None:
        source, diffuse = blender_uv_pairs((0.25, 0.75), flip_v=True)
        self.assertEqual((0.25, 0.25), source)
        self.assertEqual(source, diffuse)

    def test_four_component_uv_splits_lightmap_and_diffuse_pairs(self) -> None:
        source, diffuse = blender_uv_pairs(
            (0.26611328125, 0.26611328125, 0.134521484375, 0.888671875),
            flip_v=True,
        )
        self.assertEqual((0.26611328125, 0.73388671875), source)
        self.assertEqual((0.134521484375, 0.888671875), diffuse)

    def test_packed_diffuse_v_can_use_source_diagnostic_convention(self) -> None:
        source, diffuse = blender_uv_pairs(
            (0.25, 0.75, 0.125, 0.875),
            flip_v=False,
        )
        self.assertEqual((0.25, 0.75), source)
        self.assertEqual((0.125, 0.125), diffuse)


if __name__ == "__main__":
    unittest.main()
