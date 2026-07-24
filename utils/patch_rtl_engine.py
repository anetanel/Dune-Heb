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

2. Conditional pen-advance (font_draw_glyph_func, both the tall-font body
   at load offset 0xCAF9 and the small-font body at 0xCB93): the pen
   advance `add [font_draw_position_x],ax` is the SAME shared instruction
   used by ~17 call sites across the whole game (dialogue, menus, map
   labels, save screens, ...), each seeded from its own left-edge starting
   position by its own caller. A blind add->sub flip (an earlier version
   of this script) makes dialogue render right-to-left correctly but also
   flips every *other* caller's text, which still starts from a left-edge
   seed -- those callers were never updated to match, so their text
   renders backwards/truncated. That's the "many problems" the branch was
   shelved for.

   The fix patched here is scoped instead of global: the pen advance now
   calls a small appended routine (a "code cave", since neither 6-byte
   site had 20 bytes of slack in place) that checks the engine's own
   `current_bubble_layout_ptr` variable (floppy 0x42F5) -- non-zero
   exactly while draw_speech_bubble/draw_subtitle_body's dialogue-box
   render is in progress (set at draw_speech_bubble's first instruction,
   cleared inside draw_subtitle_body once a batch of lines finishes) --
   and only subtracts (goes right-to-left) when it's set; every other
   caller, which never touches 0x42F5, keeps the original add/left-to-
   right behavior completely unpatched in practice. font_get_draw_position
   already reads the pen *before* it's advanced and draws the glyph at
   that old position, so this still needs no reordering, just a
   conditional in place of the unconditional opcode.

   The two 6-byte sites (4-byte `add [0xfc50],ax` + 2-byte `mov cl,al`,
   contiguous in both bodies) become a 5-byte far call into the cave plus
   1 NOP; the cave replicates `mov cl,al` itself before returning, so
   nothing downstream changes. The cave lives in a brand-new segment
   appended past the file's real end (no existing >=20-byte dead-space run
   exists anywhere in the confirmed code/data region -- see the moonshot
   memory's code-cave search), reached via a far call since a same-segment
   near call can't address past the 0xFFFF real-mode segment limit from
   CS=0. This requires growing the file, bumping the MZ header's e_cblp
   (page count e_cp is unaffected -- the growth still fits in the file's
   existing last page), and adding 2 new relocation table entries (one per
   far-call site) so the loader patches each call's segment half to the
   actual load segment at run time -- the existing 17-entry table has
   plenty of room (68 of 416 header bytes used) without resizing the
   header. Relocation entry format empirically confirmed against the
   program's own DS=SS=ES startup immediate (entry 0 = offset 2, segment
   1 -> load-address 0x12, matching the `mov ax,0xecf` at load offset
   0x11 whose immediate operand sits at 0x12): each entry is (offset,
   segment) as two u16 LE words, and address = segment*16 + offset,
   relative to the load module (add 0x200 for file offset).

Neither patch alone, nor both together, will render correct Hebrew until
utils/split.py's --rtl-native mode is also wired into the build for
PHRASE11/PHRASE12 (see utils/build_translation.py's build_phrases()).

Usage:
    ./patch_rtl_engine.py [--exe PATH]

Idempotent: does nothing to a file already in the fully-patched state.
Refuses (and makes no change) if any site's bytes match neither the known
original nor the known patched sequence. Always backs up the pre-patch
file once, next to the original, before the first patch of a fresh run.
"""

import argparse
import shutil
import struct
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

RELOC_TABLE_OFFSET = 0x1C  # e_lfarlc for this EXE: relocation table starts
                            # right after the fixed-size header fields.
E_CBLP_OFFSET = 0x02
E_CP_OFFSET = 0x04
E_CRLC_OFFSET = 0x06

# --- Patch 1: pen-seed reorder in draw_speech_bubble --------------------

PEN_SEED_OFFSET = 0x9AE3
PEN_SEED_ORIG = bytes.fromhex(
    "8B4600" "AB" "8BD0" "0306DB42" "A3E842" "A3EC42"
    "8B4602" "AB" "8BD8" "0306DF42" "A3EA42" "A3EE42"
    "8B4604" "03D0" "2B06DB42" "2B06DD42" "A3E642"
)
PEN_SEED_NEW = bytes.fromhex(
    "8B4600" "AB" "8B5604" "03D0" "8BC2" "2B06DD42" "A3E842"
    "8B4602" "AB" "8BD8" "0306DF42" "A3EA42"
    "8B4604" "2B06DB42" "2B06DD42" "A3E642"
    "909090"
)
assert len(PEN_SEED_ORIG) == len(PEN_SEED_NEW) == 48

# --- Patch 2: conditional pen-advance (far call into the cave) ----------

# Both sites: 4-byte `add [0xfc50],ax` immediately followed by 2-byte
# `mov cl,al` -- replaced by a 5-byte far call plus 1 NOP.
PEN_ADVANCE_SITES = [0xCB18, 0xCBB2]  # tall, small
PEN_ADVANCE_ORIG = bytes.fromhex("010650FC" "8AC8")
CAVE_SEG_OFFSET_IN_CALL = 3  # far call = 9A <off_lo><off_hi><seg_lo><seg_hi>

def pen_advance_new(cave_seg_raw):
    return bytes([0x9A, 0x00, 0x00]) + struct.pack("<H", cave_seg_raw) + bytes([0x90])

# --- The cave itself ------------------------------------------------------
# cmp word [0x42f5],0    ; current_bubble_layout_ptr -- non-zero while a
#                         ; dialogue box is actively being laid out/drawn
# jz L_ltr
# sub [0xfc50],ax        ; RTL: pen moves backward through the glyph stream
# jmp L_done
# L_ltr:
# add [0xfc50],ax        ; original behavior, unconditionally used by every
#                         ; other caller (they never touch 0x42f5)
# L_done:
# mov cl,al               ; replicates the instruction displaced from the
#                         ; call site, unaffected by which branch ran
# retf
def _build_cave_code():
    cmp_ = bytes.fromhex("833EF54200")            # cmp word [0x42f5],0
    sub_ = bytes.fromhex("290650FC")               # sub [0xfc50],ax
    add_ = bytes.fromhex("010650FC")               # add [0xfc50],ax
    movcl = bytes.fromhex("8AC8")                  # mov cl,al
    retf = bytes.fromhex("CB")

    # jz target = start of add_ (L_ltr); jmp target = start of movcl (L_done)
    jz_len, jmp_len = 2, 2
    off_after_jz = len(cmp_) + jz_len
    off_sub = off_after_jz
    off_after_jmp = off_sub + len(sub_) + jmp_len
    off_ltr = off_after_jmp
    jz_rel = off_ltr - off_after_jz
    off_done = off_ltr + len(add_)
    off_after_sub_jmp = off_sub + len(sub_) + jmp_len
    jmp_rel = off_done - off_after_sub_jmp

    assert 0 <= jz_rel <= 127 and 0 <= jmp_rel <= 127
    code = (
        cmp_
        + bytes([0x74, jz_rel])   # jz L_ltr
        + sub_
        + bytes([0xEB, jmp_rel])  # jmp L_done
        + add_                    # L_ltr:
        + movcl                   # L_done:
        + retf
    )
    return code


CAVE_CODE = _build_cave_code()
assert len(CAVE_CODE) == 20, len(CAVE_CODE)


class Patch:
    def __init__(self, name, load_offset, orig, new):
        self.name = name
        self.file_offset = MZ_HEADER_SIZE + load_offset
        self.orig = orig
        self.new = new
        assert len(self.orig) == len(self.new)


def build_patches(cave_seg_raw):
    patches = [
        Patch(
            "pen seed (draw_speech_bubble: right edge instead of left)",
            PEN_SEED_OFFSET, PEN_SEED_ORIG, PEN_SEED_NEW,
        ),
    ]
    for offset, label in zip(PEN_ADVANCE_SITES, ("tall font", "small font")):
        patches.append(Patch(
            f"conditional pen advance ({label}, far call into RTL cave)",
            offset, PEN_ADVANCE_ORIG, pen_advance_new(cave_seg_raw),
        ))
    return patches


def compute_cave_layout(load_module_len):
    """Return (pad_len, cave_load_offset, cave_seg_raw) placing the cave at
    a paragraph-aligned (16-byte) load-module offset right after the
    current end of the load module, padded with NOPs if needed."""
    pad_len = (-load_module_len) % 16
    cave_load_offset = load_module_len + pad_len
    assert cave_load_offset % 16 == 0
    cave_seg_raw = cave_load_offset // 16
    return pad_len, cave_load_offset, cave_seg_raw


def reloc_entry_for_load_offset(load_offset):
    """Simplest valid (offset, segment) decomposition: segment=0, so
    address = offset directly. Still requires the loader's relocation pass
    (adds load_base_para to the *segment* field of the target far-call
    instruction, at file offset MZ_HEADER_SIZE + this load_offset +
    CAVE_SEG_OFFSET_IN_CALL) -- segment=0 here describes where the fixup
    site is, not the value being fixed up."""
    return 0x0000, load_offset


def detect_existing_cave(data):
    """Read back whatever's actually at each pen-advance site (independent
    of the current file length, unlike compute_cave_layout) and check
    whether it's our far-call pattern pointing at a cave whose on-disk
    contents still match CAVE_CODE. Returns the shared cave_seg_raw if both
    sites agree and are internally consistent, else None."""
    seg_raws = []
    for offset in PEN_ADVANCE_SITES:
        file_offset = MZ_HEADER_SIZE + offset
        current = bytes(data[file_offset:file_offset + 6])
        if current[0] != 0x9A or current[1:3] != b"\x00\x00" or current[5] != 0x90:
            return None
        seg_raw = struct.unpack_from("<H", current, 3)[0]
        seg_raws.append(seg_raw)
    if seg_raws[0] != seg_raws[1]:
        return None
    cave_seg_raw = seg_raws[0]
    cave_file_offset = MZ_HEADER_SIZE + cave_seg_raw * 16
    if bytes(data[cave_file_offset:cave_file_offset + len(CAVE_CODE)]) != CAVE_CODE:
        return None
    return cave_seg_raw


def apply_patches(exe_path):
    with open(exe_path, "rb") as f:
        data = bytearray(f.read())

    # Fully-patched already? Detected from what's actually on disk, not
    # recomputed from the current (possibly already-grown) file length.
    existing_cave_seg_raw = detect_existing_cave(data)
    if existing_cave_seg_raw is not None:
        seed_current = bytes(data[MZ_HEADER_SIZE + PEN_SEED_OFFSET:
                                   MZ_HEADER_SIZE + PEN_SEED_OFFSET + len(PEN_SEED_NEW)])
        if seed_current == PEN_SEED_NEW:
            print(f"[patch] {exe_path.name}: already fully patched (RTL cave at load offset 0x{existing_cave_seg_raw * 16:x})")
            return False
        sys.exit(f"{exe_path}: pen-advance sites are cave-patched but pen-seed is not -- inconsistent state, refusing.")

    # Fresh/pristine file: place the cave right after the current end of
    # the (not-yet-grown) load module.
    load_module_len = len(data) - MZ_HEADER_SIZE
    pad_len, cave_load_offset, cave_seg_raw = compute_cave_layout(load_module_len)
    cave_file_offset = MZ_HEADER_SIZE + cave_load_offset

    patches = build_patches(cave_seg_raw)

    # Every site must be at its known-original bytes (fresh/pristine file).
    for patch in patches:
        current = bytes(data[patch.file_offset:patch.file_offset + len(patch.orig)])
        if current != patch.orig:
            sys.exit(
                f"{exe_path}: bytes at file offset 0x{patch.file_offset:x} ({patch.name}) "
                f"match neither the known original nor the known patched sequence "
                f"(found {current.hex()}). Refusing to patch anything -- this offset was "
                f"derived from a specific DUNEPRG.EXE build and may not apply here, or "
                f"the file is already patched with an older/incompatible version of this "
                f"script (e.g. the unconditional add->sub flip -- restore from "
                f"{exe_path.name}.orig-backup and re-run)."
            )

    backup_path = exe_path.with_suffix(exe_path.suffix + ".orig-backup")
    if not backup_path.exists():
        shutil.copy2(exe_path, backup_path)
        print(f"[patch] backed up {exe_path.name} -> {backup_path.name}")

    # 1. Append padding + cave.
    data.extend(b"\x90" * pad_len)
    assert len(data) == cave_file_offset
    data.extend(CAVE_CODE)
    print(f"[patch] {exe_path.name}: appended {pad_len}-byte pad + {len(CAVE_CODE)}-byte RTL cave "
          f"at load offset 0x{cave_load_offset:x} (raw segment 0x{cave_seg_raw:x})")

    # 2. In-place patches (pen seed + both pen-advance far calls).
    for patch in patches:
        data[patch.file_offset:patch.file_offset + len(patch.new)] = patch.new
        print(f"[patch] {exe_path.name}: applied {patch.name} at file offset 0x{patch.file_offset:x}")

    # 3. MZ header: bump e_cblp (bytes used in the file's last 512-byte
    # page) to cover the newly-appended bytes. The growth here (pad+cave,
    # well under 512 bytes) always fits within the existing last page --
    # e_cp (total page count) never needs to change for this cave size.
    new_file_len = len(data)
    e_cp = struct.unpack_from("<H", data, E_CP_OFFSET)[0]
    old_e_cblp = struct.unpack_from("<H", data, E_CBLP_OFFSET)[0]
    expected_last_page_bytes = new_file_len - (e_cp - 1) * 512
    if not (0 < expected_last_page_bytes <= 512):
        sys.exit(
            f"{exe_path}: cave growth pushed the file past its current last MZ page "
            f"(e_cp={e_cp}, would need {expected_last_page_bytes} bytes in the last page) -- "
            f"this script only handles growth that fits in-page; bumping e_cp itself needs "
            f"new code, not yet implemented."
        )
    new_e_cblp = expected_last_page_bytes % 512  # 512 encodes as 0 per MZ convention
    struct.pack_into("<H", data, E_CBLP_OFFSET, new_e_cblp)
    print(f"[patch] {exe_path.name}: e_cblp {old_e_cblp} -> {new_e_cblp} (file grew to {new_file_len} bytes)")

    # 4. Relocation table: 2 new entries, one per far-call site's segment
    # field, appended right after the existing e_crlc entries (verified
    # there's room before code starts at file offset 512).
    e_crlc = struct.unpack_from("<H", data, E_CRLC_OFFSET)[0]
    reloc_write_offset = RELOC_TABLE_OFFSET + e_crlc * 4
    new_entries = []
    for offset in PEN_ADVANCE_SITES:
        seg_field_load_offset = offset + CAVE_SEG_OFFSET_IN_CALL
        seg, off = reloc_entry_for_load_offset(seg_field_load_offset)
        new_entries.append((off, seg))

    needed = reloc_write_offset + len(new_entries) * 4
    if needed > MZ_HEADER_SIZE:
        sys.exit(f"{exe_path}: not enough header room for {len(new_entries)} new relocation "
                 f"entries (would need up to file offset 0x{needed:x}, header is {MZ_HEADER_SIZE} bytes).")
    for off, seg in new_entries:
        struct.pack_into("<HH", data, reloc_write_offset, off, seg)
        print(f"[patch] {exe_path.name}: added relocation entry (offset=0x{off:x}, segment=0x{seg:x}) at file offset 0x{reloc_write_offset:x}")
        reloc_write_offset += 4
    struct.pack_into("<H", data, E_CRLC_OFFSET, e_crlc + len(new_entries))
    print(f"[patch] {exe_path.name}: e_crlc {e_crlc} -> {e_crlc + len(new_entries)}")

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
