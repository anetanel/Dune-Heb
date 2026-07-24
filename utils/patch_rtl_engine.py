#!/usr/bin/env python3

"""
patch_rtl_engine.py - EXPERIMENTAL. Patches game/DUNEPRG.EXE's dialogue/
subtitle-box draw path to render text natively right-to-left, instead of
relying on translations/*.HEB being pre-reversed for an LTR-only drawer.
This is the moonshot explored across several sessions -- see the
dune_rtl_engine_patch_moonshot project memory for the full derivation
(disassembly addresses, why each patch is safe, what's still unverified).

Two independent patches, both required together -- applying only one
leaves *all* dialogue text (English included) broken until its counterpart
also lands:

1. Pen-seed reorder (draw_speech_bubble, load offset 0x9AE3-0x9B13): seeds
   subtitle_pen_x from the box's right edge (box.x + box.w - pad_right)
   instead of the left edge (box.x + pad_left). Pure in-place reorder, 48
   bytes in and out, zero net length change -- freed by dropping two
   writes (subtitle_line_start_x/y, floppy 0x42EC/0x42EE) confirmed dead
   (written once here, never read anywhere in the 74245-byte load module).
   The value needed downstream at load offset 0x9B23 (dx = box.x+box.w,
   unclamped) is preserved exactly.

2. Pen-advance flip (font_draw_glyph_func, both the tall-font body at load
   offset 0xCAF9 and the small-font body at 0xCB93): changes the pen
   advance from `add [font_draw_position_x],ax` to `sub`, so the drawer
   moves right-to-left through each glyph instead of left-to-right.
   font_get_draw_position already reads the pen *before* it's advanced and
   draws the glyph at that old position, so a bare add-to-sub opcode swap
   is sufficient -- no reordering needed here.

Neither patch alone, nor both together, will render correct Hebrew until
utils/split.py's --rtl-native mode is also wired into the build for
PHRASE11/PHRASE12 (not yet done as of this writing -- deliberately left
unwired so the current shipped build is unaffected). This script exists to
apply the engine-side half in isolation for testing; it is NOT invoked by
build_translation.py.

Usage:
    ./patch_rtl_engine.py [--exe PATH]

Idempotent: does nothing to a site already patched. Refuses (and makes no
change to that site) if its bytes match neither the known original nor the
known patched sequence. Always backs up the pre-patch file once, next to
the original, before the first patch of a fresh run.
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
    # draw_speech_bubble (load offset 0x9AE3): reorder so subtitle_pen_x is
    # seeded from the text area's right edge instead of its left edge --
    # see module docstring point 1. 48 bytes -> 48 bytes.
    Patch(
        "pen seed (draw_speech_bubble: right edge instead of left)",
        0x9AE3,
        "8B4600" "AB" "8BD0" "0306DB42" "A3E842" "A3EC42"
        "8B4602" "AB" "8BD8" "0306DF42" "A3EA42" "A3EE42"
        "8B4604" "03D0" "2B06DB42" "2B06DD42" "A3E642",
        "8B4600" "AB" "8B5604" "03D0" "8BC2" "2B06DD42" "A3E842"
        "8B4602" "AB" "8BD8" "0306DF42" "A3EA42"
        "8B4604" "2B06DB42" "2B06DD42" "A3E642"
        "909090",
    ),
    # font_draw_glyph_func pen advance, tall-font body (load offset
    # 0xCB18): add [font_draw_position_x],ax -> sub -- see docstring point 2.
    Patch("pen advance (tall font)", 0xCB18, "010650FC", "290650FC"),
    # font_draw_glyph_func pen advance, small-font body (load offset
    # 0xCBB2): same swap, same shared font_draw_position_x variable.
    Patch("pen advance (small font)", 0xCBB2, "010650FC", "290650FC"),
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
