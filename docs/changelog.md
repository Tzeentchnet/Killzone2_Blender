# Changelog

This file records user-visible extension changes and significant validation
results. The project has not assigned a new release version to the changes
below; the extension manifest remains at version `0.5.3`.

## Unreleased

### 2026-11-02 - Blender 5.2 audit integration

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

