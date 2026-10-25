"""Blender operators, preferences, and menu registration."""

from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import replace
from pathlib import Path
from typing import ClassVar

import bpy
from bpy.props import (
    BoolProperty,
    EnumProperty,
    FloatProperty,
    IntProperty,
    StringProperty,
)
from bpy.types import AddonPreferences, Operator, Panel
from bpy_extras.io_utils import ImportHelper

from .import_scene import (
    ImportOptions,
    ImportResult,
    import_core_file,
    import_psarc_entity,
    import_psarc_level,
)
from .kz2_assets.asset_catalog import (
    ENTITY_CATEGORY_ALL,
    ENTITY_CATEGORY_ITEMS,
    CachedEntity,
    EntityAssetCatalog,
    cached_entity_source_label,
    filter_cached_entity_indices,
    scan_entity_asset_catalog,
)
from .kz2_assets.errors import FormatError, Kz2AssetError
from .kz2_assets.level_catalog import (
    LEVEL_KIND_CAMPAIGN,
    LEVEL_KIND_ITEMS,
    CachedLevel,
    scan_level_catalog,
)
from .kz2_assets.resource_index import RESOURCE_INDEX_VERSION

_LOG = logging.getLogger(__name__)
_ADDON_ID = __package__
_NO_ITEMS = "__NONE__"
_ENTITY_PAGE_SIZE = 12
_cached_json = ""
_cached_catalog: EntityAssetCatalog | None = None


def _invalidate_runtime_cache() -> None:
    global _cached_json, _cached_catalog
    _cached_json = ""
    _cached_catalog = None


def _addon_preferences(
    context: bpy.types.Context,
) -> KZ2_AddonPreferences:
    addon = context.preferences.addons.get(_ADDON_ID)
    if addon is None:
        raise FormatError(
            "Killzone 2 Asset Importer preferences are unavailable"
        )
    return addon.preferences


def _catalog_from_preferences(
    preferences: KZ2_AddonPreferences,
) -> EntityAssetCatalog | None:
    global _cached_json, _cached_catalog
    raw = preferences.entity_cache_json
    if not raw:
        return None
    if raw != _cached_json:
        _cached_catalog = EntityAssetCatalog.from_json(raw)
        _cached_json = raw
    return _cached_catalog


def _current_catalog(
    context: bpy.types.Context,
) -> EntityAssetCatalog | None:
    preferences = _addon_preferences(context)
    catalog = _catalog_from_preferences(preferences)
    if catalog is None or not preferences.archive_source:
        return None
    source_path = bpy.path.abspath(preferences.archive_source)
    if not catalog.matches_source(source_path):
        return None
    return catalog


def _safe_current_catalog(
    context: bpy.types.Context | None,
) -> EntityAssetCatalog | None:
    if context is None:
        return None
    try:
        return _current_catalog(context)
    except (Kz2AssetError, OSError):
        return None


def _selected_entity(
    context: bpy.types.Context,
    catalog: EntityAssetCatalog,
) -> CachedEntity:
    identifier = context.scene.kz2_entity_asset
    if identifier == _NO_ITEMS:
        raise FormatError("No cached entity is selected")
    try:
        index = int(identifier)
    except ValueError as exc:
        raise FormatError(
            "Cached entity selection is invalid; rescan the PSARC"
        ) from exc
    if not 0 <= index < len(catalog.entities):
        raise FormatError(
            "Cached entity selection is stale; rescan the PSARC"
        )
    return catalog.entities[index]


def _selected_level(
    context: bpy.types.Context,
    catalog: EntityAssetCatalog,
) -> CachedLevel:
    identifier = context.scene.kz2_level_asset
    if identifier == _NO_ITEMS:
        raise FormatError("No cached level is selected")
    try:
        index = int(identifier)
    except ValueError as exc:
        raise FormatError(
            "Cached level selection is invalid; refresh the map catalog"
        ) from exc
    if not 0 <= index < len(catalog.levels):
        raise FormatError(
            "Cached level selection is stale; refresh the map catalog"
        )
    level = catalog.levels[index]
    if level.kind != context.scene.kz2_level_kind:
        raise FormatError(
            "Cached level selection does not match the active map group"
        )
    return level


def _level_asset_items(
    _scene: bpy.types.Scene,
    context: bpy.types.Context | None,
) -> list[tuple[str, str, str]]:
    catalog = _safe_current_catalog(context)
    if catalog is None:
        return [(_NO_ITEMS, "No Maps", "Index the configured PSARC first")]
    items = [
        (
            str(index),
            level.display_name,
            (
                f"{len(level.sections)} section"
                f"{'' if len(level.sections) == 1 else 's'}"
            ),
        )
        for index, level in enumerate(catalog.levels)
        if level.kind == context.scene.kz2_level_kind
    ]
    return items or [
        (_NO_ITEMS, "No Maps", "No maps were cached for this group")
    ]


def _reset_level_section_selection(
    scene: bpy.types.Scene,
    _context: bpy.types.Context,
) -> None:
    scene.kz2_level_section = 0


def _reset_level_map_selection(
    scene: bpy.types.Scene,
    context: bpy.types.Context,
) -> None:
    catalog = _safe_current_catalog(context)
    if catalog is None:
        scene.kz2_level_asset = _NO_ITEMS
        scene.kz2_level_section = 0
        return
    identifier = next(
        (
            str(index)
            for index, level in enumerate(catalog.levels)
            if level.kind == scene.kz2_level_kind
        ),
        _NO_ITEMS,
    )
    scene.kz2_level_asset = identifier
    scene.kz2_level_section = 0


def _configured_psarc(context: bpy.types.Context) -> str:
    preferences = _addon_preferences(context)
    if not preferences.archive_source:
        return ""
    return bpy.path.abspath(preferences.archive_source)


def _configured_resource_index(
    context: bpy.types.Context,
    source_psarc: str,
) -> str:
    if not source_psarc:
        return ""
    catalog = _safe_current_catalog(context)
    if catalog is None:
        return ""
    resolved = str(Path(source_psarc).expanduser().resolve())
    if os.path.normcase(resolved) != os.path.normcase(catalog.source_path):
        return ""
    return catalog.resource_index_path


def _resource_index_path(source_path: str) -> str:
    resolved = Path(source_path).expanduser().resolve()
    source_stat = resolved.stat()
    signature = (
        f"{os.path.normcase(str(resolved))}\0"
        f"{source_stat.st_size}\0{source_stat.st_mtime_ns}"
    )
    digest = hashlib.sha256(signature.encode("utf-8")).hexdigest()[:16]
    cache_root = Path(
        bpy.utils.extension_path_user(
            _ADDON_ID,
            path=".cache",
            create=True,
        )
    )
    return str(
        cache_root
        / f"resource-index-v{RESOURCE_INDEX_VERSION}-{digest}.sqlite3"
    )


def _reset_scene_selections(catalog: EntityAssetCatalog) -> None:
    identifier = "0" if catalog.entities else _NO_ITEMS
    for scene in bpy.data.scenes:
        scene.kz2_entity_asset = identifier
        scene.kz2_entity_category = ENTITY_CATEGORY_ALL
        scene.kz2_entity_search = ""
        scene.kz2_entity_page = 1
        level_index = next(
            (
                index
                for index, level in enumerate(catalog.levels)
                if level.kind == LEVEL_KIND_CAMPAIGN
            ),
            0,
        )
        scene.kz2_level_kind = (
            catalog.levels[level_index].kind
            if catalog.levels
            else LEVEL_KIND_CAMPAIGN
        )
        scene.kz2_level_asset = (
            str(level_index) if catalog.levels else _NO_ITEMS
        )
        scene.kz2_level_section = 0


def _reset_entity_browser_page(
    scene: bpy.types.Scene,
    _context: bpy.types.Context,
) -> None:
    scene.kz2_entity_page = 1


def _report_import_result(
    operator: Operator,
    result: ImportResult,
) -> None:
    for warning in result.warnings[:10]:
        _LOG.warning("%s", warning)
        operator.report({"WARNING"}, warning)
    if len(result.warnings) > 10:
        _LOG.warning(
            "%d additional warnings were omitted",
            len(result.warnings) - 10,
        )
        operator.report(
            {"WARNING"},
            f"{len(result.warnings) - 10} additional warnings were omitted",
        )
    operator.report(
        {"INFO"},
        (
            f"Imported {result.meshes} meshes, {result.armatures} armatures, "
            f"{result.level_instances} level instances, "
            f"{result.entity_instances} entity placements, "
            f"{result.materials} materials, {result.images} images, and "
            f"{result.actions} actions"
        ),
    )


class KZ2_AddonPreferences(AddonPreferences):
    bl_idname = _ADDON_ID

    archive_source: StringProperty(  # type: ignore[valid-type]
        name="Killzone 2 PSARC",
        description="Authorized retail data_ps3.psarc archive",
        subtype="FILE_PATH",
        default="",
    )
    entity_cache_json: StringProperty(  # type: ignore[valid-type]
        name="Entity Asset Cache",
        options={"HIDDEN"},
        default="",
    )

    def draw(self, context: bpy.types.Context) -> None:
        layout = self.layout
        layout.prop(self, "archive_source")
        layout.operator(
            KZ2_OT_scan_entities.bl_idname,
            text="Scan / Rescan Assets and Maps",
            icon="FILE_REFRESH",
        )
        layout.label(
            text=(
                "The first scan may take several minutes, especially on "
                "external USB drives."
            ),
            icon="INFO",
        )
        if not self.entity_cache_json:
            layout.label(
                text="Choose data_ps3.psarc, then scan once.",
                icon="INFO",
            )
            return
        try:
            catalog = _catalog_from_preferences(self)
        except Kz2AssetError as exc:
            box = layout.box()
            box.alert = True
            box.label(text=str(exc), icon="ERROR")
            return
        if catalog is None:
            return
        try:
            source_matches = bool(
                self.archive_source
                and catalog.matches_source(
                    bpy.path.abspath(self.archive_source)
                )
            )
        except OSError as exc:
            box = layout.box()
            box.alert = True
            box.label(text=str(exc), icon="ERROR")
            return
        if not source_matches:
            box = layout.box()
            box.alert = True
            box.label(
                text="The PSARC changed after scanning. Rescan it.",
                icon="ERROR",
            )
            return
        box = layout.box()
        box.label(
            text=(
                f"Cached: {len(catalog.entities)} entities, "
                f"{len(catalog.levels)} maps"
            ),
            icon="CHECKMARK",
        )
        box.label(text="External dependency index: Ready")
        if not catalog.levels:
            box.operator(
                KZ2_OT_scan_levels.bl_idname,
                text="Index Maps (Fast)",
                icon="WORLD_DATA",
            )
        if catalog.issues:
            warning = box.row()
            warning.alert = True
            warning.label(
                text=(
                    f"{len(catalog.issues)} unsupported candidate(s) "
                    "were skipped"
                ),
                icon="ERROR",
            )


class KZ2_OT_scan_entities(Operator):
    """Scan the configured PSARC and persist assets and map hierarchy."""

    bl_idname = "kz2.scan_entities"
    bl_label = "Scan Importable Entities"
    bl_description = (
        "Index render-backed entities, maps, and external dependencies in the "
        "configured PSARC; the first scan may take several minutes"
    )
    bl_options: ClassVar[set[str]] = {"REGISTER"}

    def execute(self, context: bpy.types.Context):
        preferences = _addon_preferences(context)
        if not preferences.archive_source:
            self.report(
                {"ERROR"},
                "Choose a Killzone 2 data_ps3.psarc first",
            )
            return {"CANCELLED"}
        source_path = bpy.path.abspath(preferences.archive_source)
        resource_index_path = _resource_index_path(source_path)
        context.window_manager.progress_begin(0, 1)
        try:
            catalog = scan_entity_asset_catalog(
                source_path,
                resource_index_path=resource_index_path,
            )
        except (Kz2AssetError, OSError) as exc:
            _LOG.error("Entity indexing failed: %s", exc)
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        finally:
            context.window_manager.progress_end()
        preferences.archive_source = catalog.source_path
        preferences.entity_cache_json = catalog.to_json()
        _invalidate_runtime_cache()
        _catalog_from_preferences(preferences)
        _reset_scene_selections(catalog)
        if catalog.issues:
            self.report(
                {"WARNING"},
                f"Skipped {len(catalog.issues)} unsupported candidate(s)",
            )
        self.report(
            {"INFO"},
            f"Cached {len(catalog.entities)} importable entities, "
            f"{len(catalog.levels)} maps, and external dependencies",
        )
        return {"FINISHED"}


class KZ2_OT_scan_levels(Operator):
    """Add the map hierarchy to an existing entity catalog."""

    bl_idname = "kz2.scan_levels"
    bl_label = "Index Maps"
    bl_description = (
        "Read only the small campaign and multiplayer manifests to add maps "
        "and sections without repeating the full entity scan"
    )
    bl_options: ClassVar[set[str]] = {"REGISTER"}

    def execute(self, context: bpy.types.Context):
        try:
            catalog = _current_catalog(context)
            if catalog is None:
                raise FormatError(
                    "Scan the configured PSARC before indexing maps"
                )
            levels = scan_level_catalog(catalog.source_path)
        except (Kz2AssetError, OSError) as exc:
            _LOG.error("Map indexing failed: %s", exc)
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        updated = replace(catalog, levels=levels)
        preferences = _addon_preferences(context)
        preferences.entity_cache_json = updated.to_json()
        _invalidate_runtime_cache()
        _catalog_from_preferences(preferences)
        _reset_scene_selections(updated)
        self.report({"INFO"}, f"Cached {len(levels)} campaign and MP maps")
        return {"FINISHED"}


class KZ2_OT_open_preferences(Operator):
    """Open Blender preferences to configure the Killzone 2 PSARC."""

    bl_idname = "kz2.open_preferences"
    bl_label = "Open Add-on Preferences"

    def execute(self, context: bpy.types.Context):
        context.preferences.active_section = "ADDONS"
        bpy.ops.screen.userpref_show("INVOKE_DEFAULT")
        return {"FINISHED"}


class IMPORT_SCENE_OT_killzone2_core(Operator, ImportHelper):
    bl_idname = "import_scene.killzone2_core"
    bl_label = "Import Killzone 2 CORE"
    bl_description = (
        "Import BCUS98116 01.01 meshes, materials, skeletons, skinning, "
        "EA02 animations, and placed level geometry"
    )
    bl_options: ClassVar[set[str]] = {"REGISTER", "UNDO"}

    filename_ext = ".core"
    filter_glob: StringProperty(  # type: ignore[valid-type]
        default="*.core",
        options={"HIDDEN"},
        maxlen=255,
    )
    global_scale: FloatProperty(  # type: ignore[valid-type]
        name="Scale",
        description="Uniform scale applied to mesh and skeleton translations",
        default=1.0,
        min=1e-6,
        soft_max=100.0,
    )
    flip_uv_v: BoolProperty(  # type: ignore[valid-type]
        name="Flip UV V",
        description=(
            "Convert Killzone's top-origin V coordinate to Blender's UV convention"
        ),
        default=True,
    )
    import_static_meshes: BoolProperty(  # type: ignore[valid-type]
        name="Static Meshes",
        default=True,
    )
    import_skinned_meshes: BoolProperty(  # type: ignore[valid-type]
        name="Skinned Meshes",
        default=True,
    )
    import_skeletons: BoolProperty(  # type: ignore[valid-type]
        name="Skeletons",
        default=True,
    )
    import_animations: BoolProperty(  # type: ignore[valid-type]
        name="Animations",
        description="Decode packed EA02 transform tracks into Blender Actions",
        default=True,
    )
    import_materials: BoolProperty(  # type: ignore[valid-type]
        name="Materials and Textures",
        description="Decode local textures and create preview material graphs",
        default=True,
    )
    import_level_objects: BoolProperty(  # type: ignore[valid-type]
        name="Level Placements",
        description=(
            "Import direct StaticMeshInstance records and EntityPlaceHolder "
            "props instead of unplaced mesh definitions"
        ),
        default=True,
    )
    convert_map_axes: BoolProperty(  # type: ignore[valid-type]
        name="Legacy Y-Up Map Conversion",
        description=(
            "Diagnostic compatibility with version 0.3.1 and earlier: "
            "convert level XYZ to (x, -z, y); leave disabled for "
            "Blender-correct Z-up maps"
        ),
        default=False,
    )
    source_psarc: StringProperty(  # type: ignore[valid-type]
        name="Source PSARC",
        description=(
            "Optional data_ps3.psarc used to resolve external resources; "
            "blank uses the PSARC configured in add-on preferences"
        ),
        subtype="FILE_PATH",
        default="",
    )

    def execute(self, context: bpy.types.Context):
        try:
            source_psarc = self.source_psarc or _configured_psarc(context)
            resource_index_path = _configured_resource_index(
                context,
                source_psarc,
            )
            options = ImportOptions(
                global_scale=self.global_scale,
                flip_uv_v=self.flip_uv_v,
                import_static_meshes=self.import_static_meshes,
                import_skinned_meshes=self.import_skinned_meshes,
                import_skeletons=self.import_skeletons,
                import_animations=self.import_animations,
                import_materials=self.import_materials,
                import_level_objects=self.import_level_objects,
                convert_map_axes=self.convert_map_axes,
                source_psarc=source_psarc,
                resource_index_path=resource_index_path,
            )
            result = import_core_file(context, self.filepath, options)
        except Kz2AssetError as exc:
            _LOG.error("CORE import failed: %s", exc)
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        except (OSError, ValueError) as exc:
            _LOG.error("CORE import failed: %s", exc)
            self.report({"ERROR"}, f"Killzone 2 import failed: {exc}")
            return {"CANCELLED"}
        _report_import_result(self, result)
        return {"FINISHED"}


class KZ2_OT_import_cached_entity(Operator):
    """Import the selected cached entity from the configured PSARC."""

    bl_idname = "kz2.import_cached_entity"
    bl_label = "Import Entity"
    bl_options: ClassVar[set[str]] = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        catalog = _safe_current_catalog(context)
        return catalog is not None and bool(catalog.entities)

    def execute(self, context: bpy.types.Context):
        try:
            catalog = _current_catalog(context)
            if catalog is None:
                raise FormatError(
                    "Scan the configured PSARC before importing an entity"
                )
            entity = _selected_entity(context, catalog)
            options = ImportOptions(
                global_scale=context.scene.kz2_entity_scale,
                flip_uv_v=context.scene.kz2_entity_flip_uv_v,
                import_animations=context.scene.kz2_entity_animations,
                import_materials=context.scene.kz2_entity_materials,
                source_psarc=catalog.source_path,
                resource_index_path=catalog.resource_index_path,
            )
            result = import_psarc_entity(
                context,
                catalog.source_path,
                entity.path,
                entity.object_index,
                options,
            )
        except (Kz2AssetError, OSError, ValueError) as exc:
            _LOG.error("Cached entity import failed: %s", exc)
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        _report_import_result(self, result)
        return {"FINISHED"}


class KZ2_OT_select_cached_entity(Operator):
    """Select a cached entity in the filtered asset browser."""

    bl_idname = "kz2.select_cached_entity"
    bl_label = "Select Entity"
    bl_options: ClassVar[set[str]] = {"INTERNAL"}

    entity_index: IntProperty(options={"HIDDEN"})  # type: ignore[valid-type]

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        catalog = _safe_current_catalog(context)
        return catalog is not None and bool(catalog.entities)

    def execute(self, context: bpy.types.Context):
        try:
            catalog = _current_catalog(context)
        except (Kz2AssetError, OSError) as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        if catalog is None or not 0 <= self.entity_index < len(
            catalog.entities
        ):
            self.report(
                {"ERROR"},
                "Cached entity selection is stale; rescan the PSARC",
            )
            return {"CANCELLED"}
        context.scene.kz2_entity_asset = str(self.entity_index)
        return {"FINISHED"}


class KZ2_OT_set_entity_page(Operator):
    """Change the filtered cached-entity browser page."""

    bl_idname = "kz2.set_entity_page"
    bl_label = "Change Entity Page"
    bl_options: ClassVar[set[str]] = {"INTERNAL"}

    page: IntProperty(default=1, min=1, options={"HIDDEN"})  # type: ignore[valid-type]

    def execute(self, context: bpy.types.Context):
        context.scene.kz2_entity_page = self.page
        return {"FINISHED"}


class KZ2_OT_select_level_section(Operator):
    """Select a section in the cached map hierarchy."""

    bl_idname = "kz2.select_level_section"
    bl_label = "Select Level Section"
    bl_options: ClassVar[set[str]] = {"INTERNAL"}

    section_index: IntProperty(  # type: ignore[valid-type]
        default=0,
        min=0,
        options={"HIDDEN"},
    )

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        catalog = _safe_current_catalog(context)
        return catalog is not None and bool(catalog.levels)

    def execute(self, context: bpy.types.Context):
        try:
            catalog = _current_catalog(context)
            if catalog is None:
                raise FormatError("Refresh the configured map catalog")
            level = _selected_level(context, catalog)
            if not 0 <= self.section_index < len(level.sections):
                raise FormatError(
                    "Cached level section is stale; refresh the map catalog"
                )
        except (Kz2AssetError, OSError) as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        context.scene.kz2_level_section = self.section_index
        return {"FINISHED"}


class KZ2_OT_import_level(Operator):
    """Import an entire cached map or one selected section."""

    bl_idname = "kz2.import_level"
    bl_label = "Import Level"
    bl_options: ClassVar[set[str]] = {"REGISTER", "UNDO"}

    section_index: IntProperty(  # type: ignore[valid-type]
        default=-1,
        min=-1,
        options={"HIDDEN"},
    )

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        catalog = _safe_current_catalog(context)
        return catalog is not None and bool(catalog.levels)

    def execute(self, context: bpy.types.Context):
        manager = context.window_manager
        manager.progress_begin(0, 100)
        try:
            catalog = _current_catalog(context)
            if catalog is None:
                raise FormatError(
                    "Refresh the configured map catalog before importing"
                )
            level = _selected_level(context, catalog)
            section_index = (
                None if self.section_index < 0 else self.section_index
            )
            options = ImportOptions(
                global_scale=context.scene.kz2_level_scale,
                flip_uv_v=context.scene.kz2_level_flip_uv_v,
                import_animations=False,
                import_materials=context.scene.kz2_level_materials,
                import_level_entities=context.scene.kz2_level_entities,
                include_skybox=context.scene.kz2_level_skybox,
                source_psarc=catalog.source_path,
                resource_index_path=catalog.resource_index_path,
            )
            result = import_psarc_level(
                context,
                catalog.source_path,
                level,
                section_index,
                options,
                progress=lambda current, total, _path: (
                    manager.progress_update(
                        100 * current / max(1, total)
                    )
                ),
            )
        except (Kz2AssetError, OSError, ValueError) as exc:
            _LOG.error("Level import failed: %s", exc)
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        finally:
            manager.progress_end()
        _report_import_result(self, result)
        return {"FINISHED"}


class KZ2_PT_tools(Panel):
    bl_label = "Killzone 2 Asset Importer"
    bl_idname = "KZ2_PT_asset_tools"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "KZ2 Tools"

    def draw(self, context: bpy.types.Context) -> None:
        layout = self.layout

        core_box = layout.box()
        core_box.label(text="Import Extracted CORE", icon="FILE")
        core_box.operator(
            IMPORT_SCENE_OT_killzone2_core.bl_idname,
            icon="IMPORT",
        )

        try:
            preferences = _addon_preferences(context)
            catalog = _catalog_from_preferences(preferences)
        except Kz2AssetError as exc:
            box = layout.box()
            box.alert = True
            box.label(text=str(exc), icon="ERROR")
            box.operator(KZ2_OT_open_preferences.bl_idname)
            return

        source_matches = False
        if catalog is not None and preferences.archive_source:
            try:
                source_matches = catalog.matches_source(
                    bpy.path.abspath(preferences.archive_source)
                )
            except OSError:
                source_matches = False
        if not source_matches:
            box = layout.box()
            box.label(
                text="Configure the PSARC and scan assets once.",
                icon="INFO",
            )
            box.label(text="First scan may take several minutes.")
            box.label(text="External USB drives can be slower.")
            row = box.row(align=True)
            row.operator(
                KZ2_OT_open_preferences.bl_idname,
                text="Settings",
                icon="PREFERENCES",
            )
            if preferences.archive_source:
                row.operator(
                    KZ2_OT_scan_entities.bl_idname,
                    text="Scan",
                    icon="FILE_REFRESH",
                )
            return

        header = layout.row(align=True)
        header.label(
            text=Path(catalog.source_path).name,
            icon="CHECKMARK",
        )
        header.operator(
            KZ2_OT_open_preferences.bl_idname,
            text="Settings",
            icon="PREFERENCES",
        )

        level_box = layout.box()
        level_box.label(text="Import Complete Level", icon="WORLD_DATA")
        if not catalog.levels:
            level_box.label(
                text="Map hierarchy is not cached yet.",
                icon="INFO",
            )
            level_box.operator(
                KZ2_OT_scan_levels.bl_idname,
                text="Index Maps (Fast)",
                icon="FILE_REFRESH",
            )
        else:
            level_box.prop(
                context.scene,
                "kz2_level_kind",
                text="",
                expand=True,
            )
            level_box.prop(context.scene, "kz2_level_asset", text="Map")
            try:
                level = _selected_level(context, catalog)
            except Kz2AssetError:
                level = None
            if level is not None:
                level_box.label(
                    text=(
                        f"{len(level.sections)} section"
                        f"{'' if len(level.sections) == 1 else 's'}"
                    ),
                    icon="OUTLINER_COLLECTION",
                )
                selected_section = min(
                    max(0, context.scene.kz2_level_section),
                    max(0, len(level.sections) - 1),
                )
                for section_index, section in enumerate(level.sections):
                    row = level_box.row(align=True)
                    select = row.operator(
                        KZ2_OT_select_level_section.bl_idname,
                        text=section.display_name,
                        icon=(
                            "RADIOBUT_ON"
                            if section_index == selected_section
                            else "RADIOBUT_OFF"
                        ),
                    )
                    select.section_index = section_index
                    row.label(
                        text=(
                            f"{len(section.core_paths)} CORE"
                            f"{'' if len(section.core_paths) == 1 else 's'}"
                        )
                    )
                level_box.separator()
                level_box.prop(context.scene, "kz2_level_materials")
                level_box.prop(context.scene, "kz2_level_entities")
                level_box.prop(context.scene, "kz2_level_skybox")
                level_box.prop(context.scene, "kz2_level_flip_uv_v")
                level_box.prop(context.scene, "kz2_level_scale")
                actions = level_box.row(align=True)
                whole = actions.operator(
                    KZ2_OT_import_level.bl_idname,
                    text="Import Entire Map",
                    icon="IMPORT",
                )
                whole.section_index = -1
                selected = actions.operator(
                    KZ2_OT_import_level.bl_idname,
                    text="Import Section",
                    icon="OUTLINER_COLLECTION",
                )
                selected.section_index = selected_section
                level_box.label(
                    text="Entire maps can take several minutes to import.",
                    icon="INFO",
                )

        entity_box = layout.box()
        entity_box.label(text="Import Cached Entity", icon="OBJECT_DATA")
        if not catalog.entities:
            entity_box.label(text="No importable entities were cached")
            return
        entity_box.prop(
            context.scene,
            "kz2_entity_category",
            text="Category",
        )
        entity_box.prop(
            context.scene,
            "kz2_entity_search",
            text="Search",
            icon="VIEWZOOM",
        )
        matching_indices = filter_cached_entity_indices(
            catalog.entities,
            context.scene.kz2_entity_category,
            context.scene.kz2_entity_search,
        )
        page_count = max(
            1,
            (
                len(matching_indices)
                + _ENTITY_PAGE_SIZE
                - 1
            )
            // _ENTITY_PAGE_SIZE,
        )
        page = min(
            max(1, context.scene.kz2_entity_page),
            page_count,
        )
        entity_box.label(
            text=(
                f"{len(matching_indices)} matching "
                f"{len(catalog.entities)} cached entities"
            ),
            icon="FILTER",
        )
        if matching_indices:
            start = (page - 1) * _ENTITY_PAGE_SIZE
            selected_identifier = context.scene.kz2_entity_asset
            for entity_index in matching_indices[
                start : start + _ENTITY_PAGE_SIZE
            ]:
                listed_entity = catalog.entities[entity_index]
                is_selected = selected_identifier == str(entity_index)
                row = entity_box.row(align=True)
                select = row.operator(
                    KZ2_OT_select_cached_entity.bl_idname,
                    text=(
                        f"{listed_entity.resource_name} "
                        f"[{listed_entity.object_index}]"
                    ),
                    icon=(
                        "RADIOBUT_ON"
                        if is_selected
                        else "RADIOBUT_OFF"
                    ),
                )
                select.entity_index = entity_index
                row.label(
                    text=cached_entity_source_label(listed_entity)
                )

            navigation = entity_box.row(align=True)
            first = navigation.row(align=True)
            first.enabled = page > 1
            first.operator(
                KZ2_OT_set_entity_page.bl_idname,
                text="",
                icon="REW",
            ).page = 1
            previous = navigation.row(align=True)
            previous.enabled = page > 1
            previous.operator(
                KZ2_OT_set_entity_page.bl_idname,
                text="",
                icon="TRIA_LEFT",
            ).page = max(1, page - 1)
            navigation.label(text=f"Page {page} / {page_count}")
            following = navigation.row(align=True)
            following.enabled = page < page_count
            following.operator(
                KZ2_OT_set_entity_page.bl_idname,
                text="",
                icon="TRIA_RIGHT",
            ).page = min(page_count, page + 1)
            last = navigation.row(align=True)
            last.enabled = page < page_count
            last.operator(
                KZ2_OT_set_entity_page.bl_idname,
                text="",
                icon="FF",
            ).page = page_count
        else:
            entity_box.label(
                text="No entities match these filters",
                icon="INFO",
            )
        try:
            entity = _selected_entity(context, catalog)
        except Kz2AssetError:
            entity = None
        if entity is not None:
            entity_box.separator()
            entity_box.label(
                text=f"Selected: {entity.resource_name}",
                icon="CHECKMARK",
            )
            entity_box.label(
                text=Path(entity.path).name,
                icon="FILE",
            )
            render_sources = []
            if entity.has_model:
                render_sources.append("model")
            if entity.has_destructibility:
                render_sources.append("destructibility")
            entity_box.label(
                text=f"Render source: {' + '.join(render_sources)}",
                icon="MESH_DATA",
            )
        entity_box.prop(context.scene, "kz2_entity_materials")
        entity_box.prop(context.scene, "kz2_entity_animations")
        entity_box.prop(context.scene, "kz2_entity_flip_uv_v")
        entity_box.prop(context.scene, "kz2_entity_scale")
        entity_box.operator(
            KZ2_OT_import_cached_entity.bl_idname,
            icon="IMPORT",
        )
        if catalog.issues:
            warning = layout.row()
            warning.alert = True
            warning.label(
                text=f"{len(catalog.issues)} scan issue(s)",
                icon="ERROR",
            )


def _menu_import(self, context: bpy.types.Context) -> None:
    self.layout.operator(
        IMPORT_SCENE_OT_killzone2_core.bl_idname,
        text="Killzone 2 CORE (.core)",
    )


_CLASSES = (
    KZ2_AddonPreferences,
    KZ2_OT_scan_entities,
    KZ2_OT_scan_levels,
    KZ2_OT_open_preferences,
    IMPORT_SCENE_OT_killzone2_core,
    KZ2_OT_import_cached_entity,
    KZ2_OT_select_cached_entity,
    KZ2_OT_set_entity_page,
    KZ2_OT_select_level_section,
    KZ2_OT_import_level,
    KZ2_PT_tools,
)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.kz2_level_kind = EnumProperty(
        name="Map Group",
        description="Choose campaign levels or multiplayer maps",
        items=LEVEL_KIND_ITEMS,
        default=LEVEL_KIND_CAMPAIGN,
        update=_reset_level_map_selection,
    )
    bpy.types.Scene.kz2_level_asset = EnumProperty(
        name="Map",
        description="Campaign level or multiplayer map to import",
        items=_level_asset_items,
        update=_reset_level_section_selection,
    )
    bpy.types.Scene.kz2_level_section = IntProperty(
        name="Level Section",
        description="Index of the selected section within the cached map",
        default=0,
        min=0,
        options={"HIDDEN"},
    )
    bpy.types.Scene.kz2_level_materials = BoolProperty(
        name="Materials and Textures",
        description="Decode textures and create preview material graphs",
        default=True,
    )
    bpy.types.Scene.kz2_level_entities = BoolProperty(
        name="Entity Props",
        description=(
            "Import mesh-backed EntityPlaceHolder props in addition to "
            "direct static environment placements"
        ),
        default=True,
    )
    bpy.types.Scene.kz2_level_skybox = BoolProperty(
        name="Skybox Geometry",
        description="Include dedicated skybox CORE geometry when present",
        default=True,
    )
    bpy.types.Scene.kz2_level_flip_uv_v = BoolProperty(
        name="Flip UV V",
        description=(
            "Convert Killzone's top-origin V coordinate to Blender's UV "
            "convention"
        ),
        default=True,
    )
    bpy.types.Scene.kz2_level_scale = FloatProperty(
        name="Scale",
        description="Uniform scale applied to imported map geometry",
        default=1.0,
        min=1e-6,
        soft_max=100.0,
    )
    bpy.types.Scene.kz2_entity_asset = StringProperty(
        name="Selected Entity",
        description="Index of the selected cached render-backed entity",
        default="0",
        options={"HIDDEN"},
    )
    bpy.types.Scene.kz2_entity_page = IntProperty(
        name="Entity Browser Page",
        default=1,
        min=1,
        options={"HIDDEN"},
    )
    bpy.types.Scene.kz2_entity_category = EnumProperty(
        name="Asset Category",
        description="Limit the cached entity browser by asset type",
        items=ENTITY_CATEGORY_ITEMS,
        default=ENTITY_CATEGORY_ALL,
        update=_reset_entity_browser_page,
    )
    bpy.types.Scene.kz2_entity_search = StringProperty(
        name="Entity Search",
        description="Filter cached entities by name or source CORE path",
        default="",
        update=_reset_entity_browser_page,
    )
    bpy.types.Scene.kz2_entity_materials = BoolProperty(
        name="Materials and Textures",
        description="Decode textures and create preview material graphs",
        default=True,
    )
    bpy.types.Scene.kz2_entity_animations = BoolProperty(
        name="Animations",
        description=(
            "Decode compatible EA02 clips into persistent Blender Actions"
        ),
        default=True,
    )
    bpy.types.Scene.kz2_entity_flip_uv_v = BoolProperty(
        name="Flip UV V",
        description=(
            "Convert Killzone's top-origin V coordinate to Blender's UV convention"
        ),
        default=True,
    )
    bpy.types.Scene.kz2_entity_scale = FloatProperty(
        name="Scale",
        description="Uniform scale applied to imported entity geometry",
        default=1.0,
        min=1e-6,
        soft_max=100.0,
    )
    bpy.types.TOPBAR_MT_file_import.append(_menu_import)


def unregister() -> None:
    bpy.types.TOPBAR_MT_file_import.remove(_menu_import)
    del bpy.types.Scene.kz2_entity_scale
    del bpy.types.Scene.kz2_entity_flip_uv_v
    del bpy.types.Scene.kz2_entity_animations
    del bpy.types.Scene.kz2_entity_materials
    del bpy.types.Scene.kz2_entity_page
    del bpy.types.Scene.kz2_entity_search
    del bpy.types.Scene.kz2_entity_category
    del bpy.types.Scene.kz2_entity_asset
    del bpy.types.Scene.kz2_level_scale
    del bpy.types.Scene.kz2_level_flip_uv_v
    del bpy.types.Scene.kz2_level_skybox
    del bpy.types.Scene.kz2_level_entities
    del bpy.types.Scene.kz2_level_materials
    del bpy.types.Scene.kz2_level_section
    del bpy.types.Scene.kz2_level_asset
    del bpy.types.Scene.kz2_level_kind
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
    _invalidate_runtime_cache()
