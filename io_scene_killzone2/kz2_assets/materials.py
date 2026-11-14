"""Render-effect and shader-resource graph parsing."""

from __future__ import annotations

import hashlib
import math
import struct
from dataclasses import dataclass

from .binary import BinaryReader
from .geometry import read_resource_prefix
from .model import (
    RenderEffect,
    RenderState,
    RenderTechnique,
    ShaderResource,
    ShaderVariable,
    TextureBinding,
)


@dataclass(frozen=True, slots=True)
class ShaderPreviewProfile:
    profile_id: str | None = None
    confidence: str = "heuristic"
    base_color_sampler: str | None = None
    base_color_uv_map: str = "DiffuseUV"
    secondary_base_color_sampler: str | None = None
    secondary_base_color_uv_map: str = "UV0"
    opacity_sampler: str | None = None
    normal_sampler: str | None = None
    detail_normal_sampler: str | None = None
    normal_space: str | None = None
    # Shader-space inversion before compensating for the imported UV-V basis.
    normal_y_flip: bool | None = None
    material_sampler: str | None = None
    reflection_sampler: str | None = None
    specular_power_channel: str | None = None
    specular_power_scale: float = 1.0
    specular_intensity_channel: str | None = None
    flip_diffuse_v: bool = False

    def role_for_sampler(self, sampler: str) -> str | None:
        roles: list[str] = []
        if sampler == self.base_color_sampler:
            roles.append("base color")
        if sampler == self.secondary_base_color_sampler:
            roles.append("secondary base color")
        if sampler == self.opacity_sampler:
            roles.append("opacity")
        if sampler == self.normal_sampler:
            roles.append(f"{self.normal_space or 'unknown-space'} normal")
        if sampler == self.detail_normal_sampler:
            roles.append("detail normal")
        if sampler == self.material_sampler:
            roles.append("packed material")
        if sampler == self.reflection_sampler:
            roles.append("IBL lookup")
        return " + ".join(roles) or None


_DEFAULT_SHADER_PREVIEW_PROFILE = ShaderPreviewProfile()


@dataclass(frozen=True, slots=True)
class _ShaderPreviewProfileEntry:
    sampler_signature: tuple[str, ...]
    profile: ShaderPreviewProfile
    shader_body_sha256: str | None = None
    shader_object_id: str | None = None
    variable_signature: tuple[str, ...] | None = None
    technique_index: int = 0


_SHADER_PREVIEW_PROFILES = (
    _ShaderPreviewProfileEntry(
        shader_body_sha256=(
            "4162c929f17afe2645b68ca16e44d1f875e1b23ff54fbdb8ceeb1b9e18dae24a"
        ),
        shader_object_id="c4f7e7bcb20c3fc6c54b2e469dcb264b",
        sampler_signature=("inSampler0", "inSampler1"),
        variable_signature=(),
        profile=ShaderPreviewProfile(
            profile_id="isa_cruiser_masked_deferred",
            confidence="proven",
            base_color_sampler="inSampler1",
            base_color_uv_map="DiffuseUV",
            flip_diffuse_v=True,
        ),
    ),
    _ShaderPreviewProfileEntry(
        shader_body_sha256=(
            "d0bf1912b3998059fc02279daae5007291ba904d5e86265cb59b934e475898e6"
        ),
        shader_object_id="1519aa1c31d97763f309fff78341ad0a",
        sampler_signature=(
            "inSampler5",
            "inSampler6",
            "inSampler0",
            "inSampler1",
            "inSampler2",
            "inSampler3",
            "inSampler4",
        ),
        variable_signature=("inVariable0",),
        profile=ShaderPreviewProfile(
            profile_id="isa_cruiser_layered_deferred",
            confidence="proven",
            base_color_sampler="inSampler1",
            normal_sampler="inSampler5",
            normal_space="tangent",
            material_sampler="inSampler3",
            flip_diffuse_v=True,
        ),
    ),
    _ShaderPreviewProfileEntry(
        shader_body_sha256=(
            "52ff04908ef8c4e30efb9d463aeb4b7e16a28afd658173e9da4c2614d13bd134"
        ),
        shader_object_id="377c0e533f5a2c46c9ecafca3a6f8709",
        sampler_signature=(
            "inSampler5",
            "inSampler6",
            "inSampler1",
            "inSampler0",
            "inSampler2",
            "inSampler3",
            "inSampler4",
        ),
        variable_signature=("inVariable0",),
        profile=ShaderPreviewProfile(
            profile_id="isa_cruiser_layered_deferred",
            confidence="proven",
            base_color_sampler="inSampler1",
            normal_sampler="inSampler5",
            normal_space="tangent",
            material_sampler="inSampler3",
            flip_diffuse_v=True,
        ),
    ),
    _ShaderPreviewProfileEntry(
        shader_body_sha256=(
            "4101f53b769cb64337e2d5fa99b28e666cc8321686fb48704cff13798c4d7eac"
        ),
        shader_object_id="37fda7bbada90e7e8522211c4278690d",
        sampler_signature=(
            "inSampler5",
            "inSampler6",
            "inSampler0",
            "inSampler1",
            "inSampler2",
            "inSampler3",
            "inSampler4",
        ),
        variable_signature=("inVariable0",),
        profile=ShaderPreviewProfile(
            profile_id="isa_cruiser_layered_deferred",
            confidence="proven",
            base_color_sampler="inSampler1",
            normal_sampler="inSampler5",
            normal_space="tangent",
            material_sampler="inSampler3",
            flip_diffuse_v=True,
        ),
    ),
    _ShaderPreviewProfileEntry(
        shader_body_sha256=(
            "ada42a8d61bb7f771c2cf907d04464cab2ae95f2298c87181b9446fd70be8f02"
        ),
        shader_object_id="631dc38edec0712bbb2713bb8a048023",
        sampler_signature=(
            "inSampler3",
            "inSampler4",
            "inSampler0",
            "inSampler1",
            "inSampler2",
        ),
        variable_signature=(),
        profile=ShaderPreviewProfile(
            profile_id="isa_intruder_detail_deferred",
            confidence="proven",
            base_color_sampler="inSampler0",
            base_color_uv_map="DiffuseUV",
            flip_diffuse_v=True,
        ),
    ),
    _ShaderPreviewProfileEntry(
        shader_body_sha256=(
            "63dddec576e98fd952ab7800bfeda3724d805aed72d4887feb2267ba8ceda1a5"
        ),
        shader_object_id="c69e155a3ee8bf78fb69eb313bd02765",
        sampler_signature=(
            "inSampler3",
            "inSampler4",
            "inSampler0",
            "inSampler1",
            "inSampler2",
        ),
        variable_signature=(),
        profile=ShaderPreviewProfile(
            profile_id="isa_intruder_detail_deferred",
            confidence="proven",
            base_color_sampler="inSampler0",
            base_color_uv_map="DiffuseUV",
            flip_diffuse_v=True,
        ),
    ),
    _ShaderPreviewProfileEntry(
        shader_body_sha256=(
            "efdfa954d7e86a54650086a4d288eecfe1dde75ac1d7f187dd483b2b19e075d7"
        ),
        shader_object_id="454c34802c8ecfd0f88b37607d5b615b",
        sampler_signature=(
            "inSampler3",
            "inSampler0",
            "inSampler1",
            "inSampler2",
        ),
        variable_signature=(),
        profile=ShaderPreviewProfile(
            profile_id="isa_intruder_deferred",
            confidence="proven",
            base_color_sampler="inSampler0",
            base_color_uv_map="DiffuseUV",
            flip_diffuse_v=True,
        ),
    ),
    _ShaderPreviewProfileEntry(
        shader_object_id="c54604143359f6cb1e7766c8fa2a3b38",
        sampler_signature=(
            "inSampler3",
            "inSampler0",
            "inSampler1",
            "inSampler2",
        ),
        variable_signature=(),
        profile=ShaderPreviewProfile(
            profile_id="hgh_dropship_deferred",
            confidence="proven",
            base_color_sampler="inSampler0",
            normal_sampler="inSampler3",
            normal_space="tangent",
            normal_y_flip=False,
            material_sampler="inSampler1",
            reflection_sampler="inSampler2",
            specular_power_channel="G",
            specular_power_scale=98.0,
            specular_intensity_channel="B",
        ),
    ),
    _ShaderPreviewProfileEntry(
        shader_body_sha256=(
            "b26212b2b1a9d326fff9ab939f5f2b52aa1a755de9a4dacb393aa064bc5790ab"
        ),
        shader_object_id="2ee5e3ff3a42dd5cd1f33374891901f0",
        sampler_signature=(
            "inSampler3",
            "inSampler0",
            "inSampler1",
            "inSampler2",
        ),
        variable_signature=(),
        profile=ShaderPreviewProfile(
            profile_id="hgh_character_deferred_g24_5",
            confidence="proven",
            base_color_sampler="inSampler0",
            normal_sampler="inSampler3",
            normal_space="tangent",
            normal_y_flip=False,
            material_sampler="inSampler1",
            reflection_sampler="inSampler2",
            specular_power_channel="G",
            specular_power_scale=24.5,
            specular_intensity_channel="B",
        ),
    ),
    _ShaderPreviewProfileEntry(
        shader_body_sha256=(
            "9c95d347249ae1ecb6bda6bbff7d0f3f159d5b59c342e42296e700027045ed5a"
        ),
        shader_object_id="3d23b1bce366078a44c0bfe6ac133f61",
        sampler_signature=(
            "inSampler3",
            "inSampler0",
            "inSampler1",
            "inSampler2",
        ),
        variable_signature=(),
        profile=ShaderPreviewProfile(
            profile_id="hgh_character_deferred_r14_7024",
            confidence="proven",
            base_color_sampler="inSampler0",
            normal_sampler="inSampler3",
            normal_space="tangent",
            normal_y_flip=False,
            material_sampler="inSampler1",
            reflection_sampler="inSampler2",
            specular_power_channel="R",
            specular_power_scale=14.7024,
            specular_intensity_channel="B",
        ),
    ),
    _ShaderPreviewProfileEntry(
        shader_body_sha256=(
            "5201239dbeafa941605bde1b0eb3f0e2f8b49342334400592395b96519e6b36c"
        ),
        shader_object_id="1de0cb0694403a758185efc552229c6d",
        sampler_signature=(
            "inSampler3",
            "inSampler0",
            "inSampler1",
            "inSampler2",
        ),
        variable_signature=(),
        profile=ShaderPreviewProfile(
            profile_id="hgh_character_deferred_r49",
            confidence="proven",
            base_color_sampler="inSampler0",
            normal_sampler="inSampler3",
            normal_space="tangent",
            normal_y_flip=False,
            material_sampler="inSampler1",
            reflection_sampler="inSampler2",
            specular_power_channel="R",
            specular_power_scale=49.0,
            specular_intensity_channel="B",
        ),
    ),
    _ShaderPreviewProfileEntry(
        shader_body_sha256=(
            "b6079e1145b9b727936891c66c57a247ed0868fdc33b05c45b0e2030a696a7fb"
        ),
        shader_object_id="ea746cea3656762cd8b70a78060d9654",
        sampler_signature=(
            "inSampler3",
            "inSampler0",
            "inSampler1",
            "inSampler2",
        ),
        variable_signature=(),
        profile=ShaderPreviewProfile(
            profile_id="hgh_character_deferred_r49",
            confidence="proven",
            base_color_sampler="inSampler0",
            normal_sampler="inSampler3",
            normal_space="tangent",
            normal_y_flip=False,
            material_sampler="inSampler1",
            reflection_sampler="inSampler2",
            specular_power_channel="R",
            specular_power_scale=49.0,
            specular_intensity_channel="B",
        ),
    ),
    _ShaderPreviewProfileEntry(
        shader_body_sha256=(
            "856b7cea143b22b4236c2e64f9ede01a5739afdf2d737d65c816713f928215cb"
        ),
        shader_object_id="37118f7b07d5263cfeee4f6d6b6a7ba4",
        sampler_signature=(
            "inSampler3",
            "inSampler4",
            "inSampler0",
            "inSampler1",
            "inSampler2",
        ),
        variable_signature=(),
        profile=ShaderPreviewProfile(
            profile_id="hgh_tank_layered_deferred",
            confidence="proven",
            base_color_sampler="inSampler0",
            normal_sampler="inSampler3",
            detail_normal_sampler="inSampler4",
            normal_space="tangent",
            normal_y_flip=False,
            material_sampler="inSampler1",
            reflection_sampler="inSampler2",
            specular_power_channel="G",
            specular_power_scale=49.0,
            specular_intensity_channel="B",
        ),
    ),
    _ShaderPreviewProfileEntry(
        shader_body_sha256=(
            "102252b294a07c0df6a8ec5fb3fdc46b11e928ab045e407df87bd480886e4e2f"
        ),
        shader_object_id="90f8c2677ed634533711eb2bfed57af0",
        sampler_signature=(
            "inSampler3",
            "inSampler4",
            "inSampler0",
            "inSampler1",
            "inSampler2",
        ),
        variable_signature=(),
        profile=ShaderPreviewProfile(
            profile_id="hgh_tank_layered_deferred",
            confidence="proven",
            base_color_sampler="inSampler0",
            normal_sampler="inSampler3",
            detail_normal_sampler="inSampler4",
            normal_space="tangent",
            normal_y_flip=False,
            material_sampler="inSampler1",
            reflection_sampler="inSampler2",
            specular_power_channel="G",
            specular_power_scale=49.0,
            specular_intensity_channel="B",
        ),
    ),
    _ShaderPreviewProfileEntry(
        shader_body_sha256=(
            "2e06729d23b996c93e525ce0a89f643e3ca196bdbe0ea6c3959f302fb3ab07ad"
        ),
        shader_object_id="6cf769273cef73fc83c4b5f780adfebc",
        sampler_signature=(
            "inSampler3",
            "inSampler4",
            "inSampler0",
            "inSampler1",
            "inSampler2",
        ),
        variable_signature=(),
        profile=ShaderPreviewProfile(
            profile_id="hgh_tank_layered_deferred",
            confidence="proven",
            base_color_sampler="inSampler0",
            normal_sampler="inSampler3",
            detail_normal_sampler="inSampler4",
            normal_space="tangent",
            normal_y_flip=False,
            material_sampler="inSampler1",
            reflection_sampler="inSampler2",
            specular_power_channel="G",
            specular_power_scale=49.0,
            specular_intensity_channel="B",
        ),
    ),
    _ShaderPreviewProfileEntry(
        shader_body_sha256=(
            "6f8cb5a4e7ead10942835fe7335f4b75e4e887ab1f5188fb0fa40f89f149519f"
        ),
        shader_object_id="e03466b9264b1c8a6e663a39cb0f511e",
        sampler_signature=(
            "inSampler4",
            "inSampler0",
            "inSampler1",
            "inSampler2",
            "inSampler3",
        ),
        variable_signature=("inVariable0",),
        profile=ShaderPreviewProfile(
            profile_id="hgh_tank_track_deferred",
            confidence="proven",
            base_color_sampler="inSampler0",
            secondary_base_color_sampler="inSampler1",
            normal_sampler="inSampler4",
            normal_space="tangent",
            normal_y_flip=False,
            material_sampler="inSampler2",
            reflection_sampler="inSampler3",
            specular_power_channel="R",
            specular_power_scale=49.0,
            specular_intensity_channel="B",
        ),
    ),
    _ShaderPreviewProfileEntry(
        shader_body_sha256=(
            "a98c18009a2d8cf814ed0fe73877e9925c63a76b9ddba10836e59215bbbbbade"
        ),
        shader_object_id="b0a8b52eb0560707ecc42dc8c1ec7f88",
        sampler_signature=("inSampler2", "inSampler0", "inSampler1"),
        variable_signature=(),
        profile=ShaderPreviewProfile(
            profile_id="hgh_tank_compact_deferred",
            confidence="proven",
            base_color_sampler="inSampler0",
            normal_sampler="inSampler2",
            normal_space="tangent",
            normal_y_flip=False,
            material_sampler="inSampler1",
            specular_power_channel="R",
            specular_power_scale=49.0,
            specular_intensity_channel="B",
        ),
    ),
    _ShaderPreviewProfileEntry(
        shader_body_sha256=(
            "0f4a68bb17adc14ca0ebed9f3dcc5a651e42bd8be016bc300369f90e5f74b9e7"
        ),
        shader_object_id="a58e7b007d4d88e2ddfaa61af86a4ab7",
        sampler_signature=("inSampler0", "inSampler1"),
        variable_signature=(),
        profile=ShaderPreviewProfile(
            profile_id="hgh_tank_packed_g49",
            confidence="proven",
            base_color_sampler="inSampler0",
            material_sampler="inSampler1",
            specular_power_channel="G",
            specular_power_scale=49.0,
            specular_intensity_channel="B",
        ),
    ),
    _ShaderPreviewProfileEntry(
        shader_body_sha256=(
            "9ca4f7b18d8c62d21cb9723f7489389a03a2dcde7c4671b94f3a50303cdb1f26"
        ),
        shader_object_id="5c879214cf2789ce4728d5de9d9cbc16",
        sampler_signature=("inSampler0", "inSampler1"),
        variable_signature=(),
        profile=ShaderPreviewProfile(
            profile_id="hgh_tank_packed_r49",
            confidence="proven",
            base_color_sampler="inSampler0",
            material_sampler="inSampler1",
            specular_power_channel="R",
            specular_power_scale=49.0,
            specular_intensity_channel="B",
        ),
    ),
    _ShaderPreviewProfileEntry(
        shader_body_sha256=(
            "b971098ebeb5904b705406b56b0417c477db425bacb78676ae91c0e370191db7"
        ),
        shader_object_id="c4de041dda84841306f1f2921c995c89",
        sampler_signature=(
            "inSampler2",
            "inSampler3",
            "inSampler0",
            "inSampler1",
        ),
        variable_signature=(),
        profile=ShaderPreviewProfile(
            profile_id="isa_tank_layered_deferred",
            confidence="proven",
            base_color_sampler="inSampler0",
            normal_sampler="inSampler2",
            detail_normal_sampler="inSampler3",
            normal_space="tangent",
            normal_y_flip=False,
            material_sampler="inSampler1",
            specular_power_channel="G",
            specular_power_scale=39.1904,
            specular_intensity_channel="B",
        ),
    ),
    _ShaderPreviewProfileEntry(
        shader_body_sha256=(
            "7402904c59530bb8e54c2aac9a8cdd8c1a2966d6a00e52b1552d2d23c5109c6a"
        ),
        shader_object_id="7a83011fface8f01eca0b3604fb22f5a",
        sampler_signature=(
            "inSampler2",
            "inSampler3",
            "inSampler0",
            "inSampler1",
        ),
        variable_signature=(),
        profile=ShaderPreviewProfile(
            profile_id="isa_tank_layered_deferred",
            confidence="proven",
            base_color_sampler="inSampler0",
            normal_sampler="inSampler2",
            detail_normal_sampler="inSampler3",
            normal_space="tangent",
            normal_y_flip=False,
            material_sampler="inSampler1",
            specular_power_channel="G",
            specular_power_scale=39.1904,
            specular_intensity_channel="B",
        ),
    ),
    _ShaderPreviewProfileEntry(
        shader_body_sha256=(
            "7663f616f936f6018008dc0fd7e96b0aa0c5cca61d28b6ec864ba8347afc4c2a"
        ),
        shader_object_id="710036fa5d9aaddd90279f934e1c7275",
        sampler_signature=(
            "inSampler2",
            "inSampler3",
            "inSampler0",
            "inSampler1",
        ),
        variable_signature=(),
        profile=ShaderPreviewProfile(
            profile_id="isa_tank_layered_deferred",
            confidence="proven",
            base_color_sampler="inSampler0",
            normal_sampler="inSampler2",
            detail_normal_sampler="inSampler3",
            normal_space="tangent",
            normal_y_flip=False,
            material_sampler="inSampler1",
            specular_power_channel="G",
            specular_power_scale=39.1904,
            specular_intensity_channel="B",
        ),
    ),
    _ShaderPreviewProfileEntry(
        shader_body_sha256=(
            "ab8b28b5a81fd5f7ac88d818e2165f9bc87fa9436f92496a4fac7305fd9744de"
        ),
        shader_object_id="0a1b584d6a258c6dc25610e3289d5406",
        sampler_signature=(
            "inSampler2",
            "inSampler3",
            "inSampler1",
            "inSampler0",
        ),
        variable_signature=(),
        profile=ShaderPreviewProfile(
            profile_id="isa_tank_layered_deferred",
            confidence="proven",
            base_color_sampler="inSampler0",
            normal_sampler="inSampler2",
            detail_normal_sampler="inSampler3",
            normal_space="tangent",
            normal_y_flip=False,
            material_sampler="inSampler1",
            specular_power_channel="G",
            specular_power_scale=39.1904,
            specular_intensity_channel="B",
        ),
    ),
    _ShaderPreviewProfileEntry(
        shader_body_sha256=(
            "5083bb26725fdb4a33ee9249c58b0d6a79b67cfbee5658c074613928aacbd331"
        ),
        shader_object_id="ef5c0a58695ab51ed3bd2e44d1ea0c91",
        sampler_signature=(
            "inSampler2",
            "inSampler3",
            "inSampler1",
            "inSampler0",
        ),
        variable_signature=(),
        profile=ShaderPreviewProfile(
            profile_id="isa_tank_layered_deferred",
            confidence="proven",
            base_color_sampler="inSampler0",
            normal_sampler="inSampler2",
            detail_normal_sampler="inSampler3",
            normal_space="tangent",
            normal_y_flip=False,
            material_sampler="inSampler1",
            specular_power_channel="G",
            specular_power_scale=39.1904,
            specular_intensity_channel="B",
        ),
    ),
    _ShaderPreviewProfileEntry(
        shader_body_sha256=(
            "f1898eed7929bc16990619e97b61d26db72df54d0974721b575460fd0429706f"
        ),
        shader_object_id="2283f987591d17d65f60db32222736bb",
        sampler_signature=(
            "inSampler3",
            "inSampler0",
            "inSampler1",
            "inSampler2",
        ),
        variable_signature=("inVariable0",),
        profile=ShaderPreviewProfile(
            profile_id="isa_tank_track_deferred",
            confidence="proven",
            base_color_sampler="inSampler0",
            secondary_base_color_sampler="inSampler1",
            normal_sampler="inSampler3",
            normal_space="tangent",
            normal_y_flip=False,
            material_sampler="inSampler2",
            specular_power_channel="R",
            specular_power_scale=39.1904,
            specular_intensity_channel="B",
        ),
    ),
    _ShaderPreviewProfileEntry(
        shader_body_sha256=(
            "8af06e77b62d7f458fe4d44cdeebb7920f0775c6e06cae624581fcbea292bfd4"
        ),
        shader_object_id="d8de6701c66b7caece3b70e3555b198b",
        sampler_signature=("inSampler2", "inSampler0", "inSampler1"),
        variable_signature=(),
        profile=ShaderPreviewProfile(
            profile_id="isa_tank_compact_deferred",
            confidence="proven",
            base_color_sampler="inSampler0",
            normal_sampler="inSampler2",
            normal_space="tangent",
            normal_y_flip=False,
            material_sampler="inSampler1",
            specular_power_channel="R",
            specular_power_scale=49.0,
            specular_intensity_channel="B",
        ),
    ),
    _ShaderPreviewProfileEntry(
        shader_body_sha256=(
            "300b90c5c33795d92e8feda85c29cbe817fe376ac0e3b0876e022478fbdb1bd4"
        ),
        sampler_signature=("inSampler1", "inSampler0", "inSampler_LM"),
        profile=ShaderPreviewProfile(
            profile_id="reflection_room_atlas",
            confidence="proven",
            base_color_sampler="inSampler0",
            flip_diffuse_v=True,
        ),
    ),
    _ShaderPreviewProfileEntry(
        shader_body_sha256=(
            "bb5b6b36c051125d587d91fbd50fa8ceaeddb26bdcbb4eed8f4aceeeb9652adc"
        ),
        sampler_signature=(
            "inSampler1",
            "inSampler0",
            "inSampler_LM",
            "inSampler2",
        ),
        profile=ShaderPreviewProfile(
            profile_id="reflection_room_linear_albedo",
            confidence="proven",
            base_color_sampler="inSampler0",
        ),
    ),
    _ShaderPreviewProfileEntry(
        shader_object_id="ce82c6eac4ef761a6c5e553ef8b425ab",
        sampler_signature=("inSampler0", "inSampler1", "inSampler2"),
        profile=ShaderPreviewProfile(
            profile_id="m82_conventional_deferred",
            confidence="proven",
            base_color_sampler="inSampler0",
            normal_sampler="inSampler1",
            normal_space="tangent",
            material_sampler="inSampler2",
        ),
    ),
    _ShaderPreviewProfileEntry(
        shader_object_id="91ba5cc96b216995a84b335b093d3cd3",
        sampler_signature=("inSampler0", "inSampler1", "inSampler2"),
        profile=ShaderPreviewProfile(
            profile_id="sev_mechanical_object_space",
            confidence="proven",
            base_color_sampler="inSampler1",
            normal_sampler="inSampler2",
            normal_space="object",
            material_sampler="inSampler0",
        ),
    ),
    _ShaderPreviewProfileEntry(
        shader_object_id="5ffa940d43696f3321eee75691e23b15",
        sampler_signature=("inSampler0", "inSampler1", "inSampler2"),
        profile=ShaderPreviewProfile(
            profile_id="sev_mechanical_object_space",
            confidence="proven",
            base_color_sampler="inSampler1",
            normal_sampler="inSampler2",
            normal_space="object",
            material_sampler="inSampler0",
        ),
    ),
    _ShaderPreviewProfileEntry(
        shader_object_id="671ca18ce63e7acc921d4fe785366e46",
        sampler_signature=("inSampler0", "inSampler1"),
        profile=ShaderPreviewProfile(
            profile_id="hair_alpha_tangent_normal",
            confidence="proven",
            base_color_sampler="inSampler0",
            opacity_sampler="inSampler0",
            normal_sampler="inSampler1",
            normal_space="tangent",
        ),
    ),
    _ShaderPreviewProfileEntry(
        shader_object_id="1126768122f73410dad7fb2d14155704",
        sampler_signature=("inSampler0",),
        profile=ShaderPreviewProfile(
            profile_id="hair_color_only",
            confidence="proven",
            base_color_sampler="inSampler0",
        ),
    ),
)


def blender_roughness_from_specular_power(
    encoded_power: float,
    *,
    power_scale: float,
) -> float:
    specular_power = max(0.0, encoded_power * power_scale)
    return math.sqrt(2.0 / (specular_power + 2.0))


def shader_preview_profile(
    shader_body_sha256: str | None,
    sampler_signature: tuple[str, ...],
    *,
    shader_object_id: str | None = None,
    variable_signature: tuple[str, ...] | None = None,
    technique_index: int = 0,
) -> ShaderPreviewProfile:
    for entry in _SHADER_PREVIEW_PROFILES:
        if entry.technique_index != technique_index:
            continue
        if entry.sampler_signature != sampler_signature:
            continue
        if (
            entry.shader_body_sha256 is not None
            and entry.shader_body_sha256 != shader_body_sha256
        ):
            continue
        if (
            entry.shader_object_id is not None
            and entry.shader_object_id != shader_object_id
        ):
            continue
        if (
            entry.variable_signature is not None
            and entry.variable_signature != variable_signature
        ):
            continue
        return entry.profile
    return _DEFAULT_SHADER_PREVIEW_PROFILE


def decode_packed_lightmap_sample(
    packed: tuple[float, float, float, float],
) -> tuple[tuple[float, float, float], float]:
    u, v, sun_occlusion, luminance = packed
    red = _saturate(luminance + 0.375 * u - 0.125 * v - 0.125)
    green = _saturate(luminance - 0.125 * u - 0.125 * v + 0.125)
    blue = _saturate(luminance - 0.125 * u + 0.375 * v - 0.125)
    return (
        (4.0 * red * red, 4.0 * green * green, 4.0 * blue * blue),
        _saturate(sun_occlusion),
    )


def _saturate(value: float) -> float:
    return min(1.0, max(0.0, value))


def blender_lightmap_uv_mapping(
    values: tuple[float, float, float, float],
    *,
    flip_v: bool,
) -> tuple[
    tuple[float, float, float],
    tuple[float, float, float],
]:
    scale_u, scale_v, offset_u, offset_v = values
    if flip_v:
        offset_v = 1.0 - offset_v - scale_v
    return (
        (offset_u, offset_v, 0.0),
        (scale_u, scale_v, 1.0),
    )


def parse_render_effect(decoder, obj, reader: BinaryReader) -> RenderEffect:
    prefix = read_resource_prefix(decoder, reader)
    animator = decoder.core.read_reference(reader)
    technique_count = reader.count("render-technique count")
    techniques = tuple(
        _read_render_technique(decoder, reader) for _ in range(technique_count)
    )
    return RenderEffect(prefix, animator, techniques, reader.read(reader.remaining))


def parse_shader_resource(decoder, obj, reader: BinaryReader) -> ShaderResource:
    prefix = read_resource_prefix(decoder, reader)
    ibl_shading_mode = reader.u32()
    shader_object_size = reader.u32()
    shader_object = reader.read(shader_object_size)
    if reader.remaining:
        reader.fail(
            "shader resource has trailing bytes",
            expected=0,
            observed=reader.remaining,
        )
    if len(shader_object) < 12 or shader_object[8:12] != b"SHO\x08":
        reader.fail(
            "invalid shader object signature",
            expected=b"SHO\x08",
            observed=shader_object[8:12],
        )
    return ShaderResource(
        prefix=prefix,
        ibl_shading_mode=ibl_shading_mode,
        shader_object=shader_object,
        body_sha256=hashlib.sha256(shader_object).hexdigest(),
    )


def _read_render_technique(decoder, reader: BinaryReader) -> RenderTechnique:
    raw_state = reader.read(27)
    state_reader = BinaryReader.from_bytes(raw_state, source=reader.source)
    render_state = RenderState(
        unknown_flag=state_reader.u8(),
        cull_mode=state_reader.i32(),
        blend_mode=state_reader.i32(),
        write_depth=state_reader.u8(),
        depth_test_func=state_reader.i32(),
        alpha_test_func=state_reader.i32(),
        alpha_test_value=state_reader.u8(),
        enable_depth_bias=state_reader.u8(),
        enable_depth_clamp=state_reader.u8(),
        color_mask=state_reader.u16(),
        technique_type=state_reader.i32(),
        raw=raw_state,
    )
    if render_state.unknown_flag != 0:
        reader.fail(
            "unexpected render-state leading flag",
            expected=0,
            observed=render_state.unknown_flag,
        )
    shader = decoder.core.read_reference(reader)
    texture_count = reader.count("texture-binding count")
    texture_bindings = tuple(
        _read_texture_binding(decoder, reader) for _ in range(texture_count)
    )
    variable_count = reader.count("shader-variable count")
    variable_bindings = tuple(
        _read_shader_variable(decoder, reader) for _ in range(variable_count)
    )
    reflection_sampler_bias = reader.f32()
    return RenderTechnique(
        render_state=render_state,
        shader=shader,
        texture_bindings=texture_bindings,
        variable_bindings=variable_bindings,
        reflection_sampler_bias=reflection_sampler_bias,
    )


def _read_texture_binding(decoder, reader: BinaryReader) -> TextureBinding:
    sampler = decoder.core.read_string_index(reader, "String")
    texture = decoder.core.read_reference(reader)
    mip_bias_bits = reader.u16()
    mip_bias = struct.unpack(">e", mip_bias_bits.to_bytes(2, "big"))[0]
    packed_sampler_state = reader.u16()
    return TextureBinding(
        sampler=sampler,
        texture=texture,
        mip_bias=mip_bias,
        mip_bias_bits=mip_bias_bits,
        packed_sampler_state=packed_sampler_state,
    )


def _read_shader_variable(decoder, reader: BinaryReader) -> ShaderVariable:
    name = decoder.core.read_string_index(reader, "String")
    variable_type = reader.u32()
    raw_value = reader.read(16)
    value_count = {1: 1, 2: 2, 3: 3, 4: 4, 9: 1}.get(variable_type)
    value = (
        struct.unpack(">4f", raw_value)[:value_count]
        if value_count is not None
        else None
    )
    animator = decoder.core.read_reference(reader)
    shader_input = decoder.core.read_string_index(reader, "String")
    return ShaderVariable(
        name=name,
        variable_type=variable_type,
        raw_value=raw_value,
        value=value,
        animator=animator,
        shader_input=shader_input,
    )
