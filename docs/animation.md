# Skeletons and animation

[Documentation index](./README.md) | [CORE format](./core.md) |
[Known unknowns](./known-unknowns.md)

Killzone 2 skeleton and animation data is layered:

1. reflected CORE resources provide names and references;
2. a `Skeleton` contains both an expanded joint block and an embedded `ES00`
   runtime skeleton;
3. a `SkeletonAnimationResource` wraps an embedded `EA02` clip;
4. animation-state, directional, and time-mapped resources name or group
   clips.

`ES00` and `EA02` are not standalone inputs in the Blender UI. The
pure-Python parser can decode an `EA02` byte blob directly.

## `Skeleton` CORE resource

After the common resource prefix, the parser reads:

1. compact bone-set count and `String` indices;
2. compact skeleton-reference count and CORE references;
3. compact count of unknown 24-byte records;
4. compact IK-control count and records;
5. compact animation-channel count and `String` indices;
6. compact `ES00` byte count and blob;
7. four-byte alignment;
8. expanded skeleton block.

Counts are bounded to 65,535. The embedded `ES00` blob is limited to 64 MiB.

### IK control

Each IK record contains:

- name and a list of channel names;
- `u8` start, middle, and end joint indices;
- three `f32` hinge-axis components;
- `f32` maximum and minimum hinge limits.

The importer retains IK metadata but does not create Blender IK constraints.
Units, axis space, and limit conventions are not proven.

## Embedded `ES00`

All fields are big-endian.

| Offset | Size | Field |
|---:|---:|---|
| `0x00` | 4 | Magic, must be `ES00` |
| `0x04` | 4 | Unknown |
| `0x08` | 4 | Total blob size |
| `0x0C` | 2 | Joint lookup count |
| `0x0E` | 2 | User-channel lookup count |
| `0x10` | 2 | Unknown count |
| `0x12` | 2 | Reserved/unknown |
| `0x14` | 24 | Six self-relative `u32` offsets |

Each nonzero offset is relative to the address of its own offset field. The
parser validates all six targets but currently assigns meaning only to:

1. offset at `0x14`: fallback-pose array;
2. offset at `0x18`: parent-index array.

The remaining four targets are **unknown/raw**.

### Fallback pose

Each joint pose is 48 bytes:

```text
f32 rotation_xyzw[4]
f32 translation_xyz[3]
f32 translation_padding
f32 scale_xyz[3]
f32 scale_padding
```

All values must be finite. The quaternion must already have unit length within
the parser tolerance; it is not silently repaired here.

The parent array contains one `u16` per joint. `0xFFFF` means no parent.
Indices must be in range, cannot point to the same joint, and the hierarchy
must be acyclic.

These fallback transforms are important for animation. When an `EA02` clip
omits a rotation, translation, or scale channel, the importer uses the
corresponding `ES00` transform rather than the expanded skinning rest pose.

## Expanded skeleton block

The expanded block begins after the `ES00` blob and four-byte alignment. Its
first 64 bytes are only partially understood:

| Relative offset | Size | Interpreted field |
|---:|---:|---|
| `0x0C` | 1 | Auxiliary record count |
| `0x0D` | 1 | Joint count |
| `0x0E` | 1 | Secondary count |
| `0x12` | 2 | String-chunk byte length |

From the block origin, the parser:

1. aligns to 64 bytes and skips 24 unknown bytes per joint;
2. if present, aligns to 16 bytes and skips 80 bytes per auxiliary record;
3. aligns to 16 bytes and reads expanded joints;
4. aligns to four bytes and reads one unknown `u32` per joint;
5. aligns again and reads `2 * joint_count + secondary_count` unknown `u32`
   values;
6. reads the UTF-8 NUL-terminated name chunk.

Each expanded joint contains:

```text
f32 rotation_xyzw[4]
f32 translation_xyz[3]
u32 name_offset
u32 parent_name_offset
12 raw bytes
```

Names must be unique, and every nonempty parent name must resolve. When
present, `ES00` and expanded joint counts must agree. The expanded block
provides named rest transforms for armature construction; the original roles
of the skipped tables and joint tail remain unknown.

## Animation graph resources

### `SkeletonAnimationBoneMap`

A compact count is followed by that many joint-name indices and raw trailing
bytes. The importer requires the names/order used by a clip to match the
target armature metadata before binding an Action.

The complete semantics of this object are only partially understood. Current
binding uses the validated direct Edge joint-index order.

### `AnimationState`

Contains a resource prefix, one animation reference, and raw trailing bytes.
The resource name is used as a human-readable Action name when its graph
ultimately reaches an `EA02` clip.

### `TimeMappedSkeletonAnimationResource`

Contains:

1. resource prefix;
2. `u32` wrapper version;
3. wrapper-flags `u8`;
4. cardinality-adaptive wrapper index;
5. animation reference;
6. raw trailing bytes.

### `DirectionalAnimationResource`

Contains a prefix, `u32` version, `u32` entry count, and entries made of an
animation reference, `f32` weight, `f32` heading in degrees, and one unknown
`u8`. Entry counts are limited to 65,535.

The importer walks time-mapped and directional wrappers to find leaf skeleton
clips. It does not reproduce the runtime directional blend or time mapping in
Blender.

## `SkeletonAnimationResource` wrapper

After its resource prefix:

| Field | Encoding |
|---|---|
| Wrapper version | `u32` |
| Wrapper flags | `u8` |
| Wrapper index | Adaptive reference-width integer |
| Duration hint | `f32` |
| Skeleton/bone-map reference | `CoreReference` |
| `EA02` byte count | Compact nonnegative scalar |
| Clip | Declared number of bytes |
| Trailing data | Preserved raw |

The clip must begin with `EA02`, and the wrapper duration must match the
embedded duration within a tight floating-point tolerance. The reference is
named `skeleton_reference` in the intermediate representation, while the
Blender binding path treats it as the animation bone-map reference. That
terminology mismatch reflects incomplete knowledge of the original field.

## `EA02` header

All header scalars are big-endian.

| Offset | Size | Field |
|---:|---:|---|
| `0x00` | 4 | Magic, `EA02` |
| `0x04` | 4 | Duration (`f32`) |
| `0x08` | 4 | Sample frequency (`f32`) |
| `0x0C` | 2 | Header size |
| `0x0E` | 2 | Joint count |
| `0x10` | 2 | Frame count |
| `0x12` | 2 | Frame-set count |
| `0x14` | 2 | Evaluation-buffer size, retained but not interpreted |
| `0x16` | 16 | Eight `u16` channel counts |
| `0x26` | 2 | Flags |
| `0x28` | 12 | Unknown/reserved |
| `0x34` | 4 | Self-relative frame-set DMA-table offset |
| `0x38` | 4 | Self-relative frame-set info-table offset |
| `0x3C` | 4 | Self-relative constant-rotation data offset |
| `0x40` | 4 | Self-relative constant-translation data offset |
| `0x44` | 4 | Self-relative constant-scale data offset |
| `0x48` | 4 | Self-relative constant-user data offset |
| `0x4C` | 4 | Optional self-relative locomotion-delta offset |
| `0x50` | 4 | Self-relative packing-specification offset |
| `0x54` | 12 | Unknown/reserved |
| `0x60` | variable | Channel-index tables |

The eight counts are, in order:

```text
constant rotation, translation, scale, user,
animated rotation, translation, scale, user
```

Duration must be finite and nonnegative; frequency must be finite and
positive. At least one frame and frame set is required. For a nonzero
duration, the parser enforces:

```text
sample_frequency ~= (frame_count - 1) / duration
```

Only `flags == 0` is currently sampleable. Other flag values are structurally
reported but transform decoding fails explicitly.

## Channel-index tables

Each table contains big-endian `u16` channel indices followed by padding:

- constant rotation pads to eight lanes;
- all other constant tables pad to four lanes;
- animated tables pad to four lanes.

Transform indices must be less than the joint count and cannot be duplicated
within a table. A transform index also cannot occur in both the constant and
animated table for the same channel kind.

Transform-table padding must equal the joint count. User-channel padding uses
a repeated sentinel greater than every actual user-channel index. User/scalar
tables and payload sizes are framed, but their values are not converted to
Blender properties, materials, or shape keys.

## Packing specifications

The packing block starts with one `u32` descriptor for each constant channel
kind, then one descriptor per animated channel in table order.

A descriptor packs three component definitions and a two-bit selector:

| Bits | Meaning |
|---|---|
| 31 | Component 0 sign-bit count |
| 27..30 | Component 0 exponent-bit count |
| 22..26 | Component 0 mantissa-bit count |
| 21 | Component 1 sign-bit count |
| 17..20 | Component 1 exponent-bit count |
| 12..16 | Component 1 mantissa-bit count |
| 11 | Component 2 sign-bit count |
| 7..10 | Component 2 exponent-bit count |
| 2..6 | Component 2 mantissa-bit count |
| 0..1 | Omitted quaternion component |

Component data is read MSB-first.

When exponent bits are zero, a component is reconstructed as a signed fixed
fraction over `(2^mantissa_bits - 1)`. A zero-width component becomes zero.
When exponent bits are present, the parser expands the stored sign, biased
exponent, and mantissa into an IEEE-754 `f32`.

A vector uses the three decoded components. A quaternion inserts the three
values around the selected omitted component and reconstructs that component
as the positive square root of `1 - dot(v, v)`, then normalizes the result.
This behavior is **retail-validated for supported flag-zero clips**, but the
meaning of other flag profiles is unknown.

## Frame sets and sparse keys

The info table stores `(base_frame, intra_frame_count)` as two `u16` values
per frame set. The DMA table stores an opaque `u32` word and an absolute
`u32` data offset per frame set. DMA words are preserved but not interpreted.

Base frames and data offsets must be strictly increasing. The final frame set
is a sentinel whose base frame equals `frame_count - 1`. The first data offset
must equal the header-size field.

At each non-sentinel data offset:

1. eight `u16` section sizes;
2. initial rotation, translation, scale, and user key groups;
3. one sparse-presence bitmap covering every animated channel and intra frame;
4. packed intra rotation, translation, and scale groups;
5. four-byte alignment;
6. packed intra user group;
7. 16-byte alignment to the next frame-set offset;
8. another eight-size table and final key groups.

The parser checks section sizes against every packing descriptor and against
the number of set bits in the sparse bitmap. Scale sections absorb required
four-byte padding, and the user section absorbs final 16-byte padding.

At a requested integer frame, missing sparse keys are interpolated between the
nearest stored values: linear interpolation for translation/scale and
spherical interpolation for rotations.

## Constant values and locomotion

Constant rotation, translation, and scale groups use the corresponding
self-relative offsets and one shared packing descriptor per kind. Constant
user data is not semantically decoded.

When present, the locomotion record is 32 bytes interpreted as eight `f32`
values. The first four form a normalized quaternion and the next three form a
translation. The eighth value is currently ignored. Locomotion is reported by
the audit tool but is not applied as Blender root motion.

## Blender Action mapping

- Rotation, translation, and scale are sampled at every native clip frame.
- Key timing is converted relative to the Blender scene FPS without rounding
  native sample times to integer Blender frames.
- Missing transform channels use `ES00` fallback transforms.
- Bone-map metadata must match the target armature before a clip is bound.
- Decoded transforms are currently treated as absolute local transforms.
- User/scalar channels and locomotion deltas are not mapped.
- Directional/time-mapped wrappers provide discovery and names, not runtime
  blend behavior.

## Known gaps

- Four `ES00` offset targets, expanded skeleton tables, joint-tail bytes, and
  several skeleton resource arrays are unknown.
- `SkeletonAnimationBoneMap` semantics are only partially established.
- Wrapper versions, flags, and index meanings are retained but not generally
  interpreted.
- `EA02` flags other than zero are unsupported.
- User/scalar channel values are framed but not decoded into scene data.
- The DMA word, evaluation-buffer size, reserved header ranges, and the last
  four values of the final frame-set size table are not assigned semantics.
- Locomotion deltas are parsed but not applied.
- Additive, composite, directional-blend, and time-map runtime semantics have
  not been proven. Transform clips are imported as absolute local motion.
