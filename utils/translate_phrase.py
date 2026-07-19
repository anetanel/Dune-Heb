#!/usr/bin/env python3

"""
translate_phrase.py - run the full Hebrew phrase-translation pipeline:

  1. take a Hebrew translation TXT (pasted from the Google Sheet)
  2. encode Hebrew letters + restore control bytes (heb_encode.encode_file)
  3. split/reverse lines for the game's renderer (utils/split.py), or just
     reverse each line as-is with --no-split (no word-wrap, no extra \\r)
  4. pack the text into a phrase binary (utils/tu -p)
  5. compress it into the game directory (utils/hsq -c)

Usage:
    ./translate_phrase.py PHRASE11 [--phrase-name PHRASE11]
    ./translate_phrase.py COMMAND1 --no-split

The Hebrew source (translations/<name>.HEB) is located automatically from
the phrase name. The English reference (tmp/<name>.TXT) is extracted
automatically from org_files/<name>.HSQ if not already present -- see
build_translation.ensure_english_txt() -- or pass --english to point at a
different file. Generated intermediates are written to tmp/, and the final
compressed file is written to build/<name>.HSQ (override with --out-dir).
"""

import argparse
import subprocess
import sys
from pathlib import Path

import heb_encode
import split as heb_split

UTILS_DIR = Path(__file__).resolve().parent
REPO_ROOT = UTILS_DIR.parent
TRANSLATIONS_DIR = REPO_ROOT / "translations"
TMP_DIR = REPO_ROOT / "tmp"
SPLIT_SCRIPT = UTILS_DIR / "split.py"
TU_BIN = [sys.executable, str(UTILS_DIR / "tu.py")]
HSQ_BIN = [sys.executable, str(UTILS_DIR / "hsq.py")]
BUILD_DIR = REPO_ROOT / "build"


def run(cmd):
    print("+", " ".join(str(c) for c in cmd))
    subprocess.run(cmd, check=True)


def reverse_only(encoded_bytes):
    """Reverse each line in place, with no word-wrap and no added \\r.

    Used for files like COMMAND1 whose entries are short UI labels: the
    game still expects each line's characters reversed, but none of
    split.py's word-wrap/line-length padding logic applies, and no line
    break should be added beyond whatever \\r the translator explicitly
    placed in the source (already present in encoded_bytes from step 2).

    Reversal happens at the unit level (utils/split.py's scan_units), not
    per raw byte: multi-byte control tokens (location/quantity variables,
    line-break variants) and literal digit runs must keep their internal
    byte order even as their position within the line flips, since the
    game's phrase engine reads their bytes in a fixed order.
    """
    lines = encoded_bytes.split(b"\n")
    return b"\n".join(
        b"".join(reversed(heb_split.scan_units(line))) for line in lines
    )


def build_phrase(phrase_name, heb_path, english_path, no_split=False, out_dir=None,
                  wide_lines=None, wide_len=None):
    """Run the full pipeline for one phrase/command file, returning the output path.

    wide_lines: 0-based line numbers within this file to word-wrap at
    wide_len instead of split.py's normal dialogue-box LINE_LENGTH, with a
    forced line break at every sentence boundary (bare \\r/M marker) -- for
    non-dialogue entries in a wider box embedded in an otherwise
    line-length-limited file, e.g. PHRASE12's encyclopedia lines.

    Raises SystemExit on encoding errors (mirrors the previous CLI behavior),
    so callers (including build_translation.py) get a clear abort message.
    """
    out_dir = Path(out_dir) if out_dir else BUILD_DIR
    out_dir.mkdir(exist_ok=True)
    TMP_DIR.mkdir(exist_ok=True)

    encoded_txt = TMP_DIR / f"{phrase_name}_HEB.TXT"
    split_bin = TMP_DIR / f"{phrase_name}_HEB_SPLIT.BIN"
    packed_bin = TMP_DIR / f"{phrase_name}_HEB.BIN"
    hsq_out = out_dir / f"{phrase_name}.HSQ"

    print(f"[1/4] encoding Hebrew letters + control bytes -> {encoded_txt}")
    output, errors = heb_encode.encode_file(str(english_path), str(heb_path))
    if errors:
        for e in errors:
            print(e, file=sys.stderr)
        sys.exit(f"{len(errors)} line(s) failed to encode; aborting.")
    encoded_txt.write_bytes(output)
    print(f"    wrote {len(output)} bytes")

    if no_split:
        print(f"[2/4] reversing lines (no word-wrap) -> {split_bin}")
        split_bin.write_bytes(reverse_only(encoded_txt.read_bytes()))
    else:
        print(f"[2/4] splitting/reversing lines -> {split_bin}")
        cmd = [sys.executable, str(SPLIT_SCRIPT), "--input", str(encoded_txt), "--output", str(split_bin)]
        if wide_lines:
            cmd += ["--wide-lines", ",".join(str(n) for n in sorted(wide_lines)),
                    "--wide-len", str(wide_len)]
        run(cmd)

    print(f"[3/4] packing to binary phrase file -> {packed_bin}")
    run(TU_BIN + ["-p", str(split_bin), str(packed_bin)])

    print(f"[4/4] compressing -> {hsq_out}")
    if hsq_out.exists():
        print(f"    removing existing {hsq_out}")
        hsq_out.unlink()
    run(HSQ_BIN + ["-c", str(packed_bin), "-o", str(hsq_out)])

    print(f"Done. {hsq_out} is ready.")
    return hsq_out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("name", help="phrase name, e.g. PHRASE11 (a bare name, or a path to the .HEB file)")
    parser.add_argument("--heb-file", help="Hebrew translation source (default: translations/<name>.HEB)")
    parser.add_argument("--english", help="matching original English TXT (default: extracted to tmp/<name>.TXT)")
    parser.add_argument("--phrase-name", help="base phrase name used for output filenames (default: derived from name)")
    parser.add_argument("--out-dir", help="directory for the final compressed .HSQ (default: build/)")
    parser.add_argument("--no-split", action="store_true",
                         help="reverse lines as-is instead of running split.py's word-wrap "
                              "(use for short-entry files like COMMAND1; no line breaks are added "
                              "beyond what the translator explicitly placed in the source)")
    args = parser.parse_args()

    name_path = Path(args.name)
    phrase_name = args.phrase_name or name_path.stem
    heb_path = Path(args.heb_file) if args.heb_file else (
        name_path if name_path.suffix else TRANSLATIONS_DIR / f"{phrase_name}.HEB"
    )
    if args.english:
        english_path = Path(args.english)
    else:
        import build_translation
        english_path = build_translation.ensure_english_txt(phrase_name)

    if not heb_path.exists():
        sys.exit(f"Hebrew source file not found: {heb_path}")
    if not english_path.exists():
        sys.exit(f"English reference file not found: {english_path}")

    build_phrase(phrase_name, heb_path, english_path, no_split=args.no_split, out_dir=args.out_dir)


if __name__ == "__main__":
    main()
