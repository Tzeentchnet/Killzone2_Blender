from __future__ import annotations

import math
import os
import unittest

from io_scene_killzone2.kz2_assets.animation_graph import (
    animation_state_clip_names,
)
from io_scene_killzone2.kz2_assets.core import CoreFile
from io_scene_killzone2.kz2_assets.decoder import CoreDecoder
from io_scene_killzone2.kz2_assets.edge_animation import (
    read_skeleton_animation_bone_map_reference,
)
from io_scene_killzone2.kz2_assets.errors import FormatError
from io_scene_killzone2.kz2_assets.level import (
    EntityPlaceHolder,
    GameScript,
    StaticMeshInstance,
)
from io_scene_killzone2.kz2_assets.level_catalog import (
    LEVEL_KIND_CAMPAIGN,
    LEVEL_KIND_MULTIPLAYER,
    scan_level_catalog,
)
from io_scene_killzone2.kz2_assets.model import (
    BoneBindings,
    DestructibilityPartState,
    EntityResource,
    LodMesh,
    ModelPart,
    ModelResource,
    RenderEffect,
    ShaderResource,
    Skeleton,
    SkeletonAnimation,
    SkeletonAnimationBoneMap,
    SkinInfo,
    SkinnedMesh,
    StateBasedDestructibility,
    StaticMesh,
    Texture,
    VertexArray,
)
from io_scene_killzone2.kz2_assets.psarc import Psarc

PSARC_PATH = os.environ.get("KZ2_PSARC")


@unittest.skipUnless(PSARC_PATH, "KZ2_PSARC is not configured")
class LocalArchiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.archive = Psarc.open(PSARC_PATH)

    def test_supported_revision_inventory(self) -> None:
        self.assertEqual(3951, len(self.archive.entries))
        self.assertEqual(
            2051,
            sum(
                1
                for entry in self.archive.entries
                if entry.path and entry.path.lower().endswith(".core")
            ),
        )

    def test_campaign_and_multiplayer_level_catalog(self) -> None:
        levels = scan_level_catalog(self.archive)

        self.assertEqual(18, len(levels))
        self.assertEqual(
            10,
            sum(level.kind == LEVEL_KIND_CAMPAIGN for level in levels),
        )
        self.assertEqual(
            8,
            sum(level.kind == LEVEL_KIND_MULTIPLAYER for level in levels),
        )
        corinth = next(
            level for level in levels if level.key == "campaign:corinth_river"
        )
        self.assertEqual(9, len(corinth.sections))
        self.assertIn(
            (
                "LocalCachePS3/lumps/levels.single_player.corinth_river."
                "section_thenewsun.cutscenes.assets_optimized_skybox.core"
            ),
            {
                path
                for section in corinth.sections
                for path in section.core_paths
            },
        )
        self.assertIn(
            (
                "LocalCachePS3/lumps/levels.single_player.corinth_river."
                "section_shared.section_dam_shared_shared.core"
            ),
            {
                path
                for section in corinth.sections
                for path in section.core_paths
            },
        )
        self.assertEqual(
            "Corinth Crossing (MP 01)",
            next(
                level
                for level in levels
                if level.key == "multiplayer:mp_level_01"
            ).display_name,
        )

    def test_static_mesh_sample(self) -> None:
        path = "LocalCachePS3/lumps/assets_description.loading_loading_assets.core"
        core = CoreFile.from_bytes(self.archive.read_entry(path), source=path)
        decoder = CoreDecoder(core)
        meshes = list(decoder.iter_type("StaticMeshResource"))
        arrays = list(decoder.iter_type("VertexArrayResource"))
        self.assertEqual(2, len(meshes))
        self.assertTrue(all(isinstance(value, StaticMesh) for _, value in meshes))
        self.assertEqual(1, len(arrays))
        self.assertIsInstance(arrays[0][1], VertexArray)
        self.assertEqual(8, arrays[0][1].vertex_count)

    def test_skeleton_sample(self) -> None:
        path = (
            "LocalCachePS3/lumps/English/"
            "assets_description.svo.svomainmenu_svomainmenu_speech.core"
        )
        core = CoreFile.from_bytes(self.archive.read_entry(path), source=path)
        decoder = CoreDecoder(core)
        skeletons = list(decoder.iter_type("Skeleton"))
        self.assertEqual(1, len(skeletons))
        self.assertIsInstance(skeletons[0][1], Skeleton)
        skeleton = skeletons[0][1]
        self.assertEqual(91, len(skeleton.joints))
        self.assertEqual(91, len(skeleton.edge_joints))
        self.assertEqual((0.0, 0.0, 0.0, 1.0), skeleton.edge_joints[2].rotation_xyzw)
        self.assertEqual((0.0, 0.0, 0.0), skeleton.edge_joints[2].translation)
        self.assertEqual((1.0, 1.0, 1.0), skeleton.edge_joints[2].scale)
        self.assertEqual(1, skeleton.edge_joints[2].parent_index)
        animations = list(decoder.iter_type("SkeletonAnimationResource"))
        self.assertEqual(1, len(animations))
        self.assertIsInstance(animations[0][1], SkeletonAnimation)
        clip = animations[0][1].clip
        self.assertEqual(61, clip.frame_count)
        self.assertTrue(clip.decode_supported)
        self.assertEqual((51, 76, 0, 3, 1, 0, 0, 16), clip.counts)
        first_samples = clip.sample_frame(0)
        self.assertIsNotNone(first_samples[2].rotation_xyzw)
        self.assertIsNone(first_samples[3].rotation_xyzw)
        self.assertAlmostEqual(
            1.0,
            abs(
                sum(
                    left * right
                    for left, right in zip(
                        first_samples[2].rotation_xyzw,
                        skeleton.joints[3].rotation_xyzw,
                        strict=True,
                    )
                )
            ),
            places=5,
        )
        for frame_index in (0, 30, 60):
            samples = clip.sample_frame(frame_index)
            self.assertEqual(91, len(samples))
            for sample in samples:
                if sample.rotation_xyzw is not None:
                    self.assertTrue(
                        all(math.isfinite(value) for value in sample.rotation_xyzw)
                    )
                    self.assertAlmostEqual(
                        1.0,
                        sum(value * value for value in sample.rotation_xyzw),
                        places=5,
                    )
                if sample.translation is not None:
                    self.assertTrue(
                        all(math.isfinite(value) for value in sample.translation)
                    )

    def test_dropship_animation_graph(self) -> None:
        path = (
            "LocalCachePS3/lumps/"
            "assets_description.vehicles.hgh_dropship_hgh_dropship.core"
        )
        core = CoreFile.from_bytes(
            self.archive.read_entry(path),
            source=path,
        )
        decoder = CoreDecoder(core)
        skeleton = decoder.decode(342)
        body_bone_map = decoder.decode(254)

        self.assertIsInstance(skeleton, Skeleton)
        self.assertIsInstance(
            body_bone_map,
            SkeletonAnimationBoneMap,
        )
        self.assertEqual(
            tuple(joint.name for joint in skeleton.joints),
            body_bone_map.bone_names,
        )
        self.assertEqual(
            {
                328: ("Default",),
                329: ("Default",),
                330: ("Hover",),
                331: ("Neutral",),
                332: ("Approach",),
                333: ("Default",),
                334: ("Fly Away",),
            },
            animation_state_clip_names(decoder),
        )

    def test_tank_animation_header_preflight(self) -> None:
        path = (
            "LocalCachePS3/lumps/"
            "levels.single_player.tharsis_refinery.section_train."
            "assets_optimized_traingp_trainhi_hghtanktrtrain_"
            "hghcommando_hghsupporttrooper.core"
        )
        core = CoreFile.from_bytes(
            self.archive.read_entry(path),
            source=path,
        )
        decoder = CoreDecoder(core)

        unrelated = read_skeleton_animation_bone_map_reference(
            decoder,
            core.objects[8859],
        )
        tank_clip = read_skeleton_animation_bone_map_reference(
            decoder,
            core.objects[8868],
        )
        self.assertEqual(7422, unrelated.local_index)
        self.assertEqual(7420, tank_clip.local_index)
        self.assertEqual(
            78,
            len(decoder.decode(unrelated.local_index).bone_names),
        )
        self.assertEqual(
            131,
            len(decoder.decode(tank_clip.local_index).bone_names),
        )
        with self.assertRaisesRegex(
            FormatError,
            "header size does not match",
        ):
            decoder.decode(8859)
        self.assertIsInstance(
            decoder.decode(8868),
            SkeletonAnimation,
        )

    def test_skinned_character_sample(self) -> None:
        path = (
            "LocalCachePS3/lumps/"
            "assets_description.characters.isa_dog_soldier_shared_"
            "isa_dog_soldier_shared.core"
        )
        core = CoreFile.from_bytes(self.archive.read_entry(path), source=path)
        decoder = CoreDecoder(core)
        meshes = list(decoder.iter_type("RegularSkinnedMeshResource"))
        self.assertEqual(12, len(meshes))
        self.assertTrue(all(isinstance(value, SkinnedMesh) for _, value in meshes))
        first = meshes[0][1]
        skin_info = decoder.resolve(first.skin_info)
        bindings = decoder.resolve(first.bone_bindings)
        self.assertIsInstance(skin_info, SkinInfo)
        self.assertEqual(3, len(skin_info.parts))
        self.assertEqual((2139, 1068, 955), tuple(len(part.vertices) for part in skin_info.parts))
        self.assertIsInstance(bindings, BoneBindings)
        self.assertEqual(91, len(bindings.bone_names))
        textures = list(decoder.iter_type("Texture"))
        effects = list(decoder.iter_type("RenderEffectResource"))
        shaders = list(decoder.iter_type("ShaderResource"))
        self.assertEqual(10, len(textures))
        self.assertTrue(all(isinstance(value, Texture) for _, value in textures))
        self.assertEqual(3, len(effects))
        self.assertTrue(
            all(isinstance(value, RenderEffect) for _, value in effects)
        )
        self.assertEqual((1, 1, 2), tuple(len(value.techniques) for _, value in effects))
        self.assertEqual(4, len(shaders))
        self.assertTrue(
            all(isinstance(value, ShaderResource) for _, value in shaders)
        )

    def test_level_direct_framing_and_lod_placements(self) -> None:
        path = (
            "LocalCachePS3/lumps/levels.single_player.blood_meridian."
            "section_01_mainsquare.assets_optimized_art_superlow.core"
        )
        core = CoreFile.from_bytes(self.archive.read_entry(path), source=path)
        decoder = CoreDecoder(core)
        instances = list(decoder.iter_type("StaticMeshInstance"))
        lod_meshes = list(decoder.iter_type("LodMeshResource"))
        static_meshes = list(decoder.iter_type("StaticMeshResource"))
        self.assertEqual(0, core.trailing_bytes)
        self.assertEqual(40, len(instances))
        self.assertTrue(
            all(isinstance(value, StaticMeshInstance) for _, value in instances)
        )
        self.assertEqual(39, len(lod_meshes))
        self.assertTrue(all(isinstance(value, LodMesh) for _, value in lod_meshes))
        self.assertEqual(40, len(static_meshes))
        self.assertTrue(
            all(isinstance(value, StaticMesh) for _, value in static_meshes)
        )
        translations = tuple(
            value.local_translate_scale[:3] for _, value in static_meshes
        )
        self.assertGreater(max(value[0] for value in translations), 250.0)
        self.assertLess(min(value[1] for value in translations), -100.0)
        self.assertTrue(
            all(value.local_translate_scale[3] > 0.0 for _, value in static_meshes)
        )
        self.assertEqual(
            {1},
            {len(value.parts) for _, value in lod_meshes},
        )
        self.assertTrue(
            all(
                value.static_model.local_index is not None
                for _, value in instances
            )
        )

    def test_level_game_script_recursive_framing(self) -> None:
        path = (
            "LocalCachePS3/lumps/"
            "Levels.Single_Player.Optimized.Blood_Meridian_Optimized.core"
        )
        core = CoreFile.from_bytes(self.archive.read_entry(path), source=path)
        scripts = [
            obj.direct_value
            for obj in core.objects
            if isinstance(obj.direct_value, GameScript)
        ]
        self.assertEqual(0, core.trailing_bytes)
        self.assertEqual(1, len(scripts))
        self.assertEqual(bytes.fromhex("1b4c75615100000404040400"), scripts[0].lua_header)
        self.assertGreater(len(scripts[0].root.nested), 0)

    def test_reflection_room_entity_placement_graph(self) -> None:
        path = (
            "LocalCachePS3/lumps/levels.single_player.the_new_sun."
            "section_01.assets_optimized_art_reflection_room.core"
        )
        core = CoreFile.from_bytes(self.archive.read_entry(path), source=path)
        decoder = CoreDecoder(core)
        placeholders = list(decoder.iter_type("EntityPlaceHolder"))
        entities = list(decoder.iter_type("EntityResource"))
        static_models = list(decoder.iter_type("StaticModelResource"))
        skinned_models = list(decoder.iter_type("SkinnedModelResource"))
        model_parts = list(decoder.iter_type("ModelPartResource"))
        part_states = list(
            decoder.iter_type("DestructibilityPartStateResource")
        )
        destructibilities = list(
            decoder.iter_type("StateBasedDestructibilityResource")
        )

        self.assertEqual(20, len(placeholders))
        self.assertTrue(
            all(
                isinstance(value, EntityPlaceHolder)
                for _, value in placeholders
            )
        )
        self.assertEqual(12, len(entities))
        self.assertTrue(
            all(isinstance(value, EntityResource) for _, value in entities)
        )
        self.assertEqual(11, len(static_models))
        self.assertTrue(
            all(
                isinstance(value, ModelResource)
                for _, value in static_models
            )
        )
        self.assertEqual(1, len(skinned_models))
        self.assertIsInstance(skinned_models[0][1], ModelResource)
        self.assertEqual(7, len(model_parts))
        self.assertTrue(
            all(isinstance(value, ModelPart) for _, value in model_parts)
        )
        self.assertEqual(7, len(part_states))
        self.assertTrue(
            all(
                isinstance(value, DestructibilityPartState)
                for _, value in part_states
            )
        )
        self.assertEqual(
            (6, 1),
            tuple(
                len(value.part_states)
                for _, value in destructibilities
                if isinstance(value, StateBasedDestructibility)
            ),
        )

    def test_salamun_aapc_wreck_blade_destructibility(self) -> None:
        path = (
            "LocalCachePS3/lumps/levels.single_player.salamun_district."
            "section_bridge.assets_optimized_bridgesdintr_bridgehi_"
            "isam1lmgtp_rico_natko_narville_hghradec_isainf_hghatac_isa.core"
        )
        core = CoreFile.from_bytes(self.archive.read_entry(path), source=path)

        destructibility = CoreDecoder(core).decode(3501)

        self.assertIsInstance(destructibility, StateBasedDestructibility)
        self.assertTrue(destructibility.part_states_complete)
        self.assertEqual(
            (7485,),
            tuple(
                reference.local_index
                for reference in destructibility.part_states
            ),
        )

    def test_empty_edge_skeleton_table_at_blob_end(self) -> None:
        path = (
            "LocalCachePS3/lumps/assets_description.vehicles."
            "isa_intruder_baseassets_isa_intruder_baseassets.core"
        )
        core = CoreFile.from_bytes(self.archive.read_entry(path), source=path)
        decoder = CoreDecoder(core)
        skeletons = list(decoder.iter_type("Skeleton"))
        self.assertEqual(2, len(skeletons))
        self.assertTrue(all(isinstance(value, Skeleton) for _, value in skeletons))
        self.assertEqual((40, 2), tuple(len(value.joints) for _, value in skeletons))
        self.assertEqual(
            (40, 2),
            tuple(len(value.edge_joints) for _, value in skeletons),
        )

    def test_animation_wrapper_reference_variants(self) -> None:
        local_path = "LocalCachePS3/lumps/English/aigeneratedgarza.core"
        local_core = CoreFile.from_bytes(
            self.archive.read_entry(local_path),
            source=local_path,
        )
        local_animation = next(
            CoreDecoder(local_core).iter_type("SkeletonAnimationResource")
        )[1]
        self.assertIsInstance(local_animation, SkeletonAnimation)
        self.assertEqual(2, local_animation.wrapper_version)
        self.assertEqual(19, local_animation.skeleton_reference.local_index)
        self.assertTrue(local_animation.clip.decode_supported)

        external_path = (
            "LocalCachePS3/lumps/assets_description.characters."
            "hgh_assault_infantry_hgh_assault_infantry_character.core"
        )
        external_core = CoreFile.from_bytes(
            self.archive.read_entry(external_path),
            source=external_path,
        )
        external_animation = next(
            CoreDecoder(external_core).iter_type("SkeletonAnimationResource")
        )[1]
        self.assertIsInstance(external_animation, SkeletonAnimation)
        self.assertIsNotNone(external_animation.skeleton_reference.external_id)
        self.assertTrue(external_animation.clip.decode_supported)


if __name__ == "__main__":
    unittest.main()
