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
    bpy is not None
    and os.environ.get("KZ2_PSARC")
    and os.environ.get("KZ2_RESOURCE_INDEX"),
    "requires Blender, KZ2_PSARC, and KZ2_RESOURCE_INDEX",
)
class BlenderLevelImportTests(unittest.TestCase):
    def test_imports_campaign_section_from_psarc(self) -> None:
        from io_scene_killzone2.import_scene import (
            ImportOptions,
            import_psarc_level,
        )
        from io_scene_killzone2.kz2_assets.level_catalog import (
            scan_level_catalog,
        )

        archive_path = str(Path(os.environ["KZ2_PSARC"]).resolve())
        levels = scan_level_catalog(archive_path)
        level = next(
            value
            for value in levels
            if value.key == "campaign:corinth_river"
        )
        section_index = next(
            index
            for index, section in enumerate(level.sections)
            if section.display_name == "07 Tunnel"
        )

        result = import_psarc_level(
            bpy.context,
            archive_path,
            level,
            section_index,
            ImportOptions(
                source_psarc=archive_path,
                resource_index_path=os.environ["KZ2_RESOURCE_INDEX"],
                import_materials=False,
                import_level_entities=False,
            ),
        )

        self.assertEqual(252, result.level_instances)
        self.assertEqual(0, result.entity_instances)
        self.assertGreater(result.meshes, 0)
        collection = next(
            value
            for value in bpy.data.collections
            if value.get("kz2_level_key") == "campaign:corinth_river"
        )
        self.assertEqual(
            "LocalCachePS3/lumps/Levels.Single_Player.Optimized."
            "Corinth_River_Optimized.core",
            collection["kz2_level_root"],
        )
        self.assertIn("07 Tunnel", collection.children)

    def test_imports_dedicated_skybox_geometry(self) -> None:
        from io_scene_killzone2.import_scene import (
            ImportOptions,
            import_psarc_level,
        )
        from io_scene_killzone2.kz2_assets.level_catalog import (
            LEVEL_KIND_CAMPAIGN,
            CachedLevel,
            CachedLevelSection,
        )

        archive_path = str(Path(os.environ["KZ2_PSARC"]).resolve())
        skybox_path = (
            "LocalCachePS3/lumps/levels.single_player.corinth_river."
            "section_thenewsun.cutscenes.assets_optimized_skybox.core"
        )
        level = CachedLevel(
            key="campaign:corinth_river_skybox_test",
            kind=LEVEL_KIND_CAMPAIGN,
            display_name="Corinth River Skybox Test",
            root_path=skybox_path,
            sections=(
                CachedLevelSection(
                    key="skybox",
                    display_name="Skybox",
                    core_paths=(skybox_path,),
                ),
            ),
        )

        result = import_psarc_level(
            bpy.context,
            archive_path,
            level,
            0,
            ImportOptions(
                source_psarc=archive_path,
                resource_index_path=os.environ["KZ2_RESOURCE_INDEX"],
            ),
        )

        self.assertEqual(217, result.level_instances)
        self.assertEqual(50, result.entity_instances)
        self.assertGreater(result.meshes, 0)
        self.assertGreater(result.materials, 0)
        self.assertGreater(result.images, 0)
        ship_materials = {
            "KZ2_b84dd80684c0_T00": (
                "isa_cruiser_masked_deferred",
                "DiffuseUV",
                "ea36ad5ebc9c883dc442af553dc533cf",
            ),
            "KZ2_f4420075d2bb_T00": (
                "isa_cruiser_layered_deferred",
                "DiffuseUV",
                "08c0617c2127c68dd66f08122aa60578",
            ),
            "KZ2_deb08015ce36_T00": (
                "isa_cruiser_layered_deferred",
                "DiffuseUV",
                "9b7294a2ef4914c199d3591d63d50bc0",
            ),
            "KZ2_b932de375550_T00": (
                "isa_cruiser_layered_deferred",
                "DiffuseUV",
                "ea36ad5ebc9c883dc442af553dc533cf",
            ),
        }
        for material_name, (
            profile_id,
            uv_map,
            texture_id,
        ) in ship_materials.items():
            with self.subTest(material=material_name):
                material = bpy.data.materials.get(material_name)
                self.assertIsNotNone(material)
                self.assertEqual(profile_id, material["kz2_profile_id"])
                self.assertEqual("proven", material["kz2_profile_status"])
                self.assertEqual(
                    "inSampler1",
                    material["kz2_preview_base_color"],
                )
                self.assertEqual(
                    "shader_profile",
                    material["kz2_preview_base_color_source"],
                )
                self.assertEqual(uv_map, material["kz2_preview_uv_map"])
                self.assertTrue(material["kz2_preview_diffuse_v_flip"])
                principled = next(
                    node
                    for node in material.node_tree.nodes
                    if node.bl_idname == "ShaderNodeBsdfPrincipled"
                )
                base_color = principled.inputs["Base Color"]
                self.assertTrue(base_color.is_linked)
                texture_node = base_color.links[0].from_node
                self.assertEqual("inSampler1", texture_node["kz2_sampler"])
                self.assertEqual(
                    texture_id,
                    texture_node.image["kz2_object_id"],
                )
                self.assertEqual(
                    texture_node,
                    material.node_tree.nodes.active,
                )
                mapping = texture_node.inputs["Vector"].links[0].from_node
                self.assertEqual("KZ2_DiffuseUVTransform", mapping.name)
                uv_node = mapping.inputs["Vector"].links[0].from_node
                self.assertEqual(
                    uv_map,
                    uv_node.uv_map,
                )

    def test_imports_multiplayer_map_from_psarc(self) -> None:
        from io_scene_killzone2.import_scene import (
            ImportOptions,
            import_psarc_level,
        )
        from io_scene_killzone2.kz2_assets.level_catalog import (
            scan_level_catalog,
        )

        archive_path = str(Path(os.environ["KZ2_PSARC"]).resolve())
        level = next(
            value
            for value in scan_level_catalog(archive_path)
            if value.key == "multiplayer:mp_level_07"
        )

        result = import_psarc_level(
            bpy.context,
            archive_path,
            level,
            0,
            ImportOptions(
                source_psarc=archive_path,
                resource_index_path=os.environ["KZ2_RESOURCE_INDEX"],
                import_materials=False,
                import_level_entities=False,
            ),
        )

        self.assertEqual(295, result.level_instances)
        self.assertEqual(0, result.entity_instances)
        self.assertGreater(result.meshes, 0)


if __name__ == "__main__":
    unittest.main(argv=[__file__])
