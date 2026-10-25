from __future__ import annotations

import hashlib
import json
import struct
import tempfile
import unittest
import zlib
from pathlib import Path

from io_scene_killzone2.kz2_assets.asset_catalog import (
    ENTITY_CATEGORY_CHARACTERS,
    ENTITY_CATEGORY_MAPS,
    ENTITY_CATEGORY_PROPS,
    ENTITY_CATEGORY_VEHICLES,
    ENTITY_CATEGORY_WEAPONS,
    CachedEntity,
    EntityAssetCatalog,
    cached_entity_category,
    cached_entity_source_label,
    filter_cached_entity_indices,
    scan_entity_asset_catalog,
)
from io_scene_killzone2.kz2_assets.core import CORE_SIGNATURE
from io_scene_killzone2.kz2_assets.errors import FormatError
from io_scene_killzone2.kz2_assets.level_catalog import (
    LEVEL_KIND_CAMPAIGN,
    CachedLevel,
    CachedLevelSection,
)
from io_scene_killzone2.kz2_assets.model import EntityResource
from io_scene_killzone2.kz2_assets.resolver import PsarcResourceResolver
from io_scene_killzone2.kz2_assets.resource_index import ResourceIndex


def compact(value: int) -> bytes:
    if -128 <= value <= 127 and value not in (-128, -127):
        return struct.pack(">b", value)
    if -32768 <= value <= 32767:
        return b"\x81" + struct.pack(">h", value)
    return b"\x80" + struct.pack(">i", value)


def external_reference(value: bytes) -> bytes:
    return b"\x02" + compact(len(value)) + value


def synthetic_entity_core() -> bytes:
    types = (b"EntityResource", b"String")
    resource_name = b"CachedEntity"
    payload = (
        b"\x00\x01"
        + b"\x00\x00"
        + b"\x00"
        + b"\x00\x00"
        + external_reference(b"model-id")
        + b"\x00\x00"
    )

    data = bytearray(CORE_SIGNATURE)
    data.append(1)
    data.extend(bytes(16))
    data.extend(compact(len(types)))
    for type_name in types:
        data.extend(compact(len(type_name)))
        data.extend(type_name)
    data.extend(compact(1))
    data.append(0)
    data.extend(bytes.fromhex("00112233445566778899aabbccddeeff"))
    data.extend(struct.pack(">I", len(payload)))
    data.extend(bytes(12))
    data.extend(compact(0))
    data.extend(compact(1))
    data.append(1)
    data.extend(compact(1))
    data.extend(compact(1))
    data.append(len(resource_name))
    data.extend(resource_name)
    data.extend(compact(0))
    data.extend(compact(0))
    data.extend(compact(0))
    data.extend(compact(0))
    data.extend(payload)
    return bytes(data)


def u40(value: int) -> bytes:
    return value.to_bytes(5, "big")


def build_archive(path: Path, logical_path: str, payload: bytes) -> None:
    manifest = (logical_path + "\n").encode()
    block_size = 256
    entries_data: list[list[bytes]] = []
    block_lengths: list[int] = []
    for content in (manifest, payload):
        stored_blocks = []
        for start in range(0, len(content), block_size):
            raw = content[start : start + block_size]
            compressed = zlib.compress(raw, level=9)
            stored = compressed if len(compressed) < len(raw) else raw
            stored_blocks.append(stored)
            block_lengths.append(
                0 if len(stored) == block_size else len(stored)
            )
        entries_data.append(stored_blocks)

    entry_count = 2
    toc_length = 0x20 + entry_count * 30 + len(block_lengths)
    data_offsets = []
    cursor = toc_length
    for blocks in entries_data:
        data_offsets.append(cursor)
        cursor += sum(len(block) for block in blocks)

    header = (
        b"PSAR"
        + struct.pack(">I", 0x00010003)
        + b"zlib"
        + struct.pack(">IIIII", toc_length, 30, entry_count, block_size, 1)
    )
    toc = bytearray()
    toc.extend(bytes(16))
    toc.extend(struct.pack(">I", 0))
    toc.extend(u40(len(manifest)))
    toc.extend(u40(data_offsets[0]))
    toc.extend(hashlib.md5(logical_path.upper().encode()).digest())
    toc.extend(struct.pack(">I", len(entries_data[0])))
    toc.extend(u40(len(payload)))
    toc.extend(u40(data_offsets[1]))

    with path.open("wb") as stream:
        stream.write(header)
        stream.write(toc)
        stream.write(bytes(block_lengths))
        for blocks in entries_data:
            for block in blocks:
                stream.write(block)


class EntityAssetCatalogTests(unittest.TestCase):
    def test_scans_render_backed_entities_and_round_trips_json(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "test.psarc"
            index_path = Path(temporary) / "resources.sqlite3"
            logical_path = "LocalCachePS3/lumps/cached_entity.core"
            build_archive(path, logical_path, synthetic_entity_core())

            catalog = scan_entity_asset_catalog(
                path,
                resource_index_path=index_path,
            )

            self.assertEqual(str(path.resolve()), catalog.source_path)
            self.assertEqual(
                str(index_path.resolve()),
                catalog.resource_index_path,
            )
            self.assertEqual(1, len(catalog.entities))
            entity = catalog.entities[0]
            self.assertEqual("CachedEntity", entity.display_name)
            self.assertEqual("CachedEntity", entity.resource_name)
            self.assertEqual(logical_path, entity.path)
            self.assertEqual(0, entity.object_index)
            self.assertTrue(entity.has_model)
            self.assertFalse(entity.has_destructibility)
            self.assertEqual((), catalog.issues)
            self.assertTrue(catalog.matches_source(path))
            self.assertEqual(
                catalog,
                EntityAssetCatalog.from_json(catalog.to_json()),
            )
            resource_index = ResourceIndex(
                index_path,
                source_path=path,
                source_size=catalog.source_size,
                source_mtime_ns=catalog.source_mtime_ns,
            )
            definitions = resource_index.definitions({entity.object_id})
            self.assertEqual(1, len(definitions))
            self.assertEqual(logical_path, definitions[0].path)
            self.assertEqual("EntityResource", definitions[0].type_name)
            named = resource_index.named_definitions(
                {("EntityResource", "CachedEntity")}
            )
            self.assertEqual(1, len(named))
            self.assertEqual("CachedEntity", named[0].resource_name)
            resolver = PsarcResourceResolver(
                path,
                {entity.object_id},
                resource_index_path=index_path,
            )
            resolved = resolver.resolve(
                entity.object_id,
                expected_type="EntityResource",
            )
            self.assertIsNotNone(resolved)
            self.assertIsInstance(resolved.value, EntityResource)
            named_resolver = PsarcResourceResolver(
                path,
                set(),
                requested_names={("EntityResource", "CachedEntity")},
                resource_index_path=index_path,
            )
            named_resolved = named_resolver.resolve_named(
                "EntityResource",
                "CachedEntity",
            )
            self.assertIsNotNone(named_resolved)
            self.assertIsInstance(named_resolved.value, EntityResource)
            with path.open("ab") as stream:
                stream.write(b"\x00")
            self.assertFalse(catalog.matches_source(path))

    def test_rejects_unknown_cache_version(self) -> None:
        with self.assertRaisesRegex(
            FormatError,
            "cache version is unsupported",
        ):
            EntityAssetCatalog.from_json('{"cache_version":999}')

    def test_round_trips_level_hierarchy_and_loads_v2_cache(self) -> None:
        catalog = EntityAssetCatalog(
            source_path=r"C:\retail\data_ps3.psarc",
            source_size=123,
            source_mtime_ns=456,
            resource_index_path=r"C:\cache\resources.sqlite3",
            entities=(),
            levels=(
                CachedLevel(
                    key="campaign:corinth_river",
                    kind=LEVEL_KIND_CAMPAIGN,
                    display_name="Corinth River",
                    root_path=(
                        "LocalCachePS3/lumps/Levels.Single_Player."
                        "Optimized.Corinth_River_Optimized.core"
                    ),
                    sections=(
                        CachedLevelSection(
                            key="section_thenewsun",
                            display_name="The New Sun",
                            core_paths=(
                                (
                                    "LocalCachePS3/lumps/levels.single_player."
                                    "corinth_river.section_thenewsun.cutscenes."
                                    "assets_optimized_skybox.core"
                                ),
                            ),
                        ),
                    ),
                ),
            ),
            issues=(),
        )

        self.assertEqual(
            catalog,
            EntityAssetCatalog.from_json(catalog.to_json()),
        )

        version_two = json.loads(catalog.to_json())
        version_two["cache_version"] = 2
        version_two.pop("levels")
        migrated = EntityAssetCatalog.from_json(json.dumps(version_two))
        self.assertEqual((), migrated.levels)
        self.assertEqual(catalog.source_path, migrated.source_path)

    def test_categorizes_and_filters_cached_entities(self) -> None:
        def entity(name: str, path: str) -> CachedEntity:
            return CachedEntity(
                path=path,
                display_name=name,
                resource_name=name,
                resource_type="EntityResource",
                object_index=1,
                object_id="00" * 16,
                has_model=True,
                has_destructibility=False,
            )

        entities = (
            entity(
                "hgh_assault_01",
                "LocalCachePS3/lumps/assets_description.characters.core",
            ),
            entity(
                "AAPC_Wreck_Blade",
                "LocalCachePS3/lumps/levels.single_player.salamun_district."
                "section_bridge.core",
            ),
            entity(
                "ISA_Assault_Rifle",
                "LocalCachePS3/lumps/assets_description.weapons.core",
            ),
            entity(
                "A4_Clipboard",
                "LocalCachePS3/lumps/levels.single_player.cruiser.core",
            ),
            entity(
                "Bridge_Main",
                "LocalCachePS3/lumps/levels.single_player.bridge.core",
            ),
        )

        self.assertEqual(
            (
                ENTITY_CATEGORY_CHARACTERS,
                ENTITY_CATEGORY_VEHICLES,
                ENTITY_CATEGORY_WEAPONS,
                ENTITY_CATEGORY_PROPS,
                ENTITY_CATEGORY_MAPS,
            ),
            tuple(cached_entity_category(value) for value in entities),
        )
        self.assertEqual(
            (1,),
            filter_cached_entity_indices(
                entities,
                ENTITY_CATEGORY_VEHICLES,
                "wreck blade",
            ),
        )
        self.assertEqual(
            (0,),
            filter_cached_entity_indices(
                entities,
                query="characters assault",
            ),
        )
        self.assertEqual(
            "salamun district / bridge",
            cached_entity_source_label(entities[1]),
        )


if __name__ == "__main__":
    unittest.main()
