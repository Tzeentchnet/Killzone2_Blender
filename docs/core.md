# RTTIBin CORE object graphs

[Documentation index](./README.md) | [Known unknowns](./known-unknowns.md)

## Supported profile

The importer accepts only the big-endian Killzone 2 retail profile whose
15-byte signature is:

```text
RTTIBin<1.58>··
```

The two trailing characters are ASCII spaces. Byte `0x0F` is an endian marker
and must be `1`. The filename extension is conventionally `.core`, but the
signature and marker are the actual parser contract.

All numeric fields below are big-endian. Sizes and offsets are relative to the
start of the decoded CORE entry unless stated otherwise.

## Variable-width primitives

### Compact signed scalar

The first byte selects the representation:

| First byte | Following bytes | Value |
|---:|---:|---|
| `0x80` | 4 | Signed `i32` |
| `0x81` | 2 | Signed `i16` |
| Any other value | 0 | The first byte interpreted as signed `i8` |

As a consequence, single-byte encodings for `-128` and `-127` are reserved by
the two extended forms. Counts use this scalar but reject negative values.

### Cardinality-adaptive index

The width is selected from the number of possible values, not from an inline
tag:

| Cardinality | Width |
|---:|---:|
| `0..0x100` | `u8` |
| `0x101..0x10000` | `u16` |
| Greater | `u32` |

An ordinary adaptive index must be less than the cardinality. A local object
reference uses a cardinality of `object_count + 1` and interprets zero as
null.

### Typed string length

| First byte | Length |
|---:|---|
| `0x00..0xFD` | First byte |
| `0xFE` | Following `u16` |
| `0xFF` | Following `u32` |

Type names and external IDs use a different form: a nonnegative compact-scalar
byte count followed by UTF-8 bytes.

## Top-level serialization order

The parser consumes a CORE in this order:

1. 15-byte signature and one-byte endian marker.
2. Three `u32` and two `u16` header fields.
3. Type-name table.
4. Object count and one adaptive type index per object.
5. One fixed 32-byte metadata record per object.
6. First global allocation list.
7. Typed string banks.
8. One compact scalar of unknown purpose.
9. Second global allocation list.
10. For every object in metadata order:
    - two object-local allocation lists;
    - either its declared buffered payload, or a registered direct serializer
      when the declared payload size is zero.

The five post-marker header fields, both global allocation-list roles, and the
unnamed scalar are **unknown/raw**. Their values are retained by the
intermediate representation.

### Type-name table

The table begins with a compact nonnegative count. Each type name is a compact
byte length followed by UTF-8. Object metadata stores an adaptive index into
this table.

### Object metadata record

| Relative offset | Size | Field | Confidence |
|---:|---:|---|---|
| `0x00` | 16 | Opaque object ID | Structural use is enforced; original semantics unknown |
| `0x10` | 4 | Buffered payload size | Enforced, maximum 512 MiB |
| `0x14` | 12 | Metadata tail | Unknown/raw |

For diagnostics, the importer displays an object ID by reversing each
four-byte group independently and then formatting the 16-byte result as
lowercase hexadecimal. This display transform should not be mistaken for a
standard UUID byte order.

### Allocation list

An allocation list is a compact count followed by five compact signed scalars
per record. The parser retains all five values but does not assign general
semantics. One specialized destructibility heuristic uses the first value;
see [Levels and entities](./levels-entities.md).

## String banks

After the first global allocation list:

1. Read the compact bank count.
2. Read an adaptive type-table index naming each bank.
3. Read a compact string count.
4. For each value, read one allocation scalar, a typed string length, and the
   encoded data.

`String` values are UTF-8. `WString` values use UTF-16BE and multiply the
stored character count by two to obtain the byte count. The allocation scalar
must be `-1` or greater, but its meaning is unknown. Duplicate bank names are
rejected.

Resource names, sampler names, joint names, and many level identifiers are
stored as adaptive indices into the `String` bank.

## Object references

A reference begins with a one-byte discriminator:

| Discriminator | Payload | Meaning |
|---:|---|---|
| `0` | Adaptive reference index | `0` is null; `n > 0` refers to local object `n - 1` |
| `2` | Compact UTF-8 string | External opaque ID |

Other discriminators are rejected. Local references are resolved lazily.
External references require a source PSARC and are looked up by exact opaque
ID; the importer does not silently select a near match.

## Buffered and direct objects

Most reflected objects have a nonzero metadata payload size. The parser can
skip an unknown buffered type safely and represents it as `UnknownObject`.
This permits structural analysis without pretending the payload was decoded.

Retail level COREs also contain objects whose metadata payload size is zero
even though bytes immediately follow their allocation lists. Their length is
defined only by the reflected type's serializer. The importer calls these
**direct objects** and supports exactly the registered retail set listed in
[Levels and entities](./levels-entities.md). An unregistered zero-sized direct
serializer fails closed when direct parsing is enabled because guessing its
length would misalign every following object.

Direct parsing is automatically enabled when all zero-sized types in a CORE
belong to the registered level set. It can also be requested explicitly by
the pure-Python caller.

Any bytes remaining after all framed objects are recorded as top-level
trailing bytes rather than silently attributed to the final object.

## Registered buffered object types

`CoreDecoder` has typed decoders for the following exact RTTI names:

### Geometry and mesh graphs

- `VertexArrayResource`
- `IndexArrayResource`
- `PrimitiveResource`
- `RenderingPrimitiveResource`
- `StaticMeshResource`
- `LodMeshResource`
- `MultiMeshResource`
- `SwitchMeshResource`
- `RegularSkinnedMeshResource`
- `RegularSkinnedMeshResourceSkinInfo`
- `SkinnedMeshBoneBindings`

### Skeleton and animation

- `Skeleton`
- `SkeletonAnimationBoneMap`
- `SkeletonAnimationResource`
- `AnimationState`
- `TimeMappedSkeletonAnimationResource`
- `DirectionalAnimationResource`

### Textures and materials

- `Texture`
- `RenderEffectResource`
- `ShaderResource`

### Entities and destructibility

- `EntityResource`
- `StaticModelResource`
- `SkinnedModelResource`
- `ModelPartResource`
- `DestructibilityPartStateResource`
- `StateBasedDestructibilityResource`

An exact type name not in this list remains an `UnknownObject` unless it is a
registered direct type. This distinction matters: "the CORE parsed" does not
mean every reflected object payload was semantically decoded.

## Resource prefix

Most registered resource payloads begin with:

1. one adaptive index into the `String` bank, treated as the resource name;
2. one `u8` of unknown purpose.

This is called `ResourcePrefix` in the implementation. Some helper objects,
such as skin-info and bone-binding payloads, do not use it.

## Unknowns and compatibility boundaries

- The format is treated as a serialized RTTI object graph, but the repository
  does not contain a general RTTI schema, class inheritance table, or field
  metadata.
- The five fixed header values, 12-byte object metadata tail, allocation
  records, and resource-prefix byte are not semantically decoded.
- External ID strings are treated as opaque. Hexadecimal IDs are common, but
  the core reader does not require that syntax.
- The meaning of top-level trailing bytes is unknown.
- Other `RTTIBin` revisions, endian markers, reference discriminators, and
  direct serializers are unsupported even if they are valid for another game
  build.
- Several registered decoders intentionally retain trailing or raw fields.
  See the asset-specific pages before assigning engine semantics to those
  bytes.
