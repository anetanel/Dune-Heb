#!/usr/bin/env python3

"""
verify_bigs_sprite_roundtrip.py - permanent regression guard for
bigs_sprite.py's encode_sprite(). Confirms that decoding every sprite in a
real picture-resource file and re-encoding it with encode_sprite() produces
pixel-identical output, for every sprite in both INTDS.HSQ (all sprites
320px wide, always a multiple of 4 -- the original, never-broken case) and
GENERIC.HSQ (individual letter-glyph sprites, frequently non-4-aligned
widths -- the case encode_sprite() was extended to support for
patch_generic_letters.py). Also exercises a synthetic worst-case: several
fully-uniform rows at an odd, non-multiple-of-4 width, since a long uniform
run is exactly what tempts a naive RLE packer to want to span row
boundaries (see bigs_sprite.py's module docstring for why that's unsafe).

Run this any time encode_sprite()/_pack_row()/_pad_row() change -- these
functions have no other test coverage in this repo.

Usage:
    ./verify_bigs_sprite_roundtrip.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bigs_sprite
import hsq

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def source_path(name):
    """Same org_files/-then-game/ fallback as extract_sprites.py's
    source_path() -- GENERIC.HSQ may not be in org_files/ yet depending on
    what order this repo's build_translation.py wiring has been applied in.
    """
    org = os.path.join(REPO_ROOT, "org_files", f"{name}.HSQ")
    if os.path.exists(org):
        return org
    return os.path.join(REPO_ROOT, "game", f"{name}.HSQ")


def verify_file(name):
    path = source_path(name)
    if not os.path.exists(path):
        print(f"[skip] {name}: {path} not found")
        return True

    dec = hsq.decompress_bytes(open(path, "rb").read())
    _offset_A, _palette, sprites = bigs_sprite.parse_sprites(dec)

    ok = True
    for i, sprite in enumerate(sprites):
        w, h = sprite["width"], sprite["height"]
        if w == 0 or h == 0:
            continue  # not a real sprite (see extract_sprites.py's truncation-tolerance note)
        grid = bigs_sprite.decode_sprite_pixels(dec, sprite)
        for compressed in (True, False):
            block = bigs_sprite.encode_sprite(grid, w, h, sprite["palbase"], compressed=compressed)
            resprite = {"pos": 4, "width": w, "height": h, "compressed": compressed, "palbase": sprite["palbase"]}
            regrid = bigs_sprite.decode_sprite_pixels(block, resprite)
            if regrid != grid:
                ok = False
                print(f"[FAIL] {name} sprite {i} ({w}x{h}, compressed={compressed}): round-trip mismatch")
    if ok:
        print(f"[ok] {name}: {len(sprites)} sprites round-trip cleanly (both compressed and uncompressed)")
    return ok


def verify_synthetic_odd_width():
    """Several fully-uniform rows at width=21 (odd, not a multiple of 4) --
    the case most likely to tempt a naive RLE packer into letting a single
    long run span a row boundary.
    """
    width, height = 21, 6
    grid = [[(row + 1) % 16] * width for row in range(height)]

    ok = True
    for compressed in (True, False):
        block = bigs_sprite.encode_sprite(grid, width, height, palbase=100, compressed=compressed)
        sprite = {"pos": 4, "width": width, "height": height, "compressed": compressed, "palbase": 100}
        regrid = bigs_sprite.decode_sprite_pixels(block, sprite)
        if regrid != grid:
            ok = False
            print(f"[FAIL] synthetic odd-width uniform-rows case (compressed={compressed}): round-trip mismatch")
            print("  expected:", grid)
            print("  got:     ", regrid)
    if ok:
        print("[ok] synthetic odd-width uniform-rows case round-trips cleanly")
    return ok


def main():
    results = [
        verify_file("INTDS"),
        verify_file("GENERIC"),
        verify_synthetic_odd_width(),
    ]
    if not all(results):
        sys.exit("verify_bigs_sprite_roundtrip: FAILED")
    print("verify_bigs_sprite_roundtrip: all checks passed")


if __name__ == "__main__":
    main()
