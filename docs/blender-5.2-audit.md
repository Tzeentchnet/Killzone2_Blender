# Blender 5.2 compatibility and feature audit

Audit of the `io_scene_killzone2` extension (version 0.5.3) against Blender
**5.2.3 LTS**. The changes listed under
[Implemented](#implemented-by-this-audit) are included with this audit.

## Method

- Reviewed all Blender-facing modules (`__init__.py`, `operators.py`,
  `import_scene.py`, `import_materials.py`, and the manifest). The `kz2_assets`
  decoders do not use `bpy` and are only covered where the UI depends on them.
- Checked every API claim at runtime against the official `bpy==5.2.3` wheel
  from PyPI, running headless on Python 3.13. Claims were not taken from
  memory.
- Validated the manifest and built ZIP with Blender's bundled `blender_ext`
  tool. Installed, enabled, and disabled the extension through
  `extensions.package_install_files`.
- Measured performance and output equivalence with synthetic geometry,
  skinning, animation, and texture data. No retail game data was available,
  so a full CORE, level, or entity import was **not** run end to end. The 18
  `KZ2_PSARC` integration tests are still skipped.
- The sidebar and operator `draw()` code was run against a recording layout
  with a stub catalog. It was not rendered in a GUI session.

## What is already modern

The extension already targets current Blender well:

| Area | Status |
|---|---|
| Extension platform | `blender_manifest.toml` passes validation. Uses `extension_path_user()` for the SQLite index. Registers and unregisters on 5.2.3 without leftover properties. |
| Animation | Uses the slotted/layered Action API (`action.slots`, `layers`, `strips`, `channelbag(..., ensure=True)`, `animation_data.action_slot`). |
| Normals | Uses `normals_split_custom_set_from_vertices` without legacy auto-smooth. On 5.2, custom normals apply to both flat and smooth faces (verified). |
| Node groups | Uses the `NodeTree.interface.new_socket` API, `ShaderNodeSeparateColor`/`CombineColor`, and Principled v2 socket names such as `Specular IOR Level`. |
| Removed APIs | No `bgl`, `blend_method`/`shadow_method`, `use_auto_smooth`, `calc_normals_split`, or legacy `action.fcurves` usage. |

## Implemented by this audit

| # | Finding | Evidence on 5.2.3 | Change |
|---|---|---|---|
| 1 | UV layers were written with one RNA assignment per loop, twice for packed UV0. This dominated mesh build time. | Synthetic 50k-triangle mesh: **1131 ms → 399 ms** (2.8×). Position, normal, and UV fingerprint unchanged. | Read loop vertices once with `foreach_get`. Write each layer with `foreach_set`. |
| 2 | Skin weights used one `VertexGroup.add` call per influence. | Weight fingerprint unchanged. 58 → 50 ms on synthetic data. Real gains depend on how often quantized weights repeat. | Batch vertices per `(bone, weight)` pair. |
| 3 | Keyframe interpolation was set one keyframe at a time. | Action F-curve fingerprint unchanged; all keys `LINEAR`. | One `keyframe_points.foreach_set("interpolation", ...)` per curve. |
| 4 | `Material.use_nodes = True` is deprecated. | 5.2.3 warns: *"'Material.use_nodes' is expected to be removed in Blender 6.0"*. New materials already have a node tree. | Removed. |
| 5 | Decoded images were saved to a temporary PNG/EXR, packed, then deleted. | `Image.pack()` encodes generated images in memory (PNG for byte buffers, OpenEXR for float buffers). Save/reload error is identical to the old path (7.4e-8 for PNG, 4.7e-7 for EXR). | In-memory pack. `tempfile` dependency removed. |
| 6 | The dynamic `EnumProperty` items callback for the map list returned new strings on every call. Blender's documentation requires Python to keep them referenced, or labels can be garbled or Blender can crash. | The requirement is still in the 5.2 `EnumProperty` docstring. | Items are cached per catalog object and map group, and cleared on rescan. |
| 7 | Importing while another object was in Edit Mode left inconsistent mode state, because armature creation toggles Edit Mode on new objects. | Reproduced: `context.mode == "EDIT_MESH"` while the edited object reported `OBJECT`. | All three import operators switch to Object Mode first. |
| 8 | `_create_action` assigned the Action and slot **before** validating the joint count. Blender 5.2.3 segfaults when a slot is assigned to an armature with zero bones. | Minimal crash reproduced (bone-less armature with `action_slot = slot`). Armatures with bones are unaffected. | Validate before creating any data. Mismatches now raise without leaving an orphan Action. |
| 9 | No drag-and-drop and no multi-file import. | `bpy.types.FileHandler` and `ImportHelper.invoke_popup` are available. | Added `KZ2_FH_core`: drop `.core` files into the 3D Viewport or Outliner. The importer accepts `directory` + `files`, imports each file into its own collection, and reports per-file errors. |
| 10 | Import options were a flat list. | `UILayout.panel()` is available. | Grouped into collapsible Include, Transform, External Resources, and Diagnostics panels in both the file browser and the drop dialog. |
| 11 | Sidebar sections were static boxes. | — | The CORE, Complete Level, and Cached Entity sections are collapsible `layout.panel()` sections. Scan issues remain visible when the entity list is empty. |
| 12 | **Settings** opened Preferences without locating the extension. | — | Uses `preferences.addon_show(module=...)`, which filters to this extension and expands its settings. |
| 13 | The manifest lacked `website`, `tags`, and a `[build]` section. | Still passes `blender_ext validate`. | Added these, so `blender --command extension build` also works natively. |
| — | No Blender-side tests ran without game data. | — | Added `tests/test_blender_builders.py`, which uses synthetic data and is skipped without `bpy`. |

## Recommendations not implemented

Ordered by expected value. Each needs a design decision or validation with
retail data first.

### High

1. **Non-blocking scan and level import.** `scan_entity_asset_catalog` does
   not use `bpy`, so it can run in a worker thread under a modal operator. A
   `bpy.app.timers` or modal timer would poll it, ESC would cancel, and
   progress could show through `UILayout.progress()`. Today the first scan
   freezes the UI for minutes, and `progress_begin(0, 1)` never advances.
   Level import must stay on the main thread, but it could be split across
   modal timer ticks, one CORE per tick.
2. **Move the catalog out of `AddonPreferences`.** The whole entity and map
   catalog is a JSON string in `userpref.blend`. Every sidebar redraw, `poll()`,
   and enum callback copies and compares that string, then runs
   `Path.resolve()` and `stat()` on the PSARC. Storing the catalog next to the
   SQLite index under `extension_path_user()` and caching validity for the
   redraw would remove that per-frame I/O.
3. **Native list for the entity browser.** Replace the manual 12-row
   pagination with a `UIList` (`filter_items`, scrolling, sorting, built-in
   filter field) backed by a `CollectionProperty`. Alternatively, add a
   `StringProperty(search=...)` type-ahead for direct name lookup.
4. **Group the scene settings.** Sixteen loose `Scene.kz2_*` properties
   should become one `Scene.kz2` `PointerProperty` to a `PropertyGroup`. This
   is a breaking change for values saved in existing `.blend` files, so plan a
   migration.

### Medium

5. **Material render settings, once enums are validated.** `blend_mode` could
   map to `surface_render_method = "BLENDED"` with `use_transparency_overlap`.
   `cull_mode` could map to `use_backface_culling` and
   `use_backface_culling_shadow`. `alpha_test_*` could map to a cutout. These
   are deliberately not implemented because
   [known-unknowns](known-unknowns.md) lists the render-state enums as
   unvalidated. Until then, exposing the decoded fields (not only the raw hex)
   as material properties would help analysis.
6. **Animation UX.** Add a sidebar clip switcher that lists Actions whose
   `kz2_target_armature` matches the active armature. Activating a clip would
   assign the Action and slot and set the scene frame range. Optionally push
   clips to NLA tracks, or match the scene frame rate to `sample_frequency`.
7. **Armature display.** Derive bone lengths from child distance (only
   cosmetic; `matrix_local` is unchanged). Group bones into bone collections,
   and offer a `display_type` choice.
8. **LOD and switch visibility.** Add a sidebar control, or child collections
   per LOD and switch key, so users can show LOD *n* across a level instead of
   unhiding objects one by one.
9. **Further mesh speedups.** Most of the remaining 399 ms is `from_pydata`
   and building Python tuples. Use `vertices`/`loops`/`polygons.foreach_set`
   with NumPy arrays (NumPy ships with Blender). `shade_flat=False` would avoid
   allocating a `sharp_face` attribute. Custom normals were verified to look
   the same either way.
10. **Instance repeated props.** Mesh data is already shared. Repeated entity
    props could become collection instances, which would greatly reduce object
    count and viewport cost on full maps.

### Low

11. Remove `bl_info` from `__init__.py`. Extensions ignore it, and it
    duplicates the version, so the two can drift.
12. Add CI that installs `bpy==5.2.*` from PyPI, runs
    `tests/test_blender_builders.py`, and runs `blender_ext validate` on the
    built ZIP.
13. Convert cubemaps to an equirectangular world texture. The README lists
    this as not yet mapped.
14. Add an optional "build asset library" step that marks imported entities
    with `asset_mark()` and `asset_generate_preview()`. The scanned catalog
    would then be browsable and draggable from Blender's Asset Browser.
15. Write the full warning list to a Text datablock such as
    `KZ2 Import Report`. Operator reports currently stop after 10 warnings.

## Reproducing the verification

```bash
python3 -m venv .bpy && .bpy/bin/pip install bpy==5.2.3
.bpy/bin/python -m unittest discover -s tests -v
python3 tools/build_extension.py
.bpy/bin/python .bpy/lib/python3.13/site-packages/bpy/5.2/scripts/addons_core/bl_pkg/cli/blender_ext.py validate dist/io_scene_killzone2-0.5.3.zip
```
