#!/usr/bin/env python3

"""
build_translation.py - single entry point for producing a translated build.

Usage:
    ./build_translation.py

Preconditions: copy the game into game/ before running (never committed to
this repo). The pipeline then:

  0. verifies/repairs org_files/ (the unmodified originals) by content hash,
     pulling from game/ if a canonical file is missing or doesn't match
  1. builds the Hebrew font once (skipped on later runs if build/DUNECHAR.HSQ
     already exists; use --rebuild-font to force)
  2. extracts each <NAME>.TXT (English reference text) from org_files/ if
     not already present in tmp/ (regenerated with utils/hsq -d + utils/tu -u)
  3. builds every translated phrase/command file (always re-run)
  4. copies the results from build/ into game/, overwriting the originals
"""

import argparse
import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

import translate_phrase

UTILS_DIR = Path(__file__).resolve().parent
REPO_ROOT = UTILS_DIR.parent
ORG_FILES_DIR = REPO_ROOT / "org_files"
GAME_DIR = REPO_ROOT / "game"
BUILD_DIR = REPO_ROOT / "build"
TMP_DIR = REPO_ROOT / "tmp"
FONT_PNG_DIR = REPO_ROOT / "font_png"
HSQ_BIN = UTILS_DIR / "hsq"
TU_BIN = UTILS_DIR / "tu"
FONT_BIN = UTILS_DIR / "font.py"

GAME_SENTINEL = "DUNEPRG.EXE"

# Known-good md5sums for the four unmodified originals this pipeline replaces.
EXPECTED_MD5 = {
    "COMMAND1.HSQ": "dc5615a5399182e462ad25c69cb53ffe",
    "DUNECHAR.HSQ": "e28fc15009f666f7e3dc34c0b969f7a3",
    "PHRASE11.HSQ": "5b12b3ca83fc0cd3c90f9a8958f4efab",
    "PHRASE12.HSQ": "46150b9b41dc8528602e422c20a86a9c",
}

# Font glyph positions loaded from font_png/, ported from load_heb_font.sh.
FONT_POSITIONS = [65] + list(range(66, 92)) + [163, 167, 175] + list(range(193, 220)) + \
    [34, 35, 39, 45] + list(range(48, 58))


def run(cmd):
    print("+", " ".join(str(c) for c in cmd))
    subprocess.run(cmd, check=True)


def binary_runs(path):
    """Return True if the compiled binary at path can actually execute here
    (e.g. not built for a different CPU architecture)."""
    try:
        subprocess.run([str(path), "--help"], capture_output=True)
        return True
    except OSError:
        return False


def ensure_native_binaries():
    """Rebuild utils/hsq and utils/tu from source if the committed binaries
    can't run on this machine (e.g. they were built for a different CPU
    architecture). Uses the existing utils/Makefile -- never hand-rolls the
    compiler invocation.
    """
    stale = [b for b in (HSQ_BIN, TU_BIN) if not binary_runs(b)]
    if not stale:
        print("  utils/hsq, utils/tu: OK (native)")
        return

    names = ", ".join(b.name for b in stale)
    print(f"  {names}: won't run on this machine, rebuilding from source")
    # make only checks file existence/mtime, not whether a binary actually
    # runs -- remove the stale ones first so it's forced to relink them.
    for b in stale:
        b.unlink()
        obj = b.with_suffix(".o")
        if obj.exists():
            obj.unlink()
    run(["make", "-C", str(UTILS_DIR)])

    still_broken = [b for b in stale if not binary_runs(b)]
    if still_broken:
        sys.exit(
            f"Rebuilt {', '.join(b.name for b in still_broken)} but it still "
            f"won't run. Check that a C compiler (gcc/clang) is installed."
        )
    print(f"  rebuilt {names} for this machine")


def md5sum(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def check_game_dir_populated():
    if not GAME_DIR.exists() or not (GAME_DIR / GAME_SENTINEL).exists():
        sys.exit(
            f"game/ is missing or not populated (expected {GAME_SENTINEL}). "
            f"Copy the game files into {GAME_DIR} first."
        )


def ensure_org_files():
    """Verify org_files/<name>.HSQ against its expected md5, repairing from
    game/ (any filename) if missing or mismatched. Never trusts a filename
    alone -- only a matching hash makes a file canonical.
    """
    ORG_FILES_DIR.mkdir(exist_ok=True)
    game_candidates = [p for p in GAME_DIR.iterdir() if p.is_file()] if GAME_DIR.exists() else []
    game_hashes = None  # computed lazily, only if actually needed

    for name, expected in EXPECTED_MD5.items():
        target = ORG_FILES_DIR / name
        if target.exists() and md5sum(target) == expected:
            print(f"  org_files/{name}: OK")
            continue

        if target.exists():
            print(f"  org_files/{name}: hash mismatch, re-deriving from game/")
        else:
            print(f"  org_files/{name}: missing, deriving from game/")

        if game_hashes is None:
            game_hashes = {p: md5sum(p) for p in game_candidates}

        match = next((p for p, h in game_hashes.items() if h == expected), None)
        if match is None:
            sys.exit(
                f"Cannot find an unmodified copy of {name} (md5 {expected}) "
                f"in org_files/ or game/. Restore it manually before continuing."
            )
        shutil.copy2(match, target)
        print(f"    recovered from {match.relative_to(REPO_ROOT)} -> org_files/{name}")


def build_font(rebuild=False):
    out_hsq = BUILD_DIR / "DUNECHAR.HSQ"
    if out_hsq.exists() and not rebuild:
        print(f"[font] {out_hsq} already exists, skipping (use --rebuild-font to force)")
        return out_hsq

    BUILD_DIR.mkdir(exist_ok=True)
    TMP_DIR.mkdir(exist_ok=True)
    raw_bin = TMP_DIR / "DUNECHAR.BIN"
    tmp_bin = TMP_DIR / "TMPCHAR.BIN"
    before_png = TMP_DIR / "DUNECHAR_before.png"
    after_png = TMP_DIR / "DUNECHAR_after.png"

    print(f"[font] decompressing org_files/DUNECHAR.HSQ -> {raw_bin}")
    run([str(HSQ_BIN), "-d", str(ORG_FILES_DIR / "DUNECHAR.HSQ"), "-o", str(raw_bin)])

    print(f"[font] dumping original glyphs -> {before_png}")
    dump_font_png(raw_bin, before_png)

    current = raw_bin
    for pos in FONT_POSITIONS:
        png = FONT_PNG_DIR / f"{pos}.png" if pos not in range(65, 92) else FONT_PNG_DIR / f"{pos}L.png"
        run([str(FONT_BIN), "--load", str(png), "--output", str(tmp_bin), "--position", str(pos), str(current)])
        current = tmp_bin

    print(f"[font] dumping modified glyphs -> {after_png}")
    dump_font_png(tmp_bin, after_png)

    print(f"[font] compressing {tmp_bin} -> {out_hsq}")
    run([str(HSQ_BIN), "-c", str(tmp_bin), "-o", str(out_hsq)])
    return out_hsq


def dump_font_png(font_bin, out_png):
    """Render font_bin's glyph table to a PNG for visual before/after comparison.

    font.py's --dump takes a (discarded) value only to flag dump mode, and
    reads the actual glyph-table bytes from the positional input file -- see
    utils/font.py's argparse setup for VerifyDump/VerifyLoad.
    """
    run([str(FONT_BIN), str(font_bin), "--dump", "x", "--output", str(out_png)])


def ensure_english_txt(name):
    """Regenerate tmp/<name>.TXT from org_files/<name>.HSQ if not already
    present. This is the same content originally shipped in the game --
    always derived, never edited -- so it's never committed and doesn't
    need to live at the repo root.
    """
    english_path = TMP_DIR / f"{name}.TXT"
    if english_path.exists():
        return english_path

    TMP_DIR.mkdir(exist_ok=True)
    raw_bin = TMP_DIR / f"{name}_ORIG.BIN"
    print(f"[english] extracting {name}.TXT from org_files/{name}.HSQ")
    run([str(HSQ_BIN), "-d", str(ORG_FILES_DIR / f"{name}.HSQ"), "-o", str(raw_bin)])
    run([str(TU_BIN), "-u", str(raw_bin), str(english_path)])
    return english_path


def build_phrases():
    outputs = []
    for name, no_split in [("PHRASE11", False), ("PHRASE12", False), ("COMMAND1", True)]:
        heb_path = REPO_ROOT / "translations" / f"{name}.HEB"
        english_path = ensure_english_txt(name)
        outputs.append(translate_phrase.build_phrase(name, heb_path, english_path, no_split=no_split))
    return outputs


def install_to_game(built_files):
    for path in built_files:
        dest = GAME_DIR / path.name
        shutil.copy2(path, dest)
        print(f"[install] {path.relative_to(REPO_ROOT)} -> {dest.relative_to(REPO_ROOT)}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rebuild-font", action="store_true", help="rebuild the Hebrew font even if build/DUNECHAR.HSQ exists")
    args = parser.parse_args()

    check_game_dir_populated()

    print("[0/4] checking utils/hsq, utils/tu run on this machine")
    ensure_native_binaries()

    print("[1/4] verifying org_files/")
    ensure_org_files()

    print("[2/4] building Hebrew font")
    font_hsq = build_font(rebuild=args.rebuild_font)

    print("[3/4] building translated phrase/command files")
    phrase_files = build_phrases()  # extracts each <NAME>.TXT into tmp/ as needed

    print("[4/4] installing into game/")
    install_to_game([font_hsq] + phrase_files)

    print("Done.")


if __name__ == "__main__":
    main()
