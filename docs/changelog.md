# Changelog

This file records user-visible extension changes and significant validation
results.

## 0.5.12 - 2026-11-14

### Added

- Fourteen exact HGH/ISA tank shader profiles now connect proven base
  tangent-space normal maps and packed material channels to Blender's
  Principled normal, roughness, and specular inputs.
- Tank track profiles multiply the source `DiffuseUV` base color by the
  source `UV0` secondary color layer, matching the compiled fragment
  programs.
- Proven detail-normal samplers are labeled explicitly and retained as
  unconnected non-color nodes. Their vehicle-specific composition remains
  intentionally deferred rather than approximated with a generic blend.

### Research

- RSX fragment-program disassembly established the tank packed-material
  layouts: HGH profiles use `G * 49` or `R * 49`, ISA body profiles use
  `G * 39.1904`, ISA tracks use `R * 39.1904`, and compact ISA materials use
  `R * 49`; all use blue for specular intensity.
- The primary HGH tank family uses `inSampler3` for its base normal and
  `inSampler4` for its detail normal. The primary ISA tank family uses
  `inSampler2` and `inSampler3` respectively.

## 0.5.11 - 2026-11-14

### Fixed

- Explicit destructibility bone attachments are now matched
  case-insensitively while preserving both source and resolved bone names.
  The ISA tank's 16 source `Body` parts now follow the actual `body` bone and
  retain their part/state placement instead of remaining at the entity origin.
- Imported destructibility meshes now record their source state and model-part
  names, and cached armatures record their owning entity.

### Research

- Retail state names identify part-99 mesh `10317` as
  `ISA_Tank_M4_Rocket_FakeShadow_Cube`, not collision geometry. The HGH
  tank's matching mesh `10292` is likewise named
  `HGH_Light_Tank_FakeShadow_Cube`; the preference and metadata terminology
  have been corrected accordingly.
- ISA meshes `10301` and `10303` belong to `LeftTrackInitialState` and
  `RightTrackInitialState`. `_P00` and `_P01` are material primitives within
  those initial-state meshes, not damage-state selectors.
- `Skeleton_11068` is the ISA tank's combined 143-joint tank/occupant rig and
  receives 36 ranged Actions. `Skeleton_90` belongs to the mounted-LMG child
  and receives 18 ranged Actions.

## 0.5.10 - 2026-11-14

### Added

- **Import Fake Shadow Meshes** is now an add-on preference and defaults to
  disabled. The filter recognizes the retail low-poly skinned shadow-volume
  convention shared by the HGH tank, HGH AAPC, ISA buggy, and ISA tank;
  enabling the preference restores those meshes and marks them with
  `kz2_fake_shadow_mesh`.

### Fixed

- Unnamed rigid destructibility pieces now bind to the closest rest joint that
  is demonstrably weighted by a sibling skinned mesh. The tank's
  `StaticMeshResource_00613_P00` therefore follows
  `wheel5_right_suspension` instead of remaining under the entity empty.
- Imported Actions retain explicit start/end ranges derived from native EA02
  sample timing.

### Research

- `HGH_Tank [8393]` uses `Skeleton_11070` for its combined tank/occupant rig:
  ten of its 21 compatible clips animate only vehicle joints, six animate
  vehicle motion plus root/camera joints, four are static poses, and one is a
  mixed hatch/character interaction. `Skeleton_11067` belongs to the mounted
  LMG child and has 19 compatible interaction clips.

## 0.5.9 - 2026-11-14

### Fixed

- `HGH_Tank [8393]` now imports its enabled destructibility assembly instead
  of only the mounted-gun child. Variable-length `DamagePassOn` arrays are
  decoded as part of each destructibility part rather than making the entire
  state layout appear unsupported.
- Child bone/helper attachment lookup is case-insensitive while preserving the
  source and Blender bone names. The tank's `Gunner_Bone` mover now attaches
  to the retail skeleton's `gunner_bone` instead of remaining at the entity
  origin.

### Performance

- EA02 animated frame-set keys, sparse key locations, and packed values are
  decoded once per frame set and reused across sampled frames.
- Blender 5.2's bundled NumPy accelerates DXT1/DXT3/DXT5 surface decoding.
  The existing pure-Python decoder remains as an exact fallback, with
  byte-for-byte regression coverage between both paths.
- A profiled full `HGH_Tank [8393]` import with materials and animations
  improved from **43.691 seconds to 10.103 seconds**. Action construction fell
  from 31.711 seconds to 3.490 seconds, while texture surface decoding fell
  from 4.550 seconds to below the profile's leading call list.

## 0.5.8 - 2026-11-14

### Fixed

- Add-on registration no longer resets scene selectors while Blender exposes
  restricted data during extension enablement. This prevents the
  `'_RestrictData' object has no attribute 'scenes'` failure and the resulting
  partially registered `KZ2_AddonPreferences` class.
- Persistent source and catalog restoration still occurs during registration;
  scene selectors are initialized normally and explicitly refreshed by the
  persistent post-load handler once scene data is available.

### Validation

- The packaged extension enabled through Blender's restricted-data
  `addon_utils` pathway with a persisted decrypted ISO, then restored all
  7,241 entities and 18 levels without a registration error.
- The complete Python 3.12 suite passed against the retail archive and bundled
  index: **100 tests, zero failures, 18 Blender-only skips**.
- The default Python 3.13 and `bpy` 5.2.2 suite passed against the retail
  archive and bundled index: **100 tests, zero failures, seven intentional
  exhaustive-tier skips**.
- `io_scene_killzone2-0.5.8.zip` passed Blender's `blender_ext` manifest
  validator.

## 0.5.7 - 2026-11-14

### Added

- A bundled, fingerprint-validated retail catalog containing 7,241 importable
  entities and all 18 campaign/multiplayer levels.
- A bundled SQLite dependency index that works with either the matching
  extracted PSARC or decrypted ISO.
- Scene-independent persistence for the selected data source and active asset
  catalog in Blender's user configuration directory.

### Changed

- Selecting the supported retail PSARC or ISO activates the bundled cache
  immediately instead of requiring an initial full archive scan.
- User-generated dependency indexes are stored outside the installed extension
  so extension upgrades do not remove them.
- Asset catalog and resource-index schemas now validate against the logical
  PSARC fingerprint rather than a machine-specific file path and timestamp.

### Validation

- The complete Python 3.12 suite passed against the supported retail archive
  and bundled index: **100 tests in 2.559 seconds, zero failures, 18
  Blender-only skips**.
- The default Python 3.13 and `bpy` 5.2.2 suite passed against the supported
  retail archive and bundled index: **100 tests in 7.923 seconds, zero
  failures, seven intentional exhaustive-tier skips**.
- The bundled cache matched both the extracted retail PSARC and the decrypted
  retail ISO, exposing 7,241 entities and 18 levels without a full scan.
- The packaged extension activated the real ISO through its bundled cache and
  restored the selected source and catalog through a new-file load handler and
  in a fresh Blender 5.2.2 process using an isolated user configuration.
- `io_scene_killzone2-0.5.7.zip` passed Blender's `blender_ext` manifest
  validator.

## 0.5.6 - 2026-11-14

### Added

- Direct decrypted-ISO data sources. The importer resolves and streams the
  multi-extent `PS3_GAME/USRDIR/data_ps3.psarc` member without mounting the ISO
  or extracting a temporary archive.

### Validation

- The complete Python 3.12 suite passed against the supported retail archive:
  **98 tests in 1.757 seconds, zero failures, 17 Blender-only skips**.
- The default Python 3.13 and `bpy` 5.2.2 suite passed against the supported
  retail archive and persistent resource index: **98 tests in 7.604 seconds,
  zero failures, seven intentional exhaustive-tier skips**.
- Direct ISO validation streamed all seven extents of the retail
  6,998,576,922-byte PSARC and matched sampled data from every extent against
  the extracted archive. A full scan from the ISO cached **7,241 entities and
  18 levels** with the same seven expected unsupported candidates, then
  resolved an entity through the generated persistent resource index.

## 0.5.5 - 2026-11-14

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
- Blender mesh construction now validates source topology before assigning
  normals and UVs. Invalid or duplicate triangles are repaired with a logged
  warning instead of being silently changed later by an interchange exporter.
- ISA Intruder base-color previews now use exact shader profiles that apply
  the vertex programs' `(z, 1-w)` coordinate transform. This replaces the
  unflipped heuristic that visibly mapped unrelated atlas regions onto the
  vehicle.

### Changed

- Entity catalog and SQLite resource-index schema versions were incremented
  so older caches that omit derived resources are rebuilt.
- `EntityPlaceHolder.LinkedObjects` remains distinct from child entity
  resources; direct linked lights are not incorrectly treated as animated
  children.
- Retail Blender test discovery now runs representative Dropship, character,
  and multiplayer-map end-to-end scenarios by default. Set
  `KZ2_EXHAUSTIVE_E2E=1` to add every retail entity and full-geometry level
  regression.
- Level placements can now be imported as transform and metadata empties when
  static mesh geometry is disabled, keeping placement-only validation
  end-to-end without constructing a complete map's mesh payload.

### Validation

- The complete Python 3.12 suite passed against the supported retail archive:
  **96 tests in 2.235 seconds, zero failures, 17 Blender-only skips**.
- The default Python 3.13 and `bpy` 5.2.2 suite passed against the supported
  retail archive and persistent resource index: **96 tests in 7.710 seconds,
  zero failures, seven intentional exhaustive-tier skips**. The selected
  retail scenarios were the HGH Dropship, canonical Commando attachment, and
  all 295 placements from Visari Hammer. This is roughly 36 times faster than
  the prior 278.669-second focused run that materialized the complete
  multiplayer mesh payload.
- The opt-in exhaustive retail files also passed: **six entity scenarios in
  13.849 seconds** and **four level scenarios in 331.190 seconds**. The level
  total includes the intentionally expensive 280.906-second full multiplayer
  geometry regression.

- Retail dropship validation imports 19 meshes, three armatures, ten Actions,
  and two helper-attached turret placements that move with the body rig.
- Packaged-extension operator tests import and save both canonical Dropships,
  then round-trip their GLB exports in a fresh Blender process. HGH preserves
  19 meshes, 33,632 triangles, three armatures with 3/3/43 bones, all ten
  Actions and the animated turret hierarchy. ISA Intruder preserves 17
  meshes, 27,161 triangles, its 40-bone armature, and its Action. Its
  unsupported damage-state layout remains explicitly warned and skipped.
- Retail commando validation imports the intact body, head, and helmet on one
  shared 91-bone armature in rest pose, excludes five disabled damage
  fragments, imports no unrelated Actions, and verifies all four exact
  material profiles, normal graphs, and packed roughness graphs.
- Compiled retail RSX shader inspection proves that the HGH dropship and four
  Commando shaders expand `inSampler3` as `RGB * 2 - 1` without a shader-side
  Y inversion. It also proves per-shader `inSampler1` specular-power scales of
  98, 24.5, 14.7024, and 49, with B as specular intensity.
- Compiled ISA Intruder vertex and fragment programs prove that all three
  `inSampler0` variants use the source `.zw` UV pair with an explicit
  `1-w` transform. A rendered A/B check confirms the profiled transform
  restores coherent panel, grille, marking, and wheel texture placement.
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
