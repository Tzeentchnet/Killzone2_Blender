from __future__ import annotations

import os
import sys
import unittest
from collections import Counter
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
    def setUp(self) -> None:
        bpy.ops.wm.read_factory_settings(use_empty=True)

    def tearDown(self) -> None:
        bpy.ops.wm.read_factory_settings(use_empty=True)

    def assert_tank_material_profiles(
        self,
        expected: dict[
            str,
            tuple[str, str | None, str | None, str, str | None],
        ],
    ) -> None:
        materials_by_shader: dict[str, list] = {}
        for material in bpy.data.materials:
            shader_id = material.get("kz2_shader_object_id")
            if shader_id in expected:
                materials_by_shader.setdefault(shader_id, []).append(material)

        self.assertEqual(set(expected), set(materials_by_shader))
        for shader_id, materials in materials_by_shader.items():
            (
                profile_id,
                normal_sampler,
                detail_normal_sampler,
                material_sampler,
                secondary_base_color_sampler,
            ) = expected[shader_id]
            for material in materials:
                with self.subTest(
                    material=material.name,
                    shader_id=shader_id,
                ):
                    self.assertEqual(
                        profile_id,
                        material["kz2_profile_id"],
                    )
                    nodes = material.node_tree.nodes
                    principled = next(
                        node
                        for node in nodes
                        if node.bl_idname == "ShaderNodeBsdfPrincipled"
                    )
                    self.assertTrue(
                        principled.inputs["Roughness"].is_linked
                    )
                    self.assertEqual(
                        "KZ2_Roughness",
                        principled.inputs["Roughness"]
                        .links[0]
                        .from_node.name,
                    )
                    material_texture = (
                        nodes["KZ2_MaterialChannels"]
                        .inputs["Color"]
                        .links[0]
                        .from_node
                    )
                    self.assertEqual(
                        material_sampler,
                        material_texture["kz2_sampler"],
                    )
                    self.assertEqual(
                        "Non-Color",
                        material_texture.image.colorspace_settings.name,
                    )
                    if normal_sampler is not None:
                        self.assertTrue(
                            principled.inputs["Normal"].is_linked
                        )
                        normal_texture = (
                            nodes["KZ2_NormalChannels"]
                            .inputs["Color"]
                            .links[0]
                            .from_node
                        )
                        self.assertEqual(
                            normal_sampler,
                            normal_texture["kz2_sampler"],
                        )
                        self.assertEqual(
                            "Non-Color",
                            normal_texture.image.colorspace_settings.name,
                        )
                    if detail_normal_sampler is not None:
                        detail_texture = next(
                            node
                            for node in nodes
                            if node.get("kz2_sampler")
                            == detail_normal_sampler
                        )
                        self.assertEqual(
                            "detail normal",
                            detail_texture["kz2_profile_role"],
                        )
                        self.assertFalse(
                            detail_texture.outputs["Color"].is_linked
                        )
                        self.assertEqual(
                            "retained unconnected",
                            material["kz2_preview_detail_normal"],
                        )
                    if secondary_base_color_sampler is not None:
                        base_color_mix = nodes[
                            "KZ2_BaseColorMultiply"
                        ]
                        self.assertEqual(
                            "MULTIPLY",
                            base_color_mix.blend_type,
                        )
                        self.assertEqual(
                            base_color_mix,
                            principled.inputs["Base Color"]
                            .links[0]
                            .from_node,
                        )
                        secondary_texture = (
                            base_color_mix.inputs["Color2"]
                            .links[0]
                            .from_node
                        )
                        self.assertEqual(
                            secondary_base_color_sampler,
                            secondary_texture["kz2_sampler"],
                        )
                        secondary_uv = (
                            secondary_texture.inputs["Vector"]
                            .links[0]
                            .from_node
                        )
                        self.assertEqual("UV0", secondary_uv.uv_map)

    def test_dropship_imports_attached_turrets_and_profiled_material(
        self,
    ) -> None:
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
                resource_index_path=os.environ.get(
                    "KZ2_RESOURCE_INDEX",
                    "",
                ),
                import_materials=True,
                flip_uv_v=True,
            ),
        )

        self.assertEqual(19, result.meshes)
        self.assertEqual(3, result.armatures)
        self.assertEqual(10, result.actions)
        self.assertEqual(3, result.entity_instances)
        self.assertEqual([], result.warnings)
        armatures = [
            obj for obj in bpy.data.objects if obj.type == "ARMATURE"
        ]
        self.assertEqual(3, len(armatures))
        root_armature = next(
            obj for obj in armatures if len(obj.data.bones) == 43
        )
        turret_armatures = [
            obj for obj in armatures if len(obj.data.bones) == 3
        ]
        self.assertEqual(2, len(turret_armatures))
        rigged_meshes = [
            obj
            for obj in bpy.data.objects
            if obj.type == "MESH"
            and any(
                modifier.type == "ARMATURE"
                and modifier.object == root_armature
                for modifier in obj.modifiers
            )
        ]
        self.assertEqual(16, len(rigged_meshes))
        self.assertTrue(
            all(len(obj.vertex_groups) == 43 for obj in rigged_meshes)
        )
        turret_placements = [
            obj
            for obj in bpy.data.objects
            if obj.get("kz2_child_entity")
        ]
        self.assertEqual(2, len(turret_placements))
        self.assertEqual(
            {"Turret_Left", "Turret_Right"},
            {
                obj["kz2_attachment_target_name"]
                for obj in turret_placements
            },
        )
        self.assertTrue(
            all(obj.parent == root_armature for obj in turret_placements)
        )
        self.assertTrue(
            all(obj.parent_type == "BONE" for obj in turret_placements)
        )
        self.assertTrue(
            all(
                obj.parent_bone == "Body_Bone"
                for obj in turret_placements
            )
        )
        self.assertTrue(
            all(
                obj["kz2_attachment_target_kind"] == "helper"
                for obj in turret_placements
            )
        )
        self.assertTrue(
            all(
                armature.parent in turret_placements
                for armature in turret_armatures
            )
        )
        actions = [
            action
            for action in bpy.data.actions
            if action.get("kz2_entity_name") == "HGH_Dropship"
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
                action["kz2_target_armature"] == root_armature.name
                for action in actions
            )
        )
        self.assertTrue(all(action.layers for action in actions))
        self.assertEqual(
            "HGH_Dropship - Fly Away",
            root_armature.animation_data.action.name,
        )
        self.assertEqual(root_armature, bpy.context.active_object)
        self.assertTrue(root_armature.select_get())
        self.assertEqual(1, bpy.context.scene.frame_current)
        hover = bpy.data.actions["HGH_Dropship - Hover"]
        root_armature.animation_data.action = hover
        root_armature.animation_data.action_slot = hover.slots[0]
        bpy.context.scene.frame_set(round(hover.frame_range[0]))
        start_pose = tuple(
            value
            for bone in root_armature.pose.bones
            for row in bone.matrix_basis
            for value in row
        )
        bpy.context.scene.frame_set(round(hover.frame_range[1]))
        end_pose = tuple(
            value
            for bone in root_armature.pose.bones
            for row in bone.matrix_basis
            for value in row
        )
        self.assertTrue(
            any(
                abs(start - end) > 1e-5
                for start, end in zip(start_pose, end_pose, strict=True)
            )
        )
        turret_moved = False
        for action in actions:
            root_armature.animation_data.action = action
            root_armature.animation_data.action_slot = action.slots[0]
            bpy.context.scene.frame_set(round(action.frame_range[0]))
            start_transforms = tuple(
                tuple(value for row in obj.matrix_world for value in row)
                for obj in turret_placements
            )
            bpy.context.scene.frame_set(round(action.frame_range[1]))
            end_transforms = tuple(
                tuple(value for row in obj.matrix_world for value in row)
                for obj in turret_placements
            )
            turret_moved = any(
                abs(start - end) > 1e-5
                for start_matrix, end_matrix in zip(
                    start_transforms,
                    end_transforms,
                    strict=True,
                )
                for start, end in zip(
                    start_matrix,
                    end_matrix,
                    strict=True,
                )
            )
            if turret_moved:
                break
        self.assertTrue(turret_moved)

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
        self.assertFalse(material["kz2_profile_normal_y_flip"])
        self.assertTrue(material["kz2_preview_normal_y_flip"])
        self.assertTrue(principled.inputs["Normal"].is_linked)
        normal_map = principled.inputs["Normal"].links[0].from_node
        self.assertEqual("KZ2_NormalMap", normal_map.name)
        self.assertEqual("TANGENT", normal_map.space)
        self.assertEqual("DiffuseUV", normal_map.uv_map)
        self.assertEqual(
            "KZ2_NormalColor",
            normal_map.inputs["Color"].links[0].from_node.name,
        )
        normal_texture = (
            nodes["KZ2_NormalChannels"]
            .inputs["Color"]
            .links[0]
            .from_node
        )
        self.assertEqual("inSampler3", normal_texture["kz2_sampler"])
        self.assertEqual(
            "Non-Color",
            normal_texture.image.colorspace_settings.name,
        )

    @unittest.skipUnless(
        os.environ.get("KZ2_EXHAUSTIVE_E2E") == "1",
        "requires KZ2_EXHAUSTIVE_E2E=1",
    )
    def test_isa_intruder_uses_profiled_diffuse_uv_transform(self) -> None:
        from io_scene_killzone2.import_scene import (
            ImportOptions,
            import_psarc_entity,
        )

        archive_path = str(Path(os.environ["KZ2_PSARC"]).resolve())
        core_path = (
            "LocalCachePS3/lumps/"
            "assets_description.vehicles.isa_intruder_isa_intruder.core"
        )
        result = import_psarc_entity(
            bpy.context,
            archive_path,
            core_path,
            22,
            ImportOptions(
                source_psarc=archive_path,
                resource_index_path=os.environ.get(
                    "KZ2_RESOURCE_INDEX",
                    "",
                ),
                import_materials=True,
                flip_uv_v=True,
            ),
        )

        self.assertEqual(17, result.meshes)
        self.assertEqual(1, result.armatures)
        self.assertEqual(1, result.actions)
        self.assertEqual(3, result.materials)
        expected_profiles = {
            "631dc38edec0712bbb2713bb8a048023": (
                "isa_intruder_detail_deferred"
            ),
            "c69e155a3ee8bf78fb69eb313bd02765": (
                "isa_intruder_detail_deferred"
            ),
            "454c34802c8ecfd0f88b37607d5b615b": "isa_intruder_deferred",
        }
        materials = {
            material["kz2_shader_object_id"]: material
            for material in bpy.data.materials
            if material.get("kz2_shader_object_id") in expected_profiles
        }
        self.assertEqual(expected_profiles.keys(), materials.keys())
        for shader_object_id, profile_id in expected_profiles.items():
            with self.subTest(shader_object_id=shader_object_id):
                material = materials[shader_object_id]
                self.assertEqual(profile_id, material["kz2_profile_id"])
                self.assertEqual("proven", material["kz2_profile_status"])
                self.assertEqual(
                    "inSampler0",
                    material["kz2_preview_base_color"],
                )
                self.assertEqual(
                    "shader_profile",
                    material["kz2_preview_base_color_source"],
                )
                self.assertEqual(
                    "DiffuseUV",
                    material["kz2_preview_uv_map"],
                )
                self.assertTrue(material["kz2_preview_diffuse_v_flip"])
                principled = next(
                    node
                    for node in material.node_tree.nodes
                    if node.bl_idname == "ShaderNodeBsdfPrincipled"
                )
                texture = (
                    principled.inputs["Base Color"].links[0].from_node
                )
                self.assertEqual("inSampler0", texture["kz2_sampler"])
                mapping = texture.inputs["Vector"].links[0].from_node
                self.assertEqual("KZ2_DiffuseUVTransform", mapping.name)
                uv = mapping.inputs["Vector"].links[0].from_node
                self.assertEqual("DiffuseUV", uv.uv_map)

        profiled_meshes = [
            obj
            for obj in bpy.data.objects
            if obj.type == "MESH"
            and any(
                material is not None
                and material.get("kz2_shader_object_id")
                in expected_profiles
                for material in obj.data.materials
            )
        ]
        self.assertTrue(profiled_meshes)
        self.assertTrue(
            all(
                {"UV0", "DiffuseUV"}.issubset(obj.data.uv_layers.keys())
                for obj in profiled_meshes
            )
        )

    @unittest.skipUnless(
        os.environ.get("KZ2_EXHAUSTIVE_E2E") == "1",
        "requires KZ2_EXHAUSTIVE_E2E=1",
    )
    def test_hgh_tank_imports_destructibility_hull_and_cupola(self) -> None:
        from io_scene_killzone2.import_scene import (
            ImportOptions,
            import_psarc_entity,
        )

        archive_path = str(Path(os.environ["KZ2_PSARC"]).resolve())
        core_path = (
            "LocalCachePS3/lumps/"
            "levels.single_player.corinth_river.section_06_dam."
            "assets_optimized_damgp_scriptedweaponartillery_isatank_"
            "hghtank_damart.core"
        )

        result = import_psarc_entity(
            bpy.context,
            archive_path,
            core_path,
            8393,
            ImportOptions(
                source_psarc=archive_path,
                resource_index_path=os.environ.get(
                    "KZ2_RESOURCE_INDEX",
                    "",
                ),
                import_materials=True,
                import_animations=True,
                flip_uv_v=True,
            ),
        )

        meshes = [
            obj for obj in bpy.data.objects if obj.type == "MESH"
        ]
        part_meshes = [
            obj
            for obj in meshes
            if "kz2_destructibility_part_id" in obj
        ]
        child_entities = [
            obj
            for obj in bpy.data.objects
            if obj.get("kz2_child_entity")
        ]
        actions = list(bpy.data.actions)
        self.assertEqual(53, result.meshes)
        self.assertEqual(2, result.armatures)
        self.assertEqual(40, result.actions)
        self.assertEqual(2, result.entity_instances)
        self.assertEqual(53, len(meshes))
        self.assertEqual(42, len(part_meshes))
        self.assertEqual(
            {
                1,
                2,
                3,
                4,
                5,
                6,
                8,
                9,
                10,
                11,
                12,
                13,
                14,
                15,
                16,
                17,
            },
            {
                int(obj["kz2_destructibility_part_id"])
                for obj in part_meshes
            },
        )
        self.assertNotIn(
            10292,
            {
                int(obj["kz2_mesh_object_index"])
                for obj in meshes
                if "kz2_mesh_object_index" in obj
            },
        )
        rigid_track = next(
            obj
            for obj in meshes
            if obj.get("kz2_mesh_object_index") == 613
        )
        self.assertEqual("ARMATURE", rigid_track.parent.type)
        self.assertEqual("BONE", rigid_track.parent_type)
        self.assertEqual(
            "wheel5_right_suspension",
            rigid_track.parent_bone,
        )
        self.assertTrue(
            rigid_track["kz2_destructibility_bone_inferred"]
        )
        before = rigid_track.matrix_world.translation.copy()
        rigid_track.parent.pose.bones[
            rigid_track.parent_bone
        ].location.x += 0.5
        bpy.context.view_layer.update()
        self.assertGreater(
            (rigid_track.matrix_world.translation - before).length,
            0.1,
        )
        self.assertEqual(40, len(actions))
        self.assertTrue(
            all(
                action.use_frame_range
                and action.frame_start == 1.0
                and action.frame_end >= action.frame_start
                for action in actions
            )
        )
        self.assertEqual(
            {
                "HGH_Tank": 21,
                "HGH_M1_LMG_Mounted": 19,
            },
            Counter(
                action.get("kz2_entity_name")
                for action in actions
            ),
        )
        self.assertEqual(
            {
                "Skeleton_11070": 21,
                "Skeleton_11067": 19,
            },
            Counter(
                action.get("kz2_target_armature")
                for action in actions
            ),
        )
        self.assertEqual(
            ["HGH_M1_LMG_Mounted"],
            [obj.name for obj in child_entities],
        )
        self.assertEqual("ARMATURE", child_entities[0].parent.type)
        self.assertEqual("BONE", child_entities[0].parent_type)
        self.assertEqual("gunner_bone", child_entities[0].parent_bone)
        self.assertEqual(
            "Gunner_Bone",
            child_entities[0]["kz2_attachment_target_name"],
        )
        self.assert_tank_material_profiles(
            {
                "37118f7b07d5263cfeee4f6d6b6a7ba4": (
                    "hgh_tank_layered_deferred",
                    "inSampler3",
                    "inSampler4",
                    "inSampler1",
                    None,
                ),
                "90f8c2677ed634533711eb2bfed57af0": (
                    "hgh_tank_layered_deferred",
                    "inSampler3",
                    "inSampler4",
                    "inSampler1",
                    None,
                ),
                "6cf769273cef73fc83c4b5f780adfebc": (
                    "hgh_tank_layered_deferred",
                    "inSampler3",
                    "inSampler4",
                    "inSampler1",
                    None,
                ),
                "e03466b9264b1c8a6e663a39cb0f511e": (
                    "hgh_tank_track_deferred",
                    "inSampler4",
                    None,
                    "inSampler2",
                    "inSampler1",
                ),
                "b0a8b52eb0560707ecc42dc8c1ec7f88": (
                    "hgh_tank_compact_deferred",
                    "inSampler2",
                    None,
                    "inSampler1",
                    None,
                ),
                "a58e7b007d4d88e2ddfaa61af86a4ab7": (
                    "hgh_tank_packed_g49",
                    None,
                    None,
                    "inSampler1",
                    None,
                ),
                "5c879214cf2789ce4728d5de9d9cbc16": (
                    "hgh_tank_packed_r49",
                    None,
                    None,
                    "inSampler1",
                    None,
                ),
            }
        )
        self.assertEqual(2, len(result.warnings))
        self.assertTrue(
            all(
                "scalar/user channels" in warning
                for warning in result.warnings
            )
        )

    @unittest.skipUnless(
        os.environ.get("KZ2_EXHAUSTIVE_E2E") == "1",
        "requires KZ2_EXHAUSTIVE_E2E=1",
    )
    def test_hgh_tank_fake_shadow_preference_restores_mesh(self) -> None:
        from io_scene_killzone2.import_scene import (
            ImportOptions,
            import_psarc_entity,
        )

        archive_path = str(Path(os.environ["KZ2_PSARC"]).resolve())
        core_path = (
            "LocalCachePS3/lumps/"
            "levels.single_player.corinth_river.section_06_dam."
            "assets_optimized_damgp_scriptedweaponartillery_isatank_"
            "hghtank_damart.core"
        )

        result = import_psarc_entity(
            bpy.context,
            archive_path,
            core_path,
            8393,
            ImportOptions(
                source_psarc=archive_path,
                resource_index_path=os.environ.get(
                    "KZ2_RESOURCE_INDEX",
                    "",
                ),
                import_fake_shadow_meshes=True,
                import_materials=False,
                import_animations=False,
            ),
        )

        fake_shadow = next(
            obj
            for obj in bpy.data.objects
            if obj.get("kz2_mesh_object_index") == 10292
        )
        self.assertEqual(54, result.meshes)
        self.assertTrue(fake_shadow["kz2_fake_shadow_mesh"])
        self.assertEqual(99, fake_shadow["kz2_destructibility_part_id"])

    @unittest.skipUnless(
        os.environ.get("KZ2_EXHAUSTIVE_E2E") == "1",
        "requires KZ2_EXHAUSTIVE_E2E=1",
    )
    def test_isa_tank_imports_initial_tracks_and_body_parts(self) -> None:
        from io_scene_killzone2.import_scene import (
            ImportOptions,
            import_psarc_entity,
        )

        archive_path = str(Path(os.environ["KZ2_PSARC"]).resolve())
        core_path = (
            "LocalCachePS3/lumps/"
            "levels.single_player.corinth_river.section_06_dam."
            "assets_optimized_damgp_scriptedweaponartillery_isatank_"
            "hghtank_damart.core"
        )

        result = import_psarc_entity(
            bpy.context,
            archive_path,
            core_path,
            8392,
            ImportOptions(
                source_psarc=archive_path,
                resource_index_path=os.environ.get(
                    "KZ2_RESOURCE_INDEX",
                    "",
                ),
                import_materials=True,
                import_animations=True,
                flip_uv_v=True,
            ),
        )

        meshes = [
            obj for obj in bpy.data.objects if obj.type == "MESH"
        ]
        armatures = {
            obj.name: obj
            for obj in bpy.data.objects
            if obj.type == "ARMATURE"
        }
        actions = list(bpy.data.actions)
        self.assertEqual(63, result.meshes)
        self.assertEqual(2, result.armatures)
        self.assertEqual(54, result.actions)
        self.assertEqual(2, result.entity_instances)
        self.assertEqual(63, len(meshes))
        self.assertNotIn(
            10317,
            {
                int(obj["kz2_mesh_object_index"])
                for obj in meshes
                if "kz2_mesh_object_index" in obj
            },
        )
        self.assertEqual(
            {
                "Skeleton_11068": "ISA_Tank",
                "Skeleton_90": "ISA_Tank_LMG_Mounted",
            },
            {
                name: armature["kz2_armature_owner"]
                for name, armature in armatures.items()
            },
        )
        self.assertTrue(
            {
                "thread",
                "body",
                "turret",
                "rack_bone",
                "Launcher1_Right",
                "wheel2_right_track",
                "Tank_Camera_Joint",
            }.issubset(armatures["Skeleton_11068"].data.bones.keys())
        )
        body_parts = [
            obj
            for obj in meshes
            if obj.get("kz2_destructibility_bone") == "Body"
        ]
        self.assertEqual(16, len(body_parts))
        self.assertTrue(
            all(
                obj.parent == armatures["Skeleton_11068"]
                and obj.parent_type == "BONE"
                and obj.parent_bone == "body"
                and obj["kz2_destructibility_bone_attached"]
                and obj["kz2_destructibility_parent_bone"] == "body"
                and obj.matrix_world.translation.length > 0.5
                for obj in body_parts
            )
        )
        for mesh_index in (631, 653, 698):
            part = next(
                obj
                for obj in meshes
                if obj.get("kz2_mesh_object_index") == mesh_index
            )
            self.assertEqual("body", part.parent_bone)
            self.assertGreater(part.matrix_world.translation.length, 0.5)
        track_expectations = {
            10301: (6, "LeftTrackInitialState", "Track_Left"),
            10303: (7, "RightTrackInitialState", "Track_Right"),
        }
        for mesh_index, expected in track_expectations.items():
            tracks = [
                obj
                for obj in meshes
                if obj.get("kz2_mesh_object_index") == mesh_index
            ]
            self.assertEqual(2, len(tracks))
            self.assertEqual(
                {"P00", "P01"},
                {obj.name.rsplit("_", 1)[-1] for obj in tracks},
            )
            self.assertTrue(
                all(
                    (
                        obj["kz2_destructibility_part_id"],
                        obj["kz2_destructibility_state_name"],
                        obj["kz2_destructibility_model_part_name"],
                    )
                    == expected
                    for obj in tracks
                )
            )
        self.assertEqual(
            {
                "ISA_Tank": 36,
                "ISA_Tank_LMG_Mounted": 18,
            },
            Counter(
                action.get("kz2_entity_name")
                for action in actions
            ),
        )
        self.assertEqual(
            {
                "Skeleton_11068": 36,
                "Skeleton_90": 18,
            },
            Counter(
                action.get("kz2_target_armature")
                for action in actions
            ),
        )
        self.assertTrue(
            all(
                action.use_frame_range
                and action.frame_start == 1.0
                and action.frame_end >= action.frame_start
                for action in actions
            )
        )
        self.assert_tank_material_profiles(
            {
                "c4de041dda84841306f1f2921c995c89": (
                    "isa_tank_layered_deferred",
                    "inSampler2",
                    "inSampler3",
                    "inSampler1",
                    None,
                ),
                "7a83011fface8f01eca0b3604fb22f5a": (
                    "isa_tank_layered_deferred",
                    "inSampler2",
                    "inSampler3",
                    "inSampler1",
                    None,
                ),
                "710036fa5d9aaddd90279f934e1c7275": (
                    "isa_tank_layered_deferred",
                    "inSampler2",
                    "inSampler3",
                    "inSampler1",
                    None,
                ),
                "0a1b584d6a258c6dc25610e3289d5406": (
                    "isa_tank_layered_deferred",
                    "inSampler2",
                    "inSampler3",
                    "inSampler1",
                    None,
                ),
                "ef5c0a58695ab51ed3bd2e44d1ea0c91": (
                    "isa_tank_layered_deferred",
                    "inSampler2",
                    "inSampler3",
                    "inSampler1",
                    None,
                ),
                "2283f987591d17d65f60db32222736bb": (
                    "isa_tank_track_deferred",
                    "inSampler3",
                    None,
                    "inSampler2",
                    "inSampler1",
                ),
                "d8de6701c66b7caece3b70e3555b198b": (
                    "isa_tank_compact_deferred",
                    "inSampler2",
                    None,
                    "inSampler1",
                    None,
                ),
            }
        )
        self.assertEqual(2, len(result.warnings))
        self.assertTrue(
            all(
                "scalar/user channels" in warning
                for warning in result.warnings
            )
        )

    @unittest.skipUnless(
        os.environ.get("KZ2_EXHAUSTIVE_E2E") == "1",
        "requires KZ2_EXHAUSTIVE_E2E=1",
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
                resource_index_path=os.environ.get(
                    "KZ2_RESOURCE_INDEX",
                    "",
                ),
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

    def test_z_commando_head_uses_body_armature(self) -> None:
        from io_scene_killzone2.import_scene import (
            ImportOptions,
            import_psarc_entity,
        )

        archive_path = str(Path(os.environ["KZ2_PSARC"]).resolve())
        core_path = (
            "LocalCachePS3/lumps/"
            "assets_description.characters.hgh_commando_"
            "hgh_commando_character.core"
        )
        existing_objects = set(bpy.data.objects)

        result = import_psarc_entity(
            bpy.context,
            archive_path,
            core_path,
            16,
            ImportOptions(
                source_psarc=archive_path,
                resource_index_path=os.environ.get(
                    "KZ2_RESOURCE_INDEX",
                    "",
                ),
                import_materials=True,
                import_animations=True,
            ),
        )

        imported_objects = [
            obj for obj in bpy.data.objects if obj not in existing_objects
        ]
        armatures = [
            obj for obj in imported_objects if obj.type == "ARMATURE"
        ]
        self.assertEqual(1, result.armatures)
        self.assertEqual(1, len(armatures))
        self.assertEqual(91, len(armatures[0].data.bones))
        self.assertEqual(0, result.actions)
        self.assertTrue(
            armatures[0].animation_data is None
            or armatures[0].animation_data.action is None
        )
        head_mesh_indices = {216, 222, 225, 230}
        head_meshes = [
            obj
            for obj in imported_objects
            if obj.type == "MESH"
            and obj.get("kz2_mesh_object_index") in head_mesh_indices
        ]
        self.assertEqual(head_mesh_indices, {
            obj["kz2_mesh_object_index"] for obj in head_meshes
        })
        self.assertTrue(
            all(
                any(
                    modifier.type == "ARMATURE"
                    and modifier.object == armatures[0]
                    for modifier in obj.modifiers
                )
                for obj in head_meshes
            )
        )
        body_meshes = [
            obj
            for obj in imported_objects
            if obj.type == "MESH"
            and obj.get("kz2_mesh_object_index") not in head_mesh_indices
            and any(
                modifier.type == "ARMATURE"
                and modifier.object == armatures[0]
                for modifier in obj.modifiers
            )
        ]
        self.assertTrue(body_meshes)
        self.assertIn(
            220,
            {obj["kz2_mesh_object_index"] for obj in body_meshes},
        )
        helmet = next(
            obj
            for obj in imported_objects
            if obj.type == "MESH"
            and obj.get("kz2_mesh_object_index") == 18
            and not obj.hide_viewport
        )
        self.assertIs(armatures[0], helmet.parent)
        self.assertEqual("BONE", helmet.parent_type)
        self.assertEqual("neck", helmet.parent_bone)
        self.assertEqual(0, helmet["kz2_destructibility_part_id"])
        self.assertTrue(helmet["kz2_destructibility_bone_attached"])
        expected_profiles = {
            "2ee5e3ff3a42dd5cd1f33374891901f0": (
                "hgh_character_deferred_g24_5",
                "G",
                24.5,
            ),
            "3d23b1bce366078a44c0bfe6ac133f61": (
                "hgh_character_deferred_r14_7024",
                "R",
                14.7024,
            ),
            "1de0cb0694403a758185efc552229c6d": (
                "hgh_character_deferred_r49",
                "R",
                49.0,
            ),
            "ea746cea3656762cd8b70a78060d9654": (
                "hgh_character_deferred_r49",
                "R",
                49.0,
            ),
        }
        imported_materials = {
            material["kz2_shader_object_id"]: material
            for obj in imported_objects
            if obj.type == "MESH"
            for material in obj.data.materials
            if material is not None
            and material.get("kz2_shader_object_id") in expected_profiles
        }
        self.assertEqual(4, result.materials)
        self.assertEqual(expected_profiles.keys(), imported_materials.keys())
        for shader_object_id, (
            profile_id,
            power_channel,
            power_scale,
        ) in expected_profiles.items():
            with self.subTest(shader_object_id=shader_object_id):
                material = imported_materials[shader_object_id]
                self.assertEqual(profile_id, material["kz2_profile_id"])
                self.assertEqual(
                    power_channel,
                    material["kz2_profile_specular_power_channel"],
                )
                self.assertEqual(
                    power_scale,
                    material["kz2_profile_specular_power_scale"],
                )
                self.assertEqual(
                    "B",
                    material["kz2_profile_specular_intensity_channel"],
                )
                self.assertFalse(material["kz2_profile_normal_y_flip"])
                self.assertTrue(material["kz2_preview_normal_y_flip"])
                principled = next(
                    node
                    for node in material.node_tree.nodes
                    if node.bl_idname == "ShaderNodeBsdfPrincipled"
                )
                self.assertEqual(
                    "KZ2_NormalMap",
                    principled.inputs["Normal"].links[0].from_node.name,
                )
                self.assertEqual(
                    "KZ2_Roughness",
                    principled.inputs["Roughness"].links[0].from_node.name,
                )
        self.assertEqual(
            "ea746cea3656762cd8b70a78060d9654",
            helmet.data.materials[0]["kz2_shader_object_id"],
        )
        self.assertEqual(1, len(result.warnings))
        self.assertIn(
            "model could not be resolved",
            result.warnings[0],
        )

    @unittest.skipUnless(
        os.environ.get("KZ2_EXHAUSTIVE_E2E") == "1",
        "requires KZ2_EXHAUSTIVE_E2E=1",
    )
    def test_y_animated_commando_helmet_follows_neck(self) -> None:
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
        existing_objects = set(bpy.data.objects)

        result = import_psarc_entity(
            bpy.context,
            archive_path,
            core_path,
            132,
            ImportOptions(
                source_psarc=archive_path,
                resource_index_path=os.environ.get(
                    "KZ2_RESOURCE_INDEX",
                    "",
                ),
                import_materials=False,
                import_animations=True,
            ),
        )

        imported_objects = [
            obj for obj in bpy.data.objects if obj not in existing_objects
        ]
        armature = next(
            obj for obj in imported_objects if obj.type == "ARMATURE"
        )
        helmet = next(
            obj
            for obj in imported_objects
            if obj.type == "MESH"
            and obj.get("kz2_mesh_object_index") == 323
            and not obj.hide_viewport
        )
        action = next(
            action
            for action in bpy.data.actions
            if action.get("kz2_entity_name") == "HGH_Commando"
            and action.get("kz2_source_object") == 8897
        )

        self.assertEqual(9, result.actions)
        self.assertIs(armature, helmet.parent)
        self.assertEqual("BONE", helmet.parent_type)
        self.assertEqual("neck", helmet.parent_bone)
        armature.animation_data.action = action
        neck = armature.pose.bones["neck"]
        relative_matrices = []
        world_positions = []
        for frame in (1, 16, 31):
            bpy.context.scene.frame_set(frame)
            neck_world = armature.matrix_world @ neck.matrix
            relative_matrices.append(
                neck_world.inverted() @ helmet.matrix_world
            )
            world_positions.append(helmet.matrix_world.translation.copy())

        self.assertGreater(
            (world_positions[1] - world_positions[0]).length,
            0.5,
        )
        for matrix in relative_matrices[1:]:
            for row, expected_row in zip(
                matrix,
                relative_matrices[0],
                strict=True,
            ):
                for value, expected in zip(
                    row,
                    expected_row,
                    strict=True,
                ):
                    self.assertAlmostEqual(expected, value, places=5)

    @unittest.skipUnless(
        os.environ.get("KZ2_EXHAUSTIVE_E2E") == "1",
        "requires KZ2_EXHAUSTIVE_E2E=1",
    )
    def test_x_rifle_infantry_helmet_uses_declared_bone(self) -> None:
        from io_scene_killzone2.import_scene import (
            ImportOptions,
            import_psarc_entity,
        )

        archive_path = str(Path(os.environ["KZ2_PSARC"]).resolve())
        core_path = (
            "LocalCachePS3/lumps/"
            "assets_description.characters.hgh_rifle_infantry_"
            "hgh_rifle_infantry_character.core"
        )
        existing_objects = set(bpy.data.objects)

        import_psarc_entity(
            bpy.context,
            archive_path,
            core_path,
            15,
            ImportOptions(
                source_psarc=archive_path,
                resource_index_path=os.environ.get(
                    "KZ2_RESOURCE_INDEX",
                    "",
                ),
                import_materials=False,
                import_animations=False,
            ),
        )

        imported_objects = [
            obj for obj in bpy.data.objects if obj not in existing_objects
        ]
        armature = next(
            obj for obj in imported_objects if obj.type == "ARMATURE"
        )
        attached = [
            obj
            for obj in imported_objects
            if obj.type == "MESH"
            and obj.get("kz2_destructibility_bone_attached")
        ]

        self.assertEqual(
            {16, 17, 18, 19},
            {obj["kz2_mesh_object_index"] for obj in attached},
        )
        self.assertTrue(
            all(
                obj.parent == armature
                and obj.parent_type == "BONE"
                and obj.parent_bone == "neck"
                and obj["kz2_destructibility_part_id"] == 0
                for obj in attached
            )
        )


if __name__ == "__main__":
    unittest.main(argv=[__file__])
