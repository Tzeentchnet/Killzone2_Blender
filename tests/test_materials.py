from __future__ import annotations

import struct
import unittest
from collections import Counter

try:
    import bpy
except ModuleNotFoundError:
    bpy = None

from io_scene_killzone2.kz2_assets.character_material_profiles import (
    CHARACTER_SHADER_PROFILE_GROUPS,
)
from io_scene_killzone2.kz2_assets.core import CORE_SIGNATURE, CoreFile
from io_scene_killzone2.kz2_assets.decoder import CoreDecoder
from io_scene_killzone2.kz2_assets.materials import (
    ShaderPreviewProfile,
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
                if normal_sampler is not None:
                    self.assertEqual("tangent", profile.normal_space)
                    self.assertFalse(profile.normal_y_flip)
                    self.assertEqual(
                        "inSampler6",
                        profile.detail_normal_sampler,
                    )
                    self.assertEqual(
                        "inSampler4",
                        profile.reflection_sampler,
                    )

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
                layered_samplers[:-2] + (layered_samplers[-1], layered_samplers[-2]),
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

    def test_isa_intruder_profiles_flip_source_diffuse_v(self) -> None:
        detail_samplers = (
            "inSampler3",
            "inSampler4",
            "inSampler0",
            "inSampler1",
            "inSampler2",
        )
        variants = (
            (
                "isa_intruder_detail_deferred",
                ("ada42a8d61bb7f771c2cf907d04464cab2ae95f2298c87181b9446fd70be8f02"),
                "631dc38edec0712bbb2713bb8a048023",
                detail_samplers,
            ),
            (
                "isa_intruder_detail_deferred",
                ("63dddec576e98fd952ab7800bfeda3724d805aed72d4887feb2267ba8ceda1a5"),
                "c69e155a3ee8bf78fb69eb313bd02765",
                detail_samplers,
            ),
            (
                "isa_intruder_deferred",
                ("efdfa954d7e86a54650086a4d288eecfe1dde75ac1d7f187dd483b2b19e075d7"),
                "454c34802c8ecfd0f88b37607d5b615b",
                (
                    "inSampler3",
                    "inSampler0",
                    "inSampler1",
                    "inSampler2",
                ),
            ),
        )
        for profile_id, shader_hash, shader_object_id, samplers in variants:
            with self.subTest(shader_object_id=shader_object_id):
                profile = shader_preview_profile(
                    shader_hash,
                    samplers,
                    shader_object_id=shader_object_id,
                    variable_signature=(),
                )
                self.assertEqual(profile_id, profile.profile_id)
                self.assertEqual("proven", profile.confidence)
                self.assertEqual("inSampler0", profile.base_color_sampler)
                self.assertEqual("DiffuseUV", profile.base_color_uv_map)
                self.assertEqual(
                    "inSampler1",
                    profile.secondary_base_color_sampler,
                )
                self.assertEqual("inSampler3", profile.normal_sampler)
                self.assertEqual("tangent", profile.normal_space)
                self.assertFalse(profile.normal_y_flip)
                self.assertEqual("inSampler2", profile.material_sampler)
                self.assertEqual("R", profile.specular_power_channel)
                self.assertEqual(49.0, profile.specular_power_scale)
                self.assertEqual("B", profile.specular_intensity_channel)
                expected_detail = (
                    "inSampler4"
                    if profile_id == "isa_intruder_detail_deferred"
                    else None
                )
                self.assertEqual(
                    expected_detail,
                    profile.detail_normal_sampler,
                )
                self.assertTrue(profile.flip_diffuse_v)

                unmatched = (
                    shader_preview_profile(
                        "0" * 64,
                        samplers,
                        shader_object_id=shader_object_id,
                        variable_signature=(),
                    ),
                    shader_preview_profile(
                        shader_hash,
                        samplers,
                        shader_object_id="0" * 32,
                        variable_signature=(),
                    ),
                    shader_preview_profile(
                        shader_hash,
                        samplers[:-2] + (samplers[-1], samplers[-2]),
                        shader_object_id=shader_object_id,
                        variable_signature=(),
                    ),
                    shader_preview_profile(
                        shader_hash,
                        samplers,
                        shader_object_id=shader_object_id,
                        variable_signature=("inVariable0",),
                    ),
                    shader_preview_profile(
                        shader_hash,
                        samplers,
                        shader_object_id=shader_object_id,
                        variable_signature=(),
                        technique_index=1,
                    ),
                )
                self.assertTrue(all(value.profile_id is None for value in unmatched))

    def test_vehicle_profiles_match_audited_shader_identities(self) -> None:
        variants = (
            (
                "58708032f3f7904b61d80752fb598b8972c6cb41f7e3ff5aa78ae922b6589b96",
                "0f8732ddf90aacb3a9f3b4c1e63fc752",
                "inSampler0 inSampler1 inSampler2",
                "",
                "hgh_dropship_unbumped_deferred",
                (
                    (
                        "inSampler0",
                        None,
                        None,
                        None,
                        "inSampler1",
                        "inSampler2",
                    ),
                    ("G", 98.0, "B", False),
                ),
            ),
            (
                "c3569b2b9d9b56216083d2b4a7ac42fa35f62d657c36086c5a709a0f0cd355b9",
                "6b2068005285c8ab951a526590a4d8a4",
                "inSampler3 inSampler0 inSampler1 inSampler2",
                "",
                "hgh_dropship_complex_deferred",
                (
                    (
                        "inSampler0",
                        None,
                        "inSampler3",
                        None,
                        "inSampler1",
                        "inSampler2",
                    ),
                    (None, None, None, False),
                ),
            ),
            (
                "7fb539ad3e61bc26299da411f01fb6393caf9a7eb81da72dc7bcc26f283c8680",
                "8c77e857231655920b6e4835ab731d4b",
                "inSampler3 inSampler0 inSampler1 inSampler2",
                "",
                "hgh_dropship_deferred",
                (
                    (
                        "inSampler0",
                        None,
                        "inSampler3",
                        None,
                        "inSampler1",
                        "inSampler2",
                    ),
                    ("G", 98.0, "B", False),
                ),
            ),
            (
                "0c4a01afb71e82cbedba54226b10bba85aa3231b576a093c93ddc508acecf947",
                "ed94308fbe5e1714583b41e9e0e7e3c0",
                "inSampler3 inSampler0 inSampler1 inSampler2",
                "",
                "hgh_dropship_deferred_g49",
                (
                    (
                        "inSampler0",
                        None,
                        "inSampler3",
                        None,
                        "inSampler1",
                        "inSampler2",
                    ),
                    ("G", 49.0, "B", False),
                ),
            ),
            (
                "c0569bd01c8a612f914a0ecd7fc7552cbfbaf0eaf064913192288c1c1fee266e",
                "9a0fa36f2abfa919579d6395db377b27",
                "inSampler3 inSampler4 inSampler0 inSampler1 inSampler2",
                "",
                "hgh_aapc_layered_deferred",
                (
                    (
                        "inSampler0",
                        "inSampler1",
                        "inSampler3",
                        "inSampler4",
                        "inSampler2",
                        None,
                    ),
                    (None, None, None, False),
                ),
            ),
            (
                "598aaf0957f2f2511905a1ab42de71c1c9714ef08b61813ec34b970e5fdbf4a0",
                "c1ad2d75a5e9d53144d03f7e7f0cd1f0",
                "inSampler3 inSampler4 inSampler0 inSampler1 inSampler2",
                "",
                "hgh_aapc_layered_deferred_r49",
                (
                    (
                        "inSampler0",
                        "inSampler1",
                        "inSampler3",
                        "inSampler4",
                        "inSampler2",
                        None,
                    ),
                    ("R", 49.0, "B", False),
                ),
            ),
            (
                "d5ac3104161f2016a4fe07a902f57ff83ed4f55fa08511e6972b29742e7451e2",
                "c0d90d1ad5eaed9ee58cd8ab7dc82471",
                "inSampler0 inSampler1 inSampler3 inRampSampler2",
                "inVariable0",
                "hgh_aapc_ramp_deferred",
                (
                    (
                        "inSampler0",
                        "inSampler1",
                        None,
                        None,
                        "inSampler3",
                        None,
                    ),
                    (None, None, None, False),
                ),
            ),
            (
                "1caece656ebfdc41de02d665814b6fecfe5edd75748b35ee70d9bdf6ad70218e",
                "152325d374d7231cd503d72edc7e5de7",
                "inSampler2 inSampler3 inSampler0 inSampler1",
                "",
                "isa_aapc_layered_deferred",
                (
                    (
                        "inSampler0",
                        None,
                        "inSampler2",
                        "inSampler3",
                        "inSampler1",
                        None,
                    ),
                    ("R", 49.0, "B", False),
                ),
            ),
            (
                "7ab01c0931d5c8b4e1739de007de15106cce40d7a50cd950d1f2a15c9bd60b6a",
                "1ed8c531fb7f936c69492669042b94f0",
                "inSampler2 inSampler3 inSampler0 inSampler1",
                "",
                "isa_aapc_layered_deferred",
                (
                    (
                        "inSampler0",
                        None,
                        "inSampler2",
                        "inSampler3",
                        "inSampler1",
                        None,
                    ),
                    ("R", 49.0, "B", False),
                ),
            ),
            (
                "fe891522674ec48a0ff924b61e122cca650219ca36380493e1e813e645246924",
                "4a209aac9aeef3306dce19e46527d0fa",
                "inSampler2 inSampler3 inSampler0 inSampler1",
                "",
                "isa_aapc_layered_deferred",
                (
                    (
                        "inSampler0",
                        None,
                        "inSampler2",
                        "inSampler3",
                        "inSampler1",
                        None,
                    ),
                    ("R", 49.0, "B", False),
                ),
            ),
            (
                "054f00500f1cc394fba8a6ac12db7327284ab5be843973b0fe1c6f05c17899ed",
                "b87a2ff0d9c2aad4cdbd231b0cc2b9d6",
                "inSampler2 inSampler3 inSampler0 inSampler1",
                "",
                "isa_aapc_layered_deferred",
                (
                    (
                        "inSampler0",
                        None,
                        "inSampler2",
                        "inSampler3",
                        "inSampler1",
                        None,
                    ),
                    ("R", 49.0, "B", False),
                ),
            ),
            (
                "72da9756337549d6a55bba21325d100218fe25c83164f1d09755b1abfc2064ff",
                "406ed5c26dc5c3d5d3ed3fc501004304",
                "inSampler2 inSampler0 inSampler1",
                "inVariable0",
                "isa_aapc_compact_masked_deferred",
                (
                    (
                        "inSampler0",
                        None,
                        "inSampler2",
                        None,
                        "inSampler1",
                        None,
                    ),
                    (None, None, None, False),
                ),
            ),
            (
                "8559e15443f242179eac974262a80d1e73136559757bbb5b1fe6ce681a63a33f",
                "9688112c2f4711397c49debe72fcd216",
                "inSampler2 inSampler0 inSampler1",
                "inVariable0",
                "isa_aapc_compact_masked_deferred",
                (
                    (
                        "inSampler0",
                        None,
                        "inSampler2",
                        None,
                        "inSampler1",
                        None,
                    ),
                    (None, None, None, False),
                ),
            ),
            (
                "b47fc160fd010b6e61750f1523a39464d6dba63a0c7cdab299267d2c75822748",
                "616083d7b97f21c7175a098bbc01b493",
                "inSampler0 inSampler2 inRampSampler1",
                "inVariable0",
                "isa_aapc_ramp_deferred_g49",
                (
                    (
                        "inSampler0",
                        None,
                        None,
                        None,
                        "inSampler2",
                        None,
                    ),
                    ("G", 49.0, "B", False),
                ),
            ),
            (
                "3d5ceebb9198d3702a678a04f40fae9cfb88ff7f01b684eb1743a8282bcf47cc",
                "8735cb04da386a30d617748a6c942f86",
                "inSampler0 inSampler1 inSampler3 inRampSampler2",
                "inVariable0",
                "isa_aapc_ramp_deferred",
                (
                    (
                        "inSampler0",
                        None,
                        None,
                        None,
                        "inSampler3",
                        None,
                    ),
                    (None, None, None, False),
                ),
            ),
            (
                "cbadbe7f3c1f92001f20d7de9195026c648ffd550fcbf93af1fde0b4b634c314",
                "4f122b99db7e4223fd2865254cf1d158",
                "inSampler3 inSampler0 inSampler1 inSampler2",
                "",
                "isa_buggy_secondary_deferred",
                (
                    (
                        "inSampler0",
                        "inSampler1",
                        "inSampler3",
                        None,
                        "inSampler2",
                        None,
                    ),
                    ("R", 49.0, "B", False),
                ),
            ),
            (
                "4f9d2da40a9d977500e1bbbf3ad630553c7b62bb67ecd86b402d8eff9c342f2a",
                "6ecc1dabafc42f2a93c41a50df1b6687",
                "inSampler4 inSampler5 inSampler2 inSampler0 inSampler1 inSampler3",
                "",
                "isa_buggy_layered_deferred",
                (
                    (
                        "inSampler0",
                        None,
                        "inSampler4",
                        "inSampler5",
                        "inSampler3",
                        None,
                    ),
                    ("R", 49.0, "B", False),
                ),
            ),
            (
                "dcbee4ab35c0a7acf4ff85489c8cba55a6a5b3a4f23fdfb1d40ab2253db4afe2",
                "ef63afc5d7b34083a33cd95d15f245e1",
                "inSampler4 inSampler5 inSampler2 inSampler0 inSampler1 inSampler3",
                "",
                "isa_buggy_layered_deferred",
                (
                    (
                        "inSampler0",
                        None,
                        "inSampler4",
                        "inSampler5",
                        "inSampler3",
                        None,
                    ),
                    ("R", 49.0, "B", False),
                ),
            ),
            (
                "7f055707a9b1251075a5aa088652eef5d04eb0bdab6c73c8a86d3594b7de45ce",
                "6f62459a7e6472f7e324bbfd6f863e9b",
                "inSampler2 inSampler0 inSampler1",
                "",
                "isa_buggy_compact_deferred",
                (
                    (
                        "inSampler0",
                        None,
                        "inSampler2",
                        None,
                        "inSampler1",
                        None,
                    ),
                    ("R", 49.0, "B", False),
                ),
            ),
            (
                "25897f95582e5e5a2a8e4d65e9772940af24eb9d754f89466380205472df6ad1",
                "aab8fd8e71d04ac7493f3ce2ec02acb4",
                "inSampler0 inSampler1 inSampler3 inRampSampler2",
                "inVariable0",
                "isa_buggy_response_ramp_deferred_r49",
                (
                    (
                        "inSampler0",
                        None,
                        None,
                        None,
                        "inSampler3",
                        None,
                    ),
                    ("R", 49.0, "B", False),
                ),
            ),
            (
                "06a1e37041ac53c888e849fc273d644ff67f1af6eeb123f5c1f46e6652bcf1ab",
                "c0c99abb39f9e039272da739390ad8f9",
                "inSampler0 inSampler1 inSampler2",
                "",
                "isa_buggy_secondary_material_deferred",
                (
                    (
                        "inSampler0",
                        "inSampler1",
                        None,
                        None,
                        "inSampler2",
                        None,
                    ),
                    ("R", 49.0, "B", False),
                ),
            ),
            (
                "fb5d2e1409facfa4a789a47e5b033d91f70efea299995a42586e1e58b8277e88",
                "f8f6ce14f8ba83f5f28a17da586ce44a",
                "inSampler0 inSampler2 inRampSampler1",
                "inVariable0",
                "isa_buggy_ramp_deferred_r49",
                (
                    (
                        "inSampler0",
                        None,
                        None,
                        None,
                        "inSampler2",
                        None,
                    ),
                    ("R", 49.0, "B", False),
                ),
            ),
            (
                "7227b32f7ec3404bfa816fba22588460b6ac6be3c30efbd92902458466d543b2",
                "7de10c6edd018eeed89312a403ef4f83",
                "inSampler0 inSampler1 inSampler2",
                "",
                "isa_exoskeleton_response_deferred",
                (
                    (
                        "inSampler0",
                        None,
                        None,
                        None,
                        "inSampler2",
                        None,
                    ),
                    (None, None, None, False),
                ),
            ),
            (
                "fbbdd7aa9e2bf575db2230329ae08983a8960b5db36443c8f2833fc89a67cec1",
                "a61e51472c418e6f30154536683653fd",
                "inSampler3 inSampler4 inSampler0 inSampler1 inSampler2",
                "",
                "isa_exoskeleton_layered_deferred",
                (
                    (
                        "inSampler0",
                        "inSampler1",
                        "inSampler3",
                        "inSampler4",
                        "inSampler2",
                        None,
                    ),
                    ("G", 49.0, "B", False),
                ),
            ),
            (
                "d393292d085c298e27433d2dae9f79fc884d06ffd17426660e38f1e8131044e4",
                "a715f1d41d0e6be11eaea6d4071df562",
                "inSampler3 inSampler4 inSampler0 inSampler1 inSampler2",
                "",
                "isa_exoskeleton_layered_deferred",
                (
                    (
                        "inSampler0",
                        "inSampler1",
                        "inSampler3",
                        "inSampler4",
                        "inSampler2",
                        None,
                    ),
                    ("G", 49.0, "B", False),
                ),
            ),
            (
                "d67859911e4d407619b2821692266b23b42aa7b3d858302bbbe334e17d98b4ca",
                "f8ad3999502f015448dce15888a17263",
                "inSampler2 inSampler0 inSampler1",
                "",
                "hgh_atac_deferred",
                (
                    (
                        "inSampler0",
                        None,
                        "inSampler2",
                        None,
                        "inSampler1",
                        None,
                    ),
                    ("R", 6.125, "B", False),
                ),
            ),
            (
                "027b0d3e923850b4f18a87443502ddac6a9403862890fcc8e34e7b6124f70aba",
                "1717d873cb90d6a8fc5cc44dac5d7fc8",
                "inSampler2 inSampler0 inSampler1 inSampler3 inSampler4",
                "",
                "hgh_cruiser_layered_deferred",
                (
                    (
                        "inSampler0",
                        None,
                        "inSampler2",
                        None,
                        "inSampler3",
                        "inSampler4",
                    ),
                    (None, None, None, False),
                ),
            ),
            (
                "06f6c846742879c30547a4e95622cf064d61898d2b17126ddb5078a1dcde49bf",
                "7511eed9983ae59c434a301c52d28ccf",
                "inSampler4 inSampler2 inSampler0 inSampler1 inSampler3",
                "",
                "hgh_cruiser_compact_deferred",
                (
                    (
                        "inSampler0",
                        None,
                        "inSampler4",
                        None,
                        "inSampler3",
                        None,
                    ),
                    (None, None, None, False),
                ),
            ),
            (
                "8e07bf59967b2bfbcc9cf4c02cfd3910bac418d157ae4814c81bc4bb040dd722",
                "def4c8fbe2b0ff5e5f00548cbb4ef0ea",
                "inSampler4 inSampler2 inSampler0 inSampler1 inSampler3",
                "",
                "hgh_cruiser_compact_deferred",
                (
                    (
                        "inSampler0",
                        None,
                        "inSampler4",
                        None,
                        "inSampler3",
                        None,
                    ),
                    (None, None, None, False),
                ),
            ),
            (
                "78d6aa1a200321676f5ab707ee4da8de5643d1accaacc83d4e48311e5a871d15",
                "cfb995ef1c6f06bb97384e211a4d4b81",
                "inSampler4 inSampler0 inSampler1 inSampler2 inSampler3",
                "",
                "hgh_cruiser_deferred_g12_25",
                (
                    (
                        "inSampler0",
                        None,
                        "inSampler4",
                        None,
                        "inSampler2",
                        "inSampler3",
                    ),
                    ("G", 12.25, "B", False),
                ),
            ),
            (
                "4fc4f3a8ab53f39cbe5e77583f34b06b87845f9d590064a246ae6a896d035e89",
                "5b0789b6e9abb31163bdbe55a8111d99",
                (
                    "inSampler5 inSampler6 inSampler0 inSampler1 "
                    "inSampler2 inSampler3 inSampler4"
                ),
                "inVariable0",
                "isa_cruiser_layered_deferred",
                (
                    (
                        "inSampler1",
                        None,
                        "inSampler5",
                        "inSampler6",
                        "inSampler3",
                        "inSampler4",
                    ),
                    (None, None, None, True),
                ),
            ),
            (
                "3c8ba294abfa1b895bb397bba592ffcd18bc8f85df058818bc4ba5314bb512ba",
                "c6dcc542d690fc4761ab407049ad4873",
                (
                    "inSampler5 inSampler6 inSampler0 inSampler1 "
                    "inSampler2 inSampler3 inSampler4"
                ),
                "inVariable0",
                "isa_cruiser_layered_deferred",
                (
                    (
                        "inSampler1",
                        None,
                        "inSampler5",
                        "inSampler6",
                        "inSampler3",
                        "inSampler4",
                    ),
                    (None, None, None, True),
                ),
            ),
            (
                "3353134d8be4637272de114a7a20506450c99491febc420db4983b19c08349a2",
                "f1ef6049dec3cd24a903e307d77f0be4",
                (
                    "inSampler5 inSampler6 inSampler1 inSampler0 "
                    "inSampler2 inSampler3 inSampler4"
                ),
                "inVariable0",
                "isa_cruiser_layered_deferred",
                (
                    (
                        "inSampler1",
                        None,
                        "inSampler5",
                        "inSampler6",
                        "inSampler3",
                        "inSampler4",
                    ),
                    (None, None, None, True),
                ),
            ),
        )
        for (
            shader_hash,
            shader_object_id,
            samplers,
            variables,
            profile_id,
            expected,
        ) in variants:
            with self.subTest(shader_object_id=shader_object_id):
                sampler_signature = tuple(samplers.split())
                variable_signature = tuple(variables.split())
                profile = shader_preview_profile(
                    shader_hash,
                    sampler_signature,
                    shader_object_id=shader_object_id,
                    variable_signature=variable_signature,
                )
                self.assertEqual(profile_id, profile.profile_id)
                self.assertEqual("proven", profile.confidence)
                self.assertEqual(
                    expected,
                    (
                        (
                            profile.base_color_sampler,
                            profile.secondary_base_color_sampler,
                            profile.normal_sampler,
                            profile.detail_normal_sampler,
                            profile.material_sampler,
                            profile.reflection_sampler,
                        ),
                        (
                            profile.specular_power_channel,
                            profile.specular_power_scale,
                            profile.specular_intensity_channel,
                            profile.flip_diffuse_v,
                        ),
                    ),
                )
                if profile.normal_sampler is not None:
                    self.assertEqual("tangent", profile.normal_space)
                    self.assertFalse(profile.normal_y_flip)
                mismatches = (
                    shader_preview_profile(
                        "0" * 64,
                        sampler_signature,
                        shader_object_id=shader_object_id,
                        variable_signature=variable_signature,
                    ),
                    shader_preview_profile(
                        shader_hash,
                        sampler_signature,
                        shader_object_id="0" * 32,
                        variable_signature=variable_signature,
                    ),
                    shader_preview_profile(
                        shader_hash,
                        sampler_signature + ("mismatch",),
                        shader_object_id=shader_object_id,
                        variable_signature=variable_signature,
                    ),
                    shader_preview_profile(
                        shader_hash,
                        sampler_signature,
                        shader_object_id=shader_object_id,
                        variable_signature=variable_signature + ("mismatch",),
                    ),
                    shader_preview_profile(
                        shader_hash,
                        sampler_signature,
                        shader_object_id=shader_object_id,
                        variable_signature=variable_signature,
                        technique_index=1,
                    ),
                )
                self.assertTrue(all(item.profile_id is None for item in mismatches))

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
        self.assertFalse(profile.normal_y_flip)
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

    def test_tank_profiles_match_disassembled_shader_identities(
        self,
    ) -> None:
        expected = {
            "hgh_layered": (
                "hgh_tank_layered_deferred",
                "inSampler0",
                None,
                "inSampler3",
                "inSampler4",
                "inSampler1",
                "inSampler2",
                "G",
                49.0,
            ),
            "hgh_track": (
                "hgh_tank_track_deferred",
                "inSampler0",
                "inSampler1",
                "inSampler4",
                None,
                "inSampler2",
                "inSampler3",
                "R",
                49.0,
            ),
            "hgh_compact": (
                "hgh_tank_compact_deferred",
                "inSampler0",
                None,
                "inSampler2",
                None,
                "inSampler1",
                None,
                "R",
                49.0,
            ),
            "hgh_g49": (
                "hgh_tank_packed_g49",
                "inSampler0",
                None,
                None,
                None,
                "inSampler1",
                None,
                "G",
                49.0,
            ),
            "hgh_r49": (
                "hgh_tank_packed_r49",
                "inSampler0",
                None,
                None,
                None,
                "inSampler1",
                None,
                "R",
                49.0,
            ),
            "isa_layered": (
                "isa_tank_layered_deferred",
                "inSampler0",
                None,
                "inSampler2",
                "inSampler3",
                "inSampler1",
                None,
                "G",
                39.1904,
            ),
            "isa_track": (
                "isa_tank_track_deferred",
                "inSampler0",
                "inSampler1",
                "inSampler3",
                None,
                "inSampler2",
                None,
                "R",
                39.1904,
            ),
            "isa_compact": (
                "isa_tank_compact_deferred",
                "inSampler0",
                None,
                "inSampler2",
                None,
                "inSampler1",
                None,
                "R",
                49.0,
            ),
        }
        variants = (
            (
                "37118f7b07d5263cfeee4f6d6b6a7ba4",
                ("856b7cea143b22b4236c2e64f9ede01a5739afdf2d737d65c816713f928215cb"),
                (
                    "inSampler3",
                    "inSampler4",
                    "inSampler0",
                    "inSampler1",
                    "inSampler2",
                ),
                (),
                "hgh_layered",
            ),
            (
                "90f8c2677ed634533711eb2bfed57af0",
                ("102252b294a07c0df6a8ec5fb3fdc46b11e928ab045e407df87bd480886e4e2f"),
                (
                    "inSampler3",
                    "inSampler4",
                    "inSampler0",
                    "inSampler1",
                    "inSampler2",
                ),
                (),
                "hgh_layered",
            ),
            (
                "6cf769273cef73fc83c4b5f780adfebc",
                ("2e06729d23b996c93e525ce0a89f643e3ca196bdbe0ea6c3959f302fb3ab07ad"),
                (
                    "inSampler3",
                    "inSampler4",
                    "inSampler0",
                    "inSampler1",
                    "inSampler2",
                ),
                (),
                "hgh_layered",
            ),
            (
                "e03466b9264b1c8a6e663a39cb0f511e",
                ("6f8cb5a4e7ead10942835fe7335f4b75e4e887ab1f5188fb0fa40f89f149519f"),
                (
                    "inSampler4",
                    "inSampler0",
                    "inSampler1",
                    "inSampler2",
                    "inSampler3",
                ),
                ("inVariable0",),
                "hgh_track",
            ),
            (
                "b0a8b52eb0560707ecc42dc8c1ec7f88",
                ("a98c18009a2d8cf814ed0fe73877e9925c63a76b9ddba10836e59215bbbbbade"),
                ("inSampler2", "inSampler0", "inSampler1"),
                (),
                "hgh_compact",
            ),
            (
                "a58e7b007d4d88e2ddfaa61af86a4ab7",
                ("0f4a68bb17adc14ca0ebed9f3dcc5a651e42bd8be016bc300369f90e5f74b9e7"),
                ("inSampler0", "inSampler1"),
                (),
                "hgh_g49",
            ),
            (
                "5c879214cf2789ce4728d5de9d9cbc16",
                ("9ca4f7b18d8c62d21cb9723f7489389a03a2dcde7c4671b94f3a50303cdb1f26"),
                ("inSampler0", "inSampler1"),
                (),
                "hgh_r49",
            ),
            (
                "c4de041dda84841306f1f2921c995c89",
                ("b971098ebeb5904b705406b56b0417c477db425bacb78676ae91c0e370191db7"),
                (
                    "inSampler2",
                    "inSampler3",
                    "inSampler0",
                    "inSampler1",
                ),
                (),
                "isa_layered",
            ),
            (
                "7a83011fface8f01eca0b3604fb22f5a",
                ("7402904c59530bb8e54c2aac9a8cdd8c1a2966d6a00e52b1552d2d23c5109c6a"),
                (
                    "inSampler2",
                    "inSampler3",
                    "inSampler0",
                    "inSampler1",
                ),
                (),
                "isa_layered",
            ),
            (
                "710036fa5d9aaddd90279f934e1c7275",
                ("7663f616f936f6018008dc0fd7e96b0aa0c5cca61d28b6ec864ba8347afc4c2a"),
                (
                    "inSampler2",
                    "inSampler3",
                    "inSampler0",
                    "inSampler1",
                ),
                (),
                "isa_layered",
            ),
            (
                "0a1b584d6a258c6dc25610e3289d5406",
                ("ab8b28b5a81fd5f7ac88d818e2165f9bc87fa9436f92496a4fac7305fd9744de"),
                (
                    "inSampler2",
                    "inSampler3",
                    "inSampler1",
                    "inSampler0",
                ),
                (),
                "isa_layered",
            ),
            (
                "ef5c0a58695ab51ed3bd2e44d1ea0c91",
                ("5083bb26725fdb4a33ee9249c58b0d6a79b67cfbee5658c074613928aacbd331"),
                (
                    "inSampler2",
                    "inSampler3",
                    "inSampler1",
                    "inSampler0",
                ),
                (),
                "isa_layered",
            ),
            (
                "2283f987591d17d65f60db32222736bb",
                ("f1898eed7929bc16990619e97b61d26db72df54d0974721b575460fd0429706f"),
                (
                    "inSampler3",
                    "inSampler0",
                    "inSampler1",
                    "inSampler2",
                ),
                ("inVariable0",),
                "isa_track",
            ),
            (
                "d8de6701c66b7caece3b70e3555b198b",
                ("8af06e77b62d7f458fe4d44cdeebb7920f0775c6e06cae624581fcbea292bfd4"),
                ("inSampler2", "inSampler0", "inSampler1"),
                (),
                "isa_compact",
            ),
        )

        for shader_id, shader_hash, samplers, variables, profile_key in variants:
            with self.subTest(shader_id=shader_id):
                profile = shader_preview_profile(
                    shader_hash,
                    samplers,
                    shader_object_id=shader_id,
                    variable_signature=variables,
                )
                self.assertEqual("proven", profile.confidence)
                self.assertEqual(
                    expected[profile_key],
                    (
                        profile.profile_id,
                        profile.base_color_sampler,
                        profile.secondary_base_color_sampler,
                        profile.normal_sampler,
                        profile.detail_normal_sampler,
                        profile.material_sampler,
                        profile.reflection_sampler,
                        profile.specular_power_channel,
                        profile.specular_power_scale,
                    ),
                )
                self.assertEqual(
                    "B",
                    profile.specular_intensity_channel,
                )
                if profile.normal_sampler is not None:
                    self.assertEqual("tangent", profile.normal_space)
                    self.assertFalse(profile.normal_y_flip)
                if profile.detail_normal_sampler is not None:
                    self.assertEqual(
                        "detail normal",
                        profile.role_for_sampler(profile.detail_normal_sampler),
                    )

    def test_character_profiles_match_all_audited_shader_identities(
        self,
    ) -> None:
        expected_counts = {
            "character_alpha_color": 11,
            "character_color_material": 1,
            "character_color_only": 3,
            "character_color_reflection": 1,
            "character_compact_material_deferred": 4,
            "character_compact_normal_deferred": 5,
            "character_constant_deferred": 1,
            "character_eye_layered": 1,
            "character_hair_deferred": 2,
            "character_layered_deferred": 9,
            "character_modulated_deferred": 1,
            "character_multiply_deferred": 1,
            "character_navy_packed_normal_deferred": 2,
            "character_packed_xy_normal_deferred": 1,
            "character_rico_packed_normal_deferred": 1,
            "character_skin_deferred": 12,
            "character_specialized_deferred": 2,
            "character_standard_deferred": 46,
            "character_visari_skin_deferred": 1,
        }
        counts = Counter(
            group.profile_id
            for group in CHARACTER_SHADER_PROFILE_GROUPS
            for _identity in group.identities
        )
        self.assertEqual(expected_counts, counts)
        self.assertEqual(105, counts.total())

        for group in CHARACTER_SHADER_PROFILE_GROUPS:
            for shader_hash, shader_object_id in group.identities:
                with self.subTest(shader_object_id=shader_object_id):
                    profile = shader_preview_profile(
                        shader_hash,
                        group.sampler_signature,
                        shader_object_id=shader_object_id,
                        variable_signature=group.variable_signature,
                    )
                    self.assertEqual(group.profile_id, profile.profile_id)
                    self.assertEqual("proven", profile.confidence)

                    unmatched = (
                        shader_preview_profile(
                            (("0" if shader_hash[0] != "0" else "1") + shader_hash[1:]),
                            group.sampler_signature,
                            shader_object_id=shader_object_id,
                            variable_signature=group.variable_signature,
                        ),
                        shader_preview_profile(
                            shader_hash,
                            group.sampler_signature,
                            shader_object_id="wrong-shader-object",
                            variable_signature=group.variable_signature,
                        ),
                        shader_preview_profile(
                            shader_hash,
                            group.sampler_signature + ("unexpected",),
                            shader_object_id=shader_object_id,
                            variable_signature=group.variable_signature,
                        ),
                        shader_preview_profile(
                            shader_hash,
                            group.sampler_signature,
                            shader_object_id=shader_object_id,
                            variable_signature=(
                                group.variable_signature + ("unexpected",)
                            ),
                        ),
                        shader_preview_profile(
                            shader_hash,
                            group.sampler_signature,
                            shader_object_id=shader_object_id,
                            variable_signature=group.variable_signature,
                            technique_index=1,
                        ),
                    )
                    self.assertTrue(
                        all(candidate.profile_id is None for candidate in unmatched)
                    )

    def test_character_profiles_retain_complex_non_color_inputs(
        self,
    ) -> None:
        def profile_for(shader_object_id: str) -> ShaderPreviewProfile:
            group = next(
                group
                for group in CHARACTER_SHADER_PROFILE_GROUPS
                if any(
                    identity_id == shader_object_id
                    for _shader_hash, identity_id in group.identities
                )
            )
            shader_hash = next(
                shader_hash
                for shader_hash, identity_id in group.identities
                if identity_id == shader_object_id
            )
            return shader_preview_profile(
                shader_hash,
                group.sampler_signature,
                shader_object_id=shader_object_id,
                variable_signature=group.variable_signature,
            )

        standard = profile_for("13b036b8673f119668a570058207c529")
        self.assertEqual("inSampler3", standard.normal_sampler)
        self.assertEqual("inSampler1", standard.material_sampler)
        self.assertTrue(standard.sampler_uses_non_color_data("inSampler2"))

        layered = profile_for("05854aef4bf093bd5534e00f614100f3")
        self.assertEqual("inSampler5", layered.normal_sampler)
        self.assertEqual("inSampler1", layered.detail_normal_sampler)
        self.assertEqual(
            "normal blend mask",
            layered.role_for_sampler("inSampler2"),
        )
        self.assertTrue(layered.sampler_uses_non_color_data("inSampler2"))

        skin = profile_for("05e194883fd5ca33b17a25c9d195f229")
        self.assertEqual("inSampler6", skin.normal_sampler)
        self.assertEqual("inSampler5", skin.detail_normal_sampler)
        self.assertEqual(
            "skin response ramp",
            skin.role_for_sampler("inRampSampler1"),
        )
        self.assertTrue(skin.sampler_uses_non_color_data("inRampSampler1"))

        alpha = profile_for("1126768122f73410dad7fb2d14155704")
        self.assertEqual("inSampler0", alpha.opacity_sampler)
        self.assertEqual(
            "base color + opacity",
            alpha.role_for_sampler("inSampler0"),
        )

        hair = profile_for("671ca18ce63e7acc921d4fe785366e46")
        self.assertEqual("inSampler1", hair.normal_sampler)
        self.assertFalse(hair.normal_y_flip)

        packed = profile_for("25a9bb8e28fce9a8db299efaf19e1edd")
        self.assertIsNone(packed.normal_sampler)
        self.assertEqual(
            "packed A/G tangent normal (retained)",
            packed.role_for_sampler("inSampler1"),
        )
        self.assertTrue(packed.sampler_uses_non_color_data("inSampler1"))

    def test_hgh_character_profiles_decode_normal_and_specular_channels(
        self,
    ) -> None:
        samplers = (
            "inSampler3",
            "inSampler0",
            "inSampler1",
            "inSampler2",
        )
        variants = (
            (
                ("b26212b2b1a9d326fff9ab939f5f2b52aa1a755de9a4dacb393aa064bc5790ab"),
                "2ee5e3ff3a42dd5cd1f33374891901f0",
                "hgh_character_deferred_g24_5",
                "G",
                24.5,
            ),
            (
                ("9c95d347249ae1ecb6bda6bbff7d0f3f159d5b59c342e42296e700027045ed5a"),
                "3d23b1bce366078a44c0bfe6ac133f61",
                "hgh_character_deferred_r14_7024",
                "R",
                14.7024,
            ),
            (
                ("5201239dbeafa941605bde1b0eb3f0e2f8b49342334400592395b96519e6b36c"),
                "1de0cb0694403a758185efc552229c6d",
                "hgh_character_deferred_r49",
                "R",
                49.0,
            ),
            (
                ("b6079e1145b9b727936891c66c57a247ed0868fdc33b05c45b0e2030a696a7fb"),
                "ea746cea3656762cd8b70a78060d9654",
                "hgh_character_deferred_r49",
                "R",
                49.0,
            ),
        )
        for (
            shader_hash,
            shader_object_id,
            profile_id,
            power_channel,
            power_scale,
        ) in variants:
            with self.subTest(shader_object_id=shader_object_id):
                profile = shader_preview_profile(
                    shader_hash,
                    samplers,
                    shader_object_id=shader_object_id,
                    variable_signature=(),
                )
                self.assertEqual(profile_id, profile.profile_id)
                self.assertEqual("proven", profile.confidence)
                self.assertEqual("inSampler0", profile.base_color_sampler)
                self.assertEqual("inSampler3", profile.normal_sampler)
                self.assertEqual("tangent", profile.normal_space)
                self.assertFalse(profile.normal_y_flip)
                self.assertEqual("inSampler1", profile.material_sampler)
                self.assertEqual("inSampler2", profile.reflection_sampler)
                self.assertTrue(
                    profile.sampler_uses_non_color_data("inSampler2")
                )
                self.assertEqual(
                    power_channel,
                    profile.specular_power_channel,
                )
                self.assertEqual(
                    power_scale,
                    profile.specular_power_scale,
                )
                self.assertEqual(
                    "B",
                    profile.specular_intensity_channel,
                )

                unmatched = shader_preview_profile(
                    "0" * 64,
                    samplers,
                    shader_object_id=shader_object_id,
                    variable_signature=(),
                )
                self.assertIsNone(unmatched.profile_id)

    def test_hgh_fighter_profiles_decode_material_and_prelighting(
        self,
    ) -> None:
        variants = (
            (
                "9fe62b8cc9f046214ae79628ea86b824c385f988bd758cfcabbff6b1fb45d0d8",
                "8b87b9478d05f0dce11dc5c7be09a657",
                "hgh_fighter_skinned",
                2.0,
            ),
            (
                "3fbfdd224e92a41d0b6db8008dd1013762fb86cf268f93beaed24ad85220ae8c",
                "0baf0706e8b84648060ad143b16824ac",
                "hgh_fighter_static",
                None,
            ),
        )
        for (
            shader_hash,
            shader_object_id,
            profile_id,
            emission_power,
        ) in variants:
            with self.subTest(shader_object_id=shader_object_id):
                profile = shader_preview_profile(
                    shader_hash,
                    ("inSampler0", "inSampler1"),
                    shader_object_id=shader_object_id,
                    variable_signature=(),
                )
                self.assertEqual(profile_id, profile.profile_id)
                self.assertEqual("proven", profile.confidence)
                self.assertEqual("inSampler0", profile.base_color_sampler)
                self.assertEqual("inSampler1", profile.material_sampler)
                self.assertEqual("G", profile.specular_power_channel)
                self.assertEqual(24.5, profile.specular_power_scale)
                self.assertEqual("B", profile.specular_intensity_channel)
                self.assertEqual(
                    emission_power,
                    profile.vertex_color_emission_power,
                )
                self.assertTrue(
                    profile.sampler_uses_non_color_data("inSampler1")
                )

                unmatched = shader_preview_profile(
                    "0" * 64,
                    ("inSampler0", "inSampler1"),
                    shader_object_id=shader_object_id,
                    variable_signature=(),
                )
                self.assertIsNone(unmatched.profile_id)

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

    def test_new_sun_sky_profiles_match_exact_programs(self) -> None:
        variants = (
            (
                "new_sun_billboard_alpha",
                "de47f31edd3b479dbc7fded3beeec4c33e68125c4bd36c77cf3691e9543f3291",
                "97960894e18149c37bd02786ee11ee7b",
                ("inSampler0",),
                (),
                "texture_alpha",
            ),
            (
                "new_sun_vertex_alpha",
                "e041134742e4b530334a6e3dbb519f9910aa785d17ad11b186896817d6ec76b0",
                "5e4ce80dd829fb6c4e17892e174a622d",
                ("inSampler0",),
                ("inVariable0",),
                "texture_alpha_times_vertex_alpha",
            ),
            (
                "new_sun_vertex_masked",
                "9f6918b3cc4e19f7ead38072f07a117489ec931b64c41d47bcbd179b134226e5",
                "1f5d38798722b5fcc2bd472978c93de9",
                ("inSampler0",),
                ("inVariable0",),
                "one_minus_vertex_alpha_over_texture_alpha",
            ),
        )
        for (
            profile_id,
            shader_hash,
            shader_id,
            samplers,
            variables,
            opacity_expression,
        ) in variants:
            with self.subTest(profile=profile_id):
                profile = shader_preview_profile(
                    shader_hash,
                    samplers,
                    shader_object_id=shader_id,
                    variable_signature=variables,
                )
                self.assertEqual(profile_id, profile.profile_id)
                self.assertEqual("proven", profile.confidence)
                self.assertEqual("inSampler0", profile.base_color_sampler)
                self.assertEqual("inSampler0", profile.opacity_sampler)
                self.assertEqual(
                    opacity_expression,
                    profile.opacity_expression,
                )
                self.assertEqual("BLENDED", profile.surface_render_method)

        vertex_masked = shader_preview_profile(
            variants[2][1],
            variants[2][3],
            shader_object_id=variants[2][2],
            variable_signature=variants[2][4],
        )
        self.assertEqual(
            2.0,
            vertex_masked.base_color_vertex_color_power,
        )

        layered = shader_preview_profile(
            "73d8b758bef9520c54cf5b90f0d2b552f886f152fc02cdabe90495d6d25d8802",
            (
                "inSampler2",
                "inSampler3",
                "inSampler4",
                "inSampler5",
                "inSampler1",
                "inSampler6",
                "inSampler7",
                "inSampler0",
            ),
            shader_object_id="2da9b02ce6f215167b66522f0d88082b",
            variable_signature=(
                "inVariable3",
                "inVariable4",
                "inVariable5",
                "inVariable6",
                "inVariable7",
                "inVariable8",
                "inVariable9",
                "inVariable1",
                "inVariable2",
                "inVariable0",
            ),
        )
        self.assertEqual("new_sun_layered_sky", layered.profile_id)
        self.assertEqual(
            "new_sun_layered_sky",
            layered.preview_composition,
        )
        self.assertEqual(
            "primary sky color",
            layered.role_for_sampler("inSampler2"),
        )
        self.assertTrue(layered.sampler_uses_non_color_data("inSampler5"))

        mismatched = shader_preview_profile(
            variants[0][1],
            variants[0][3],
            shader_object_id=variants[0][2],
            variable_signature=("inVariable0",),
        )
        self.assertIsNone(mismatched.profile_id)

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
            ("c1bdc521d39e4d5918ba0138950f93b03bc0171e524bebf3280c4a71f438d213"),
            ("inSampler0", "inSampler1"),
            shader_object_id="671ca18ce63e7acc921d4fe785366e46",
            variable_signature=(),
        )
        self.assertEqual("character_hair_deferred", profile.profile_id)
        self.assertEqual("inSampler0", profile.base_color_sampler)
        self.assertEqual("inSampler0", profile.opacity_sampler)
        self.assertFalse(profile.normal_y_flip)
        self.assertEqual(
            "base color + opacity",
            profile.role_for_sampler("inSampler0"),
        )
        self.assertIsNone(
            shader_preview_profile(
                None,
                ("inSampler0", "inSampler1"),
                shader_object_id="671ca18ce63e7acc921d4fe785366e46",
                variable_signature=(),
            ).profile_id
        )

    def test_packed_lightmap_sample_decode(self) -> None:
        indirect, sun_occlusion = decode_packed_lightmap_sample((0.5, 0.5, 0.25, 0.5))
        self.assertEqual((1.0, 1.0, 1.0), indirect)
        self.assertEqual(0.25, sun_occlusion)

        indirect, sun_occlusion = decode_packed_lightmap_sample((1.0, 0.0, 2.0, 1.0))
        self.assertEqual((4.0, 4.0, 2.25), indirect)
        self.assertEqual(1.0, sun_occlusion)

    def test_shader_profile_requires_exact_hash_and_sampler_signature(
        self,
    ) -> None:
        shader_hash = "300b90c5c33795d92e8feda85c29cbe817fe376ac0e3b0876e022478fbdb1bd4"
        self.assertFalse(
            shader_preview_profile(
                shader_hash,
                ("inSampler0", "inSampler1", "inSampler_LM"),
            ).flip_diffuse_v
        )
        self.assertFalse(shader_preview_profile(None, ()).flip_diffuse_v)
        self.assertIsNone(shader_preview_profile(None, ()).base_color_sampler)

    def test_shader_and_render_effect_graph(self) -> None:
        shader_object = bytes(8) + b"SHO\x08"
        shader_payload = (
            bytes((0, 0)) + struct.pack(">II", 5, len(shader_object)) + shader_object
        )

        render_state = bytes((0,)) + struct.pack(
            ">iiBiiBBBHi", 1, 5, 1, 3, 7, 0, 0, 0, 0xFFFF, 17
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
        self.assertEqual(
            (3, 0, 0, 1),
            (
                binding.filter_mode,
                binding.address_u,
                binding.address_v,
                binding.anisotropy,
            ),
        )

        variable = technique.variable_bindings[0]
        self.assertEqual("LMUVShift", variable.name)
        self.assertEqual((0.5, 0.25, 0.125, 0.75), variable.value)
        self.assertTrue(variable.animator.is_null)
        self.assertEqual("inVariable0", variable.shader_input)


@unittest.skipUnless(bpy is not None, "requires Blender")
class MaterialNodeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.material = bpy.data.materials.new("KZ2_NormalNodeTest")
        self.nodes = self.material.node_tree.nodes
        self.nodes.clear()
        self.principled = self.nodes.new("ShaderNodeBsdfPrincipled")
        self.texture = self.nodes.new("ShaderNodeTexImage")
        self.texture["kz2_sampler"] = "normal"
        self.image = bpy.data.images.new("KZ2_NormalNodeImage", 1, 1)
        self.image.colorspace_settings.name = "Non-Color"
        self.texture.image = self.image

    def tearDown(self) -> None:
        bpy.data.materials.remove(self.material)
        bpy.data.images.remove(self.image)

    def test_tangent_normal_compensates_for_imported_v_flip(self) -> None:
        from io_scene_killzone2.import_materials import MaterialBuilder

        warnings: list[str] = []
        builder = MaterialBuilder(
            decoder=None,
            warnings=warnings,
            flip_uv_v=True,
        )
        profile = ShaderPreviewProfile(
            profile_id="normal-test",
            confidence="proven",
            normal_sampler="normal",
            normal_space="tangent",
            normal_y_flip=False,
        )

        builder._connect_profile_normal(
            self.nodes,
            self.material.node_tree,
            self.principled,
            profile,
            {"normal": self.texture},
            self.material,
        )

        self.assertEqual([], warnings)
        normal_map = self.nodes["KZ2_NormalMap"]
        self.assertEqual("TANGENT", normal_map.space)
        self.assertEqual("DiffuseUV", normal_map.uv_map)
        self.assertTrue(self.principled.inputs["Normal"].is_linked)
        self.assertEqual(
            normal_map.name,
            self.principled.inputs["Normal"].links[0].from_node.name,
        )
        self.assertEqual(
            "KZ2_NormalColor",
            normal_map.inputs["Color"].links[0].from_node.name,
        )
        invert = self.nodes["KZ2_NormalYInvert"]
        self.assertEqual("SUBTRACT", invert.operation)
        self.assertEqual(1.0, invert.inputs[0].default_value)
        self.assertEqual(
            "KZ2_NormalChannels",
            invert.inputs[1].links[0].from_node.name,
        )
        self.assertTrue(self.material["kz2_preview_normal_y_flip"])

    def test_object_normal_keeps_proven_source_y_sign(self) -> None:
        from io_scene_killzone2.import_materials import MaterialBuilder

        warnings: list[str] = []
        builder = MaterialBuilder(
            decoder=None,
            warnings=warnings,
            flip_uv_v=True,
        )
        profile = ShaderPreviewProfile(
            profile_id="normal-test",
            confidence="proven",
            normal_sampler="normal",
            normal_space="object",
            normal_y_flip=False,
        )

        builder._connect_profile_normal(
            self.nodes,
            self.material.node_tree,
            self.principled,
            profile,
            {"normal": self.texture},
            self.material,
        )

        self.assertEqual([], warnings)
        normal_map = self.nodes["KZ2_NormalMap"]
        self.assertEqual("OBJECT", normal_map.space)
        self.assertEqual(
            self.texture.name,
            normal_map.inputs["Color"].links[0].from_node.name,
        )
        self.assertIsNone(self.nodes.get("KZ2_NormalYInvert"))
        self.assertFalse(self.material["kz2_preview_normal_y_flip"])


if __name__ == "__main__":
    unittest.main()
