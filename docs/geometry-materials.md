# Geometry, textures, and materials

[Documentation index](./README.md) | [CORE format](./core.md) |
[Known unknowns](./known-unknowns.md)

This page describes registered CORE payloads used to construct visible
Blender assets. Unless noted otherwise, scalar fields and vertex components
are big-endian.

## Common resource and mesh prefixes

Most named resources begin with a `ResourcePrefix`:

1. adaptive index into the CORE `String` bank;
2. one unknown `u8`.

Mesh resources then carry another 29 bytes whose semantics are unknown. The
importer preserves those bytes as `mesh_base_unknown`.

## Vertex arrays

A `VertexArrayResource` is decoded as:

1. resource prefix;
2. padding to a four-byte boundary;
3. signed `i32` vertex count;
4. unknown `i32`;
5. signed `i32` stream count;
6. all stream headers and declarations;
7. for each stream, four-byte alignment followed by
   `vertex_count * stride` bytes.

The importer bounds the vertex count to 20,000,000, the stream count to 64,
the declarations per stream to 64, and a stream stride to 1..4096 bytes.

Each stream header contains:

| Field | Encoding | Status |
|---|---|---|
| Flags | `i32` | Unknown/raw |
| Stride | `i32` | Enforced positive byte stride |
| Declaration count | `i32` | Enforced bounded count |

Each declaration is eight bytes:

| Relative offset | Size | Field |
|---:|---:|---|
| `0x00` | 4 | Semantic code (`u32`) |
| `0x04` | 1 | Byte offset in the stream record |
| `0x05` | 1 | Storage code |
| `0x06` | 1 | Component count |
| `0x07` | 1 | Unknown |

The declaration's decoded byte span must fit inside the stream stride.
Component counts from one through four are accepted. The packed
`X11Y11Z10` storage occupies four bytes and always decodes three values.

### Semantic codes

These names are the importer's current **interpretation** of the numeric
codes:

| Code | Name | Code | Name |
|---:|---|---:|---|
| 0 | `POSITION` | 9 | `UV1` |
| 1 | `POSITION4` | 10 | `UV2` |
| 2 | `WEIGHTS` | 11 | `UV3` |
| 3 | `TANGENT_QUATERNION` | 12 | `UV4` |
| 4 | `TANGENT` | 13 | `UV5` |
| 5 | `BINORMAL` | 14 | `UV6` |
| 6 | `NORMAL` | 15 | `MOTION_VECTOR` |
| 7 | `COLOR` | 16 | `VECTOR4_BYTE0` |
| 8 | `UV0` |  |  |

The parser can retain declarations with other semantic values, but the
Blender importer only has behavior for semantics it requests.

### Storage codes

| Code | Storage | Decode |
|---:|---|---|
| 0 | Undefined | Rejected when decoded |
| 1 | Signed short normalized | `i16 / 32767`, clamped to at least `-1` |
| 2 | Float | IEEE-754 `f32` |
| 3 | Half float | IEEE-754 `f16` |
| 4 | Unsigned byte normalized | `u8 / 255` |
| 5 | Signed short | `i16` converted to float |
| 6 | `X11Y11Z10` normalized | Signed packed 11/11/10-bit XYZ |
| 7 | Unsigned byte | `u8` converted to float |

For code 6, bits 0..10 are X, 11..21 are Y, and 22..31 are Z. X/Y divide by
1023 and Z divides by 511 after sign extension, with the minimum clamped to
`-1`.

## Index arrays and primitives

An `IndexArrayResource` contains a resource prefix, four-byte alignment, a
signed `i32` index count, eight unknown bytes, and `index_count` big-endian
`u16` indices. The count must be divisible by three. The importer therefore
supports 16-bit triangle lists only; it does not infer strips, fans, or 32-bit
index buffers.

`PrimitiveResource` contains:

1. resource prefix;
2. four raw flag/unknown bytes;
3. vertex-array reference;
4. index-array reference;
5. signed `i32` base vertex;
6. spatial-tree reference.

`RenderingPrimitiveResource` appends 16 raw bytes and a render-effect
reference.

## Static meshes and wrappers

### `StaticMeshResource`

After the common mesh prefix:

1. compact primitive-reference count and that many references;
2. exactly 35 tail bytes.

The first 16 bytes of that tail are interpreted as four `f32` values:
`translate_x`, `translate_y`, `translate_z`, and uniform `scale`. A source
position is reconstructed as:

```text
world_xyz = source_xyz * scale + translate_xyz
```

This interpretation is **retail-validated** for normalized positions in
optimized level chunks. The remaining 19 tail bytes are unknown/raw.

### Wrapper resources

- `LodMeshResource`: mesh/distance pairs plus one maximum distance.
- `MultiMeshResource`: mesh references paired with row-major 4x4 local
  transforms.
- `SwitchMeshResource`: a name, default key, one unknown/boolean-like byte,
  and mesh/key pairs.

Source order, keys, transforms, and distances are preserved. The exact LOD
comparison rule, equality behavior, origin, and distance unit are not proven,
so Blender initially hides non-primary variants.

## UV interpretation

With the default V flip, an ordinary two-component UV is converted from
`(u, v)` to `(u, 1-v)` and copied to both `UV0` and `DiffuseUV`.

For an optimized four-component `UV0` declaration, the importer interprets:

- `.xy` as lightmap UVs, exposed as `UV0`;
- `.zw` as a pre-flipped diffuse pair, exposed as `DiffuseUV`.

With default settings the `.zw` V component is used as stored. Disabling the
diagnostic V flip instead converts it to `1-w`. This asymmetric behavior is
**retail-validated** against traced level shader variants, but it should not
be generalized to every engine or shader revision without evidence.

## Skinned geometry

`RegularSkinnedMeshResource` stores:

1. common mesh prefix;
2. skeleton reference;
3. 19 unknown bytes;
4. bone-bindings reference;
5. skin-info reference;
6. primitive references;
7. render-effect references;
8. position reconstruction scale (`f32[3]`);
9. position reconstruction offset (`f32[3]`).

Quantized skin positions are reconstructed component-wise:

```text
position = quantized_i16 * scale + offset
```

`SkinnedMeshBoneBindings` stores a compact count, that many `String` bank
indices, and one 4x4 matrix per name. The importer treats the matrices as
inverse bind matrices.

`RegularSkinnedMeshResourceSkinInfo` contains parts of type 0 or 1. Each part
has 16 unknown bytes, a basic-vertex array, and an NBT-vertex array. Other part
types are rejected.

### Compact skin vertex

| Field | Basic size | Decode |
|---|---:|---|
| Quantized position | 6 | Three `i16` |
| First two weights | 2 | Two `u8` |
| Normal | 3 | Three signed normalized `i8` |
| Bone indices | 3 | Three `u8` |
| Binormal | 3, NBT only | Three signed normalized `i8` |
| Tangent | 3, NBT only | Three signed normalized `i8` |

A basic record is 14 bytes and an NBT record is 20 bytes. The third weight is
implicit:

```text
w2 = 255 - w0 - w1
```

The sum of the first two stored weights may not exceed 255. All three weights
are divided by 255. Type 1 prefers NBT vertices when present; otherwise the
nonempty available array is used. The original semantic distinction between
skin-info types 0 and 1 is still unknown.

## Texture resource layout

A `Texture` payload begins with a resource prefix. Offsets in this table are
relative to the byte immediately after that prefix:

| Offset | Size | Field |
|---:|---:|---|
| `0x00` | 4 | Texture type (`i32`) |
| `0x04` | 4 | Width (`i32`) |
| `0x08` | 4 | Height (`i32`) |
| `0x0C` | 4 | Pixel-format code (`i32`) |
| `0x10` | 4 | Memory type (`i32`, must be 0) |
| `0x14` | 4 | Mip count (`i32`) |
| `0x18` | 4 | Color space (`i32`: 0 linear, 1 sRGB) |
| `0x1C` | 4 | Texture-coordinate type (`i32`, must be 0) |
| `0x20` | 4 | Creation flags (`u32`: 1 static, 3 dynamic) |
| `0x24` | 4 | Allocation size |
| `0x28` | 4 | Allocation constant, must be `0x01050000` |
| `0x2C` | 4 | Payload size |
| `0x30` | 16 | Four state words |
| `0x40` | 24 | RSX texture descriptor |
| `0x58` | 80 | Fill bytes, all `0x50` |
| `0xA8` | variable | Image payload |

Texture type 0 is 2D and type 2 is a cubemap. Dimensions are limited to
1..16384. The mip count must be at least one and no greater than
`bit_length(max(width, height))`.

The allocation size must be `payload_size + 128`. The first three state words
must be `(1, 0, 128)`. The fourth is the logical storage end described below.
No bytes may follow the declared payload.

### RSX descriptor

| Relative offset | Size | Field |
|---:|---:|---|
| `0x00` | 1 | RSX format |
| `0x01` | 1 | Mip count |
| `0x02` | 1 | Dimension, must be 2 |
| `0x03` | 1 | Cubemap flag |
| `0x04` | 4 | ARGB remap |
| `0x08` | 2 | Width |
| `0x0A` | 2 | Height |
| `0x0C` | 2 | Depth, must be 1 |
| `0x0E` | 1 | Location, must be 0 |
| `0x0F` | 1 | Padding, must be 0 |
| `0x10` | 4 | Pitch |
| `0x14` | 4 | Offset, must be 0 |

The descriptor dimensions, mip count, and cubemap flag must repeat the
serialized texture header.

### Decoded pixel formats

| CORE code | Name | RSX format | Remap | Bytes/layout |
|---:|---|---:|---:|---|
| 5 | `RGBA_8888`, static | `0x85` | `0x0000AA6C` | 4 bytes/texel, rectangular Morton order |
| 5 | `RGBA_8888`, dynamic | `0xA5` | `0x0000AA93` | 4 bytes/texel, linear, pitch = width * 4, one mip |
| 6 | `RGBA_8888_REV` | `0x85` | `0x0000AAE4` | 4 bytes/texel, rectangular Morton order |
| 19 | `S3TC1` / DXT1 | `0x86` | `0x0000AAE4` | 8 bytes per 4x4 block |
| 20 | `S3TC3` / DXT3 | `0x87` | `0x0000AAE4` | 16 bytes per 4x4 block |
| 21 | `S3TC5` / DXT5 | `0x88` | `0x0000AAE4` | 16 bytes per 4x4 block |
| 31 | `RGBA_FLOAT_16` | `0x9A` | `0x0000AAE4` | Four big-endian half floats per texel |

All profiles except dynamic RGBA8 require a zero pitch. Unknown pixel-format
codes can be structurally retained, but image decoding raises an unsupported
feature error.

S3TC color and alpha endpoint/index words use the standard little-endian DXT
block encoding even though their surrounding CORE fields are big-endian.

### Mips and cubemap faces

Mips are stored largest to smallest. A face's logical byte count is the sum of
its mip sizes. Each face starts on a 128-byte boundary:

```text
face_stride = align_up(sum(mip_sizes), 128)
payload_size = face_stride * face_count
```

A 2D texture has one face and a cubemap has six. The fourth state word equals
the end of the last face's logical mip data, excluding only the final
alignment padding. Face and mip order are preserved by the pure-Python API.
The cubemap face names and conversion from the game's basis to Blender's
environment convention are unknown.

Static uncompressed images use a rectangular Morton interleave based on the
number of bits needed by each dimension. Dynamic RGBA8 is linear. The RSX
ARGB remap selects or forces channels before the importer returns RGBA.

## Render effects and shader resources

### `RenderEffectResource`

After its resource prefix, a render effect contains an animator reference, a
compact technique count, and the techniques. Unparsed trailing bytes are
preserved.

Each technique contains:

1. 27-byte render state;
2. shader reference;
3. compact texture-binding count and bindings;
4. compact shader-variable count and variables;
5. one `f32` reflection sampler bias.

The 27-byte state is framed as:

```text
u8 unknown_leading_flag       # enforced zero
i32 cull_mode
i32 blend_mode
u8 write_depth
i32 depth_test_func
i32 alpha_test_func
u8 alpha_test_value
u8 enable_depth_bias
u8 enable_depth_clamp
u16 color_mask
i32 technique_type
```

A texture binding is a sampler-name index, texture reference, raw/decoded
`f16` mip bias, and packed `u16` sampler state. The importer exposes the
sampler-state nibbles as filter mode, address U, address V, and anisotropy,
from least to most significant nibble. Numeric enum meanings are only
partially interpreted for Blender.

A shader variable contains its name, `u32` type, 16 raw value bytes, animator
reference, and shader-input name. Types 1, 2, 3, 4, and 9 are exposed as the
first 1, 2, 3, 4, or 1 big-endian floats respectively. Other types remain
raw.

### `ShaderResource`

After the prefix:

1. `u32` IBL shading mode;
2. `u32` shader-object size;
3. compiled shader-object bytes.

Bytes 8..11 of the compiled object must equal `SHO\x08`. The complete object
is SHA-256 hashed for exact preview-profile matching. The importer does not
generally translate the compiled RSX program.

## Blender preview interpretation

The render graph is preserved more completely than it is reproduced.
Technique zero supplies Blender preview nodes. Exact profiles are selected by
combinations of shader-object ID, full shader hash, ordered sampler signature,
variable signature, and technique index.

Profiles marked `proven` in the implementation cover representative M82, Sev,
hair, Reflection Room, HGH dropship, and ISA cruiser variants. "Proven" means
the sampler role was established for that exact identity, not that the entire
PS3 deferred shader has an equivalent Blender graph.

If no exact profile matches, an sRGB `inSampler0` texture is connected as an
explicit `srgb_heuristic` base color. That fallback is a **guess**, not a
format rule. Other numbered samplers remain available as named nodes rather
than being assigned speculative roles.

### Packed lightmaps

For confirmed `inSampler_LM` bindings, the importer interprets one packed
sample `(u, v, sun, luminance)` as:

```text
r = saturate(luminance + 0.375*u - 0.125*v - 0.125)
g = saturate(luminance - 0.125*u - 0.125*v + 0.125)
b = saturate(luminance - 0.125*u + 0.375*v - 0.125)

indirect_rgb = 4 * (r*r, g*g, b*b)
sun_occlusion = saturate(sun)
```

This decode and its Blender node-group output are **retail-validated**. Adding
the indirect term through Blender emission is a preview approximation; it is
not a claim of complete equivalence with the original deferred renderer.

## Known gaps

- Stream flags, the declaration tail byte, index-array header bytes, primitive
  flags, mesh prefix bytes, and many resource tails remain unknown/raw.
- Only 16-bit triangle lists are supported.
- The original runtime meanings of skin-info type 0/1 and the 19-byte skinned
  mesh field are unknown.
- LOD distance semantics and switch-selection behavior are unproven.
- Cubemap face semantics and basis conversion are not mapped.
- Unknown texture formats are retained but not decoded.
- Compiled `SHO` programs are not generally decompiled into Blender graphs.
  Exact profiles cover only identified shaders; the generic base-color path
  is deliberately labeled as a heuristic.
- Normal-map signs, packed material channels, ramps, emissive behavior, IBL,
  secondary detail layers, and blend arithmetic remain profile-specific
  research topics unless an exact profile says otherwise.
