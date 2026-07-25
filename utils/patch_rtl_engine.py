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

# Debugging toggles for the token-substitution reversal (sietch name /
# occupation / digits): a full application of both caused a hang in-game
# that isolated static analysis + an algorithm-level Python simulation
# (see dune_rtl_engine_patch_moonshot memory) couldn't reproduce or
# explain, so these let each half be tested independently to narrow down
# which one is actually at fault before re-enabling both.
ENABLE_NAME_REVERSAL = False
ENABLE_QUANTITY_REVERSAL = False

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

# Inter-word space advance is flipped IN PLACE, no cave. The original
# sequence at 0x9822 is:
#     push dx            ; dx currently = the line's inter-word spacing
#     add dx,[0xfc50]    ; dx = spacing + pen
#     ...remainder tweak (inc dx)...
#     mov [0xfc50],dx    ; pen += spacing
#     pop dx             ; restore spacing
# The push/pop are load-bearing: font_draw_glyph_func preserves dx across
# every glyph, so `dx = spacing` must survive the whole line for the 2nd,
# 3rd, ... space to advance correctly. (An earlier version replaced the
# push and nop'd the pop, which left dx = garbage after the first space and
# made later words pile up.) For RTL we keep push/pop and only change the
# arithmetic: neutralise the `+pen` (dx stays = spacing) and flip the store
# to subtract, so pen -= spacing. The remainder `inc dx` is kept as-is
# (it enlarges the gap by 1px for some words, which is still what we want
# when subtracting).
# --- Token-substitution reversal caves (sietch name / occupation / digits) ---
#
# Root cause (found by static analysis of the token-expansion function at
# load offset 0x9609, after `dosbox_continue`-based live tracing turned out
# to be non-functional this session -- see dune_rtl_engine_patch_moonshot
# memory): draw_subtitle_body's own byte-stream loop treats any high-bit
# byte (0x80+ -- the ma-ml/m@@ name tokens AND the mq/mr quantity tokens)
# as a mere word-boundary marker; the ACTUAL substitution happens earlier,
# in 0x9609, which for a name token resolves a COMMAND1-table pointer and
# copies its bytes FORWARD (stosb, si++/di++) into the phrase's output
# buffer, and for a quantity token (0x9694) computes decimal digits
# MOST-SIGNIFICANT-FIRST and also writes them forward via stosb. Both feed
# the same buffer that draw_subtitle_body later draws via the RTL-patched
# path -- so both come out backwards, for the same reason as everything
# else this patch already fixes: natural/forward content drawn with a
# leftward-walking pen reads reversed.
#
# Fix: bracket each substitution's write span. Two "entry mark" caves
# record `di` (the buffer write position) in `[0x42EE]` (subtitle_line_
# start_y, freed by the pen-seed patch alongside [0x42EC] -- both dead
# fields recycled here) at the moment a substitution begins -- one for
# name tokens (entered via 0x9647), one for quantity tokens (entered via
# 0x9694, a different code path, so it needs its own marker). Two "exit"
# caves each replace their token type's "done, resume the outer loop"
# jump; if the RTL flag [0x42EC] is set, they reverse the bytes written
# between the recorded start and the current `di` (ES-relative, matching
# where stosb/lodsb actually read/write throughout this function) before
# resuming.
#
# All four assembled with nasm (not hand-encoded -- this session already
# cost real time to two separate hand-arithmetic mistakes elsewhere; see
# dune_rtl_engine_patch_moonshot / feedback_dosbox_tracing_pitfalls) from:
#
#   entry_mark_cave:                      ; replaces 964D-9651 (5B)
#       add     bp, 4                     ; replayed
#       mov     si, ax                    ; replayed
#       mov     word [0x42ee], di         ; NEW: record output-span start
#       retf
#
#   qty_entry_mark_cave:                  ; replaces 969C-96A2 (7B)
#       mov     ax, [bp+0]                ; replayed (original uses the
#                                         ; longer disp16 encoding; this
#                                         ; replay's shorter disp8 encoding
#                                         ; is behaviourally identical)
#       cmp     bl, 0x92                  ; replayed
#       mov     word [0x42ee], di         ; NEW: record output-span start
#       retf
#
#   name_exit_cave:                       ; replaces 967C-9680 (5B)
#       mov     ds, [bp+2]                ; replayed
#       cmp     byte [0x42ec], 0
#       jz      .skip
#       push ax / push bx / push di
#       mov     bx, [0x42ee]              ; left = recorded start
#       dec     di                        ; right = current end - 1
#   .loop: cmp bx,di / jae .done
#       mov al,[es:bx] / xchg al,[es:di] / mov [es:bx],al
#       inc bx / dec di / jmp short .loop
#   .done: pop di / pop bx / pop ax
#   .skip:
#       add     sp, 4                     ; discard far-call return addr
#       jmp     0x0:0x9618                ; far jmp, seg relocated to main
#
#   quantity_exit_cave:                   ; replaces 96DE-96E2 (5B, incl.
#                                          ; the freed byte at 96E2 -- see
#                                          ; the jc-redirect patch below)
#       pop     bp                        ; replayed
#       <identical body to name_exit_cave from the cmp onward>
ENTRY_MARK_CAVE = bytes.fromhex("83c50489c6893eee42cb")

QTY_ENTRY_MARK_CAVE = bytes.fromhex("8b460080fb92893eee42cb")

NAME_EXIT_CAVE = bytes.fromhex(
    "8e5e02803eec4200741c5053578b1eee424f39fb730d"
    "268a07268605268807434febef5f5b5883c404ea18960000"
)
NAME_EXIT_FARJMP_LOCAL_OFF = 0x2C  # offset within NAME_EXIT_CAVE of the far-jmp's seg field

QUANTITY_EXIT_CAVE = bytes.fromhex(
    "5d803eec4200741c5053578b1eee424f39fb730d"
    "268a07268605268807434febef5f5b5883c404ea18960000"
)
QUANTITY_EXIT_FARJMP_LOCAL_OFF = 0x2A  # offset within QUANTITY_EXIT_CAVE of the far-jmp's seg field

CAVES = [
    ("pen_advance", PEN_ADVANCE_CAVE),
    ("set", SET_CAVE),
    ("clear", CLEAR_CAVE),
]
if ENABLE_NAME_REVERSAL:
    CAVES += [
        ("entry_mark", ENTRY_MARK_CAVE),
        ("name_exit", NAME_EXIT_CAVE),
    ]
if ENABLE_QUANTITY_REVERSAL:
    CAVES += [
        ("qty_entry_mark", QTY_ENTRY_MARK_CAVE),
        ("quantity_exit", QUANTITY_EXIT_CAVE),
    ]

# Caves whose own bytes contain a far-jmp-back-to-main needing its OWN
# relocation entry (segment field, relative to that cave's position once
# placed in the blob). (cave_name, local_offset_of_seg_field).
CAVE_INTERNAL_FARJMPS = []
if ENABLE_NAME_REVERSAL:
    CAVE_INTERNAL_FARJMPS.append(("name_exit", NAME_EXIT_FARJMP_LOCAL_OFF))
if ENABLE_QUANTITY_REVERSAL:
    CAVE_INTERNAL_FARJMPS.append(("quantity_exit", QUANTITY_EXIT_FARJMP_LOCAL_OFF))


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
    em = blob_offsets.get("entry_mark")
    qem = blob_offsets.get("qty_entry_mark")
    ne = blob_offsets.get("name_exit")
    qe = blob_offsets.get("quantity_exit")

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

    # SPACE advance, flipped in place (keeps the load-bearing push/pop dx
    # at 0x9822/0x9837 that carry the line's spacing across every glyph):
    #   0x9823 `add dx,[0xfc50]` (dx = spacing + pen) -> 4 NOPs (dx stays
    #          = spacing), and
    #   0x9833 `mov [0xfc50],dx` (pen = spacing + pen) -> `sub [0xfc50],dx`
    #          (pen -= spacing).
    # 0x982E `inc dx` and the 0x9822/0x9837 push/pop are left untouched.
    sites.append(Site(
        "space add+pen neutralised (dx stays = spacing)",
        0x9823, "031650FC", lambda: b"\x90\x90\x90\x90",
    ))
    sites.append(Site(
        "space store -> subtract (pen -= spacing, RTL)",
        0x9833, "891650FC", lambda: bytes.fromhex("291650FC"),
    ))

    # --- Token-substitution reversal (sietch name / occupation / digits) ---
    # See the CAVES section above for full derivation. Gated behind
    # ENABLE_NAME_REVERSAL / ENABLE_QUANTITY_REVERSAL (see their
    # definitions) while debugging an in-game hang neither static analysis
    # nor an algorithm-level simulation could explain -- applying each
    # half independently narrows down which one is actually at fault.

    if ENABLE_NAME_REVERSAL:
        # Name/m@@ token entry, 0x964D: `add bp,4; mov si,ax` (5B) -> far
        # call entry_mark_cave (replays both, marks di as the output-span
        # start).
        sites.append(Site(
            "name-token entry mark (records output-span start)",
            0x964D, "83C504" "8BF0",
            (lambda o=em: far_call(cave_seg_raw, o)),
            reloc_field_rel=3,
        ))

        # Name/m@@ token exit, 0x967C: `mov ds,[bp+2]; jmp short 0x9618`
        # (5B) -> far call name_exit_cave (replays the DS restore,
        # conditionally reverses the output span, far-jmps back to 0x9618).
        sites.append(Site(
            "name-token exit (conditional reversal)",
            0x967C, "8E5E02" "EB97",
            (lambda o=ne: far_call(cave_seg_raw, o)),
            reloc_field_rel=3,
        ))

    if ENABLE_QUANTITY_REVERSAL:
        # Quantity token entry, 0x969C: `mov ax,[bp+0]` (long disp16
        # encoding, 4B) + `cmp bl,0x92` (3B) = 7B -> far call
        # qty_entry_mark_cave + 2 nops (replays both, marks di as the
        # output-span start).
        sites.append(Site(
            "quantity-token entry mark (records output-span start)",
            0x969C, "8B860000" "80FB92",
            (lambda o=qem: far_call(cave_seg_raw, o) + b"\x90\x90"),
            reloc_field_rel=3,
        ))

        # draw_subtitle_body's own early-exit `jc 0x96e2` (at 0x96ED) is
        # redirected to the functionally-identical bare `ret` at 0x9693
        # (the normal exit of the SAME function, after its "add sp,0x32"
        # -- 0x9693 is just the `ret` byte itself, landing there skips the
        # add exactly like landing at 0x96e2 always did). Frees byte
        # 0x96E2 -- the quantity-token exit site below needs it as its 5th
        # byte and nothing else in the file references 0x96E2 once this is
        # applied. Same-length operand swap, the lowest-risk patch
        # category used throughout this file.
        sites.append(Site(
            "draw_subtitle_body early-exit jc retarget (frees 0x96E2)",
            0x96ED, "72F3", lambda: bytes.fromhex("72A4"),
        ))

        # Quantity token exit, 0x96DE: `pop bp; jmp 0x9618` (4B) + the
        # now-freed `ret` at 0x96E2 (1B) = 5B -> far call
        # quantity_exit_cave (replays the bp pop, conditionally reverses
        # the output span, far-jmps back to 0x9618). MUST be applied
        # together with the jc-retarget site above (both refuse/no-op
        # idempotently as a pair, same as every other multi-site group in
        # this file).
        sites.append(Site(
            "quantity-token exit (conditional reversal; consumes freed 0x96E2)",
            0x96DE, "5D" "E936FF" "C3",
            (lambda o=qe: far_call(cave_seg_raw, o)),
            reloc_field_rel=3,
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
    """Return True if the pen-advance sites AND the (newer) name-token
    entry-mark site already hold far calls to blobs with matching bytes.
    (Read back from disk, not recomputed from length.) Checking a site from
    each generation of this script avoids a false "already patched" on a
    file only an OLDER version of this script touched (e.g. one without
    the token-substitution reversal caves), which would otherwise cause
    apply_patches to skip installing the sites this version adds."""
    checks = [(0xCB18, PEN_ADVANCE_CAVE), (0xCBB2, PEN_ADVANCE_CAVE)]
    if ENABLE_NAME_REVERSAL:
        checks.append((0x964D, ENTRY_MARK_CAVE))
    if ENABLE_QUANTITY_REVERSAL:
        checks.append((0x969C, QTY_ENTRY_MARK_CAVE))
    for off, expected in checks:
        fo = MZ_HEADER_SIZE + off
        cur = bytes(data[fo:fo + 5])
        if cur[0] != 0x9A:
            return False
        cave_off = struct.unpack_from("<H", cur, 1)[0]
        cave_seg_raw = struct.unpack_from("<H", cur, 3)[0]
        cave_file = MZ_HEADER_SIZE + cave_seg_raw * 16 + cave_off
        if bytes(data[cave_file:cave_file + len(expected)]) != expected:
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

    # 2. In-place patches; collect relocation field offsets. Starts with
    # the blob-internal far-jmp-back-to-main sites (name_exit_cave and
    # quantity_exit_cave each jump to 0x0:0x9618 with the segment half
    # needing the same load-time fixup as every other far-call/jmp here).
    reloc_offsets = []
    for cave_name, local_off in CAVE_INTERNAL_FARJMPS:
        seg_field_load_offset = blob_load_offset + blob_offsets[cave_name] + local_off
        reloc_offsets.append(seg_field_load_offset)
        print(f"[patch] {cave_name} internal far-jmp seg field @ load offset 0x{seg_field_load_offset:x}")
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
