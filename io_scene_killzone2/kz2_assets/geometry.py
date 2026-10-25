"""Geometry, skinning, and vertex-format decoders."""

from __future__ import annotations

import struct
from typing import TYPE_CHECKING

from .binary import BinaryReader
from .model import (
    BoneBindings,
    IndexArray,
    LodMesh,
    LodMeshPart,
    Matrix4,
    MultiMesh,
    MultiMeshPart,
    Primitive,
    ResourcePrefix,
    SkinInfo,
    SkinnedMesh,
    SkinPart,
    SkinVertex,
    StaticMesh,
    SwitchMesh,
    SwitchMeshPart,
    VertexArray,
    VertexDeclaration,
    VertexStorage,
    VertexStream,
)

if TYPE_CHECKING:
    from .core import CoreObject
    from .decoder import CoreDecoder


MAX_VERTICES = 20_000_000
MAX_STREAMS = 64
MAX_DECLARATIONS = 64
STATIC_MESH_TAIL_SIZE = 35


def read_resource_prefix(
    decoder: CoreDecoder,
    reader: BinaryReader,
) -> ResourcePrefix:
    return ResourcePrefix(
        name=decoder.core.read_string_index(reader, "String"),
        unknown=reader.u8(),
    )


def read_mesh_prefix(
    decoder: CoreDecoder,
    reader: BinaryReader,
) -> tuple[ResourcePrefix, bytes]:
    prefix = read_resource_prefix(decoder, reader)
    return prefix, reader.read(29)


def parse_vertex_array(
    decoder: CoreDecoder,
    obj: CoreObject,
    reader: BinaryReader,
) -> VertexArray:
    prefix = read_resource_prefix(decoder, reader)
    reader.align(4)
    vertex_count = _bounded_i32(reader, "vertex count", MAX_VERTICES)
    unknown = reader.i32()
    stream_count = _bounded_i32(reader, "vertex stream count", MAX_STREAMS)

    headers: list[tuple[int, int, tuple[VertexDeclaration, ...]]] = []
    for _ in range(stream_count):
        flags = reader.i32()
        stride = _bounded_i32(reader, "vertex stride", 4096, minimum=1)
        declaration_count = _bounded_i32(
            reader,
            "vertex declaration count",
            MAX_DECLARATIONS,
        )
        declarations = tuple(
            VertexDeclaration(
                semantic=reader.u32(),
                byte_offset=reader.u8(),
                storage_type=reader.u8(),
                component_count=reader.u8(),
                unknown=reader.u8(),
            )
            for _ in range(declaration_count)
        )
        for declaration in declarations:
            element_size = declaration_size(declaration)
            if declaration.byte_offset + element_size > stride:
                reader.fail(
                    "vertex declaration exceeds its stream stride",
                    expected=f"at most {stride}",
                    observed=declaration.byte_offset + element_size,
                )
        headers.append((flags, stride, declarations))

    streams: list[VertexStream] = []
    for flags, stride, declarations in headers:
        reader.align(4)
        size = stride * vertex_count
        streams.append(
            VertexStream(
                flags=flags,
                stride=stride,
                declarations=declarations,
                data=reader.read(size),
            )
        )
    return VertexArray(prefix, vertex_count, unknown, tuple(streams))


def parse_index_array(
    decoder: CoreDecoder,
    obj: CoreObject,
    reader: BinaryReader,
) -> IndexArray:
    prefix = read_resource_prefix(decoder, reader)
    reader.align(4)
    index_count = _bounded_i32(reader, "index count", MAX_VERTICES * 6)
    unknown = reader.read(8)
    indexes = tuple(reader.u16() for _ in range(index_count))
    if index_count % 3:
        reader.fail(
            "triangle-list index count is not divisible by three",
            expected="a multiple of 3",
            observed=index_count,
        )
    return IndexArray(prefix, indexes, unknown)


def parse_primitive(
    decoder: CoreDecoder,
    obj: CoreObject,
    reader: BinaryReader,
) -> Primitive:
    prefix = read_resource_prefix(decoder, reader)
    flags_or_unknown = reader.read(4)
    vertex_array = decoder.core.read_reference(reader)
    index_array = decoder.core.read_reference(reader)
    base_vertex = reader.i32()
    spatial_tree = decoder.core.read_reference(reader)
    render_unknown = b""
    render_effect = decoder.null_reference
    if obj.type_name == "RenderingPrimitiveResource":
        render_unknown = reader.read(16)
        render_effect = decoder.core.read_reference(reader)
    return Primitive(
        prefix=prefix,
        flags_or_unknown=flags_or_unknown,
        vertex_array=vertex_array,
        index_array=index_array,
        base_vertex=base_vertex,
        spatial_tree=spatial_tree,
        render_unknown=render_unknown,
        render_effect=render_effect,
    )


def parse_static_mesh(
    decoder: CoreDecoder,
    obj: CoreObject,
    reader: BinaryReader,
) -> StaticMesh:
    prefix, unknown = read_mesh_prefix(decoder, reader)
    primitive_count = reader.count("primitive count", maximum=1_000_000)
    primitives = tuple(
        decoder.core.read_reference(reader) for _ in range(primitive_count)
    )
    if reader.remaining != STATIC_MESH_TAIL_SIZE:
        reader.fail(
            "unexpected static mesh tail size",
            expected=STATIC_MESH_TAIL_SIZE,
            observed=reader.remaining,
        )
    local_translate_scale = (
        reader.f32(),
        reader.f32(),
        reader.f32(),
        reader.f32(),
    )
    return StaticMesh(
        prefix=prefix,
        mesh_base_unknown=unknown,
        primitives=primitives,
        local_translate_scale=local_translate_scale,
        static_mesh_tail=reader.read(reader.remaining),
    )


def parse_lod_mesh(
    decoder: CoreDecoder,
    obj: CoreObject,
    reader: BinaryReader,
) -> LodMesh:
    prefix, unknown = read_mesh_prefix(decoder, reader)
    part_count = reader.count("LOD mesh part count", maximum=1_000_000)
    parts = tuple(
        LodMeshPart(
            mesh=decoder.core.read_reference(reader),
            distance=reader.f32(),
        )
        for _ in range(part_count)
    )
    return LodMesh(
        prefix=prefix,
        mesh_base_unknown=unknown,
        parts=parts,
        max_distance=reader.f32(),
    )


def parse_multi_mesh(
    decoder: CoreDecoder,
    obj: CoreObject,
    reader: BinaryReader,
) -> MultiMesh:
    prefix, unknown = read_mesh_prefix(decoder, reader)
    part_count = reader.count("multi-mesh part count", maximum=1_000_000)
    parts = tuple(
        MultiMeshPart(
            mesh=decoder.core.read_reference(reader),
            local_transform=_read_matrix4(reader),
        )
        for _ in range(part_count)
    )
    return MultiMesh(prefix, unknown, parts)


def parse_switch_mesh(
    decoder: CoreDecoder,
    obj: CoreObject,
    reader: BinaryReader,
) -> SwitchMesh:
    prefix, unknown = read_mesh_prefix(decoder, reader)
    name = decoder.core.read_string_index(reader, "String")
    default_key = decoder.core.read_string_index(reader, "String")
    parts_use_the_same_mesh = reader.u8()
    part_count = reader.count("switch-mesh part count", maximum=1_000_000)
    parts = tuple(
        SwitchMeshPart(
            mesh=decoder.core.read_reference(reader),
            key=decoder.core.read_string_index(reader, "String"),
        )
        for _ in range(part_count)
    )
    return SwitchMesh(
        prefix,
        unknown,
        name,
        default_key,
        parts_use_the_same_mesh,
        parts,
    )


def parse_skinned_mesh(
    decoder: CoreDecoder,
    obj: CoreObject,
    reader: BinaryReader,
) -> SkinnedMesh:
    prefix, mesh_unknown = read_mesh_prefix(decoder, reader)
    skeleton = decoder.core.read_reference(reader)
    unknown = reader.read(19)
    bindings = decoder.core.read_reference(reader)
    skin_info = decoder.core.read_reference(reader)
    primitive_count = reader.count("skinned primitive count", maximum=1_000_000)
    primitives = tuple(
        decoder.core.read_reference(reader) for _ in range(primitive_count)
    )
    effect_count = reader.count("render-effect count", maximum=1_000_000)
    effects = tuple(
        decoder.core.read_reference(reader) for _ in range(effect_count)
    )
    scale = (reader.f32(), reader.f32(), reader.f32())
    offset = (reader.f32(), reader.f32(), reader.f32())
    return SkinnedMesh(
        prefix=prefix,
        mesh_base_unknown=mesh_unknown,
        skeleton=skeleton,
        unknown=unknown,
        bone_bindings=bindings,
        skin_info=skin_info,
        primitives=primitives,
        render_effects=effects,
        position_bounds_scale=scale,
        position_bounds_offset=offset,
    )


def parse_skin_info(
    decoder: CoreDecoder,
    obj: CoreObject,
    reader: BinaryReader,
) -> SkinInfo:
    part_count = reader.count("skin-part count", maximum=1_000_000)
    parts: list[SkinPart] = []
    for _ in range(part_count):
        skin_info_type = reader.i32()
        if skin_info_type not in (0, 1):
            reader.fail(
                "unsupported skin-info type",
                expected="0 or 1",
                observed=skin_info_type,
            )
        unknown = reader.read(16)
        basic_count = reader.count("basic skin vertex count", maximum=MAX_VERTICES)
        basic = tuple(_read_skin_vertex(reader, nbt=False) for _ in range(basic_count))
        nbt_count = reader.count("NBT skin vertex count", maximum=MAX_VERTICES)
        nbt = tuple(_read_skin_vertex(reader, nbt=True) for _ in range(nbt_count))
        parts.append(SkinPart(skin_info_type, unknown, basic, nbt))
    return SkinInfo(None, tuple(parts))


def parse_bone_bindings(
    decoder: CoreDecoder,
    obj: CoreObject,
    reader: BinaryReader,
) -> BoneBindings:
    bone_count = reader.count("bone-binding count", maximum=65_535)
    names = tuple(
        decoder.core.read_string_index(reader, "String") for _ in range(bone_count)
    )
    matrices = tuple(_read_matrix4(reader) for _ in range(bone_count))
    return BoneBindings(None, names, matrices)


def declaration_size(declaration: VertexDeclaration) -> int:
    try:
        storage = VertexStorage(declaration.storage_type)
    except ValueError:
        raise ValueError(f"unknown vertex storage type {declaration.storage_type}")
    if declaration.component_count <= 0 or declaration.component_count > 4:
        raise ValueError(
            f"invalid vertex component count {declaration.component_count}"
        )
    if storage is VertexStorage.UNDEFINED:
        raise ValueError("undefined vertex storage type")
    if storage is VertexStorage.X11Y11Z10_NORMALIZED:
        return 4
    component_size = {
        VertexStorage.SIGNED_SHORT_NORMALIZED: 2,
        VertexStorage.FLOAT: 4,
        VertexStorage.HALF_FLOAT: 2,
        VertexStorage.UNSIGNED_BYTE_NORMALIZED: 1,
        VertexStorage.SIGNED_SHORT: 2,
        VertexStorage.UNSIGNED_BYTE: 1,
    }[storage]
    return component_size * declaration.component_count


def decode_semantic(
    vertex_array: VertexArray,
    semantic: int,
) -> tuple[tuple[float, ...], ...] | None:
    for stream in vertex_array.streams:
        for declaration in stream.declarations:
            if declaration.semantic != semantic:
                continue
            return tuple(
                decode_vertex_element(
                    stream.data,
                    vertex_index * stream.stride + declaration.byte_offset,
                    declaration,
                )
                for vertex_index in range(vertex_array.vertex_count)
            )
    return None


def decode_vertex_element(
    data: bytes,
    offset: int,
    declaration: VertexDeclaration,
) -> tuple[float, ...]:
    storage = VertexStorage(declaration.storage_type)
    count = declaration.component_count
    if storage is VertexStorage.SIGNED_SHORT_NORMALIZED:
        values = struct.unpack_from(f">{count}h", data, offset)
        return tuple(max(-1.0, value / 32767.0) for value in values)
    if storage is VertexStorage.FLOAT:
        return tuple(float(value) for value in struct.unpack_from(f">{count}f", data, offset))
    if storage is VertexStorage.HALF_FLOAT:
        return tuple(float(value) for value in struct.unpack_from(f">{count}e", data, offset))
    if storage is VertexStorage.UNSIGNED_BYTE_NORMALIZED:
        return tuple(value / 255.0 for value in data[offset : offset + count])
    if storage is VertexStorage.SIGNED_SHORT:
        return tuple(
            float(value) for value in struct.unpack_from(f">{count}h", data, offset)
        )
    if storage is VertexStorage.X11Y11Z10_NORMALIZED:
        packed = struct.unpack_from(">I", data, offset)[0]
        x = _signed_bits(packed & 0x7FF, 11)
        y = _signed_bits((packed >> 11) & 0x7FF, 11)
        z = _signed_bits((packed >> 22) & 0x3FF, 10)
        return (
            max(-1.0, x / 1023.0),
            max(-1.0, y / 1023.0),
            max(-1.0, z / 511.0),
        )
    if storage is VertexStorage.UNSIGNED_BYTE:
        return tuple(float(value) for value in data[offset : offset + count])
    raise ValueError("undefined vertex storage cannot be decoded")


def blender_uv_pairs(
    value: tuple[float, ...],
    *,
    flip_v: bool,
) -> tuple[tuple[float, float], tuple[float, float]]:
    if len(value) < 2:
        raise ValueError("UV vertex element must contain at least two components")
    source = (
        value[0],
        1.0 - value[1] if flip_v else value[1],
    )
    if len(value) < 4:
        return source, source

    # Packed level UV0 stores lightmap XY and a pre-flipped diffuse pair in ZW.
    diffuse = (
        value[2],
        value[3] if flip_v else 1.0 - value[3],
    )
    return source, diffuse


def reconstruct_skin_position(
    vertex: SkinVertex,
    scale: tuple[float, float, float],
    offset: tuple[float, float, float],
) -> tuple[float, float, float]:
    return (
        vertex.quantized_position[0] * scale[0] + offset[0],
        vertex.quantized_position[1] * scale[1] + offset[1],
        vertex.quantized_position[2] * scale[2] + offset[2],
    )


def reconstruct_static_position(
    position: tuple[float, ...],
    local_translate_scale: tuple[float, float, float, float],
) -> tuple[float, float, float]:
    scale = local_translate_scale[3]
    return (
        position[0] * scale + local_translate_scale[0],
        position[1] * scale + local_translate_scale[1],
        position[2] * scale + local_translate_scale[2],
    )


def _read_skin_vertex(reader: BinaryReader, *, nbt: bool) -> SkinVertex:
    position = (reader.i16(), reader.i16(), reader.i16())
    weight0 = reader.u8()
    weight1 = reader.u8()
    if weight0 + weight1 > 255:
        reader.fail(
            "skin weights exceed 255",
            expected="weight0 + weight1 <= 255",
            observed=weight0 + weight1,
        )
    normal = _read_snorm_i8_vector3(reader)
    bones = (reader.u8(), reader.u8(), reader.u8())
    binormal = None
    tangent = None
    if nbt:
        binormal = _read_snorm_i8_vector3(reader)
        tangent = _read_snorm_i8_vector3(reader)
    weights = (
        weight0 / 255.0,
        weight1 / 255.0,
        (255 - weight0 - weight1) / 255.0,
    )
    return SkinVertex(
        quantized_position=position,
        weights=weights,
        normal=normal,
        bone_indices=bones,
        binormal=binormal,
        tangent=tangent,
    )


def _read_snorm_i8_vector3(
    reader: BinaryReader,
) -> tuple[float, float, float]:
    return (
        max(-1.0, reader.i8() / 127.0),
        max(-1.0, reader.i8() / 127.0),
        max(-1.0, reader.i8() / 127.0),
    )


def _read_matrix4(reader: BinaryReader) -> Matrix4:
    rows = tuple(
        tuple(reader.f32() for _ in range(4))
        for _ in range(4)
    )
    return rows  # type: ignore[return-value]


def _bounded_i32(
    reader: BinaryReader,
    label: str,
    maximum: int,
    *,
    minimum: int = 0,
) -> int:
    offset = reader.position
    value = reader.i32()
    if value < minimum or value > maximum:
        reader.fail(
            f"invalid {label}",
            offset=offset,
            expected=f"{minimum}..{maximum}",
            observed=value,
        )
    return value


def _signed_bits(value: int, bits: int) -> int:
    sign = 1 << (bits - 1)
    return value - (1 << bits) if value & sign else value
