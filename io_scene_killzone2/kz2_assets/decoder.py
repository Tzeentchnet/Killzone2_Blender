"""Lazy dispatch from CORE reflected types to typed intermediate objects."""

from __future__ import annotations

from collections.abc import Iterable

from .animation_graph import (
    parse_animation_state,
    parse_directional_animation,
    parse_skeleton_animation_bone_map,
    parse_time_mapped_skeleton_animation,
)
from .core import CoreFile, CoreObject, CoreReference
from .edge_animation import parse_skeleton_animation
from .entities import (
    parse_destructibility_part_state,
    parse_entity_resource,
    parse_model_part,
    parse_model_resource,
    parse_state_based_destructibility,
)
from .geometry import (
    parse_bone_bindings,
    parse_index_array,
    parse_lod_mesh,
    parse_multi_mesh,
    parse_primitive,
    parse_skin_info,
    parse_skinned_mesh,
    parse_static_mesh,
    parse_switch_mesh,
    parse_vertex_array,
)
from .materials import parse_render_effect, parse_shader_resource
from .model import UnknownObject
from .skeleton import parse_skeleton
from .textures import parse_texture


class CoreDecoder:
    def __init__(self, core: CoreFile) -> None:
        self.core = core
        self._cache: dict[int, object] = {}
        self.null_reference = CoreReference()

    def decode(self, obj: CoreObject | int) -> object:
        record = self.core.objects[obj] if isinstance(obj, int) else obj
        if record.index in self._cache:
            return self._cache[record.index]
        if record.direct_value is not None:
            self._cache[record.index] = record.direct_value
            return record.direct_value
        reader = self.core.reader_for(record)
        parser = _PARSERS.get(record.type_name)
        if parser is None:
            decoded: object = UnknownObject(record.type_name, record.payload_size)
        else:
            decoded = parser(self, record, reader)
        self._cache[record.index] = decoded
        return decoded

    def resolve(self, reference: CoreReference) -> object | None:
        if reference.local_index is not None:
            return self.decode(reference.local_index)
        return None

    def iter_type(self, *type_names: str) -> Iterable[tuple[CoreObject, object]]:
        accepted = set(type_names)
        for record in self.core.objects:
            if record.type_name in accepted:
                yield record, self.decode(record)

    def decode_supported(self) -> Iterable[tuple[CoreObject, object]]:
        for record in self.core.objects:
            if record.type_name in _PARSERS:
                yield record, self.decode(record)


_PARSERS = {
    "VertexArrayResource": parse_vertex_array,
    "IndexArrayResource": parse_index_array,
    "PrimitiveResource": parse_primitive,
    "RenderingPrimitiveResource": parse_primitive,
    "StaticMeshResource": parse_static_mesh,
    "LodMeshResource": parse_lod_mesh,
    "MultiMeshResource": parse_multi_mesh,
    "SwitchMeshResource": parse_switch_mesh,
    "RegularSkinnedMeshResource": parse_skinned_mesh,
    "RegularSkinnedMeshResourceSkinInfo": parse_skin_info,
    "SkinnedMeshBoneBindings": parse_bone_bindings,
    "Skeleton": parse_skeleton,
    "SkeletonAnimationBoneMap": parse_skeleton_animation_bone_map,
    "SkeletonAnimationResource": parse_skeleton_animation,
    "AnimationState": parse_animation_state,
    "TimeMappedSkeletonAnimationResource": (
        parse_time_mapped_skeleton_animation
    ),
    "DirectionalAnimationResource": parse_directional_animation,
    "Texture": parse_texture,
    "RenderEffectResource": parse_render_effect,
    "ShaderResource": parse_shader_resource,
    "EntityResource": parse_entity_resource,
    "StaticModelResource": parse_model_resource,
    "SkinnedModelResource": parse_model_resource,
    "ModelPartResource": parse_model_part,
    "DestructibilityPartStateResource": parse_destructibility_part_state,
    "StateBasedDestructibilityResource": parse_state_based_destructibility,
}

SUPPORTED_OBJECT_TYPES = frozenset(_PARSERS)
