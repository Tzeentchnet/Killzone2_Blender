"""Synthetic Blender-side builder tests.

These run under Blender's bundled Python or the ``bpy`` wheel from PyPI
(``pip install bpy==5.2.*``) and need no game data.
"""

from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    import bpy
except ModuleNotFoundError:
    bpy = None


def _mesh_inputs(import_scene, vertex_semantic):
    positions = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (1.0, 1.0, 0.0)]
    normals = [(0.0, 0.0, 1.0)] * 4
    # Packed level UV0: lightmap XY plus a pre-flipped diffuse pair in ZW.
    uv0 = [(0.1, 0.2, 0.3, 0.4), (0.5, 0.6, 0.7, 0.8), (0.9, 0.1, 0.2, 0.3), (0.4, 0.5, 0.6, 0.7)]
    uv1 = [(0.25, 0.75), (0.5, 0.5), (0.75, 0.25), (1.0, 0.0)]
    streams = {
        vertex_semantic.POSITION: positions,
        vertex_semantic.NORMAL: normals,
        vertex_semantic.UV0: uv0,
        vertex_semantic.UV1: uv1,
    }
    return streams, types.SimpleNamespace(base_vertex=0), types.SimpleNamespace(
        indexes=(0, 1, 2, 1, 3, 2)
    )


@unittest.skipUnless(bpy is not None, "requires Blender's bpy module")
class BlenderBuilderTests(unittest.TestCase):
    def setUp(self) -> None:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        from io_scene_killzone2 import import_scene

        self.import_scene = import_scene

    def test_mesh_uv_layers_match_per_loop_conversion(self) -> None:
        from io_scene_killzone2.kz2_assets.geometry import blender_uv_pairs
        from io_scene_killzone2.kz2_assets.model import VertexSemantic

        streams, primitive, index_array = _mesh_inputs(
            self.import_scene,
            VertexSemantic,
        )
        original = self.import_scene.decode_semantic
        self.import_scene.decode_semantic = lambda _array, semantic: streams.get(
            semantic
        )
        try:
            mesh = self.import_scene._create_mesh_data(
                "uv_test",
                primitive,
                object(),
                index_array,
                self.import_scene.ImportOptions(flip_uv_v=True),
            )
        finally:
            self.import_scene.decode_semantic = original

        self.assertEqual(
            ["UV0", "DiffuseUV", "UV1"],
            [layer.name for layer in mesh.uv_layers],
        )
        self.assertEqual("DiffuseUV", mesh.uv_layers.active.name)
        for loop in mesh.loops:
            vertex = loop.vertex_index
            lightmap, diffuse = blender_uv_pairs(streams[VertexSemantic.UV0][vertex], flip_v=True)
            secondary, _ = blender_uv_pairs(streams[VertexSemantic.UV1][vertex], flip_v=True)
            for name, expected in (("UV0", lightmap), ("DiffuseUV", diffuse), ("UV1", secondary)):
                observed = mesh.uv_layers[name].data[loop.index].uv
                self.assertAlmostEqual(expected[0], observed[0], places=6)
                self.assertAlmostEqual(expected[1], observed[1], places=6)

    def test_skin_weights_are_batched_without_changing_values(self) -> None:
        mesh = bpy.data.meshes.new("skin_test")
        mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], (), [(0, 1, 2)])
        obj = bpy.data.objects.new("skin_test", mesh)
        vertex = types.SimpleNamespace
        vertices = (
            vertex(bone_indices=(0, 1, 2), weights=(0.5, 0.25, 0.25)),
            vertex(bone_indices=(1, 1, 0), weights=(0.5, 0.25, 0.0)),
            vertex(bone_indices=(2, 9, 0), weights=(1.0, 0.5, 0.0)),
        )
        result = self.import_scene.ImportResult()
        self.import_scene._add_skin_weights(
            obj,
            vertices,
            types.SimpleNamespace(bone_names=("a", "b", "c")),
            result,
        )

        def weights(index: int) -> dict[str, float]:
            return {
                obj.vertex_groups[group.group].name: round(group.weight, 6)
                for group in mesh.vertices[index].groups
            }

        self.assertEqual({"a": 0.5, "b": 0.25, "c": 0.25}, weights(0))
        self.assertEqual({"b": 0.75}, weights(1))
        self.assertEqual({"c": 1.0}, weights(2))
        self.assertEqual(1, len(result.warnings))
        self.assertIn("[9]", result.warnings[0])

    def test_action_joint_mismatch_fails_before_creating_data(self) -> None:
        armature = bpy.data.objects.new("rig", bpy.data.armatures.new("rig"))
        bpy.context.scene.collection.objects.link(armature)
        clip_type = type("FakeClip", (), {"joint_count": 2, "sample_frequency": 30.0})
        original = self.import_scene.EdgeAnimation
        self.import_scene.EdgeAnimation = clip_type
        try:
            with self.assertRaises(ValueError):
                self.import_scene._create_action(
                    armature,
                    (),
                    types.SimpleNamespace(index=0, display_id="00000000"),
                    types.SimpleNamespace(clip=clip_type(), prefix=None),
                    1.0,
                    24.0,
                )
        finally:
            self.import_scene.EdgeAnimation = original
        self.assertEqual(0, len(bpy.data.actions))
        self.assertIsNone(armature.animation_data)

    def test_generated_images_pack_in_memory(self) -> None:
        from io_scene_killzone2.import_materials import _pack_image

        for is_float, suffix, file_format in (
            (False, ".png", "PNG"),
            (True, ".exr", "OPEN_EXR"),
        ):
            image = bpy.data.images.new(
                f"pack_{suffix}",
                4,
                4,
                alpha=True,
                float_buffer=is_float,
            )
            image.pixels.foreach_set([0.5] * 64)
            _pack_image(image, filename="KZ2_test", is_float=is_float)
            self.assertIsNotNone(image.packed_file)
            self.assertEqual(file_format, image.file_format)
            self.assertEqual(f"//KZ2_test{suffix}", image.filepath_raw)


if __name__ == "__main__":
    unittest.main()
