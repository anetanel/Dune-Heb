#!/usr/bin/env python3

"""
restore_original.py - copy the unmodified originals from org_files/ back
into game/, undoing the Hebrew translation install so the game can be run
with the original English files again.
"""

import shutil
import sys
from pathlib import Path

import build_translation as bt


def main():
    bt.check_game_dir_populated()
    bt.ensure_org_files()

    for name in bt.EXPECTED_MD5:
        src = bt.ORG_FILES_DIR / name
        dest = bt.GAME_DIR / name
        shutil.copy2(src, dest)
        print(f"[restore] {src.relative_to(bt.REPO_ROOT)} -> {dest.relative_to(bt.REPO_ROOT)}")


if __name__ == "__main__":
    sys.exit(main())
