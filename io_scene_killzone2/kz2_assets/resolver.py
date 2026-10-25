"""Cross-CORE exact-ID resolution backed by a Killzone 2 PSARC."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .binary import BinaryReader
from .core import (
    CORE_SIGNATURE,
    CoreObject,
    _read_allocation_list,
    _read_compact_utf8,
    _read_typed_string,
)
from .decoder import CoreDecoder
from .errors import FormatError
from .psarc import Psarc, PsarcEntry
from .resource_index import (
    IndexedResourceDefinition,
    ResourceIndex,
    opaque_id_from_display_id,
)

_LOG = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ResolvedResource:
    decoder: CoreDecoder
    record: CoreObject
    value: object


class ResourceResolver(Protocol):
    def add_requested_ids(self, external_ids: set[str]) -> None: ...

    def resolve(
        self,
        external_id: str,
        *,
        expected_type: str | None = None,
    ) -> ResolvedResource | None: ...


@dataclass(frozen=True, slots=True)
class _Definition:
    entry: PsarcEntry
    object_index: int
    type_name: str
    opaque_id: bytes


@dataclass(frozen=True, slots=True)
class _ObjectMetadata:
    index: int
    type_name: str
    opaque_id: bytes


@dataclass(frozen=True, slots=True)
class _CoreMetadata:
    objects: tuple[_ObjectMetadata, ...]
    strings: frozenset[str]


class PsarcResourceResolver:
    def __init__(
        self,
        archive_path: str | Path | Psarc,
        requested_ids: set[str],
        *,
        requested_names: set[tuple[str, str]] | None = None,
        resource_index_path: str | Path | None = None,
    ) -> None:
        self.archive = (
            archive_path
            if isinstance(archive_path, Psarc)
            else Psarc.open(archive_path)
        )
        self.requested_ids = {value.casefold() for value in requested_ids}
        self.missing_ids: set[str] = set(self.requested_ids)
        self.requested_names = set(requested_names or ())
        self.missing_names: set[tuple[str, str]] = set(self.requested_names)
        self._definitions: dict[str, list[_Definition]] = {}
        self._named_definitions: dict[
            tuple[str, str], list[_Definition]
        ] = {}
        self._decoders: dict[tuple[int, int, int], CoreDecoder] = {}
        self._failed_cores: dict[tuple[int, int, int], FormatError] = {}
        self._metadata: dict[tuple[int, int, int], _CoreMetadata] = {}
        if resource_index_path:
            source_stat = self.archive.path.stat()
            self._resource_index: ResourceIndex | None = ResourceIndex(
                resource_index_path,
                source_path=self.archive.path,
                source_size=self.archive.archive_size,
                source_mtime_ns=source_stat.st_mtime_ns,
            )
            _LOG.info(
                "Using persistent PSARC resource index: %s",
                self._resource_index.path,
            )
        else:
            self._resource_index = None
        self._catalog_requested_resources(
            self.requested_ids,
            self.requested_names,
        )

    def add_requested_ids(self, external_ids: set[str]) -> None:
        new_ids = {
            value.casefold()
            for value in external_ids
            if value.casefold() not in self.requested_ids
        }
        if not new_ids:
            return
        self.requested_ids.update(new_ids)
        self.missing_ids.update(new_ids)
        self._catalog_requested_resources(new_ids, set())

    def add_requested_names(
        self,
        resource_names: set[tuple[str, str]],
    ) -> None:
        new_names = resource_names - self.requested_names
        if not new_names:
            return
        self.requested_names.update(new_names)
        self.missing_names.update(new_names)
        self._catalog_requested_resources(set(), new_names)

    def resolve(
        self,
        external_id: str,
        *,
        expected_type: str | None = None,
    ) -> ResolvedResource | None:
        definitions = tuple(
            self._definitions.get(external_id.casefold(), ())
        )
        if expected_type is not None:
            definitions = tuple(
                definition
                for definition in definitions
                if definition.type_name == expected_type
            )
        if not definitions:
            return None
        types = {definition.type_name for definition in definitions}
        if len(types) != 1:
            raise FormatError(
                "external ID resolves to conflicting reflected types",
                source=str(self.archive.path),
                expected=expected_type,
                observed=sorted(types),
            )
        return self._resolve_definitions(
            definitions,
            expected_type=expected_type,
        )

    def resolve_named(
        self,
        type_name: str,
        resource_name: str,
        *,
        source_path: str | None = None,
    ) -> ResolvedResource | None:
        key = (type_name, resource_name)
        definitions = tuple(self._named_definitions.get(key, ()))
        if not definitions:
            return None
        selected = _select_named_definitions(
            definitions,
            source_path=source_path,
        )
        if not selected:
            return None
        identities = {_definition_identity(value) for value in selected}
        if len(identities) != 1:
            raise FormatError(
                "resource class/name resolves ambiguously",
                source=str(self.archive.path),
                expected=f"one exact {type_name}:{resource_name} definition",
                observed=[
                    (
                        definition.entry.path,
                        _display_id(definition.opaque_id),
                    )
                    for definition in selected
                ],
            )
        return self._resolve_definitions(
            selected,
            expected_type=type_name,
        )

    def _resolve_definitions(
        self,
        definitions: tuple[_Definition, ...],
        *,
        expected_type: str | None,
    ) -> ResolvedResource:
        errors: list[str] = []
        for definition in definitions:
            key = definition.entry.physical_key
            failed = self._failed_cores.get(key)
            if failed is not None:
                errors.append(str(failed))
                continue
            decoder = self._decoders.get(key)
            if decoder is None:
                from .core import CoreFile

                try:
                    core = CoreFile.from_bytes(
                        self.archive.read_entry(definition.entry),
                        source=(
                            definition.entry.path
                            or "<unnamed PSARC CORE>"
                        ),
                    )
                except FormatError as exc:
                    self._failed_cores[key] = exc
                    errors.append(str(exc))
                    continue
                decoder = CoreDecoder(core)
                self._decoders[key] = decoder
            record = decoder.core.objects[definition.object_index]
            return ResolvedResource(decoder, record, decoder.decode(record))
        raise FormatError(
            "all matching resource definitions failed to parse",
            source=str(self.archive.path),
            expected=expected_type,
            observed=errors,
        )

    def _catalog_requested_resources(
        self,
        requested_ids: set[str],
        requested_names: set[tuple[str, str]],
    ) -> None:
        if not requested_ids and not requested_names:
            return
        if self._resource_index is not None:
            self._catalog_indexed_resources(
                requested_ids,
                requested_names,
            )
            return
        started = time.perf_counter()
        _LOG.warning(
            "No persistent resource index is available; scanning PSARC "
            "CORE metadata for %d IDs and %d named resources",
            len(requested_ids),
            len(requested_names),
        )
        requested_types = {type_name for type_name, _ in requested_names}
        requested_resource_names = {
            resource_name for _, resource_name in requested_names
        }
        seen: set[tuple[int, int, int]] = set()
        scanned = 0
        for entry in self.archive.entries:
            if (
                entry.physical_key in seen
                or not entry.path
                or not entry.path.casefold().endswith(".core")
            ):
                continue
            seen.add(entry.physical_key)
            scanned += 1
            if scanned == 1 or scanned % 250 == 0:
                _LOG.info(
                    "Fallback resource scan: %d CORE entries checked",
                    scanned,
                )
            metadata = self._read_metadata(entry)
            for obj in metadata.objects:
                display_id = _display_id(obj.opaque_id)
                if display_id not in requested_ids:
                    continue
                definition = _Definition(
                    entry,
                    obj.index,
                    obj.type_name,
                    obj.opaque_id,
                )
                definitions = self._definitions.setdefault(display_id, [])
                if definition not in definitions:
                    definitions.append(definition)
                self.missing_ids.discard(display_id)
            if (
                not requested_types.intersection(
                    obj.type_name for obj in metadata.objects
                )
                or not requested_resource_names.intersection(metadata.strings)
            ):
                continue
            decoder = self._decoder_for_catalog(entry)
            if decoder is None:
                continue
            for record in decoder.core.objects:
                if record.type_name not in requested_types or not record.payload_size:
                    continue
                reader = decoder.core.reader_for(record)
                resource_name = decoder.core.read_string_index(reader, "String")
                name_key = (record.type_name, resource_name)
                if name_key not in requested_names:
                    continue
                definition = _Definition(
                    entry,
                    record.index,
                    record.type_name,
                    record.opaque_id,
                )
                definitions = self._named_definitions.setdefault(name_key, [])
                if definition not in definitions:
                    definitions.append(definition)
                self.missing_names.discard(name_key)
        _LOG.info(
            "Fallback resource scan completed in %.1fs across %d CORE entries",
            time.perf_counter() - started,
            scanned,
        )

    def _catalog_indexed_resources(
        self,
        requested_ids: set[str],
        requested_names: set[tuple[str, str]],
    ) -> None:
        for indexed in self._resource_index.definitions(requested_ids):
            definition = self._indexed_definition(indexed)
            definitions = self._definitions.setdefault(
                indexed.object_id,
                [],
            )
            if definition not in definitions:
                definitions.append(definition)
            self.missing_ids.discard(indexed.object_id)

        for indexed in self._resource_index.named_definitions(
            requested_names
        ):
            if indexed.resource_name is None:
                raise FormatError(
                    "named PSARC resource index entry has no resource name",
                    source=str(self._resource_index.path),
                )
            definition = self._indexed_definition(indexed)
            name_key = (indexed.type_name, indexed.resource_name)
            definitions = self._named_definitions.setdefault(name_key, [])
            if definition not in definitions:
                definitions.append(definition)
            self.missing_names.discard(name_key)

    def _indexed_definition(
        self,
        indexed: IndexedResourceDefinition,
    ) -> _Definition:
        try:
            entry = self.archive.get(indexed.path)
        except KeyError as exc:
            raise FormatError(
                "PSARC resource index references a missing CORE; rescan it",
                source=str(self._resource_index.path),
                observed=indexed.path,
            ) from exc
        return _Definition(
            entry=entry,
            object_index=indexed.object_index,
            type_name=indexed.type_name,
            opaque_id=opaque_id_from_display_id(indexed.object_id),
        )

    def _decoder_for_catalog(
        self,
        entry: PsarcEntry,
    ) -> CoreDecoder | None:
        key = entry.physical_key
        failed = self._failed_cores.get(key)
        if failed is not None:
            return None
        decoder = self._decoders.get(key)
        if decoder is not None:
            return decoder
        from .core import CoreFile

        try:
            core = CoreFile.from_bytes(
                self.archive.read_entry(entry),
                source=entry.path or "<unnamed PSARC CORE>",
            )
        except FormatError as exc:
            self._failed_cores[key] = exc
            return None
        decoder = CoreDecoder(core)
        self._decoders[key] = decoder
        return decoder

    def _read_metadata(
        self,
        entry: PsarcEntry,
    ) -> _CoreMetadata:
        key = entry.physical_key
        cached = self._metadata.get(key)
        if cached is None:
            cached = read_core_metadata(self.archive, entry)
            self._metadata[key] = cached
        return cached


def read_core_metadata(
    archive: Psarc,
    entry: PsarcEntry,
) -> _CoreMetadata:
    limit = min(entry.uncompressed_size, 2 * 1024 * 1024)
    while True:
        try:
            return _parse_object_metadata(
                archive.read_prefix(entry, limit),
                entry.path or "<unnamed PSARC CORE>",
            )
        except FormatError:
            if limit >= entry.uncompressed_size:
                raise
            limit = min(entry.uncompressed_size, limit * 2)


def _parse_object_metadata(
    data: bytes,
    source: str,
) -> _CoreMetadata:
    reader = BinaryReader.from_bytes(data, source=source)
    signature = reader.read(len(CORE_SIGNATURE))
    if signature != CORE_SIGNATURE:
        reader.fail(
            "unsupported CORE signature",
            offset=0,
            expected=CORE_SIGNATURE,
            observed=signature,
        )
    marker = reader.u8()
    if marker != 1:
        reader.fail(
            "unsupported CORE endian marker",
            expected=1,
            observed=marker,
        )
    reader.skip(16)
    type_count = reader.count("type-name count")
    type_names = tuple(
        _read_compact_utf8(reader, "type name") for _ in range(type_count)
    )
    object_count = reader.count("object count")
    object_types = tuple(
        reader.adaptive_index(type_count, "object type index")
        for _ in range(object_count)
    )
    objects: list[_ObjectMetadata] = []
    for index, type_index in enumerate(object_types):
        opaque_id = reader.read(16)
        reader.skip(4)
        reader.skip(12)
        objects.append(
            _ObjectMetadata(index, type_names[type_index], opaque_id)
        )

    _read_allocation_list(reader)
    bank_count = reader.count("string-bank count")
    strings: set[str] = set()
    for _ in range(bank_count):
        type_index = reader.adaptive_index(
            type_count,
            "string-bank type index",
        )
        bank_type = type_names[type_index]
        string_count = reader.count("string count")
        values = (
            _read_typed_string(reader, bank_type)
            for _ in range(string_count)
        )
        if bank_type == "String":
            strings.update(values)
        else:
            for _ in values:
                pass
    return _CoreMetadata(tuple(objects), frozenset(strings))


def _select_named_definitions(
    definitions: tuple[_Definition, ...],
    *,
    source_path: str | None,
) -> tuple[_Definition, ...]:
    source_scope = _level_scope(source_path)
    if source_scope is not None:
        same_level = tuple(
            definition
            for definition in definitions
            if _level_scope(definition.entry.path) == source_scope
        )
        if same_level:
            return same_level

    shared_base = tuple(
        definition
        for definition in definitions
        if _is_shared_base_lump(definition.entry.path)
    )
    if shared_base:
        return shared_base
    return definitions


def _definition_identity(
    definition: _Definition,
) -> bytes | tuple[tuple[int, int, int], int]:
    if definition.opaque_id != bytes(16):
        return definition.opaque_id
    return definition.entry.physical_key, definition.object_index


def _level_scope(path: str | None) -> str | None:
    if not path:
        return None
    normalized = path.replace("\\", "/").casefold()
    for marker, separator in (
        ("levels.single_player.", "."),
        ("levels/single_player/", "/"),
    ):
        marker_index = normalized.find(marker)
        if marker_index < 0:
            continue
        remainder = normalized[marker_index + len(marker) :]
        return remainder.split(separator, 1)[0]
    return None


def _is_shared_base_lump(path: str | None) -> bool:
    if not path:
        return False
    normalized = path.replace("\\", "/").casefold()
    return (
        ".section_shared.baselumps." in normalized
        or "/section_shared/baselumps/" in normalized
    )


def _display_id(opaque_id: bytes) -> str:
    return b"".join(
        opaque_id[index : index + 4][::-1]
        for index in range(0, len(opaque_id), 4)
    ).hex()
