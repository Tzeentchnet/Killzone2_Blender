# Levels and entities

[Documentation index](./README.md) | [CORE format](./core.md) |
[Geometry and materials](./geometry-materials.md) |
[Known unknowns](./known-unknowns.md)

Retail levels combine ordinary buffered CORE resources with **direct objects**:
reflected objects whose metadata payload size is zero even though their
type-specific serializer emits bytes inline. Direct objects cannot be skipped
without knowing their exact serializer because there is no generic byte
length.

The importer frames all 44 direct types observed in the supported retail
profile. Only static render placements and mesh-backed entity placeholders
currently become specialized Blender scene objects. The other types remain
typed metadata.

## Level catalog discovery

The level browser is built from logical paths in the PSARC manifest. This is
catalog policy rather than a general PSARC rule.

Campaign roots must match, case-insensitively:

```text
LocalCachePS3/lumps/levels.single_player.optimized.
    <level>_optimized.core
```

The actual path is one line; it is wrapped above for readability.
Multiplayer roots must match:

```text
LocalCachePS3/lumps/levels.multi_player.mp_level_<number>.core
```

The supported archive contains 10 ordered campaign levels and 8 multiplayer
maps. Those counts and display names are profile data in the importer, not
fields in the PSARC header.

### Asset descriptors

`BaseConcreteAsset`, `ConcreteAsset`, and `DependentConcreteAsset` are
buffered objects used as level asset descriptors:

1. name (`String` bank index);
2. active `u8`;
3. unknown `u8`;
4. location (`String` index);
5. optimization-settings reference;
6. cache-persistent `u8`;
7. lump location (`String` index);
8. for `DependentConcreteAsset` only, a compact base-asset count and
   references.

No trailing payload bytes are accepted. A lump location is converted to:

```text
LocalCachePS3/lumps/<lump_location>.core
```

### Campaign sections

A `LevelSection` buffered object contains:

1. section name;
2. active `u8`;
3. two unknown `u8` values;
4. an asset count whose integer width is selected from the number of strings
   in the CORE;
5. descriptor reference and enabled `u8` for every asset.

Only active sections, enabled descriptors, and active assets are included.
Campaign entries recognized as shared base lumps are excluded from the
section-specific list and handled as shared dependencies.

### Multiplayer sections

The multiplayer catalog collects active concrete asset descriptors whose lump
location begins with `levels.multi_player.<map>.`. Locations containing
`ambient` are skipped by catalog policy. The resulting CORE paths are
presented as one `Map` section.

These path filters are **retail-validated**, but they are not a complete
description of every possible level-package arrangement.

## Common direct-object structures

### World node

A world node is:

1. parent object reference;
2. 16 finite `f32` values interpreted as a row-major 4x4 orientation matrix.

A geometry object extends it with 12 raw drawable-state bytes.

Retail map transforms are treated as Z-up XYZ and passed to Blender without an
axis rotation. The optional legacy `(x, y, z) -> (x, -z, y)` conversion is a
diagnostic compatibility mode, not an asserted property of the file format.

### Shader overrides

`MeshHierachyShaderOverrides` is the exact misspelled reflected type name in
the supported profile. It contains:

1. compact mesh-override count;
2. for each override:
   - mesh reference;
   - compact hierarchy-path length and `u32` path elements;
   - compact render-effect override count;
3. for each effect override:
   - render-effect reference;
   - texture overrides;
   - variable overrides.

A texture override carries a sampler-name index, texture reference, raw and
decoded half-float mip bias, and packed `u16` sampler state. A variable
override carries a variable name, `u32` type, 16 raw bytes, and animator
reference.

The hierarchy path is used to target a mesh inside LOD/multi/switch wrapper
graphs. Original engine terminology and path-selection edge cases are not
fully known.

## Visual placement records

### `StaticMeshInstance`

The serializer contains:

1. geometry object;
2. instance name;
3. flags `u8`;
4. static-model reference;
5. mesh hierarchy shader overrides;
6. eight raw tail bytes.

The model reference can resolve locally or through the source PSARC. Repeated
placements may share Blender mesh datablocks while keeping object-linked
material overrides.

### `EntityPlaceHolder`

The serializer contains:

1. instance name;
2. name-is-identifier `u8`;
3. 4x4 orientation matrix;
4. resource class name;
5. resource name;
6. compact attribute count and string indices;
7. compact linked-object count and references;
8. `u32` destructibility state.

The class/name pair identifies a buffered `EntityResource`. Resolution first
uses exact definitions in the current CORE, then same-level definitions, then
a unique shared base-lump definition, then a globally unique definition.
Ambiguity remains an explicit warning; the importer does not choose an
arbitrary same-name object.

The scope and shared-lump preference are **resolution policy**, not serialized
priority metadata.

## Registered direct serializers

Every name below has exact framing for the supported profile. "Metadata only"
means the decoded structure is retained but has no dedicated Blender adapter.

### Scene and render records

- `StaticMeshInstance` - Blender placement adapter.
- `EntityPlaceHolder` - Blender entity/diagnostic-empty adapter.
- `SimpleAnimatingSkinnedMeshInstance` - metadata only.
- `ParticleSystemInstance` - metadata only.
- `CoronaInstance` - metadata only.
- `LightAttachedCorona` - metadata only.
- `RenderEffectInstanceImp1` - metadata only.
- `MeshHierachyShaderOverrides` - nested override structure.

### Collision, trigger, and physics records

- `CollisionInstance`
- `PhysicsCollisionInstance`
- `CollisionMeshInstance`
- `StreamingHintTrigger`
- `CollisionTrigger`
- `PhysicsWaterPool`

Collision and physics references, matrices, bounded arrays, and fixed-size
fields are framed. Shapes and simulation behavior are not converted to
Blender collision or rigid-body data.

### Spatial, lighting, audio, and environment records

- `Portal`
- `AmbientSoundPortal`
- `AmbientSoundZone`
- `Zone`
- `SoundMixResource`
- `ReverbZone`
- `Occluder`
- `RenderZone`
- `OmniLight`
- `SpotLight`
- `SunLight`
- `WindBox`
- `Camera`
- `AIAtmosphereBox`

Names, references, shapes, finite vectors/matrices, and array boundaries are
decoded where known. Many fixed-size field groups remain deliberately named
`fields` because their members and units have not been established.

### AI and navigation records

- `AIMarker`
- `AIWaypointNeighbors`
- `AITerrainManager`
- `AINearestWaypoint`
- `AIAreaGraph`
- `AIWaypointAreaRadiusTable`
- `AIWaypointGrid`
- `AIArea`
- `AIWaypointBufferManager`
- `AILinkTypeInfoSpecialObject`

The parser knows the order and byte width of arrays/references required to
frame these records. It does not claim semantic decoding of waypoint cell,
neighbor, area, radius, or link payload bytes.

### Script, resource, and motion records

- `GameScript`
- `WeakResourceReference`
- `BlendExpression`
- `AnimationMotionCurve`
- `AnimationMotionDirectional`
- `LumpOptimizationSettingsGame`

Motion arrays are framed but are distinct from the `EA02` skeleton animation
decoder.

## Embedded `GameScript` chunks

`GameScript` begins with a small CORE/game wrapper containing bytes and
compact scalars of unknown meaning. Padding to a four-byte boundary must be
zero. An optional zero `u32` may follow.

The next 12 bytes must be:

```text
1B 4C 75 61 51 00 00 04 04 04 04 00
```

This identifies the accepted big-endian Lua 5.1 profile. The parser recursively
frames prototypes with:

- length-prefixed byte strings;
- line range, upvalue/parameter/vararg/stack metadata;
- raw four-byte instructions;
- constants;
- nested prototypes;
- line information;
- local-variable records;
- upvalue names.

Accepted constant tags are null (0), boolean (1), four raw bytes (3), and byte
string (4). The parser limits prototype nesting to 128 levels. It preserves
instructions and numeric bytes but never executes gameplay code.

This is not a general Lua bytecode disassembler. Opcode meanings, numeric
interpretation, wrapper fields, and engine bindings remain out of scope.

## Buffered entity graph

### Entity and model links

`EntityResource` is decoded as:

1. resource prefix;
2. animator reference;
3. lockable `u8`;
4. mover reference;
5. model reference;
6. destructibility reference;
7. raw trailing bytes.

`StaticModelResource` and `SkinnedModelResource` each contain a resource
prefix, one mesh reference, and raw trailing bytes.

`ModelPartResource` similarly links to one mesh.
`DestructibilityPartStateResource` links to one model part. These chains let
the importer reach intact renderable mesh states without decoding complete
gameplay behavior.

### State-based destructibility heuristic

`StateBasedDestructibilityResource` is the clearest intentional format
heuristic in this area. Three observed candidate layouts are tried:

| Candidate | Header bytes before count | Bytes before each part reference | Bytes after each reference |
|---:|---:|---:|---:|
| 1 | 90 | 86 | 2 |
| 2 | 91 | 87 | 2 |
| 3 | 103 | 87 | 2 |

If the object's first allocation list has a positive first field divisible by
144, the largest such quotient is treated as an expected part count. A
candidate is rejected when its count differs or a local reference does not
target `DestructibilityPartStateResource`.

Among surviving candidates, the importer prefers:

1. the most non-null part references;
2. then the largest part count;
3. then the earliest candidate above.

If none survive, the remaining bytes are preserved and
`part_states_complete` is false. This strategy is **heuristic**, despite being
validated on known shifted retail layouts. The 144-byte allocation unit and
the internal meaning of the header/per-part raw fields have not been proven.

## Framing versus Blender support

Exact framing means the importer can find the next object without consuming
the wrong bytes and can expose typed fields or bounded raw regions. It does
not imply that a Blender object is created.

Currently:

- `StaticMeshInstance` creates placed environment geometry when its resources
  resolve.
- `EntityPlaceHolder` creates imported props or visible diagnostic empties.
- Shader overrides can provide per-instance lightmaps and variables.
- Collision, physics, particles, lights, zones, portals, cameras, audio,
  scripts, and AI/navigation records remain metadata.

## Known gaps

- Most fixed-size `fields` groups have known framing but unknown member
  semantics, enum values, coordinate units, and flags.
- Direct serializer coverage is profile-locked. An unknown zero-sized type
  cannot be skipped safely and is rejected.
- Level discovery depends on retail manifest naming conventions.
- Complete gameplay dependency graphs and behaviors are not decoded.
- Destructibility selection uses the explicit candidate heuristic documented
  above.
- Only static render placements and mesh-backed entity placeholders have
  specialized Blender adapters.
- `GameScript` is structurally parsed but not disassembled or executed.
