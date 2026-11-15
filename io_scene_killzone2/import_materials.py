"""Blender image and preview-material creation for decoded KZ2 resources."""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

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

KZ2_VERTEX_COLOR_ATTRIBUTE = "KZ2Color"


@dataclass(slots=True)
class MaterialBuilder:
    decoder: CoreDecoder
    warnings: list[str]
    external_resolver: ResourceResolver | None = None
    flip_uv_v: bool = True
    images_created: int = 0
    materials_created: int = 0
    _images: dict[tuple[str, int, bool], bpy.types.Image | None] = field(
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
                    f"Render effect {reference.external_id!r} could not be resolved"
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
                f"{record.type_name} {record.display_id} is not a decoded render effect"
            )
            return None
        if not decoded.techniques:
            self._warn_once(f"Render effect {record.display_id} has no techniques")
            return None
        if not 0 <= technique_index < len(decoded.techniques):
            self._warn_once(
                f"Render effect {record.display_id} has no technique {technique_index}"
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
                    node["kz2_mip_bias_bits"] = texture_override.mip_bias_bits
                    node["kz2_sampler_state"] = texture_override.packed_sampler_state
                    node.interpolation = (
                        "Closest"
                        if (texture_override.packed_sampler_state & 0xF) in (0, 1)
                        else "Linear"
                    )
                    address_u = (texture_override.packed_sampler_state >> 4) & 0xF
                    address_v = (texture_override.packed_sampler_state >> 8) & 0xF
                    if address_u == 1 and address_v == 1:
                        node.extension = "EXTEND"
                    elif address_u == 2 and address_v == 2:
                        node.extension = "MIRROR"
                    else:
                        node.extension = "REPEAT"
                    node.label = (
                        f"{texture_override.sampler} (instance override)"
                        if image is not None
                        else (f"{texture_override.sampler} (unresolved override)")
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
        material = bpy.data.materials.new(f"{base_name}_T{technique_index:02d}")
        # Blender 5.x materials always own a node tree; Material.use_nodes is
        # deprecated and scheduled for removal in Blender 6.0.
        material.diffuse_color = (0.8, 0.8, 0.8, 1.0)
        material["kz2_object_index"] = record.index
        material["kz2_object_id"] = record.display_id
        material["kz2_type"] = record.type_name
        material["kz2_technique_index"] = technique_index
        material["kz2_technique_count"] = len(effect.techniques)
        material["kz2_render_state"] = technique.render_state.raw.hex()
        material["kz2_reflection_sampler_bias"] = technique.reflection_sampler_bias
        material["kz2_render_effect_trailing"] = effect.trailing.hex()
        self._set_shader_properties(material, technique, decoder)
        shader_hash = material.get("kz2_shader_body_sha256")
        shader_object_id = material.get("kz2_shader_object_id")
        preview_profile = shader_preview_profile(
            shader_hash if isinstance(shader_hash, str) else None,
            tuple(binding.sampler for binding in technique.texture_bindings),
            shader_object_id=(
                shader_object_id if isinstance(shader_object_id, str) else None
            ),
            variable_signature=tuple(
                variable.shader_input for variable in technique.variable_bindings
            ),
            technique_index=technique_index,
        )
        material["kz2_profile_status"] = preview_profile.confidence
        if preview_profile.profile_id is not None:
            material["kz2_profile_id"] = preview_profile.profile_id
        if preview_profile.normal_sampler is not None:
            material["kz2_profile_normal_sampler"] = preview_profile.normal_sampler
            material["kz2_profile_normal_space"] = (
                preview_profile.normal_space or "unknown"
            )
            material["kz2_profile_normal_y_flip"] = (
                "unvalidated"
                if preview_profile.normal_y_flip is None
                else preview_profile.normal_y_flip
            )
        if preview_profile.detail_normal_sampler is not None:
            material["kz2_profile_detail_normal_sampler"] = (
                preview_profile.detail_normal_sampler
            )
            material["kz2_preview_detail_normal"] = "retained unconnected"
        if preview_profile.secondary_base_color_sampler is not None:
            material["kz2_profile_secondary_base_color_sampler"] = (
                preview_profile.secondary_base_color_sampler
            )
        if preview_profile.material_sampler is not None:
            material["kz2_profile_material_sampler"] = preview_profile.material_sampler
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
        if preview_profile.retained_sampler_roles:
            material["kz2_profile_retained_sampler_roles"] = "; ".join(
                f"{sampler}: {role}"
                for sampler, role in preview_profile.retained_sampler_roles
            )
        if preview_profile.non_color_samplers:
            material["kz2_profile_non_color_samplers"] = ", ".join(
                preview_profile.non_color_samplers
            )
        if preview_profile.opacity_expression != "texture_alpha":
            material["kz2_profile_opacity_expression"] = (
                preview_profile.opacity_expression
            )
        if preview_profile.base_color_vertex_color_power is not None:
            material["kz2_profile_vertex_color_power"] = (
                preview_profile.base_color_vertex_color_power
            )
        if preview_profile.vertex_color_emission_power is not None:
            material["kz2_profile_vertex_emission_power"] = (
                preview_profile.vertex_color_emission_power
            )
        if preview_profile.preview_composition is not None:
            material["kz2_profile_composition"] = (
                preview_profile.preview_composition
            )
        if preview_profile.surface_render_method is not None:
            material.surface_render_method = (
                preview_profile.surface_render_method
            )
        material["kz2_preview_diffuse_v_flip"] = preview_profile.flip_diffuse_v

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
        secondary_base_color_output = None
        secondary_base_color_binding_index = None
        secondary_base_color_sampler = None
        secondary_base_color_uv_map = "UV0"
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
                node.image = self._image_for(
                    resolved_texture,
                    force_non_color=(
                        preview_profile.sampler_uses_non_color_data(binding.sampler)
                    ),
                )
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
                    "shader_profile" if is_profile_base_color else "srgb_heuristic"
                )
                material["kz2_preview_uv_map"] = base_color_uv_map
            if (
                preview_profile.secondary_base_color_sampler == binding.sampler
                and node.image is not None
            ):
                secondary_base_color_output = node.outputs["Color"]
                secondary_base_color_binding_index = binding_index
                secondary_base_color_sampler = binding.sampler
                secondary_base_color_uv_map = (
                    preview_profile.secondary_base_color_uv_map
                )
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
                material["kz2_lightmap_sun_occlusion"] = "preserved node-group output"
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

        if base_color_output is not None and secondary_base_color_output is not None:
            base_color_mix = nodes.new("ShaderNodeMixRGB")
            base_color_mix.name = "KZ2_BaseColorMultiply"
            base_color_mix.label = "Shader profile: base * secondary"
            base_color_mix.blend_type = "MULTIPLY"
            base_color_mix.inputs["Fac"].default_value = 1.0
            base_color_mix.location = (100.0, 100.0)
            node_tree.links.new(
                base_color_output,
                base_color_mix.inputs["Color1"],
            )
            node_tree.links.new(
                secondary_base_color_output,
                base_color_mix.inputs["Color2"],
            )
            base_color_output = base_color_mix.outputs["Color"]
            material["kz2_preview_base_color_combine"] = "multiply"

        base_color_output, opacity_output = self._connect_profile_composition(
            nodes,
            node_tree,
            preview_profile,
            texture_nodes,
            material,
            base_color_output,
            opacity_output,
        )

        self._connect_profile_normal(
            nodes,
            node_tree,
            principled,
            preview_profile,
            texture_nodes,
            material,
        )
        self._connect_profile_specular(
            nodes,
            node_tree,
            principled,
            preview_profile,
            texture_nodes,
            material,
        )
        self._connect_profile_vertex_emission(
            nodes,
            node_tree,
            principled,
            preview_profile,
            material,
        )
        if base_color_binding_index is not None and base_color_sampler is not None:
            self._connect_diffuse_coordinates(
                nodes,
                node_tree,
                base_color_binding_index,
                base_color_sampler,
                uv_map=base_color_uv_map,
                flip_v=preview_profile.flip_diffuse_v,
            )
        if (
            secondary_base_color_binding_index is not None
            and secondary_base_color_sampler is not None
        ):
            self._connect_diffuse_coordinates(
                nodes,
                node_tree,
                secondary_base_color_binding_index,
                secondary_base_color_sampler,
                uv_map=secondary_base_color_uv_map,
                flip_v=False,
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
            material["kz2_lightmap_preview"] = "decoded indirect emission approximation"
        elif lightmap_binding_index is not None:
            material["kz2_lightmap_preview"] = "decoded outputs retained unconnected"
        node_tree.links.new(surface_output, output.inputs["Surface"])
        if base_color_sampler is not None:
            preview_node = texture_nodes[base_color_sampler]
            for node in nodes:
                node.select = False
            preview_node.select = True
            nodes.active = preview_node
        return material

    def _connect_profile_composition(
        self,
        nodes,
        node_tree,
        profile: ShaderPreviewProfile,
        texture_nodes: dict[str, bpy.types.ShaderNodeTexImage],
        material: bpy.types.Material,
        base_color_output,
        opacity_output,
    ):
        if profile.preview_composition is not None:
            if profile.preview_composition == "new_sun_layered_sky":
                return self._connect_new_sun_layered_sky(
                    nodes,
                    node_tree,
                    texture_nodes,
                    material,
                )
            self._warn_once(
                f"Material profile {profile.profile_id} has unsupported "
                f"composition {profile.preview_composition}"
            )
            return base_color_output, opacity_output

        needs_vertex_color = (
            profile.base_color_vertex_color_power is not None
            or profile.opacity_expression != "texture_alpha"
        )
        vertex_color = (
            self._vertex_color_node(nodes)
            if needs_vertex_color
            else None
        )
        if (
            base_color_output is not None
            and vertex_color is not None
            and profile.base_color_vertex_color_power is not None
        ):
            if profile.base_color_vertex_color_power != 2.0:
                self._warn_once(
                    f"Material profile {profile.profile_id} has unsupported "
                    "vertex-color power"
                )
            else:
                squared = nodes.new("ShaderNodeVectorMath")
                squared.name = "KZ2_VertexColorSquared"
                squared.label = "Source vertex RGB squared"
                squared.operation = "MULTIPLY"
                squared.location = (-140.0, 360.0)
                node_tree.links.new(
                    vertex_color.outputs["Color"],
                    squared.inputs[0],
                )
                node_tree.links.new(
                    vertex_color.outputs["Color"],
                    squared.inputs[1],
                )
                multiply = nodes.new("ShaderNodeVectorMath")
                multiply.name = "KZ2_NewSunVertexColorModulate"
                multiply.label = "Texture RGB * squared vertex RGB"
                multiply.operation = "MULTIPLY"
                multiply.location = (100.0, 320.0)
                node_tree.links.new(base_color_output, multiply.inputs[0])
                node_tree.links.new(
                    squared.outputs["Vector"],
                    multiply.inputs[1],
                )
                base_color_output = multiply.outputs["Vector"]
                material["kz2_preview_vertex_color"] = "RGB squared modulation"

        if (
            opacity_output is not None
            and vertex_color is not None
            and profile.opacity_expression == "texture_alpha_times_vertex_alpha"
        ):
            multiply = nodes.new("ShaderNodeMath")
            multiply.name = "KZ2_NewSunVertexAlpha"
            multiply.label = "Texture alpha * vertex alpha"
            multiply.operation = "MULTIPLY"
            multiply.location = (120.0, -300.0)
            node_tree.links.new(opacity_output, multiply.inputs[0])
            node_tree.links.new(
                vertex_color.outputs["Alpha"],
                multiply.inputs[1],
            )
            opacity_output = multiply.outputs[0]
        elif (
            opacity_output is not None
            and vertex_color is not None
            and profile.opacity_expression
            == "one_minus_vertex_alpha_over_texture_alpha"
        ):
            denominator = nodes.new("ShaderNodeMath")
            denominator.name = "KZ2_NewSunAlphaDenominator"
            denominator.label = "max(texture alpha, epsilon)"
            denominator.operation = "MAXIMUM"
            denominator.location = (-80.0, -380.0)
            denominator.inputs[1].default_value = 1.0e-6
            node_tree.links.new(opacity_output, denominator.inputs[0])

            divide = nodes.new("ShaderNodeMath")
            divide.name = "KZ2_NewSunVertexAlphaRatio"
            divide.label = "Vertex alpha / texture alpha"
            divide.operation = "DIVIDE"
            divide.location = (100.0, -380.0)
            node_tree.links.new(
                vertex_color.outputs["Alpha"],
                divide.inputs[0],
            )
            node_tree.links.new(denominator.outputs[0], divide.inputs[1])

            invert = nodes.new("ShaderNodeMath")
            invert.name = "KZ2_NewSunInverseAlpha"
            invert.label = "1 - vertex alpha / texture alpha"
            invert.operation = "SUBTRACT"
            invert.use_clamp = True
            invert.location = (280.0, -380.0)
            invert.inputs[0].default_value = 1.0
            node_tree.links.new(divide.outputs[0], invert.inputs[1])
            opacity_output = invert.outputs[0]
        elif profile.opacity_expression != "texture_alpha":
            self._warn_once(
                f"Material profile {profile.profile_id} has unsupported "
                f"opacity expression {profile.opacity_expression}"
            )

        return base_color_output, opacity_output

    def _connect_new_sun_layered_sky(
        self,
        nodes,
        node_tree,
        texture_nodes: dict[str, bpy.types.ShaderNodeTexImage],
        material: bpy.types.Material,
    ):
        required = {f"inSampler{index}" for index in range(8)}
        missing = sorted(required.difference(texture_nodes))
        if missing:
            self._warn_once(
                "New Sun layered sky profile is missing samplers "
                + ", ".join(missing)
            )
            return None, None

        def math_node(
            name,
            label,
            operation,
            left,
            right,
            location,
            *,
            clamp=False,
        ):
            node = nodes.new("ShaderNodeMath")
            node.name = name
            node.label = label
            node.operation = operation
            node.use_clamp = clamp
            node.location = location
            for socket, value in zip(node.inputs[:2], (left, right)):
                if isinstance(value, (int, float)):
                    socket.default_value = value
                else:
                    node_tree.links.new(value, socket)
            return node.outputs[0]

        def vector_node(name, label, operation, left, right, location):
            node = nodes.new("ShaderNodeVectorMath")
            node.name = name
            node.label = label
            node.operation = operation
            node.location = location
            for socket, value in zip(node.inputs[:2], (left, right)):
                if isinstance(value, tuple):
                    socket.default_value = value
                else:
                    node_tree.links.new(value, socket)
            return node.outputs["Vector"]

        def combine_node(name, label, values, location):
            node = nodes.new("ShaderNodeCombineXYZ")
            node.name = name
            node.label = label
            node.location = location
            for socket, value in zip(node.inputs, values):
                if isinstance(value, (int, float)):
                    socket.default_value = value
                else:
                    node_tree.links.new(value, socket)
            return node.outputs["Vector"]

        def separate_color(name, label, color, location):
            node = nodes.new("ShaderNodeSeparateColor")
            node.name = name
            node.label = label
            node.mode = "RGB"
            node.location = location
            node_tree.links.new(color, node.inputs["Color"])
            return node

        def mapping_node(name, label, texture, scale, offset, location):
            node = nodes.new("ShaderNodeMapping")
            node.name = name
            node.label = label
            node.location = location
            node.inputs["Scale"].default_value = (*scale, 1.0)
            node.inputs["Location"].default_value = (*offset, 0.0)
            node_tree.links.new(uv.outputs["UV"], node.inputs["Vector"])
            node_tree.links.new(
                node.outputs["Vector"],
                texture.inputs["Vector"],
            )

        uv = nodes.new("ShaderNodeUVMap")
        uv.name = "KZ2_NewSunSkyUV"
        uv.label = "New Sun source UV0"
        uv.uv_map = "UV0"
        uv.location = (-1450.0, 0.0)
        uv_channels = nodes.new("ShaderNodeSeparateXYZ")
        uv_channels.name = "KZ2_NewSunSkyUVChannels"
        uv_channels.location = (-1250.0, -760.0)
        node_tree.links.new(uv.outputs["UV"], uv_channels.inputs["Vector"])

        sampler1 = texture_nodes["inSampler1"]
        sampler1_secondary = nodes.new("ShaderNodeTexImage")
        sampler1_secondary.name = "KZ2_NewSunSampler1Secondary"
        sampler1_secondary.label = "inSampler1 (secondary coordinate)"
        sampler1_secondary.location = (-650.0, -1880.0)
        sampler1_secondary.image = sampler1.image
        sampler1_secondary.interpolation = sampler1.interpolation
        sampler1_secondary.extension = sampler1.extension
        sampler1_secondary.projection = sampler1.projection
        sampler1_secondary["kz2_sampler"] = "inSampler1"
        sampler1_secondary["kz2_secondary_sample"] = True
        for key in (
            "kz2_mip_bias",
            "kz2_mip_bias_bits",
            "kz2_sampler_state",
        ):
            if key in sampler1:
                sampler1_secondary[key] = sampler1[key]

        mapping_node(
            "KZ2_NewSunSampler1PrimaryUV",
            "TC3.zw",
            sampler1,
            (-0.35, 0.525),
            (-0.35, 0.475),
            (-1050.0, sampler1.location.y),
        )
        mapping_node(
            "KZ2_NewSunSampler1SecondaryUV",
            "TC3.xy",
            sampler1_secondary,
            (0.35, 0.0),
            (-0.35, 1.0),
            (-1050.0, sampler1_secondary.location.y),
        )
        mapping_node(
            "KZ2_NewSunSampler2UV",
            "TC5.zw",
            texture_nodes["inSampler2"],
            (-0.35, -0.35),
            (-0.35, 1.35),
            (-1050.0, texture_nodes["inSampler2"].location.y),
        )
        mapping_node(
            "KZ2_NewSunSampler3UV",
            "TC4.zw",
            texture_nodes["inSampler3"],
            (1.05, -1.05),
            (-0.35, 2.575),
            (-1050.0, texture_nodes["inSampler3"].location.y),
        )
        mapping_node(
            "KZ2_NewSunSampler4UV",
            "TC4.xy",
            texture_nodes["inSampler4"],
            (-2.8, -3.15),
            (-0.35, 4.15),
            (-1050.0, texture_nodes["inSampler4"].location.y),
        )
        mapping_node(
            "KZ2_NewSunSampler7UV",
            "TC5.xy",
            texture_nodes["inSampler7"],
            (-1.05, 0.7),
            (-0.35, 0.3),
            (-1050.0, texture_nodes["inSampler7"].location.y),
        )

        sampler0_coordinates = combine_node(
            "KZ2_NewSunSampler0UV",
            "TC2.xy default",
            (-0.35, 1.175, 0.0),
            (-1050.0, texture_nodes["inSampler0"].location.y),
        )
        node_tree.links.new(
            sampler0_coordinates,
            texture_nodes["inSampler0"].inputs["Vector"],
        )

        sampler1_primary_channels = separate_color(
            "KZ2_NewSunSampler1PrimaryChannels",
            "Primary cloud-coordinate mask",
            sampler1.outputs["Color"],
            (-420.0, -1050.0),
        )
        sampler1_secondary_channels = separate_color(
            "KZ2_NewSunSampler1SecondaryChannels",
            "Secondary opacity mask",
            sampler1_secondary.outputs["Color"],
            (-420.0, -1840.0),
        )
        sampler2_channels = separate_color(
            "KZ2_NewSunSampler2Channels",
            "Primary sky channels",
            texture_nodes["inSampler2"].outputs["Color"],
            (-420.0, 260.0),
        )
        sampler5_channels = separate_color(
            "KZ2_NewSunSampler5Channels",
            "Sky blend factor",
            texture_nodes["inSampler5"].outputs["Color"],
            (-420.0, -720.0),
        )
        sampler7_channels = separate_color(
            "KZ2_NewSunSampler7Channels",
            "Primary sky modulation",
            texture_nodes["inSampler7"].outputs["Color"],
            (-420.0, -1380.0),
        )
        sampler0_channels = separate_color(
            "KZ2_NewSunSampler0Channels",
            "Animated opacity threshold",
            texture_nodes["inSampler0"].outputs["Color"],
            (-420.0, -2160.0),
        )

        sampler5_y = math_node(
            "KZ2_NewSunSampler5V",
            "1.02 - UV.y",
            "SUBTRACT",
            1.02,
            uv_channels.outputs["Y"],
            (-1050.0, -720.0),
        )
        sampler5_coordinates = combine_node(
            "KZ2_NewSunSampler5UV",
            "Fragment-derived sampler5 coordinates",
            (
                sampler1_primary_channels.outputs["Red"],
                sampler5_y,
                0.0,
            ),
            (-850.0, -720.0),
        )
        node_tree.links.new(
            sampler5_coordinates,
            texture_nodes["inSampler5"].inputs["Vector"],
        )

        sampler6_x_offset = math_node(
            "KZ2_NewSunSampler6XOffset",
            "Sampler2.R * 0.1",
            "MULTIPLY",
            sampler2_channels.outputs["Red"],
            0.1,
            (-1050.0, -1120.0),
        )
        sampler6_x = math_node(
            "KZ2_NewSunSampler6U",
            "UV.x + cloud offset",
            "ADD",
            uv_channels.outputs["X"],
            sampler6_x_offset,
            (-850.0, -1120.0),
        )
        sampler6_y_scaled = math_node(
            "KZ2_NewSunSampler6VScale",
            "UV.y * -4",
            "MULTIPLY",
            uv_channels.outputs["Y"],
            -4.0,
            (-1050.0, -1240.0),
        )
        sampler6_y = math_node(
            "KZ2_NewSunSampler6V",
            "4.008 - 4 * UV.y",
            "ADD",
            sampler6_y_scaled,
            4.008,
            (-850.0, -1240.0),
        )
        sampler6_coordinates = combine_node(
            "KZ2_NewSunSampler6UV",
            "Fragment-derived sampler6 coordinates",
            (sampler6_x, sampler6_y, 0.0),
            (-650.0, -1200.0),
        )
        node_tree.links.new(
            sampler6_coordinates,
            texture_nodes["inSampler6"].inputs["Vector"],
        )

        vertex_color = self._vertex_color_node(nodes)
        vertex_squared = vector_node(
            "KZ2_NewSunVertexColorSquared",
            "Source vertex RGB squared",
            "MULTIPLY",
            vertex_color.outputs["Color"],
            vertex_color.outputs["Color"],
            (-360.0, 820.0),
        )
        vertex_channels = nodes.new("ShaderNodeSeparateXYZ")
        vertex_channels.name = "KZ2_NewSunVertexChannels"
        vertex_channels.location = (-140.0, 820.0)
        node_tree.links.new(
            vertex_squared,
            vertex_channels.inputs["Vector"],
        )

        sampler7_green_scale = math_node(
            "KZ2_NewSunSampler7GreenScale",
            "Sampler7.R * 0.563833",
            "MULTIPLY",
            sampler7_channels.outputs["Red"],
            0.563833,
            (-140.0, 640.0),
        )
        sampler7_green = math_node(
            "KZ2_NewSunSampler7Green",
            "1 - green modulation",
            "SUBTRACT",
            1.0,
            sampler7_green_scale,
            (40.0, 640.0),
        )
        sampler7_blue = math_node(
            "KZ2_NewSunSampler7Blue",
            "1 - blue modulation",
            "SUBTRACT",
            1.0,
            sampler7_channels.outputs["Red"],
            (40.0, 560.0),
        )
        primary_tint = combine_node(
            "KZ2_NewSunPrimaryTint",
            "Primary sky tint",
            (1.0, sampler7_green, sampler7_blue),
            (220.0, 620.0),
        )
        primary_tinted = vector_node(
            "KZ2_NewSunPrimaryTinted",
            "Sampler2 * primary tint",
            "MULTIPLY",
            texture_nodes["inSampler2"].outputs["Color"],
            primary_tint,
            (420.0, 620.0),
        )
        primary_weight = math_node(
            "KZ2_NewSunPrimaryWeight",
            "1 - squared vertex green",
            "SUBTRACT",
            1.0,
            vertex_channels.outputs["Y"],
            (220.0, 500.0),
        )
        primary_weight_vector = combine_node(
            "KZ2_NewSunPrimaryWeightVector",
            "Primary sky weight",
            (primary_weight, primary_weight, primary_weight),
            (420.0, 500.0),
        )
        primary_color = vector_node(
            "KZ2_NewSunPrimaryColor",
            "Weighted primary sky",
            "MULTIPLY",
            primary_tinted,
            primary_weight_vector,
            (620.0, 600.0),
        )

        secondary_product = vector_node(
            "KZ2_NewSunSecondaryProduct",
            "Sampler3 * sampler4",
            "MULTIPLY",
            texture_nodes["inSampler3"].outputs["Color"],
            texture_nodes["inSampler4"].outputs["Color"],
            (220.0, 300.0),
        )
        secondary_color = vector_node(
            "KZ2_NewSunSecondaryColor",
            "Secondary sky + source bias",
            "ADD",
            secondary_product,
            (0.3, 0.2378, 0.0),
            (420.0, 300.0),
        )

        sampler5_scale = math_node(
            "KZ2_NewSunSampler5Scale",
            "Sampler5.R * 20",
            "MULTIPLY",
            sampler5_channels.outputs["Red"],
            20.0,
            (40.0, 160.0),
        )
        sampler1_scale = math_node(
            "KZ2_NewSunSampler1Scale",
            "Sampler1.R * 0.04",
            "MULTIPLY",
            sampler1_primary_channels.outputs["Red"],
            0.04,
            (40.0, 80.0),
        )
        blend_sum = math_node(
            "KZ2_NewSunBlendSum",
            "Sky blend response",
            "ADD",
            sampler5_scale,
            sampler1_scale,
            (220.0, 120.0),
        )
        blend_factor = math_node(
            "KZ2_NewSunBlendFactor",
            "Vertex alpha * sky blend response",
            "MULTIPLY",
            vertex_color.outputs["Alpha"],
            blend_sum,
            (420.0, 120.0),
        )
        inverse_blend = math_node(
            "KZ2_NewSunInverseBlend",
            "1 - sky blend factor",
            "SUBTRACT",
            1.0,
            blend_factor,
            (600.0, 80.0),
        )
        blend_vector = combine_node(
            "KZ2_NewSunBlendVector",
            "Sky blend factor",
            (blend_factor, blend_factor, blend_factor),
            (620.0, 180.0),
        )
        inverse_blend_vector = combine_node(
            "KZ2_NewSunInverseBlendVector",
            "Inverse sky blend factor",
            (inverse_blend, inverse_blend, inverse_blend),
            (780.0, 80.0),
        )
        primary_blend = vector_node(
            "KZ2_NewSunPrimaryBlend",
            "Primary * (1 - factor)",
            "MULTIPLY",
            primary_color,
            inverse_blend_vector,
            (820.0, 520.0),
        )
        secondary_blend = vector_node(
            "KZ2_NewSunSecondaryBlend",
            "Secondary * factor",
            "MULTIPLY",
            secondary_color,
            blend_vector,
            (820.0, 300.0),
        )
        layered_color = vector_node(
            "KZ2_NewSunLayeredColor",
            "Primary + secondary sky",
            "ADD",
            primary_blend,
            secondary_blend,
            (1020.0, 420.0),
        )

        inverse_red = math_node(
            "KZ2_NewSunInverseVertexRed",
            "1 - squared vertex red",
            "SUBTRACT",
            1.0,
            vertex_channels.outputs["X"],
            (600.0, -100.0),
        )
        inverse_red_vector = combine_node(
            "KZ2_NewSunInverseVertexRedVector",
            "Inverse squared vertex red",
            (inverse_red, inverse_red, inverse_red),
            (780.0, -100.0),
        )
        modulated_color = vector_node(
            "KZ2_NewSunModulatedColor",
            "Sampler6 * inverse vertex red",
            "MULTIPLY",
            texture_nodes["inSampler6"].outputs["Color"],
            inverse_red_vector,
            (980.0, -60.0),
        )
        vertex_red_vector = combine_node(
            "KZ2_NewSunVertexRedVector",
            "Squared vertex red",
            (
                vertex_channels.outputs["X"],
                vertex_channels.outputs["X"],
                vertex_channels.outputs["X"],
            ),
            (980.0, -180.0),
        )
        final_modulation = vector_node(
            "KZ2_NewSunFinalModulation",
            "Vertex red + sampler6 modulation",
            "ADD",
            modulated_color,
            vertex_red_vector,
            (1180.0, -80.0),
        )
        final_color = vector_node(
            "KZ2_NewSunSkyFinalColor",
            "Layered sky * final modulation",
            "MULTIPLY",
            layered_color,
            final_modulation,
            (1260.0, 300.0),
        )

        opacity_mask_scale = math_node(
            "KZ2_NewSunOpacityMaskScale",
            "Sampler1.B * 14.2857",
            "MULTIPLY",
            sampler1_secondary_channels.outputs["Blue"],
            14.2857,
            (600.0, -360.0),
        )
        opacity_mask = math_node(
            "KZ2_NewSunOpacityMask",
            "Scaled sampler1 mask * squared vertex blue",
            "MULTIPLY",
            opacity_mask_scale,
            vertex_channels.outputs["Z"],
            (780.0, -360.0),
            clamp=True,
        )
        opacity_vertex_blue = math_node(
            "KZ2_NewSunOpacityVertexBlue",
            "Squared vertex blue * 1.02857",
            "MULTIPLY",
            vertex_channels.outputs["Z"],
            1.02857,
            (600.0, -460.0),
        )
        opacity_threshold = math_node(
            "KZ2_NewSunOpacityThreshold",
            "Sampler0.R + vertex threshold",
            "ADD",
            sampler0_channels.outputs["Red"],
            opacity_vertex_blue,
            (780.0, -460.0),
        )
        inverse_opacity_mask = math_node(
            "KZ2_NewSunInverseOpacityMask",
            "1 - opacity mask",
            "SUBTRACT",
            1.0,
            opacity_mask,
            (960.0, -420.0),
        )
        threshold_term = math_node(
            "KZ2_NewSunOpacityThresholdTerm",
            "Threshold * inverse mask",
            "MULTIPLY",
            opacity_threshold,
            inverse_opacity_mask,
            (1140.0, -460.0),
        )
        mask_term = math_node(
            "KZ2_NewSunOpacityMaskTerm",
            "Opacity mask * 4.32",
            "MULTIPLY",
            opacity_mask,
            4.32,
            (1140.0, -360.0),
        )
        opacity_sum = math_node(
            "KZ2_NewSunOpacitySum",
            "Source opacity response",
            "ADD",
            threshold_term,
            mask_term,
            (1320.0, -420.0),
        )
        opacity = math_node(
            "KZ2_NewSunSkyAlpha",
            "1 - source opacity response",
            "SUBTRACT",
            1.0,
            opacity_sum,
            (1500.0, -420.0),
            clamp=True,
        )

        material["kz2_preview_base_color"] = "eight-sampler sky composition"
        material["kz2_preview_base_color_source"] = "shader_profile"
        material["kz2_preview_opacity"] = "shader-computed"
        material["kz2_preview_uv_map"] = "UV0"
        material["kz2_preview_vertex_color"] = "RGB squared; alpha linear"
        material["kz2_preview_base_color_combine"] = (
            "decoded New Sun layered sky"
        )
        preview_node = texture_nodes["inSampler2"]
        for node in nodes:
            node.select = False
        preview_node.select = True
        nodes.active = preview_node
        return final_color, opacity

    @staticmethod
    def _vertex_color_node(nodes):
        existing = nodes.get("KZ2_VertexColor")
        if existing is not None:
            return existing
        node = nodes.new("ShaderNodeVertexColor")
        node.name = "KZ2_VertexColor"
        node.label = f"KZ2 {KZ2_VERTEX_COLOR_ATTRIBUTE}"
        node.layer_name = KZ2_VERTEX_COLOR_ATTRIBUTE
        node.location = (-420.0, 860.0)
        return node

    def _connect_profile_normal(
        self,
        nodes,
        node_tree,
        principled,
        profile: ShaderPreviewProfile,
        texture_nodes: dict[str, bpy.types.ShaderNodeTexImage],
        material: bpy.types.Material,
    ) -> None:
        if profile.normal_sampler is None:
            return
        if profile.normal_y_flip is None:
            material["kz2_preview_normal_status"] = "unvalidated-y-sign"
            return
        spaces = {
            "tangent": "TANGENT",
            "object": "OBJECT",
            "world": "WORLD",
        }
        node_space = spaces.get(profile.normal_space or "")
        if node_space is None:
            self._warn_once(
                f"Material profile {profile.profile_id} has invalid normal "
                "space configuration"
            )
            return
        texture = texture_nodes.get(profile.normal_sampler)
        if texture is None or texture.image is None:
            self._warn_once(
                f"Normal sampler {profile.normal_sampler} could not be resolved"
            )
            return
        if texture.image.colorspace_settings.name != "Non-Color":
            self._warn_once(
                f"Normal sampler {profile.normal_sampler} is not marked Non-Color"
            )
            return

        encoded_normal = texture.outputs["Color"]
        preview_y_flip = bool(profile.normal_y_flip)
        if node_space == "TANGENT":
            preview_y_flip ^= self.flip_uv_v
        if preview_y_flip:
            separate = nodes.new("ShaderNodeSeparateColor")
            separate.name = "KZ2_NormalChannels"
            separate.label = "KZ2 encoded normal channels"
            separate.mode = "RGB"
            separate.location = (-350.0, texture.location.y - 100.0)
            node_tree.links.new(encoded_normal, separate.inputs["Color"])

            invert_y = nodes.new("ShaderNodeMath")
            invert_y.name = "KZ2_NormalYInvert"
            invert_y.label = "Normal Y: 1 - G"
            invert_y.operation = "SUBTRACT"
            invert_y.location = (-150.0, texture.location.y - 100.0)
            invert_y.inputs[0].default_value = 1.0
            node_tree.links.new(
                separate.outputs["Green"],
                invert_y.inputs[1],
            )

            combine = nodes.new("ShaderNodeCombineColor")
            combine.name = "KZ2_NormalColor"
            combine.label = "KZ2 Blender normal encoding"
            combine.mode = "RGB"
            combine.location = (40.0, texture.location.y - 100.0)
            node_tree.links.new(
                separate.outputs["Red"],
                combine.inputs["Red"],
            )
            node_tree.links.new(
                invert_y.outputs[0],
                combine.inputs["Green"],
            )
            node_tree.links.new(
                separate.outputs["Blue"],
                combine.inputs["Blue"],
            )
            encoded_normal = combine.outputs["Color"]

        normal_map = nodes.new("ShaderNodeNormalMap")
        normal_map.name = "KZ2_NormalMap"
        normal_map.label = f"KZ2 {profile.normal_space}-space normal"
        normal_map.space = node_space
        normal_map.location = (220.0, texture.location.y - 100.0)
        if node_space == "TANGENT":
            normal_map.uv_map = "DiffuseUV"
        node_tree.links.new(encoded_normal, normal_map.inputs["Color"])
        normal_socket = principled.inputs.get("Normal")
        if normal_socket is not None:
            node_tree.links.new(normal_map.outputs["Normal"], normal_socket)
        material["kz2_preview_normal_status"] = "connected"
        material["kz2_preview_normal_space"] = profile.normal_space
        material["kz2_preview_normal_y_flip"] = preview_y_flip
        material["kz2_normal_decode"] = "RGB * 2 - 1"

    def _connect_profile_specular(
        self,
        nodes,
        node_tree,
        principled,
        profile: ShaderPreviewProfile,
        texture_nodes: dict[str, bpy.types.ShaderNodeTexImage],
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
        material["kz2_roughness_approximation"] = "sqrt(2 / (specular_power + 2))"

    def _connect_profile_vertex_emission(
        self,
        nodes,
        node_tree,
        principled,
        profile: ShaderPreviewProfile,
        material: bpy.types.Material,
    ) -> None:
        power = profile.vertex_color_emission_power
        if power is None:
            return
        if power != 2.0:
            self._warn_once(
                f"Material profile {profile.profile_id} has unsupported "
                "vertex-emission power"
            )
            return
        emission_socket = principled.inputs.get("Emission Color")
        if emission_socket is None:
            emission_socket = principled.inputs.get("Emission")
        if emission_socket is None:
            self._warn_once(
                f"Material profile {profile.profile_id} cannot connect "
                "vertex emission to this Principled BSDF"
            )
            return

        vertex_color = self._vertex_color_node(nodes)
        squared = nodes.new("ShaderNodeVectorMath")
        squared.name = "KZ2_VertexEmissionSquared"
        squared.label = "Source vertex RGB squared emission"
        squared.operation = "MULTIPLY"
        squared.location = (80.0, -520.0)
        node_tree.links.new(
            vertex_color.outputs["Color"],
            squared.inputs[0],
        )
        node_tree.links.new(
            vertex_color.outputs["Color"],
            squared.inputs[1],
        )
        node_tree.links.new(squared.outputs["Vector"], emission_socket)
        strength_socket = principled.inputs.get("Emission Strength")
        if strength_socket is not None:
            strength_socket.default_value = 1.0
        material["kz2_preview_vertex_emission"] = "RGB squared"

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
        texture_node = nodes.get(f"KZ2_{binding_index:02d}_{sampler}")
        if texture_node is None:
            return
        uv = nodes.new("ShaderNodeUVMap")
        uv.name = "KZ2_DiffuseUV" if uv_map == "DiffuseUV" else "KZ2_BaseColorUV"
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
        texture_node = nodes.get(f"KZ2_{binding_index:02d}_inSampler_LM")
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
        if reference.external_id is not None and self.external_resolver is not None:
            self.external_resolver.add_requested_ids({reference.external_id})
            return self.external_resolver.resolve(
                reference.external_id,
                expected_type=expected_type,
            )
        return None

    def _image_for(
        self,
        resolved: ResolvedResource,
        *,
        force_non_color: bool = False,
    ) -> bpy.types.Image | None:
        texture = resolved.value
        assert isinstance(texture, Texture)
        record = resolved.record
        is_float = texture.pixel_format == TextureFormat.RGBA_FLOAT_16
        override_non_color = (
            force_non_color
            and texture.color_space == TextureColorSpace.SRGB
            and not is_float
        )
        key = (
            resolved.decoder.core.source,
            record.index,
            override_non_color,
        )
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
            self._warn_once(f"Texture {record.display_id} could not be decoded: {exc}")
            self._images[key] = None
            return None

        name = texture.prefix.name or f"KZ2_{record.display_id[:12]}"
        if override_non_color:
            name = f"{name}_NonColor"
        image = bpy.data.images.new(
            name,
            width=surface.width,
            height=surface.height,
            alpha=True,
            float_buffer=is_float,
        )
        image.colorspace_settings.name = (
            "sRGB"
            if (
                texture.color_space == TextureColorSpace.SRGB
                and not is_float
                and not force_non_color
            )
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
        if override_non_color:
            image["kz2_color_space_override"] = "shader_profile"
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
    # Packing a generated image encodes it in memory (PNG for byte buffers,
    # OpenEXR for float buffers), so no temporary file round-trip is needed.
    suffix = ".exr" if is_float else ".png"
    image.file_format = "OPEN_EXR" if is_float else "PNG"
    image.pack()
    if image.packed_file is None:
        raise RuntimeError("Blender did not create a packed image")
    image.filepath_raw = f"//{filename}{suffix}"
