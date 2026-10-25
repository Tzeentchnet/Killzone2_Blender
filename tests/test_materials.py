from __future__ import annotations

import struct
import unittest

from io_scene_killzone2.kz2_assets.core import CORE_SIGNATURE, CoreFile
from io_scene_killzone2.kz2_assets.decoder import CoreDecoder
from io_scene_killzone2.kz2_assets.materials import (
    blender_lightmap_uv_mapping,
    blender_roughness_from_specular_power,
    decode_packed_lightmap_sample,
    shader_preview_profile,
)
from io_scene_killzone2.kz2_assets.model import RenderEffect, ShaderResource


def compact(value: int) -> bytes:
    if -128 <= value <= 127 and value not in (-128, -127):
        return struct.pack(">b", value)
    if -32768 <= value <= 32767:
        return b"\x81" + struct.pack(">h", value)
    return b"\x80" + struct.pack(">i", value)


def typed_string(value: str) -> bytes:
    encoded = value.encode("utf-8")
    return compact(len(encoded) + 1) + bytes((len(encoded),)) + encoded


def synthetic_core(
    objects: tuple[tuple[int, bytes], ...],
    strings: tuple[str, ...],
) -> bytes:
    data = bytearray(CORE_SIGNATURE)
    data.append(1)
    data.extend(bytes(16))
    types = (b"ShaderResource", b"RenderEffectResource", b"String")
    data.extend(compact(len(types)))
    for type_name in types:
        data.extend(compact(len(type_name)))
        data.extend(type_name)
    data.extend(compact(len(objects)))
    data.extend(type_index for type_index, _ in objects)
    for index, (_, payload) in enumerate(objects):
        data.extend(index.to_bytes(16, "big"))
        data.extend(struct.pack(">I", len(payload)))
        data.extend(bytes(12))
    data.extend(compact(0))
    data.extend(compact(1))
    data.append(2)
    data.extend(compact(len(strings)))
    for value in strings:
        data.extend(typed_string(value))
    data.extend(compact(0))
    data.extend(compact(0))
    for _, payload in objects:
        data.extend(compact(0))
        data.extend(compact(0))
        data.extend(payload)
    return bytes(data)


class MaterialTests(unittest.TestCase):
    def test_isa_cruiser_profiles_select_verified_albedo(self) -> None:
        layered_samplers = (
            "inSampler5",
            "inSampler6",
            "inSampler0",
            "inSampler1",
            "inSampler2",
            "inSampler3",
            "inSampler4",
        )
        variants = (
            (
                "isa_cruiser_masked_deferred",
                "4162c929f17afe2645b68ca16e44d1f875e1b23ff54fbdb8ceeb1b9e18dae24a",
                "c4f7e7bcb20c3fc6c54b2e469dcb264b",
                ("inSampler0", "inSampler1"),
                (),
                "DiffuseUV",
                None,
                None,
            ),
            (
                "isa_cruiser_layered_deferred",
                "d0bf1912b3998059fc02279daae5007291ba904d5e86265cb59b934e475898e6",
                "1519aa1c31d97763f309fff78341ad0a",
                layered_samplers,
                ("inVariable0",),
                "DiffuseUV",
                "inSampler5",
                "inSampler3",
            ),
            (
                "isa_cruiser_layered_deferred",
                "52ff04908ef8c4e30efb9d463aeb4b7e16a28afd658173e9da4c2614d13bd134",
                "377c0e533f5a2c46c9ecafca3a6f8709",
                layered_samplers[:2]
                + ("inSampler1", "inSampler0")
                + layered_samplers[4:],
                ("inVariable0",),
                "DiffuseUV",
                "inSampler5",
                "inSampler3",
            ),
            (
                "isa_cruiser_layered_deferred",
                "4101f53b769cb64337e2d5fa99b28e666cc8321686fb48704cff13798c4d7eac",
                "37fda7bbada90e7e8522211c4278690d",
                layered_samplers,
                ("inVariable0",),
                "DiffuseUV",
                "inSampler5",
                "inSampler3",
            ),
        )
        for (
            profile_id,
            shader_hash,
            shader_object_id,
            samplers,
            variables,
            uv_map,
            normal_sampler,
            material_sampler,
        ) in variants:
            with self.subTest(shader_object_id=shader_object_id):
                profile = shader_preview_profile(
                    shader_hash,
                    samplers,
                    shader_object_id=shader_object_id,
                    variable_signature=variables,
                )
                self.assertEqual(profile_id, profile.profile_id)
                self.assertEqual("proven", profile.confidence)
                self.assertEqual("inSampler1", profile.base_color_sampler)
                self.assertEqual(uv_map, profile.base_color_uv_map)
                self.assertTrue(profile.flip_diffuse_v)
                self.assertEqual(normal_sampler, profile.normal_sampler)
                self.assertEqual(material_sampler, profile.material_sampler)

        shader_hash = variants[-1][1]
        shader_object_id = variants[-1][2]
        unmatched = (
            shader_preview_profile(
                "0" * 64,
                layered_samplers,
                shader_object_id=shader_object_id,
                variable_signature=("inVariable0",),
            ),
            shader_preview_profile(
                shader_hash,
                layered_samplers[:-2]
                + (layered_samplers[-1], layered_samplers[-2]),
                shader_object_id=shader_object_id,
                variable_signature=("inVariable0",),
            ),
            shader_preview_profile(
                shader_hash,
                layered_samplers,
                shader_object_id=shader_object_id,
                variable_signature=(),
            ),
            shader_preview_profile(
                shader_hash,
                layered_samplers,
                shader_object_id=shader_object_id,
                variable_signature=("inVariable0",),
                technique_index=1,
            ),
        )
        self.assertTrue(all(profile.profile_id is None for profile in unmatched))

    def test_dropship_profile_decodes_specular_channels(self) -> None:
        profile = shader_preview_profile(
            "44cef75c8dee1cdaa570b698db010cc222f46afcd76707fdb22b188d38a0bf60",
            (
                "inSampler3",
                "inSampler0",
                "inSampler1",
                "inSampler2",
            ),
            shader_object_id="c54604143359f6cb1e7766c8fa2a3b38",
            variable_signature=(),
        )

        self.assertEqual("hgh_dropship_deferred", profile.profile_id)
        self.assertEqual("inSampler0", profile.base_color_sampler)
        self.assertEqual("inSampler3", profile.normal_sampler)
        self.assertEqual("inSampler1", profile.material_sampler)
        self.assertEqual("inSampler2", profile.reflection_sampler)
        self.assertEqual("G", profile.specular_power_channel)
        self.assertEqual(98.0, profile.specular_power_scale)
        self.assertEqual("B", profile.specular_intensity_channel)
        self.assertEqual(
            "packed material",
            profile.role_for_sampler("inSampler1"),
        )
        self.assertEqual(
            "IBL lookup",
            profile.role_for_sampler("inSampler2"),
        )

    def test_specular_power_to_blender_roughness(self) -> None:
        self.assertEqual(
            1.0,
            blender_roughness_from_specular_power(
                0.0,
                power_scale=98.0,
            ),
        )
        self.assertAlmostEqual(
            (2.0 / 100.0) ** 0.5,
            blender_roughness_from_specular_power(
                1.0,
                power_scale=98.0,
            ),
        )

    def test_blender_lightmap_uv_mapping(self) -> None:
        values = (0.5, 0.25, 0.125, 0.2)
        self.assertEqual(
            ((0.125, 0.2, 0.0), (0.5, 0.25, 1.0)),
            blender_lightmap_uv_mapping(values, flip_v=False),
        )
        location, scale = blender_lightmap_uv_mapping(values, flip_v=True)
        self.assertEqual((0.125, 0.55, 0.0), location)
        self.assertEqual((0.5, 0.25, 1.0), scale)

    def test_reflection_atlas_shader_profile_flips_diffuse_v(self) -> None:
        profile = shader_preview_profile(
            "300b90c5c33795d92e8feda85c29cbe817fe376ac0e3b0876e022478fbdb1bd4",
            ("inSampler1", "inSampler0", "inSampler_LM"),
        )
        self.assertEqual("inSampler0", profile.base_color_sampler)
        self.assertTrue(profile.flip_diffuse_v)
        self.assertEqual("reflection_room_atlas", profile.profile_id)
        self.assertEqual("proven", profile.confidence)

    def test_linear_reflection_albedo_has_explicit_shader_profile(self) -> None:
        profile = shader_preview_profile(
            "bb5b6b36c051125d587d91fbd50fa8ceaeddb26bdcbb4eed8f4aceeeb9652adc",
            ("inSampler1", "inSampler0", "inSampler_LM", "inSampler2"),
        )
        self.assertEqual("inSampler0", profile.base_color_sampler)
        self.assertFalse(profile.flip_diffuse_v)

    def test_representative_profiles_require_shader_and_signature(self) -> None:
        m82 = shader_preview_profile(
            None,
            ("inSampler0", "inSampler1", "inSampler2"),
            shader_object_id="ce82c6eac4ef761a6c5e553ef8b425ab",
            variable_signature=(),
        )
        self.assertEqual("m82_conventional_deferred", m82.profile_id)
        self.assertEqual("inSampler0", m82.base_color_sampler)
        self.assertEqual("inSampler1", m82.normal_sampler)
        self.assertEqual("tangent", m82.normal_space)
        self.assertIsNone(m82.normal_y_flip)
        self.assertEqual("inSampler2", m82.material_sampler)

        sev = shader_preview_profile(
            None,
            ("inSampler0", "inSampler1", "inSampler2"),
            shader_object_id="91ba5cc96b216995a84b335b093d3cd3",
            variable_signature=(),
        )
        self.assertEqual("sev_mechanical_object_space", sev.profile_id)
        self.assertEqual("inSampler1", sev.base_color_sampler)
        self.assertEqual("object", sev.normal_space)
        self.assertEqual(
            "object normal",
            sev.role_for_sampler("inSampler2"),
        )

        unmatched = shader_preview_profile(
            None,
            ("inSampler0", "inSampler1", "inSampler2"),
            shader_object_id="ce82c6eac4ef761a6c5e553ef8b425ab",
            technique_index=1,
        )
        self.assertIsNone(unmatched.profile_id)
        self.assertEqual("heuristic", unmatched.confidence)

    def test_hair_profile_routes_proven_opacity(self) -> None:
        profile = shader_preview_profile(
            None,
            ("inSampler0", "inSampler1"),
            shader_object_id="671ca18ce63e7acc921d4fe785366e46",
        )
        self.assertEqual("inSampler0", profile.base_color_sampler)
        self.assertEqual("inSampler0", profile.opacity_sampler)
        self.assertEqual(
            "base color + opacity",
            profile.role_for_sampler("inSampler0"),
        )

    def test_packed_lightmap_sample_decode(self) -> None:
        indirect, sun_occlusion = decode_packed_lightmap_sample(
            (0.5, 0.5, 0.25, 0.5)
        )
        self.assertEqual((1.0, 1.0, 1.0), indirect)
        self.assertEqual(0.25, sun_occlusion)

        indirect, sun_occlusion = decode_packed_lightmap_sample(
            (1.0, 0.0, 2.0, 1.0)
        )
        self.assertEqual((4.0, 4.0, 2.25), indirect)
        self.assertEqual(1.0, sun_occlusion)

    def test_shader_profile_requires_exact_hash_and_sampler_signature(
        self,
    ) -> None:
        shader_hash = (
            "300b90c5c33795d92e8feda85c29cbe817fe376ac0e3b0876e022478fbdb1bd4"
        )
        self.assertFalse(
            shader_preview_profile(
                shader_hash,
                ("inSampler0", "inSampler1", "inSampler_LM"),
            ).flip_diffuse_v
        )
        self.assertFalse(
            shader_preview_profile(None, ()).flip_diffuse_v
        )
        self.assertIsNone(
            shader_preview_profile(None, ()).base_color_sampler
        )

    def test_shader_and_render_effect_graph(self) -> None:
        shader_object = bytes(8) + b"SHO\x08"
        shader_payload = (
            bytes((0, 0))
            + struct.pack(">II", 5, len(shader_object))
            + shader_object
        )

        render_state = (
            bytes((0,))
            + struct.pack(">iiBiiBBBHi", 1, 5, 1, 3, 7, 0, 0, 0, 0xFFFF, 17)
        )
        self.assertEqual(27, len(render_state))
        effect_payload = bytearray((0, 0))
        effect_payload.extend((0, 0))
        effect_payload.extend(compact(1))
        effect_payload.extend(render_state)
        effect_payload.extend((0, 1))
        effect_payload.extend(compact(1))
        effect_payload.append(1)
        effect_payload.append(2)
        effect_payload.extend(compact(len("external-texture")))
        effect_payload.extend(b"external-texture")
        effect_payload.extend(struct.pack(">eH", -0.5, 0x1003))
        effect_payload.extend(compact(1))
        effect_payload.append(2)
        effect_payload.extend(struct.pack(">I4f", 4, 0.5, 0.25, 0.125, 0.75))
        effect_payload.extend((0, 0))
        effect_payload.append(3)
        effect_payload.extend(struct.pack(">f", 2.5))
        effect_payload.extend(bytes.fromhex("00112233445566778899aabbccddeeff"))

        core = CoreFile.from_bytes(
            synthetic_core(
                ((0, shader_payload), (1, bytes(effect_payload))),
                ("", "inSampler0", "LMUVShift", "inVariable0"),
            ),
            source="materials.core",
        )
        decoder = CoreDecoder(core)
        shader = decoder.decode(0)
        effect = decoder.decode(1)
        self.assertIsInstance(shader, ShaderResource)
        self.assertIsInstance(effect, RenderEffect)
        self.assertEqual(5, shader.ibl_shading_mode)
        self.assertEqual(shader_object, shader.shader_object)
        self.assertEqual(64, len(shader.body_sha256))
        self.assertEqual(1, len(effect.techniques))
        self.assertEqual(16, len(effect.trailing))

        technique = effect.techniques[0]
        self.assertEqual(1, technique.render_state.cull_mode)
        self.assertEqual(5, technique.render_state.blend_mode)
        self.assertEqual(17, technique.render_state.technique_type)
        self.assertEqual(0, technique.shader.local_index)
        self.assertEqual(2.5, technique.reflection_sampler_bias)

        binding = technique.texture_bindings[0]
        self.assertEqual("inSampler0", binding.sampler)
        self.assertEqual("external-texture", binding.texture.external_id)
        self.assertEqual(-0.5, binding.mip_bias)
        self.assertEqual(0x1003, binding.packed_sampler_state)
        self.assertEqual((3, 0, 0, 1), (
            binding.filter_mode,
            binding.address_u,
            binding.address_v,
            binding.anisotropy,
        ))

        variable = technique.variable_bindings[0]
        self.assertEqual("LMUVShift", variable.name)
        self.assertEqual((0.5, 0.25, 0.125, 0.75), variable.value)
        self.assertTrue(variable.animator.is_null)
        self.assertEqual("inVariable0", variable.shader_input)


if __name__ == "__main__":
    unittest.main()
