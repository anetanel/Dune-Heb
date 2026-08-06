#!/usr/bin/env python3
"""Package game/ into a DUNE.zip at the repo root, under a top-level DUNE/
folder, excluding save files, backups, and repo bookkeeping files."""

import pathlib
import shutil
import tempfile
import zipfile

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
GAME_DIR = REPO_ROOT / "game"
OUT_ZIP = REPO_ROOT / "DUNE.zip.txt"

EXCLUDE_NAMES = {".gitkeep", ".DS_Store"}
EXCLUDE_SUFFIXES = {".SAV", ".orig-backup"}


def is_excluded(path: pathlib.Path) -> bool:
    if path.name in EXCLUDE_NAMES:
        return True
    return any(path.name.endswith(suffix) for suffix in EXCLUDE_SUFFIXES)


def main() -> None:
    if OUT_ZIP.exists():
        OUT_ZIP.unlink()

    with tempfile.TemporaryDirectory() as tmp:
        staging = pathlib.Path(tmp) / "DUNE"
        shutil.copytree(
            GAME_DIR,
            staging,
            ignore=lambda _dir, names: [n for n in names if is_excluded(pathlib.Path(n))],
        )

        with zipfile.ZipFile(OUT_ZIP, "w", zipfile.ZIP_DEFLATED) as zf:
            for file in sorted(staging.rglob("*")):
                if file.is_file():
                    zf.write(file, file.relative_to(staging.parent))

    file_count = sum(1 for _ in zipfile.ZipFile(OUT_ZIP).namelist())
    print(f"Wrote {OUT_ZIP} ({file_count} files)")


if __name__ == "__main__":
    main()
