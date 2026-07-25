#!/usr/bin/env python3

"""
patch_intro_title.py - replaces the "DUNE" title card in the intro montage
with "חולית" (this translation's Hebrew title), by regenerating sprite 5
inside org_files/INTDS.HSQ's picture-resource container (see
utils/bigs_sprite.py for that format) and writing the result to
build/INTDS.HSQ.

Background: the boot sequence (Virgin Games -> Cryo -> title -> desert
montage, played by game/DUNEPRG.EXE, *not* game/LOGO.EXE/LOGO.HNM which is
just the animated Cryo logo) draws its studio-credit and title text as
pre-rendered picture sprites, not via the in-game DUNECHAR bitmap font that
heb_encode.py/translate_phrase.py target -- confirmed by their soft
anti-aliased letterforms (DUNECHAR is a plain blocky 1bpp-style font) and by
tracing the exact file-open sequence during boot (DOSBox-X's own
`[log] files=true` tracing) against a byte-for-byte match of the live VGA
framebuffer at the title screen. INTDS.HSQ ("INTro Data Sequence") turned out
to hold every text overlay for that sequence as separate sprites: 6="VIRGIN
GAMES", 7="presents", 8="A production from", 9="CRYO",
10="Interactive Entertainment Systems", and 5="DUNE" -- the one this script
replaces. Sprites 0-2 are background art (desert dunes, black/blue gradient)
and 3-4 more gradient bands; none of those are touched.

Sprite 5 has no palette of its own (reuses whatever's already loaded), with
palbase=225 pointing at a small local ramp already defined in INTDS.HSQ's own
palette section: index 225 = a brown/orange fallback shade (only visible if
something -- like the standalone dump.c reference decoder -- renders it
without the engine's transparency handling), 226-229 = a red anti-aliasing
ramp from bright to dark. Nibble 0 (-> index 225) is what the real engine
treats as transparent, letting the black/blue gradient sprite drawn earlier
show through -- confirmed by the shipped title showing a black background
despite sprite 5's own "background" color being brown.

The very last row of the original sprite is a solid red horizontal rule,
baked into the bitmap itself rather than drawn separately -- easy to drop by
accident when regenerating only the letters (found the hard way: an earlier
version of this script produced a title with no rule line at all until this
was noticed and the last row was force-filled with the rule color).

Sprite 5's height can safely change (from the original 27 content rows + 1
rule row = 28) as long as the rule stays on the sprite's *last* row -- taller
values up to ~38 total were tested in-game via dosbox-mcp and still compose
correctly against the fixed-position blue gradient sprite drawn after it;
much taller (52) started covering/hiding the rule line, so this isn't
unlimited headroom. 38 (37 letter rows + 1 rule row) is what shipped.

The sprite draws from a fixed top-left screen anchor (not centered/bottom-
anchored), so a taller sprite pushes its *bottom* (the rule line) further
down by exactly the added height -- confirmed by pixel-measuring the rule's
screen row across two builds of different heights (a 34px height increase
moved the rule down by exactly 34px). This is a real constraint with no
content-only workaround: a bigger font unavoidably sits lower on screen.

Do NOT try to "fix" this by patching game/DUNEPRG.EXE's title Y position.
This was attempted: live-tracing the VGA blit call chain in dosbox-mcp
(breakpoint at the offset-computation routine `DI = Y*320 + X + [cs:0x1A0]`,
called with BX=Y/DX=X) found what looked like the intro's script table --
(sprite_id: u16, x: u16, y: u16) records, 0xFFFF-separated groups -- with
sprite 5's (title) record apparently at file offset 0x1041D (y=74, x=0),
corroborated by neighboring records for sprites 8/9/10 matching live-
captured X/Y for those credit lines exactly. Despite that, patching that
y value in-game did NOT change the title's rendered position at all, and
separately corrupted an unrelated later scene (the Chani/desert-sunset zoom
several fades later showed Paul's face bleeding through in place of part of
the background) -- confirmed reproducible: rebuilding with only the taller
sprite was clean, adding the EXE patch on top reintroduced the corruption
every time. Whatever that offset actually is, it is not a safe standalone
title-position anchor and is aliased with something else the engine reads
later. If this is revisited, it needs a fresh, more rigorous static-plus-
live cross-check of that offset's true role before ever writing to it again
-- not just pattern-matching neighboring table values.

Font: fonts/AharoniCLM-Book.ttf (Culmus project's "Aharoni CLM", GPLv2 --
see fonts/AharoniCLM-LICENSE), chosen to match the bold, tall/narrow block
lettering on the actual Israeli ("Bug Games") retail release's box art,
which this translation's title deliberately echoes.

Usage:
    ./patch_intro_title.py

Always regenerates build/INTDS.HSQ (cheap, deterministic -- same spirit as
translate_phrase.py's phrase files, not cached like the font step).
"""

import struct
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

import bigs_sprite
import hsq

UTILS_DIR = Path(__file__).resolve().parent
REPO_ROOT = UTILS_DIR.parent
ORG_FILES_DIR = REPO_ROOT / "org_files"
BUILD_DIR = REPO_ROOT / "build"
FONT_PATH = REPO_ROOT / "fonts" / "AharoniCLM-Book.ttf"

SPRITE_NAME = "INTDS.HSQ"
TITLE_SPRITE_INDEX = 5
TITLE_TEXT = "חולית"

# Sprite 5's fixed palbase in INTDS.HSQ (see module docstring). Nibble 0 is
# the engine's transparent marker; 1 is the brightest red, 2-4 progressively
# darker -- used here as a small anti-aliasing ramp for the letter edges.
PALBASE = 225
NIBBLE_TRANSPARENT = 0
NIBBLE_RED_BRIGHT = 1

SPRITE_WIDTH = 320  # full VGA width, matches every other sprite in this file

# Rendering pipeline constants, tuned by eye against the real title screen in
# DOSBox-X (see repo history for the iteration -- these aren't derived from
# anything, just the values that looked right).
WORK_SCALE = 16       # supersampling factor before the final box-filter downsample
FONT_SIZE = 68 * WORK_SCALE   # effective ~68px final glyph size, confirmed to look good by eye
TARGET_WIDTH_FRAC = 0.66   # total letter+spacing width, as a fraction of the sprite width
V_STRETCH = 1.3       # vertical stretch applied to the rendered glyphs
H_STRETCH = 1.18      # horizontal stretch applied to the rendered glyphs
BINARIZE_THRESHOLD = 110   # mask threshold applied before the box downsample, for crisp edges

# Minimal anti-aliasing: mostly a hard transparent/bright split, with a single
# thin mid-shade band right at the edge to take the harshest jaggies off
# curves without going back to a full soft multi-step ramp.
NIBBLE_LEVELS = [(64, 0), (176, 3)]  # (upper bound, nibble); falls through to 1


def intensity_to_nibble(v):
    for bound, nibble in NIBBLE_LEVELS:
        if v < bound:
            return nibble
    return NIBBLE_RED_BRIGHT


def render_title_mask():
    """Renders TITLE_TEXT as a crisp black/white mask sized to fit
    SPRITE_WIDTH, letter-spaced and stretched to taste. Returns a PIL 'L'
    image (0=background, 255=ink) at final pixel size, plus the number of
    ink rows it needs (its height).
    """
    canvas_w = SPRITE_WIDTH * WORK_SCALE
    letters = list(TITLE_TEXT)
    font = ImageFont.truetype(str(FONT_PATH), FONT_SIZE)
    ascent, descent = font.getmetrics()

    probe = ImageDraw.Draw(Image.new("L", (10, 10)))
    widths = [probe.textlength(ch, font=font) for ch in letters]

    target_total_w = canvas_w * TARGET_WIDTH_FRAC
    spacing = max(10, (target_total_w - sum(widths)) / (len(letters) - 1))
    total_w = sum(widths) + spacing * (len(letters) - 1)

    work_h = (ascent + descent) * 2
    baseline_y = work_h - descent - 40

    canvas = Image.new("L", (canvas_w, work_h), 0)
    draw = ImageDraw.Draw(canvas)
    # Hebrew reads right-to-left: place the first letter of the word rightmost.
    x = canvas_w - (canvas_w - total_w) / 2
    for ch, w in zip(letters, widths):
        x -= w
        draw.text((x, baseline_y), ch, font=font, fill=255, anchor="ls")
        x -= spacing

    new_h = int(work_h * V_STRETCH)
    new_w = int(canvas_w * H_STRETCH)
    stretched = canvas.resize((new_w, new_h), Image.LANCZOS)
    binarized = stretched.point(lambda p: 255 if p >= BINARIZE_THRESHOLD else 0)

    ink_bbox = binarized.getbbox()
    margin_top = 6 * WORK_SCALE
    margin_bottom = int(descent * V_STRETCH * 0.25)
    margin_side = 4 * WORK_SCALE
    crop_box = (
        max(0, ink_bbox[0] - margin_side),
        max(0, ink_bbox[1] - margin_top),
        min(new_w, ink_bbox[2] + margin_side),
        min(new_h, ink_bbox[3] + margin_bottom),
    )
    cropped = binarized.crop(crop_box)

    sprite_h = round(cropped.height / WORK_SCALE)
    sprite_w = round(cropped.width / WORK_SCALE)
    mask = cropped.resize((sprite_w, sprite_h), Image.BOX)
    return mask


def build_title_nibble_grid():
    """Returns (width, height, grid) for the full sprite 5 replacement:
    the rendered title mask, quantized and centered within SPRITE_WIDTH,
    plus a full-width bright-red rule line as the last row.
    """
    mask = render_title_mask()
    w, h = mask.size
    px = mask.load()

    offset_x = (SPRITE_WIDTH - w) // 2
    height = h + 1  # +1 for the rule line
    grid = [[NIBBLE_TRANSPARENT] * SPRITE_WIDTH for _ in range(height)]
    for y in range(h):
        for x in range(w):
            grid[y][offset_x + x] = intensity_to_nibble(px[x, y])
    grid[h] = [NIBBLE_RED_BRIGHT] * SPRITE_WIDTH
    return SPRITE_WIDTH, height, grid


def build(org_path=None, out_path=None):
    org_path = org_path or (ORG_FILES_DIR / SPRITE_NAME)
    out_path = out_path or (BUILD_DIR / SPRITE_NAME)

    print(f"[intro-title] decompressing {org_path.relative_to(REPO_ROOT)}")
    data = org_path.read_bytes()
    dec = hsq.decompress_bytes(data)

    print("[intro-title] rendering title sprite")
    width, height, grid = build_title_nibble_grid()
    new_block = bigs_sprite.encode_sprite(grid, width, height, PALBASE)

    print(f"[intro-title] splicing sprite {TITLE_SPRITE_INDEX} ({width}x{height})")
    new_dec = bigs_sprite.splice_sprite(dec, TITLE_SPRITE_INDEX, new_block)

    print(f"[intro-title] recompressing -> {out_path.relative_to(REPO_ROOT)}")
    recompressed = hsq.compress_bytes(new_dec)
    assert hsq.decompress_bytes(recompressed) == new_dec, "recompression round-trip mismatch"

    BUILD_DIR.mkdir(exist_ok=True)
    out_path.write_bytes(recompressed)
    return out_path, height


def main():
    if not (ORG_FILES_DIR / SPRITE_NAME).exists():
        sys.exit(f"{ORG_FILES_DIR / SPRITE_NAME} not found -- run build_translation.py first")
    build()


if __name__ == "__main__":
    main()
