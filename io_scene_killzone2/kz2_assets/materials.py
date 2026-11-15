"""Render-effect and shader-resource graph parsing."""

from __future__ import annotations

import hashlib
import math
import struct
from dataclasses import dataclass

from .binary import BinaryReader
from .character_material_profiles import CHARACTER_SHADER_PROFILE_GROUPS
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
    opacity_expression: str = "texture_alpha"
    base_color_vertex_color_power: float | None = None
    vertex_color_emission_power: float | None = None
    preview_composition: str | None = None
    surface_render_method: str | None = None
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
    retained_sampler_roles: tuple[tuple[str, str], ...] = ()
    non_color_samplers: tuple[str, ...] = ()
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
        roles.extend(
            role
            for retained_sampler, role in self.retained_sampler_roles
            if sampler == retained_sampler
        )
        return " + ".join(roles) or None

    def sampler_uses_non_color_data(self, sampler: str) -> bool:
        return (
            sampler
            in {
                self.normal_sampler,
                self.detail_normal_sampler,
                self.material_sampler,
            }
            or sampler in self.non_color_samplers
        )


_DEFAULT_SHADER_PREVIEW_PROFILE = ShaderPreviewProfile()


@dataclass(frozen=True, slots=True)
class _ShaderPreviewProfileEntry:
    sampler_signature: tuple[str, ...]
    profile: ShaderPreviewProfile
    shader_body_sha256: str | None = None
    shader_object_id: str | None = None
    variable_signature: tuple[str, ...] | None = None
    technique_index: int = 0


def _exact_vehicle_profile(
    shader_body_sha256: str,
    shader_object_id: str,
    sampler_signature: str,
    profile_id: str,
    *,
    variable_signature: str = "",
    base_color_sampler: str,
    secondary_base_color_sampler: str | None = None,
    normal_sampler: str | None = None,
    detail_normal_sampler: str | None = None,
    material_sampler: str | None = None,
    reflection_sampler: str | None = None,
    specular_power_channel: str | None = None,
    specular_power_scale: float | None = None,
    specular_intensity_channel: str | None = None,
    flip_diffuse_v: bool = False,
) -> _ShaderPreviewProfileEntry:
    return _ShaderPreviewProfileEntry(
        shader_body_sha256=shader_body_sha256,
        shader_object_id=shader_object_id,
        sampler_signature=tuple(sampler_signature.split()),
        variable_signature=tuple(variable_signature.split()),
        profile=ShaderPreviewProfile(
            profile_id=profile_id,
            confidence="proven",
            base_color_sampler=base_color_sampler,
            secondary_base_color_sampler=secondary_base_color_sampler,
            normal_sampler=normal_sampler,
            normal_space="tangent" if normal_sampler is not None else None,
            normal_y_flip=False if normal_sampler is not None else None,
            detail_normal_sampler=detail_normal_sampler,
            material_sampler=material_sampler,
            reflection_sampler=reflection_sampler,
            specular_power_channel=specular_power_channel,
            specular_power_scale=specular_power_scale,
            specular_intensity_channel=specular_intensity_channel,
            flip_diffuse_v=flip_diffuse_v,
        ),
    )


_CHARACTER_PROFILE_TEMPLATES = {
    "character_alpha_color": ShaderPreviewProfile(
        profile_id="character_alpha_color",
        confidence="proven",
        base_color_sampler="inSampler0",
        opacity_sampler="inSampler0",
    ),
    "character_color_material": ShaderPreviewProfile(
        profile_id="character_color_material",
        confidence="proven",
        base_color_sampler="inSampler0",
        material_sampler="inSampler1",
    ),
    "character_color_only": ShaderPreviewProfile(
        profile_id="character_color_only",
        confidence="proven",
        base_color_sampler="inSampler0",
    ),
    "character_color_reflection": ShaderPreviewProfile(
        profile_id="character_color_reflection",
        confidence="proven",
        base_color_sampler="inSampler0",
        reflection_sampler="inSampler1",
        non_color_samplers=("inSampler1",),
    ),
    "character_compact_material_deferred": ShaderPreviewProfile(
        profile_id="character_compact_material_deferred",
        confidence="proven",
        base_color_sampler="inSampler0",
        material_sampler="inSampler1",
        reflection_sampler="inSampler2",
        non_color_samplers=("inSampler2",),
    ),
    "character_compact_normal_deferred": ShaderPreviewProfile(
        profile_id="character_compact_normal_deferred",
        confidence="proven",
        base_color_sampler="inSampler0",
        normal_sampler="inSampler2",
        normal_space="tangent",
        normal_y_flip=False,
        material_sampler="inSampler1",
    ),
    "character_constant_deferred": ShaderPreviewProfile(
        profile_id="character_constant_deferred",
        confidence="proven",
    ),
    "character_eye_layered": ShaderPreviewProfile(
        profile_id="character_eye_layered",
        confidence="proven",
        base_color_sampler="inSampler0",
        retained_sampler_roles=(("inSampler1", "color modulation"),),
    ),
    "character_hair_deferred": ShaderPreviewProfile(
        profile_id="character_hair_deferred",
        confidence="proven",
        base_color_sampler="inSampler0",
        opacity_sampler="inSampler0",
        normal_sampler="inSampler1",
        normal_space="tangent",
        normal_y_flip=False,
    ),
    "character_layered_deferred": ShaderPreviewProfile(
        profile_id="character_layered_deferred",
        confidence="proven",
        base_color_sampler="inSampler0",
        normal_sampler="inSampler5",
        detail_normal_sampler="inSampler1",
        normal_space="tangent",
        normal_y_flip=False,
        material_sampler="inSampler3",
        reflection_sampler="inSampler4",
        retained_sampler_roles=(("inSampler2", "normal blend mask"),),
        non_color_samplers=("inSampler2", "inSampler4"),
    ),
    "character_modulated_deferred": ShaderPreviewProfile(
        profile_id="character_modulated_deferred",
        confidence="proven",
        base_color_sampler="inSampler1",
        material_sampler="inSampler0",
        retained_sampler_roles=(("inSampler2", "material modulation data"),),
        non_color_samplers=("inSampler2",),
    ),
    "character_multiply_deferred": ShaderPreviewProfile(
        profile_id="character_multiply_deferred",
        confidence="proven",
        base_color_sampler="inSampler0",
        secondary_base_color_sampler="inSampler1",
        material_sampler="inSampler2",
    ),
    "character_navy_packed_normal_deferred": ShaderPreviewProfile(
        profile_id="character_navy_packed_normal_deferred",
        confidence="proven",
        base_color_sampler="inSampler0",
        material_sampler="inSampler1",
        reflection_sampler="inSampler2",
        retained_sampler_roles=(
            ("inSampler3", "packed A/G tangent normal (retained)"),
        ),
        non_color_samplers=("inSampler2", "inSampler3"),
    ),
    "character_packed_xy_normal_deferred": ShaderPreviewProfile(
        profile_id="character_packed_xy_normal_deferred",
        confidence="proven",
        base_color_sampler="inSampler0",
        reflection_sampler="inSampler2",
        retained_sampler_roles=(("inSampler1", "packed XY tangent normal (retained)"),),
        non_color_samplers=("inSampler1", "inSampler2"),
    ),
    "character_rico_packed_normal_deferred": ShaderPreviewProfile(
        profile_id="character_rico_packed_normal_deferred",
        confidence="proven",
        base_color_sampler="inSampler0",
        material_sampler="inSampler2",
        reflection_sampler="inSampler3",
        retained_sampler_roles=(
            ("inSampler1", "packed A/G tangent normal (retained)"),
        ),
        non_color_samplers=("inSampler1", "inSampler3"),
    ),
    "character_skin_deferred": ShaderPreviewProfile(
        profile_id="character_skin_deferred",
        confidence="proven",
        base_color_sampler="inSampler0",
        normal_sampler="inSampler6",
        detail_normal_sampler="inSampler5",
        normal_space="tangent",
        normal_y_flip=False,
        retained_sampler_roles=(
            ("inSampler2", "skin mask/response data"),
            ("inSampler3", "skin mask/response data"),
            ("inSampler4", "skin mask/response data"),
            ("inSampler7", "skin mask/response data"),
            ("inRampSampler1", "skin response ramp"),
        ),
        non_color_samplers=(
            "inSampler2",
            "inSampler3",
            "inSampler4",
            "inSampler7",
            "inRampSampler1",
        ),
    ),
    "character_specialized_deferred": ShaderPreviewProfile(
        profile_id="character_specialized_deferred",
        confidence="proven",
        base_color_sampler="inSampler0",
        normal_sampler="inSampler3",
        normal_space="tangent",
        normal_y_flip=False,
        material_sampler="inSampler1",
        reflection_sampler="inSampler2",
        non_color_samplers=("inSampler2",),
    ),
    "character_standard_deferred": ShaderPreviewProfile(
        profile_id="character_standard_deferred",
        confidence="proven",
        base_color_sampler="inSampler0",
        normal_sampler="inSampler3",
        normal_space="tangent",
        normal_y_flip=False,
        material_sampler="inSampler1",
        reflection_sampler="inSampler2",
        non_color_samplers=("inSampler2",),
    ),
    "character_visari_skin_deferred": ShaderPreviewProfile(
        profile_id="character_visari_skin_deferred",
        confidence="proven",
        base_color_sampler="inSampler0",
        normal_sampler="inSampler3",
        detail_normal_sampler="inSampler4",
        normal_space="tangent",
        normal_y_flip=False,
        reflection_sampler="inSampler2",
        retained_sampler_roles=(
            ("inSampler1", "color modulation"),
            ("inSampler5", "skin mask/response data"),
            ("inSampler6", "skin mask/response data"),
            ("inSampler7", "skin mask/response data"),
            ("inSampler8", "skin mask/response data"),
        ),
        non_color_samplers=(
            "inSampler2",
            "inSampler5",
            "inSampler6",
            "inSampler7",
            "inSampler8",
        ),
    ),
}


def _exact_character_profiles() -> tuple[_ShaderPreviewProfileEntry, ...]:
    profiles: list[_ShaderPreviewProfileEntry] = []
    for group in CHARACTER_SHADER_PROFILE_GROUPS:
        profile = _CHARACTER_PROFILE_TEMPLATES[group.profile_id]
        profiles.extend(
            _ShaderPreviewProfileEntry(
                shader_body_sha256=shader_body_sha256,
                shader_object_id=shader_object_id,
                sampler_signature=group.sampler_signature,
                variable_signature=group.variable_signature,
                profile=profile,
            )
            for shader_body_sha256, shader_object_id in group.identities
        )
    return tuple(profiles)


_CHARACTER_SHADER_PREVIEW_PROFILES = _exact_character_profiles()


_SHADER_PREVIEW_PROFILES = (
    *_CHARACTER_SHADER_PREVIEW_PROFILES,
    _ShaderPreviewProfileEntry(
        shader_body_sha256=(
            "de47f31edd3b479dbc7fded3beeec4c33e68125c4bd36c77cf3691e9543f3291"
        ),
        shader_object_id="97960894e18149c37bd02786ee11ee7b",
        sampler_signature=("inSampler0",),
        variable_signature=(),
        profile=ShaderPreviewProfile(
            profile_id="new_sun_billboard_alpha",
            confidence="proven",
            base_color_sampler="inSampler0",
            opacity_sampler="inSampler0",
            surface_render_method="BLENDED",
        ),
    ),
    _ShaderPreviewProfileEntry(
        shader_body_sha256=(
            "e041134742e4b530334a6e3dbb519f9910aa785d17ad11b186896817d6ec76b0"
        ),
        shader_object_id="5e4ce80dd829fb6c4e17892e174a622d",
        sampler_signature=("inSampler0",),
        variable_signature=("inVariable0",),
        profile=ShaderPreviewProfile(
            profile_id="new_sun_vertex_alpha",
            confidence="proven",
            base_color_sampler="inSampler0",
            opacity_sampler="inSampler0",
            opacity_expression="texture_alpha_times_vertex_alpha",
            surface_render_method="BLENDED",
        ),
    ),
    _ShaderPreviewProfileEntry(
        shader_body_sha256=(
            "73d8b758bef9520c54cf5b90f0d2b552f886f152fc02cdabe90495d6d25d8802"
        ),
        shader_object_id="2da9b02ce6f215167b66522f0d88082b",
        sampler_signature=(
            "inSampler2",
            "inSampler3",
            "inSampler4",
            "inSampler5",
            "inSampler1",
            "inSampler6",
            "inSampler7",
            "inSampler0",
        ),
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
        profile=ShaderPreviewProfile(
            profile_id="new_sun_layered_sky",
            confidence="proven",
            preview_composition="new_sun_layered_sky",
            surface_render_method="BLENDED",
            retained_sampler_roles=(
                ("inSampler0", "animated opacity threshold"),
                ("inSampler1", "cloud coordinate and opacity mask"),
                ("inSampler2", "primary sky color"),
                ("inSampler3", "secondary sky color"),
                ("inSampler4", "secondary sky modulation"),
                ("inSampler5", "sky blend factor"),
                ("inSampler6", "final sky color modulation"),
                ("inSampler7", "primary sky color modulation"),
            ),
            non_color_samplers=(
                "inSampler0",
                "inSampler1",
                "inSampler5",
                "inSampler7",
            ),
        ),
    ),
    _ShaderPreviewProfileEntry(
        shader_body_sha256=(
            "9f6918b3cc4e19f7ead38072f07a117489ec931b64c41d47bcbd179b134226e5"
        ),
        shader_object_id="1f5d38798722b5fcc2bd472978c93de9",
        sampler_signature=("inSampler0",),
        variable_signature=("inVariable0",),
        profile=ShaderPreviewProfile(
            profile_id="new_sun_vertex_masked",
            confidence="proven",
            base_color_sampler="inSampler0",
            opacity_sampler="inSampler0",
            opacity_expression="one_minus_vertex_alpha_over_texture_alpha",
            base_color_vertex_color_power=2.0,
            surface_render_method="BLENDED",
        ),
    ),
    _ShaderPreviewProfileEntry(
        shader_body_sha256=(
            "9fe62b8cc9f046214ae79628ea86b824c385f988bd758cfcabbff6b1fb45d0d8"
        ),
        shader_object_id="8b87b9478d05f0dce11dc5c7be09a657",
        sampler_signature=("inSampler0", "inSampler1"),
        variable_signature=(),
        profile=ShaderPreviewProfile(
            profile_id="hgh_fighter_skinned",
            confidence="proven",
            base_color_sampler="inSampler0",
            material_sampler="inSampler1",
            specular_power_channel="G",
            specular_power_scale=24.5,
            specular_intensity_channel="B",
            vertex_color_emission_power=2.0,
        ),
    ),
    _ShaderPreviewProfileEntry(
        shader_body_sha256=(
            "3fbfdd224e92a41d0b6db8008dd1013762fb86cf268f93beaed24ad85220ae8c"
        ),
        shader_object_id="0baf0706e8b84648060ad143b16824ac",
        sampler_signature=("inSampler0", "inSampler1"),
        variable_signature=(),
        profile=ShaderPreviewProfile(
            profile_id="hgh_fighter_static",
            confidence="proven",
            base_color_sampler="inSampler0",
            material_sampler="inSampler1",
            specular_power_channel="G",
            specular_power_scale=24.5,
            specular_intensity_channel="B",
        ),
    ),
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
            normal_y_flip=False,
            detail_normal_sampler="inSampler6",
            material_sampler="inSampler3",
            reflection_sampler="inSampler4",
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
            normal_y_flip=False,
            detail_normal_sampler="inSampler6",
            material_sampler="inSampler3",
            reflection_sampler="inSampler4",
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
            normal_y_flip=False,
            detail_normal_sampler="inSampler6",
            material_sampler="inSampler3",
            reflection_sampler="inSampler4",
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
            secondary_base_color_sampler="inSampler1",
            normal_sampler="inSampler3",
            normal_space="tangent",
            normal_y_flip=False,
            detail_normal_sampler="inSampler4",
            material_sampler="inSampler2",
            specular_power_channel="R",
            specular_power_scale=49.0,
            specular_intensity_channel="B",
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
            secondary_base_color_sampler="inSampler1",
            normal_sampler="inSampler3",
            normal_space="tangent",
            normal_y_flip=False,
            detail_normal_sampler="inSampler4",
            material_sampler="inSampler2",
            specular_power_channel="R",
            specular_power_scale=49.0,
            specular_intensity_channel="B",
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
            secondary_base_color_sampler="inSampler1",
            normal_sampler="inSampler3",
            normal_space="tangent",
            normal_y_flip=False,
            material_sampler="inSampler2",
            specular_power_channel="R",
            specular_power_scale=49.0,
            specular_intensity_channel="B",
            flip_diffuse_v=True,
        ),
    ),
    _ShaderPreviewProfileEntry(
        shader_body_sha256=(
            "44cef75c8dee1cdaa570b698db010cc222f46afcd76707fdb22b188d38a0bf60"
        ),
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
    _exact_vehicle_profile(
        "58708032f3f7904b61d80752fb598b8972c6cb41f7e3ff5aa78ae922b6589b96",
        "0f8732ddf90aacb3a9f3b4c1e63fc752",
        "inSampler0 inSampler1 inSampler2",
        "hgh_dropship_unbumped_deferred",
        base_color_sampler="inSampler0",
        material_sampler="inSampler1",
        reflection_sampler="inSampler2",
        specular_power_channel="G",
        specular_intensity_channel="B",
        specular_power_scale=98.0,
    ),
    _exact_vehicle_profile(
        "c3569b2b9d9b56216083d2b4a7ac42fa35f62d657c36086c5a709a0f0cd355b9",
        "6b2068005285c8ab951a526590a4d8a4",
        "inSampler3 inSampler0 inSampler1 inSampler2",
        "hgh_dropship_complex_deferred",
        base_color_sampler="inSampler0",
        normal_sampler="inSampler3",
        material_sampler="inSampler1",
        reflection_sampler="inSampler2",
    ),
    _exact_vehicle_profile(
        "7fb539ad3e61bc26299da411f01fb6393caf9a7eb81da72dc7bcc26f283c8680",
        "8c77e857231655920b6e4835ab731d4b",
        "inSampler3 inSampler0 inSampler1 inSampler2",
        "hgh_dropship_deferred",
        base_color_sampler="inSampler0",
        normal_sampler="inSampler3",
        material_sampler="inSampler1",
        reflection_sampler="inSampler2",
        specular_power_channel="G",
        specular_intensity_channel="B",
        specular_power_scale=98.0,
    ),
    _exact_vehicle_profile(
        "0c4a01afb71e82cbedba54226b10bba85aa3231b576a093c93ddc508acecf947",
        "ed94308fbe5e1714583b41e9e0e7e3c0",
        "inSampler3 inSampler0 inSampler1 inSampler2",
        "hgh_dropship_deferred_g49",
        base_color_sampler="inSampler0",
        normal_sampler="inSampler3",
        material_sampler="inSampler1",
        reflection_sampler="inSampler2",
        specular_power_channel="G",
        specular_intensity_channel="B",
        specular_power_scale=49.0,
    ),
    _exact_vehicle_profile(
        "c0569bd01c8a612f914a0ecd7fc7552cbfbaf0eaf064913192288c1c1fee266e",
        "9a0fa36f2abfa919579d6395db377b27",
        "inSampler3 inSampler4 inSampler0 inSampler1 inSampler2",
        "hgh_aapc_layered_deferred",
        base_color_sampler="inSampler0",
        secondary_base_color_sampler="inSampler1",
        normal_sampler="inSampler3",
        detail_normal_sampler="inSampler4",
        material_sampler="inSampler2",
    ),
    _exact_vehicle_profile(
        "598aaf0957f2f2511905a1ab42de71c1c9714ef08b61813ec34b970e5fdbf4a0",
        "c1ad2d75a5e9d53144d03f7e7f0cd1f0",
        "inSampler3 inSampler4 inSampler0 inSampler1 inSampler2",
        "hgh_aapc_layered_deferred_r49",
        base_color_sampler="inSampler0",
        secondary_base_color_sampler="inSampler1",
        normal_sampler="inSampler3",
        detail_normal_sampler="inSampler4",
        material_sampler="inSampler2",
        specular_power_channel="R",
        specular_intensity_channel="B",
        specular_power_scale=49.0,
    ),
    _exact_vehicle_profile(
        "d5ac3104161f2016a4fe07a902f57ff83ed4f55fa08511e6972b29742e7451e2",
        "c0d90d1ad5eaed9ee58cd8ab7dc82471",
        "inSampler0 inSampler1 inSampler3 inRampSampler2",
        "hgh_aapc_ramp_deferred",
        variable_signature="inVariable0",
        base_color_sampler="inSampler0",
        secondary_base_color_sampler="inSampler1",
        material_sampler="inSampler3",
    ),
    _exact_vehicle_profile(
        "1caece656ebfdc41de02d665814b6fecfe5edd75748b35ee70d9bdf6ad70218e",
        "152325d374d7231cd503d72edc7e5de7",
        "inSampler2 inSampler3 inSampler0 inSampler1",
        "isa_aapc_layered_deferred",
        base_color_sampler="inSampler0",
        normal_sampler="inSampler2",
        detail_normal_sampler="inSampler3",
        material_sampler="inSampler1",
        specular_power_channel="R",
        specular_intensity_channel="B",
        specular_power_scale=49.0,
    ),
    _exact_vehicle_profile(
        "7ab01c0931d5c8b4e1739de007de15106cce40d7a50cd950d1f2a15c9bd60b6a",
        "1ed8c531fb7f936c69492669042b94f0",
        "inSampler2 inSampler3 inSampler0 inSampler1",
        "isa_aapc_layered_deferred",
        base_color_sampler="inSampler0",
        normal_sampler="inSampler2",
        detail_normal_sampler="inSampler3",
        material_sampler="inSampler1",
        specular_power_channel="R",
        specular_intensity_channel="B",
        specular_power_scale=49.0,
    ),
    _exact_vehicle_profile(
        "fe891522674ec48a0ff924b61e122cca650219ca36380493e1e813e645246924",
        "4a209aac9aeef3306dce19e46527d0fa",
        "inSampler2 inSampler3 inSampler0 inSampler1",
        "isa_aapc_layered_deferred",
        base_color_sampler="inSampler0",
        normal_sampler="inSampler2",
        detail_normal_sampler="inSampler3",
        material_sampler="inSampler1",
        specular_power_channel="R",
        specular_intensity_channel="B",
        specular_power_scale=49.0,
    ),
    _exact_vehicle_profile(
        "054f00500f1cc394fba8a6ac12db7327284ab5be843973b0fe1c6f05c17899ed",
        "b87a2ff0d9c2aad4cdbd231b0cc2b9d6",
        "inSampler2 inSampler3 inSampler0 inSampler1",
        "isa_aapc_layered_deferred",
        base_color_sampler="inSampler0",
        normal_sampler="inSampler2",
        detail_normal_sampler="inSampler3",
        material_sampler="inSampler1",
        specular_power_channel="R",
        specular_intensity_channel="B",
        specular_power_scale=49.0,
    ),
    _exact_vehicle_profile(
        "72da9756337549d6a55bba21325d100218fe25c83164f1d09755b1abfc2064ff",
        "406ed5c26dc5c3d5d3ed3fc501004304",
        "inSampler2 inSampler0 inSampler1",
        "isa_aapc_compact_masked_deferred",
        variable_signature="inVariable0",
        base_color_sampler="inSampler0",
        normal_sampler="inSampler2",
        material_sampler="inSampler1",
    ),
    _exact_vehicle_profile(
        "8559e15443f242179eac974262a80d1e73136559757bbb5b1fe6ce681a63a33f",
        "9688112c2f4711397c49debe72fcd216",
        "inSampler2 inSampler0 inSampler1",
        "isa_aapc_compact_masked_deferred",
        variable_signature="inVariable0",
        base_color_sampler="inSampler0",
        normal_sampler="inSampler2",
        material_sampler="inSampler1",
    ),
    _exact_vehicle_profile(
        "b47fc160fd010b6e61750f1523a39464d6dba63a0c7cdab299267d2c75822748",
        "616083d7b97f21c7175a098bbc01b493",
        "inSampler0 inSampler2 inRampSampler1",
        "isa_aapc_ramp_deferred_g49",
        variable_signature="inVariable0",
        base_color_sampler="inSampler0",
        material_sampler="inSampler2",
        specular_power_channel="G",
        specular_intensity_channel="B",
        specular_power_scale=49.0,
    ),
    _exact_vehicle_profile(
        "3d5ceebb9198d3702a678a04f40fae9cfb88ff7f01b684eb1743a8282bcf47cc",
        "8735cb04da386a30d617748a6c942f86",
        "inSampler0 inSampler1 inSampler3 inRampSampler2",
        "isa_aapc_ramp_deferred",
        variable_signature="inVariable0",
        base_color_sampler="inSampler0",
        material_sampler="inSampler3",
    ),
    _exact_vehicle_profile(
        "cbadbe7f3c1f92001f20d7de9195026c648ffd550fcbf93af1fde0b4b634c314",
        "4f122b99db7e4223fd2865254cf1d158",
        "inSampler3 inSampler0 inSampler1 inSampler2",
        "isa_buggy_secondary_deferred",
        base_color_sampler="inSampler0",
        secondary_base_color_sampler="inSampler1",
        normal_sampler="inSampler3",
        material_sampler="inSampler2",
        specular_power_channel="R",
        specular_intensity_channel="B",
        specular_power_scale=49.0,
    ),
    _exact_vehicle_profile(
        "4f9d2da40a9d977500e1bbbf3ad630553c7b62bb67ecd86b402d8eff9c342f2a",
        "6ecc1dabafc42f2a93c41a50df1b6687",
        "inSampler4 inSampler5 inSampler2 inSampler0 inSampler1 inSampler3",
        "isa_buggy_layered_deferred",
        base_color_sampler="inSampler0",
        normal_sampler="inSampler4",
        detail_normal_sampler="inSampler5",
        material_sampler="inSampler3",
        specular_power_channel="R",
        specular_intensity_channel="B",
        specular_power_scale=49.0,
    ),
    _exact_vehicle_profile(
        "dcbee4ab35c0a7acf4ff85489c8cba55a6a5b3a4f23fdfb1d40ab2253db4afe2",
        "ef63afc5d7b34083a33cd95d15f245e1",
        "inSampler4 inSampler5 inSampler2 inSampler0 inSampler1 inSampler3",
        "isa_buggy_layered_deferred",
        base_color_sampler="inSampler0",
        normal_sampler="inSampler4",
        detail_normal_sampler="inSampler5",
        material_sampler="inSampler3",
        specular_power_channel="R",
        specular_intensity_channel="B",
        specular_power_scale=49.0,
    ),
    _exact_vehicle_profile(
        "7f055707a9b1251075a5aa088652eef5d04eb0bdab6c73c8a86d3594b7de45ce",
        "6f62459a7e6472f7e324bbfd6f863e9b",
        "inSampler2 inSampler0 inSampler1",
        "isa_buggy_compact_deferred",
        base_color_sampler="inSampler0",
        normal_sampler="inSampler2",
        material_sampler="inSampler1",
        specular_power_channel="R",
        specular_intensity_channel="B",
        specular_power_scale=49.0,
    ),
    _exact_vehicle_profile(
        "25897f95582e5e5a2a8e4d65e9772940af24eb9d754f89466380205472df6ad1",
        "aab8fd8e71d04ac7493f3ce2ec02acb4",
        "inSampler0 inSampler1 inSampler3 inRampSampler2",
        "isa_buggy_response_ramp_deferred_r49",
        variable_signature="inVariable0",
        base_color_sampler="inSampler0",
        material_sampler="inSampler3",
        specular_power_channel="R",
        specular_intensity_channel="B",
        specular_power_scale=49.0,
    ),
    _exact_vehicle_profile(
        "06a1e37041ac53c888e849fc273d644ff67f1af6eeb123f5c1f46e6652bcf1ab",
        "c0c99abb39f9e039272da739390ad8f9",
        "inSampler0 inSampler1 inSampler2",
        "isa_buggy_secondary_material_deferred",
        base_color_sampler="inSampler0",
        secondary_base_color_sampler="inSampler1",
        material_sampler="inSampler2",
        specular_power_channel="R",
        specular_intensity_channel="B",
        specular_power_scale=49.0,
    ),
    _exact_vehicle_profile(
        "fb5d2e1409facfa4a789a47e5b033d91f70efea299995a42586e1e58b8277e88",
        "f8f6ce14f8ba83f5f28a17da586ce44a",
        "inSampler0 inSampler2 inRampSampler1",
        "isa_buggy_ramp_deferred_r49",
        variable_signature="inVariable0",
        base_color_sampler="inSampler0",
        material_sampler="inSampler2",
        specular_power_channel="R",
        specular_intensity_channel="B",
        specular_power_scale=49.0,
    ),
    _exact_vehicle_profile(
        "7227b32f7ec3404bfa816fba22588460b6ac6be3c30efbd92902458466d543b2",
        "7de10c6edd018eeed89312a403ef4f83",
        "inSampler0 inSampler1 inSampler2",
        "isa_exoskeleton_response_deferred",
        base_color_sampler="inSampler0",
        material_sampler="inSampler2",
    ),
    _exact_vehicle_profile(
        "fbbdd7aa9e2bf575db2230329ae08983a8960b5db36443c8f2833fc89a67cec1",
        "a61e51472c418e6f30154536683653fd",
        "inSampler3 inSampler4 inSampler0 inSampler1 inSampler2",
        "isa_exoskeleton_layered_deferred",
        base_color_sampler="inSampler0",
        secondary_base_color_sampler="inSampler1",
        normal_sampler="inSampler3",
        detail_normal_sampler="inSampler4",
        material_sampler="inSampler2",
        specular_power_channel="G",
        specular_intensity_channel="B",
        specular_power_scale=49.0,
    ),
    _exact_vehicle_profile(
        "d393292d085c298e27433d2dae9f79fc884d06ffd17426660e38f1e8131044e4",
        "a715f1d41d0e6be11eaea6d4071df562",
        "inSampler3 inSampler4 inSampler0 inSampler1 inSampler2",
        "isa_exoskeleton_layered_deferred",
        base_color_sampler="inSampler0",
        secondary_base_color_sampler="inSampler1",
        normal_sampler="inSampler3",
        detail_normal_sampler="inSampler4",
        material_sampler="inSampler2",
        specular_power_channel="G",
        specular_intensity_channel="B",
        specular_power_scale=49.0,
    ),
    _exact_vehicle_profile(
        "d67859911e4d407619b2821692266b23b42aa7b3d858302bbbe334e17d98b4ca",
        "f8ad3999502f015448dce15888a17263",
        "inSampler2 inSampler0 inSampler1",
        "hgh_atac_deferred",
        base_color_sampler="inSampler0",
        normal_sampler="inSampler2",
        material_sampler="inSampler1",
        specular_power_channel="R",
        specular_intensity_channel="B",
        specular_power_scale=6.125,
    ),
    _exact_vehicle_profile(
        "027b0d3e923850b4f18a87443502ddac6a9403862890fcc8e34e7b6124f70aba",
        "1717d873cb90d6a8fc5cc44dac5d7fc8",
        "inSampler2 inSampler0 inSampler1 inSampler3 inSampler4",
        "hgh_cruiser_layered_deferred",
        base_color_sampler="inSampler0",
        normal_sampler="inSampler2",
        material_sampler="inSampler3",
        reflection_sampler="inSampler4",
    ),
    _exact_vehicle_profile(
        "06f6c846742879c30547a4e95622cf064d61898d2b17126ddb5078a1dcde49bf",
        "7511eed9983ae59c434a301c52d28ccf",
        "inSampler4 inSampler2 inSampler0 inSampler1 inSampler3",
        "hgh_cruiser_compact_deferred",
        base_color_sampler="inSampler0",
        normal_sampler="inSampler4",
        material_sampler="inSampler3",
    ),
    _exact_vehicle_profile(
        "8e07bf59967b2bfbcc9cf4c02cfd3910bac418d157ae4814c81bc4bb040dd722",
        "def4c8fbe2b0ff5e5f00548cbb4ef0ea",
        "inSampler4 inSampler2 inSampler0 inSampler1 inSampler3",
        "hgh_cruiser_compact_deferred",
        base_color_sampler="inSampler0",
        normal_sampler="inSampler4",
        material_sampler="inSampler3",
    ),
    _exact_vehicle_profile(
        "78d6aa1a200321676f5ab707ee4da8de5643d1accaacc83d4e48311e5a871d15",
        "cfb995ef1c6f06bb97384e211a4d4b81",
        "inSampler4 inSampler0 inSampler1 inSampler2 inSampler3",
        "hgh_cruiser_deferred_g12_25",
        base_color_sampler="inSampler0",
        normal_sampler="inSampler4",
        material_sampler="inSampler2",
        reflection_sampler="inSampler3",
        specular_power_channel="G",
        specular_intensity_channel="B",
        specular_power_scale=12.25,
    ),
    _exact_vehicle_profile(
        "4fc4f3a8ab53f39cbe5e77583f34b06b87845f9d590064a246ae6a896d035e89",
        "5b0789b6e9abb31163bdbe55a8111d99",
        "inSampler5 inSampler6 inSampler0 inSampler1 inSampler2 inSampler3 inSampler4",
        "isa_cruiser_layered_deferred",
        variable_signature="inVariable0",
        base_color_sampler="inSampler1",
        normal_sampler="inSampler5",
        detail_normal_sampler="inSampler6",
        material_sampler="inSampler3",
        reflection_sampler="inSampler4",
        flip_diffuse_v=True,
    ),
    _exact_vehicle_profile(
        "3c8ba294abfa1b895bb397bba592ffcd18bc8f85df058818bc4ba5314bb512ba",
        "c6dcc542d690fc4761ab407049ad4873",
        "inSampler5 inSampler6 inSampler0 inSampler1 inSampler2 inSampler3 inSampler4",
        "isa_cruiser_layered_deferred",
        variable_signature="inVariable0",
        base_color_sampler="inSampler1",
        normal_sampler="inSampler5",
        detail_normal_sampler="inSampler6",
        material_sampler="inSampler3",
        reflection_sampler="inSampler4",
        flip_diffuse_v=True,
    ),
    _exact_vehicle_profile(
        "3353134d8be4637272de114a7a20506450c99491febc420db4983b19c08349a2",
        "f1ef6049dec3cd24a903e307d77f0be4",
        "inSampler5 inSampler6 inSampler1 inSampler0 inSampler2 inSampler3 inSampler4",
        "isa_cruiser_layered_deferred",
        variable_signature="inVariable0",
        base_color_sampler="inSampler1",
        normal_sampler="inSampler5",
        detail_normal_sampler="inSampler6",
        material_sampler="inSampler3",
        reflection_sampler="inSampler4",
        flip_diffuse_v=True,
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
            non_color_samplers=("inSampler2",),
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
            non_color_samplers=("inSampler2",),
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
            non_color_samplers=("inSampler2",),
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
            non_color_samplers=("inSampler2",),
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
