"""Blender-independent intermediate representations for decoded KZ2 assets."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import TypeAlias

from .core import CoreReference


class VertexSemantic(IntEnum):
    POSITION = 0
    POSITION4 = 1
    WEIGHTS = 2
    TANGENT_QUATERNION = 3
    TANGENT = 4
    BINORMAL = 5
    NORMAL = 6
    COLOR = 7
    UV0 = 8
    UV1 = 9
    UV2 = 10
    UV3 = 11
    UV4 = 12
    UV5 = 13
    UV6 = 14
    MOTION_VECTOR = 15
    VECTOR4_BYTE0 = 16


class VertexStorage(IntEnum):
    UNDEFINED = 0
    SIGNED_SHORT_NORMALIZED = 1
    FLOAT = 2
    HALF_FLOAT = 3
    UNSIGNED_BYTE_NORMALIZED = 4
    SIGNED_SHORT = 5
    X11Y11Z10_NORMALIZED = 6
    UNSIGNED_BYTE = 7


class TextureType(IntEnum):
    TEXTURE_2D = 0
    CUBEMAP = 2


class TextureFormat(IntEnum):
    RGBA_8888 = 5
    RGBA_8888_REV = 6
    S3TC1 = 19
    S3TC3 = 20
    S3TC5 = 21
    RGBA_FLOAT_16 = 31


class TextureColorSpace(IntEnum):
    LINEAR = 0
    SRGB = 1


Vector: TypeAlias = tuple[float, ...]
Matrix4: TypeAlias = tuple[
    tuple[float, float, float, float],
    tuple[float, float, float, float],
    tuple[float, float, float, float],
    tuple[float, float, float, float],
]


@dataclass(frozen=True, slots=True)
class ResourcePrefix:
    name: str
    unknown: int


@dataclass(frozen=True, slots=True)
class EntityResource:
    prefix: ResourcePrefix
    animator: CoreReference
    lockable: int
    mover: CoreReference
    model: CoreReference
    destructibility: CoreReference
    trailing: bytes


@dataclass(frozen=True, slots=True)
class ModelResource:
    prefix: ResourcePrefix
    mesh: CoreReference
    trailing: bytes


@dataclass(frozen=True, slots=True)
class ModelPart:
    prefix: ResourcePrefix
    mesh: CoreReference
    trailing: bytes


@dataclass(frozen=True, slots=True)
class DestructibilityPartState:
    prefix: ResourcePrefix
    model_part: CoreReference
    trailing: bytes


@dataclass(frozen=True, slots=True)
class StateBasedDestructibility:
    prefix: ResourcePrefix
    header: bytes
    part_states: tuple[CoreReference, ...]
    part_fields: tuple[tuple[bytes, bytes], ...]
    trailing: bytes
    part_states_complete: bool


@dataclass(frozen=True, slots=True)
class RsxTextureDescriptor:
    format: int
    mipmap: int
    dimension: int
    cubemap: int
    remap: int
    width: int
    height: int
    depth: int
    location: int
    padding: int
    pitch: int
    offset: int

    @property
    def is_linear(self) -> bool:
        return bool(self.format & 0x20)


@dataclass(frozen=True, slots=True)
class Texture:
    prefix: ResourcePrefix
    texture_type: int
    width: int
    height: int
    pixel_format: int
    memory_type: int
    mip_count: int
    color_space: int
    texcoord_type: int
    creation_flags: int
    allocation_size: int
    allocation_constant: int
    payload_size: int
    state_words: tuple[int, int, int, int]
    descriptor: RsxTextureDescriptor
    state_fill: bytes
    payload: bytes


@dataclass(frozen=True, slots=True)
class RenderState:
    unknown_flag: int
    cull_mode: int
    blend_mode: int
    write_depth: int
    depth_test_func: int
    alpha_test_func: int
    alpha_test_value: int
    enable_depth_bias: int
    enable_depth_clamp: int
    color_mask: int
    technique_type: int
    raw: bytes


@dataclass(frozen=True, slots=True)
class TextureBinding:
    sampler: str
    texture: CoreReference
    mip_bias: float
    mip_bias_bits: int
    packed_sampler_state: int

    @property
    def filter_mode(self) -> int:
        return self.packed_sampler_state & 0x000F

    @property
    def address_u(self) -> int:
        return (self.packed_sampler_state >> 4) & 0x000F

    @property
    def address_v(self) -> int:
        return (self.packed_sampler_state >> 8) & 0x000F

    @property
    def anisotropy(self) -> int:
        return (self.packed_sampler_state >> 12) & 0x000F


@dataclass(frozen=True, slots=True)
class ShaderVariable:
    name: str
    variable_type: int
    raw_value: bytes
    value: tuple[float, ...] | None
    animator: CoreReference
    shader_input: str


@dataclass(frozen=True, slots=True)
class RenderTechnique:
    render_state: RenderState
    shader: CoreReference
    texture_bindings: tuple[TextureBinding, ...]
    variable_bindings: tuple[ShaderVariable, ...]
    reflection_sampler_bias: float


@dataclass(frozen=True, slots=True)
class RenderEffect:
    prefix: ResourcePrefix
    animator: CoreReference
    techniques: tuple[RenderTechnique, ...]
    trailing: bytes


@dataclass(frozen=True, slots=True)
class ShaderResource:
    prefix: ResourcePrefix
    ibl_shading_mode: int
    shader_object: bytes
    body_sha256: str


@dataclass(frozen=True, slots=True)
class VertexDeclaration:
    semantic: int
    byte_offset: int
    storage_type: int
    component_count: int
    unknown: int


@dataclass(frozen=True, slots=True)
class VertexStream:
    flags: int
    stride: int
    declarations: tuple[VertexDeclaration, ...]
    data: bytes


@dataclass(frozen=True, slots=True)
class VertexArray:
    prefix: ResourcePrefix
    vertex_count: int
    unknown: int
    streams: tuple[VertexStream, ...]


@dataclass(frozen=True, slots=True)
class IndexArray:
    prefix: ResourcePrefix
    indexes: tuple[int, ...]
    unknown: bytes


@dataclass(frozen=True, slots=True)
class Primitive:
    prefix: ResourcePrefix
    flags_or_unknown: bytes
    vertex_array: CoreReference
    index_array: CoreReference
    base_vertex: int
    spatial_tree: CoreReference
    render_unknown: bytes = b""
    render_effect: CoreReference = field(default_factory=CoreReference)


@dataclass(frozen=True, slots=True)
class StaticMesh:
    prefix: ResourcePrefix
    mesh_base_unknown: bytes
    primitives: tuple[CoreReference, ...]
    local_translate_scale: tuple[float, float, float, float]
    static_mesh_tail: bytes


@dataclass(frozen=True, slots=True)
class LodMeshPart:
    mesh: CoreReference
    distance: float


@dataclass(frozen=True, slots=True)
class LodMesh:
    prefix: ResourcePrefix
    mesh_base_unknown: bytes
    parts: tuple[LodMeshPart, ...]
    max_distance: float


@dataclass(frozen=True, slots=True)
class MultiMeshPart:
    mesh: CoreReference
    local_transform: Matrix4


@dataclass(frozen=True, slots=True)
class MultiMesh:
    prefix: ResourcePrefix
    mesh_base_unknown: bytes
    parts: tuple[MultiMeshPart, ...]


@dataclass(frozen=True, slots=True)
class SwitchMeshPart:
    mesh: CoreReference
    key: str


@dataclass(frozen=True, slots=True)
class SwitchMesh:
    prefix: ResourcePrefix
    mesh_base_unknown: bytes
    name: str
    default_key: str
    parts_use_the_same_mesh: int
    parts: tuple[SwitchMeshPart, ...]


@dataclass(frozen=True, slots=True)
class SkinVertex:
    quantized_position: tuple[int, int, int]
    weights: tuple[float, float, float]
    normal: tuple[float, float, float]
    bone_indices: tuple[int, int, int]
    binormal: tuple[float, float, float] | None = None
    tangent: tuple[float, float, float] | None = None


@dataclass(frozen=True, slots=True)
class SkinPart:
    skin_info_type: int
    unknown: bytes
    basic_vertices: tuple[SkinVertex, ...]
    nbt_vertices: tuple[SkinVertex, ...]

    @property
    def vertices(self) -> tuple[SkinVertex, ...]:
        if self.skin_info_type == 1 and self.nbt_vertices:
            return self.nbt_vertices
        if self.basic_vertices:
            return self.basic_vertices
        return self.nbt_vertices


@dataclass(frozen=True, slots=True)
class SkinInfo:
    prefix: ResourcePrefix | None
    parts: tuple[SkinPart, ...]


@dataclass(frozen=True, slots=True)
class BoneBindings:
    prefix: ResourcePrefix | None
    bone_names: tuple[str, ...]
    inverse_bind_matrices: tuple[Matrix4, ...]


@dataclass(frozen=True, slots=True)
class SkinnedMesh:
    prefix: ResourcePrefix
    mesh_base_unknown: bytes
    skeleton: CoreReference
    unknown: bytes
    bone_bindings: CoreReference
    skin_info: CoreReference
    primitives: tuple[CoreReference, ...]
    render_effects: tuple[CoreReference, ...]
    position_bounds_scale: tuple[float, float, float]
    position_bounds_offset: tuple[float, float, float]


@dataclass(frozen=True, slots=True)
class IkControl:
    name: str
    channel_names: tuple[str, ...]
    start_joint_index: int
    mid_joint_index: int
    end_joint_index: int
    hinge_axis: tuple[float, float, float]
    hinge_max_limit: float
    hinge_min_limit: float


@dataclass(frozen=True, slots=True)
class EdgeSkeletonHeader:
    total_size: int
    joint_lookup_count: int
    user_channel_lookup_count: int
    unknown_count: int
    unknown_reserved: int
    offsets: tuple[int, int, int, int, int, int]


@dataclass(frozen=True, slots=True)
class EdgeJoint:
    rotation_xyzw: tuple[float, float, float, float]
    translation: tuple[float, float, float]
    scale: tuple[float, float, float]
    parent_index: int | None


@dataclass(frozen=True, slots=True)
class Joint:
    name: str
    parent_name: str
    rotation_xyzw: tuple[float, float, float, float]
    translation: tuple[float, float, float]
    unknown: bytes


@dataclass(frozen=True, slots=True)
class Skeleton:
    prefix: ResourcePrefix
    bone_sets: tuple[str, ...]
    references: tuple[CoreReference, ...]
    unknown_records: tuple[bytes, ...]
    ik_controls: tuple[IkControl, ...]
    animation_channels: tuple[str, ...]
    edge_blob: bytes
    edge_header: EdgeSkeletonHeader
    edge_joints: tuple[EdgeJoint, ...]
    joints: tuple[Joint, ...]
    auxiliary_count: int
    secondary_count: int
    unknown_arrays: tuple[tuple[int, ...], tuple[int, ...]]


@dataclass(frozen=True, slots=True)
class SkeletonAnimationBoneMap:
    bone_names: tuple[str, ...]
    trailing: bytes


@dataclass(frozen=True, slots=True)
class AnimationState:
    prefix: ResourcePrefix
    animation: CoreReference
    trailing: bytes


@dataclass(frozen=True, slots=True)
class TimeMappedSkeletonAnimation:
    prefix: ResourcePrefix
    version: int
    wrapper_flags: int
    wrapper_index: int
    animation: CoreReference
    trailing: bytes


@dataclass(frozen=True, slots=True)
class DirectionalAnimationEntry:
    animation: CoreReference
    weight: float
    heading_degrees: float
    unknown: int


@dataclass(frozen=True, slots=True)
class DirectionalAnimation:
    prefix: ResourcePrefix
    version: int
    entries: tuple[DirectionalAnimationEntry, ...]
    trailing: bytes


@dataclass(frozen=True, slots=True)
class SkeletonAnimation:
    prefix: ResourcePrefix | None
    wrapper_version: int
    wrapper_flags: int
    wrapper_index: int
    duration_hint: float
    skeleton_reference: CoreReference
    trailing: bytes
    clip: object


@dataclass(frozen=True, slots=True)
class UnknownObject:
    type_name: str
    payload_size: int
