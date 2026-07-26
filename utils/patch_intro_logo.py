#!/usr/bin/env python3

"""
patch_intro_logo.py - adds this translation's own "hebrew_adv_pixel" logo
badge below the existing "Interactive Entertainment Systems" credit line
(sprite 10) inside INTDS.HSQ's picture-resource container (see
utils/bigs_sprite.py for that format).

Chained after patch_intro_title.py: reads build/INTDS.HSQ (already carrying
the "DUNE" -> "חולית" title-sprite replacement) rather than org_files/, and
writes back to the same path -- the same "re-read the last build product"
pattern build_translation.py already uses for game/DUNEPRG.EXE across its
patch_location_name_order.py / patch_rtl_engine.py steps.

Sprite 10 is fixed at screen position x=14, y=98 (320x200 VGA mode13h),
14px left/right margin either side of its 292px width -- read directly out
of game/DUNEPRG.EXE's intro sprite position table (a (sprite_id: u16,
x: u16, y: u16) run, sprite 10's own record at file offset 0x103ff, one
entry per intro sprite, 0xFFFF-separated groups; same table
patch_intro_title.py's docstring describes finding for sprite 5,
corroborated there against sprites 8/9/10).
That leaves 200 - (98 + 13) = 89px of screen headroom below the original
text before anything would run off the bottom of the screen -- comfortably
more than what this adds. Unlike patch_intro_title.py's abandoned attempt to
retarget sprite 5's Y position by patching that table directly (see its
docstring: found to have no effect and to corrupt an unrelated later scene),
this script never touches DUNEPRG.EXE -- it only grows sprite 10's own
bitmap downward in place, the same technique validated safe for sprite 5's
rule line.

Sprite 10 shares its palette range (indices 224-239, palbase 224) with
sprites 6 and 9, and its neighboring shades (225-229) overlap what sprite 5
uses too (see patch_intro_title.py) -- none of those slots are free to
repaint without altering some other credit line's colors. Rather than
touch them, this script gives sprite 10 its own dedicated palbase (192,
picked from the file's only completely unused 16-wide index run) and
rebuilds sprite 10 from scratch on that new base: the original text pixels
are remapped 1:1 onto new nibbles carrying the exact same original RGB
values (so the credit line itself is visually unchanged), and the
now-free remaining nibbles carry the logo's own colors. This keeps every
other sprite in the file byte-identical.

Usage:
    ./patch_intro_logo.py

Always regenerates build/INTDS.HSQ in place (cheap, deterministic). Run
after patch_intro_title.py -- build_translation.py wires both in as one
combined intro-graphics step.
"""

import struct
import sys
from pathlib import Path

from PIL import Image

import bigs_sprite
import hsq

UTILS_DIR = Path(__file__).resolve().parent
REPO_ROOT = UTILS_DIR.parent
BUILD_DIR = REPO_ROOT / "build"
LOGO_PATH = REPO_ROOT / "assets" / "hebrew_adv_pixel.png"

SPRITE_NAME = "INTDS.HSQ"
CREDIT_SPRITE_INDEX = 10

# The file's only completely unused 16-wide palette-index run (see module
# docstring) -- gives sprite 10 a dedicated palette so remapping it can
# never bleed into sprites 5/6/9's shared 224-239 range.
NEW_PALBASE = 192
NIBBLE_TRANSPARENT = 0

# Sprite 10's own width (292) is left untouched -- only its height grows,
# same technique patch_intro_title.py validated for sprite 5's rule line.
GAP_ROWS = 4  # blank rows between the credit text and the logo

# The logo source is scaled to span the credit line's own width, so it
# reads as one continuous block with the text above it. BOX (a plain area
# average, same choice patch_intro_title.py's own supersample-then-downsample
# step makes) rather than LANCZOS -- LANCZOS's negative-lobe ringing on this
# flat-color, hard-edged badge was overshooting alpha/color values right at
# letter and border edges, which the nearest-color quantization below then
# snapped to the *wrong* color -- visible in-game as small holes punched
# into letter strokes and stray-color fringes along the box border.
LOGO_RESAMPLE = Image.BOX

ALPHA_OPAQUE_THRESHOLD = 128


def _color_distance(a, b):
    return sum((x - y) ** 2 for x, y in zip(a, b))


def load_logo_colors():
    """Returns the sorted list of distinct opaque RGB colors actually used
    in assets/hebrew_adv_pixel.png (excluding transparent pixels). It's a
    flat pixel-art badge with no pre-existing anti-aliasing ramp, so this
    is normally just a handful of colors -- read directly from the file
    rather than hardcoded, so a re-export/recolor of the artwork (as
    happened once already: the border went from near-black to a brown that
    matches the credit text's own palette) doesn't silently keep quantizing
    against a stale color list.
    """
    img = Image.open(LOGO_PATH).convert("RGBA")
    colors = {px[:3] for px in img.getdata() if px[3] >= ALPHA_OPAQUE_THRESHOLD}
    return sorted(colors)


def load_remapped_credit_grid(dec):
    """Decodes sprite 10's current pixel grid and remaps its nibbles onto
    NEW_PALBASE, preserving each pixel's exact original RGB color. Returns
    (grid, nibble_to_color) where nibble_to_color has one entry per new
    nibble actually used (excluding transparent).
    """
    _offset_A, palette, sprites = bigs_sprite.parse_sprites(dec)
    sprite = sprites[CREDIT_SPRITE_INDEX]
    old_grid = bigs_sprite.decode_sprite_pixels(dec, sprite)
    old_palbase = sprite["palbase"]

    old_nibbles = sorted({nib for row in old_grid for nib in row if nib != NIBBLE_TRANSPARENT})
    remap = {NIBBLE_TRANSPARENT: NIBBLE_TRANSPARENT}
    nibble_to_color = {}
    for new_nib, old_nib in enumerate(old_nibbles, start=1):
        remap[old_nib] = new_nib
        nibble_to_color[new_nib] = palette[(old_palbase + old_nib) & 0xFF]

    new_grid = [[remap[nib] for nib in row] for row in old_grid]
    return new_grid, nibble_to_color, sprite["width"]


def build_logo_grid(target_width, color_to_nibble):
    """Loads and quantizes the logo into a nibble grid `target_width` wide
    (aspect ratio preserved), using color_to_nibble (keyed by the exact RGB
    triples load_logo_colors() found) for its opaque colors.
    """
    img = Image.open(LOGO_PATH).convert("RGBA")
    target_height = round(img.height * target_width / img.width)
    img = img.resize((target_width, target_height), LOGO_RESAMPLE)

    colors = list(color_to_nibble)
    px = img.load()
    grid = [[NIBBLE_TRANSPARENT] * target_width for _ in range(target_height)]
    for y in range(target_height):
        for x in range(target_width):
            r, g, b, a = px[x, y]
            if a < ALPHA_OPAQUE_THRESHOLD:
                continue
            color = min(colors, key=lambda c: _color_distance((r, g, b), c))
            grid[y][x] = color_to_nibble[color]
    return grid


def build_credit_replacement(dec):
    """Returns (grid, width, height, palbase, palette) for the full sprite
    10 replacement: the original credit text (remapped, unchanged colors),
    a gap, and the logo badge below it.
    """
    text_grid, nibble_to_color, width = load_remapped_credit_grid(dec)
    text_h = len(text_grid)

    logo_colors = load_logo_colors()
    free_nibbles = 15 - max(nibble_to_color)  # nibbles 1-15 minus what the text already used
    assert len(logo_colors) <= free_nibbles, (
        f"logo has {len(logo_colors)} distinct colors, but only {free_nibbles} "
        f"nibbles are free alongside the credit text's own colors"
    )

    next_nibble = max(nibble_to_color) + 1
    color_to_nibble = {}
    for rgb in logo_colors:
        color_to_nibble[rgb] = next_nibble
        nibble_to_color[next_nibble] = rgb
        next_nibble += 1

    logo_grid = build_logo_grid(width, color_to_nibble)
    logo_h = len(logo_grid)

    height = text_h + GAP_ROWS + logo_h
    grid = [[NIBBLE_TRANSPARENT] * width for _ in range(height)]
    for y, row in enumerate(text_grid):
        grid[y] = row
    for y, row in enumerate(logo_grid):
        grid[text_h + GAP_ROWS + y] = row

    palette = {NEW_PALBASE: (0, 0, 0)}  # nibble 0 (transparent) -- color unused by the engine
    for nib, rgb in nibble_to_color.items():
        palette[NEW_PALBASE + nib] = rgb
    return grid, width, height, NEW_PALBASE, palette


def _find_palette_sentinel_pos(dec):
    """Walks the palette section's (start, count, RGB*count) blocks the same
    way bigs_sprite.parse_sprites does, and returns the byte offset of the
    FF,FF terminator pair. NOT assumed to be offset_A - 2: at least in
    INTDS.HSQ there's an unexplained 1-byte gap between the terminator and
    offset_A that parse_sprites tolerates (it locates the sprite offset
    table via offset_A directly, never cross-checking it against where the
    palette walk actually stops) -- so this must be found by walking, not
    computed from offset_A.
    """
    pos = 2
    while True:
        start, count = dec[pos], dec[pos + 1]
        if start == 0xFF and count == 0xFF:
            return pos
        n = 256 if count == 0 else count
        pos += 2 + n * 3


def insert_palette_block(dec, palette):
    """Inserts a new contiguous palette block (as produced by
    build_credit_replacement) right before the palette section's FF,FF
    terminator, and bumps offset_A to account for it. Returns the new
    decompressed buffer.
    """
    offset_A = struct.unpack_from("<H", dec, 0)[0]
    assert offset_A != 2, f"{SPRITE_NAME} unexpectedly has no palette section"

    start = min(palette)
    count = max(palette) - start + 1
    assert count == len(palette), "palette block must be contiguous"
    assert 1 <= count <= 256

    block = bytearray([start & 0xFF, count & 0xFF])
    for idx in range(start, start + count):
        r, g, b = palette[idx]
        block += bytes([(r // 4) & 0x3F, (g // 4) & 0x3F, (b // 4) & 0x3F])

    insert_pos = _find_palette_sentinel_pos(dec)
    new_dec = dec[:insert_pos] + bytes(block) + dec[insert_pos:]
    new_offset_A = offset_A + len(block)
    return struct.pack("<H", new_offset_A) + new_dec[2:]


def build(in_path=None, out_path=None):
    in_path = in_path or (BUILD_DIR / SPRITE_NAME)
    out_path = out_path or (BUILD_DIR / SPRITE_NAME)

    print(f"[intro-logo] decompressing {in_path.relative_to(REPO_ROOT)}")
    dec = hsq.decompress_bytes(in_path.read_bytes())

    print("[intro-logo] rendering logo badge under the credit line")
    grid, width, height, palbase, palette = build_credit_replacement(dec)
    dec_with_palette = insert_palette_block(dec, palette)

    print(f"[intro-logo] splicing sprite {CREDIT_SPRITE_INDEX} ({width}x{height})")
    new_block = bigs_sprite.encode_sprite(grid, width, height, palbase)
    new_dec = bigs_sprite.splice_sprite(dec_with_palette, CREDIT_SPRITE_INDEX, new_block)

    print(f"[intro-logo] recompressing -> {out_path.relative_to(REPO_ROOT)}")
    recompressed = hsq.compress_bytes(new_dec)
    assert hsq.decompress_bytes(recompressed) == new_dec, "recompression round-trip mismatch"

    BUILD_DIR.mkdir(exist_ok=True)
    out_path.write_bytes(recompressed)
    return out_path, height


def main():
    if not (BUILD_DIR / SPRITE_NAME).exists():
        sys.exit(f"{BUILD_DIR / SPRITE_NAME} not found -- run patch_intro_title.py first")
    build()


if __name__ == "__main__":
    main()
