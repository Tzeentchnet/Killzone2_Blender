from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path

from tools.build_extension import (
    build_extension,
    distribution_filename,
    read_manifest,
)


class BuildExtensionTests(unittest.TestCase):
    def test_distribution_filename_includes_manifest_version(self) -> None:
        self.assertEqual(
            "example_extension-1.2.3.zip",
            distribution_filename(
                {
                    "id": "example_extension",
                    "version": "1.2.3",
                }
            ),
        )

    def test_rejects_missing_version(self) -> None:
        with self.assertRaisesRegex(ValueError, "version"):
            distribution_filename({"id": "example_extension"})

    def test_build_uses_versioned_filename(self) -> None:
        manifest = read_manifest()
        with tempfile.TemporaryDirectory() as temporary:
            output = build_extension(output_dir=Path(temporary))

            self.assertEqual(
                f"{manifest['id']}-{manifest['version']}.zip",
                output.name,
            )
            with zipfile.ZipFile(output) as archive:
                self.assertIn("blender_manifest.toml", archive.namelist())
                self.assertIn("LICENSE", archive.namelist())


if __name__ == "__main__":
    unittest.main()
