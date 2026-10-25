"""Campaign and multiplayer hierarchy stored in retail level manifests."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .core import CoreFile
from .errors import Kz2AssetError
from .level import parse_level_asset_descriptor, parse_level_section
from .psarc import Psarc, PsarcEntry

LEVEL_KIND_CAMPAIGN = "CAMPAIGN"
LEVEL_KIND_MULTIPLAYER = "MULTIPLAYER"
LEVEL_KIND_ITEMS = (
    (LEVEL_KIND_CAMPAIGN, "Campaign", "Show single-player campaign levels"),
    (LEVEL_KIND_MULTIPLAYER, "Multiplayer", "Show multiplayer maps"),
)

_CAMPAIGN_ROOT = re.compile(
    r"^localcacheps3/lumps/levels\.single_player\.optimized\."
    r"([^.]+)_optimized\.core$",
    re.IGNORECASE,
)
_MULTIPLAYER_ROOT = re.compile(
    r"^localcacheps3/lumps/levels\.multi_player\."
    r"(mp_level_\d+)\.core$",
    re.IGNORECASE,
)
_NATURAL_PART = re.compile(r"(\d+)")
_CAMPAIGN_ORDER = (
    "corinth_river",
    "blood_meridian",
    "visari_square",
    "salamun_district",
    "salamun_bridge",
    "suljeva_village",
    "tharsis_refinery",
    "the_cruiser",
    "maelstra_barrens",
    "visari_palace",
)
_MULTIPLAYER_NAMES = {
    "mp_level_01": "Corinth Crossing",
    "mp_level_02": "Blood Gracht",
    "mp_level_03": "Salamun Market",
    "mp_level_04": "Pyrrhus Rise",
    "mp_level_05": "Radec Academy",
    "mp_level_06": "Tharsis Depot",
    "mp_level_07": "Visari Hammer",
    "mp_level_08": "Helghan Industries",
}
_DISPLAY_PARTS = {
    "controlroom": "Control Room",
    "crashsite": "Crash Site",
    "depod": "Depot",
    "interogation": "Interrogation",
    "landingbay": "Landing Bay",
    "mainsquare": "Main Square",
    "thenewsun": "The New Sun",
    "trackroom": "Track Room",
    "trainstation": "Train Station",
}


@dataclass(frozen=True, slots=True)
class CachedLevelSection:
    key: str
    display_name: str
    core_paths: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CachedLevel:
    key: str
    kind: str
    display_name: str
    root_path: str
    sections: tuple[CachedLevelSection, ...]


def scan_level_catalog(
    source: str | Path | Psarc,
) -> tuple[CachedLevel, ...]:
    archive = source if isinstance(source, Psarc) else Psarc.open(source)
    roots = _level_roots(archive)
    levels = [
        _scan_campaign_level(archive, entry, slug)
        for entry, slug, kind in roots
        if kind == LEVEL_KIND_CAMPAIGN
    ]
    levels.extend(
        _scan_multiplayer_level(archive, entry, slug)
        for entry, slug, kind in roots
        if kind == LEVEL_KIND_MULTIPLAYER
    )
    return tuple(
        sorted(
            levels,
            key=lambda level: (
                0 if level.kind == LEVEL_KIND_CAMPAIGN else 1,
                _level_order(level),
                _natural_key(level.display_name),
            ),
        )
    )


def _level_roots(
    archive: Psarc,
) -> tuple[tuple[PsarcEntry, str, str], ...]:
    roots: list[tuple[PsarcEntry, str, str]] = []
    seen: set[tuple[int, int, int]] = set()
    for entry in archive.entries:
        if entry.physical_key in seen or not entry.path:
            continue
        campaign = _CAMPAIGN_ROOT.fullmatch(entry.path)
        multiplayer = _MULTIPLAYER_ROOT.fullmatch(entry.path)
        if campaign is None and multiplayer is None:
            continue
        seen.add(entry.physical_key)
        if campaign is not None:
            roots.append(
                (entry, campaign.group(1).casefold(), LEVEL_KIND_CAMPAIGN)
            )
        else:
            assert multiplayer is not None
            roots.append(
                (
                    entry,
                    multiplayer.group(1).casefold(),
                    LEVEL_KIND_MULTIPLAYER,
                )
            )
    return tuple(roots)


def _scan_campaign_level(
    archive: Psarc,
    root_entry: PsarcEntry,
    slug: str,
) -> CachedLevel:
    root_path = root_entry.path or f"{slug}.core"
    core = CoreFile.from_bytes(
        archive.read_entry(root_entry),
        source=root_path,
    )
    sections: list[CachedLevelSection] = []
    for record in core.objects:
        if record.type_name != "LevelSection":
            continue
        section = parse_level_section(core, record)
        if not section.active:
            continue
        core_paths: list[str] = []
        seen_physical: set[tuple[int, int, int]] = set()
        for asset in section.assets:
            if not asset.enabled or asset.descriptor.local_index is None:
                continue
            descriptor_record = core.objects[asset.descriptor.local_index]
            try:
                descriptor = parse_level_asset_descriptor(
                    core,
                    descriptor_record,
                )
            except (Kz2AssetError, ValueError):
                continue
            if not descriptor.active:
                continue
            entry = _lump_entry(archive, descriptor.lump_location)
            if (
                entry is None
                or ".baselumps." in (entry.path or "").casefold()
                or entry.physical_key in seen_physical
            ):
                continue
            seen_physical.add(entry.physical_key)
            core_paths.append(
                entry.path
                or _lump_core_path(descriptor.lump_location)
            )
        if not core_paths:
            continue
        sections.append(
            CachedLevelSection(
                key=section.name.casefold(),
                display_name=_section_display_name(section.name),
                core_paths=tuple(core_paths),
            )
        )
    sections.sort(key=lambda value: _section_key(value.display_name))
    return CachedLevel(
        key=f"campaign:{slug}",
        kind=LEVEL_KIND_CAMPAIGN,
        display_name=_identifier_display_name(slug),
        root_path=root_path,
        sections=tuple(sections),
    )


def _scan_multiplayer_level(
    archive: Psarc,
    root_entry: PsarcEntry,
    slug: str,
) -> CachedLevel:
    root_path = root_entry.path or f"{slug}.core"
    core = CoreFile.from_bytes(
        archive.read_entry(root_entry),
        source=root_path,
    )
    core_paths: list[str] = []
    seen_physical: set[tuple[int, int, int]] = set()
    prefix = f"levels.multi_player.{slug}."
    for record in core.objects:
        if record.type_name not in {
            "BaseConcreteAsset",
            "ConcreteAsset",
            "DependentConcreteAsset",
        }:
            continue
        try:
            descriptor = parse_level_asset_descriptor(core, record)
        except (Kz2AssetError, ValueError):
            continue
        lump_location = descriptor.lump_location
        if (
            not descriptor.active
            or not lump_location.casefold().startswith(prefix)
            or "ambient" in lump_location.casefold()
        ):
            continue
        entry = _lump_entry(archive, lump_location)
        if entry is None or entry.physical_key in seen_physical:
            continue
        seen_physical.add(entry.physical_key)
        core_paths.append(entry.path or _lump_core_path(lump_location))
    section = CachedLevelSection(
        key="map",
        display_name="Map",
        core_paths=tuple(core_paths),
    )
    identifier = int(slug.rsplit("_", 1)[-1])
    display_name = _MULTIPLAYER_NAMES.get(
        slug,
        _identifier_display_name(slug),
    )
    return CachedLevel(
        key=f"multiplayer:{slug}",
        kind=LEVEL_KIND_MULTIPLAYER,
        display_name=f"{display_name} (MP {identifier:02d})",
        root_path=root_path,
        sections=(section,),
    )


def _lump_entry(
    archive: Psarc,
    lump_location: str,
) -> PsarcEntry | None:
    try:
        return archive.get(_lump_core_path(lump_location))
    except KeyError:
        return None


def _lump_core_path(lump_location: str) -> str:
    return f"LocalCachePS3/lumps/{lump_location}.core"


def _identifier_display_name(value: str) -> str:
    return " ".join(
        _DISPLAY_PARTS.get(part.casefold(), part.capitalize())
        for part in value.split("_")
    )


def _section_display_name(value: str) -> str:
    label = value
    if label.casefold().startswith("section_"):
        label = label[len("section_") :]
    return _identifier_display_name(label)


def _section_key(value: str) -> tuple[int, tuple[tuple[int, int | str], ...]]:
    folded = value.casefold()
    if "intro" in folded:
        rank = 0
    elif match := re.match(r"(\d+)", folded):
        rank = 10 + int(match.group(1))
    elif "outro" in folded:
        rank = 10_000
    else:
        rank = 1_000
    return rank, _natural_key(value)


def _level_order(level: CachedLevel) -> int:
    slug = level.key.partition(":")[2]
    if level.kind == LEVEL_KIND_CAMPAIGN:
        try:
            return _CAMPAIGN_ORDER.index(slug)
        except ValueError:
            return len(_CAMPAIGN_ORDER)
    try:
        return int(slug.rsplit("_", 1)[-1])
    except ValueError:
        return 10_000


def _natural_key(value: str) -> tuple[tuple[int, int | str], ...]:
    return tuple(
        (0, int(part)) if part.isdigit() else (1, part.casefold())
        for part in _NATURAL_PART.split(value)
        if part
    )
