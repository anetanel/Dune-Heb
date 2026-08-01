#!/usr/bin/env python3

"""
patch_final_credits.py - replaces two whole-phrase picture sprites inside
FINAL.HSQ's picture-resource container (see utils/bigs_sprite.py for that
format) with Hebrew translations: sprite 4 ("THE END", originally 142x14)
and sprite 5 ("with" / "(in order of Appearance)", two lines, originally
189x51) -- the outro screens that play right before the credit-name
sequence patch_generic_letters.py handles.

Both sprites share palbase=159: nibble 0 is undefined in the palette (the
engine's universal transparent-nibble convention, same as every other
"bigs" sprite this pipeline touches) and nibbles 1-14 map to palette
indices 160-173, a 14-step gold ramp -- confirmed to be the *exact same*
RGB values as GENERIC.HSQ's credit-name letters (palbase=239, indices
240-253), just mounted at a different palette base in this file.

Unlike patch_generic_letters.py (which composes individual letter glyphs
and derives their gold shading from row position against a single shared
baseline), these are whole hand-authored images with no such shared
per-letter structure -- "with" and "(in order of Appearance)" are two
differently-sized lines in one sprite, not a run of same-height glyphs.
So this script doesn't render or shade anything itself: it just quantizes
each input PNG's own colors against the existing 14-shade gold ramp via
nearest-color match (same technique patch_intro_logo.py uses for its
hand-authored logo badge) and splices the result in at the PNG's own
pixel dimensions.

Input PNGs (RGBA; alpha >= ALPHA_OPAQUE_THRESHOLD counts as opaque ink,
below that is transparent background) belong at:
    final_png/04_the_end.png
    final_png/05_with_in_order_of_appearance.png
Either can be dropped in independently -- build() replaces whichever of
the two exists and leaves the other sprite as the untouched original, so
this doesn't need to be done in one pass.
Any pixel size works -- splice_sprite() doesn't require matching the
original 142x14 / 189x51 -- but FINAL.HSQ's on-screen sprite positioning
hasn't been investigated yet (unlike INTDS.HSQ's sprites 5/10, whose
fixed top-left screen anchors patch_intro_title.py/patch_intro_logo.py
already found and documented). A very different size or aspect ratio
might shift where it lands or get cropped; verify live in-game (dosbox-mcp)
after the first real swap, same as every other sprite replacement in this
pipeline.

Sprites are always encoded uncompressed (compressed=False) -- see
bigs_sprite.py's module docstring and patch_generic_letters.py's own note
on this: RLE-compressed re-encoding of this same "bigs" format was
confirmed live to crash the real engine even for pixel-unchanged content
(a decode divergence from the real assembly never root-caused at the
instruction level); uncompressed is the validated-safe path for any
sprite this pipeline regenerates, not just GENERIC.HSQ's letters.

Usage:
    ./patch_final_credits.py

Always regenerates build/FINAL.HSQ (cheap, deterministic -- same policy
as every other patch_*.py script here, not cached), plus
tmp/FINAL_{4,5}_before.png / tmp/FINAL_{4,5}_after.png for visual comparison
(true pixel size, real alpha transparency).
"""

import sys
from pathlib import Path

from PIL import Image

import bigs_sprite
import hsq

UTILS_DIR = Path(__file__).resolve().parent
REPO_ROOT = UTILS_DIR.parent
ORG_FILES_DIR = REPO_ROOT / "org_files"
BUILD_DIR = REPO_ROOT / "build"
TMP_DIR = REPO_ROOT / "tmp"
# Hand-authored replacement art, same "committed human-edited source"
# role as generic_png/ and font_png/ -- not regenerated output.
FINAL_PNG_DIR = REPO_ROOT / "final_png"

SPRITE_NAME = "FINAL.HSQ"
THE_END_INDEX = 4
WITH_INDEX = 5
PALBASE = 159
NIBBLE_TRANSPARENT = 0
GRADIENT_LEVELS = 14
ALPHA_OPAQUE_THRESHOLD = 128

REPLACEMENTS = {
    THE_END_INDEX: FINAL_PNG_DIR / "04_the_end.png",
    WITH_INDEX: FINAL_PNG_DIR / "05_with_in_order_of_appearance.png",
}


def _color_distance(a, b):
    return sum((x - y) ** 2 for x, y in zip(a, b))


def build_grid_from_png(path, palette):
    """Quantizes an RGBA PNG's own colors against the existing 14-shade
    gold ramp (palette indices PALBASE+1..PALBASE+14) via nearest-color
    match. Returns (width, height, nibble_grid).
    """
    img = Image.open(path).convert("RGBA")
    width, height = img.size
    px = img.load()

    ramp = {nib: palette[(PALBASE + nib) & 0xFF] for nib in range(1, GRADIENT_LEVELS + 1)}

    grid = [[NIBBLE_TRANSPARENT] * width for _ in range(height)]
    for y in range(height):
        for x in range(width):
            r, g, b, a = px[x, y]
            if a < ALPHA_OPAQUE_THRESHOLD:
                continue
            nib = min(ramp, key=lambda n: _color_distance((r, g, b), ramp[n]))
            grid[y][x] = nib
    return width, height, grid


def render_sprite_png(dec, palette, index, out_path, scale=1):
    """Renders one sprite as a true-alpha PNG (nibble 0 -> fully
    transparent) for visual reference/comparison -- same purpose as
    patch_generic_letters.py's render_letters_png(), for a single sprite.
    """
    _offset_A, _pal_unused, sprites = bigs_sprite.parse_sprites(dec)
    sprite = sprites[index]
    grid = bigs_sprite.decode_sprite_pixels(dec, sprite)
    w, h = sprite["width"], sprite["height"]
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    px = img.load()
    for y, row in enumerate(grid):
        for x, nib in enumerate(row):
            if nib != NIBBLE_TRANSPARENT:
                rgb = palette.get((sprite["palbase"] + nib) & 0xFF, (255, 0, 255))
                px[x, y] = rgb + (255,)
    if scale != 1:
        img = img.resize((w * scale, h * scale), Image.NEAREST)
    out_path.parent.mkdir(exist_ok=True)
    img.save(out_path)


def build(org_path=None, out_path=None):
    org_path = org_path or (ORG_FILES_DIR / SPRITE_NAME)
    out_path = out_path or (BUILD_DIR / SPRITE_NAME)

    print(f"[final-credits] decompressing {org_path.relative_to(REPO_ROOT)}")
    dec = hsq.decompress_bytes(org_path.read_bytes())
    _offset_A, palette, _sprites = bigs_sprite.parse_sprites(dec)
    original_size = len(dec)

    for index in sorted(REPLACEMENTS):
        png_path = REPLACEMENTS[index]
        render_sprite_png(dec, palette, index, TMP_DIR / f"FINAL_{index}_before.png")
        if not png_path.exists():
            print(f"  sprite {index}: {png_path.relative_to(REPO_ROOT)} not found, leaving original untouched")
            continue
        width, height, grid = build_grid_from_png(png_path, palette)
        new_block = bigs_sprite.encode_sprite(grid, width, height, PALBASE, compressed=False)
        dec = bigs_sprite.splice_sprite(dec, index, new_block)
        print(f"  sprite {index} ({png_path.name}) -> {width}x{height}")

    print(f"[final-credits] recompressing -> {out_path.relative_to(REPO_ROOT)}")
    recompressed = hsq.compress_bytes(dec)
    assert hsq.decompress_bytes(recompressed) == dec, "recompression round-trip mismatch"

    _offset_A, palette_after, _sprites = bigs_sprite.parse_sprites(dec)
    for index in sorted(REPLACEMENTS):
        render_sprite_png(dec, palette_after, index, TMP_DIR / f"FINAL_{index}_after.png")

    print(f"[final-credits] decompressed size {original_size}B -> {len(dec)}B")

    BUILD_DIR.mkdir(exist_ok=True)
    out_path.write_bytes(recompressed)
    return out_path


def main():
    if not (ORG_FILES_DIR / SPRITE_NAME).exists():
        sys.exit(f"{ORG_FILES_DIR / SPRITE_NAME} not found -- run build_translation.py first")
    build()


if __name__ == "__main__":
    main()
