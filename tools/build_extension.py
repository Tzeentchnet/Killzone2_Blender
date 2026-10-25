"""Build an installable Blender extension archive."""

from __future__ import annotations

import re
import tomllib
import zipfile
from collections.abc import Mapping
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "io_scene_killzone2"
MANIFEST = PACKAGE / "blender_manifest.toml"
DIST = ROOT / "dist"
_FILENAME_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")


def read_manifest(path: Path = MANIFEST) -> dict[str, object]:
    with path.open("rb") as stream:
        return tomllib.load(stream)


def distribution_filename(manifest: Mapping[str, object]) -> str:
    extension_id = _filename_component(manifest, "id")
    version = _filename_component(manifest, "version")
    return f"{extension_id}-{version}.zip"


def distribution_path(
    *,
    manifest_path: Path = MANIFEST,
    output_dir: Path = DIST,
) -> Path:
    return output_dir / distribution_filename(read_manifest(manifest_path))


def build_extension(*, output_dir: Path = DIST) -> Path:
    output = distribution_path(output_dir=output_dir)
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(
        output,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        for path in sorted(PACKAGE.rglob("*")):
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            archive.write(path, path.relative_to(PACKAGE).as_posix())
        archive.write(ROOT / "LICENSE", "LICENSE")
    return output


def _filename_component(
    manifest: Mapping[str, object],
    field: str,
) -> str:
    value = manifest.get(field)
    if not isinstance(value, str) or not _FILENAME_COMPONENT.fullmatch(value):
        raise ValueError(
            f"Blender manifest {field!r} must be a filename-safe string"
        )
    return value


def main() -> None:
    print(build_extension())


if __name__ == "__main__":
    main()
