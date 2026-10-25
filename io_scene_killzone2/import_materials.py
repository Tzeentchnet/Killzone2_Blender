"""Blender image and preview-material creation for decoded KZ2 resources."""

from __future__ import annotations

import struct
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import bpy

from .kz2_assets.core import CoreObject, CoreReference
from .kz2_assets.decoder import CoreDecoder
from .kz2_assets.errors import Kz2AssetError
from .kz2_assets.level import TextureOverride, VariableOverride
from .kz2_assets.materials import (
    ShaderPreviewProfile,
    blender_lightmap_uv_mapping,
    shader_preview_profile,
)
from .kz2_assets.model import (
    RenderEffect,
    RenderTechnique,
    ShaderResource,
    Texture,
    TextureColorSpace,
    TextureFormat,
    TextureType,
)
from .kz2_assets.resolver import ResolvedResource, ResourceResolver
from .kz2_assets.textures import decode_texture_surface


@dataclass(slots=True)
class MaterialBuilder:
    decoder: CoreDecoder
    warnings: list[str]
    external_resolver: ResourceResolver | None = None
    flip_uv_v: bool = True
    images_created: int = 0
    materials_created: int = 0
    _images: dict[tuple[str, int], bpy.types.Image | None] = field(
        default_factory=dict
    )
    _materials: dict[tuple[str, int, int], bpy.types.Material] = field(
        default_factory=dict
    )
    _override_materials: dict[tuple[object, ...], bpy.types.Material] = field(
        default_factory=dict
    )
    _warned: set[str] = field(default_factory=set)

    def assign(
        self,
        obj: bpy.types.Object,
        reference: CoreReference,
        *,
        technique_index: int = 0,
    ) -> None:
        material = self.material_for(reference, technique_index=technique_index)
        if material is not None:
            obj.data.materials.append(material)

    def material_for(
        self,
        reference: CoreReference,
        *,
        technique_index: int = 0,
        decoder: CoreDecoder | None = None,
    ) -> bpy.types.Material | None:
        source_decoder = decoder or self.decoder
        resolved = self._resolve_resource(
            reference,
            source_decoder,
            expected_type="RenderEffectResource",
        )
        if resolved is None:
            if reference.external_id is not None:
                self._warn_once(
                    f"Render effect {reference.external_id!r} could not be "
                    "resolved"
                )
            return None
        key = (
            resolved.decoder.core.source,
            resolved.record.index,
            technique_index,
        )
        existing = self._materials.get(key)
        if existing is not None:
            return existing

        record = resolved.record
        decoded = resolved.value
        if not isinstance(decoded, RenderEffect):
            self._warn_once(
                f"{record.type_name} {record.display_id} is not a decoded "
                "render effect"
            )
            return None
        if not decoded.techniques:
            self._warn_once(f"Render effect {record.display_id} has no techniques")
            return None
        if not 0 <= technique_index < len(decoded.techniques):
            self._warn_once(
                f"Render effect {record.display_id} has no technique "
                f"{technique_index}"
            )
            return None

        material = self._create_material(
            record,
            decoded,
            decoded.techniques[technique_index],
            technique_index,
            resolved.decoder,
        )
        self._materials[key] = material
        self.materials_created += 1
        return material

    def material_with_overrides(
        self,
        reference: CoreReference,
        texture_overrides: tuple[TextureOverride, ...],
        variable_overrides: tuple[VariableOverride, ...],
        *,
        instance_key: int,
        technique_index: int = 0,
        decoder: CoreDecoder | None = None,
    ) -> bpy.types.Material | None:
        source_decoder = decoder or self.decoder
        key = (
            source_decoder.core.source,
            reference.local_index,
            reference.external_id,
            technique_index,
            instance_key,
            texture_overrides,
            variable_overrides,
        )
        existing = self._override_materials.get(key)
        if existing is not None:
            return existing
        base = self.material_for(
            reference,
            technique_index=technique_index,
            decoder=source_decoder,
        )
        if base is None:
            return None
        material = base.copy()
        material.name = f"{base.name}_I{instance_key:05d}"
        material["kz2_instance_override"] = instance_key
        nodes = material.node_tree.nodes if material.node_tree is not None else ()
        for texture_override in texture_overrides:
            resolved = self._resolve_texture(texture_override.texture)
            image = self._image_for(resolved) if resolved is not None else None
            for node in nodes:
                if (
                    node.bl_idname == "ShaderNodeTexImage"
                    and node.get("kz2_sampler") == texture_override.sampler
                ):
                    node.image = image
                    node["kz2_mip_bias"] = texture_override.mip_bias
                    node["kz2_mip_bias_bits"] = (
                        texture_override.mip_bias_bits
                    )
                    node["kz2_sampler_state"] = (
                        texture_override.packed_sampler_state
                    )
                    node.interpolation = (
                        "Closest"
                        if (
                            texture_override.packed_sampler_state & 0xF
                        )
                        in (0, 1)
                        else "Linear"
                    )
                    address_u = (
                        texture_override.packed_sampler_state >> 4
                    ) & 0xF
                    address_v = (
                        texture_override.packed_sampler_state >> 8
                    ) & 0xF
                    if address_u == 1 and address_v == 1:
                        node.extension = "EXTEND"
                    elif address_u == 2 and address_v == 2:
                        node.extension = "MIRROR"
                    else:
                        node.extension = "REPEAT"
                    node.label = (
                        f"{texture_override.sampler} (instance override)"
                        if image is not None
                        else (
                            f"{texture_override.sampler} "
                            "(unresolved override)"
                        )
                    )
        for variable_override in variable_overrides:
            material[f"kz2_variable_override_{variable_override.variable}"] = (
                variable_override.raw_value.hex()
            )
            if (
                variable_override.variable == "LMUVShift"
                and variable_override.variable_type == 4
                and material.node_tree is not None
            ):
                values = struct.unpack(">4f", variable_override.raw_value)
                mapping = material.node_tree.nodes.get("KZ2_LMUVShift")
                if mapping is not None:
                    self._set_lightmap_uv_mapping(mapping, values)
        self._override_materials[key] = material
        self.materials_created += 1
        return material

    def _create_material(
        self,
        record: CoreObject,
        effect: RenderEffect,
        technique: RenderTechnique,
        technique_index: int,
        decoder: CoreDecoder,
    ) -> bpy.types.Material:
        base_name = effect.prefix.name or f"KZ2_{record.display_id[:12]}"
        material = bpy.data.materials.new(
            f"{base_name}_T{technique_index:02d}"
        )
        material.use_nodes = True
        material.diffuse_color = (0.8, 0.8, 0.8, 1.0)
        material["kz2_object_index"] = record.index
        material["kz2_object_id"] = record.display_id
        material["kz2_type"] = record.type_name
        material["kz2_technique_index"] = technique_index
        material["kz2_technique_count"] = len(effect.techniques)
        material["kz2_render_state"] = technique.render_state.raw.hex()
        material["kz2_reflection_sampler_bias"] = (
            technique.reflection_sampler_bias
        )
        material["kz2_render_effect_trailing"] = effect.trailing.hex()
        self._set_shader_properties(material, technique, decoder)
        shader_hash = material.get("kz2_shader_body_sha256")
        shader_object_id = material.get("kz2_shader_object_id")
        preview_profile = shader_preview_profile(
            shader_hash if isinstance(shader_hash, str) else None,
            tuple(binding.sampler for binding in technique.texture_bindings),
            shader_object_id=(
                shader_object_id
                if isinstance(shader_object_id, str)
                else None
            ),
            variable_signature=tuple(
                variable.shader_input
                for variable in technique.variable_bindings
            ),
            technique_index=technique_index,
        )
        material["kz2_profile_status"] = preview_profile.confidence
        if preview_profile.profile_id is not None:
            material["kz2_profile_id"] = preview_profile.profile_id
        if preview_profile.normal_sampler is not None:
            material["kz2_profile_normal_sampler"] = (
                preview_profile.normal_sampler
            )
            material["kz2_profile_normal_space"] = (
                preview_profile.normal_space or "unknown"
            )
            material["kz2_profile_normal_y_flip"] = (
                "unvalidated"
                if preview_profile.normal_y_flip is None
                else preview_profile.normal_y_flip
            )
        if preview_profile.material_sampler is not None:
            material["kz2_profile_material_sampler"] = (
                preview_profile.material_sampler
            )
        if preview_profile.reflection_sampler is not None:
            material["kz2_profile_reflection_sampler"] = (
                preview_profile.reflection_sampler
            )
        if preview_profile.specular_power_channel is not None:
            material["kz2_profile_specular_power_channel"] = (
                preview_profile.specular_power_channel
            )
            material["kz2_profile_specular_power_scale"] = (
                preview_profile.specular_power_scale
            )
        if preview_profile.specular_intensity_channel is not None:
            material["kz2_profile_specular_intensity_channel"] = (
                preview_profile.specular_intensity_channel
            )
        material["kz2_preview_diffuse_v_flip"] = (
            preview_profile.flip_diffuse_v
        )

        node_tree = material.node_tree
        nodes = node_tree.nodes
        nodes.clear()
        output = nodes.new("ShaderNodeOutputMaterial")
        output.location = (700.0, 0.0)
        principled = nodes.new("ShaderNodeBsdfPrincipled")
        principled.location = (400.0, 0.0)

        base_color_socket = principled.inputs.get("Base Color")
        alpha_socket = principled.inputs.get("Alpha")
        base_color_output = None
        opacity_output = None
        base_color_binding_index = None
        base_color_sampler = None
        base_color_uv_map = "DiffuseUV"
        lightmap_binding_index = None
        lightmap_is_resolved = False
        texture_nodes = {}

        variable_values = {
            variable.name: variable.value
            for variable in technique.variable_bindings
            if variable.value is not None
        }
        for binding_index, binding in enumerate(technique.texture_bindings):
            node = nodes.new("ShaderNodeTexImage")
            node.name = f"KZ2_{binding_index:02d}_{binding.sampler}"
            node.label = binding.sampler
            node.location = (-650.0, 260.0 - binding_index * 260.0)
            node.interpolation = (
                "Closest" if binding.filter_mode in (0, 1) else "Linear"
            )
            if binding.address_u == 1 and binding.address_v == 1:
                node.extension = "EXTEND"
            elif binding.address_u == 2 and binding.address_v == 2:
                node.extension = "MIRROR"
            else:
                node.extension = "REPEAT"
            node["kz2_sampler"] = binding.sampler
            node["kz2_mip_bias"] = binding.mip_bias
            node["kz2_mip_bias_bits"] = binding.mip_bias_bits
            node["kz2_sampler_state"] = binding.packed_sampler_state
            texture_nodes[binding.sampler] = node

            resolved_texture = self._resolve_texture(
                binding.texture,
                decoder,
            )
            texture = (
                resolved_texture.value
                if resolved_texture is not None
                and isinstance(resolved_texture.value, Texture)
                else None
            )
            if resolved_texture is not None:
                node.image = self._image_for(resolved_texture)
            if node.image is None:
                node.label = f"{binding.sampler} (unresolved)"

            profile_role = preview_profile.role_for_sampler(binding.sampler)
            if profile_role is not None:
                node["kz2_profile_role"] = profile_role
                node.label = f"{binding.sampler} ({profile_role})"
            elif binding.sampler.startswith("inRampSampler"):
                node["kz2_profile_role"] = "response ramp"
                node.label = f"{binding.sampler} (response ramp)"

            is_profile_base_color = (
                preview_profile.base_color_sampler is not None
                and binding.sampler == preview_profile.base_color_sampler
            )
            is_srgb_base_color = (
                preview_profile.base_color_sampler is None
                and binding.sampler == "inSampler0"
                and texture is not None
                and texture.color_space == TextureColorSpace.SRGB
            )
            if (
                texture is not None
                and node.image is not None
                and (is_profile_base_color or is_srgb_base_color)
            ):
                node.label = f"{binding.sampler} (preview base color)"
                base_color_output = node.outputs["Color"]
                base_color_binding_index = binding_index
                base_color_sampler = binding.sampler
                base_color_uv_map = (
                    preview_profile.base_color_uv_map
                    if is_profile_base_color
                    else "DiffuseUV"
                )
                material["kz2_preview_base_color"] = binding.sampler
                material["kz2_preview_base_color_source"] = (
                    "shader_profile"
                    if is_profile_base_color
                    else "srgb_heuristic"
                )
                material["kz2_preview_uv_map"] = base_color_uv_map
            if (
                preview_profile.opacity_sampler == binding.sampler
                and node.image is not None
            ):
                opacity_output = node.outputs["Alpha"]
                material["kz2_preview_opacity"] = binding.sampler
            if binding.sampler == "inSampler_LM":
                lightmap_binding_index = binding_index
                lightmap_is_resolved = node.image is not None
                node["kz2_profile_role"] = "packed YUV lightmap"
                node.label = f"{binding.sampler} (packed YUV lightmap)"
                material["kz2_lightmap_decode"] = "KZ2_LightmapYUV_v1"
                material["kz2_lightmap_sun_occlusion"] = (
                    "preserved node-group output"
                )
                if (
                    texture is not None
                    and texture.color_space == TextureColorSpace.SRGB
                ):
                    material["kz2_lightmap_source_color_space"] = "sRGB"
                    self._warn_once(
                        f"Lightmap {resolved_texture.record.display_id} is "
                        "marked sRGB; preserving the exceptional source "
                        "metadata"
                    )

        self._connect_profile_specular(
            nodes,
            node_tree,
            principled,
            preview_profile,
            texture_nodes,
            material,
        )
        if (
            base_color_binding_index is not None
            and base_color_sampler is not None
        ):
            self._connect_diffuse_coordinates(
                nodes,
                node_tree,
                base_color_binding_index,
                base_color_sampler,
                uv_map=base_color_uv_map,
                flip_v=preview_profile.flip_diffuse_v,
            )
        indirect_light_output = None
        if lightmap_binding_index is not None:
            self._connect_lightmap_coordinates(
                nodes,
                node_tree,
                lightmap_binding_index,
                variable_values.get("LMUVShift"),
            )
            indirect_light_output = self._connect_lightmap_decode(
                nodes,
                node_tree,
                lightmap_binding_index,
            )
        if base_color_output is not None and base_color_socket is not None:
            node_tree.links.new(base_color_output, base_color_socket)
        if (
            opacity_output is not None
            and alpha_socket is not None
            and technique.render_state.blend_mode in (5, 8, 11)
        ):
            node_tree.links.new(opacity_output, alpha_socket)
        surface_output = principled.outputs["BSDF"]
        if (
            base_color_output is not None
            and indirect_light_output is not None
            and lightmap_is_resolved
        ):
            surface_output = self._add_indirect_light(
                nodes,
                node_tree,
                base_color_output,
                indirect_light_output,
                surface_output,
            )
            material["kz2_lightmap_preview"] = (
                "decoded indirect emission approximation"
            )
        elif lightmap_binding_index is not None:
            material["kz2_lightmap_preview"] = (
                "decoded outputs retained unconnected"
            )
        node_tree.links.new(surface_output, output.inputs["Surface"])
        if base_color_sampler is not None:
            preview_node = texture_nodes[base_color_sampler]
            for node in nodes:
                node.select = False
            preview_node.select = True
            nodes.active = preview_node
        return material

    def _connect_profile_specular(
        self,
        nodes,
        node_tree,
        principled,
        profile: ShaderPreviewProfile,
        texture_nodes: dict[str, object],
        material: bpy.types.Material,
    ) -> None:
        if (
            profile.material_sampler is None
            or profile.specular_power_channel is None
            or profile.specular_intensity_channel is None
        ):
            return
        texture = texture_nodes.get(profile.material_sampler)
        if texture is None or texture.image is None:
            self._warn_once(
                f"Packed material sampler {profile.material_sampler} "
                "could not be resolved"
            )
            return

        separate = nodes.new("ShaderNodeSeparateColor")
        separate.name = "KZ2_MaterialChannels"
        separate.label = "KZ2 packed material channels"
        separate.mode = "RGB"
        separate.location = (-350.0, texture.location.y)
        node_tree.links.new(texture.outputs["Color"], separate.inputs["Color"])
        channels = {
            "R": separate.outputs["Red"],
            "G": separate.outputs["Green"],
            "B": separate.outputs["Blue"],
        }
        power = channels.get(profile.specular_power_channel)
        intensity = channels.get(profile.specular_intensity_channel)
        if power is None or intensity is None:
            self._warn_once(
                f"Material profile {profile.profile_id} has invalid packed "
                "channel configuration"
            )
            return

        scale = nodes.new("ShaderNodeMath")
        scale.name = "KZ2_SpecularPower"
        scale.label = (
            f"Specular power: {profile.specular_power_channel} "
            f"* {profile.specular_power_scale:g}"
        )
        scale.operation = "MULTIPLY"
        scale.location = (-80.0, texture.location.y + 40.0)
        scale.inputs[1].default_value = profile.specular_power_scale
        node_tree.links.new(power, scale.inputs[0])

        denominator = nodes.new("ShaderNodeMath")
        denominator.name = "KZ2_SpecularPowerPlusTwo"
        denominator.label = "Specular power + 2"
        denominator.operation = "ADD"
        denominator.location = (100.0, texture.location.y + 40.0)
        denominator.inputs[1].default_value = 2.0
        node_tree.links.new(scale.outputs[0], denominator.inputs[0])

        ratio = nodes.new("ShaderNodeMath")
        ratio.name = "KZ2_RoughnessSquared"
        ratio.label = "2 / (specular power + 2)"
        ratio.operation = "DIVIDE"
        ratio.location = (280.0, texture.location.y + 40.0)
        ratio.inputs[0].default_value = 2.0
        node_tree.links.new(denominator.outputs[0], ratio.inputs[1])

        roughness = nodes.new("ShaderNodeMath")
        roughness.name = "KZ2_Roughness"
        roughness.label = "Blinn-Phong power to roughness"
        roughness.operation = "SQRT"
        roughness.location = (460.0, texture.location.y + 40.0)
        node_tree.links.new(ratio.outputs[0], roughness.inputs[0])
        roughness_socket = principled.inputs.get("Roughness")
        if roughness_socket is not None:
            node_tree.links.new(roughness.outputs[0], roughness_socket)

        specular_socket = principled.inputs.get("Specular IOR Level")
        if specular_socket is not None:
            node_tree.links.new(intensity, specular_socket)
        material["kz2_roughness_approximation"] = (
            "sqrt(2 / (specular_power + 2))"
        )

    def _connect_diffuse_coordinates(
        self,
        nodes,
        node_tree,
        binding_index: int,
        sampler: str,
        *,
        uv_map: str,
        flip_v: bool,
    ) -> None:
        texture_node = nodes.get(
            f"KZ2_{binding_index:02d}_{sampler}"
        )
        if texture_node is None:
            return
        uv = nodes.new("ShaderNodeUVMap")
        uv.name = (
            "KZ2_DiffuseUV"
            if uv_map == "DiffuseUV"
            else "KZ2_BaseColorUV"
        )
        uv.label = f"KZ2 base color {uv_map}"
        uv.uv_map = uv_map
        uv.location = (-1100.0 if flip_v else -880.0, texture_node.location.y)
        coordinates = uv.outputs["UV"]
        if flip_v:
            mapping = nodes.new("ShaderNodeMapping")
            mapping.name = "KZ2_DiffuseUVTransform"
            mapping.label = "Shader profile: (u, 1 - v)"
            mapping.location = (-880.0, texture_node.location.y)
            mapping.inputs["Location"].default_value = (0.0, 1.0, 0.0)
            mapping.inputs["Scale"].default_value = (1.0, -1.0, 1.0)
            node_tree.links.new(coordinates, mapping.inputs["Vector"])
            coordinates = mapping.outputs["Vector"]
        node_tree.links.new(coordinates, texture_node.inputs["Vector"])

    def _connect_lightmap_coordinates(
        self,
        nodes,
        node_tree,
        binding_index: int,
        lmuv_shift: tuple[float, ...] | None,
    ) -> None:
        texture_node = nodes.get(f"KZ2_{binding_index:02d}_inSampler_LM")
        if texture_node is None:
            return
        uv = nodes.new("ShaderNodeUVMap")
        uv.name = "KZ2_LightmapUV0"
        uv.label = "KZ2 lightmap UV0"
        uv.uv_map = "UV0"
        uv.location = (-1100.0, texture_node.location.y)
        mapping = nodes.new("ShaderNodeMapping")
        mapping.name = "KZ2_LMUVShift"
        mapping.label = "LMUVShift: UV * scale + offset"
        mapping.location = (-880.0, texture_node.location.y)
        if lmuv_shift is not None and len(lmuv_shift) == 4:
            self._set_lightmap_uv_mapping(mapping, lmuv_shift)
        node_tree.links.new(uv.outputs["UV"], mapping.inputs["Vector"])
        node_tree.links.new(mapping.outputs["Vector"], texture_node.inputs["Vector"])

    def _connect_lightmap_decode(
        self,
        nodes,
        node_tree,
        binding_index: int,
    ):
        texture_node = nodes.get(
            f"KZ2_{binding_index:02d}_inSampler_LM"
        )
        if texture_node is None:
            return None
        decode = nodes.new("ShaderNodeGroup")
        decode.node_tree = _lightmap_node_group()
        decode.name = "KZ2_LightmapYUV"
        decode.label = "Packed YUV lightmap decode"
        decode.location = (-350.0, texture_node.location.y)
        node_tree.links.new(
            texture_node.outputs["Color"],
            decode.inputs["Packed RGB"],
        )
        node_tree.links.new(
            texture_node.outputs["Alpha"],
            decode.inputs["Luminance"],
        )
        return decode.outputs["Indirect RGB"]

    def _add_indirect_light(
        self,
        nodes,
        node_tree,
        base_color,
        indirect_light,
        surface,
    ):
        multiply = nodes.new("ShaderNodeVectorMath")
        multiply.name = "KZ2_IndirectBaseColor"
        multiply.label = "Base color * decoded indirect light"
        multiply.operation = "MULTIPLY"
        multiply.location = (0.0, -180.0)
        node_tree.links.new(base_color, multiply.inputs[0])
        node_tree.links.new(indirect_light, multiply.inputs[1])

        emission = nodes.new("ShaderNodeEmission")
        emission.name = "KZ2_IndirectEmission"
        emission.label = "Indirect-light approximation"
        emission.location = (200.0, -180.0)
        emission.inputs["Strength"].default_value = 1.0
        node_tree.links.new(multiply.outputs["Vector"], emission.inputs["Color"])

        add = nodes.new("ShaderNodeAddShader")
        add.name = "KZ2_SurfacePlusIndirect"
        add.label = "Direct surface + baked indirect"
        add.location = (500.0, 0.0)
        node_tree.links.new(surface, add.inputs[0])
        node_tree.links.new(emission.outputs["Emission"], add.inputs[1])
        return add.outputs["Shader"]

    def _set_lightmap_uv_mapping(
        self,
        mapping,
        values: tuple[float, ...],
    ) -> None:
        location, scale = blender_lightmap_uv_mapping(
            (values[0], values[1], values[2], values[3]),
            flip_v=self.flip_uv_v,
        )
        mapping.inputs["Location"].default_value = location
        mapping.inputs["Scale"].default_value = scale

    def _set_shader_properties(
        self,
        material: bpy.types.Material,
        technique: RenderTechnique,
        decoder: CoreDecoder,
    ) -> None:
        reference = technique.shader
        resolved = self._resolve_resource(
            reference,
            decoder,
            expected_type="ShaderResource",
        )
        if resolved is None:
            if reference.external_id is not None:
                material["kz2_shader_external_id"] = reference.external_id
            return
        record = resolved.record
        decoded = resolved.value
        material["kz2_shader_object_id"] = record.display_id
        if isinstance(decoded, ShaderResource):
            material["kz2_shader_ibl_mode"] = decoded.ibl_shading_mode
            material["kz2_shader_body_sha256"] = decoded.body_sha256

    def _resolve_texture(
        self,
        reference: CoreReference,
        decoder: CoreDecoder | None = None,
    ) -> ResolvedResource | None:
        resolved = self._resolve_resource(
            reference,
            decoder or self.decoder,
            expected_type="Texture",
        )
        if resolved is None:
            if reference.external_id is not None:
                self._warn_once(
                    f"Texture {reference.external_id!r} could not be resolved"
                )
            return None
        if not isinstance(resolved.value, Texture):
            self._warn_once(
                f"{resolved.record.type_name} {resolved.record.display_id} is "
                "not a decoded texture"
            )
            return None
        return resolved

    def _resolve_resource(
        self,
        reference: CoreReference,
        decoder: CoreDecoder,
        *,
        expected_type: str,
    ) -> ResolvedResource | None:
        if reference.local_index is not None:
            record = decoder.core.objects[reference.local_index]
            return ResolvedResource(decoder, record, decoder.decode(record))
        if (
            reference.external_id is not None
            and self.external_resolver is not None
        ):
            self.external_resolver.add_requested_ids(
                {reference.external_id}
            )
            return self.external_resolver.resolve(
                reference.external_id,
                expected_type=expected_type,
            )
        return None

    def _image_for(
        self,
        resolved: ResolvedResource,
    ) -> bpy.types.Image | None:
        texture = resolved.value
        assert isinstance(texture, Texture)
        record = resolved.record
        key = (resolved.decoder.core.source, record.index)
        if key in self._images:
            return self._images[key]
        if texture.texture_type == TextureType.CUBEMAP:
            self._warn_once(
                f"Cubemap texture {record.display_id} is retained but cannot be "
                "bound directly to a Blender 2D image node"
            )
            self._images[key] = None
            return None
        try:
            surface = decode_texture_surface(texture)
        except (Kz2AssetError, ValueError) as exc:
            self._warn_once(
                f"Texture {record.display_id} could not be decoded: {exc}"
            )
            self._images[key] = None
            return None

        name = texture.prefix.name or f"KZ2_{record.display_id[:12]}"
        is_float = texture.pixel_format == TextureFormat.RGBA_FLOAT_16
        image = bpy.data.images.new(
            name,
            width=surface.width,
            height=surface.height,
            alpha=True,
            float_buffer=is_float,
        )
        image.colorspace_settings.name = (
            "sRGB"
            if texture.color_space == TextureColorSpace.SRGB and not is_float
            else "Non-Color"
        )
        image.alpha_mode = "STRAIGHT"
        image.pixels.foreach_set(surface.pixels)
        image["kz2_object_index"] = record.index
        image["kz2_object_id"] = record.display_id
        image["kz2_texture_type"] = texture.texture_type
        image["kz2_pixel_format"] = texture.pixel_format
        image["kz2_mip_count"] = texture.mip_count
        image["kz2_color_space"] = texture.color_space
        image["kz2_creation_flags"] = texture.creation_flags
        image["kz2_rsx_format"] = texture.descriptor.format
        image["kz2_rsx_remap"] = texture.descriptor.remap
        image.update()
        try:
            _pack_image(
                image,
                filename=f"KZ2_{record.display_id}",
                is_float=is_float,
            )
        except (OSError, RuntimeError) as exc:
            bpy.data.images.remove(image)
            raise ValueError(
                f"Could not pack decoded texture {record.display_id}: {exc}"
            ) from exc
        self._images[key] = image
        self.images_created += 1
        return image

    def _warn_once(self, warning: str) -> None:
        if warning in self._warned:
            return
        self._warned.add(warning)
        self.warnings.append(warning)


def _lightmap_node_group():
    for node_group in bpy.data.node_groups:
        if (
            node_group.get("kz2_group_type") == "lightmap_yuv"
            and node_group.get("kz2_group_version") == 1
        ):
            return node_group

    node_group = bpy.data.node_groups.new(
        "KZ2_LightmapYUV",
        "ShaderNodeTree",
    )
    node_group["kz2_group_type"] = "lightmap_yuv"
    node_group["kz2_group_version"] = 1
    node_group.interface.new_socket(
        name="Packed RGB",
        in_out="INPUT",
        socket_type="NodeSocketColor",
    )
    node_group.interface.new_socket(
        name="Luminance",
        in_out="INPUT",
        socket_type="NodeSocketFloat",
    )
    node_group.interface.new_socket(
        name="Indirect RGB",
        in_out="OUTPUT",
        socket_type="NodeSocketColor",
    )
    node_group.interface.new_socket(
        name="Sun Occlusion",
        in_out="OUTPUT",
        socket_type="NodeSocketFloat",
    )

    nodes = node_group.nodes
    links = node_group.links
    group_input = nodes.new("NodeGroupInput")
    group_input.location = (-900.0, 0.0)
    group_output = nodes.new("NodeGroupOutput")
    group_output.location = (500.0, 0.0)
    group_output.is_active_output = True

    separate = nodes.new("ShaderNodeSeparateColor")
    separate.mode = "RGB"
    separate.location = (-700.0, 0.0)
    links.new(
        group_input.outputs["Packed RGB"],
        separate.inputs["Color"],
    )

    packed_vector = nodes.new("ShaderNodeCombineXYZ")
    packed_vector.label = "(U, V, luminance)"
    packed_vector.location = (-500.0, 80.0)
    links.new(separate.outputs["Red"], packed_vector.inputs["X"])
    links.new(separate.outputs["Green"], packed_vector.inputs["Y"])
    links.new(group_input.outputs["Luminance"], packed_vector.inputs["Z"])

    def decoded_channel(
        name: str,
        coefficients: tuple[float, float, float],
        offset: float,
        y: float,
    ):
        dot = nodes.new("ShaderNodeVectorMath")
        dot.name = f"KZ2_{name}_Dot"
        dot.label = f"{name} YUV coefficients"
        dot.operation = "DOT_PRODUCT"
        dot.location = (-280.0, y)
        dot.inputs[1].default_value = coefficients
        links.new(packed_vector.outputs["Vector"], dot.inputs[0])

        add = nodes.new("ShaderNodeMath")
        add.operation = "ADD"
        add.location = (-80.0, y)
        add.inputs[1].default_value = offset
        links.new(dot.outputs["Value"], add.inputs[0])

        clamp = nodes.new("ShaderNodeClamp")
        clamp.location = (100.0, y)
        clamp.inputs["Min"].default_value = 0.0
        clamp.inputs["Max"].default_value = 1.0
        links.new(add.outputs["Value"], clamp.inputs["Value"])

        square = nodes.new("ShaderNodeMath")
        square.operation = "MULTIPLY"
        square.location = (280.0, y)
        links.new(clamp.outputs["Result"], square.inputs[0])
        links.new(clamp.outputs["Result"], square.inputs[1])

        scale = nodes.new("ShaderNodeMath")
        scale.operation = "MULTIPLY"
        scale.location = (460.0, y)
        scale.inputs[1].default_value = 4.0
        links.new(square.outputs["Value"], scale.inputs[0])
        return scale.outputs["Value"]

    red = decoded_channel("Red", (0.375, -0.125, 1.0), -0.125, 280.0)
    green = decoded_channel(
        "Green",
        (-0.125, -0.125, 1.0),
        0.125,
        40.0,
    )
    blue = decoded_channel(
        "Blue",
        (-0.125, 0.375, 1.0),
        -0.125,
        -200.0,
    )

    combine = nodes.new("ShaderNodeCombineColor")
    combine.mode = "RGB"
    combine.location = (680.0, 100.0)
    links.new(red, combine.inputs["Red"])
    links.new(green, combine.inputs["Green"])
    links.new(blue, combine.inputs["Blue"])
    links.new(combine.outputs["Color"], group_output.inputs["Indirect RGB"])

    sun_clamp = nodes.new("ShaderNodeClamp")
    sun_clamp.label = "Static sun occlusion"
    sun_clamp.location = (280.0, -430.0)
    sun_clamp.inputs["Min"].default_value = 0.0
    sun_clamp.inputs["Max"].default_value = 1.0
    links.new(separate.outputs["Blue"], sun_clamp.inputs["Value"])
    links.new(
        sun_clamp.outputs["Result"],
        group_output.inputs["Sun Occlusion"],
    )
    return node_group


def _pack_image(
    image: bpy.types.Image,
    *,
    filename: str,
    is_float: bool,
) -> None:
    suffix = ".exr" if is_float else ".png"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as temporary:
        temporary_path = Path(temporary.name)
    try:
        image.filepath_raw = str(temporary_path)
        image.file_format = "OPEN_EXR" if is_float else "PNG"
        image.save()
        image.pack()
        if image.packed_file is None:
            raise RuntimeError("Blender did not create a packed image")
    finally:
        temporary_path.unlink(missing_ok=True)
    image.filepath_raw = f"//{filename}{suffix}"
