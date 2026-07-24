#!/usr/bin/env python3

"""
patch_location_name_order.py - fix Hebrew RTL rendering of the map/globe
screen's two-part location labels (e.g. "Sietch: " + "area-site name") in
game/DUNEPRG.EXE, by patching two independent spots in draw_location_name
and its callers. Neither is fixable from translation content -- both are
about which of two separate font-draw calls happens first on screen, which
is decided by fixed engine code, not by any string's own bytes.

1. draw_location_name (operand swap): the routine always draws
   Location.first_name (area) on the left and Location.last_name (site) on
   the right of the separator glyph -- two unconditional sequential glyph
   draws with no script-direction awareness. Correct for English, backwards
   for Hebrew. first_name/last_name are per-location bytes baked into the
   executable's data table, not exposed in any translated phrase file, so
   this swaps which struct field the routine draws first.

2. label/name call-site order swap: separately, the two map/globe hover-
   label call sites that draw a type label ("Sietch: "/"Palace: "/etc, via
   draw_string_location_type) immediately followed by draw_location_name
   with no engine-inserted gap -- any gap has to come from the label
   string's own trailing bytes. The English originals have a trailing
   space baked into the label; the Hebrew translations of those lines
   don't, so in the shipped Hebrew text (post-swap-1, area/site names on
   the correct sides) the label sits pixel-adjacent to the name. Since
   translation content can't be made to work for both draw orders,
   swapping which draws first -- name, then label -- also fixes this: read
   with [[dune_location_name_concat_order]]'s corrected area-site order,
   "<name> <label>" reads more naturally RTL than "<label><name>" ever
   would have. This patches the call-site instructions (not
   draw_location_name itself) to call draw_location_name before
   draw_string_location_type, leaving the font_get_draw_position call
   between them (it refreshes the cursor position for whichever draws
   second -- unaffected by which one that is).

   Only 2 of the 4 draw_location_name call sites use this reversible
   same-line "draw label, refresh cursor, draw name" pattern; the other 2
   position the name at a manually computed offset (a different, vertical
   list-style layout, not a same-line concatenation) and aren't touched
   here -- swapping their call order wouldn't fix a gap issue (there isn't
   one; they're not adjacent) and would need separately re-deriving their
   offset math.

Found via the CD build's disassembly (madmoose/dune-chani), then located in
the floppy DUNEPRG.EXE by structural instruction-pattern search (same
technique as the charisma live-patch, see repo history/memory) rather than
byte-signature matching, since the floppy build's own immediates/addresses
differ from the CD build's. Every patch below only swaps operand/target
bytes within a fixed-length instruction span -- total length is unchanged
at each site, so nothing else in the binary shifts.

Usage:
    ./patch_location_name_order.py [--exe PATH]

Idempotent: does nothing to a site already patched. Refuses (and makes no
change to that site) if its bytes match neither the known original nor the
known patched sequence, since that means this isn't the DUNEPRG.EXE build
these offsets were derived from. Always backs up the pre-patch file once,
next to the original, before the first patch of a fresh run.
"""

import argparse
import shutil
import sys
from pathlib import Path

UTILS_DIR = Path(__file__).resolve().parent
REPO_ROOT = UTILS_DIR.parent
GAME_DIR = REPO_ROOT / "game"
EXE_NAME = "DUNEPRG.EXE"

# MZ header size for this EXE (hdr_para=32 * 16 bytes) -- offset from the
# start of the file to the load module, which is what raw code offsets
# (as produced by ndisasm on the extracted load module) are relative to.
MZ_HEADER_SIZE = 512


class Patch:
    def __init__(self, name, load_offset, orig_hex, new_hex):
        self.name = name
        self.file_offset = MZ_HEADER_SIZE + load_offset
        self.orig = bytes.fromhex(orig_hex)
        self.new = bytes.fromhex(new_hex)
        assert len(self.orig) == len(self.new)


PATCHES = [
    # draw_location_name (load offset 0x7046): swap which struct field
    # (Location.first_name / .last_name) is drawn first vs. after the
    # separator glyph -- see module docstring point 1.
    Patch(
        "location-name field order (area/site)",
        0x7046,
        "8A05" "32E4" "050000" "E8A85B" "807D0103" "B020" "7202" "B02D"
        "FF16342A" "8A4501" "32E4" "050C00" "E9965B",
        "8A4501" "32E4" "050C00" "E8A75B" "807D0103" "B020" "7202" "B02D"
        "FF16342A" "8A05" "32E4" "050000" "E9965B",
    ),
    # Map hover-label call site 1 (single "call rel16" swap, x2): draw
    # location name before the type label instead of after.
    Patch("label/name call order (site 1a)", 0x4E50, "E8EA21", "E8F321"),
    Patch("label/name call order (site 1b)", 0x4E57, "E8EC21", "E8E321"),
    # Map hover-label call site 2 (same swap, different code location).
    Patch("label/name call order (site 2a)", 0x6457, "E8E30B", "E8EC0B"),
    Patch("label/name call order (site 2b)", 0x6460, "E8E30B", "E8DA0B"),
]


def apply_patches(exe_path):
    """Apply every patch in PATCHES to exe_path in place. Returns True if
    any change was made, False if everything was already patched. Exits
    without writing anything if any site's bytes match neither its known
    original nor its known patched sequence.
    """
    with open(exe_path, "rb") as f:
        data = bytearray(f.read())

    to_apply = []
    for patch in PATCHES:
        current = bytes(data[patch.file_offset:patch.file_offset + len(patch.orig)])
        if current == patch.new:
            print(f"[patch] {exe_path.name}: {patch.name} already patched")
            continue
        if current != patch.orig:
            sys.exit(
                f"{exe_path}: bytes at file offset 0x{patch.file_offset:x} ({patch.name}) "
                f"match neither the known original nor the known patched sequence "
                f"(found {current.hex()}). Refusing to patch anything -- this offset was "
                f"derived from a specific DUNEPRG.EXE build and may not apply here."
            )
        to_apply.append(patch)

    if not to_apply:
        return False

    backup_path = exe_path.with_suffix(exe_path.suffix + ".orig-backup")
    if not backup_path.exists():
        shutil.copy2(exe_path, backup_path)
        print(f"[patch] backed up {exe_path.name} -> {backup_path.name}")

    for patch in to_apply:
        data[patch.file_offset:patch.file_offset + len(patch.new)] = patch.new
        print(f"[patch] {exe_path.name}: applied {patch.name} at file offset 0x{patch.file_offset:x}")

    with open(exe_path, "wb") as f:
        f.write(data)
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--exe", type=Path, default=GAME_DIR / EXE_NAME, help=f"path to {EXE_NAME} (default: game/{EXE_NAME})")
    args = parser.parse_args()

    if not args.exe.exists():
        sys.exit(f"{args.exe} not found")

    apply_patches(args.exe)


if __name__ == "__main__":
    main()
