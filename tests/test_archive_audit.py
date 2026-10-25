from __future__ import annotations

import hashlib
import struct
import tempfile
import unittest
import zlib
from pathlib import Path

from io_scene_killzone2.kz2_assets.core import CORE_SIGNATURE, CoreReference
from io_scene_killzone2.kz2_assets.level import StructuredDirectObject
from io_scene_killzone2.kz2_assets.psarc import Psarc
from tools.audit_retail_archive import (
    _iter_core_references,
    audit_archive,
    report_exit_code,
    serialize_report,
)


def compact(value: int) -> bytes:
    if -128 <= value <= 127 and value not in (-128, -127):
        return struct.pack(">b", value)
    if -32768 <= value <= 32767:
        return b"\x81" + struct.pack(">h", value)
    return b"\x80" + struct.pack(">i", value)


def synthetic_core(type_name: str, payload: bytes) -> bytes:
    encoded_type = type_name.encode()
    data = bytearray(CORE_SIGNATURE)
    data.append(1)
    data.extend(bytes(16))
    data.extend(compact(1))
    data.extend(compact(len(encoded_type)))
    data.extend(encoded_type)
    data.extend(compact(1))
    data.append(0)
    data.extend(bytes.fromhex("00112233445566778899aabbccddeeff"))
    data.extend(struct.pack(">I", len(payload)))
    data.extend(bytes(12))
    data.extend(compact(0))
    data.extend(compact(0))
    data.extend(compact(0))
    data.extend(compact(0))
    data.extend(compact(0))
    data.extend(compact(0))
    data.extend(payload)
    return bytes(data)


def _u40(value: int) -> bytes:
    return value.to_bytes(5, "big")


def build_archive(path: Path, files: list[tuple[str, bytes]]) -> None:
    manifest = ("\n".join(name for name, _ in files) + "\n").encode()
    contents = [manifest, *(payload for _, payload in files)]
    block_size = 256
    stored_entries: list[list[bytes]] = []
    block_lengths: list[int] = []
    first_blocks: list[int] = []

    for content in contents:
        first_blocks.append(len(block_lengths))
        stored_blocks = []
        for start in range(0, len(content), block_size):
            raw = content[start : start + block_size]
            compressed = zlib.compress(raw, level=9)
            stored = compressed if len(compressed) < len(raw) else raw
            stored_blocks.append(stored)
            block_lengths.append(0 if len(stored) == block_size else len(stored))
        stored_entries.append(stored_blocks)

    entry_count = len(contents)
    toc_length = 0x20 + entry_count * 30 + len(block_lengths)
    data_offsets = []
    cursor = toc_length
    for blocks in stored_entries:
        data_offsets.append(cursor)
        cursor += sum(len(block) for block in blocks)

    header = (
        b"PSAR"
        + struct.pack(">I", 0x00010003)
        + b"zlib"
        + struct.pack(">IIIII", toc_length, 30, entry_count, block_size, 1)
    )
    records = bytearray()
    for index, content in enumerate(contents):
        digest = (
            bytes(16)
            if index == 0
            else hashlib.md5(files[index - 1][0].upper().encode()).digest()
        )
        records.extend(digest)
        records.extend(struct.pack(">I", first_blocks[index]))
        records.extend(_u40(len(content)))
        records.extend(_u40(data_offsets[index]))

    with path.open("wb") as stream:
        stream.write(header)
        stream.write(records)
        stream.write(bytes(block_lengths))
        for blocks in stored_entries:
            for block in blocks:
                stream.write(block)


class ArchiveAuditTests(unittest.TestCase):
    def _audit(
        self,
        files: list[tuple[str, bytes]],
        *,
        decode_supported: bool = True,
    ) -> dict[str, object]:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "test.psarc"
            build_archive(path, files)
            return audit_archive(
                Psarc.open(path),
                decode_supported=decode_supported,
            )

    def test_report_is_deterministic_and_inventories_linear_tracks(self) -> None:
        files = [
            (
                "folder/zeta.core",
                synthetic_core("Dummy", b"abc"),
            ),
            (
                "folder/alpha.core",
                synthetic_core("AnimationTrackLinearFQuat", bytes(24)),
            ),
        ]
        first = self._audit(files)
        second = self._audit(list(reversed(files)))

        self.assertEqual(serialize_report(first), serialize_report(second))
        self.assertEqual(0, report_exit_code(first))
        self.assertEqual(
            {
                "psarc_entries_including_manifest": 3,
                "manifest_paths": 2,
                "logical_core_paths": 2,
                "physical_core_entries": 2,
                "parsed_core_entries": 2,
            },
            first["archive"],
        )
        objects = first["objects"]
        self.assertIsInstance(objects, dict)
        self.assertEqual(
            {
                "AnimationTrackLinearFQuat": 1,
                "Dummy": 1,
            },
            objects["reflected_type_counts"],
        )
        animation = first["animation"]
        self.assertIsInstance(animation, dict)
        tracks = animation["linear_tracks"]
        self.assertIsInstance(tracks, dict)
        quaternion_tracks = tracks["AnimationTrackLinearFQuat"]
        self.assertEqual(1, quaternion_tracks["count"])
        self.assertEqual({"24": 1}, quaternion_tracks["payload_size_counts"])

    def test_decoder_failures_are_bounded_and_fail_the_report(self) -> None:
        report = self._audit(
            [
                ("folder/zeta.core", synthetic_core("Texture", b"")),
                ("folder/alpha.core", synthetic_core("Texture", b"")),
            ]
        )

        self.assertEqual(1, report_exit_code(report))
        self.assertEqual(2, report["summary"]["decoder_issue_count"])
        self.assertEqual(
            ["folder/alpha.core", "folder/zeta.core"],
            [issue["path"] for issue in report["issues"]],
        )
        self.assertTrue(
            all(issue["stage"] == "decoder" for issue in report["issues"])
        )
        self.assertTrue(
            all(issue["type_name"] == "Texture" for issue in report["issues"])
        )

    def test_structure_only_skips_supported_decoders(self) -> None:
        report = self._audit(
            [("folder/texture.core", synthetic_core("Texture", b""))],
            decode_supported=False,
        )

        self.assertEqual(0, report_exit_code(report))
        self.assertEqual([], report["issues"])
        self.assertEqual({}, report["objects"]["decoded_type_counts"])

    def test_structured_direct_object_references_are_discovered(self) -> None:
        expected = CoreReference(external_id="01" * 16)
        decoded = StructuredDirectObject(
            "Synthetic",
            (("resource", expected),),
        )

        self.assertEqual([expected], list(_iter_core_references(decoded)))

    def test_structural_failures_are_reported_without_aborting_scan(self) -> None:
        report = self._audit(
            [
                ("folder/good.core", synthetic_core("Dummy", b"abc")),
                ("folder/bad.core", b"not a core"),
            ]
        )

        self.assertEqual(1, report_exit_code(report))
        self.assertEqual(1, report["archive"]["parsed_core_entries"])
        self.assertEqual(1, report["summary"]["structural_issue_count"])
        self.assertEqual("folder/bad.core", report["issues"][0]["path"])


if __name__ == "__main__":
    unittest.main()
