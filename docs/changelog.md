# Changelog

This file records user-visible extension changes and significant validation
results.

## Unreleased

### Added

- Recursive import of `EntityResource` child resources, including their
  render graphs, armatures, and compatible Actions.
- Bone/helper attachment movers and expanded-skeleton helper records. Child
  placements now follow the owning armature while retaining their own rigs.
- Catalog and direct-import support for the validated serialized
  `EntityResource` subclass profile, including `SoldierResource` and
  `TurretResource`.
- RTTI-profiled humanoid body-variant references for `HumanoidResource` and
  `SoldierResource`.
- Exact HGH dropship and character normal-map previews. Proven tangent-space
  maps now feed Blender's Normal Map node, including green-channel
  compensation for the default imported UV-V flip.
- Four exact HGH character packed-material profiles. Their shader-specific
  specular-power channels and scales now drive Principled roughness, while
  packed B drives Specular IOR Level.

### Fixed

- Helghan dropship gun resources are now imported and attached to
  `Turret_Left` and `Turret_Right` instead of remaining hidden references.
- Humanoid entities now import their first/default body variant when the base
  model has a null mesh, so the intact body is no longer omitted.
- Destructibility part IDs and enabled flags are now decoded. Only enabled
  initial states are imported, preventing disabled blood-sheet fragments from
  appearing on intact characters.
- Humanoid head model parts reachable through enabled destructibility states
  now import with the body and share its armature.
- Static keyframed destructibility parts now use their RTTI-defined bone name,
  part-local matrix, and state offset matrix. Shootable Helghast helmets
  therefore follow the animated neck instead of remaining at the entity
  origin.
- Cached-entity Action discovery no longer scans unrelated external COREs
  merely because they provide a compatible shared skeleton.
- Base `DestructibilityResource` objects no longer produce an unsupported
  damage-state warning when they contain no additional render graph.

### Changed

- Entity catalog and SQLite resource-index schema versions were incremented
  so older caches that omit derived resources are rebuilt.
- `EntityPlaceHolder.LinkedObjects` remains distinct from child entity
  resources; direct linked lights are not incorrectly treated as animated
  children.

### Validation

- The complete Python 3.12 suite passed against the supported retail archive:
  **92 tests, zero failures, 14 Blender-only skips**.
- The complete Python 3.13 and `bpy` 5.2.2 suite passed with a freshly built
  retail resource index: **92 tests, zero failures, zero skips**.
- Retail dropship validation imports 19 meshes, three armatures, ten Actions,
  and two helper-attached turret placements that move with the body rig.
- Retail commando validation imports the intact body, head, and helmet on one
  shared 91-bone armature in rest pose, excludes five disabled damage
  fragments, imports no unrelated Actions, and verifies all four exact
  material profiles, normal graphs, and packed roughness graphs.
- Compiled retail RSX shader inspection proves that the HGH dropship and four
  Commando shaders expand `inSampler3` as `RGB * 2 - 1` without a shader-side
  Y inversion. It also proves per-shader `inSampler1` specular-power scales of
  98, 24.5, 14.7024, and 49, with B as specular intensity.
- An embedded retail HGH Commando imports nine compatible Actions. Applying
  one to the canonical commando confirms that
  `StaticMeshResource_00018_P00` moves with the `neck` bone across frames 1,
  16, and 31 while preserving its exact neck-relative transform and
  0.19541-unit mesh-center offset.
- Independent HGH Rifle Infantry validation bone-parents all four helmet LOD
  meshes to its separately decoded `neck` attachment, guarding against a
  Commando-specific implementation.
- A retail-wide destructibility audit decoded 4,060 state-based resources and
  12,406 part-state resources with no decoder failures, including 542
  bone-attached parts across 129 bone names.
- A retail-wide body-variant audit decoded all 115 parseable
  `SoldierResource` records without an ambiguous layout. The one excluded
  online-character CORE has the pre-existing allocation-framing failure
  documented under known limitations.

## 0.5.4 - 2026-11-02

### Blender 5.2 audit integration

Included commits:

- `060ec4c` - Audit and modernize importer for Blender 5.2
- `f29ead8` - Document unsupported retail EA02 variants

#### Added

- Drag-and-drop import for `.core` files in the 3D Viewport and Outliner.
- Multi-file CORE selection through **File > Import > Killzone 2 CORE**. Each
  selected file is imported into its own collection, while results and
  warnings are combined for the final operator report.
- Grouped, collapsible CORE import options for included asset types,
  transforms, external resources, and diagnostics.
- Collapsible **Extracted CORE**, **Complete Level**, and **Cached Entity**
  sections in the KZ2 Tools sidebar.
- Direct navigation from KZ2 Tools to this extension's Blender preferences.
- Manifest website and category metadata plus native extension-build exclusion
  patterns.
- Synthetic Blender-side tests for UV construction, batched skinning, Action
  validation, and in-memory image packing.
- A detailed [Blender 5.2 compatibility audit](./blender-5.2-audit.md).

#### Changed

- Generated textures are packed directly from memory instead of being saved
  through temporary PNG or OpenEXR files.
- Deprecated `Material.use_nodes` assignment was removed. Blender 5.2 creates
  new materials with their node trees enabled.
- Level-map enum items are retained in a runtime cache so Blender never keeps
  references to temporary Python strings.
- CORE, entity, and level import operators leave Edit, Pose, or another
  object mode before creating Blender data.
- Action joint-count and scene-frame-rate validation now happens before an
  Action or slot is created or assigned.
- Retail EA02 limitations now distinguish unsupported nonzero flags from the
  [zero-flag interstitial-header variants](./animation.md#retail-interstitial-header-variants).

#### Performance

- Mesh loop vertex indices are read once with `foreach_get`, and UV layers are
  written in bulk with `foreach_set`.
- Skin influences with the same bone and weight are submitted in batches
  instead of one `VertexGroup.add` call per influence.
- Keyframe interpolation is assigned with one `foreach_set` operation per
  F-curve.
- The audit's synthetic 50,000-triangle mesh measurement reduced mesh-build
  time from 1,131 ms to 399 ms while preserving the tested output
  fingerprints.

#### Fixed

- Dynamic map enum labels can no longer be invalidated after their callback
  returns.
- Starting an import while another object is in Edit or Pose Mode no longer
  leaves Blender with inconsistent mode state.
- Animation joint mismatches fail before leaving an orphan Action.
- Bone-less armatures are rejected before Action-slot assignment, avoiding a
  Blender 5.2 crash path.

#### Validation

- The complete test suite passed under Python 3.13 and `bpy` 5.2.2:
  **82 tests, zero failures, zero skips**.
- All 18 retail-data tests passed against the supported
  `BCUS98116 01.01` archive. These cover archive decoding, real entity
  imports, rigs, skinning, Actions, materials, textures, campaign and
  multiplayer sections, dedicated skybox geometry, and cross-CORE resource
  resolution.
- Extension registration and unregistration completed without residual
  Blender properties or classes.
- The built extension ZIP passed Blender's `blender_ext` manifest validation.

#### Deferred retail EA02 work

The full retail audit found 3,061 zero-flag
`SkeletonAnimationResource` records whose declared header ends before the
first frame-set payload. They occur across 42 logical CORE paths and contain a
nonzero, 16-byte-aligned interstitial block.

An in-memory diagnostic that changed only the header-size field allowed all
3,061 clips to parse and sample their first and final frames. The change was
not implemented because the intervening block's semantics are still unknown.
The inventory, evidence, and acceptance criteria for future support are in
[Skeletons and animation](./animation.md#retail-interstitial-header-variants)
and [Known unknowns](./known-unknowns.md).
