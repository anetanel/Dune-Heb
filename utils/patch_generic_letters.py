#!/usr/bin/env python3

"""
patch_generic_letters.py - replaces the English capital-letter sprites
(indices 33-58, 'A'-'Z') inside GENERIC.HSQ's picture-resource container
(see utils/bigs_sprite.py for that format) with Hebrew letterforms, baked
with the same gold/tan gradient shading and transparency as the originals.
Also fills in sprite 7 (blank in the original) with a geresh punctuation
mark, needed by some of the same credit-name text -- see below.

Background: GENERIC.HSQ is a "bigs" sprite sheet (same container format as
INTDS.HSQ) used to spell text in the game's outro. Sprite 32 is a 1x1 space;
sprites 33-58 are A-Z, each `palbase=239`, mostly 14px tall (sprite 49 'Q'
is 16px, 2 extra rows for its descender tail -- proof per-sprite height is
free to vary). Decoding sprite 33 ('A') directly confirmed the palette:
index 239 is undefined (nibble 0 -> transparent, same convention as
INTDS.HSQ sprite 5 -- see patch_intro_title.py), indices 240-253 are a
14-step gold ramp (240=(252,220,128) brightest .. 253=(168,132,56) darkest),
and the shading rule is a per-row vertical gradient tied to each glyph's own
height: a filled pixel's nibble is approximately its row index + 1 (clamped
at 14), with some hand-applied dithering between adjacent shades on select
rows for extra smoothness (e.g. 'A' row 3 is literally 3,4,3,4,3,4
alternating) -- not perfectly reproduced pixel-for-pixel here (that's
hand-tuned 1992 pixel art, not a derivable formula), but approximated with
build_letter_grid()'s own continuous-shade dithering below.

Letter mapping: reuses heb_encode.py's HEB_LETTER_TO_BYTE order (the same
one already governing DUNECHAR's Hebrew-in-place-of-A-Z glyph slots),
shifted from its 65-91 byte range onto GENERIC's 33-58 sprite range
(generic_index = 33 + (dunechar_byte - 65)). That table has 27 Hebrew
letterforms (22 base + 5 finals) for GENERIC's 26 slots; ץ (final Tsadi,
byte 91) is dropped -- the one letter with no slot under this order,
confirmed with the translator as the mapping to use.

Font: fonts/PublicPixel.ttf ("Public Pixel Font", GGBotNet,
ggbot.itch.io/public-pixel-font), CC0 1.0 Universal -- see
fonts/PublicPixel-LICENSE-CC0.txt. A native 8x8-grid pixel font with full
Hebrew coverage (added in its v1.0 devlog), chosen after two prior
attempts:
  - fonts/AharoniCLM-Book.ttf (Culmus "Aharoni CLM", GPLv2, used for the
    intro title in patch_intro_title.py) -- renders correctly with no
    artifacts, but the translator felt its book-weight strokes don't suit
    this credits-style bold gold-gradient lettering.
  - "Haim Reloaded"/"Revolutions" (Meir Sadan, oketz.com) -- both have
    deliberately detached geometric fragments in ג and (Reloaded only) ט
    that read as stray pixels at this sprite's ~14px size, confirmed live
    in-game; also never had a clear redistribution license from the author.
  - Handjet (Google Fonts / rosettatype, SIL OFL) -- a dot-matrix variable
    font, tried next; live in-game result wasn't good enough to keep (per
    the translator, without a specific artifact identified the way the
    Haim ones had one). Also notable in its own right: flattening it to a
    static instance via fonttools.varLib.instancer at ELSH=0 (its
    "Element Shape"/dot-roundness axis, 0=squarest) silently produced a
    *broken* font -- valid hmtx/cmap metrics (getbbox()/getlength() kept
    working) but empty glyf contours, so draw.text() rendered nothing;
    PIL's own set_variation_by_axes() hits the same empty-outline result
    at ELSH=0, so it's a genuine degenerate case in the font/rasterizer,
    not an instancer-specific bug -- moot now that Handjet isn't used, but
    worth remembering if a variable font is tried again.

Sizing: FONT_SIZE is derived from the font's own metrics (not eyeballed)
so that its cap-line-to-baseline distance maps to exactly
TARGET_CORE_HEIGHT (14) output rows -- confirmed via getbbox() that all 26
mapped letters share the same cap-line/baseline (282/826 at size=1000)
except ל (taller ascender) and the three sofit finals with descenders
(ך/ן/ף, extending well past baseline). Letters overflowing the standard
14-row core in either direction grow the sprite's own height beyond 14,
the same way the original 'Q' does for its tail -- there is no shared
uniform height requirement, only a shared *reference* cap-line/baseline
alignment so all 26 letters' gradients start from the same row-0 meaning.

Scope: sprites 65-90 in GENERIC.HSQ also use palbase=239 (a second,
unidentified glyph set) -- deliberately never touched here; only 33-58,
plus sprite 7 (see below).

The outro's credit-name text (confirmed live via dosbox-mcp, playing a save
right before the outro) is driven by the exact same byte scheme as
DUNECHAR: it reads COMMAND1's already-translated Hebrew text (e.g. line
284 "הארה" comes out as the byte string "EATE", i.e. each Hebrew letter's
heb_encode.HEB_LETTER_TO_BYTE value, since ה=69='E', א=65='A', ת=86='T'),
and draws each byte via GENERIC sprite index = byte - 32 (65-91 -> 33-59,
matching this file's own A-Z/'[' layout). This is why replacing 33-58 with
Hebrew glyphs alone makes the credits render correctly in Hebrew without
touching any string table -- confirmed rendering "הארה" cleanly in-game.
One gap this exposed: heb_encode.py maps the geresh punctuation mark (׳,
used in COMMAND1.HEB lines 280/289 e.g. "ג׳סיקה") to a literal apostrophe,
byte 39 -- which lands on GENERIC sprite index 7 (39-32=7), not any of
33-58. Sprite 7 is a *pre-existing, dedicated* slot in the original file
(1x1/blank there, since the original English credits apparently never
used an apostrophe) rather than something we need to invent a slot for --
GERESH_INDEX below fills it in with an actual geresh mark, styled the same
as the letters, rather than sacrificing one of the 26 letter slots for it.

Out of scope: no other GENERIC byte/index correspondence has been checked
beyond the 65-91 (letters) and 39 (geresh) cases confirmed live for this
specific credit-name text -- if a future line needs other punctuation,
re-derive its byte from heb_encode.py the same way before assuming a slot.

Usage:
    ./patch_generic_letters.py
    ./patch_generic_letters.py --dump-pngs

Always regenerates build/GENERIC.HSQ (cheap, deterministic -- same policy
as patch_intro_title.py/patch_intro_logo.py, not cached), plus four
transparent-background PNGs of sprites 33-58 + 7 for visual comparison
(same purpose as build_translation.py's DUNECHAR_before.png/DUNECHAR_after.png):
tmp/GENERIC_before.png / tmp/GENERIC_after.png (8x upscaled, easy to eyeball)
and tmp/GENERIC_before_truesize.png / tmp/GENERIC_after_truesize.png (1:1
pixel size, actual in-game footprint).

Hand-editing workflow: `--dump-pngs` writes each of the 26 letters + the
geresh mark's *current* shape (whatever FONT_PATH currently renders) as
individual true-size, black-ink-on-white PNGs into generic_png/ -- same
convention as font_png/ (font.py's DUNECHAR glyph editing). Edit any of
those files in a pixel editor (resizing the canvas changes that glyph's
sprite dimensions; the file's own size is authoritative, no need to keep
it at whatever size it was dumped at) and re-run `./patch_generic_letters.py`
(no flag) -- build()/_letter_grid() automatically prefers an edited PNG
over the live font render for any letter that has one in generic_png/,
falling back to the font for any that don't. generic_png/ is committed,
human-edited source (like font_png/), not regenerated output -- don't
treat it as disposable.
"""

import argparse
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

import bigs_sprite
import hsq

UTILS_DIR = Path(__file__).resolve().parent
REPO_ROOT = UTILS_DIR.parent
ORG_FILES_DIR = REPO_ROOT / "org_files"
BUILD_DIR = REPO_ROOT / "build"
TMP_DIR = REPO_ROOT / "tmp"
# Hand-editable glyph source, same role/convention as font_png/ (font.py):
# committed, human-edited pixel art that build() prefers over the live
# font render when present -- see dump_letter_pngs()/_letter_grid().
GENERIC_PNG_DIR = REPO_ROOT / "generic_png"
FONT_PATH = REPO_ROOT / "fonts" / "PublicPixel.ttf"
FONT_VARIATION_AXES = None  # not a variable font

SPRITE_NAME = "GENERIC.HSQ"
FIRST_LETTER_INDEX = 33  # 'A'
LAST_LETTER_INDEX = 58  # 'Z'

# Byte 39 (ASCII apostrophe) -> GENERIC index 39-32=7, a pre-existing but
# blank (1x1) slot in the original file -- see module docstring for how
# this was confirmed live to be where heb_encode.py's geresh (׳) mapping
# lands for this outro text.
GERESH_INDEX = 7
GERESH_CHAR = "׳"

# GENERIC's existing palette for all 26 letter slots (see module docstring).
# Reused as-is -- nibbles 1-14 already cover a full gold gradient ramp, no
# new palette entries are needed.
PALBASE = 239
NIBBLE_TRANSPARENT = 0
GRADIENT_LEVELS = 14  # nibbles 1-14 -> palette indices 240-253

# heb_encode.HEB_LETTER_TO_BYTE's order, shifted from its 65-91 DUNECHAR
# byte range onto GENERIC's 33-58 sprite range (generic_index = 33 +
# (dunechar_byte - 65)). ץ (byte 91, would land on nonexistent slot 59) is
# dropped -- see module docstring. Written out explicitly rather than
# derived at import time so it's trivially eyeballed/edited on its own.
LETTER_MAPPING = {
    33: "א", 34: "ב", 35: "ג", 36: "ד", 37: "ה", 38: "ו", 39: "ז", 40: "ח",
    41: "ט", 42: "י", 43: "כ", 44: "ל", 45: "מ", 46: "נ", 47: "ס", 48: "ע",
    49: "פ", 50: "צ", 51: "ק", 52: "ר", 53: "ש", 54: "ת", 55: "ך", 56: "ם",
    57: "ן", 58: "ף",
}

# Rendering pipeline constants.
WORK_SCALE = 16  # supersampling factor before the final box-filter downsample
TARGET_CORE_HEIGHT = 14  # output rows spanning the font's own cap-line -> baseline
BINARIZE_THRESHOLD = 110  # mask threshold applied before the box downsample, for crisp edges
MARGIN_PX = 2 * WORK_SCALE  # supersampled headroom around each glyph's own ink

# GENERIC.HSQ's original sprites 33-58 never exceed 16 rows (sprite 49 'Q'
# is the tallest, at 16, for its descender tail) -- live-tested in-game:
# letting a replacement letter exceed this (ל's natural ascender rendered
# at 17 rows) crashed the outro's credit-name compositor with a corrupted
# framebuffer the moment a name containing that letter (e.g. "Paul
# Atreides", COMMAND1.TXT line 278, the first name shown) was drawn --
# whatever fixed-size buffer that routine uses for compositing a credit
# line was seemingly sized to this file's own historical max height and
# has no bounds check against a taller glyph. Sprites 65-90 (a separate,
# unidentified glyph family sharing palbase=239) go up to 18 rows in the
# original without apparent issue, but that's a different code path with
# its own buffer -- not evidence this cap is unnecessary for 33-58.
MAX_HEIGHT = 16

# Hand-applied dithering in the original 1992 art (e.g. 'A' row 3 is
# literally 3,4,3,4,3,4) fakes finer-than-14-level gradient resolution.
# Approximated here with a continuous per-row shade value, dithered between
# its floor/ceiling by x-parity, rather than a hard integer step per row.
DITHER = True


def _load_font(size):
    """Loads FONT_PATH at the given size, applying FONT_VARIATION_AXES if
    the font has variation axes (a variable font like Handjet would; a
    static font like PublicPixel raises OSError from get_variation_axes()
    instead of returning empty, so that's caught here rather than checked).
    """
    font = ImageFont.truetype(str(FONT_PATH), size)
    if FONT_VARIATION_AXES is not None:
        try:
            has_axes = bool(font.get_variation_axes())
        except OSError:
            has_axes = False
        if has_axes:
            font.set_variation_by_axes(FONT_VARIATION_AXES)
    return font


def _font_metrics():
    """Derives FONT_SIZE from the font's own metrics so the cap-line ->
    baseline distance (shared by all 26 mapped letters except for the
    ascender/descender outliers noted in the module docstring) maps to
    exactly TARGET_CORE_HEIGHT * WORK_SCALE supersampled pixels. Probes at
    a large reference size (1000) since PIL font metrics scale linearly.
    """
    probe_size = 1000
    probe_font = _load_font(probe_size)
    tops, bottoms = [], []
    for ch in LETTER_MAPPING.values():
        _l, t, _r, b = probe_font.getbbox(ch)
        tops.append(t)
        bottoms.append(b)
    # The cap-line/baseline shared by most letters is each list's most
    # common (mode) value -- outliers (lamed's ascender, sofit descenders)
    # sit off to one side and shouldn't drag the reference line.
    cap_line = max(set(tops), key=tops.count)
    baseline = max(set(bottoms), key=bottoms.count)
    core_units = baseline - cap_line

    target_core_px = TARGET_CORE_HEIGHT * WORK_SCALE
    font_size = round(probe_size * target_core_px / core_units)
    font = _load_font(font_size)
    scale = font_size / probe_size
    return font, cap_line * scale, baseline * scale


_FONT, _CAP_LINE, _BASELINE = _font_metrics()


def render_letter_mask(heb_char):
    """Renders one Hebrew letter at supersampled size, anchored to the
    shared cap-line/baseline computed by _font_metrics(), and returns
    (mask, cap_line_row, baseline_row) where the row values are in the
    mask's own pixel coordinates (supersampled, pre-downsample).
    """
    canvas_h = round(_BASELINE + MARGIN_PX * 2)
    canvas_w = round(_FONT.getlength(heb_char) + MARGIN_PX * 2)
    canvas = Image.new("L", (max(canvas_w, 1), canvas_h), 0)
    draw = ImageDraw.Draw(canvas)
    draw.text((MARGIN_PX, _BASELINE + MARGIN_PX), heb_char, font=_FONT, fill=255, anchor="ls")

    ink_bbox = canvas.getbbox()
    if ink_bbox is None:
        ink_bbox = (0, 0, 1, 1)
    return canvas, ink_bbox, _CAP_LINE + MARGIN_PX, _BASELINE + MARGIN_PX


def _row_shade(core_row_f):
    """Continuous shade value (1..GRADIENT_LEVELS) for a row position,
    where core_row_f is a float row index relative to the cap line, 0 at
    the cap line and (TARGET_CORE_HEIGHT-1) at the baseline -- clamped so
    rows above the cap line or below the baseline (ascender/descender
    overflow) saturate at the boundary shade, same as the original 'Q'
    tail saturating at nibble 14.
    """
    core_row_f = min(max(core_row_f, 0), TARGET_CORE_HEIGHT - 1)
    return 1 + (GRADIENT_LEVELS - 1) * core_row_f / (TARGET_CORE_HEIGHT - 1)


def _quantize_mask_to_grid(get_intensity, width, height, cap_line_row, threshold=BINARIZE_THRESHOLD):
    """Shared by build_letter_grid() (live font render) and
    load_letter_grid_from_png() (hand-edited PNG): given a width x height
    intensity lookup (0=ink..255=background) and the row offset where the
    gradient's cap line (nibble 1) sits, returns the quantized nibble grid.
    """
    grid = [[NIBBLE_TRANSPARENT] * width for _ in range(height)]
    for y in range(height):
        core_row_f = y - cap_line_row
        shade_f = _row_shade(core_row_f)
        floor_nib, ceil_nib = int(shade_f), min(GRADIENT_LEVELS, int(shade_f) + 1)
        frac = shade_f - floor_nib
        for x in range(width):
            if get_intensity(x, y) < threshold:
                continue
            if DITHER and floor_nib != ceil_nib:
                use_ceil = (x % 2 == 0) == (frac >= 0.5)
                grid[y][x] = ceil_nib if use_ceil else floor_nib
            else:
                grid[y][x] = max(1, min(GRADIENT_LEVELS, round(shade_f)))
    return grid


def build_letter_grid(heb_char):
    """Returns (width, height, nibble_grid) for one letter's replacement
    sprite: an anti-aliased render of heb_char, quantized into the
    existing 14-step gold gradient with a row-position-based shade and
    optional x-parity dithering (see DITHER).
    """
    mask, ink_bbox, cap_line, baseline = render_letter_mask(heb_char)
    left, top, right, bottom = ink_bbox
    core_top, core_bottom = round(cap_line), round(baseline)
    crop_top = min(top, core_top)
    crop_bottom = max(bottom, core_bottom)
    # Clip ascender/descender overflow so no replacement exceeds MAX_HEIGHT
    # rows total -- see its own docstring above for why. Both sides share
    # one combined budget (scaled down proportionally if both overflow at
    # once) rather than each independently allowed up to the full budget,
    # which would let the *sum* exceed MAX_HEIGHT (found live: ל has a
    # large top overflow *and* a small bottom one simultaneously, and
    # independent per-side clamping let the combined total reach 17 rows).
    max_extra = (MAX_HEIGHT - TARGET_CORE_HEIGHT) * WORK_SCALE
    top_overflow = max(0, core_top - crop_top)
    bottom_overflow = max(0, crop_bottom - core_bottom)
    total_overflow = top_overflow + bottom_overflow
    if total_overflow > max_extra:
        scale = max_extra / total_overflow
        top_overflow = int(top_overflow * scale)
        bottom_overflow = int(bottom_overflow * scale)
    crop_top = core_top - top_overflow
    crop_bottom = core_bottom + bottom_overflow
    cropped = mask.crop((left, crop_top, right, crop_bottom))

    width = max(1, round(cropped.width / WORK_SCALE))
    height = max(1, round(cropped.height / WORK_SCALE))
    resized = cropped.resize((width, height), Image.BOX)
    px = resized.load()

    cap_line_row = (cap_line - crop_top) / WORK_SCALE
    grid = _quantize_mask_to_grid(lambda x, y: px[x, y], width, height, cap_line_row)
    return width, height, grid


def _png_name(index, heb_char):
    return f"{index:02d}_{'geresh' if index == GERESH_INDEX else heb_char}.png"


def dump_letter_pngs(out_dir=None):
    """Dumps each of the 26 mapped letters plus the geresh mark as an
    individual black-ink-on-white PNG (RGB, true pixel size -- no
    supersampling, since these are meant to be hand-edited pixel-by-pixel)
    for manual touch-up, same black=ink/white=background convention as
    font_png/ (see font.py's dump_single()/load()). Once edited,
    build()/_letter_grid() prefers these files over the live font render.
    """
    out_dir = out_dir or GENERIC_PNG_DIR
    out_dir.mkdir(exist_ok=True)
    items = list(LETTER_MAPPING.items()) + [(GERESH_INDEX, GERESH_CHAR)]
    for index, ch in items:
        width, height, grid = build_letter_grid(ch)
        img = Image.new("RGB", (width, height), "white")
        for y, row in enumerate(grid):
            for x, nib in enumerate(row):
                if nib != NIBBLE_TRANSPARENT:
                    img.putpixel((x, y), (0, 0, 0))
        out_path = out_dir / _png_name(index, ch)
        img.save(out_path)
        print(f"  wrote {out_path.relative_to(REPO_ROOT)} ({width}x{height})")
    return out_dir


def load_letter_grid_from_png(path):
    """Loads a hand-edited glyph PNG (see dump_letter_pngs()) and builds
    its nibble grid at the image's own pixel size -- no supersampling,
    since these are edited pixel-for-pixel at final size already. Row 0
    of the image is treated as the gradient's cap line (nibble 1),
    saturating at nibble GRADIENT_LEVELS for any row beyond
    TARGET_CORE_HEIGHT-1 -- same convention build_letter_grid() uses for
    its live-rendered letters, so a hand-drawn descender/ascender just
    needs extra rows appended below/above like the font-rendered ones do.
    """
    img = Image.open(path)
    if img.mode != "RGB":
        img = img.convert("RGB")
    width, height = img.size
    px = img.load()
    # _quantize_mask_to_grid()'s threshold check treats HIGH intensity as
    # ink (matching render_letter_mask()'s white-ink-on-black canvas), so
    # this must invert the PNG's black-ink-on-white convention: black -> 255
    # ("definitely ink"), white -> 0 ("definitely background").
    grid = _quantize_mask_to_grid(
        lambda x, y: 255 if px[x, y] == (0, 0, 0) else 0, width, height, cap_line_row=0
    )
    return width, height, grid


def _letter_grid(index, heb_char):
    """Prefers a hand-edited PNG in GENERIC_PNG_DIR if present (see
    dump_letter_pngs()), otherwise renders live from FONT_PATH.
    """
    edited = GENERIC_PNG_DIR / _png_name(index, heb_char)
    if edited.exists():
        return load_letter_grid_from_png(edited)
    return build_letter_grid(heb_char)


def render_letters_png(dec, out_path, scale=8, indices=None):
    """Renders the given sprite indices (default FIRST_LETTER_INDEX..
    LAST_LETTER_INDEX plus GERESH_INDEX) as a single horizontal-strip PNG
    (real alpha transparency for nibble 0) for visual before/after
    comparison -- same purpose as build_translation.py's build_font() step
    producing DUNECHAR_before.png/DUNECHAR_after.png.
    """
    if indices is None:
        indices = list(range(FIRST_LETTER_INDEX, LAST_LETTER_INDEX + 1)) + [GERESH_INDEX]
    _offset_A, palette, sprites = bigs_sprite.parse_sprites(dec)
    tiles = []
    for index in indices:
        sprite = sprites[index]
        grid = bigs_sprite.decode_sprite_pixels(dec, sprite)
        w, h = sprite["width"], sprite["height"]
        tile = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        px = tile.load()
        for y, row in enumerate(grid):
            for x, nib in enumerate(row):
                if nib != NIBBLE_TRANSPARENT:
                    rgb = palette.get((sprite["palbase"] + nib) & 0xFF, (255, 0, 255))
                    px[x, y] = rgb + (255,)
        tiles.append(tile.resize((w * scale, h * scale), Image.NEAREST))

    pad = scale
    max_h = max(t.height for t in tiles)
    total_w = sum(t.width for t in tiles) + pad * (len(tiles) + 1)
    sheet = Image.new("RGBA", (total_w, max_h), (0, 0, 0, 0))
    x = pad
    for tile in tiles:
        sheet.paste(tile, (x, max_h - tile.height), tile)
        x += tile.width + pad

    out_path.parent.mkdir(exist_ok=True)
    sheet.save(out_path)


def build(org_path=None, out_path=None):
    org_path = org_path or (ORG_FILES_DIR / SPRITE_NAME)
    out_path = out_path or (BUILD_DIR / SPRITE_NAME)
    before_png = TMP_DIR / "GENERIC_before.png"
    after_png = TMP_DIR / "GENERIC_after.png"
    before_truesize_png = TMP_DIR / "GENERIC_before_truesize.png"
    after_truesize_png = TMP_DIR / "GENERIC_after_truesize.png"

    print(f"[generic-letters] decompressing {org_path.relative_to(REPO_ROOT)}")
    dec = hsq.decompress_bytes(org_path.read_bytes())
    original_size = len(dec)

    print(f"[generic-letters] dumping original glyphs -> {before_png.relative_to(REPO_ROOT)}")
    render_letters_png(dec, before_png)
    render_letters_png(dec, before_truesize_png, scale=1)

    print("[generic-letters] rendering Hebrew letters")
    for index in range(FIRST_LETTER_INDEX, LAST_LETTER_INDEX + 1):
        heb_char = LETTER_MAPPING[index]
        width, height, grid = _letter_grid(index, heb_char)
        # compressed=False: live-tested in-game (dosbox-mcp) against a save
        # right before the outro -- compressed=True (the default) crashed
        # the credit-name compositor (CS register hijacked into garbage
        # memory, e.g. cs=0xa5a5, a classic uninitialized-memory poison
        # pattern) even when re-encoding a sprite's OWN unchanged original
        # pixel content through bigs_sprite.encode_sprite() with no content
        # change at all -- so the divergence is in our RLE packer's byte-
        # level token choices vs whatever the real engine's decoder
        # actually expects (both our encode and decode round-trip
        # correctly against each other and against dump.c, but that only
        # proves internal consistency, not fidelity to the real assembly).
        # Uncompressed sizes are larger but every letter here is small
        # (GENERIC.HSQ shrinks either way -- see the size log below), and
        # the uncompressed path was confirmed crash-free through a full
        # live playthrough of the outro to completion (clean exit to DOS).
        new_block = bigs_sprite.encode_sprite(grid, width, height, PALBASE, compressed=False)
        dec = bigs_sprite.splice_sprite(dec, index, new_block)
        source = "edited PNG" if (GENERIC_PNG_DIR / _png_name(index, heb_char)).exists() else "font"
        print(f"  sprite {index} ({heb_char}) -> {width}x{height} ({source})")

    print("[generic-letters] rendering geresh punctuation mark")
    width, height, grid = _letter_grid(GERESH_INDEX, GERESH_CHAR)
    new_block = bigs_sprite.encode_sprite(grid, width, height, PALBASE, compressed=False)
    dec = bigs_sprite.splice_sprite(dec, GERESH_INDEX, new_block)
    geresh_source = "edited PNG" if (GENERIC_PNG_DIR / _png_name(GERESH_INDEX, GERESH_CHAR)).exists() else "font"
    print(f"  sprite {GERESH_INDEX} (geresh) -> {width}x{height} ({geresh_source})")

    print(f"[generic-letters] recompressing -> {out_path.relative_to(REPO_ROOT)}")
    recompressed = hsq.compress_bytes(dec)
    assert hsq.decompress_bytes(recompressed) == dec, "recompression round-trip mismatch"

    print(f"[generic-letters] dumping modified glyphs -> {after_png.relative_to(REPO_ROOT)}")
    render_letters_png(dec, after_png)
    render_letters_png(dec, after_truesize_png, scale=1)

    print(f"[generic-letters] decompressed size {original_size}B -> {len(dec)}B")

    BUILD_DIR.mkdir(exist_ok=True)
    out_path.write_bytes(recompressed)
    return out_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dump-pngs", action="store_true",
        help=f"write each letter's current shape to {GENERIC_PNG_DIR.name}/ for hand-editing, then exit"
    )
    args = parser.parse_args()

    if args.dump_pngs:
        print(f"[generic-letters] dumping editable glyph PNGs -> {GENERIC_PNG_DIR.relative_to(REPO_ROOT)}/")
        dump_letter_pngs()
        return

    if not (ORG_FILES_DIR / SPRITE_NAME).exists():
        sys.exit(f"{ORG_FILES_DIR / SPRITE_NAME} not found -- run build_translation.py first")
    build()


if __name__ == "__main__":
    main()
