#!/usr/bin/env python3

"""
patch_rtl_engine_cd.py - EXPERIMENTAL. CD-build port of patch_rtl_engine.py
(see that file's docstring for the full derivation of the RTL dialogue
rendering scheme -- pen-flip glyph advance, right-edge pen seed, per-line
RTL flag, and the name/quantity token-substitution reversal). This patches
DUNE_CD/DNCDPRG.EXE (Cryo Dune 3.7 CD, SHA1 c55e9e35c24941d8590c068c69cb6cee
85e4afcb) instead of the floppy's game/DUNEPRG.EXE.

The CD build is a different compile of the same engine, not a byte-for-byte
match to the floppy -- every patch site below was independently re-derived
from the labelled disassembly at
https://thomas.fach-pedersen.net/dune/cryo-dune-3.7-cd-dncdprg.html (Chani
Disassembler output by Thomas Fach-Pedersen / madmoose, whose symbol names
this file's addresses and comments are taken from), then cross-checked
against a full ndisasm pass over the CD binary's own code segment. The
underlying architecture -- draw_subtitle_body's per-line pen-flip, the
subtitle_line_start_x/y dead-field reuse as the RTL flag + span marker, the
format_interpolated_string token-substitution pre-pass and its ES-vs-DS
segment instability -- turned out to match the floppy build's design almost
exactly, which is what makes this port possible at all.

CURRENT STATUS: BLOCKED, NOT FUNCTIONAL YET
--------------------------------------------
Applying this script produces a CD exe that boots and plays the intro
without crashing, but the RTL rendering never visibly engages -- dialogue
still renders as plain LTR text. Root cause, confirmed live via dosbox-mcp:
this CD build's bump allocator (bump_allocate_bump_cx_bytes, fed by
_word_2C318_allocator_last_free_seg) is not bounded by the ARENA_CLEAR_TOP
constant the way the module docstring below originally assumed -- that
constant only bounds the ONE-TIME startup clear, not the allocator's actual
runtime growth. During the intro (logos, video frames, audio), the game
bump-allocates real resource data across the cave blob's location well
before the first subtitle is ever drawn, silently overwriting the cave with
legitimate game data. Reading the cave's live memory location after the
intro's first subtitle line confirmed this directly: it held clearly
structured resource-table-shaped bytes, not the cave code that was written
there at load time.

Net effect: there is no location past the load module's own static content
that's safe from this allocator long-term, no matter how far out the cave
is placed -- unlike the floppy build, appending a cave after the file's own
content is not a viable technique for this CD build at all. A real fix
needs the cave to live inside the STATIC region instead (code segment
0x0-0xf4b0, or the seg001 data below the 0x3cbc bump-arena start) --
genuinely dead/unused bytes the allocator never touches -- and a scan for
those has not been done yet (the code segment's alignment gaps found so far
total only a few bytes, nowhere near the ~350B all seven caves need).
Whoever picks this up next should start there, or consider a smaller/
different injection strategy (e.g. shrinking which caves are enabled, or
looking for spare bytes inside seg001's static tables) rather than
re-attempting the append approach.

The CD exe has been restored to its clean original state (DNCDPRG.EXE.orig-
backup matches it byte-for-byte, SHA1 c55e9e35c24941d8590c068c69cb6cee
85e4afcb) -- this script is left in place for the derivation work it
already captures (every patch site below was verified correct: bytes
match, relocations compute correctly, the startup crash this uncovered
along the way is genuinely fixed), not because it's ready to use as-is.

DIFFERENCES FROM THE FLOPPY PATCH
----------------------------------
1. Field/function names differ (CD has proper symbol names from the
   disassembly; the floppy script only had raw addresses):
     floppy [0x42e8]/[0x42ea]        -> CD subtitle_pen_x/y      (0x4791/0x4793)
     floppy [0x42ec] (RTL flag)      -> CD subtitle_line_start_x (0x4795)
     floppy [0x42ee] (span marker)   -> CD subtitle_line_start_y (0x4797)
     floppy [0xfc50] (pen)           -> CD font_draw_position_x  (0xd82c)
     floppy 0x9609 (token expansion) -> CD format_interpolated_string (0x8944)
     floppy 0x9618 (main copy loop)  -> CD loc_08953

2. format_interpolated_string has exactly 4 callers in the CD build, and
   EVERY one of them feeds a subsequent draw_subtitle_body call (dialogue
   subtitle or the Hebrew book/History window) -- verified by reading each
   caller's context, not assumed. That's confirmed narrower than the floppy
   build, where name-token substitution was *also* dialogue-only but
   quantity-token substitution additionally fires from a wholly separate,
   LTR, non-draw_subtitle_body caller: troop_prepare_troop_data_for_condit's
   spice-prospector readout ("Average: {} kgs/h" / "Current: {} kgs/h"),
   reached via font_draw_interpolated_string_w_color_at_pos -> a *second*,
   otherwise-unremarkable caller of loc_08a23 (the decimal-digit formatter)
   at loc_09b1c. So exactly like the floppy build: name-token reversal is
   made UNCONDITIONAL (draw_subtitle_body's own pre-pass token expansion
   runs before the RTL flag is ever raised, so a flag-gated check never
   fires -- same finding as floppy, re-confirmed here by inspecting the
   flag's only two write sites, both inside draw_subtitle_body itself), but
   quantity-token reversal STAYS gated on the RTL flag to avoid reversing
   the spice-prospector's LTR digits. See ENABLE_QUANTITY_REVERSAL below --
   same experimental-and-off-by-default posture as the floppy build, for
   the same reason (floppy's still-unresolved savegame corruption when
   quantity reversal is enabled; this port has not yet been proven clear of
   an analogous issue and needs the same live-testing scrutiny before
   trusting it with real saves).

3. The quantity-token EXIT site is only 4 bytes in the CD build (`pop bp` +
   `jmp near <main loop>`) -- one byte short of the 5-byte far call every
   other site uses, and unlike the floppy build there is no adjacent freed
   `ret` byte to consume (the next byte belongs to loc_08a23, the shared
   digit-formatter with its own unrelated caller, so it can't be touched).
   Fixed with a jmp-chain instead of a far call: the site's own `pop bp` is
   left untouched, and its `jmp near` is retargeted (same 3-byte length,
   only the rel16 operand changes) to a small far-jmp trampoline written
   into the seg000 tail padding (7 unused zero bytes at load offset
   0xf4a9-0xf4af, right before seg001 starts -- confirmed empty and
   unreferenced by a whole-segment ndisasm xref scan). That trampoline
   far-jmps into quantity_exit_cave in the main appended blob, which -- for
   the same reason -- needs neither `add sp,4` nor a replayed `pop bp`
   (nothing in this chain is a CALL, so there's no far-call return address
   to balance, unlike name_exit_cave which IS reached by a far call and
   does need the add-sp-4/pop-bp-replay dance).

4. The CD file's last MZ page has less slack than the appended cave blob
   needs (148 bytes free vs. ~160 needed), so growth crosses a 512-byte
   page boundary. The floppy script's e_cblp update refuses that case
   outright (page-boundary crossing was "not implemented" there because it
   never came up); this script implements the general form (bump e_cp by
   however many pages, recompute e_cblp for the new final page) rather
   than reusing the floppy script's restricted version.

Usage:  ./patch_rtl_engine_cd.py [--exe PATH]
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
CD_DIR = REPO_ROOT / "DUNE_CD"
EXE_NAME = "DNCDPRG.EXE"

MZ_HEADER_SIZE = 512
E_CBLP_OFFSET = 0x02
E_CP_OFFSET = 0x04
E_CRLC_OFFSET = 0x06
E_SS_OFFSET = 0x0E       # paragraph offset (from load segment) of seg001 (data+stack)
E_LFARLC_OFFSET = 0x18  # file offset of the relocation table's OWN file offset;
                         # NOT assumed to be the standard 0x1C -- this CD exe's
                         # linker placed it at 0x1E instead. Writing new
                         # relocation entries at a hardcoded 0x1C (as the floppy
                         # script safely does, since ITS exe really does use
                         # 0x1C) corrupted the tail of this file's real 20th
                         # relocation entry and caused an on-load crash; always
                         # read e_lfarlc from the file being patched instead.

# initialize_system (load offset 0xe594) unconditionally zeroes its own
# bump-allocator scratch arena at every startup:
#   mov ax,seg001 / mov es,ax / mov cx,0xDD1D / mov di,_word_2316C_error_msg
#   / sub cx,di / xor ax,ax / rep stosb
# -- i.e. it clears seg001-relative offsets [offset(_word_2316C_error_msg),
# 0xDD1D). _word_2316C_error_msg sits at seg001-relative 0x3CBC (== e_sp,
# confirmed against the header), and 0x3CBC also happens to be exactly
# where the ORIGINAL file's load module naturally ends (seg001 starts at
# e_ss*16 = 0xf4b0, and 0xf4b0+0x3cbc == 0x1316c == the unpatched file's own
# load-module length) -- the linker simply doesn't bother storing bytes for
# a range the program is going to zero itself anyway, and sizes e_minalloc
# (0xa07 paragraphs, confirmed by direct computation) to request exactly
# enough extra memory to cover the arena up to that same 0xDD1D boundary.
# Appending a cave anywhere past the file's natural end -- the floppy
# script's approach -- therefore always lands inside this always-cleared
# arena and gets wiped before any dialogue is ever drawn (confirmed live:
# the cave blob read back as all zeroes at runtime, and every far
# call/jmp into it produced DOSBox's "IBM ROM BASIC NOT IMPLEMENTED" wild-
# jump message). Placing the cave past the arena's startup-clear end
# (e_ss*16 + ARENA_CLEAR_TOP) fixes THAT crash -- confirmed live, the game
# now boots and plays the intro cleanly -- but does NOT make the cave safe
# long-term: bump_allocate_bump_cx_bytes's actual runtime growth is not
# bounded by this constant (it's only where the one-time startup clear
# stops), and by the time the intro's first subtitle line is reached the
# allocator has already grown across the cave's location with real
# resource data, silently overwriting it. See the CURRENT STATUS section
# of the module docstring -- this constant is necessary but not sufficient;
# a real fix needs the cave inside genuinely static, never-allocated-over
# memory instead (the code segment or seg001's own static-data region).
ARENA_CLEAR_TOP = 0xDD1D  # seg001-relative; from initialize_system's own `mov cx,0dd1dh`

# Debugging toggles, same rationale/names as patch_rtl_engine.py: name-token
# reversal is unconditional and safe everywhere it fires (dialogue/book
# text only); quantity-token reversal is flag-gated because it ALSO fires
# from an LTR, non-dialogue readout (see module docstring point 2) and its
# floppy-build counterpart has a still-unresolved savegame-corruption bug
# when enabled -- this port needs the same live-testing scrutiny before
# trusting it.
ENABLE_NAME_REVERSAL = True
ENABLE_QUANTITY_REVERSAL = True

# CD field addresses (DS-relative unless noted), from the labelled
# disassembly. See module docstring for the floppy-equivalent mapping.
SUBTITLE_PEN_X = 0x4791
SUBTITLE_PEN_Y = 0x4793
RTL_FLAG = 0x4795          # subtitle_line_start_x, freed by the pen-seed reorder
SPAN_MARKER = 0x4797       # subtitle_line_start_y, freed by the pen-seed reorder
FONT_DRAW_POSITION_X = 0xD82C
SUBTITLE_PAD_LEFT = 0x4784
SUBTITLE_PAD_TOP = 0x4788
SUBTITLE_PAD_RIGHT = 0x4786
SUBTITLE_TEXT_WIDTH_BUDGET = 0x478F
MAIN_LOOP_RESUME = 0x8953  # format_interpolated_string's main copy loop

# ---------------------------------------------------------------------------
# Cave blob: independent little routines the in-place far calls jump to.
# Hand-assembled with nasm from readable source, then round-tripped through
# ndisasm to confirm each decodes back to the intended instructions before
# being frozen as hex here (see the derivation session for the .asm files).
# ---------------------------------------------------------------------------

PEN_ADVANCE_CAVE = bytes.fromhex(
    "803e954700"  # cmp byte [0x4795],0        (RTL flag)
    "7408"        # jz .ltr
    "29062cd8"    # sub [0xd82c],ax             RTL: pen -= width
    "29c2"        # sub dx,ax                   RTL: blit position -= width
    "eb04"        # jmp short .done
    "01062cd8"    # .ltr: add [0xd82c],ax       LTR original: pen += width
    "88c1"        # .done: mov cl,al            (replayed displaced instruction)
    "cb"          # retf
)

SET_CAVE = bytes.fromhex(
    "8b169147"    # mov dx,[0x4791]             subtitle_pen_x
    "8b1e9347"    # mov bx,[0x4793]             subtitle_pen_y
    "c606954701"  # mov byte [0x4795],1         RTL flag on
    "cb"          # retf
)

CLEAR_CAVE = bytes.fromhex(
    "89169147"    # mov [0x4791],dx
    "891e9347"    # mov [0x4793],bx
    "4e"          # dec si
    "c606954700"  # mov byte [0x4795],0         RTL flag off
    "cb"          # retf
)

ENTRY_MARK_CAVE = bytes.fromhex(
    "83c504"      # add bp,4                    (replayed)
    "89c6"        # mov si,ax                   (replayed)
    "26893e9747"  # mov [es:0x4797],di          NEW: record output-span start
    "cb"          # retf
)

QTY_ENTRY_MARK_CAVE = bytes.fromhex(
    "8b4600"      # mov ax,[bp+0]               (replayed)
    "80fb92"      # cmp bl,0x92                 (replayed)
    "26893e9747"  # mov [es:0x4797],di          NEW: record output-span start
    "cb"          # retf
)

NAME_EXIT_CAVE = bytes.fromhex(
    "8e5e02"                  # mov ds,[bp+2]              (replayed)
    "505357"                  # push ax / push bx / push di
    "268b1e9747"              # mov bx,[es:0x4797]          left = recorded start
    "4f"                      # dec di                      right = end - 1
    "39fb"                    # .loop: cmp bx,di
    "730d"                    # jnc .done
    "268a07"                  # mov al,[es:bx]
    "268605"                  # xchg al,[es:di]
    "268807"                  # mov [es:bx],al
    "43"                      # inc bx
    "4f"                      # dec di
    "ebef"                    # jmp short .loop
    "5f5b58"                  # .done: pop di / pop bx / pop ax
    "83c404"                  # add sp,4                    discard far-call return addr
    "ea53890000"              # jmp 0x0:0x8953              seg relocated to main
)
NAME_EXIT_FARJMP_LOCAL_OFF = len(NAME_EXIT_CAVE) - 2  # seg field of the trailing far jmp

QUANTITY_EXIT_CAVE = bytes.fromhex(
    "26803e954700"            # cmp byte [es:0x4795],0      RTL flag (only gate here)
    "741d"                    # jz .skip
    "505357"                  # push ax / push bx / push di
    "268b1e9747"              # mov bx,[es:0x4797]
    "4f"                      # dec di
    "39fb"                    # .loop2: cmp bx,di
    "730d"                    # jnc .done2
    "268a07"                  # mov al,[es:bx]
    "268605"                  # xchg al,[es:di]
    "268807"                  # mov [es:bx],al
    "43"                      # inc bx
    "4f"                      # dec di
    "ebef"                    # jmp short .loop2
    "5f5b58"                  # .done2: pop di / pop bx / pop ax
    "ea53890000"              # .skip: jmp 0x0:0x8953       seg relocated to main; no add
                               # sp,4/pop-bp needed -- this whole chain is jmp-only
                               # (see module docstring point 3), no far-call return
                               # address was ever pushed.
)
QUANTITY_EXIT_FARJMP_LOCAL_OFF = len(QUANTITY_EXIT_CAVE) - 2

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

CAVE_INTERNAL_FARJMPS = []
if ENABLE_NAME_REVERSAL:
    CAVE_INTERNAL_FARJMPS.append(("name_exit", NAME_EXIT_FARJMP_LOCAL_OFF))
if ENABLE_QUANTITY_REVERSAL:
    CAVE_INTERNAL_FARJMPS.append(("quantity_exit", QUANTITY_EXIT_FARJMP_LOCAL_OFF))


def build_blob():
    blob = bytearray()
    offsets = {}
    for name, code in CAVES:
        offsets[name] = len(blob)
        blob += code
    return bytes(blob), offsets


# ---------------------------------------------------------------------------
# In-place patch sites.
# ---------------------------------------------------------------------------

def far_call(cave_seg_raw, cave_off):
    return bytes([0x9A]) + struct.pack("<H", cave_off) + struct.pack("<H", cave_seg_raw)


def far_jmp(cave_seg_raw, cave_off):
    return bytes([0xEA]) + struct.pack("<H", cave_off) + struct.pack("<H", cave_seg_raw)


class Site:
    def __init__(self, name, load_offset, orig_hex, new_builder, reloc_field_rel=None):
        self.name = name
        self.load_offset = load_offset
        self.file_offset = MZ_HEADER_SIZE + load_offset
        self.orig = bytes.fromhex(orig_hex)
        self.new_builder = new_builder
        self.reloc_field_rel = reloc_field_rel

    def reloc_load_offset(self):
        return None if self.reloc_field_rel is None else self.load_offset + self.reloc_field_rel


# Pen-seed reorder in draw_speech_bubble: seed subtitle_pen_x from the box's
# RIGHT edge instead of the left, and stop writing the (dead)
# subtitle_line_start_x/y fields -- freeing them for reuse as the RTL flag
# and the token-substitution span marker. Structurally identical to the
# floppy build's pen-seed reorder (same instruction sequence, different
# absolute addresses) -- verified via ndisasm against the CD binary itself.
PEN_SEED_OFFSET = 0x8F31
PEN_SEED_ORIG = (
    "8B4600" "AB" "8BD0" "03068447" "A39147" "A39547"
    "8B4602" "AB" "8BD8" "03068847" "A39347" "A39747"
    "8B4604" "03D0" "2B068447" "2B068647" "A38F47"
)
PEN_SEED_NEW = (
    "8B4600" "AB" "8B5604" "03D0" "8BC2" "2B068647" "A39147"
    "8B4602" "AB" "8BD8" "03068847" "A39347"
    "8B4604" "2B068447" "2B068647" "A38F47"
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

    sites.append(Site(
        "pen seed (draw_speech_bubble right edge; frees subtitle_line_start_x/y)",
        PEN_SEED_OFFSET, PEN_SEED_ORIG, lambda: bytes.fromhex(PEN_SEED_NEW),
    ))

    # Pen advance, both font_draw_glyph_func bodies (tall @0xd0b5, small
    # @0xd14e): `add [0xd82c],ax; mov cl,al` (6 bytes) -> far call + 1 nop.
    for label, off in (("tall", 0xD0B5), ("small", 0xD14E)):
        sites.append(Site(
            f"pen advance ({label}) -> far call RTL cave",
            off, "01062cd88ac8",
            (lambda o=pa: far_call(cave_seg_raw, o) + b"\x90"),
            reloc_field_rel=3,
        ))

    # SET hook 0x8b21: `mov dx,[subtitle_pen_x]; mov bx,[subtitle_pen_y]`
    # (8 bytes) -> far call set_cave + 3 nops.
    sites.append(Site(
        "RTL flag set hook (draw_subtitle_body pre-draw)",
        0x8B21, "8b1691478b1e9347",
        (lambda o=setc: far_call(cave_seg_raw, o) + b"\x90\x90\x90"),
        reloc_field_rel=3,
    ))

    # CLEAR hook 0x8c72: `mov [pen_x],dx; mov [pen_y],bx; dec si` (9 bytes)
    # -> far call clear_cave + 4 nops.
    sites.append(Site(
        "RTL flag clear hook (draw_subtitle_body tail)",
        0x8C72, "89169147891e93474e",
        (lambda o=clr: far_call(cave_seg_raw, o) + b"\x90\x90\x90\x90"),
        reloc_field_rel=3,
    ))

    # SPACE advance, flipped in place (push/pop dx bracketing preserved):
    #   0x8c2a `add dx,[0xd82c]` -> 4 NOPs (dx stays = spacing), and
    #   0x8c3a `mov [0xd82c],dx` -> `sub [0xd82c],dx` (pen -= spacing).
    sites.append(Site(
        "space add+pen neutralised (dx stays = spacing)",
        0x8C2A, "03162cd8", lambda: b"\x90\x90\x90\x90",
    ))
    sites.append(Site(
        "space store -> subtract (pen -= spacing, RTL)",
        0x8C3A, "89162cd8", lambda: bytes.fromhex("29162cd8"),
    ))

    if ENABLE_NAME_REVERSAL:
        # Name-token entry, 0x898a: `add bp,4; mov si,ax` (5B) -> far call
        # entry_mark_cave (exact fit, no nop needed).
        sites.append(Site(
            "name-token entry mark (records output-span start)",
            0x898A, "83c5048bf0",
            (lambda o=em: far_call(cave_seg_raw, o)),
            reloc_field_rel=3,
        ))

        # Name-token exit, 0x89bc: `mov ds,[bp+2]; jmp short 0x8953` (5B)
        # -> far call name_exit_cave (exact fit).
        sites.append(Site(
            "name-token exit (unconditional reversal)",
            0x89BC, "8e5e02eb92",
            (lambda o=ne: far_call(cave_seg_raw, o)),
            reloc_field_rel=3,
        ))

    if ENABLE_QUANTITY_REVERSAL:
        # Quantity-token entry, 0x89ec: `mov ax,[bp+0]` (short disp8 form,
        # 3B) + `cmp bl,0x92` (3B) = 7B -> far call qty_entry_mark_cave + 2
        # nops.
        sites.append(Site(
            "quantity-token entry mark (records output-span start)",
            0x89EC, "8b860000" "80fb92",
            (lambda o=qem: far_call(cave_seg_raw, o) + b"\x90\x90"),
            reloc_field_rel=3,
        ))

        # Quantity-token exit, 0x8a1f: `pop bp; jmp near 0x8953` (4B) --
        # one byte short of a far call and no adjacent freed byte to
        # consume (see module docstring point 3). `pop bp` is left
        # untouched; only the jmp's rel16 operand is retargeted (same
        # 3-byte length) to the trampoline in the seg000 tail padding.
        # Same-length operand swap, computed programmatically below (not
        # hand-counted), consistent with the floppy script's low-risk
        # patch categories.
        qty_exit_jmp_site_off = 0x8A20  # the `E9 xx xx` itself, right after `pop bp`
        trampoline_off = 0xF4A9

        def qty_exit_new_bytes(site_off=qty_exit_jmp_site_off, tramp_off=trampoline_off):
            rel16 = (tramp_off - (site_off + 3)) & 0xFFFF
            return b"\x5D" + b"\xE9" + struct.pack("<H", rel16)

        sites.append(Site(
            "quantity-token exit (jmp retargeted to tail-padding trampoline)",
            0x8A1F, "5de930ff",
            qty_exit_new_bytes,
        ))

        # Trampoline: 5 unused zero bytes' worth of the 7-byte seg000 tail
        # padding at 0xf4a9 (right before seg001 starts; confirmed empty
        # and unreferenced by anything else via a whole-segment ndisasm
        # xref scan) become a far jmp into quantity_exit_cave.
        sites.append(Site(
            "quantity-exit trampoline (tail padding -> far jmp into cave)",
            trampoline_off, "0000000000",
            (lambda o=qe: far_jmp(cave_seg_raw, o)),
            reloc_field_rel=3,
        ))

    return sites


# ---------------------------------------------------------------------------
# Apply / detect
# ---------------------------------------------------------------------------

def compute_blob_layout(load_module_len, min_load_offset):
    """Place the cave blob at the first 16-byte-aligned offset that is both
    past the end of the load module AND past min_load_offset (see the
    ARENA_CLEAR_TOP module-docstring note -- appending right after the load
    module, as the floppy script does, lands the blob inside a region the
    game zeroes at every startup in this build, so it must instead go past
    that region's real end). pad_len covers the FULL gap from the current
    file end to the (aligned) blob start, not just the alignment remainder
    -- that gap can be tens of KB when min_load_offset dominates."""
    start = max(load_module_len, min_load_offset)
    blob_load_offset = start + ((-start) % 16)
    pad_len = blob_load_offset - load_module_len
    assert blob_load_offset % 16 == 0
    return pad_len, blob_load_offset, blob_load_offset // 16


def detect_patched(data):
    checks = [(0xD0B5, PEN_ADVANCE_CAVE), (0xD14E, PEN_ADVANCE_CAVE)]
    if ENABLE_NAME_REVERSAL:
        checks.append((0x898A, ENTRY_MARK_CAVE))
    if ENABLE_QUANTITY_REVERSAL:
        checks.append((0x89EC, QTY_ENTRY_MARK_CAVE))
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
        print(f"[patch] {exe_path.name}: already patched (RTL native-dialogue engine, CD build)")
        return False

    blob, blob_offsets = build_blob()
    load_module_len = len(data) - MZ_HEADER_SIZE
    e_ss = struct.unpack_from("<H", data, E_SS_OFFSET)[0]
    arena_end_load_offset = e_ss * 16 + ARENA_CLEAR_TOP
    pad_len, blob_load_offset, cave_seg_raw = compute_blob_layout(load_module_len, arena_end_load_offset)
    blob_file_offset = MZ_HEADER_SIZE + blob_load_offset
    print(f"[patch] scratch-arena end @ load offset 0x{arena_end_load_offset:x} "
          f"(e_ss=0x{e_ss:x}); cave blob placed past it, not past the original "
          f"load-module end (0x{load_module_len:x})")

    sites = build_sites(cave_seg_raw, blob_offsets)

    for s in sites:
        cur = bytes(data[s.file_offset:s.file_offset + len(s.orig)])
        if cur != s.orig:
            new = s.new_builder()
            if cur == new:
                continue
            sys.exit(
                f"{exe_path}: bytes at file offset 0x{s.file_offset:x} ({s.name}) "
                f"are {cur.hex()}, expected original {s.orig.hex()}. Refusing to patch. "
                f"Restore from {exe_path.name}.orig-backup to get a clean base, then re-run."
            )
        if len(s.new_builder()) != len(s.orig):
            sys.exit(f"internal error: site {s.name} length mismatch")

    backup_path = exe_path.with_suffix(exe_path.suffix + ".orig-backup")
    if not backup_path.exists():
        shutil.copy2(exe_path, backup_path)
        print(f"[patch] backed up {exe_path.name} -> {backup_path.name}")

    # 1. Append pad + cave blob. Zero-filled (not 0x90 NOP) since most of
    # this gap is the scratch arena the game itself would have zeroed --
    # nothing ever executes or jumps into the padding itself either way.
    data.extend(b"\x00" * pad_len)
    assert len(data) == blob_file_offset
    data.extend(blob)
    print(f"[patch] appended {pad_len}B pad + {len(blob)}B cave blob at load offset "
          f"0x{blob_load_offset:x} (segment raw 0x{cave_seg_raw:x}); caves at "
          + ", ".join(f"{n}=+0x{o:x}" for n, o in blob_offsets.items()))

    # 2. In-place patches; collect relocation field offsets.
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

    # 3. MZ header: e_cp/e_cblp. General form (not the floppy script's
    # restricted version) since the CD blob's growth crosses a 512-byte
    # page boundary -- see module docstring point 4.
    new_len = len(data)
    old_cp = struct.unpack_from("<H", data, E_CP_OFFSET)[0]
    old_cblp = struct.unpack_from("<H", data, E_CBLP_OFFSET)[0]
    new_cp = (new_len + 511) // 512
    new_cblp = new_len - (new_cp - 1) * 512
    assert 0 < new_cblp <= 512
    assert new_cp * 512 - (512 - new_cblp) == new_len
    struct.pack_into("<H", data, E_CP_OFFSET, new_cp)
    struct.pack_into("<H", data, E_CBLP_OFFSET, new_cblp)
    print(f"[patch] e_cp {old_cp} -> {new_cp}, e_cblp {old_cblp} -> {new_cblp} (file {new_len} bytes)")

    # 4. Relocation table: one new entry per far-call/far-jmp segment field.
    # Table's own file offset comes from e_lfarlc, not a hardcoded constant
    # (see E_LFARLC_OFFSET comment -- this file's linker used 0x1E, not the
    # more common 0x1C).
    e_crlc = struct.unpack_from("<H", data, E_CRLC_OFFSET)[0]
    reloc_table_offset = struct.unpack_from("<H", data, E_LFARLC_OFFSET)[0]
    write_at = reloc_table_offset + e_crlc * 4
    if write_at + len(reloc_offsets) * 4 > MZ_HEADER_SIZE:
        sys.exit(f"{exe_path}: not enough header room for {len(reloc_offsets)} relocations.")
    for r in reloc_offsets:
        hi, lo = divmod(r, 0x10000)
        struct.pack_into("<HH", data, write_at, lo, hi << 12)
        print(f"[patch] +reloc for far-call/jmp seg field at load offset 0x{r:x}")
        write_at += 4
    struct.pack_into("<H", data, E_CRLC_OFFSET, e_crlc + len(reloc_offsets))
    print(f"[patch] e_crlc {e_crlc} -> {e_crlc + len(reloc_offsets)}")

    with open(exe_path, "wb") as f:
        f.write(data)
    return True


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--exe", type=Path, default=CD_DIR / EXE_NAME)
    args = ap.parse_args()
    if not args.exe.exists():
        sys.exit(f"{args.exe} not found")
    apply_patches(args.exe)


if __name__ == "__main__":
    main()
