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

    def assert_vehicle_material_profiles(
        self,
        expected: dict[str, str | None],
    ) -> None:
        materials_by_shader: dict[str, list] = {}
        for material in bpy.data.materials:
            shader_id = material.get("kz2_shader_object_id")
            if isinstance(shader_id, str):
                materials_by_shader.setdefault(shader_id, []).append(
                    material
                )

        self.assertEqual(set(expected), set(materials_by_shader))
        for shader_id, materials in materials_by_shader.items():
            profile_id = expected[shader_id]
            for material in materials:
                with self.subTest(
                    material=material.name,
                    shader_id=shader_id,
                ):
                    self.assertEqual(
                        profile_id,
                        material.get("kz2_profile_id"),
                    )
                    if profile_id is None:
                        continue

                    nodes = material.node_tree.nodes
                    principled = next(
                        node
                        for node in nodes
                        if node.bl_idname == "ShaderNodeBsdfPrincipled"
                    )
                    secondary_sampler = material.get(
                        "kz2_profile_secondary_base_color_sampler"
                    )
                    if secondary_sampler is not None:
                        self.assertEqual(
                            "multiply",
                            material[
                                "kz2_preview_base_color_combine"
                            ],
                        )
                        self.assertEqual(
                            "KZ2_BaseColorMultiply",
                            principled.inputs["Base Color"]
                            .links[0]
                            .from_node.name,
                        )

                    normal_sampler = material.get(
                        "kz2_profile_normal_sampler"
                    )
                    if normal_sampler is not None:
                        normal_node = next(
                            node
                            for node in nodes
                            if node.get("kz2_sampler")
                            == normal_sampler
                        )
                        self.assertEqual(
                            "Non-Color",
                            normal_node.image.colorspace_settings.name,
                        )
                        if normal_node.image["kz2_color_space"] == 1:
                            self.assertEqual(
                                "shader_profile",
                                normal_node.image[
                                    "kz2_color_space_override"
                                ],
                            )
                        self.assertEqual(
                            "KZ2_NormalMap",
                            principled.inputs["Normal"]
                            .links[0]
                            .from_node.name,
                        )
                        self.assertFalse(
                            material["kz2_profile_normal_y_flip"]
                        )
                        self.assertTrue(
                            material["kz2_preview_normal_y_flip"]
                        )

                    detail_sampler = material.get(
                        "kz2_profile_detail_normal_sampler"
                    )
                    if detail_sampler is not None:
                        detail_node = next(
                            node
                            for node in nodes
                            if node.get("kz2_sampler")
                            == detail_sampler
                        )
                        self.assertIn(
                            "detail normal",
                            detail_node["kz2_profile_role"],
                        )
                        self.assertEqual(
                            "Non-Color",
                            detail_node.image.colorspace_settings.name,
                        )
                        if detail_node.image["kz2_color_space"] == 1:
                            self.assertEqual(
                                "shader_profile",
                                detail_node.image[
                                    "kz2_color_space_override"
                                ],
                            )
                        self.assertFalse(
                            any(
                                output.is_linked
                                for output in detail_node.outputs
                            )
                        )
                        self.assertEqual(
                            "retained unconnected",
                            material["kz2_preview_detail_normal"],
                        )

                    material_sampler = material.get(
                        "kz2_profile_material_sampler"
                    )
                    if material_sampler is not None:
                        material_node = next(
                            node
                            for node in nodes
                            if node.get("kz2_sampler")
                            == material_sampler
                        )
                        self.assertIn(
                            "packed material",
                            material_node["kz2_profile_role"],
                        )
                        self.assertEqual(
                            "Non-Color",
                            material_node.image.colorspace_settings.name,
                        )
                        if material_node.image["kz2_color_space"] == 1:
                            self.assertEqual(
                                "shader_profile",
                                material_node.image[
                                    "kz2_color_space_override"
                                ],
                            )
                        if (
                            "kz2_profile_specular_power_channel"
                            in material
                        ):
                            self.assertTrue(
                                principled.inputs["Roughness"].is_linked
                            )
                            self.assertTrue(
                                principled.inputs[
                                    "Specular IOR Level"
                                ].is_linked
                            )
                        else:
                            self.assertFalse(
                                principled.inputs["Roughness"].is_linked
                            )
                            self.assertFalse(
                                principled.inputs[
                                    "Specular IOR Level"
                                ].is_linked
                            )

                    reflection_sampler = material.get(
                        "kz2_profile_reflection_sampler"
                    )
                    if reflection_sampler is not None:
                        reflection_node = next(
                            node
                            for node in nodes
                            if node.get("kz2_sampler")
                            == reflection_sampler
                        )
                        self.assertIn(
                            "IBL lookup",
                            reflection_node["kz2_profile_role"],
                        )

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
        reflection = next(
            node
            for node in material.node_tree.nodes
            if node.get("kz2_sampler") == "inSampler2"
        )
        self.assertEqual(
            "Non-Color",
            reflection.image.colorspace_settings.name,
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
        self.assert_vehicle_material_profiles(
            {
                "c54604143359f6cb1e7766c8fa2a3b38": (
                    "hgh_dropship_deferred"
                ),
                "0f8732ddf90aacb3a9f3b4c1e63fc752": (
                    "hgh_dropship_unbumped_deferred"
                ),
                "8c77e857231655920b6e4835ab731d4b": (
                    "hgh_dropship_deferred"
                ),
                "f33af2aa7a87628344572d581f1dda8f": None,
                "6b2068005285c8ab951a526590a4d8a4": (
                    "hgh_dropship_complex_deferred"
                ),
                "ed94308fbe5e1714583b41e9e0e7e3c0": (
                    "hgh_dropship_deferred_g49"
                ),
            }
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
                color_mix = (
                    principled.inputs["Base Color"]
                    .links[0]
                    .from_node
                )
                self.assertEqual("KZ2_BaseColorMultiply", color_mix.name)
                texture = color_mix.inputs["Color1"].links[0].from_node
                self.assertEqual("inSampler0", texture["kz2_sampler"])
                mapping = texture.inputs["Vector"].links[0].from_node
                self.assertEqual("KZ2_DiffuseUVTransform", mapping.name)
                uv = mapping.inputs["Vector"].links[0].from_node
                self.assertEqual("DiffuseUV", uv.uv_map)
                secondary = (
                    color_mix.inputs["Color2"].links[0].from_node
                )
                self.assertEqual(
                    "inSampler1",
                    secondary["kz2_sampler"],
                )
                secondary_uv = (
                    secondary.inputs["Vector"].links[0].from_node
                )
                self.assertEqual("UV0", secondary_uv.uv_map)

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
        self.assert_vehicle_material_profiles(
            {
                "454c34802c8ecfd0f88b37607d5b615b": (
                    "isa_intruder_deferred"
                ),
                "631dc38edec0712bbb2713bb8a048023": (
                    "isa_intruder_detail_deferred"
                ),
                "c69e155a3ee8bf78fb69eb313bd02765": (
                    "isa_intruder_detail_deferred"
                ),
            }
        )

    @unittest.skipUnless(
        os.environ.get("KZ2_EXHAUSTIVE_E2E") == "1",
        "requires KZ2_EXHAUSTIVE_E2E=1",
    )
    def test_retail_vehicle_material_profiles(self) -> None:
        from io_scene_killzone2.import_scene import (
            ImportOptions,
            import_psarc_entity,
        )

        cases = (
            (
                "HGH AAPC",
                (
                    "LocalCachePS3/lumps/"
                    "assets_description.vehicles.hgh_aapc_hgh_aapc.core"
                ),
                564,
                48,
                {
                    "c1ad2d75a5e9d53144d03f7e7f0cd1f0": (
                        "hgh_aapc_layered_deferred_r49"
                    ),
                    "b0dd8ab243f2fca164a71515eae0d632": None,
                    "0890fcb06e287f8411043cff206489a5": None,
                    "b0a8b52eb0560707ecc42dc8c1ec7f88": (
                        "hgh_tank_compact_deferred"
                    ),
                    "a58e7b007d4d88e2ddfaa61af86a4ab7": (
                        "hgh_tank_packed_g49"
                    ),
                    "4e627d3e772982d7744b52997d7652d4": None,
                    "231c838d776735259ccce8f62ac1c3fd": None,
                    "5c879214cf2789ce4728d5de9d9cbc16": (
                        "hgh_tank_packed_r49"
                    ),
                    "9a0fa36f2abfa919579d6395db377b27": (
                        "hgh_aapc_layered_deferred"
                    ),
                    "621754830acce3e2cc48c3d61013012b": None,
                    "c0d90d1ad5eaed9ee58cd8ab7dc82471": (
                        "hgh_aapc_ramp_deferred"
                    ),
                },
            ),
            (
                "ISA Buggy",
                (
                    "LocalCachePS3/lumps/"
                    "assets_description.vehicles.isa_buggy_isa_buggy.core"
                ),
                892,
                72,
                {
                    "6f62459a7e6472f7e324bbfd6f863e9b": (
                        "isa_buggy_compact_deferred"
                    ),
                    "c0c99abb39f9e039272da739390ad8f9": (
                        "isa_buggy_secondary_material_deferred"
                    ),
                    "4e627d3e772982d7744b52997d7652d4": None,
                    "ef63afc5d7b34083a33cd95d15f245e1": (
                        "isa_buggy_layered_deferred"
                    ),
                    "4f122b99db7e4223fd2865254cf1d158": (
                        "isa_buggy_secondary_deferred"
                    ),
                    "84bf4d0f1cc0cfa07c99026d56dbc6a7": None,
                    "d88b4827b93df92d3d55a281ad3f80bd": None,
                    "6ecc1dabafc42f2a93c41a50df1b6687": (
                        "isa_buggy_layered_deferred"
                    ),
                    "68ae7bd3dc772eba0f771647de94cb4f": None,
                    "f8f6ce14f8ba83f5f28a17da586ce44a": (
                        "isa_buggy_ramp_deferred_r49"
                    ),
                    "76ffdeeb808e529cf740647846a61a6a": None,
                    "aab8fd8e71d04ac7493f3ce2ec02acb4": (
                        "isa_buggy_response_ramp_deferred_r49"
                    ),
                    "ed0bf92f2fbcc1902857fbc6aabb9500": None,
                },
            ),
            (
                "ISA Exoskeleton",
                (
                    "LocalCachePS3/lumps/"
                    "assets_description.vehicles.isa_exoskeleton_"
                    "isa_exoskeleton.core"
                ),
                368,
                12,
                {
                    "6663cddd2e0e77f8f5122319895b05de": None,
                    "a61e51472c418e6f30154536683653fd": (
                        "isa_exoskeleton_layered_deferred"
                    ),
                    "7de10c6edd018eeed89312a403ef4f83": (
                        "isa_exoskeleton_response_deferred"
                    ),
                    "a715f1d41d0e6be11eaea6d4071df562": (
                        "isa_exoskeleton_layered_deferred"
                    ),
                },
            ),
            (
                "ISA AAPC",
                (
                    "LocalCachePS3/lumps/"
                    "levels.single_player.corinth_river."
                    "section_02_facility.assets_optimized_"
                    "sharedsharedwp_isaaapc.core"
                ),
                958,
                63,
                {
                    "dba9a590a94521dfe9d6c8bde893b51d": None,
                    "9226fca0d5b4e93ef67ad5ba9c9f4bc0": None,
                    "d8de6701c66b7caece3b70e3555b198b": (
                        "isa_tank_compact_deferred"
                    ),
                    "8735cb04da386a30d617748a6c942f86": (
                        "isa_aapc_ramp_deferred"
                    ),
                    "4e627d3e772982d7744b52997d7652d4": None,
                    "4a209aac9aeef3306dce19e46527d0fa": (
                        "isa_aapc_layered_deferred"
                    ),
                    "b87a2ff0d9c2aad4cdbd231b0cc2b9d6": (
                        "isa_aapc_layered_deferred"
                    ),
                    "152325d374d7231cd503d72edc7e5de7": (
                        "isa_aapc_layered_deferred"
                    ),
                    "1ed8c531fb7f936c69492669042b94f0": (
                        "isa_aapc_layered_deferred"
                    ),
                    "616083d7b97f21c7175a098bbc01b493": (
                        "isa_aapc_ramp_deferred_g49"
                    ),
                    "b70abca563d77e78e524175957fe9d76": None,
                    "406ed5c26dc5c3d5d3ed3fc501004304": (
                        "isa_aapc_compact_masked_deferred"
                    ),
                    "9688112c2f4711397c49debe72fcd216": (
                        "isa_aapc_compact_masked_deferred"
                    ),
                },
            ),
            (
                "HGH ATAC",
                (
                    "LocalCachePS3/lumps/"
                    "levels.single_player.blood_meridian."
                    "section_05_arctower.assets_optimized_"
                    "arctowergpseq_hghatac_hghsentrybot_"
                    "arctowerhi_mainsquareartbsp.core"
                ),
                20,
                6,
                {
                    "f8ad3999502f015448dce15888a17263": (
                        "hgh_atac_deferred"
                    ),
                },
            ),
            (
                "HGH Cruiser",
                (
                    "LocalCachePS3/lumps/"
                    "levels.single_player.the_cruiser.section_outro."
                    "assets_optimized_outrogp_outrohi_isatemplar_"
                    "isaevelyn_hghradec.core"
                ),
                683,
                20,
                {
                    "41fbce422ba9b57233d82e90965cc6d7": None,
                    "7511eed9983ae59c434a301c52d28ccf": (
                        "hgh_cruiser_compact_deferred"
                    ),
                    "cfb995ef1c6f06bb97384e211a4d4b81": (
                        "hgh_cruiser_deferred_g12_25"
                    ),
                    "def4c8fbe2b0ff5e5f00548cbb4ef0ea": (
                        "hgh_cruiser_compact_deferred"
                    ),
                    "1717d873cb90d6a8fc5cc44dac5d7fc8": (
                        "hgh_cruiser_layered_deferred"
                    ),
                },
            ),
            (
                "ISA Cruiser",
                (
                    "LocalCachePS3/lumps/"
                    "levels.single_player.corinth_river."
                    "section_thenewsun_mirror.assets_optimized_"
                    "isabridgecrew_isabridgecrew_gpshared_"
                    "artlaunchbayhigh_thenewsun_cutscenesnsoutra.core"
                ),
                603,
                16,
                {
                    "f1ef6049dec3cd24a903e307d77f0be4": (
                        "isa_cruiser_layered_deferred"
                    ),
                    "c6dcc542d690fc4761ab407049ad4873": (
                        "isa_cruiser_layered_deferred"
                    ),
                    "c4f7e7bcb20c3fc6c54b2e469dcb264b": (
                        "isa_cruiser_masked_deferred"
                    ),
                    "5b0789b6e9abb31163bdbe55a8111d99": (
                        "isa_cruiser_layered_deferred"
                    ),
                },
            ),
        )
        archive_path = str(Path(os.environ["KZ2_PSARC"]).resolve())
        for (
            vehicle,
            core_path,
            entity_id,
            expected_meshes,
            expected_profiles,
        ) in cases:
            with self.subTest(vehicle=vehicle):
                bpy.ops.wm.read_factory_settings(use_empty=True)
                result = import_psarc_entity(
                    bpy.context,
                    archive_path,
                    core_path,
                    entity_id,
                    ImportOptions(
                        source_psarc=archive_path,
                        resource_index_path=os.environ.get(
                            "KZ2_RESOURCE_INDEX",
                            "",
                        ),
                        import_materials=True,
                        import_animations=False,
                        import_fake_shadow_meshes=True,
                        flip_uv_v=True,
                    ),
                )

                self.assertEqual(expected_meshes, result.meshes)
                self.assert_vehicle_material_profiles(
                    expected_profiles
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
    def test_hgh_fighter_materials_debris_and_model_proxy(self) -> None:
        from io_scene_killzone2.import_scene import (
            ImportOptions,
            import_psarc_entity,
        )

        archive_path = str(Path(os.environ["KZ2_PSARC"]).resolve())
        core_path = (
            "LocalCachePS3/lumps/"
            "levels.single_player.the_cruiser.section_landingbay."
            "assets_optimized_landingbaygpst_landingbayst_hghfighter.core"
        )
        options = ImportOptions(
            source_psarc=archive_path,
            resource_index_path=os.environ.get(
                "KZ2_RESOURCE_INDEX",
                "",
            ),
            import_materials=True,
            import_animations=False,
        )

        result = import_psarc_entity(
            bpy.context,
            archive_path,
            core_path,
            1945,
            options,
        )

        self.assertEqual(4, result.meshes)
        self.assertEqual(4, result.entity_instances)
        self.assertEqual(2, result.materials)
        self.assertEqual([], result.warnings)
        root = next(
            obj
            for obj in bpy.data.objects
            if obj.get("kz2_cached_entity")
            and obj.get("kz2_entity_resource_name") == "HGH_Fighter"
        )
        self.assertEqual(
            3,
            root["kz2_staged_destruction_debris_count"],
        )
        debris_collection = next(
            collection
            for collection in bpy.data.collections
            if collection.get("kz2_staged_destruction_debris")
        )
        self.assertTrue(debris_collection.hide_viewport)
        self.assertTrue(debris_collection.hide_render)
        self.assertEqual(
            (
                "0.5: HGH_Fighter_Left_Wing, HGH_Fighter_Nose, "
                "HGH_Fighter_Right_Wing; 0.25: HGH_Fighter_Left_Wing, "
                "HGH_Fighter_Nose; 0.25: HGH_Fighter_Left_Wing, "
                "HGH_Fighter_Right_Wing"
            ),
            debris_collection["kz2_creation_branches"],
        )
        debris = {
            obj["kz2_entity_resource_name"]: obj
            for obj in debris_collection.objects
            if obj.get("kz2_staged_destruction_debris")
        }
        self.assertEqual(
            {
                "HGH_Fighter_Left_Wing",
                "HGH_Fighter_Nose",
                "HGH_Fighter_Right_Wing",
            },
            debris.keys(),
        )
        expected_debris = {
            "HGH_Fighter_Left_Wing": (
                (-3.24705, -2.29214, 0.739711),
                1.0,
            ),
            "HGH_Fighter_Nose": (
                (0.0, 3.22095, 1.84452),
                0.75,
            ),
            "HGH_Fighter_Right_Wing": (
                (3.24705, -2.29214, 0.739711),
                0.75,
            ),
        }
        for name, (translation, probability) in expected_debris.items():
            with self.subTest(debris=name):
                placement = debris[name]
                for observed, expected in zip(
                    placement.matrix_basis.translation,
                    translation,
                    strict=True,
                ):
                    self.assertAlmostEqual(expected, observed, places=5)
                self.assertAlmostEqual(
                    probability,
                    placement["kz2_spawn_probability"],
                )
                self.assertEqual(
                    1,
                    len(
                        [
                            obj
                            for obj in debris_collection.all_objects
                            if obj.type == "MESH"
                            and obj.parent == placement
                        ]
                    ),
                )

        materials = {
            material["kz2_profile_id"]: material
            for material in bpy.data.materials
            if material.get("kz2_profile_id")
            in {"hgh_fighter_skinned", "hgh_fighter_static"}
        }
        self.assertEqual(
            {"hgh_fighter_skinned", "hgh_fighter_static"},
            materials.keys(),
        )
        for profile_id, material in materials.items():
            with self.subTest(profile_id=profile_id):
                self.assertEqual(
                    "G",
                    material["kz2_profile_specular_power_channel"],
                )
                self.assertEqual(
                    24.5,
                    material["kz2_profile_specular_power_scale"],
                )
                self.assertEqual(
                    "B",
                    material["kz2_profile_specular_intensity_channel"],
                )
                principled = next(
                    node
                    for node in material.node_tree.nodes
                    if node.bl_idname == "ShaderNodeBsdfPrincipled"
                )
                self.assertEqual(
                    "KZ2_Roughness",
                    principled.inputs["Roughness"].links[0].from_node.name,
                )
                self.assertEqual(
                    "Non-Color",
                    material.node_tree.nodes[
                        "KZ2_01_inSampler1"
                    ].image.colorspace_settings.name,
                )

        skinned = materials["hgh_fighter_skinned"]
        principled = next(
            node
            for node in skinned.node_tree.nodes
            if node.bl_idname == "ShaderNodeBsdfPrincipled"
        )
        self.assertEqual(
            "KZ2_VertexEmissionSquared",
            principled.inputs["Emission Color"].links[0].from_node.name,
        )
        self.assertEqual(
            "RGB squared",
            skinned["kz2_preview_vertex_emission"],
        )
        fighter_mesh = next(
            obj.data
            for obj in bpy.data.objects
            if obj.type == "MESH"
            and obj.data.materials
            and obj.data.materials[0] == skinned
        )
        self.assertIsNotNone(fighter_mesh.color_attributes.get("KZ2Color"))

        turret_result = import_psarc_entity(
            bpy.context,
            archive_path,
            core_path,
            1933,
            options,
        )
        self.assertEqual(0, turret_result.meshes)
        self.assertEqual([], turret_result.warnings)
        turret = next(
            obj
            for obj in bpy.data.objects
            if obj.get("kz2_cached_entity")
            and obj.get("kz2_entity_resource_name")
            == "HGH_Fighter_MG_Turret"
        )
        self.assertTrue(turret["kz2_model_proxy"])
        self.assertEqual(
            "HGH_Fighter_Model",
            turret["kz2_model_proxy_target_name"],
        )
        self.assertEqual(
            (
                "Aim Heading -> Turret Aim Heading; "
                "Aim Pitch -> Turret Aim Pitch"
            ),
            turret["kz2_model_proxy_parameter_mappings"],
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

        result = import_psarc_entity(
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
                import_materials=True,
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
        expected_shader_ids = {
            "b80f807760feca99a0e4c53ba91a6475",
            "c1b648b75e0293d6900410fb35a360f2",
            "42b632e84a4076e078327f99496fb80b",
            "26fc5ce3f03d759bc972fc0298994324",
        }
        materials = {
            material["kz2_shader_object_id"]: material
            for obj in imported_objects
            if obj.type == "MESH"
            for material in obj.data.materials
            if material is not None
            and material.get("kz2_shader_object_id") in expected_shader_ids
        }
        self.assertEqual(4, result.materials)
        self.assertEqual(expected_shader_ids, materials.keys())
        for shader_object_id, material in materials.items():
            with self.subTest(shader_object_id=shader_object_id):
                self.assertEqual(
                    "character_standard_deferred",
                    material["kz2_profile_id"],
                )
                self.assertEqual("proven", material["kz2_profile_status"])
                self.assertEqual(
                    "inSampler3",
                    material["kz2_profile_normal_sampler"],
                )
                texture_nodes = {
                    node.get("kz2_sampler"): node
                    for node in material.node_tree.nodes
                    if node.bl_idname == "ShaderNodeTexImage"
                }
                self.assertEqual(
                    "Non-Color",
                    texture_nodes["inSampler1"].image.colorspace_settings.name,
                )
                self.assertEqual(
                    "Non-Color",
                    texture_nodes["inSampler2"].image.colorspace_settings.name,
                )
                principled = next(
                    node
                    for node in material.node_tree.nodes
                    if node.bl_idname == "ShaderNodeBsdfPrincipled"
                )
                self.assertEqual(
                    "KZ2_NormalMap",
                    principled.inputs["Normal"].links[0].from_node.name,
                )
                self.assertFalse(principled.inputs["Roughness"].is_linked)


if __name__ == "__main__":
    unittest.main(argv=[__file__])
