"""Direct level-object serializers for Killzone 2 retail CORE files."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, TypeAlias

from .binary import MAX_COUNT, BinaryReader
from .core import CoreFile, CoreObject, CoreReference
from .model import Matrix4

if TYPE_CHECKING:
    from collections.abc import Callable


LEVEL_DIRECT_TYPES = frozenset(
    {
        "GameScript",
        "Portal",
        "StreamingHintTrigger",
        "PhysicsCollisionInstance",
        "AmbientSoundZone",
        "CollisionTrigger",
        "Zone",
        "EntityPlaceHolder",
        "AIMarker",
        "CollisionInstance",
        "StaticMeshInstance",
        "SoundMixResource",
        "ReverbZone",
        "Occluder",
        "RenderZone",
        "OmniLight",
        "SpotLight",
        "WindBox",
        "AmbientSoundPortal",
        "BlendExpression",
        "AIWaypointNeighbors",
        "AITerrainManager",
        "AINearestWaypoint",
        "AIAreaGraph",
        "AIWaypointAreaRadiusTable",
        "AIWaypointGrid",
        "AIArea",
        "AIWaypointBufferManager",
        "AILinkTypeInfoSpecialObject",
        "Camera",
        "WeakResourceReference",
        "SimpleAnimatingSkinnedMeshInstance",
        "ParticleSystemInstance",
        "CollisionMeshInstance",
        "CoronaInstance",
        "SunLight",
        "AnimationMotionCurve",
        "AnimationMotionDirectional",
        "PhysicsWaterPool",
        "MeshHierachyShaderOverrides",
        "LightAttachedCorona",
        "RenderEffectInstanceImp1",
        "AIAtmosphereBox",
        "LumpOptimizationSettingsGame",
    }
)


@dataclass(frozen=True, slots=True)
class WorldNode:
    parent: CoreReference
    orientation: Matrix4


@dataclass(frozen=True, slots=True)
class GeometryObjectInstance:
    world_node: WorldNode
    drawable_state: bytes


@dataclass(frozen=True, slots=True)
class TextureOverride:
    sampler: str
    texture: CoreReference
    mip_bias: float
    mip_bias_bits: int
    packed_sampler_state: int


@dataclass(frozen=True, slots=True)
class VariableOverride:
    variable: str
    variable_type: int
    raw_value: bytes
    animator: CoreReference


@dataclass(frozen=True, slots=True)
class RenderEffectOverride:
    render_effect: CoreReference
    texture_overrides: tuple[TextureOverride, ...]
    variable_overrides: tuple[VariableOverride, ...]


@dataclass(frozen=True, slots=True)
class MeshOverride:
    mesh: CoreReference
    hierarchy_path: tuple[int, ...]
    render_effect_overrides: tuple[RenderEffectOverride, ...]


@dataclass(frozen=True, slots=True)
class MeshHierarchyShaderOverrides:
    mesh_overrides: tuple[MeshOverride, ...]


@dataclass(frozen=True, slots=True)
class StaticMeshInstance:
    geometry: GeometryObjectInstance
    name: str
    flags: int
    static_model: CoreReference
    shader_overrides: MeshHierarchyShaderOverrides
    tail: bytes


@dataclass(frozen=True, slots=True)
class EntityPlaceHolder:
    name: str
    name_is_identifier: int
    orientation: Matrix4
    resource_class_name: str
    resource_name: str
    attributes: tuple[str, ...]
    linked_objects: tuple[CoreReference, ...]
    destructibility_state: int


@dataclass(frozen=True, slots=True)
class LevelAssetDescriptor:
    name: str
    active: int
    unknown: int
    location: str
    optimization_settings: CoreReference
    cache_persistent: int
    lump_location: str
    base_assets: tuple[CoreReference, ...]


@dataclass(frozen=True, slots=True)
class LevelSectionAsset:
    descriptor: CoreReference
    enabled: int


@dataclass(frozen=True, slots=True)
class LevelSectionDescriptor:
    name: str
    active: int
    unknown_0: int
    unknown_1: int
    assets: tuple[LevelSectionAsset, ...]


@dataclass(frozen=True, slots=True)
class CollisionInstance:
    world_node: WorldNode
    state: bytes
    name: str
    flags: bytes
    collision_resource: CoreReference


@dataclass(frozen=True, slots=True)
class PhysicsCollisionInstance:
    physics_resource: CoreReference
    fields: bytes
    orientation: Matrix4
    active: int
    center_of_mass: tuple[float, float, float]
    mass: float
    restitution: float
    friction: float
    linear_damping: float
    angular_damping: float


@dataclass(frozen=True, slots=True)
class Shape2D:
    geometry: GeometryObjectInstance
    name: str
    fields: bytes
    point_count: int
    points: bytes


@dataclass(frozen=True, slots=True)
class Shape2DExtrusion:
    shape: Shape2D
    extrusion_fields: bytes


@dataclass(frozen=True, slots=True)
class Light:
    geometry: GeometryObjectInstance
    name: str
    fields_0: bytes
    resource_0: CoreReference
    fields_1: bytes
    resource_1: CoreReference


@dataclass(frozen=True, slots=True)
class ShadowedLight:
    light: Light
    shadow_fields: bytes


@dataclass(frozen=True, slots=True)
class StructuredDirectObject:
    type_name: str
    fields: tuple[tuple[str, object], ...]

    def get(self, name: str) -> object:
        for field_name, value in self.fields:
            if field_name == name:
                return value
        raise KeyError(name)


@dataclass(frozen=True, slots=True)
class LuaConstant:
    tag: int
    value: bool | bytes | None


@dataclass(frozen=True, slots=True)
class LuaLocal:
    name: bytes
    start_pc: int
    end_pc: int


@dataclass(frozen=True, slots=True)
class LuaPrototype:
    source: bytes
    line_defined: int
    last_line_defined: int
    upvalue_count: int
    parameter_count: int
    vararg_flags: int
    max_stack_size: int
    instructions: bytes
    constants: tuple[LuaConstant, ...]
    nested: tuple[LuaPrototype, ...]
    line_info: tuple[int, ...]
    locals: tuple[LuaLocal, ...]
    upvalue_names: tuple[bytes, ...]


@dataclass(frozen=True, slots=True)
class GameScript:
    core_script_field_0: int
    core_script_field_1: bytes
    core_script_field_2: bytes
    core_script_object_or_index: int
    core_script_field_3: int
    game_script_object_or_index_0: int
    game_script_object_or_index_1: int
    zero_auxiliary_word: int | None
    lua_header: bytes
    root: LuaPrototype


DirectObject: TypeAlias = (
    StaticMeshInstance
    | EntityPlaceHolder
    | CollisionInstance
    | PhysicsCollisionInstance
    | MeshHierarchyShaderOverrides
    | StructuredDirectObject
    | GameScript
)


def parse_level_asset_descriptor(
    core: CoreFile,
    obj: CoreObject,
) -> LevelAssetDescriptor:
    if obj.type_name not in {
        "BaseConcreteAsset",
        "ConcreteAsset",
        "DependentConcreteAsset",
    }:
        raise ValueError(f"{obj.type_name} is not a level asset descriptor")
    reader = core.reader_for(obj)
    descriptor = LevelAssetDescriptor(
        name=core.read_string_index(reader),
        active=reader.u8(),
        unknown=reader.u8(),
        location=core.read_string_index(reader),
        optimization_settings=core.read_reference(reader),
        cache_persistent=reader.u8(),
        lump_location=core.read_string_index(reader),
        base_assets=(
            tuple(
                core.read_reference(reader)
                for _ in range(reader.count("base asset count"))
            )
            if obj.type_name == "DependentConcreteAsset"
            else ()
        ),
    )
    if reader.remaining:
        reader.fail(
            "level asset descriptor has trailing bytes",
            expected=0,
            observed=reader.remaining,
        )
    return descriptor


def parse_level_section(
    core: CoreFile,
    obj: CoreObject,
) -> LevelSectionDescriptor:
    if obj.type_name != "LevelSection":
        raise ValueError(f"{obj.type_name} is not a LevelSection")
    reader = core.reader_for(obj)
    name = core.read_string_index(reader)
    active = reader.u8()
    unknown_0 = reader.u8()
    unknown_1 = reader.u8()
    asset_count = reader.adaptive_index(
        len(core.strings()),
        "level-section asset count",
    )
    if asset_count > MAX_COUNT:
        reader.fail(
            "invalid level-section asset count",
            expected=f"0..{MAX_COUNT}",
            observed=asset_count,
        )
    assets = tuple(
        LevelSectionAsset(
            descriptor=core.read_reference(reader),
            enabled=reader.u8(),
        )
        for _ in range(asset_count)
    )
    if reader.remaining:
        reader.fail(
            "LevelSection has trailing bytes",
            expected=0,
            observed=reader.remaining,
        )
    return LevelSectionDescriptor(
        name=name,
        active=active,
        unknown_0=unknown_0,
        unknown_1=unknown_1,
        assets=assets,
    )


def parse_direct_object(
    core: CoreFile,
    obj: CoreObject,
    reader: BinaryReader,
) -> DirectObject:
    parser = _DIRECT_PARSERS.get(obj.type_name)
    if parser is None:
        reader.fail(
            "unsupported zero-sized direct serializer",
            expected="a registered retail level direct type",
            observed=obj.type_name,
        )
        raise AssertionError("unreachable")
    return parser(core, reader)


def _read_world_node(core: CoreFile, reader: BinaryReader) -> WorldNode:
    return WorldNode(
        parent=core.read_reference(reader),
        orientation=_read_matrix4(reader),
    )


def _read_geometry_object(
    core: CoreFile,
    reader: BinaryReader,
) -> GeometryObjectInstance:
    return GeometryObjectInstance(
        world_node=_read_world_node(core, reader),
        drawable_state=reader.read(12),
    )


def _read_shader_overrides(
    core: CoreFile,
    reader: BinaryReader,
) -> MeshHierarchyShaderOverrides:
    mesh_count = reader.count("mesh hierarchy override count")
    return MeshHierarchyShaderOverrides(
        tuple(_read_mesh_override(core, reader) for _ in range(mesh_count))
    )


def _read_mesh_override(core: CoreFile, reader: BinaryReader) -> MeshOverride:
    mesh = core.read_reference(reader)
    path_count = reader.count("mesh resource path length")
    path = tuple(reader.u32() for _ in range(path_count))
    effect_count = reader.count("render-effect override count")
    effects = tuple(
        _read_render_effect_override(core, reader) for _ in range(effect_count)
    )
    return MeshOverride(mesh, path, effects)


def _read_render_effect_override(
    core: CoreFile,
    reader: BinaryReader,
) -> RenderEffectOverride:
    render_effect = core.read_reference(reader)
    texture_count = reader.count("texture override count")
    textures: list[TextureOverride] = []
    for _ in range(texture_count):
        sampler = core.read_string_index(reader, "String")
        texture = core.read_reference(reader)
        mip_bias_bits = reader.u16()
        mip_bias = _half_to_float(mip_bias_bits)
        textures.append(
            TextureOverride(
                sampler,
                texture,
                mip_bias,
                mip_bias_bits,
                reader.u16(),
            )
        )
    variable_count = reader.count("variable override count")
    variables = tuple(
        VariableOverride(
            variable=core.read_string_index(reader, "String"),
            variable_type=reader.u32(),
            raw_value=reader.read(16),
            animator=core.read_reference(reader),
        )
        for _ in range(variable_count)
    )
    return RenderEffectOverride(render_effect, tuple(textures), variables)


def _structured(type_name: str, **fields: object) -> StructuredDirectObject:
    return StructuredDirectObject(type_name, tuple(fields.items()))


def _read_shape_2d(core: CoreFile, reader: BinaryReader) -> Shape2D:
    geometry = _read_geometry_object(core, reader)
    name = core.read_string_index(reader, "String")
    fields = reader.read(2)
    point_count = reader.count("shape point count")
    return Shape2D(
        geometry=geometry,
        name=name,
        fields=fields,
        point_count=point_count,
        points=reader.read(point_count * 8),
    )


def _read_shape_2d_extrusion(
    core: CoreFile,
    reader: BinaryReader,
) -> Shape2DExtrusion:
    return Shape2DExtrusion(
        shape=_read_shape_2d(core, reader),
        extrusion_fields=reader.read(4),
    )


def _read_light(core: CoreFile, reader: BinaryReader) -> Light:
    return Light(
        geometry=_read_geometry_object(core, reader),
        name=core.read_string_index(reader, "String"),
        fields_0=reader.read(13),
        resource_0=core.read_reference(reader),
        fields_1=reader.read(11),
        resource_1=core.read_reference(reader),
    )


def _read_shadowed_light(core: CoreFile, reader: BinaryReader) -> ShadowedLight:
    return ShadowedLight(
        light=_read_light(core, reader),
        shadow_fields=reader.read(32),
    )


def _parse_game_script(core: CoreFile, reader: BinaryReader) -> GameScript:
    del core
    field_0 = reader.u8()
    field_1 = reader.read(4)
    field_2 = reader.read(4)
    core_object_or_index = reader.compact_scalar()
    field_3 = reader.u8()
    game_object_or_index_0 = reader.compact_scalar()
    game_object_or_index_1 = reader.compact_scalar()
    _align_zero(reader, 4, "GameScript wrapper")
    zero_auxiliary_word = None
    if reader.remaining >= 4 and reader.data[
        reader.position : reader.position + 4
    ].tobytes() == bytes(4):
        zero_auxiliary_word = reader.u32()
    lua_header = reader.read(12)
    expected_header = bytes.fromhex("1b4c75615100000404040400")
    if lua_header != expected_header:
        reader.fail(
            "invalid GameScript Lua header",
            offset=reader.position - 12,
            expected=expected_header,
            observed=lua_header,
        )
    root = _read_lua_prototype(reader, depth=0)
    _align_zero(reader, 4, "GameScript payload")
    return GameScript(
        core_script_field_0=field_0,
        core_script_field_1=field_1,
        core_script_field_2=field_2,
        core_script_object_or_index=core_object_or_index,
        core_script_field_3=field_3,
        game_script_object_or_index_0=game_object_or_index_0,
        game_script_object_or_index_1=game_object_or_index_1,
        zero_auxiliary_word=zero_auxiliary_word,
        lua_header=lua_header,
        root=root,
    )


def _read_lua_prototype(reader: BinaryReader, *, depth: int) -> LuaPrototype:
    if depth > 128:
        reader.fail("GameScript Lua prototype nesting is too deep")
    source = _read_lua_string(reader)
    line_defined = reader.u32()
    last_line_defined = reader.u32()
    upvalue_count = reader.u8()
    parameter_count = reader.u8()
    vararg_flags = reader.u8()
    max_stack_size = reader.u8()
    instruction_count = _read_u32_count(reader, "Lua instruction count")
    instructions = reader.read(instruction_count * 4)
    constant_count = _read_u32_count(reader, "Lua constant count")
    constants: list[LuaConstant] = []
    for _ in range(constant_count):
        tag = reader.u8()
        if tag == 0:
            value: bool | bytes | None = None
        elif tag == 1:
            value = bool(reader.u8())
        elif tag == 3:
            value = reader.read(4)
        elif tag == 4:
            value = _read_lua_string(reader)
        else:
            reader.fail(
                "unsupported GameScript Lua constant tag",
                expected="0, 1, 3, or 4",
                observed=tag,
            )
        constants.append(LuaConstant(tag, value))
    nested_count = _read_u32_count(reader, "Lua nested-prototype count")
    nested = tuple(
        _read_lua_prototype(reader, depth=depth + 1)
        for _ in range(nested_count)
    )
    line_info_count = _read_u32_count(reader, "Lua line-info count")
    line_info = tuple(reader.u32() for _ in range(line_info_count))
    local_count = _read_u32_count(reader, "Lua local count")
    locals_ = tuple(
        LuaLocal(_read_lua_string(reader), reader.u32(), reader.u32())
        for _ in range(local_count)
    )
    upvalue_name_count = _read_u32_count(reader, "Lua upvalue-name count")
    upvalue_names = tuple(
        _read_lua_string(reader) for _ in range(upvalue_name_count)
    )
    return LuaPrototype(
        source=source,
        line_defined=line_defined,
        last_line_defined=last_line_defined,
        upvalue_count=upvalue_count,
        parameter_count=parameter_count,
        vararg_flags=vararg_flags,
        max_stack_size=max_stack_size,
        instructions=instructions,
        constants=tuple(constants),
        nested=nested,
        line_info=line_info,
        locals=locals_,
        upvalue_names=upvalue_names,
    )


def _read_lua_string(reader: BinaryReader) -> bytes:
    size = reader.u32()
    if size > reader.remaining:
        reader.fail(
            "GameScript Lua string is out of bounds",
            expected=f"at most {reader.remaining}",
            observed=size,
        )
    return reader.read(size)


def _parse_portal(
    core: CoreFile,
    reader: BinaryReader,
) -> StructuredDirectObject:
    name = core.read_string_index(reader, "String")
    name_is_identifier = reader.u8()
    vertex_count = reader.count("portal vertex count")
    vertices = tuple(_read_vector3(reader) for _ in range(vertex_count))
    return _structured(
        "Portal",
        name=name,
        name_is_identifier=name_is_identifier,
        vertices=vertices,
        front_zone=core.read_reference(reader),
        back_zone=core.read_reference(reader),
        culling_distance=_read_f32(reader, "portal culling distance"),
    )


def _parse_ambient_sound_portal(
    core: CoreFile,
    reader: BinaryReader,
) -> StructuredDirectObject:
    name = core.read_string_index(reader, "String")
    unknown_0 = reader.u8()
    vertex_count = reader.count("ambient-sound portal vertex count")
    return _structured(
        "AmbientSoundPortal",
        name=name,
        unknown_0=unknown_0,
        vertex_count=vertex_count,
        vertices=reader.read(vertex_count * 12),
        front_zone_or_resource=core.read_reference(reader),
        back_zone_or_resource=core.read_reference(reader),
        portal_fields=reader.read(8),
    )


def _parse_ambient_sound_zone(
    core: CoreFile,
    reader: BinaryReader,
) -> StructuredDirectObject:
    return _structured(
        "AmbientSoundZone",
        shape=_read_shape_2d_extrusion(core, reader),
        ambient_sound_fields=reader.read(12),
    )


def _parse_zone(
    core: CoreFile,
    reader: BinaryReader,
) -> StructuredDirectObject:
    name = core.read_string_index(reader, "String")
    zone_fields = reader.read(89)
    reference_count = reader.count("zone reference count")
    return _structured(
        "Zone",
        name=name,
        zone_fields=zone_fields,
        references=tuple(
            core.read_reference(reader) for _ in range(reference_count)
        ),
    )


def _parse_sound_mix_resource(
    core: CoreFile,
    reader: BinaryReader,
) -> StructuredDirectObject:
    shape = _read_shape_2d_extrusion(core, reader)
    value_count = reader.count("sound-mix value count")
    return _structured(
        "SoundMixResource",
        shape=shape,
        value_count=value_count,
        values=reader.read(value_count * 4),
        tail_fields=reader.read(4),
    )


def _parse_reverb_zone(
    core: CoreFile,
    reader: BinaryReader,
) -> StructuredDirectObject:
    return _structured(
        "ReverbZone",
        shape=_read_shape_2d_extrusion(core, reader),
        reverb_name_or_key=core.read_string_index(reader, "String"),
        reverb_fields=reader.read(8),
    )


def _parse_occluder(
    core: CoreFile,
    reader: BinaryReader,
) -> StructuredDirectObject:
    name = core.read_string_index(reader, "String")
    unknown_0 = reader.u8()
    element_count = reader.count("occluder element count")
    return _structured(
        "Occluder",
        name=name,
        unknown_0=unknown_0,
        element_count=element_count,
        elements=reader.read(element_count * 12),
    )


def _parse_render_zone(
    core: CoreFile,
    reader: BinaryReader,
) -> StructuredDirectObject:
    return _structured(
        "RenderZone",
        shape=_read_shape_2d_extrusion(core, reader),
        render_fields_0=reader.read(12),
        resource_0=core.read_reference(reader),
        render_fields_1=reader.read(45),
        resource_1=core.read_reference(reader),
        tail_fields=reader.read(4),
    )


def _parse_static_mesh_instance(
    core: CoreFile,
    reader: BinaryReader,
) -> StaticMeshInstance:
    return StaticMeshInstance(
        geometry=_read_geometry_object(core, reader),
        name=core.read_string_index(reader, "String"),
        flags=reader.u8(),
        static_model=core.read_reference(reader),
        shader_overrides=_read_shader_overrides(core, reader),
        tail=reader.read(8),
    )


def _parse_entity_placeholder(
    core: CoreFile,
    reader: BinaryReader,
) -> EntityPlaceHolder:
    name = core.read_string_index(reader, "String")
    name_is_identifier = reader.u8()
    orientation = _read_matrix4(reader)
    resource_class_name = core.read_string_index(reader, "String")
    resource_name = core.read_string_index(reader, "String")
    attribute_count = reader.count("entity attribute count")
    attributes = tuple(
        core.read_string_index(reader, "String") for _ in range(attribute_count)
    )
    linked_object_count = reader.count("entity linked-object count")
    return EntityPlaceHolder(
        name=name,
        name_is_identifier=name_is_identifier,
        orientation=orientation,
        resource_class_name=resource_class_name,
        resource_name=resource_name,
        attributes=attributes,
        linked_objects=tuple(
            core.read_reference(reader) for _ in range(linked_object_count)
        ),
        destructibility_state=reader.u32(),
    )


def _parse_collision_instance(
    core: CoreFile,
    reader: BinaryReader,
) -> CollisionInstance:
    return _read_collision_instance(core, reader)


def _read_collision_instance(
    core: CoreFile,
    reader: BinaryReader,
) -> CollisionInstance:
    return CollisionInstance(
        world_node=_read_world_node(core, reader),
        state=reader.read(4),
        name=core.read_string_index(reader, "String"),
        flags=reader.read(2),
        collision_resource=core.read_reference(reader),
    )


def _parse_physics_collision_instance(
    core: CoreFile,
    reader: BinaryReader,
) -> PhysicsCollisionInstance:
    return PhysicsCollisionInstance(
        physics_resource=core.read_reference(reader),
        fields=reader.read(8),
        orientation=_read_matrix4(reader),
        active=reader.u8(),
        center_of_mass=(reader.f32(), reader.f32(), reader.f32()),
        mass=reader.f32(),
        restitution=reader.f32(),
        friction=reader.f32(),
        linear_damping=reader.f32(),
        angular_damping=reader.f32(),
    )


def _parse_collision_mesh_instance(
    core: CoreFile,
    reader: BinaryReader,
) -> StructuredDirectObject:
    return _structured(
        "CollisionMeshInstance",
        collision=_read_collision_instance(core, reader),
        drawable_fields=reader.read(12),
        mesh_resource=core.read_reference(reader),
        material_overrides=_read_shader_overrides(core, reader),
        tail_fields=reader.read(4),
    )


def _parse_streaming_hint_trigger(
    core: CoreFile,
    reader: BinaryReader,
) -> StructuredDirectObject:
    return _structured(
        "StreamingHintTrigger",
        collision=_read_collision_instance(core, reader),
        trigger_fields_0=reader.read(4),
        streaming_key=core.read_string_index(reader, "String"),
        trigger_fields_1=reader.read(4),
    )


def _parse_collision_trigger(
    core: CoreFile,
    reader: BinaryReader,
) -> StructuredDirectObject:
    return _structured(
        "CollisionTrigger",
        collision=_read_collision_instance(core, reader),
        trigger_fields=reader.read(4),
    )


def _parse_physics_water_pool(
    core: CoreFile,
    reader: BinaryReader,
) -> StructuredDirectObject:
    return _structured(
        "PhysicsWaterPool",
        physics_or_water_resource=core.read_reference(reader),
        water_fields=reader.read(8),
    )


def _parse_simple_animating_skinned_mesh_instance(
    core: CoreFile,
    reader: BinaryReader,
) -> StructuredDirectObject:
    return _structured(
        "SimpleAnimatingSkinnedMeshInstance",
        geometry=_read_geometry_object(core, reader),
        name=core.read_string_index(reader, "String"),
        name_is_identifier=reader.u8(),
        skinned_mesh_resource=core.read_reference(reader),
        animation_state=reader.u32(),
        default_animation=core.read_reference(reader),
    )


def _parse_particle_system_instance(
    core: CoreFile,
    reader: BinaryReader,
) -> StructuredDirectObject:
    return _structured(
        "ParticleSystemInstance",
        geometry=_read_geometry_object(core, reader),
        name=core.read_string_index(reader, "String"),
        particle_fields=reader.read(2),
        particle_system_resource=core.read_reference(reader),
    )


def _parse_corona_instance(
    core: CoreFile,
    reader: BinaryReader,
) -> StructuredDirectObject:
    return _structured(
        "CoronaInstance",
        geometry=_read_geometry_object(core, reader),
        corona_resource=core.read_reference(reader),
    )


def _parse_light_attached_corona(
    core: CoreFile,
    reader: BinaryReader,
) -> StructuredDirectObject:
    return _structured(
        "LightAttachedCorona",
        geometry=_read_geometry_object(core, reader),
        corona_resource=core.read_reference(reader),
    )


def _parse_render_effect_instance(
    core: CoreFile,
    reader: BinaryReader,
) -> StructuredDirectObject:
    return _structured(
        "RenderEffectInstanceImp1",
        render_effect_resource=core.read_reference(reader),
    )


def _parse_blend_expression(
    core: CoreFile,
    reader: BinaryReader,
) -> StructuredDirectObject:
    name = core.read_string_index(reader, "String")
    value_count = reader.count("blend-expression value count")
    return _structured(
        "BlendExpression",
        name=name,
        value_count=value_count,
        values=reader.read(value_count * 4),
    )


def _parse_weak_resource_reference(
    core: CoreFile,
    reader: BinaryReader,
) -> StructuredDirectObject:
    return _structured(
        "WeakResourceReference",
        resource_class=core.read_string_index(reader, "String"),
        resource_name=core.read_string_index(reader, "String"),
    )


def _parse_omni_light(
    core: CoreFile,
    reader: BinaryReader,
) -> StructuredDirectObject:
    return _structured(
        "OmniLight",
        light=_read_shadowed_light(core, reader),
        omni_fields=reader.read(16),
    )


def _parse_spot_light(
    core: CoreFile,
    reader: BinaryReader,
) -> StructuredDirectObject:
    return _structured(
        "SpotLight",
        light=_read_shadowed_light(core, reader),
        spot_fields=reader.read(20),
        spot_resource=core.read_reference(reader),
    )


def _parse_sun_light(
    core: CoreFile,
    reader: BinaryReader,
) -> StructuredDirectObject:
    return _structured(
        "SunLight",
        light=_read_light(core, reader),
        sun_fields=reader.read(141),
    )


def _parse_wind_box(
    core: CoreFile,
    reader: BinaryReader,
) -> StructuredDirectObject:
    return _structured(
        "WindBox",
        world_node=_read_world_node(core, reader),
        name=core.read_string_index(reader, "String"),
        wind_fields=reader.read(45),
        wind_resource=core.read_reference(reader),
    )


def _parse_camera(
    core: CoreFile,
    reader: BinaryReader,
) -> StructuredDirectObject:
    return _structured(
        "Camera",
        world_node=_read_world_node(core, reader),
        name=core.read_string_index(reader, "String"),
        unknown_0=reader.u8(),
        camera_resource=core.read_reference(reader),
        camera_fields=reader.read(25),
    )


def _parse_ai_atmosphere_box(
    core: CoreFile,
    reader: BinaryReader,
) -> StructuredDirectObject:
    return _structured(
        "AIAtmosphereBox",
        world_node=_read_world_node(core, reader),
        atmosphere_fields=reader.read(4),
    )


def _parse_ai_marker(
    core: CoreFile,
    reader: BinaryReader,
) -> StructuredDirectObject:
    return _structured(
        "AIMarker",
        world_node=_read_world_node(core, reader),
        name=core.read_string_index(reader, "String"),
        marker_fields=reader.read(6),
    )


def _parse_ai_area(
    core: CoreFile,
    reader: BinaryReader,
) -> StructuredDirectObject:
    name = core.read_string_index(reader, "String")
    unknown_0 = reader.u8()
    value32_count = reader.count("AI area 32-bit value count")
    values32 = reader.read(value32_count * 4)
    middle_fields = reader.read(4)
    value16_count = reader.count("AI area 16-bit value count")
    values16 = reader.read(value16_count * 2)
    return _structured(
        "AIArea",
        name=name,
        unknown_0=unknown_0,
        value32_count=value32_count,
        values32=values32,
        middle_fields=middle_fields,
        value16_count=value16_count,
        values16=values16,
        area_type_or_key=core.read_string_index(reader, "String"),
    )


def _parse_ai_waypoint_neighbors(
    core: CoreFile,
    reader: BinaryReader,
) -> StructuredDirectObject:
    array32_count = reader.count("AI waypoint 32-byte array count")
    array32 = reader.read(array32_count * 32)
    array16_count = reader.count("AI waypoint 16-byte array count")
    array16 = reader.read(array16_count * 16)
    array60_count = reader.count("AI waypoint 60-byte array count")
    array60 = reader.read(array60_count * 60)
    reference_count = reader.count("AI waypoint reference count")
    return _structured(
        "AIWaypointNeighbors",
        array32_count=array32_count,
        array32=array32,
        array16_count=array16_count,
        array16=array16,
        array60_count=array60_count,
        array60=array60,
        references=tuple(
            core.read_reference(reader) for _ in range(reference_count)
        ),
        name_or_key=core.read_string_index(reader, "String"),
        unknown_0=reader.u8(),
    )


def _parse_ai_terrain_manager(
    core: CoreFile,
    reader: BinaryReader,
) -> StructuredDirectObject:
    return _structured(
        "AITerrainManager",
        resources=tuple(core.read_reference(reader) for _ in range(7)),
    )


def _parse_ai_nearest_waypoint(
    core: CoreFile,
    reader: BinaryReader,
) -> StructuredDirectObject:
    del core
    return _structured("AINearestWaypoint", waypoint_fields=reader.read(4))


def _parse_ai_area_graph(
    core: CoreFile,
    reader: BinaryReader,
) -> StructuredDirectObject:
    reference_count = reader.count("AI area-graph reference count")
    references = tuple(
        core.read_reference(reader) for _ in range(reference_count)
    )
    value_count = reader.count("AI area-graph value count")
    return _structured(
        "AIAreaGraph",
        references=references,
        value_count=value_count,
        values=reader.read(value_count),
    )


def _parse_ai_waypoint_area_radius_table(
    core: CoreFile,
    reader: BinaryReader,
) -> StructuredDirectObject:
    del core
    first_count = reader.count("AI waypoint radius first count")
    first_values = reader.read(first_count * 2)
    second_count = reader.count("AI waypoint radius second count")
    return _structured(
        "AIWaypointAreaRadiusTable",
        first_count=first_count,
        first_values=first_values,
        second_count=second_count,
        second_values=reader.read(second_count * 2),
    )


def _parse_ai_waypoint_grid(
    core: CoreFile,
    reader: BinaryReader,
) -> StructuredDirectObject:
    del core
    cell_count = reader.count("AI waypoint grid cell count")
    return _structured(
        "AIWaypointGrid",
        cell_count=cell_count,
        cells=reader.read(cell_count * 16),
    )


def _parse_ai_waypoint_buffer_manager(
    core: CoreFile,
    reader: BinaryReader,
) -> StructuredDirectObject:
    del core
    return _structured(
        "AIWaypointBufferManager",
        buffer_fields=reader.read(4),
    )


def _parse_ai_link_type_info_special_object(
    core: CoreFile,
    reader: BinaryReader,
) -> StructuredDirectObject:
    return _structured(
        "AILinkTypeInfoSpecialObject",
        class_or_type=core.read_string_index(reader, "String"),
        name_or_key=core.read_string_index(reader, "String"),
    )


def _parse_animation_motion_curve(
    core: CoreFile,
    reader: BinaryReader,
) -> StructuredDirectObject:
    del core
    curve_fields = reader.read(4)
    point_count = reader.count("animation motion-curve point count")
    points = reader.read(point_count * 12)
    value_count = reader.count("animation motion-curve value count")
    return _structured(
        "AnimationMotionCurve",
        curve_fields=curve_fields,
        point_count=point_count,
        points=points,
        value_count=value_count,
        values=reader.read(value_count * 4),
    )


def _parse_animation_motion_directional(
    core: CoreFile,
    reader: BinaryReader,
) -> StructuredDirectObject:
    directional_fields = reader.read(4)
    value_count = reader.count("animation motion-directional value count")
    values = reader.read(value_count * 4)
    reference_count = reader.count(
        "animation motion-directional reference count"
    )
    return _structured(
        "AnimationMotionDirectional",
        directional_fields=directional_fields,
        value_count=value_count,
        values=values,
        references=tuple(
            core.read_reference(reader) for _ in range(reference_count)
        ),
    )


def _parse_lump_optimization_settings(
    core: CoreFile,
    reader: BinaryReader,
) -> StructuredDirectObject:
    del core
    return _structured(
        "LumpOptimizationSettingsGame",
        settings_fields=reader.read(6),
    )


def _read_matrix4(reader: BinaryReader) -> Matrix4:
    rows = tuple(
        tuple(_read_f32(reader, "matrix component") for _ in range(4))
        for _ in range(4)
    )
    return rows  # type: ignore[return-value]


def _read_vector3(reader: BinaryReader) -> tuple[float, float, float]:
    return (
        _read_f32(reader, "vector component"),
        _read_f32(reader, "vector component"),
        _read_f32(reader, "vector component"),
    )


def _read_f32(reader: BinaryReader, label: str) -> float:
    offset = reader.position
    value = reader.f32()
    if not math.isfinite(value):
        reader.fail(
            f"non-finite {label}",
            offset=offset,
            observed=value,
        )
    return value


def _read_u32_count(reader: BinaryReader, label: str) -> int:
    offset = reader.position
    value = reader.u32()
    if value > MAX_COUNT:
        reader.fail(
            f"invalid {label}",
            offset=offset,
            expected=f"0..{MAX_COUNT}",
            observed=value,
        )
    return value


def _align_zero(reader: BinaryReader, alignment: int, label: str) -> None:
    padding = (-reader.position) & (alignment - 1)
    if not padding:
        return
    offset = reader.position
    value = reader.read(padding)
    if value != bytes(padding):
        reader.fail(
            f"nonzero {label} alignment padding",
            offset=offset,
            expected=bytes(padding),
            observed=value,
        )


def _half_to_float(bits: int) -> float:
    return BinaryReader(bits.to_bytes(2, "big")).f16()


_DIRECT_PARSERS: dict[
    str,
    Callable[[CoreFile, BinaryReader], DirectObject],
] = {
    "GameScript": _parse_game_script,
    "Portal": _parse_portal,
    "StreamingHintTrigger": _parse_streaming_hint_trigger,
    "AmbientSoundZone": _parse_ambient_sound_zone,
    "CollisionTrigger": _parse_collision_trigger,
    "Zone": _parse_zone,
    "AIMarker": _parse_ai_marker,
    "SoundMixResource": _parse_sound_mix_resource,
    "ReverbZone": _parse_reverb_zone,
    "Occluder": _parse_occluder,
    "RenderZone": _parse_render_zone,
    "OmniLight": _parse_omni_light,
    "SpotLight": _parse_spot_light,
    "WindBox": _parse_wind_box,
    "AmbientSoundPortal": _parse_ambient_sound_portal,
    "BlendExpression": _parse_blend_expression,
    "AIWaypointNeighbors": _parse_ai_waypoint_neighbors,
    "AITerrainManager": _parse_ai_terrain_manager,
    "AINearestWaypoint": _parse_ai_nearest_waypoint,
    "AIAreaGraph": _parse_ai_area_graph,
    "AIWaypointAreaRadiusTable": _parse_ai_waypoint_area_radius_table,
    "AIWaypointGrid": _parse_ai_waypoint_grid,
    "AIArea": _parse_ai_area,
    "AIWaypointBufferManager": _parse_ai_waypoint_buffer_manager,
    "AILinkTypeInfoSpecialObject": _parse_ai_link_type_info_special_object,
    "Camera": _parse_camera,
    "WeakResourceReference": _parse_weak_resource_reference,
    "SimpleAnimatingSkinnedMeshInstance": (
        _parse_simple_animating_skinned_mesh_instance
    ),
    "ParticleSystemInstance": _parse_particle_system_instance,
    "CollisionMeshInstance": _parse_collision_mesh_instance,
    "CoronaInstance": _parse_corona_instance,
    "SunLight": _parse_sun_light,
    "AnimationMotionCurve": _parse_animation_motion_curve,
    "AnimationMotionDirectional": _parse_animation_motion_directional,
    "PhysicsWaterPool": _parse_physics_water_pool,
    "LightAttachedCorona": _parse_light_attached_corona,
    "RenderEffectInstanceImp1": _parse_render_effect_instance,
    "AIAtmosphereBox": _parse_ai_atmosphere_box,
    "LumpOptimizationSettingsGame": _parse_lump_optimization_settings,
    "StaticMeshInstance": _parse_static_mesh_instance,
    "EntityPlaceHolder": _parse_entity_placeholder,
    "CollisionInstance": _parse_collision_instance,
    "PhysicsCollisionInstance": _parse_physics_collision_instance,
    "MeshHierachyShaderOverrides": _read_shader_overrides,
}
