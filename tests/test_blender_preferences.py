from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

try:
    import bpy
except ModuleNotFoundError:
    bpy = None

from io_scene_killzone2.kz2_assets.asset_catalog import (
    EntityAssetCatalog,
    scan_entity_asset_catalog,
)
from tests.test_asset_catalog import (
    build_archive,
    synthetic_entity_core,
)


@unittest.skipUnless(bpy is not None, "requires Blender's bpy module")
class BlenderPreferencePersistenceTests(unittest.TestCase):
    def test_bundled_cache_persists_across_registration_and_load(self) -> None:
        import io_scene_killzone2
        from io_scene_killzone2 import operators

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_path = root / "source.psarc"
            index_path = root / "bundled-index.sqlite3"
            catalog_path = root / "bundled-catalog.json"
            build_archive(
                source_path,
                "LocalCachePS3/lumps/cached_entity.core",
                synthetic_entity_core(),
            )
            catalog = scan_entity_asset_catalog(
                source_path,
                resource_index_path=index_path,
            )
            catalog_path.write_text(catalog.to_json(), encoding="utf-8")

            original_root = operators._persistent_cache_root
            original_catalog_path = operators._BUNDLED_CATALOG_PATH
            original_index_path = operators._BUNDLED_RESOURCE_INDEX_PATH
            original_reset = operators._reset_scene_selections
            operators._persistent_cache_root = lambda: root / "user"
            operators._BUNDLED_CATALOG_PATH = catalog_path
            operators._BUNDLED_RESOURCE_INDEX_PATH = index_path
            addon = None
            registered = False
            try:
                addon = bpy.context.preferences.addons.new()
                addon.module = "io_scene_killzone2"
                io_scene_killzone2.register()
                registered = True
                preferences = addon.preferences
                self.assertFalse(preferences.import_fake_shadow_meshes)
                preferences.archive_source = str(source_path)

                activated = EntityAssetCatalog.from_json(
                    preferences.entity_cache_json
                )
                self.assertEqual(
                    catalog.source_fingerprint,
                    activated.source_fingerprint,
                )
                self.assertEqual(1, len(activated.entities))
                state_source, state_catalog = (
                    operators._load_persistent_state()
                )
                self.assertEqual(str(source_path.resolve()), state_source)
                self.assertIsNotNone(state_catalog)

                io_scene_killzone2.unregister()
                registered = False
                bpy.context.preferences.addons.remove(addon)
                addon = bpy.context.preferences.addons.new()
                addon.module = "io_scene_killzone2"
                operators._reset_scene_selections = lambda _catalog: (
                    self.fail(
                        "registration must not access scene data while "
                        "Blender data is restricted"
                    )
                )
                try:
                    io_scene_killzone2.register()
                finally:
                    operators._reset_scene_selections = original_reset
                registered = True
                preferences = addon.preferences
                self.assertFalse(preferences.import_fake_shadow_meshes)
                restored = EntityAssetCatalog.from_json(
                    preferences.entity_cache_json
                )
                self.assertEqual(
                    str(source_path.resolve()),
                    preferences.archive_source,
                )
                self.assertEqual(1, len(restored.entities))

                operators._assign_preferences(preferences, "", None)
                operators._restore_persistent_preferences_after_load("")
                after_load = EntityAssetCatalog.from_json(
                    preferences.entity_cache_json
                )
                self.assertEqual(
                    str(source_path.resolve()),
                    preferences.archive_source,
                )
                self.assertEqual(1, len(after_load.entities))
            finally:
                if registered:
                    io_scene_killzone2.unregister()
                if (
                    addon is not None
                    and bpy.context.preferences.addons.get(
                        "io_scene_killzone2"
                    )
                    is not None
                ):
                    bpy.context.preferences.addons.remove(addon)
                operators._persistent_cache_root = original_root
                operators._BUNDLED_CATALOG_PATH = original_catalog_path
                operators._BUNDLED_RESOURCE_INDEX_PATH = original_index_path
                operators._reset_scene_selections = original_reset


if __name__ == "__main__":
    unittest.main()
