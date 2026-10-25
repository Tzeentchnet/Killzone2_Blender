"""Build an installable Blender extension archive."""

from __future__ import annotations

import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "io_scene_killzone2"
OUTPUT = ROOT / "dist" / "io_scene_killzone2.zip"


def main() -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(
        OUTPUT,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        for path in sorted(PACKAGE.rglob("*")):
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            archive.write(path, path.relative_to(PACKAGE).as_posix())
        archive.write(ROOT / "LICENSE", "LICENSE")
    print(OUTPUT)


if __name__ == "__main__":
    main()
