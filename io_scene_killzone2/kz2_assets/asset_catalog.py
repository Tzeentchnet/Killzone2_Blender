"""Persistent catalog of render-backed entities stored in a KZ2 PSARC."""

from __future__ import annotations

import json
import logging
import os
import re
import time
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import Any

from .core import CoreFile
from .decoder import SUPPORTED_OBJECT_TYPES, CoreDecoder
from .errors import FormatError, Kz2AssetError
from .level_catalog import (
    CachedLevel,
    CachedLevelSection,
    scan_level_catalog,
)
from .model import EntityResource
from .psarc import Psarc
from .resolver import read_core_metadata
from .resource_index import ResourceIndexWriter

ENTITY_ASSET_CACHE_VERSION = 3
_SUPPORTED_CACHE_VERSIONS = frozenset({2, ENTITY_ASSET_CACHE_VERSION})
ENTITY_CATEGORY_ALL = "ALL"
ENTITY_CATEGORY_MAPS = "MAPS"
ENTITY_CATEGORY_CHARACTERS = "CHARACTERS"
ENTITY_CATEGORY_VEHICLES = "VEHICLES"
ENTITY_CATEGORY_WEAPONS = "WEAPONS"
ENTITY_CATEGORY_PROPS = "PROPS"
ENTITY_CATEGORY_OTHER = "OTHER"
ENTITY_CATEGORY_ITEMS = (
    (ENTITY_CATEGORY_ALL, "All Assets", "Show every cached entity"),
    (
        ENTITY_CATEGORY_MAPS,
        "Maps / Environment",
        "Show level and environmental assets",
    ),
    (
        ENTITY_CATEGORY_CHARACTERS,
        "Characters",
        "Show characters and infantry",
    ),
    (ENTITY_CATEGORY_VEHICLES, "Vehicles", "Show vehicles and vehicle parts"),
    (ENTITY_CATEGORY_WEAPONS, "Weapons", "Show weapons and ordnance"),
    (ENTITY_CATEGORY_PROPS, "Props", "Show furniture, containers, and props"),
    (ENTITY_CATEGORY_OTHER, "Other", "Show uncategorized entities"),
)
_NATURAL_PART = re.compile(r"(\d+)")
_ASSET_WORD = re.compile(r"[a-z0-9]+")
_LOG = logging.getLogger(__name__)
_CHARACTER_WORDS = frozenset(
    {
        "assault",
        "character",
        "evelyn",
        "garza",
        "infantry",
        "natko",
        "narville",
        "radec",
        "rico",
        "sev",
        "soldier",
        "trooper",
        "visari",
    }
)
_VEHICLE_WORDS = frozenset(
    {
        "aapc",
        "apc",
        "atac",
        "buggy",
        "dropship",
        "intruder",
        "jeep",
        "tank",
        "vehicle",
    }
)
_WEAPON_WORDS = frozenset(
    {
        "cannon",
        "flamethrower",
        "grenade",
        "launcher",
        "lmg",
        "minigun",
        "mortar",
        "pistol",
        "rifle",
        "shotgun",
        "smg",
        "weapon",
    }
)
_PROP_WORDS = frozenset(
    {
        "bag",
        "barricade",
        "barrel",
        "bench",
        "blade",
        "bottle",
        "box",
        "cable",
        "chair",
        "clipboard",
        "crate",
        "debris",
        "door",
        "dumpster",
        "filter",
        "furniture",
        "hatch",
        "light",
        "locker",
        "panel",
        "paper",
        "pipe",
        "prop",
        "table",
    }
)
_MAP_WORDS = frozenset(
    {
        "bridge",
        "building",
        "ceiling",
        "environment",
        "floor",
        "level",
        "map",
        "road",
        "roof",
        "terrain",
        "wall",
    }
)


@dataclass(frozen=True, slots=True)
class CachedEntity:
    path: str
    display_name: str
    resource_name: str
    resource_type: str
    object_index: int
    object_id: str
    has_model: bool
    has_destructibility: bool


@dataclass(frozen=True, slots=True)
class EntityAssetScanIssue:
    path: str
    message: str


@dataclass(frozen=True, slots=True)
class EntityAssetCatalog:
    source_path: str
    source_size: int
    source_mtime_ns: int
    resource_index_path: str
    entities: tuple[CachedEntity, ...]
    levels: tuple[CachedLevel, ...]
    issues: tuple[EntityAssetScanIssue, ...]

    def to_json(self) -> str:
        document = {
            "cache_version": ENTITY_ASSET_CACHE_VERSION,
            "source_path": self.source_path,
            "source_size": self.source_size,
            "source_mtime_ns": self.source_mtime_ns,
            "resource_index_path": self.resource_index_path,
            "entities": [asdict(value) for value in self.entities],
            "levels": [asdict(value) for value in self.levels],
            "issues": [asdict(value) for value in self.issues],
        }
        return json.dumps(
            document,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )

    @classmethod
    def from_json(cls, value: str) -> EntityAssetCatalog:
        try:
            document = json.loads(value)
        except json.JSONDecodeError as exc:
            raise FormatError("Entity asset cache is not valid JSON") from exc
        if not isinstance(document, Mapping):
            raise FormatError("Entity asset cache root must be an object")
        cache_version = document.get("cache_version")
        if cache_version not in _SUPPORTED_CACHE_VERSIONS:
            raise FormatError(
                "Entity asset cache version is unsupported; rescan the PSARC"
            )

        source_path = _required_string(document, "source_path")
        source_size = _required_int(document, "source_size")
        source_mtime_ns = _required_int(document, "source_mtime_ns")
        resource_index_path = _required_string(
            document,
            "resource_index_path",
        )
        entities = tuple(
            CachedEntity(
                path=_required_string(item, "path"),
                display_name=_required_string(item, "display_name"),
                resource_name=_required_string(item, "resource_name"),
                resource_type=_required_string(item, "resource_type"),
                object_index=_required_int(item, "object_index"),
                object_id=_required_string(item, "object_id"),
                has_model=_required_bool(item, "has_model"),
                has_destructibility=_required_bool(
                    item,
                    "has_destructibility",
                ),
            )
            for item in _required_records(document, "entities")
        )
        levels = (
            tuple(
                CachedLevel(
                    key=_required_string(item, "key"),
                    kind=_required_string(item, "kind"),
                    display_name=_required_string(item, "display_name"),
                    root_path=_required_string(item, "root_path"),
                    sections=tuple(
                        CachedLevelSection(
                            key=_required_string(section, "key"),
                            display_name=_required_string(
                                section,
                                "display_name",
                            ),
                            core_paths=_required_strings(
                                section,
                                "core_paths",
                            ),
                        )
                        for section in _required_records(item, "sections")
                    ),
                )
                for item in _required_records(document, "levels")
            )
            if cache_version >= 3
            else ()
        )
        issues = tuple(
            EntityAssetScanIssue(
                path=_required_string(item, "path"),
                message=_required_string(item, "message"),
            )
            for item in _required_records(document, "issues")
        )
        return cls(
            source_path=source_path,
            source_size=source_size,
            source_mtime_ns=source_mtime_ns,
            resource_index_path=resource_index_path,
            entities=entities,
            levels=levels,
            issues=issues,
        )

    def matches_source(self, source_path: str | Path) -> bool:
        resolved = Path(source_path).expanduser().resolve()
        if os.path.normcase(str(resolved)) != os.path.normcase(
            self.source_path
        ):
            return False
        source_stat = resolved.stat()
        source_matches = (
            source_stat.st_size == self.source_size
            and source_stat.st_mtime_ns == self.source_mtime_ns
        )
        return source_matches and (
            not self.resource_index_path
            or Path(self.resource_index_path).is_file()
        )


@lru_cache(maxsize=32_768)
def cached_entity_category(entity: CachedEntity) -> str:
    path = entity.path.casefold()
    words = frozenset(_ASSET_WORD.findall(entity.resource_name.casefold()))
    if ".characters." in path:
        return ENTITY_CATEGORY_CHARACTERS
    if ".vehicles." in path:
        return ENTITY_CATEGORY_VEHICLES
    if ".weapons." in path:
        return ENTITY_CATEGORY_WEAPONS
    if ".props." in path:
        return ENTITY_CATEGORY_PROPS
    if words & _VEHICLE_WORDS:
        return ENTITY_CATEGORY_VEHICLES
    if words & _WEAPON_WORDS:
        return ENTITY_CATEGORY_WEAPONS
    if words & _CHARACTER_WORDS:
        return ENTITY_CATEGORY_CHARACTERS
    if words & _PROP_WORDS:
        return ENTITY_CATEGORY_PROPS
    if "/levels." in path or words & _MAP_WORDS:
        return ENTITY_CATEGORY_MAPS
    return ENTITY_CATEGORY_OTHER


@lru_cache(maxsize=32_768)
def cached_entity_source_label(entity: CachedEntity) -> str:
    stem = PurePosixPath(entity.path).stem
    parts = stem.split(".")
    if (
        len(parts) >= 4
        and parts[0].casefold() == "levels"
        and parts[1].casefold() == "single_player"
    ):
        level = parts[2].replace("_", " ")
        section = parts[3]
        if section.casefold().startswith("section_"):
            section = section[len("section_") :]
        return f"{level} / {section.replace('_', ' ')}"
    if (
        len(parts) >= 2
        and parts[0].casefold() == "assets_description"
    ):
        return f"shared / {parts[1].replace('_', ' ')}"
    if len(stem) <= 36:
        return stem
    return f"{stem[:33]}..."


def filter_cached_entity_indices(
    entities: Sequence[CachedEntity],
    category: str = ENTITY_CATEGORY_ALL,
    query: str = "",
) -> tuple[int, ...]:
    search_terms = tuple(
        term for term in query.casefold().split() if term
    )
    matches = []
    for index, entity in enumerate(entities):
        if (
            category != ENTITY_CATEGORY_ALL
            and cached_entity_category(entity) != category
        ):
            continue
        haystack = _cached_entity_search_text(entity)
        if all(term in haystack for term in search_terms):
            matches.append(index)
    return tuple(matches)


@lru_cache(maxsize=32_768)
def _cached_entity_search_text(entity: CachedEntity) -> str:
    return (
        f"{entity.resource_name}\n{entity.display_name}\n{entity.path}"
    ).casefold()


def scan_entity_asset_catalog(
    source_path: str | Path,
    *,
    resource_index_path: str | Path | None = None,
) -> EntityAssetCatalog:
    resolved = Path(source_path).expanduser().resolve()
    started = time.perf_counter()
    _LOG.info("Opening PSARC for entity indexing: %s", resolved)
    initial_stat = resolved.stat()
    archive = Psarc.open(resolved)
    entities: list[CachedEntity] = []
    issues: list[EntityAssetScanIssue] = []
    seen: set[tuple[int, int, int]] = set()
    index_path = (
        ""
        if resource_index_path is None
        else str(Path(resource_index_path).expanduser().resolve())
    )
    resource_writer = (
        ResourceIndexWriter(
            index_path,
            source_path=resolved,
            source_size=initial_stat.st_size,
            source_mtime_ns=initial_stat.st_mtime_ns,
            supported_types=SUPPORTED_OBJECT_TYPES,
        )
        if index_path
        else None
    )
    core_entries = []
    for entry in archive.entries:
        path = entry.path
        if (
            entry.physical_key in seen
            or not path
            or not path.casefold().endswith(".core")
        ):
            continue
        seen.add(entry.physical_key)
        core_entries.append(entry)

    _LOG.info(
        "Indexing %d unique CORE entries from %d PSARC records",
        len(core_entries),
        len(archive.entries),
    )
    for core_index, entry in enumerate(core_entries, 1):
        path = entry.path
        if core_index == 1 or core_index % 250 == 0:
            _LOG.info(
                "Entity index progress: %d/%d CORE entries, %d entities found",
                core_index,
                len(core_entries),
                len(entities),
            )
        try:
            metadata = read_core_metadata(archive, entry)
        except Kz2AssetError as exc:
            issues.append(EntityAssetScanIssue(path, str(exc)))
            continue
        if resource_writer is not None:
            resource_writer.add_core(path, metadata.objects)
        if not any(
            obj.type_name == "EntityResource" for obj in metadata.objects
        ):
            continue

        try:
            core = CoreFile.from_bytes(
                archive.read_entry(entry),
                source=path,
            )
        except Kz2AssetError as exc:
            issues.append(EntityAssetScanIssue(path, str(exc)))
            continue
        decoder = CoreDecoder(core)
        for record in core.objects:
            if record.type_name != "EntityResource":
                continue
            try:
                entity = decoder.decode(record)
            except Kz2AssetError as exc:
                issues.append(
                    EntityAssetScanIssue(
                        path,
                        f"Entity object {record.index}: {exc}",
                    )
                )
                continue
            if not isinstance(entity, EntityResource):
                continue
            has_model = not entity.model.is_null
            has_destructibility = not entity.destructibility.is_null
            if not has_model and not has_destructibility:
                continue
            resource_name = (
                entity.prefix.name
                or f"EntityResource_{record.index:05d}"
            )
            entities.append(
                CachedEntity(
                    path=path,
                    display_name=resource_name,
                    resource_name=resource_name,
                    resource_type=record.type_name,
                    object_index=record.index,
                    object_id=record.display_id,
                    has_model=has_model,
                    has_destructibility=has_destructibility,
                )
            )
            if resource_writer is not None:
                resource_writer.add_named_definition(
                    path=path,
                    type_name=record.type_name,
                    resource_name=resource_name,
                    object_index=record.index,
                    opaque_id=record.opaque_id,
                )

    try:
        levels = scan_level_catalog(archive)
    except Kz2AssetError:
        if resource_writer is not None:
            resource_writer.abort()
        raise

    final_stat = resolved.stat()
    if (
        initial_stat.st_size != final_stat.st_size
        or initial_stat.st_mtime_ns != final_stat.st_mtime_ns
    ):
        if resource_writer is not None:
            resource_writer.abort()
        raise FormatError(
            "PSARC changed while its entity catalog was being scanned",
            source=str(resolved),
        )
    if resource_writer is not None:
        resource_writer.finish()

    name_counts = Counter(
        entity.resource_name.casefold() for entity in entities
    )
    labeled_entities = tuple(
        replace(
            entity,
            display_name=_entity_display_name(entity, name_counts),
        )
        for entity in sorted(
            entities,
            key=lambda value: (
                _natural_key(value.resource_name),
                _natural_key(value.path),
                value.object_index,
            ),
        )
    )
    catalog = EntityAssetCatalog(
        source_path=str(resolved),
        source_size=final_stat.st_size,
        source_mtime_ns=final_stat.st_mtime_ns,
        resource_index_path=index_path,
        entities=labeled_entities,
        levels=levels,
        issues=tuple(
            sorted(
                issues,
                key=lambda value: (
                    _natural_key(value.path),
                    value.message.casefold(),
                ),
            )
        ),
    )
    _LOG.info(
        "Asset indexing complete in %.1fs: %d entities, %d levels, %d issues",
        time.perf_counter() - started,
        len(catalog.entities),
        len(catalog.levels),
        len(catalog.issues),
    )
    return catalog


def _entity_display_name(
    entity: CachedEntity,
    name_counts: Counter[str],
) -> str:
    if name_counts[entity.resource_name.casefold()] == 1:
        return entity.resource_name
    source_name = PurePosixPath(entity.path).stem
    return f"{entity.resource_name} - {source_name} [{entity.object_index}]"


def _natural_key(value: str) -> tuple[tuple[int, int | str], ...]:
    return tuple(
        (0, int(part)) if part.isdigit() else (1, part.casefold())
        for part in _NATURAL_PART.split(value)
        if part
    )


def _required_records(
    document: Mapping[str, Any],
    name: str,
) -> tuple[Mapping[str, Any], ...]:
    value = document.get(name)
    if not isinstance(value, list) or any(
        not isinstance(item, Mapping) for item in value
    ):
        raise FormatError(
            f"Entity asset cache field {name!r} must be an array of objects"
        )
    return tuple(value)


def _required_string(document: Mapping[str, Any], name: str) -> str:
    value = document.get(name)
    if not isinstance(value, str):
        raise FormatError(
            f"Entity asset cache field {name!r} must be text"
        )
    return value


def _required_int(document: Mapping[str, Any], name: str) -> int:
    value = document.get(name)
    if isinstance(value, bool) or not isinstance(value, int):
        raise FormatError(
            f"Entity asset cache field {name!r} must be an integer"
        )
    return value


def _required_bool(document: Mapping[str, Any], name: str) -> bool:
    value = document.get(name)
    if not isinstance(value, bool):
        raise FormatError(
            f"Entity asset cache field {name!r} must be a boolean"
        )
    return value


def _required_strings(
    document: Mapping[str, Any],
    name: str,
) -> tuple[str, ...]:
    value = document.get(name)
    if not isinstance(value, list) or any(
        not isinstance(item, str) for item in value
    ):
        raise FormatError(
            f"Entity asset cache field {name!r} must be an array of text"
        )
    return tuple(value)
