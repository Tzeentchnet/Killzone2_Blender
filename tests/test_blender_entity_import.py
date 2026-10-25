from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    import bpy
except ModuleNotFoundError:
    bpy = None


@unittest.skipUnless(
    bpy is not None and os.environ.get("KZ2_PSARC"),
    "requires Blender and KZ2_PSARC",
)
class BlenderEntityImportTests(unittest.TestCase):
    def test_dropship_imports_rig_and_profiled_material(self) -> None:
        from io_scene_killzone2.import_scene import (
            ImportOptions,
            import_psarc_entity,
        )

        archive_path = str(Path(os.environ["KZ2_PSARC"]).resolve())
        core_path = (
            "LocalCachePS3/lumps/"
            "assets_description.vehicles.hgh_dropship_hgh_dropship.core"
        )

        result = import_psarc_entity(
            bpy.context,
            archive_path,
            core_path,
            45,
            ImportOptions(
                source_psarc=archive_path,
                import_materials=True,
                flip_uv_v=True,
            ),
        )

        self.assertEqual(17, result.meshes)
        self.assertEqual(1, result.armatures)
        self.assertEqual(4, result.actions)
        self.assertEqual([], result.warnings)
        armatures = [
            obj for obj in bpy.data.objects if obj.type == "ARMATURE"
        ]
        self.assertEqual(1, len(armatures))
        self.assertEqual(43, len(armatures[0].data.bones))
        rigged_meshes = [
            obj
            for obj in bpy.data.objects
            if obj.type == "MESH"
            and any(
                modifier.type == "ARMATURE"
                and modifier.object == armatures[0]
                for modifier in obj.modifiers
            )
        ]
        self.assertEqual(16, len(rigged_meshes))
        self.assertTrue(
            all(len(obj.vertex_groups) == 43 for obj in rigged_meshes)
        )
        actions = [
            action
            for action in bpy.data.actions
            if action.get("kz2_cached_entity")
        ]
        self.assertEqual(
            {
                "HGH_Dropship - Approach",
                "HGH_Dropship - Fly Away",
                "HGH_Dropship - Hover",
                "HGH_Dropship - Neutral",
            },
            {action.name for action in actions},
        )
        self.assertTrue(all(action.use_fake_user for action in actions))
        expected_action_data = {
            "HGH_Dropship - Approach": (76, 121.0),
            "HGH_Dropship - Fly Away": (46, 73.0),
            "HGH_Dropship - Hover": (31, 49.0),
            "HGH_Dropship - Neutral": (2, 1.8),
        }
        for action in actions:
            native_frame_count, expected_end = expected_action_data[
                action.name
            ]
            self.assertTrue(action.use_frame_range)
            self.assertAlmostEqual(1.0, action.frame_start)
            self.assertAlmostEqual(expected_end, action.frame_end, places=4)
            slot = action.slots[0]
            strip = action.layers[0].strips[0]
            channelbag = strip.channelbag(slot)
            self.assertEqual(430, len(channelbag.fcurves))
            self.assertEqual(
                native_frame_count * 430,
                sum(
                    len(curve.keyframe_points)
                    for curve in channelbag.fcurves
                ),
            )
        self.assertTrue(
            all(
                action["kz2_target_armature"] == armatures[0].name
                for action in actions
            )
        )
        self.assertTrue(all(action.layers for action in actions))
        self.assertEqual(
            "HGH_Dropship - Fly Away",
            armatures[0].animation_data.action.name,
        )
        self.assertEqual(armatures[0], bpy.context.active_object)
        self.assertTrue(armatures[0].select_get())
        self.assertEqual(1, bpy.context.scene.frame_current)
        hover = bpy.data.actions["HGH_Dropship - Hover"]
        armatures[0].animation_data.action = hover
        armatures[0].animation_data.action_slot = hover.slots[0]
        bpy.context.scene.frame_set(round(hover.frame_range[0]))
        start_pose = tuple(
            value
            for bone in armatures[0].pose.bones
            for row in bone.matrix_basis
            for value in row
        )
        bpy.context.scene.frame_set(round(hover.frame_range[1]))
        end_pose = tuple(
            value
            for bone in armatures[0].pose.bones
            for row in bone.matrix_basis
            for value in row
        )
        self.assertTrue(
            any(
                abs(start - end) > 1e-5
                for start, end in zip(start_pose, end_pose, strict=True)
            )
        )

        material = bpy.data.materials["KZ2_050b36ec20ad_T00"]
        self.assertEqual(
            "hgh_dropship_deferred",
            material["kz2_profile_id"],
        )
        nodes = material.node_tree.nodes
        principled = next(
            node
            for node in nodes
            if node.bl_idname == "ShaderNodeBsdfPrincipled"
        )
        self.assertTrue(principled.inputs["Roughness"].is_linked)
        self.assertEqual(
            "KZ2_Roughness",
            principled.inputs["Roughness"].links[0].from_node.name,
        )
        self.assertTrue(
            principled.inputs["Specular IOR Level"].is_linked
        )
        self.assertEqual(
            "KZ2_MaterialChannels",
            principled.inputs["Specular IOR Level"]
            .links[0]
            .from_node.name,
        )
        self.assertEqual(
            "Blue",
            principled.inputs["Specular IOR Level"]
            .links[0]
            .from_socket.name,
        )

    def test_static_tank_skips_unrelated_invalid_animation(self) -> None:
        from io_scene_killzone2.import_scene import (
            ImportOptions,
            import_psarc_entity,
        )

        archive_path = str(Path(os.environ["KZ2_PSARC"]).resolve())
        core_path = (
            "LocalCachePS3/lumps/"
            "levels.single_player.tharsis_refinery.section_train."
            "assets_optimized_traingp_trainhi_hghtanktrtrain_"
            "hghcommando_hghsupporttrooper.core"
        )

        result = import_psarc_entity(
            bpy.context,
            archive_path,
            core_path,
            713,
            ImportOptions(
                source_psarc=archive_path,
                import_materials=False,
            ),
        )

        self.assertEqual(36, result.meshes)
        self.assertEqual(1, result.armatures)
        self.assertEqual(22, result.actions)
        self.assertEqual(1, len(result.warnings))
        self.assertIn(
            "scalar/user channels",
            result.warnings[0],
        )
        actions = [
            action
            for action in bpy.data.actions
            if action.get("kz2_entity_name") == "HGH_Tank_STATIC"
        ]
        self.assertEqual(22, len(actions))
        self.assertTrue(all(action.use_frame_range for action in actions))
        self.assertNotIn(
            8859,
            {action["kz2_source_object"] for action in actions},
        )
        self.assertEqual("Skeleton_8972", bpy.context.active_object.name)


if __name__ == "__main__":
    unittest.main(argv=[__file__])
