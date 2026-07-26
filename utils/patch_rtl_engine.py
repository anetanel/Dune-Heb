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

All hooks that move engine code into the cave use only DS-relative or
register operations (never a near call/jmp back into the main code
segment, which the cave -- living in a separate process's memory, see
below -- cannot reach), so no far-return-into-main gymnastics are needed
except the two explicit stack fixups documented at the caves.

Justification: the inline justify pre-adjust at 0x97B6 is left as `add` for
now (JUSTIFY_FLIP below is False). Flipping it is the next tuning knob if
justified lines look off; it is isolated so it can be toggled without
touching anything else.

CAVE PLACEMENT: A SEPARATE TSR, NOT ANYTHING APPENDED TO THIS FILE
--------------------------------------------------------------------
Two earlier designs both appended the cave to DUNEPRG.EXE's own load
module and got it overwritten at runtime, live-confirmed via dosbox-mcp
both times:

1. Appended immediately after the load module's own end, reached via the
   standard MZ relocation table. This is what caused ENABLE_QUANTITY_
   REVERSAL's savegame corruption (dune_rtl_quantity_savegame_corruption
   memory: compress_sav truncating saves whenever the quantity caves were
   merely *present*, not executing -- "5th caller" never found) and an
   in-game hang once a quantity-token dialogue line loaded new audio/
   lip-sync resources.
2. Appended much further out -- past e_minalloc's boundary (the MZ header
   field declaring how much *guaranteed* extra memory DOS grants the
   process beyond the load module) plus a generous fixed safety margin,
   on the theory that the engine's runtime resource loader (sprites,
   audio, dialogue text -- a simple bump allocator that does not consult
   DOS's own allocator or care what DOS currently considers "free") was
   bounded by e_minalloc the same way it appeared to be on the CD build
   (DUNE_CD/DNCDPRG.EXE, see patch_rtl_engine_cd.py). Wrong: got
   overwritten too, within seconds of skipping the intro, at a distance
   the e_minalloc theory does not explain -- the bump allocator's real
   ceiling is apparently governed by however much memory this file's own
   e_maxalloc=0xFFFF ("give me everything you can") actually secures at
   load time, not by e_minalloc's smaller guaranteed floor. There is no
   position within DUNEPRG.EXE's own DOS-granted memory block that a
   bigger margin makes safe -- the bump allocator can eventually reach
   anywhere in it.

The fix used here sidesteps the whole bug class instead of picking a
bigger number: the cave lives in a small separate TSR (terminate-and-
stay-resident program, see rtl_cave_tsr.py), loaded and made resident
BEFORE DUNEPRG.EXE starts (see build_translation.py's DUNE.BAT handling).
This is safe for a structural reason, not a margin guess -- DOS's real
memory allocator (unlike the game's own internal bump allocator) DOES
respect ownership: once the TSR is resident, the game's own greedy
e_maxalloc allocation gets "whatever's left," and the game's bump
allocator, which only ever computes addresses relative to its own segment
registers, has no way to reach across into a separately-owned process's
memory.

Mechanically: DUNEPRG.EXE's own entry point (e_cs:e_ip) is redirected to a
tiny init stub appended right after the load module (same simple, no-
margin placement as design #1 above -- safe here because the stub is
transient, running once before the bump allocator has done anything, and
holds no cave code of its own to be overwritten later). The stub queries
the TSR over INT 60h, pokes the returned segment into each far-call site's
segment operand and into the cave's two internal far-jump-back-to-main
segment fields (which live in the TSR's memory but need the GAME's load
segment, computed by the stub the same way -- `mov ax,cs; sub ax,
<this stub's own known raw segment>`), then far-jumps back to the
untouched original entry point. None of this needs any MZ relocation-table
entries: the stub's own address comes from e_cs/e_ip (which DOS relocates
automatically, no table entry required), and every segment value the far-
call sites and cave need is poked in at runtime by the stub instead of
fixed up by DOS's loader at load time. See rtl_cave_tsr.py for the TSR
side of this protocol.

Whoever next adds a cave here (more RTL sites, a different subsystem,
whatever) does not need to re-derive any of this: add it to CAVES/
build_blob() as usual (unchanged from before) -- it becomes part of the
TSR's own payload automatically, with no placement risk to reconsider,
since the TSR mechanism does not degrade with size the way the appended-
blob designs did.

Usage:  ./patch_rtl_engine.py [--exe PATH]
Also writes <exe's directory>/DUNETSR.COM (the TSR -- see rtl_cave_tsr.py)
and ensures DUNE.BAT loads it first (see build_translation.py). Idempotent;
refuses on unrecognised bytes; backs up once to <exe>.orig-backup before
the first write.
"""

import argparse
import shutil
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rtl_cave_tsr  # noqa: E402  (sibling import, see CLAUDE.md)

UTILS_DIR = Path(__file__).resolve().parent
REPO_ROOT = UTILS_DIR.parent
GAME_DIR = REPO_ROOT / "game"
EXE_NAME = "DUNEPRG.EXE"
TSR_NAME = "DUNETSR.COM"

MZ_HEADER_SIZE = 512
E_CBLP_OFFSET = 0x02
E_CP_OFFSET = 0x04
E_IP_OFFSET = 0x14
E_CS_OFFSET = 0x16

JUSTIFY_FLIP = False  # flip the 0x97B6 justify pre-adjust add->sub (tuning)

# Debugging toggles for the token-substitution reversal (sietch name /
# occupation / digits): a full application of both caused a hang in-game
# that isolated static analysis + an algorithm-level Python simulation
# (see dune_rtl_engine_patch_moonshot memory) couldn't reproduce or
# explain, so these let each half be tested independently to narrow down
# which one is actually at fault before re-enabling both. Quantity
# reversal specifically also needed the TSR-based cave placement fix (see
# the module docstring's "CAVE PLACEMENT" section) before it was safe to
# enable at all -- the two earlier, appended-cave designs both corrupted
# saves or hung mid-dialogue once this flag was on.
ENABLE_NAME_REVERSAL = True
ENABLE_QUANTITY_REVERSAL = True

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
# EVERY access to [0x42EC]/[0x42EE] in all four caves below is ES-relative
# (an explicit 0x26 segment-override prefix), not DS-relative. This was
# found live (dosbox-mcp breakpoint trace through an actual multi-name
# dialogue line, entry_mark/name_exit hit repeatedly for several tokens in
# sequence): DS is only reliably the game data segment at the *outermost*
# call into this token-expansion function. Partway through a line with
# several substitutions back-to-back, DS was observed to read as 0 at a
# later entry_mark hit -- the original code's own DS save/restore
# (`mov ds,[bp+2]` at each exit) doesn't hold up the same way across
# several sequential calls into 0x9609 within one line, for reasons not
# further chased down (didn't need to be: ES was confirmed constant at
# the game data segment across every single hit, entry and exit, for the
# whole traced dialogue, so it's the reliable choice regardless of *why*
# DS drifts). Without the override, a plain `mov [0x42ee],di` at a
# DS=0 moment writes DI straight into low memory at absolute address
# 0x42EE -- confirmed by reading that physical address before and after
# the instruction executed and watching it change to the live DI value --
# corrupting whatever unrelated word normally lives there and producing
# exactly the delayed corrupt-now-crash-later BIOS int-6 hang this whole
# investigation chased. This fix was live-verified the same way (the
# corrupting write no longer happens because the write now targets
# ES:0x42EE, which stays the correct data segment throughout).
#
# All four re-assembled by hand from the nasm-verified originals plus the
# ES-override insertions (each insertion is a single 0x26 prefix byte
# before an existing instruction, so every original instruction's own
# encoding is untouched -- only two things shift as a result: the jz
# .skip branch's rel8 in the two exit caves grows by 1, 0x1C->0x1D,
# since exactly one of the two insertions falls between the jz and its
# own target while both fall before .skip; the jae .done branch is
# unaffected, both insertions precede it and its target uniformly, net
# offset unchanged at 0x0D. Offsets re-derived and cross-checked in
# Python against the original, known-correct FARJMP_LOCAL_OFF constants
# before writing -- not hand-counted.) From:
#
#   entry_mark_cave:                      ; replaces 964D-9651 (5B)
#       add     bp, 4                     ; replayed
#       mov     si, ax                    ; replayed
#       mov     word [es:0x42ee], di      ; NEW: record output-span start
#       retf
#
#   qty_entry_mark_cave:                  ; replaces 969C-96A2 (7B)
#       mov     ax, [bp+0]                ; replayed (original uses the
#                                         ; longer disp16 encoding; this
#                                         ; replay's shorter disp8 encoding
#                                         ; is behaviourally identical)
#       cmp     bl, 0x92                  ; replayed
#       mov     word [es:0x42ee], di      ; NEW: record output-span start
#       retf
#
#   name_exit_cave:                       ; replaces 967C-9680 (5B)
#       mov     ds, [bp+2]                ; replayed
#       push ax / push bx / push di
#       mov     bx, [es:0x42ee]           ; left = recorded start
#       dec     di                        ; right = current end - 1
#   .loop: cmp bx,di / jae .done
#       mov al,[es:bx] / xchg al,[es:di] / mov [es:bx],al
#       inc bx / dec di / jmp short .loop
#   .done: pop di / pop bx / pop ax
#       add     sp, 4                     ; discard far-call return addr
#       jmp     0x0:0x9618                ; far jmp, seg relocated to main
#
#   UNCONDITIONAL as of this fix -- no [0x42EC] check. Found live (many
#   dosbox-mcp breakpoint traces, several sessions): [0x42EC] reads 0 at
#   *every* entry_mark/name_exit hit, always, because 0x9609's token
#   expansion is a pre-pass that runs before draw_subtitle_body's
#   SET_CAVE ever raises the flag for the actual per-line draw -- so the
#   conditional reversal this cave was built around never once fired.
#   The visible symptom (sietch name still reversed on screen, confirmed
#   directly by the user even after the crash/hang fixes above landed)
#   was never fixed by the flag-gated design at all; only the crash
#   mechanics were. Made unconditional instead, on the reasoning (not
#   fully proven, but consistent with every other finding this session)
#   that entry_mark/name_exit specifically is reached ONLY from
#   draw_subtitle_body's own phrase-token parsing -- i.e. only ever for
#   RTL dialogue content -- unlike qty_entry_mark/quantity_exit, which
#   dune_rtl_engine_patch_moonshot's occupation/duration section already
#   established is ALSO reached from COMMAND1's own LTR troop-status
#   menu (via the same COMMAND1-table substitution mechanism), so making
#   quantity reversal unconditional would break that screen. Quantity
#   reversal was originally left flag-gated (and disabled by default)
#   rather than guessing at a fix for it here.
#
#   That gate turned out to be exactly the same dead check as
#   entry_mark/name_exit's, for the identical reason: quantity_exit is
#   reached from the same 0x9609 pre-pass, before SET_CAVE ever raises
#   [0x42EC] -- confirmed live once the crash/hang bugs above were fixed
#   and the Fremen-leader quantity dialogue could finally be watched
#   end-to-end without dying first: no crash, no corruption, but the
#   digits were still rendered in raw forward order (e.g. "1900" shown as
#   "0091"), i.e. the `jz` always took the skip branch, exactly as with
#   name_exit before its own fix. Made unconditional here too, matching
#   name_exit -- the COMMAND1-LTR-menu regression risk noted above is a
#   real possibility that hasn't been ruled out, so re-check that screen
#   (troop-status list) specifically after this change, not just dialogue.
#
#   quantity_exit_cave: reached via a jmp-chain, not a far call -- see the
#   "QUANTITY EXIT: JMP-CHAIN, NOT BYTE CONSUMPTION" note below for why
#   this no longer replays `pop bp` or consumes the neighbouring byte at
#   96E2 (both were true in an earlier version of this cave).
ENTRY_MARK_CAVE = bytes.fromhex("83c50489c626893eee42cb")

QTY_ENTRY_MARK_CAVE = bytes.fromhex("8b460080fb9226893eee42cb")

NAME_EXIT_CAVE = bytes.fromhex(
    "8e5e02505357268b1eee424f39fb730d"
    "268a07268605268807434febef5f5b5883c404ea18960000"
)
NAME_EXIT_FARJMP_LOCAL_OFF = 0x26  # offset within NAME_EXIT_CAVE of the far-jmp's seg field

# QUANTITY EXIT: JMP-CHAIN, NOT BYTE CONSUMPTION
# -------------------------------------------------
# An earlier version of this cave replayed `pop bp` and consumed the
# neighbouring byte at load offset 0x96E2 (a `ret` shared, unconditionally,
# by a second, unrelated function -- see the old "unrelated jmp retarget"
# site this replaced) to fit a direct 5-byte far call into the site's own
# 4 available bytes at 0x96DE. That consumption is what caused
# ENABLE_QUANTITY_REVERSAL's savegame corruption: confirmed live
# (dosbox-mcp) by removing just that one retarget site and nothing else --
# the corruption disappeared completely. The exact mechanism was not
# pinned down further (something about redirecting that second function's
# jump target apparently leaves the stack or a register in a state that
# corrupts a much later, unrelated operation -- decompress_sav's own
# in-buffer RLE expansion overran into the stack, confirmed by reading a
# a long run of a single repeated byte value where return addresses should
# have been), but the fix does not require finding it: don't consume that
# byte at all.
#
# Same technique already used for the CD build's own equivalent site (see
# patch_rtl_engine_cd.py) for an unrelated reason (that site was only 4
# bytes with no spare byte to consume at all): retarget the site's own
# `jmp near 0x9618` (3 bytes, load offset 0x96DE+1) to a tiny far-jmp
# trampoline living in unused tail padding (8 zero bytes at load offset
# 0xECE8, confirmed empty and unreferenced by a whole-file xref scan --
# see build_stub()/apply_patches()), which jumps into this cave; the cave,
# after its conditional reversal, far-jumps back to 0x9618 directly. `pop
# bp` and the `ret` at 0x96E2 are both left completely untouched in the
# main exe, so the jc-retarget (0x96ED) and the old "unrelated jmp
# retarget" (0xB3DD) sites are no longer needed at all -- removed.
#
# A second bug survived that rewrite: the cave's tail still had `add sp,
# byte +0x4` right before the final far jmp, left over from when entry was
# a far CALL (which pushes a 4-byte CS:IP return address that this
# instruction discarded, since the cave jumps back to 0x9618 manually
# instead of using retf). The jmp-chain entry above pushes nothing, so
# that add sp was silently eating 4 bytes of the *caller's* stack frame on
# every quantity-reversal trigger -- confirmed live (dosbox-mcp) as the
# cause of a hang (IF cleared, CPU pinned at a fixed CS:IP across repeated
# breaks) once the earlier corruption bug above was fixed. Removed; no
# other byte needed changing since it sat exactly at the preceding `jz`'s
# target offset, so the far jmp that used to follow it simply slides into
# that same offset once it's gone.
#
# A third bug surfaced once the above two were fixed and the Fremen-leader
# quantity dialogue could finally be watched rendering end-to-end: troop
# counts came out digit-scrambled (e.g. "1900" as "0190"), not simply
# unreversed. Root cause turned out to be in the *source data*, not this
# cave, and didn't need an engine-side fix at all -- see
# [[dune_quantity_suffix_literal_position]] (translations/PHRASE12.HEB):
# the English original spells troop-count lines as `\x91<0 men.`, where
# the literal "0" after the `\x91<` token is a genuine, deliberate part of
# the phrase (the engine stores/computes the value one digit short and the
# phrase supplies the missing trailing zero as static text). This cave
# only ever reverses the digits *between* entry_mark and its own exit, so
# any literal adjacent to the token in the source stays outside that span
# either way -- what determines whether it lands on the correct side once
# the whole line gets RTL-flipped is purely which side of the token it's
# typed on. Moving it in the .HEB source (before the token instead of
# after) was the fix; no cave change needed.
QUANTITY_EXIT_CAVE = bytes.fromhex(
    "505357268b1eee424f39fb730d"
    "268a07268605268807434febef5f5b58ea18960000"
)
QUANTITY_EXIT_FARJMP_LOCAL_OFF = len(QUANTITY_EXIT_CAVE) - 2  # offset of the trailing far-jmp's seg field

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
# where builder() -> new_bytes (same length as orig). poke_field_rel, if not
# None, is the byte offset within the site of a far-call segment field that
# the init stub (see build_stub()) pokes at runtime with the TSR's segment
# -- these sites carry NO MZ relocation-table entry (see the module
# docstring's "CAVE PLACEMENT" section for why: the cave now lives in a
# separately-loaded TSR, not anywhere DOS's loader can fix up for us).
# ---------------------------------------------------------------------------

def far_call(cave_off):
    """9A <off16> <seg16=0 placeholder> ; the init stub pokes the real
    segment (the TSR's) in at runtime -- see build_stub()."""
    return bytes([0x9A]) + struct.pack("<H", cave_off) + b"\x00\x00"


def far_jmp(cave_off):
    """EA <off16> <seg16=0 placeholder> ; same runtime-poke convention as
    far_call(), used by the quantity-exit trampoline (see build_sites())."""
    return bytes([0xEA]) + struct.pack("<H", cave_off) + b"\x00\x00"


class Site:
    def __init__(self, name, load_offset, orig_hex, new_builder, poke_field_rel=None):
        self.name = name
        self.load_offset = load_offset
        self.file_offset = MZ_HEADER_SIZE + load_offset
        self.orig = bytes.fromhex(orig_hex)
        self.new_builder = new_builder
        # poke_field_rel: byte offset within this site of a far-call seg
        # field the init stub must poke at runtime (None if none).
        self.poke_field_rel = poke_field_rel

    def poke_load_offset(self):
        return None if self.poke_field_rel is None else self.load_offset + self.poke_field_rel


# 8 unused zero bytes in the code segment's own tail padding, right before
# the data segment starts (load offset DS_RAW*16 == 0xECF0) -- confirmed
# empty and unreferenced by a whole-file jmp/call xref scan. Used as the
# quantity-exit trampoline's home (see build_sites()); only 5 of the 8
# bytes are needed.
QTY_EXIT_TRAMPOLINE_OFFSET = 0xECE8

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


def build_sites(tsr_offsets):
    pa = tsr_offsets["pen_advance"]
    setc = tsr_offsets["set"]
    clr = tsr_offsets["clear"]
    em = tsr_offsets.get("entry_mark")
    qem = tsr_offsets.get("qty_entry_mark")
    ne = tsr_offsets.get("name_exit")
    qe = tsr_offsets.get("quantity_exit")

    sites = []

    # Pen-seed reorder (no cave / no runtime poke).
    sites.append(Site(
        "pen seed (draw_speech_bubble right edge; frees [0x42EC])",
        PEN_SEED_OFFSET, PEN_SEED_ORIG, lambda: bytes.fromhex(PEN_SEED_NEW),
    ))

    # Pen advance, tall + small font bodies: `add [0xfc50],ax; mov cl,al`
    # (6 bytes) -> far call pen_advance_cave (in the TSR) + nop.
    for label, off in (("tall", 0xCB18), ("small", 0xCBB2)):
        sites.append(Site(
            f"pen advance ({label}) -> far call RTL cave",
            off, "010650FC" "8AC8",
            (lambda o=pa: far_call(o) + b"\x90"),
            poke_field_rel=3,
        ))

    # SET hook 0x9711: `mov dx,[0x42e8]; mov bx,[0x42ea]` (8 bytes)
    # -> far call set_cave + 3 nops. (set_cave replays both movs.)
    sites.append(Site(
        "RTL flag set hook (draw_subtitle_body pre-draw)",
        0x9711, "8B16E842" "8B1EEA42",
        (lambda o=setc: far_call(o) + b"\x90\x90\x90"),
        poke_field_rel=3,
    ))

    # CLEAR hook 0x986B: `mov [0x42e8],dx; mov [0x42ea],bx; dec si` (9 bytes,
    # NOT the trailing `ret` at 0x9874) -> far call clear_cave + 4 nops.
    # clear_cave replays the tail, clears the flag, and `retf`s back to
    # 0x9870; the NOPs fall through to the untouched `ret` at 0x9874, which
    # does the caller's near return in the correct (main) code segment.
    sites.append(Site(
        "RTL flag clear hook (draw_subtitle_body tail; keeps 0x9874 ret)",
        0x986B, "8916E842" "891EEA42" "4E",
        (lambda o=clr: far_call(o) + b"\x90\x90\x90\x90"),
        poke_field_rel=3,
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
            (lambda o=em: far_call(o)),
            poke_field_rel=3,
        ))

        # Name/m@@ token exit, 0x967C: `mov ds,[bp+2]; jmp short 0x9618`
        # (5B) -> far call name_exit_cave (replays the DS restore,
        # conditionally reverses the output span, far-jmps back to 0x9618).
        sites.append(Site(
            "name-token exit (conditional reversal)",
            0x967C, "8E5E02" "EB97",
            (lambda o=ne: far_call(o)),
            poke_field_rel=3,
        ))

    if ENABLE_QUANTITY_REVERSAL:
        # Quantity token entry, 0x969C: `mov ax,[bp+0]` (long disp16
        # encoding, 4B) + `cmp bl,0x92` (3B) = 7B -> far call
        # qty_entry_mark_cave + 2 nops (replays both, marks di as the
        # output-span start).
        sites.append(Site(
            "quantity-token entry mark (records output-span start)",
            0x969C, "8B860000" "80FB92",
            (lambda o=qem: far_call(o) + b"\x90\x90"),
            poke_field_rel=3,
        ))

        # Quantity token exit, 0x96DE: `pop bp; jmp near 0x9618` (4B). See
        # the "QUANTITY EXIT: JMP-CHAIN, NOT BYTE CONSUMPTION" note at
        # QUANTITY_EXIT_CAVE for why this no longer consumes the
        # neighbouring byte at 0x96E2 (a change that fixed the
        # ENABLE_QUANTITY_REVERSAL savegame corruption, confirmed live) --
        # `pop bp` is left completely untouched, and only the `jmp near`
        # operand is retargeted (same 3-byte length) to a tiny far-jmp
        # trampoline in unused tail padding, which jumps into
        # quantity_exit_cave.
        sites.append(Site(
            "quantity-token exit (jmp retargeted to tail-padding trampoline)",
            0x96DE, "5D" "E936FF",
            (lambda: b"\x5D\xE9" + struct.pack(
                "<H", (QTY_EXIT_TRAMPOLINE_OFFSET - (0x96DE + 1 + 3)) & 0xFFFF)),
        ))

        # Trampoline: 5 of the 8 unused zero bytes in the code segment's
        # tail padding, right before the data segment starts (confirmed
        # empty and unreferenced by a whole-file xref scan) become a far
        # jmp into quantity_exit_cave (segment runtime-poked, same
        # convention as every other TSR-pointing far call/jmp here).
        sites.append(Site(
            "quantity-exit trampoline (tail padding -> far jmp into cave)",
            QTY_EXIT_TRAMPOLINE_OFFSET, "0000000000",
            (lambda o=qe: far_jmp(o)),
            poke_field_rel=3,
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

# ---------------------------------------------------------------------------
# Init stub: appended right after the load module (small, transient -- see
# the module docstring's "CAVE PLACEMENT" section for why this placement is
# safe for the stub even though it wasn't safe for the cave itself). Hand-
# assembled with nasm from readable source, then round-tripped through
# ndisasm to confirm it decodes back to the intended instructions -- same
# process used for every cave/cave-adjacent blob in this file.
#
#   HEAD:
#       mov ax, 0x4455        ; "are you the Dune RTL cave TSR?"
#       int 0x60
#       mov ax, cs
#       sub ax, <CAVE_SEG_RAW>            ; placeholder @8 (2B) -> ax = load_segment
#       push es
#       mov es, ax                         ; es = load segment, ready for site pokes
#   (site pokes go here: one `mov word [es:<site_load_offset>], bx` per
#    far-call site, 5 bytes each incl. the 0x26 ES-override prefix)
#   MID:
#       mov es, bx                         ; es = TSR (cave) segment
#   (farjmp pokes go here: one `mov word [es:<local_off>], ax` per cave-
#    internal far-jump-back-to-main site, 4 bytes each -- AX-only short form)
#   TAIL:
#       pop es
#       push ax
#       push word 0
#       retf                                ; -> untouched original entry point
_STUB_HEAD = bytes.fromhex("b85544cd608cc82d111106 8ec0".replace(" ", ""))
_STUB_HEAD_CAVE_SEG_RAW_OFF = 8
_STUB_MID = bytes.fromhex("8ec3")
_STUB_TAIL = bytes.fromhex("07506a00cb")


def _site_poke(load_offset, reg_is_bx=True):
    """ES:[load_offset] <- bx (5B, `mov [es:off],bx`) or <- ax (4B, the
    AX-only short encoding, `mov [es:off],ax`)."""
    if reg_is_bx:
        return bytes([0x26, 0x89, 0x1E]) + struct.pack("<H", load_offset & 0xFFFF)
    return bytes([0x26, 0xA3]) + struct.pack("<H", load_offset & 0xFFFF)


def build_stub(cave_seg_raw, site_load_offsets, farjmp_local_offsets):
    head = bytearray(_STUB_HEAD)
    struct.pack_into("<H", head, _STUB_HEAD_CAVE_SEG_RAW_OFF, cave_seg_raw)
    stub = bytes(head)
    for off in site_load_offsets:
        stub += _site_poke(off, reg_is_bx=True)
    stub += _STUB_MID
    for off in farjmp_local_offsets:
        stub += _site_poke(off, reg_is_bx=False)
    stub += _STUB_TAIL
    return stub


def compute_stub_layout(load_module_len):
    """Place the (small, transient) init stub at the first 16-byte-aligned
    offset past the load module's own end -- see the module docstring for
    why this simple placement, unsafe for the cave itself, is fine for the
    stub."""
    pad_len = (-load_module_len) % 16
    stub_load_offset = load_module_len + pad_len
    assert stub_load_offset % 16 == 0
    return pad_len, stub_load_offset, stub_load_offset // 16


def detect_patched(data):
    """Return True if e_cs:e_ip already points at a stub whose fixed
    (non-placeholder) bytes match _STUB_HEAD -- i.e. this script's own
    entry-point redirect is already installed."""
    e_ip, e_cs = struct.unpack_from("<HH", data, E_IP_OFFSET)
    if e_cs == 0 and e_ip == 0:
        return False
    stub_file_off = MZ_HEADER_SIZE + e_cs * 16 + e_ip
    candidate = bytes(data[stub_file_off:stub_file_off + len(_STUB_HEAD)])
    if len(candidate) != len(_STUB_HEAD):
        return False
    off = _STUB_HEAD_CAVE_SEG_RAW_OFF
    return (candidate[:off] == _STUB_HEAD[:off]
            and candidate[off + 2:] == _STUB_HEAD[off + 2:])


def apply_patches(exe_path):
    with open(exe_path, "rb") as f:
        data = bytearray(f.read())

    if detect_patched(data):
        print(f"[patch] {exe_path.name}: already patched (RTL native-dialogue engine, TSR-based cave)")
        return False

    # 1. Build the TSR (holds the cave -- see rtl_cave_tsr.py) and write it
    # alongside the EXE. tsr_offsets gives each cave routine's absolute
    # .COM-offset within it -- what the far-call sites' off16 operand needs.
    blob, blob_offsets = build_blob()
    tsr_com, tsr_offsets = rtl_cave_tsr.build_tsr_com(blob, blob_offsets)
    tsr_path = exe_path.parent / TSR_NAME
    tsr_path.write_bytes(tsr_com)
    print(f"[patch] wrote {tsr_path.name} ({len(tsr_com)}B); cave offsets within it: "
          + ", ".join(f"{n}=0x{o:x}" for n, o in tsr_offsets.items()))

    sites = build_sites(tsr_offsets)

    # Verify every site is at its known-original bytes BEFORE writing
    # anything (same refuse-on-mismatch posture as every other patch here).
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

    # 2. In-place site patches (far-call sites now carry a 0x0000 segment
    # placeholder the init stub overwrites at runtime; non-far-call sites
    # -- pen seed, space add/store, jc/jmp retargets -- are plain same-
    # length in-place edits, unchanged from every earlier version).
    for s in sites:
        new = s.new_builder()
        data[s.file_offset:s.file_offset + len(new)] = new
        print(f"[patch] {s.name} @ 0x{s.file_offset:x}"
              + (" (runtime-poked segment)" if s.poke_load_offset() is not None else ""))

    # 3. Build + append the init stub right after the (now in-place-
    # patched) load module.
    load_module_len = len(data) - MZ_HEADER_SIZE
    pad_len, stub_load_offset, cave_seg_raw = compute_stub_layout(load_module_len)
    site_load_offsets = [s.poke_load_offset() for s in sites if s.poke_load_offset() is not None]
    farjmp_local_offsets = []
    if ENABLE_NAME_REVERSAL:
        farjmp_local_offsets.append(tsr_offsets["name_exit"] + NAME_EXIT_FARJMP_LOCAL_OFF)
    if ENABLE_QUANTITY_REVERSAL:
        farjmp_local_offsets.append(tsr_offsets["quantity_exit"] + QUANTITY_EXIT_FARJMP_LOCAL_OFF)
    stub = build_stub(cave_seg_raw, site_load_offsets, farjmp_local_offsets)

    data.extend(b"\x00" * pad_len)
    assert len(data) == MZ_HEADER_SIZE + stub_load_offset
    data.extend(stub)
    print(f"[patch] appended {pad_len}B pad + {len(stub)}B init stub at load offset "
          f"0x{stub_load_offset:x} (segment raw 0x{cave_seg_raw:x}); "
          f"{len(site_load_offsets)} site pokes, {len(farjmp_local_offsets)} internal-farjmp pokes -- "
          "all runtime, no MZ relocation entries needed")

    # 4. MZ header: e_cp/e_cblp (the stub is small, but be generally
    # correct rather than assume it never crosses a page boundary).
    new_len = len(data)
    old_cblp = struct.unpack_from("<H", data, E_CBLP_OFFSET)[0]
    old_cp = struct.unpack_from("<H", data, E_CP_OFFSET)[0]
    new_cp = (new_len + 511) // 512
    new_cblp = new_len - (new_cp - 1) * 512
    assert 0 < new_cblp <= 512
    assert new_cp * 512 - (512 - new_cblp) == new_len
    struct.pack_into("<H", data, E_CP_OFFSET, new_cp)
    struct.pack_into("<H", data, E_CBLP_OFFSET, new_cblp)
    print(f"[patch] e_cp {old_cp} -> {new_cp}, e_cblp {old_cblp} -> {new_cblp} (file {new_len} bytes)")

    # 5. MZ header: e_cs/e_ip -> redirect entry point to the init stub.
    # Original entry (0000:0000, i.e. load_segment:0000) is left completely
    # untouched in the file; the stub far-jumps back to it once it's done.
    old_ip, old_cs = struct.unpack_from("<HH", data, E_IP_OFFSET)
    struct.pack_into("<HH", data, E_IP_OFFSET, 0, cave_seg_raw)
    print(f"[patch] entry point e_cs:e_ip {old_cs:04x}:{old_ip:04x} -> {cave_seg_raw:04x}:0000 (init stub)")

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
