#!/usr/bin/env python3

"""
patch_rtl_engine.py - EXPERIMENTAL. Patches game/DUNEPRG.EXE so the
dialogue/subtitle system (draw_subtitle_body, used for spoken PHRASE11/
PHRASE12 text) renders Hebrew natively right-to-left, letting the engine's
own runtime word-wrap (layout_subtitle_lines) do the wrapping on natural,
un-pre-reversed, un-pre-wrapped content. See the dune_rtl_engine_patch_
moonshot project memory for the full multi-session derivation.

WHY THE EARLIER (SHELVED) VERSION FAILED
----------------------------------------
The first version flipped the shared glyph primitive font_draw_glyph_func's
pen advance (add [pen],ax -> sub) whenever current_bubble_layout_ptr
([0x42F5]) was non-zero. Three things broke:

1. [0x42F5] means "a dialogue interaction is active", NOT "this text is
   natural-order RTL subtitle text". It stays set for the *whole* dialogue,
   including while the option MENU (pre-reversed COMMAND1 content, drawn by
   a different routine) is drawn -> menu rendered backwards and off-area.
2. The intro narration (also drawn with [0x42F5] set, via a non-
   draw_subtitle_body path, but through the same shared font_draw_glyph_func)
   got flipped too -> pre-reversed intro content drawn RTL = garbled.
   (Confirmed decisively: neutralising just the cave's RTL branch in RAM
   made the intro render perfectly.)
3. Even for the subtitle text itself, only the GLYPH-width advance lives in
   font_draw_glyph_func. The inter-word SPACE advance and the justification
   pre-adjust are inline in draw_subtitle_body and still moved the pen the
   OTHER way -> glyphs and spaces fought -> overlapping/garbled text.

THE FIX IMPLEMENTED HERE
------------------------
Gate the pen flip on a NEW flag that is true only while draw_subtitle_body
is actually drawing its glyphs, and flip the space advance to match:

* RTL flag = byte [0x42EC]. This is subtitle_line_start_x, which the
  pen-seed reorder patch (below) proved dead (written once, never read) and
  stopped writing -- so it is a free, zero-initialised game-data byte. It is
  read/written DS-relative; every text-draw path in this game runs with
  DS = the game data segment (verified live for subtitle, menu and intro
  draws), so a DS-relative flag is reliable here.

* set_cave / clear_cave: draw_subtitle_body sets [0x42EC]=1 immediately
  before its per-line draw loop (hook at 0x9711) and clears it =0 right
  after (hook at 0x986B, the function tail). Menus and the intro never call
  draw_subtitle_body, so they always see [0x42EC]=0 -> LTR, untouched.

* pen_advance_cave: font_draw_glyph_func's `add [0xFC50],ax; mov cl,al`
  becomes a far call here; the cave subtracts when [0x42EC]!=0, adds
  otherwise, then replays `mov cl,al`.

* space_cave: draw_subtitle_body's inter-word advance `push dx;
  add dx,[0xFC50]` (dx starts as the space width) becomes `dx = pen - width`
  so spaces move the pen LEFT like the glyphs; the paired `pop dx` and the
  remainder `inc dx` are adjusted to match (pop->nop, inc->dec).

* Pen seed: draw_speech_bubble already seeds subtitle_pen_x from the box's
  RIGHT edge (the reorder patch below), and every line reseeds from it, so
  each line starts at the right edge and the flipped advances walk left.

All hooks that move engine code into the appended cave segment use only
DS-relative or register operations (never a near call/jmp back into the
main code segment, which the cave -- a separate appended segment -- cannot
reach), so no far-return-into-main gymnastics are needed except the two
explicit stack fixups documented at the caves.

The appended cave segment is reached by 16-bit far calls (a near call can't
address past the 0xFFFF real-mode segment limit from CS=0); each far call's
segment half gets a new MZ relocation-table entry so the loader fixes it up
to the real load segment at run time. The file grows by the cave blob;
e_cblp (bytes-in-last-page) and e_crlc (relocation count) are updated. The
relocation entry format was verified against the program's own DS=SS=ES
startup immediate (entry 0 = offset 2 / segment 1 -> load addr 0x12,
matching the `mov ax,0xECF` operand at load offset 0x12).

Justification: the inline justify pre-adjust at 0x97B6 is left as `add` for
now (JUSTIFY_FLIP below is False). Flipping it is the next tuning knob if
justified lines look off; it is isolated so it can be toggled without
touching anything else.

Usage:  ./patch_rtl_engine.py [--exe PATH]
Idempotent; refuses on unrecognised bytes; backs up once to
<exe>.orig-backup before the first write.
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

MZ_HEADER_SIZE = 512
RELOC_TABLE_OFFSET = 0x1C
E_CBLP_OFFSET = 0x02
E_CP_OFFSET = 0x04
E_CRLC_OFFSET = 0x06

JUSTIFY_FLIP = False  # flip the 0x97B6 justify pre-adjust add->sub (tuning)

# ---------------------------------------------------------------------------
# Cave blob: independent little routines the in-place far calls jump to.
# Offsets within the blob are computed after their bytes are known.
# ---------------------------------------------------------------------------

# pen_advance_cave: replaces `add [0xFC50],ax; mov cl,al` in both
# font_draw_glyph_func bodies. Subtract (RTL) iff [0x42EC]!=0.
# font_get_draw_position has just loaded dx = pen_x (= [0xfc50]) and the
# glyph is blitted at that dx AFTER this cave runs. For LTR the pen is the
# glyph's LEFT edge (draw at dx, then pen += width). For RTL we want the
# glyph's RIGHT edge at the pen, so we both retreat the pen (pen -= width)
# AND move the blit position dx left by the same width -- otherwise the
# glyph is blitted with its left edge at the pen and the whole line is
# shoved one glyph-width to the right, spilling past the box's right edge.
PEN_ADVANCE_CAVE = bytes.fromhex(
    "803EEC4200"  # cmp byte [0x42ec],0
    "7408"        # jz +8 (to the add branch)
    "290650FC"    # sub [0xfc50],ax        (RTL: pen -= width)
    "2BD0"        # sub dx,ax              (RTL: blit position -= width)
    "EB04"        # jmp +4 (skip add)
    "010650FC"    # add [0xfc50],ax        (LTR, original: pen += width, dx unchanged)
    "8AC8"        # mov cl,al              (replayed displaced instruction)
    "CB"          # retf
)

# set_cave: replays the two displaced movs that seed the first line's pen,
# then raises the RTL flag. Reached from the 0x9711 hook; returns to 0x9719
# where `call font_set_draw_position(dx,bx)` consumes dx/bx.
SET_CAVE = bytes.fromhex(
    "8B16E842"    # mov dx,[0x42e8]        (subtitle_pen_x = right edge)
    "8B1EEA42"    # mov bx,[0x42ea]        (subtitle_pen_y)
    "C606EC4201"  # mov byte [0x42ec],1    (RTL flag on)
    "CB"          # retf
)

# clear_cave: replays the displaced tail (save pen for next batch, dec si),
# lowers the RTL flag, and returns (FAR) to draw_subtitle_body's own `ret`
# at 0x9874, which is deliberately left in place. Reached from 0x986B.
#
# It must NOT do the caller's near return itself: this cave lives in the
# appended segment, so a `retn` would return within the CAVE's code segment
# (0x1221+load_base) instead of the main code segment -- it would jump to
# cave_seg:<main-offset> = garbage, which is exactly the invalid-opcode
# crash the previous version hit (the stack filled with the cave segment
# and execution ran off into the data segment). Instead `retf` lands back
# at 0x9870 in main, the trailing NOPs fall through to the untouched
# `ret` at 0x9874, and THAT does the near return in the correct segment.
CLEAR_CAVE = bytes.fromhex(
    "8916E842"    # mov [0x42e8],dx
    "891EEA42"    # mov [0x42ea],bx
    "4E"          # dec si
    "C606EC4200"  # mov byte [0x42ec],0    (RTL flag off)
    "CB"          # retf  (back to 0x9870 in main; 0x9874 `ret` does the rest)
)

# space_cave: inter-word advance. On entry dx = space width. Compute
# dx = pen - width so the pen walks LEFT. Reached from the 0x9822 hook;
# returns to 0x9827. (The paired push/pop dx are removed by the in-place
# patches, so the cave neither pushes nor pops.)
SPACE_CAVE = bytes.fromhex(
    "8B0650FC"    # mov ax,[0xfc50]
    "2BC2"        # sub ax,dx             (ax = pen - width)
    "8BD0"        # mov dx,ax
    "CB"          # retf
)

CAVES = [
    ("pen_advance", PEN_ADVANCE_CAVE),
    ("set", SET_CAVE),
    ("clear", CLEAR_CAVE),
    ("space", SPACE_CAVE),
]


def build_blob():
    """Concatenate the caves, returning (blob_bytes, {name: offset_in_blob})."""
    blob = bytearray()
    offsets = {}
    for name, code in CAVES:
        offsets[name] = len(blob)
        blob += code
    return bytes(blob), offsets


# ---------------------------------------------------------------------------
# In-place patch sites. Each entry: (name, load_offset, orig_hex, builder)
# where builder(cave_seg_raw, cave_off) -> new_bytes (same length as orig).
# reloc_field_off, if not None, is the load offset of the far-call segment
# field needing a relocation entry.
# ---------------------------------------------------------------------------

def far_call(cave_seg_raw, cave_off):
    """9A <off16> <seg16> ; the seg half is relocated by the loader."""
    return bytes([0x9A]) + struct.pack("<H", cave_off) + struct.pack("<H", cave_seg_raw)


class Site:
    def __init__(self, name, load_offset, orig_hex, new_builder, reloc_field_rel=None):
        self.name = name
        self.load_offset = load_offset
        self.file_offset = MZ_HEADER_SIZE + load_offset
        self.orig = bytes.fromhex(orig_hex)
        self.new_builder = new_builder
        # reloc_field_rel: byte offset within this site of a far-call seg
        # field that needs a relocation entry (None if none).
        self.reloc_field_rel = reloc_field_rel

    def reloc_load_offset(self):
        return None if self.reloc_field_rel is None else self.load_offset + self.reloc_field_rel


# Pen-seed reorder in draw_speech_bubble: seed subtitle_pen_x from the box's
# RIGHT edge instead of the left, and stop writing the (dead) line_start_x/y
# fields -- which is what frees [0x42EC]/[0x42EE] for use as the RTL flag.
# Pure 48-byte in-place reorder, no cave, no reloc. (Unchanged from the
# earlier version; still required.)
PEN_SEED_OFFSET = 0x9AE3
PEN_SEED_ORIG = (
    "8B4600" "AB" "8BD0" "0306DB42" "A3E842" "A3EC42"
    "8B4602" "AB" "8BD8" "0306DF42" "A3EA42" "A3EE42"
    "8B4604" "03D0" "2B06DB42" "2B06DD42" "A3E642"
)
PEN_SEED_NEW = (
    "8B4600" "AB" "8B5604" "03D0" "8BC2" "2B06DD42" "A3E842"
    "8B4602" "AB" "8BD8" "0306DF42" "A3EA42"
    "8B4604" "2B06DB42" "2B06DD42" "A3E642"
    "909090"
)


def build_sites(cave_seg_raw, blob_offsets):
    pa = blob_offsets["pen_advance"]
    setc = blob_offsets["set"]
    clr = blob_offsets["clear"]
    spc = blob_offsets["space"]

    sites = []

    # Pen-seed reorder (no cave / no reloc).
    sites.append(Site(
        "pen seed (draw_speech_bubble right edge; frees [0x42EC])",
        PEN_SEED_OFFSET, PEN_SEED_ORIG, lambda: bytes.fromhex(PEN_SEED_NEW),
    ))

    # Pen advance, tall + small font bodies: `add [0xfc50],ax; mov cl,al`
    # (6 bytes) -> far call pen_advance_cave + nop.
    for label, off in (("tall", 0xCB18), ("small", 0xCBB2)):
        sites.append(Site(
            f"pen advance ({label}) -> far call RTL cave",
            off, "010650FC" "8AC8",
            (lambda o=pa: far_call(cave_seg_raw, o) + b"\x90"),
            reloc_field_rel=3,
        ))

    # SET hook 0x9711: `mov dx,[0x42e8]; mov bx,[0x42ea]` (8 bytes)
    # -> far call set_cave + 3 nops. (set_cave replays both movs.)
    sites.append(Site(
        "RTL flag set hook (draw_subtitle_body pre-draw)",
        0x9711, "8B16E842" "8B1EEA42",
        (lambda o=setc: far_call(cave_seg_raw, o) + b"\x90\x90\x90"),
        reloc_field_rel=3,
    ))

    # CLEAR hook 0x986B: `mov [0x42e8],dx; mov [0x42ea],bx; dec si` (9 bytes,
    # NOT the trailing `ret` at 0x9874) -> far call clear_cave + 4 nops.
    # clear_cave replays the tail, clears the flag, and `retf`s back to
    # 0x9870; the NOPs fall through to the untouched `ret` at 0x9874, which
    # does the caller's near return in the correct (main) code segment.
    sites.append(Site(
        "RTL flag clear hook (draw_subtitle_body tail; keeps 0x9874 ret)",
        0x986B, "8916E842" "891EEA42" "4E",
        (lambda o=clr: far_call(cave_seg_raw, o) + b"\x90\x90\x90\x90"),
        reloc_field_rel=3,
    ))

    # SPACE hook 0x9822: `push dx; add dx,[0xfc50]` (5 bytes)
    # -> far call space_cave (dx = pen - width).
    sites.append(Site(
        "space advance -> RTL (subtract width)",
        0x9822, "52" "031650FC",
        (lambda o=spc: far_call(cave_seg_raw, o)),
        reloc_field_rel=3,
    ))

    # SPACE remainder distribution 0x982E: inc dx -> dec dx.
    sites.append(Site(
        "space remainder inc->dec (RTL)",
        0x982E, "42", lambda: b"\x4A",
    ))

    # SPACE paired pop 0x9837: pop dx -> nop (the matching push was removed).
    sites.append(Site(
        "space paired pop dx -> nop",
        0x9837, "5A", lambda: b"\x90",
    ))

    # Optional: justify pre-adjust 0x97B6 add->sub.
    if JUSTIFY_FLIP:
        sites.append(Site(
            "justify pre-adjust add->sub (RTL)",
            0x97B6, "011650FC", lambda: bytes.fromhex("291650FC"),
        ))

    return sites


# ---------------------------------------------------------------------------
# Apply / detect
# ---------------------------------------------------------------------------

def compute_blob_layout(load_module_len):
    pad_len = (-load_module_len) % 16
    blob_load_offset = load_module_len + pad_len
    assert blob_load_offset % 16 == 0
    return pad_len, blob_load_offset, blob_load_offset // 16


def detect_patched(data):
    """Return True if the pen-advance sites already hold our far call to a
    blob whose bytes match. (Read back from disk, not recomputed from length.)"""
    for off in (0xCB18, 0xCBB2):
        fo = MZ_HEADER_SIZE + off
        cur = bytes(data[fo:fo + 6])
        if cur[0] != 0x9A or cur[5] != 0x90:
            return False
        cave_off = struct.unpack_from("<H", cur, 1)[0]
        cave_seg_raw = struct.unpack_from("<H", cur, 3)[0]
        cave_file = MZ_HEADER_SIZE + cave_seg_raw * 16 + cave_off
        if bytes(data[cave_file:cave_file + len(PEN_ADVANCE_CAVE)]) != PEN_ADVANCE_CAVE:
            return False
    return True


def apply_patches(exe_path):
    with open(exe_path, "rb") as f:
        data = bytearray(f.read())

    if detect_patched(data):
        print(f"[patch] {exe_path.name}: already patched (RTL native-dialogue engine)")
        return False

    blob, blob_offsets = build_blob()
    load_module_len = len(data) - MZ_HEADER_SIZE
    pad_len, blob_load_offset, cave_seg_raw = compute_blob_layout(load_module_len)
    blob_file_offset = MZ_HEADER_SIZE + blob_load_offset

    sites = build_sites(cave_seg_raw, blob_offsets)

    # Verify every site is at its known-original bytes.
    for s in sites:
        cur = bytes(data[s.file_offset:s.file_offset + len(s.orig)])
        if cur != s.orig:
            new = s.new_builder()
            if cur == new:
                continue  # partially applied? treat as fine
            sys.exit(
                f"{exe_path}: bytes at file offset 0x{s.file_offset:x} ({s.name}) "
                f"are {cur.hex()}, expected original {s.orig.hex()}. Refusing to patch. "
                f"Restore from {exe_path.name}.orig-backup (and re-run the location-name "
                f"patch) to get a clean base, then re-run."
            )
        if len(s.new_builder()) != len(s.orig):
            sys.exit(f"internal error: site {s.name} length mismatch")

    backup_path = exe_path.with_suffix(exe_path.suffix + ".orig-backup")
    if not backup_path.exists():
        shutil.copy2(exe_path, backup_path)
        print(f"[patch] backed up {exe_path.name} -> {backup_path.name}")

    # 1. Append pad + cave blob.
    data.extend(b"\x90" * pad_len)
    assert len(data) == blob_file_offset
    data.extend(blob)
    print(f"[patch] appended {pad_len}B pad + {len(blob)}B cave blob at load offset "
          f"0x{blob_load_offset:x} (segment raw 0x{cave_seg_raw:x}); caves at "
          + ", ".join(f"{n}=+0x{o:x}" for n, o in blob_offsets.items()))

    # 2. In-place patches; collect relocation field offsets.
    reloc_offsets = []
    for s in sites:
        new = s.new_builder()
        data[s.file_offset:s.file_offset + len(new)] = new
        r = s.reloc_load_offset()
        if r is not None:
            reloc_offsets.append(r)
        print(f"[patch] {s.name} @ 0x{s.file_offset:x}")

    # 3. MZ header: e_cblp (bytes used in the last 512-byte page).
    new_len = len(data)
    e_cp = struct.unpack_from("<H", data, E_CP_OFFSET)[0]
    old_cblp = struct.unpack_from("<H", data, E_CBLP_OFFSET)[0]
    last_page = new_len - (e_cp - 1) * 512
    if not (0 < last_page <= 512):
        sys.exit(f"{exe_path}: cave growth crossed a 512-byte page boundary "
                 f"(last_page={last_page}); e_cp bump not implemented.")
    struct.pack_into("<H", data, E_CBLP_OFFSET, last_page % 512)
    print(f"[patch] e_cblp {old_cblp} -> {last_page % 512} (file {new_len} bytes)")

    # 4. Relocation table: one new entry per far-call segment field.
    e_crlc = struct.unpack_from("<H", data, E_CRLC_OFFSET)[0]
    write_at = RELOC_TABLE_OFFSET + e_crlc * 4
    if write_at + len(reloc_offsets) * 4 > MZ_HEADER_SIZE:
        sys.exit(f"{exe_path}: not enough header room for {len(reloc_offsets)} relocations.")
    for r in reloc_offsets:
        # segment=0 so address == offset (the fixup site's own load offset).
        struct.pack_into("<HH", data, write_at, r & 0xFFFF, 0x0000)
        print(f"[patch] +reloc for far-call seg field at load offset 0x{r:x}")
        write_at += 4
    struct.pack_into("<H", data, E_CRLC_OFFSET, e_crlc + len(reloc_offsets))
    print(f"[patch] e_crlc {e_crlc} -> {e_crlc + len(reloc_offsets)}")

    with open(exe_path, "wb") as f:
        f.write(data)
    return True


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--exe", type=Path, default=GAME_DIR / EXE_NAME)
    args = ap.parse_args()
    if not args.exe.exists():
        sys.exit(f"{args.exe} not found")
    apply_patches(args.exe)


if __name__ == "__main__":
    main()
