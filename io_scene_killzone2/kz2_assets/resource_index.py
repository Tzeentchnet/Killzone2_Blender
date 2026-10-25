"""Persistent SQLite lookup for externally referenced PSARC resources."""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterable
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, Self

from .errors import FormatError

RESOURCE_INDEX_VERSION = 1


class ResourceObjectMetadata(Protocol):
    index: int
    type_name: str
    opaque_id: bytes


@dataclass(frozen=True, slots=True)
class IndexedResourceDefinition:
    object_id: str
    path: str
    object_index: int
    type_name: str
    resource_name: str | None = None


class ResourceIndexWriter:
    def __init__(
        self,
        path: str | Path,
        *,
        source_path: str | Path,
        source_size: int,
        source_mtime_ns: int,
        supported_types: frozenset[str],
    ) -> None:
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.temporary_path = self.path.with_name(f"{self.path.name}.tmp")
        if self.temporary_path.exists():
            self.temporary_path.unlink()
        self._source_path = str(Path(source_path).expanduser().resolve())
        self._source_size = source_size
        self._source_mtime_ns = source_mtime_ns
        self._connection: sqlite3.Connection | None = None
        type_names = tuple(sorted(supported_types))
        try:
            self._connection = sqlite3.connect(self.temporary_path)
            self._connection.execute("PRAGMA journal_mode=OFF")
            self._connection.execute("PRAGMA synchronous=OFF")
            self._connection.executescript(
                """
                CREATE TABLE metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                ) WITHOUT ROWID;
                CREATE TABLE cores (
                    id INTEGER PRIMARY KEY,
                    path TEXT UNIQUE NOT NULL
                );
                CREATE TABLE types (
                    id INTEGER PRIMARY KEY,
                    name TEXT UNIQUE NOT NULL
                );
                CREATE TABLE definitions (
                    object_id BLOB NOT NULL,
                    core_id INTEGER NOT NULL,
                    object_index INTEGER NOT NULL,
                    type_id INTEGER NOT NULL,
                    PRIMARY KEY (object_id, core_id, object_index)
                ) WITHOUT ROWID;
                CREATE TABLE named_definitions (
                    type_id INTEGER NOT NULL,
                    resource_name TEXT NOT NULL,
                    core_id INTEGER NOT NULL,
                    object_index INTEGER NOT NULL,
                    object_id BLOB NOT NULL,
                    PRIMARY KEY (
                        type_id,
                        resource_name,
                        core_id,
                        object_index
                    )
                ) WITHOUT ROWID;
                """
            )
            self._connection.executemany(
                "INSERT INTO types (id, name) VALUES (?, ?)",
                enumerate(type_names),
            )
        except sqlite3.Error as exc:
            self.abort()
            raise FormatError(
                "failed to create the PSARC resource index",
                source=str(self.path),
                observed=str(exc),
            ) from exc
        self._type_ids = {
            type_name: index for index, type_name in enumerate(type_names)
        }
        self._core_ids: dict[str, int] = {}
        self._finished = False

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        if exc_type is not None or not self._finished:
            self.abort()

    def add_core(
        self,
        path: str,
        objects: Iterable[ResourceObjectMetadata],
    ) -> None:
        connection = self._require_connection()
        core_id = len(self._core_ids)
        self._core_ids[path] = core_id
        try:
            connection.execute(
                "INSERT INTO cores (id, path) VALUES (?, ?)",
                (core_id, path),
            )
            connection.executemany(
                """
                INSERT OR IGNORE INTO definitions (
                    object_id,
                    core_id,
                    object_index,
                    type_id
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    (
                        _display_id_bytes(obj.opaque_id),
                        core_id,
                        obj.index,
                        self._type_ids[obj.type_name],
                    )
                    for obj in objects
                    if obj.opaque_id != bytes(16)
                    and obj.type_name in self._type_ids
                ),
            )
        except sqlite3.Error as exc:
            self.abort()
            raise FormatError(
                "failed to write CORE metadata to the PSARC resource index",
                source=str(self.path),
                observed=str(exc),
            ) from exc

    def add_named_definition(
        self,
        *,
        path: str,
        type_name: str,
        resource_name: str,
        object_index: int,
        opaque_id: bytes,
    ) -> None:
        try:
            self._require_connection().execute(
                """
                INSERT OR IGNORE INTO named_definitions (
                    type_id,
                    resource_name,
                    core_id,
                    object_index,
                    object_id
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    self._type_ids[type_name],
                    resource_name,
                    self._core_ids[path],
                    object_index,
                    _display_id_bytes(opaque_id),
                ),
            )
        except sqlite3.Error as exc:
            self.abort()
            raise FormatError(
                "failed to write a named entity to the PSARC resource index",
                source=str(self.path),
                observed=str(exc),
            ) from exc

    def finish(self) -> None:
        connection = self._require_connection()
        try:
            connection.executemany(
                "INSERT INTO metadata (key, value) VALUES (?, ?)",
                (
                    ("version", str(RESOURCE_INDEX_VERSION)),
                    ("source_path", self._source_path),
                    ("source_size", str(self._source_size)),
                    ("source_mtime_ns", str(self._source_mtime_ns)),
                ),
            )
            connection.commit()
        except sqlite3.Error as exc:
            self.abort()
            raise FormatError(
                "failed to finalize the PSARC resource index",
                source=str(self.path),
                observed=str(exc),
            ) from exc
        finally:
            connection.close()
            self._connection = None
        os.replace(self.temporary_path, self.path)
        self._finished = True

    def abort(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None
        if self.temporary_path.exists():
            self.temporary_path.unlink()

    def _require_connection(self) -> sqlite3.Connection:
        if self._connection is None:
            raise RuntimeError("PSARC resource index writer is closed")
        return self._connection


class ResourceIndex:
    def __init__(
        self,
        path: str | Path,
        *,
        source_path: str | Path,
        source_size: int,
        source_mtime_ns: int,
    ) -> None:
        self.path = Path(path).expanduser().resolve()
        if not self.path.is_file():
            raise FormatError(
                "PSARC resource index is missing; rescan importable entities",
                source=str(self.path),
            )
        try:
            with closing(self._connect()) as connection:
                metadata = dict(
                    connection.execute("SELECT key, value FROM metadata")
                )
        except sqlite3.Error as exc:
            raise FormatError(
                "PSARC resource index is invalid; rescan importable entities",
                source=str(self.path),
                observed=str(exc),
            ) from exc
        expected_path = str(Path(source_path).expanduser().resolve())
        if (
            metadata.get("version") != str(RESOURCE_INDEX_VERSION)
            or os.path.normcase(metadata.get("source_path", ""))
            != os.path.normcase(expected_path)
            or metadata.get("source_size") != str(source_size)
            or metadata.get("source_mtime_ns") != str(source_mtime_ns)
        ):
            raise FormatError(
                "PSARC resource index is stale; rescan importable entities",
                source=str(self.path),
            )

    def definitions(
        self,
        external_ids: set[str],
    ) -> tuple[IndexedResourceDefinition, ...]:
        object_ids = _queryable_object_ids(external_ids)
        if not object_ids:
            return ()
        results: list[IndexedResourceDefinition] = []
        try:
            with closing(self._connect()) as connection:
                for chunk_start in range(0, len(object_ids), 500):
                    chunk = object_ids[chunk_start : chunk_start + 500]
                    placeholders = ",".join("?" for _ in chunk)
                    rows = connection.execute(
                        f"""
                        SELECT
                            lower(hex(definitions.object_id)),
                            cores.path,
                            definitions.object_index,
                            types.name
                        FROM definitions
                        JOIN cores ON cores.id = definitions.core_id
                        JOIN types ON types.id = definitions.type_id
                        WHERE definitions.object_id IN ({placeholders})
                        """,
                        chunk,
                    )
                    results.extend(
                        IndexedResourceDefinition(*row) for row in rows
                    )
        except sqlite3.Error as exc:
            raise FormatError(
                "failed to query the PSARC resource index",
                source=str(self.path),
                observed=str(exc),
            ) from exc
        return tuple(results)

    def named_definitions(
        self,
        resource_names: set[tuple[str, str]],
    ) -> tuple[IndexedResourceDefinition, ...]:
        results: list[IndexedResourceDefinition] = []
        try:
            with closing(self._connect()) as connection:
                for type_name, resource_name in resource_names:
                    rows = connection.execute(
                        """
                        SELECT
                            lower(hex(named_definitions.object_id)),
                            cores.path,
                            named_definitions.object_index,
                            types.name,
                            named_definitions.resource_name
                        FROM named_definitions
                        JOIN cores ON cores.id = named_definitions.core_id
                        JOIN types ON types.id = named_definitions.type_id
                        WHERE types.name = ? AND resource_name = ?
                        """,
                        (type_name, resource_name),
                    )
                    results.extend(
                        IndexedResourceDefinition(*row) for row in rows
                    )
        except sqlite3.Error as exc:
            raise FormatError(
                "failed to query named entities in the PSARC resource index",
                source=str(self.path),
                observed=str(exc),
            ) from exc
        return tuple(results)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.execute("PRAGMA query_only=ON")
        return connection


def _display_id_bytes(opaque_id: bytes) -> bytes:
    return b"".join(
        opaque_id[index : index + 4][::-1]
        for index in range(0, len(opaque_id), 4)
    )


def _opaque_id_from_display_id(display_id: str) -> bytes:
    display_bytes = bytes.fromhex(display_id)
    return b"".join(
        display_bytes[index : index + 4][::-1]
        for index in range(0, len(display_bytes), 4)
    )


def opaque_id_from_display_id(display_id: str) -> bytes:
    try:
        opaque_id = _opaque_id_from_display_id(display_id)
    except ValueError as exc:
        raise FormatError(
            "resource index contains an invalid object ID",
            observed=display_id,
        ) from exc
    if len(opaque_id) != 16:
        raise FormatError(
            "resource index contains an invalid object ID",
            expected="16 bytes",
            observed=display_id,
        )
    return opaque_id


def _queryable_object_ids(external_ids: set[str]) -> tuple[bytes, ...]:
    values: list[bytes] = []
    for external_id in external_ids:
        try:
            value = bytes.fromhex(external_id)
        except ValueError:
            continue
        if len(value) == 16:
            values.append(value)
    return tuple(values)
