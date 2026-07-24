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
HSQ_BIN = [sys.executable, str(UTILS_DIR / "hsq.py")]
TU_BIN = [sys.executable, str(UTILS_DIR / "tu.py")]
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

# Blanked to hide the engine's hardcoded English ordinal-day-suffix letters
# ("1st"/"2nd"/"3rd"/"4th") that get force-written onto COMMAND1's day
# counter -- see command1_ordinal_suffix_injection memory. Only these six
# lowercase ASCII codes are affected; nothing else in the translated text
# uses lowercase Latin letters.
FONT_POSITIONS += [ord(c) for c in "thsndr"]

# "~" (0x7e, otherwise unused anywhere in the translated text) is a second,
# narrower forced-blank glyph -- 2px wide, vs. the regular space's 4px and
# "#"'s 5px -- for fine-grained pixel-level width trimming where neither of
# those is precise enough (e.g. COMMAND1's save-slot rows overflowing their
# box by only 1-2px at triple-digit day counts). DUNECHAR stores two
# independent glyph tables in one file -- positions 0-127 (9px tall, "large"
# font) and 128-255 (7px tall, "small" font, used by menu contexts like the
# save-slot list) -- so any byte value used in a small-font context needs
# its *own* override at position+128, or the renderer falls back to
# whatever the original small-font glyph was (this bit us once: byte 126
# alone left the save-slot "~" showing the original small-font tilde glyph,
# unblanked and wider than intended). Hence both 126 and 126+128=254 here.
FONT_POSITIONS += [ord("~"), ord("~") + 128]


def run(cmd):
    print("+", " ".join(str(c) for c in cmd))
    subprocess.run(cmd, check=True)


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
    run(HSQ_BIN + ["-d", str(ORG_FILES_DIR / "DUNECHAR.HSQ"), "-o", str(raw_bin)])

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
    run(HSQ_BIN + ["-c", str(tmp_bin), "-o", str(out_hsq)])
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
    run(HSQ_BIN + ["-d", str(ORG_FILES_DIR / f"{name}.HSQ"), "-o", str(raw_bin)])
    run(TU_BIN + ["-u", str(raw_bin), str(english_path)])
    return english_path


# PHRASE12 lines 422-425 (0-based 421-424) are encyclopedia entries, not
# spoken dialogue -- they use a wider box than the dialogue box's
# LINE_LENGTH, so word-wrap them at PHRASE12_WIDE_LEN instead, with a
# forced line break at each sentence boundary.
PHRASE12_WIDE_LINES = {421, 422, 423, 424}
PHRASE12_WIDE_LEN = 200


def build_phrases():
    outputs = []
    for name, no_split, wide_lines, wide_len in [
        ("PHRASE11", False, None, None),
        ("PHRASE12", False, PHRASE12_WIDE_LINES, PHRASE12_WIDE_LEN),
        ("COMMAND1", True, None, None),
    ]:
        heb_path = REPO_ROOT / "translations" / f"{name}.HEB"
        english_path = ensure_english_txt(name)
        outputs.append(translate_phrase.build_phrase(
            name, heb_path, english_path, no_split=no_split,
            wide_lines=wide_lines, wide_len=wide_len))
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
