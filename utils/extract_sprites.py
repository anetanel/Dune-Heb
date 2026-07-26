#!/usr/bin/env python3

"""
extract_sprites.py - dump every "bigs"-format picture-resource sprite found
in the game's .HSQ files as individually named PNGs, for browsing/cataloging
the game's art assets. Read-only: never writes into org_files/, translations/,
or game/ (see CLAUDE.md's directory-roles section) -- output goes to
tmp/sprites/, since it's fully regenerable from the game files.

Not every .HSQ is a picture-resource file, and not every picture-resource
file's declared sprite count turns out to be trustworthy all the way through
-- see bigs_sprite.py's docstring for the container format itself, and the
notes below for the extra tolerance this script adds on top of it.

Detection: for each source .HSQ, decompress it and check whether the buffer
parses as bigs_sprite's format (offset_A + optional palette + sprite offset
table). Many files plainly don't -- phrase/command text tables, DUNECHAR's
font table (already handled separately by font.py), sound/music resources
(DUNEMID/DUNEPCS/DUNEADL/DUNESDB/SD1-SDB), and a handful of location-establishing
picture files paired with .AGD/.M32 companions (ARRAKIS, BAGDAD, CONDIT,
MORNING, SEKENCE, SIETCHM, WARSONG, WATER, WORMINTR, WORMSUIT, DUNEAGD) whose
header doesn't match this container at all -- some other format we haven't
reverse-engineered. All of these are skipped and listed in the report rather
than guessed at.

Truncation tolerance: bigs_sprite.py's docstring notes the offset table's
own length is inferred from its first entry (first_off // 2), on the
assumption every entry up to that point is a real sprite pointer. That
assumption holds for the small examples it was validated against (INTDS)
but not universally -- several character-portrait files (e.g. ATTACK,
BARO, CHAN) have a handful of trailing table entries that don't decode to
a plausible image (width/height of 0, or pixel data that runs off the end
of the buffer) once the real sprites are exhausted, presumably pointing at
some other kind of resource (hotspot/sound data?) packed in the same table
that this script doesn't understand. Rather than fail the whole file, this
script stops at the first such entry and keeps everything before it.

Palette: most matching files embed their own palette. A handful (offset_A
== 2, e.g. DEATH2/DEATH3/SHAI2 riding on the preceding DEATH1/SHAI palette)
declare none, relying on whatever the DOS engine last loaded from an earlier
picture -- which one exactly isn't recoverable from the file alone. Rather
than guess, those sprites are rendered on a fixed grayscale ramp (one shade
per 4-bit pixel value) and flagged in the report and filename.
"""

import argparse
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bigs_sprite
import hsq

from PIL import Image

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GRAY_RAMP = {nib: (nib * 17, nib * 17, nib * 17) for nib in range(16)}

# Known to decompress but aren't the bigs picture-resource format -- listed
# so the report can say *why* rather than just "no match".
KNOWN_NON_SPRITE = {
    "DUNECHAR": "font glyph table (see font.py, handled separately)",
    "PHRASE11": "phrase text table", "PHRASE12": "phrase text table",
    "PHRASE21": "phrase text table", "PHRASE22": "phrase text table",
    "PHRASE31": "phrase text table", "PHRASE32": "phrase text table",
    "COMMAND1": "command text table", "COMMAND2": "command text table",
    "COMMAND3": "command text table",
    "DIALOGUE": "text table", "VERBIN": "text/binary table",
    "DUNEMID": "MIDI music data", "DUNEADL": "AdLib music data",
    "DUNEPCS": "PC speaker music data", "DUNESDB": "sound data",
    "SD1": "sound data", "SD2": "sound data", "SD3": "sound data",
    "SD4": "sound data", "SD5": "sound data (also fails HSQ checksum)",
    "SD6": "sound data", "SD7": "sound data", "SD8": "sound data",
    "SD9": "sound data", "SDA": "sound data", "SDB": "sound data",
    "DUNEVGA": "VGA driver/detection stub, not image data",
    "DUNE386": "386 driver/detection stub, not image data",
    "MAP": "map/world-state data, not image data",
    "MAP2": "map/world-state data, not image data",
    "GLOBDATA": "globe/world-state data, not image data",
    "ARRAKIS": "unrecognized container (paired with .AGD/.M32)",
    "BAGDAD": "unrecognized container (paired with .AGD/.M32)",
    "CONDIT": "unrecognized container",
    "DUNEAGD": "unrecognized container",
    "MORNING": "unrecognized container (paired with .AGD/.M32)",
    "SEKENCE": "unrecognized container (paired with .AGD/.M32)",
    "SIETCHM": "unrecognized container (paired with .AGD/.M32)",
    "WARSONG": "unrecognized container (paired with .AGD/.M32)",
    "WATER": "unrecognized container (paired with .AGD/.M32)",
    "WORMINTR": "unrecognized container (paired with .AGD/.M32)",
    "WORMSUIT": "unrecognized container (paired with .AGD/.M32)",
}


def source_path(name):
    """org_files/ has the pristine original for anything the translation
    pipeline touches (currently just INTDS/DUNECHAR among picture files);
    game/ is untouched-original for everything else."""
    org = os.path.join(REPO_ROOT, "org_files", f"{name}.HSQ")
    if os.path.exists(org):
        return org
    return os.path.join(REPO_ROOT, "game", f"{name}.HSQ")


def find_sprite_table(dec):
    """Tolerant bigs-format detector/parser. Returns None if this buffer
    doesn't look like the format at all; otherwise returns (offset_A,
    palette, sprites) like bigs_sprite.parse_sprites, but with the sprite
    list truncated at the first entry that fails a basic sanity check
    (see module docstring re: trailing garbage table entries)."""
    if len(dec) < 4:
        return None
    offset_A = struct.unpack_from("<H", dec, 0)[0]
    palette = {}
    if offset_A == 2:
        base = 2
    else:
        if offset_A < 2 or offset_A >= len(dec):
            return None
        pos = 2
        while True:
            if pos + 2 > len(dec):
                return None
            start, count = dec[pos], dec[pos + 1]
            pos += 2
            if start == 0xFF and count == 0xFF:
                break
            n = 256 if count == 0 else count
            for i in range(n):
                if pos + i * 3 + 3 > len(dec):
                    return None
                r, g, b = dec[pos + i * 3], dec[pos + 1 + i * 3], dec[pos + 2 + i * 3]
                palette[(start + i) & 0xFF] = (r * 4, g * 4, b * 4)
            pos += n * 3
            if pos > offset_A:
                return None
        base = offset_A

    if base + 2 > len(dec):
        return None
    first_off = struct.unpack_from("<H", dec, base)[0]
    if first_off == 0 or first_off % 2 != 0:
        return None
    n = first_off // 2
    if n < 1 or n > 300 or base + n * 2 > len(dec):
        return None
    offsets = [struct.unpack_from("<H", dec, base + i * 2)[0] for i in range(n)]

    sprites = []
    prev = -1
    for off in offsets:
        if off <= prev:
            break
        p = base + off
        if p + 4 > len(dec):
            break
        size_x, comp, size_y, palbase = dec[p], dec[p + 1], dec[p + 2], dec[p + 3]
        width = size_x + ((comp & 0x7F) << 8)
        compressed = bool(comp & 0x80)
        if width == 0 or width > 400 or size_y == 0 or size_y > 250:
            break
        sprites.append({
            "pos": p + 4, "width": width, "height": size_y,
            "compressed": compressed, "palbase": palbase,
        })
        prev = off

    if not sprites:
        return None
    return offset_A, palette, sprites


def render_sprite(dec, sprite, palette):
    grid = bigs_sprite.decode_sprite_pixels(dec, sprite)
    img = Image.new("RGB", (sprite["width"], sprite["height"]))
    px = img.load()
    palbase = sprite["palbase"]
    for y, row in enumerate(grid):
        for x, nib in enumerate(row):
            if palette:
                color = palette.get((palbase + nib) & 0xFF, (255, 0, 255))
            else:
                color = GRAY_RAMP[nib]
            px[x, y] = color
    return img


def extract_one(name, dec, out_dir):
    found = find_sprite_table(dec)
    if found is None:
        return None
    offset_A, palette, sprites = found
    file_dir = os.path.join(out_dir, name)
    os.makedirs(file_dir, exist_ok=True)
    ok, failed = 0, 0
    for idx, sprite in enumerate(sprites):
        tag = "" if palette else "_gray"
        fname = f"{idx:03d}_{sprite['width']}x{sprite['height']}{tag}.png"
        try:
            img = render_sprite(dec, sprite, palette)
        except IndexError:
            failed += 1
            continue
        img.save(os.path.join(file_dir, fname))
        ok += 1
    return {
        "declared": len(sprites), "extracted": ok, "decode_failed": failed,
        "has_palette": bool(palette), "offset_A": offset_A,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("names", nargs="*",
                         help="specific file basenames to extract (default: all game/*.HSQ)")
    parser.add_argument("--out-dir", default=os.path.join(REPO_ROOT, "tmp", "sprites"))
    args = parser.parse_args()

    if args.names:
        names = args.names
    else:
        game_dir = os.path.join(REPO_ROOT, "game")
        names = sorted(
            fn[:-4] for fn in os.listdir(game_dir) if fn.upper().endswith(".HSQ")
        )

    os.makedirs(args.out_dir, exist_ok=True)
    report_lines = []
    matched, skipped_known, skipped_unknown, decompress_failed = [], [], [], []

    for name in names:
        path = source_path(name)
        if not os.path.exists(path):
            continue
        with open(path, "rb") as f:
            raw = f.read()
        try:
            dec = hsq.decompress_bytes(raw)
        except Exception as e:
            decompress_failed.append((name, str(e)))
            continue

        result = extract_one(name, dec, args.out_dir)
        if result is None:
            if name in KNOWN_NON_SPRITE:
                skipped_known.append((name, KNOWN_NON_SPRITE[name]))
            else:
                skipped_unknown.append(name)
            continue

        matched.append((name, result))
        pal_note = "own palette" if result["has_palette"] else "NO PALETTE (grayscale fallback)"
        note = f"{result['extracted']}/{result['declared']} sprites, {pal_note}"
        if result["decode_failed"]:
            note += f", {result['decode_failed']} failed to decode"
        print(f"{name}: {note}")

    report_lines.append(f"Sprite extraction report -- {len(matched)} matched files\n")
    report_lines.append("=== Matched (bigs picture-resource format) ===")
    total_sprites = 0
    for name, r in matched:
        total_sprites += r["extracted"]
        pal_note = "own palette" if r["has_palette"] else "NO PALETTE (rendered grayscale)"
        line = f"  {name}: {r['extracted']}/{r['declared']} sprites, {pal_note}"
        if r["decode_failed"]:
            line += f", {r['decode_failed']} decode failures skipped"
        if r["extracted"] < r["declared"]:
            line += "  [truncated: trailing table entries didn't look like real sprites]"
        report_lines.append(line)
    report_lines.append(f"\nTotal sprites extracted: {total_sprites}\n")

    report_lines.append("=== Skipped: known non-sprite format ===")
    for name, why in sorted(skipped_known):
        report_lines.append(f"  {name}: {why}")

    if skipped_unknown:
        report_lines.append("\n=== Skipped: unrecognized (not in KNOWN_NON_SPRITE) ===")
        for name in sorted(skipped_unknown):
            report_lines.append(f"  {name}")

    if decompress_failed:
        report_lines.append("\n=== HSQ decompression failed ===")
        for name, err in decompress_failed:
            report_lines.append(f"  {name}: {err}")

    report_path = os.path.join(args.out_dir, "EXTRACTION_REPORT.txt")
    with open(report_path, "w") as f:
        f.write("\n".join(report_lines) + "\n")
    print(f"\n{total_sprites} sprites from {len(matched)} files -> {args.out_dir}")
    print(f"Report: {report_path}")


if __name__ == "__main__":
    main()
