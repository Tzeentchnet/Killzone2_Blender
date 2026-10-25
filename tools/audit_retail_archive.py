"""Audit supported Killzone 2 asset structures across a retail PSARC."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import dataclass, fields, is_dataclass
from functools import lru_cache
from pathlib import Path
from typing import get_args, get_origin, get_type_hints

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from io_scene_killzone2.kz2_assets.core import (  # noqa: E402
    CoreFile,
    CoreReference,
)
from io_scene_killzone2.kz2_assets.decoder import (  # noqa: E402
    SUPPORTED_OBJECT_TYPES,
    CoreDecoder,
)
from io_scene_killzone2.kz2_assets.edge_animation import (  # noqa: E402
    EdgeAnimation,
)
from io_scene_killzone2.kz2_assets.errors import (  # noqa: E402
    FormatError,
    Kz2AssetError,
)
from io_scene_killzone2.kz2_assets.level import (  # noqa: E402
    LEVEL_DIRECT_TYPES,
)
from io_scene_killzone2.kz2_assets.model import (  # noqa: E402
    SkeletonAnimation,
    SkinnedMesh,
)
from io_scene_killzone2.kz2_assets.psarc import (  # noqa: E402
    Psarc,
    PsarcEntry,
)

REPORT_VERSION = 1
SAMPLE_LIMIT = 25
LINEAR_TRACK_TYPES = (
    "AnimationTrackLinearFQuat",
    "AnimationTrackLinearFVector3",
)

ProgressCallback = Callable[[int, int, str], None]
Ea02Signature = tuple[int, tuple[int, ...], float, int, bool]


@dataclass(frozen=True, slots=True)
class AuditIssue:
    stage: str
    path: str
    message: str
    object_index: int | None = None
    type_name: str | None = None
    offset: int | None = None
    expected: str | None = None
    observed: str | None = None

    def to_document(self) -> dict[str, object]:
        result: dict[str, object] = {
            "stage": self.stage,
            "path": self.path,
            "message": self.message,
        }
        if self.object_index is not None:
            result["object_index"] = self.object_index
        if self.type_name is not None:
            result["type_name"] = self.type_name
        if self.offset is not None:
            result["offset"] = self.offset
        if self.expected is not None:
            result["expected"] = self.expected
        if self.observed is not None:
            result["observed"] = self.observed
        return result


class ArchiveAuditor:
    def __init__(self, *, decode_supported: bool = True) -> None:
        self.decode_supported = decode_supported
        self.parsed_core_count = 0
        self.type_counts: Counter[str] = Counter()
        self.decoded_type_counts: Counter[str] = Counter()
        self.unsupported_type_counts: Counter[str] = Counter()
        self.zero_payload_type_counts: Counter[str] = Counter()
        self.direct_type_counts: Counter[str] = Counter()
        self.unparsed_direct_type_counts: Counter[str] = Counter()
        self.track_size_counts: dict[str, Counter[int]] = {
            type_name: Counter() for type_name in LINEAR_TRACK_TYPES
        }
        self.track_samples: dict[str, list[dict[str, object]]] = {
            type_name: [] for type_name in LINEAR_TRACK_TYPES
        }
        self.object_ids: set[str] = set()
        self.duplicate_object_id_definitions = 0
        self.reference_kind_counts: Counter[str] = Counter()
        self.external_reference_counts: Counter[str] = Counter()
        self.external_reference_sources: dict[str, dict[str, object]] = {}
        self.ea02_signatures: Counter[Ea02Signature] = Counter()
        self.ea02_signature_samples: dict[
            Ea02Signature, list[dict[str, object]]
        ] = {}
        self.ea02_user_channel_clips = 0
        self.ea02_user_only_clips = 0
        self.ea02_locomotion_clips = 0
        self.skinned_reference_kinds: Counter[str] = Counter()
        self.skinned_reference_samples: list[dict[str, object]] = []
        self.issues: list[AuditIssue] = []

    def audit(
        self,
        archive: Psarc,
        *,
        progress: ProgressCallback | None = None,
    ) -> dict[str, object]:
        logical_count, core_entries = _physical_core_entries(archive)
        total = len(core_entries)
        for position, entry in enumerate(core_entries, start=1):
            path = entry.path
            if path is None:
                raise AssertionError("audited CORE entry has no logical path")
            if progress is not None:
                progress(position, total, path)
            self._audit_core(archive, entry)

        unresolved, invalid = self._external_resolution_summary()
        issues = sorted(self.issues, key=_issue_sort_key)
        success = not issues
        return {
            "report_version": REPORT_VERSION,
            "profile": "BCUS98116 01.01 / RTTIBin<1.58>",
            "options": {
                "decode_supported": self.decode_supported,
            },
            "archive": {
                "psarc_entries_including_manifest": len(archive.entries),
                "manifest_paths": sum(
                    entry.path is not None for entry in archive.entries
                ),
                "logical_core_paths": logical_count,
                "physical_core_entries": total,
                "parsed_core_entries": self.parsed_core_count,
            },
            "objects": {
                "reflected_type_counts": _counter_document(self.type_counts),
                "decoded_type_counts": _counter_document(
                    self.decoded_type_counts
                ),
                "unsupported_type_counts": _counter_document(
                    self.unsupported_type_counts
                ),
                "zero_payload_type_counts": _counter_document(
                    self.zero_payload_type_counts
                ),
                "direct_type_counts": _counter_document(
                    self.direct_type_counts
                ),
                "unparsed_direct_type_counts": _counter_document(
                    self.unparsed_direct_type_counts
                ),
                "unique_nonzero_object_ids": len(self.object_ids),
                "duplicate_object_id_definitions": (
                    self.duplicate_object_id_definitions
                ),
            },
            "references": {
                "kind_counts": _counter_document(
                    self.reference_kind_counts
                ),
                "unique_external_ids": len(self.external_reference_counts),
                "external_reference_occurrences": sum(
                    self.external_reference_counts.values()
                ),
                "resolved_external_ids": (
                    len(self.external_reference_counts)
                    - len(unresolved)
                    - len(invalid)
                ),
                "unresolved_external_ids": {
                    "count": len(unresolved),
                    "samples": unresolved[:SAMPLE_LIMIT],
                },
                "invalid_external_ids": {
                    "count": len(invalid),
                    "samples": invalid[:SAMPLE_LIMIT],
                },
            },
            "animation": {
                "ea02_signatures": self._ea02_document(),
                "clips_with_user_channels": self.ea02_user_channel_clips,
                "clips_with_only_user_channels": self.ea02_user_only_clips,
                "clips_with_locomotion_delta": self.ea02_locomotion_clips,
                "linear_tracks": self._linear_track_document(),
            },
            "skinning": {
                "skinned_reference_kind_counts": _counter_document(
                    self.skinned_reference_kinds
                ),
                "candidate_samples": self.skinned_reference_samples,
            },
            "issues": [issue.to_document() for issue in issues],
            "summary": {
                "success": success,
                "issue_count": len(issues),
                "structural_issue_count": sum(
                    issue.stage == "structure" for issue in issues
                ),
                "framing_issue_count": sum(
                    issue.stage == "framing" for issue in issues
                ),
                "decoder_issue_count": sum(
                    issue.stage == "decoder" for issue in issues
                ),
            },
        }

    def _audit_core(self, archive: Psarc, entry: PsarcEntry) -> None:
        path = entry.path
        if path is None:
            raise AssertionError("audited CORE entry has no logical path")
        try:
            core = CoreFile.from_bytes(
                archive.read_entry(entry),
                source=path,
            )
        except Kz2AssetError as exc:
            self.issues.append(_issue_from_exception("structure", path, exc))
            return

        self.parsed_core_count += 1
        if core.trailing_bytes:
            self.issues.append(
                AuditIssue(
                    stage="framing",
                    path=path,
                    message="CORE has unconsumed trailing bytes",
                    observed=str(core.trailing_bytes),
                )
            )

        for record in core.objects:
            self.type_counts[record.type_name] += 1
            if record.payload_size == 0:
                self.zero_payload_type_counts[record.type_name] += 1
            if record.opaque_id != bytes(16):
                object_id = record.display_id.casefold()
                if object_id in self.object_ids:
                    self.duplicate_object_id_definitions += 1
                else:
                    self.object_ids.add(object_id)
            if record.type_name in LINEAR_TRACK_TYPES:
                self._record_linear_track(
                    path,
                    record.index,
                    record.type_name,
                    record.data_size,
                )
            if (
                record.payload_size == 0
                and record.type_name in LEVEL_DIRECT_TYPES
            ):
                if record.direct_value is None:
                    self.unparsed_direct_type_counts[record.type_name] += 1
                else:
                    self.direct_type_counts[record.type_name] += 1

        unparsed_direct_types = sorted(
            {
                record.type_name
                for record in core.objects
                if record.payload_size == 0
                and record.type_name in LEVEL_DIRECT_TYPES
                and record.direct_value is None
            }
        )
        if unparsed_direct_types:
            self.issues.append(
                AuditIssue(
                    stage="framing",
                    path=path,
                    message="known direct serializers were not decoded",
                    observed=repr(unparsed_direct_types),
                )
            )

        decoder = CoreDecoder(core)
        for record in core.objects:
            decoded: object | None = None
            if record.direct_value is not None:
                decoded = record.direct_value
                self.decoded_type_counts[record.type_name] += 1
            elif record.type_name not in SUPPORTED_OBJECT_TYPES:
                self.unsupported_type_counts[record.type_name] += 1
            elif self.decode_supported:
                try:
                    decoded = decoder.decode(record)
                except (Kz2AssetError, ValueError) as exc:
                    self.issues.append(
                        _issue_from_exception(
                            "decoder",
                            path,
                            exc,
                            object_index=record.index,
                            type_name=record.type_name,
                        )
                    )
                else:
                    self.decoded_type_counts[record.type_name] += 1

            if decoded is None:
                continue
            location = {
                "path": path,
                "object_index": record.index,
                "type_name": record.type_name,
            }
            self._record_references(decoded, location)
            self._record_animation(decoded, location)
            self._record_skinned_mesh(decoded, location)

    def _record_linear_track(
        self,
        path: str,
        object_index: int,
        type_name: str,
        payload_size: int,
    ) -> None:
        self.track_size_counts[type_name][payload_size] += 1
        samples = self.track_samples[type_name]
        if len(samples) < SAMPLE_LIMIT:
            samples.append(
                {
                    "path": path,
                    "object_index": object_index,
                    "payload_size": payload_size,
                }
            )

    def _record_references(
        self,
        decoded: object,
        location: dict[str, object],
    ) -> None:
        for reference in _iter_core_references(decoded):
            if reference.is_null:
                self.reference_kind_counts["null"] += 1
            elif reference.local_index is not None:
                self.reference_kind_counts["local"] += 1
            elif reference.external_id is not None:
                self.reference_kind_counts["external"] += 1
                external_id = reference.external_id.casefold()
                self.external_reference_counts[external_id] += 1
                self.external_reference_sources.setdefault(
                    external_id,
                    location,
                )

    def _record_animation(
        self,
        decoded: object,
        location: dict[str, object],
    ) -> None:
        if not isinstance(decoded, SkeletonAnimation):
            return
        clip = decoded.clip
        if not isinstance(clip, EdgeAnimation):
            return
        signature: Ea02Signature = (
            clip.flags,
            clip.counts,
            clip.sample_frequency,
            clip.frame_count,
            clip.decode_supported,
        )
        self.ea02_signatures[signature] += 1
        samples = self.ea02_signature_samples.setdefault(signature, [])
        if len(samples) < SAMPLE_LIMIT:
            samples.append(location)

        user_count = clip.counts[3] + clip.counts[7]
        transform_count = sum(clip.counts[:3]) + sum(clip.counts[4:7])
        if user_count:
            self.ea02_user_channel_clips += 1
            if transform_count == 0:
                self.ea02_user_only_clips += 1
        if clip.locomotion_delta is not None:
            self.ea02_locomotion_clips += 1

    def _record_skinned_mesh(
        self,
        decoded: object,
        location: dict[str, object],
    ) -> None:
        if not isinstance(decoded, SkinnedMesh):
            return
        skeleton_kind = _reference_kind(decoded.skeleton)
        binding_kind = _reference_kind(decoded.bone_bindings)
        key = f"skeleton_{skeleton_kind}__bindings_{binding_kind}"
        self.skinned_reference_kinds[key] += 1
        if len(self.skinned_reference_samples) >= SAMPLE_LIMIT:
            return
        self.skinned_reference_samples.append(
            {
                **location,
                "skeleton": _reference_document(decoded.skeleton),
                "bone_bindings": _reference_document(
                    decoded.bone_bindings
                ),
            }
        )

    def _external_resolution_summary(
        self,
    ) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
        unresolved: list[dict[str, object]] = []
        invalid: list[dict[str, object]] = []
        for external_id in sorted(self.external_reference_counts):
            sample = {
                "external_id": external_id,
                "occurrences": self.external_reference_counts[external_id],
                "first_reference": self.external_reference_sources[
                    external_id
                ],
            }
            if not _is_display_object_id(external_id):
                invalid.append(sample)
            elif external_id not in self.object_ids:
                unresolved.append(sample)
        return unresolved, invalid

    def _ea02_document(self) -> list[dict[str, object]]:
        result = []
        for signature in sorted(self.ea02_signatures):
            flags, counts, frequency, frame_count, supported = signature
            result.append(
                {
                    "flags": flags,
                    "counts": list(counts),
                    "sample_frequency": frequency,
                    "frame_count": frame_count,
                    "decode_supported": supported,
                    "occurrences": self.ea02_signatures[signature],
                    "samples": self.ea02_signature_samples[signature],
                }
            )
        return result

    def _linear_track_document(self) -> dict[str, object]:
        result: dict[str, object] = {}
        for type_name in LINEAR_TRACK_TYPES:
            sizes = self.track_size_counts[type_name]
            result[type_name] = {
                "count": sum(sizes.values()),
                "payload_size_counts": _integer_counter_document(sizes),
                "samples": self.track_samples[type_name],
            }
        return result


def audit_archive(
    archive: Psarc,
    *,
    decode_supported: bool = True,
    progress: ProgressCallback | None = None,
) -> dict[str, object]:
    return ArchiveAuditor(decode_supported=decode_supported).audit(
        archive,
        progress=progress,
    )


def serialize_report(report: dict[str, object]) -> str:
    return json.dumps(
        report,
        indent=2,
        sort_keys=True,
        ensure_ascii=True,
    ) + "\n"


def report_exit_code(report: dict[str, object]) -> int:
    summary = report.get("summary")
    if not isinstance(summary, dict):
        raise ValueError("audit report has no summary object")
    success = summary.get("success")
    if not isinstance(success, bool):
        raise ValueError("audit report summary has no boolean success field")
    return 0 if success else 1


def _physical_core_entries(
    archive: Psarc,
) -> tuple[int, tuple[PsarcEntry, ...]]:
    logical = sorted(
        (
            entry
            for entry in archive.entries
            if entry.path is not None
            and entry.path.casefold().endswith(".core")
        ),
        key=lambda entry: (entry.path.casefold(), entry.index),
    )
    physical: dict[tuple[int, int, int], PsarcEntry] = {}
    for entry in logical:
        physical.setdefault(entry.physical_key, entry)
    return len(logical), tuple(physical.values())


def _issue_from_exception(
    stage: str,
    path: str,
    error: Exception,
    *,
    object_index: int | None = None,
    type_name: str | None = None,
) -> AuditIssue:
    if isinstance(error, FormatError):
        return AuditIssue(
            stage=stage,
            path=path,
            message=error.message,
            object_index=object_index,
            type_name=type_name,
            offset=error.offset,
            expected=(
                None if error.expected is None else repr(error.expected)
            ),
            observed=(
                None if error.observed is None else repr(error.observed)
            ),
        )
    return AuditIssue(
        stage=stage,
        path=path,
        message=str(error),
        object_index=object_index,
        type_name=type_name,
    )


def _issue_sort_key(
    issue: AuditIssue,
) -> tuple[str, str, int, str, str]:
    return (
        issue.stage,
        issue.path.casefold(),
        -1 if issue.object_index is None else issue.object_index,
        issue.type_name or "",
        issue.message,
    )


def _counter_document(counter: Counter[str]) -> dict[str, int]:
    return {key: counter[key] for key in sorted(counter)}


def _integer_counter_document(counter: Counter[int]) -> dict[str, int]:
    return {str(key): counter[key] for key in sorted(counter)}


def _reference_kind(reference: CoreReference) -> str:
    if reference.is_null:
        return "null"
    if reference.local_index is not None:
        return "local"
    return "external"


def _reference_document(reference: CoreReference) -> dict[str, object]:
    kind = _reference_kind(reference)
    result: dict[str, object] = {"kind": kind}
    if reference.local_index is not None:
        result["local_index"] = reference.local_index
    if reference.external_id is not None:
        result["external_id"] = reference.external_id.casefold()
    return result


def _is_display_object_id(value: str) -> bool:
    if len(value) != 32:
        return False
    try:
        bytes.fromhex(value)
    except ValueError:
        return False
    return True


def _iter_core_references(value: object) -> Iterator[CoreReference]:
    if isinstance(value, CoreReference):
        yield value
        return
    if isinstance(value, (tuple, list, set, frozenset)):
        for item in value:
            yield from _iter_core_references(item)
        return
    if isinstance(value, dict):
        for item in value.values():
            yield from _iter_core_references(item)
        return
    if not is_dataclass(value):
        return
    for field_name in _reference_field_names(type(value)):
        yield from _iter_core_references(getattr(value, field_name))


@lru_cache(maxsize=None)
def _reference_field_names(cls: type[object]) -> tuple[str, ...]:
    hints = get_type_hints(cls)
    return tuple(
        field.name
        for field in fields(cls)
        if _annotation_contains_reference(
            hints.get(field.name, field.type),
            frozenset({cls}),
        )
    )


def _annotation_contains_reference(
    annotation: object,
    visiting: frozenset[type[object]],
) -> bool:
    if annotation is CoreReference:
        return True
    if annotation is object:
        return True
    origin = get_origin(annotation)
    if origin is not None:
        return any(
            _annotation_contains_reference(argument, visiting)
            for argument in get_args(annotation)
            if argument is not Ellipsis
        )
    if not isinstance(annotation, type) or not is_dataclass(annotation):
        return False
    if annotation in visiting:
        return False
    next_visiting = visiting | {annotation}
    hints = get_type_hints(annotation)
    return any(
        _annotation_contains_reference(field_type, next_visiting)
        for field_type in hints.values()
    )


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Audit aggregate CORE structure and supported decoders without "
            "extracting game assets."
        )
    )
    parser.add_argument(
        "psarc",
        nargs="?",
        help="Path to data_ps3.psarc; defaults to KZ2_PSARC.",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Write deterministic JSON to this path instead of stdout.",
    )
    parser.add_argument(
        "--structure-only",
        action="store_true",
        help="Parse CORE framing without running supported object decoders.",
    )
    parser.add_argument(
        "--progress-every",
        type=_positive_int,
        default=100,
        metavar="N",
        help="Print progress every N physical CORE entries (default: 100).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    source = args.psarc or os.environ.get("KZ2_PSARC")
    if not source:
        parser.error("provide a PSARC path or set KZ2_PSARC")

    try:
        archive = Psarc.open(source)
    except (Kz2AssetError, OSError) as exc:
        print(f"Failed to open PSARC: {exc}", file=sys.stderr)
        return 2

    def progress(position: int, total: int, path: str) -> None:
        if position == 1 or position == total or position % args.progress_every == 0:
            print(
                f"[{position}/{total}] {path}",
                file=sys.stderr,
            )

    report = audit_archive(
        archive,
        decode_supported=not args.structure_only,
        progress=progress,
    )
    payload = serialize_report(report)
    if args.output is None:
        sys.stdout.write(payload)
    else:
        output = args.output.expanduser().resolve()
        if output == archive.path.resolve():
            print(
                "Refusing to overwrite the source PSARC with the audit report.",
                file=sys.stderr,
            )
            return 2
        temporary = output.with_name(f"{output.name}.tmp")
        try:
            output.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(payload, encoding="utf-8", newline="\n")
            os.replace(temporary, output)
        except OSError as exc:
            temporary.unlink(missing_ok=True)
            print(f"Failed to write audit report: {exc}", file=sys.stderr)
            return 2
        print(f"Report: {output}", file=sys.stderr)

    archive_summary = report["archive"]
    summary = report["summary"]
    if not isinstance(archive_summary, dict) or not isinstance(summary, dict):
        raise AssertionError("audit generated an invalid summary")
    print(
        "Audited "
        f"{archive_summary['parsed_core_entries']}/"
        f"{archive_summary['physical_core_entries']} physical CORE entries; "
        f"{summary['issue_count']} issue(s).",
        file=sys.stderr,
    )
    return report_exit_code(report)


if __name__ == "__main__":
    raise SystemExit(main())
