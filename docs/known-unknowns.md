# Known unknowns and working hypotheses

[Documentation index](./README.md)

This page is the consolidated caution list for analysts. It separates bytes
that are merely framed from interpretations that affect Blender output.

> [!WARNING]
> A successful import proves that the file matched the current parser
> contract. It does not prove that every field has the meaning implied by an
> internal variable name, or that Blender reproduces the original renderer or
> runtime behavior.

## Explicit heuristics and policy choices

| Area | Current behavior | Confidence and risk |
|---|---|---|
| PSARC aliases | Entries with the same data offset, uncompressed size, and first block index are treated as one physical payload with logical aliases | Structurally reliable for the supported archive; no explicit alias flag has been identified |
| Level discovery | Fixed retail manifest path patterns identify campaign and multiplayer roots | Retail-validated catalog policy, not a general level format |
| External named resources | Prefer same-level definitions, then shared base lumps, then a globally unique definition | Import policy; no serialized priority field is known |
| Four-component `UV0` | `.xy` is treated as lightmap UV and `.zw` as pre-flipped diffuse UV | Retail-validated for traced optimized level shaders; may be shader/profile dependent |
| Unknown material profile | An sRGB `inSampler0` may become preview base color | Deliberate guess, labeled `srgb_heuristic` in Blender |
| State-based destructibility | Three byte layouts are tried and ranked using allocation-derived part counts and valid part-state references | Deliberate heuristic; raw bytes are retained when no candidate is credible |
| LOD display | Source order/distances are preserved, but non-primary variants begin hidden | Safe preview policy because runtime comparison and units are unknown |
| Animation binding | Bone-map names/order and direct Edge joint order must match armature metadata | Validated compatibility rule; full bone-map semantics are incomplete |
| Clip application | `EA02` transforms are applied as absolute local transforms | Working interpretation; additive/composite semantics are unproven |
| Map axes | Retail map placement matrices are treated as Blender-compatible Z-up XYZ | Retail-validated for supported maps; the legacy Y-up conversion remains diagnostic |

## Structurally known, semantically unknown

### PSARC

- Header flags.
- Any alignment convention beyond lengths and offsets enforced by the reader.
- Other compression tags, version-specific capabilities, or encryption.
- Whether equal physical tuples are the engine's complete alias definition.

### CORE container

- Three `u32` and two `u16` fields after the endian marker.
- Twelve metadata-tail bytes per object.
- The five values in each allocation record and the roles of the four
  allocation-list locations.
- The scalar between string banks and the second global allocation list.
- The byte after a resource name in `ResourcePrefix`.
- Top-level trailing bytes.

### Geometry and skinning

- Vertex-stream flags and the final byte of each declaration.
- Eight index-array header bytes.
- Primitive flags, spatial-tree payload semantics, and rendering-primitive
  raw bytes.
- Twenty-nine common mesh-prefix bytes.
- Nineteen static-mesh tail bytes after `LocalTranslateScale`.
- The 19-byte skinned-mesh field.
- Skin-info type 0 versus type 1 runtime meaning.
- Exact LOD distance origin, unit, comparison, and equality rule.
- Switch-mesh selection behavior and the
  `parts_use_the_same_mesh` field's exact type.

### Textures and materials

- Cubemap face names, orientation, and conversion to Blender's environment
  convention.
- Pixel-format codes not in the six decoded retail profiles.
- Complete numeric enums for render states and sampler-state nibbles.
- General semantics for shader-variable types outside the float-like types
  currently exposed.
- Compiled `SHO` instruction semantics outside targeted shader research.
- Full normal signs, packed material channels, response-ramp coordinates,
  emissive effects, IBL, and secondary detail-layer arithmetic.

### Levels and entities

- Member semantics for the many fixed-size direct-object `fields` groups.
- Collision, physics, light, audio, particle, zone, camera, portal, and
  AI/navigation units and enums.
- `GameScript` wrapper scalars and engine binding behavior.
- The 90/91/103-byte destructibility headers, 86/87-byte per-part prefixes,
  two-byte suffixes, and the inferred 144-byte allocation unit.
- Complete gameplay dependency and destructibility state behavior.

### Skeletons and animation

- Four of six `ES00` offset targets.
- Expanded skeleton pre-joint tables, auxiliary records, unknown arrays, and
  12-byte joint tails.
- Skeleton wrapper arrays and IK units/space.
- Complete `SkeletonAnimationBoneMap` behavior.
- Animation wrapper version/flags/index meanings.
- `EA02` flags other than zero.
- Evaluation-buffer size, DMA words, reserved header fields, and some final
  frame-set size entries.
- User/scalar channel values and destinations.
- The eighth locomotion float and runtime root-motion application.
- Additive, composite, directional blend, and time-map semantics.

## Unsupported variants versus unknown fields

These cases are not merely unknown metadata; the importer cannot currently
produce the corresponding result:

- non-`BCUS98116 01.01` / non-`RTTIBin<1.58>` CORE revisions;
- PSARC versions other than `0x00010003` or compression tags other than
  `zlib`;
- an unregistered zero-sized direct serializer;
- 32-bit or non-triangle-list index buffers;
- texture types other than 2D and cubemap;
- texture pixel formats outside RGBA8, reversed RGBA8, DXT1/3/5, and RGBA16F;
- cubemap-to-Blender environment conversion;
- `EA02` transform sampling when flags are nonzero;
- Blender mapping for scalar/user animation channels;
- specialized Blender adapters for most direct level metadata.

Unknown buffered CORE types are different: their payload boundaries are
known, so they remain `UnknownObject` and do not prevent later objects from
being framed.

## What Blender output should not be used to prove

- A Principled BSDF preview is not evidence of the original deferred shader's
  complete arithmetic.
- A connected `inSampler0` on an unmatched shader is not proof that sampler 0
  is albedo.
- Hidden secondary LODs do not prove the runtime selects the first part.
- A diagnostic empty does not prove an entity has no renderable data; it may
  indicate unresolved or ambiguous dependencies.
- Missing collision, lights, particles, audio, or AI objects in Blender do
  not mean those records were absent from the CORE.
- An imported absolute Action does not prove the source clip was non-additive.

## How to improve confidence

Useful evidence should identify:

1. game region, title ID, and executable/data version;
2. PSARC logical path;
3. CORE reflected type, object index, and displayed opaque ID;
4. byte offset and the exact structural observation;
5. whether the result came from parser validation, cross-file comparison,
   shader disassembly, runtime capture, or Blender appearance;
6. counterexamples, not only a single matching asset.

The archive auditor can collect aggregate evidence without exporting game
payloads:

```powershell
python .\tools\audit_retail_archive.py `
  "E:\path\to\data_ps3.psarc" `
  --output .\kz2-audit.json
```

When proposing a semantic name, keep the raw value available and state whether
the conclusion is enforced, retail-validated, an interpretation, or still
unknown. Avoid replacing unknown bytes with zero or silently accepting a new
layout based only on enough bytes being available.
