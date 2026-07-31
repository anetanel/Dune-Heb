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
BEFORE DUNEPRG.EXE starts (see build_translation.py's launcher-.BAT
handling -- DUNE.BAT).
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
and ensures DUNE.BAT loads it first (see build_translation.py).
Idempotent; refuses on unrecognised bytes; backs up once to
<exe>.orig-backup before the first write.
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

# Ornithopter destination-map screen's default prompt ("select a
# destination on the map" -- COMMAND1 0-based line 76, drawn via a
# dedicated frame-task callback at load offset 0x4ece, NOT draw_subtitle_
# body): the game reveals this banner one glyph per tick via
# font_draw_glyph_func_small, starting from a hardcoded LEFT-edge pen X
# (0x55) and always ADD-ing the glyph width -- entirely independent of the
# [0x42EC] RTL flag above (that flag is only ever set/cleared by draw_
# subtitle_body's own hooks, so this callback always drew LTR). Confirmed
# structurally identical to the CD build's loc_04658/frame_task_callback_
# 046b5 (see https://thomas.fach-pedersen.net/dune/cryo-dune-3.7-cd-dncdprg.html),
# and confirmed live (dosbox-mcp) that DS is the game data segment at this
# callback's own SET/CLEAR bracket points, same as draw_subtitle_body's.
ENABLE_MAP_BANNER_REVERSAL = True

# Sietch-info panel's water-quantity row (map screen, click a sietch with a
# wind-trap): the panel calls location_0605c (floppy load offset 0x6DFC,
# confirmed against a pre-existing Ghidra project for this exact EXE, whose
# function names -- draw_location_name, location_0605c, etc. -- already
# matched madmoose/dune-chani's CD-build naming from earlier sessions in
# this repo), which unconditionally draws the "water:" label
# (font_draw_phrase_or_command_string_with_color_at_pos, "cbf8") THEN draws
# the raw 3-digit quantity (a separate routine, "dda1", with no position
# argument of its own -- it just continues from wherever the label call
# left the shared pen position). Both draws run through the ordinary LTR
# glyph path (pen += glyph width every time, unconditionally) -- this
# call site is never routed through the [0x42EC]-gated RTL pen mechanism
# the rest of this file installs for dialogue/map-banner text, so it was
# never a candidate for the flip-the-flag fix used elsewhere.
#
# This is NOT a translation-content bug (see feedback_prefer_content_fix_
# over_engine_patch): the label's own reversed content ("מים:" -> stored
# ":מים", colon-then-word) already renders correctly R-to-L on its own:
# the actual defect is that the quantity is a SEPARATE draw call that
# always lands to the right of wherever the label finished, regardless of
# the label's own internal byte order -- no content change can move a
# call that always continues rightward from the END of a preceding draw
# to instead land to its LEFT. Confirmed by simulating every reordering
# of the label's own bytes: the number's absolute start position is always
# label_base_x + label_total_width, independent of internal arrangement.
#
# Fix: reorder the two draws (digits first, then label continuing after
# them) so the natural RTL reading "מים: 149" (word, colon, number) comes
# out right, instead of the shipped "water:"+"149" glued together with the
# number stuck mid-word. Digits can't simply swap position with the label
# in-place (30 extra bytes needed, no slack in location_0605c's fixed
# 80-byte slot) so the whole routine is reimplemented in a cave instead,
# reached by converting its one call site (in location_0605c's caller, at
# load offset 0x6DF1: `push ax / call location_0605c / pop ax`, 5 bytes)
# into a far call -- the exact same technique used throughout this file,
# just with more internal far-calls back into main (font_select_small_font
# @ 0xCAD8, font_set_draw_position @ 0xCAB1 -- needed here because "dda1"
# has no position argument of its own, unlike the label's all-in-one draw
# call -- the digit routine @ 0xDDA1, font_draw_phrase_or_command_string_
# with_color_at_pos @ 0xCBF8 [used three times: has-water label, no-water
# label, and the "No wind-trap" line], and the Equipment-header draw @
# 0x6F1A). Hand-assembled and round-tripped through nasm/ndisasm exactly
# like every other cave in this file, then cross-checked in Python (this
# comment's numbers match WATER_ROW_CAVE_FARCALL_OFFSETS, not hand-counted
# against the disassembly -- see the builder script this was derived from
# for the offset arithmetic).
ENABLE_WATER_ROW_REVERSAL = True

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

# --- Map-destination-banner reveal caves ---
#
# Same SET/CLEAR bracket technique as SET_CAVE/CLEAR_CAVE above, applied
# to the frame-task callback at load offset 0x4ece instead of draw_
# subtitle_body. The callback's own "call font_draw_glyph_func_small"
# (load offset 0x4f01) is left completely untouched -- bracketed instead
# by two adjacent all-non-call instruction runs that already save/restore
# the pen position around it, so no CALL instruction ever needs replaying
# inside a cave living in a different segment (the TSR). The font_set_
# draw_position/font_select_small_font/font_get_draw_position calls in
# between (load offsets 0x4ef2/0x4efd/0x4f04) are left unmodified in main
# and run with the flag already set -- harmless, since none of them touch
# the shared pen-advance primitive themselves.
#
# CLEAR_CAVE's bracket deliberately stops short of the original site's
# trailing `pop ax` (0x4F0F) -- first version replayed it inside the cave
# and hung the game on the very first glyph, live-confirmed (dosbox-mcp:
# CPU wasn't dead, BIOS tick counter still advancing, but the game's own
# main loop never resumed). Root cause: by the time a far-CALLed cave
# starts running, its own return CS:IP (4 bytes, pushed by the far call
# that reached it) sits ON TOP of the stack, above the `push ax` value
# from 0x4F00 the original `pop ax` is meant to retrieve -- so replaying
# `pop ax` inside the cave popped the cave's OWN return IP into AX
# instead, then `retf` popped CS/the real pushed-AX value as if they were
# IP/CS, jumping to a garbage address. Leaving `pop ax` as a live,
# untouched byte in main *after* the far-call+nops (where the cave's own
# retf has already restored the stack to normal before that byte ever
# runs) sidesteps the whole problem -- same lesson as QUANTITY_EXIT_CAVE's
# stray `add sp,4` bug, different instruction.
BANNER_SET_CAVE = bytes.fromhex(
    "8b169f42"    # mov dx,[0x429f]        (replayed)
    "8b1ea142"    # mov bx,[0x42a1]        (replayed)
    "c606ec4201"  # mov byte [0x42ec],1    (RTL flag on)
    "cb"          # retf
)
BANNER_CLEAR_CAVE = bytes.fromhex(
    "89169f42"    # mov [0x429f],dx        (replayed)
    "891ea142"    # mov [0x42a1],bx        (replayed)
    "c606ec4200"  # mov byte [0x42ec],0    (RTL flag off)
    "cb"          # retf
)

# --- Sietch-info water-row reorder cave ---
#
# Reimplements location_0605c's has-water/no-water branch from scratch,
# reordered so the quantity draws BEFORE the label instead of after (see
# ENABLE_WATER_ROW_REVERSAL above for the full derivation). Replaces the
# original routine's one call site (a near call wrapped in push ax/pop ax
# at the caller) with a far call into this cave -- see WATER_ROW_SITE_ORIG
# below.
#
# THREE EARLIER VERSIONS OF THIS CAVE WERE WRONG -- all live-confirmed
# broken (dosbox-mcp), each fixed by tracing/re-simulating instead of
# guessing again, per [[feedback_disassembly_over_blackbox_testing]]:
#
# v1 far-called cad8/cab1/dda1/cbf8/6f1a directly. A far call pushes a
# 4-byte CS:IP return address, but every one of those functions ends in a
# plain near `ret` (2 bytes) -- they're near-called from elsewhere in main
# and know nothing about this cave. The near `ret` only pops the IP,
# leaving the far call's pushed CS on the stack; execution resumes at the
# right offset under the WRONG segment (main's, unchanged by a near ret,
# instead of the cave's TSR segment) -- i.e. jumps into main at a small
# numeric offset that's really a cave-relative address, garbage code.
# Symptom: game faded to black and rendered intro dialogue text on the
# first sietch click.
#
# v2 fixed the segment problem with a generic `call si ; retf` trampoline
# living in main (SI holding the real target address, chosen because none
# of the 5 targets seemed to read it as input) but missed two more bugs,
# found by re-simulating the whole routine by hand instead of guessing a
# third live test blind:
#   1. cbf8 (font_draw_phrase_or_command_string_with_color_at_pos)
#      internally reseeds the pen from its own dx/bx arguments every time
#      it's called (it calls font_set_draw_position(dx,bx) itself) -- so
#      after dda1 draws the digits and advances the pen, dx/bx still held
#      the STALE pre-digit base position, and calling cbf8 for the label
#      reset the pen right back to it instead of continuing after the
#      digits. Needed an explicit font_get_draw_position call in between
#      to refresh dx/bx first.
#   2. FUN_1000_6f1a -- assumed to be a simple "Equipment:" label draw
#      like the others -- is actually a sprite-layout loop
#      (`draw_sprite_clobbering_bx_dx` in a loop) that reads a real
#      equipment-list pointer out of ES:SI. The mysterious "mov si,0x75"
#      the original code executes (and, oddly, never restores after
#      pushing it for dda1) turns out to be exactly this: SI must still
#      equal 0x75 when 6f1a runs. v2's SI-as-call-target trampoline
#      clobbered SI for every single one of the 7 cave->main calls,
#      including the one immediately before 6f1a itself -- so 6f1a read
#      its equipment pointer from ES:[6f1a-ish garbage] instead of
#      ES:[0x75] and blitted garbage sprites in a loop. Symptom: game kept
#      running (no crash) but the screen filled with visual noise on
#      sietch click -- the CPU never jumped anywhere invalid, it was
#      dutifully executing a sprite-blit loop with corrupted parameters.
#
# v3 sidesteps the whole "which register can the cave borrow" problem:
# location_0605c's own 80-byte body (load offset 0x6DFC-0x6E4C) is now
# dead code -- its one caller was redirected to this cave, and a whole-
# file xref scan confirms nothing else jumps into it (not even the has-
# water/no-water branch's internal `jz`, which is self-contained) -- so
# it's repurposed to hold 6 tiny dedicated trampolines, one per target,
# each just `call near <target> ; retf` (E8 rel16 ; CB, 4 bytes, baked-in
# target, no register involved at all): WATER_ROW_TRAMPOLINES_NEW below,
# addresses in WATER_ROW_TRAMP (computed by the same builder script that
# produced this hex, verified with a full ndisasm round-trip -- not hand-
# counted). The cave itself does a plain `far call <trampoline address>`
# for each logical call (segment placeholder poked with cave_seg_raw, same
# convention as every other far-call site here); WATER_ROW_CAVE_FARCALL_
# OFFSETS are those placeholders' byte offsets within the cave. Since
# nothing borrows any register to reach a trampoline, SI is set exactly
# once, at its original position, and never touched again -- it reaches
# the 6f1a call with the same 0x75 the original code always had there.
# v3 was live-confirmed to fix the segment and pen-reseed bugs (repeatedly
# hovering/toggling a sietch's water row no longer corrupted the screen)
# but still failed on the very first actual sietch-info-panel open: still
# noise.
#
# v3's remaining bug, found the same way (re-simulating the whole routine
# register-by-register rather than another live guess): FUN_1000_6f1a's
# own preamble calls 0xC0EC (`LES SI,[0xfdee]` then `mov bx,ax; shl bx,1;
# add si,[bx+si]`) -- SI was never the input that mattered; **AX** is,
# used as a doubled table index to compute 6f1a's real ES:SI resource
# pointer. In the ORIGINAL code, AX going into 6f1a is whatever survived
# the has-water branch's own `push ax`/`pop ax` bracket around dda1 --
# i.e. the water quantity byte itself (loaded once, well before dda1, and
# never touched again until 6f1a). This cave's v1-v3 reordering set
# `ax=0x62` for the label draw AFTER dda1 and never restored it before
# calling 6f1a, so 6f1a computed its resource pointer from a wild index
# (whatever cbf8 happened to leave in ax) instead of the water quantity --
# a second, independent way to get the exact same "garbage sprite-blit
# loop fills the screen with noise" symptom as v2's SI bug, this time
# surviving all the way to an actual mouse click on the sietch (v2's SI
# bug was already caught by mere hover/toggle, since draw_phrase runs on
# every hover; 6f1a apparently only runs once the info panel actually
# opens).
#
# v4 re-loaded the water quantity fresh from the location struct
# (`al=[di+0x1b]`) and zeroed ah immediately before the 6f1a call, on the
# theory that ax=water_quantity was what the original code passed. WRONG,
# also live-confirmed (dosbox-mcp): a sietch click still filled the screen
# with noise.
#
# v4's mistake: hand-tracing the original's `push cx / push ax / push si /
# call dda1 / pop ax / pop cx` as if it were a clean save-restore. It
# isn't -- popping only 2 of the 3 pushed values in LIFO order means `pop
# ax` actually retrieves the pushed SI value (0x75) and `pop cx` retrieves
# the pushed AX value (the water byte), leaving the *original* cx sitting
# on the stack until the later `pop bx / pop cx` (after 6f1a) finally
# retrieves it back and rebalances the stack. Confirmed by setting
# ENABLE_WATER_ROW_REVERSAL = False, rebuilding, and breaking live
# (dosbox-mcp) at the ORIGINAL, unpatched 6f1a call site on a real sietch
# with water (144): registers read ax=0x75, cx=0x90 (=144) right before
# the call -- i.e. ax ends up holding SI's own constant, and cx ends up
# holding the water quantity, exactly backwards from v4's assumption and
# from what either register held earlier in the function.
#
# v5 replicates that exact live-observed shuffle directly instead of re-
# deriving it by hand a third time: `cl = [di+0x1b]` (water quantity),
# `ch = 0`, `ax = 0x75`, right before the 6f1a call. Live-confirmed
# (dosbox-mcp) fixed: a sietch's info panel now opens showing the correct
# "<water>: <label>" layout and a real equipment icon instead of noise.
#
# v6 fixed a cosmetic issue spotted once v5 finally rendered the real
# panel: the water quantity digits render in a different colour than the
# "water:" label. Root cause: every glyph draw (both the label's and the
# digits') ultimately reads its colour from a shared variable, [0xfe17] --
# but only cbf8 (the label draw) ever WRITES it (`mov [0xfe17],cx`, the
# first thing it does). In the original ordering the label always drew
# first, so [0xfe17] was always freshly set before any digit glyph
# rendered; in this reordering the digits draw first, before cbf8 has ever
# run this call, so they inherited whatever [0xfe17] was last left as by a
# completely unrelated earlier draw (observed: consistently white). Fix:
# write the same cx cbf8 would use into [0xfe17] explicitly, right after
# `mov cl,6`, before anything draws.
#
# v6 was live-confirmed (dosbox-mcp, pixel-sampled) to fix digits-vs-label
# consistency *within* one sietch's panel -- but the user then reported
# the label's colour still drifting *between* different sietches, which
# v6 didn't touch. Root cause, found by breaking at both the digit-draw
# and label-draw call sites and reading [0xfe17]/cx directly rather than
# hand-tracing a fourth time: the same `push cx,ax,si / pop ax,pop cx`
# mismatched-pop dance already identified in v4/v5 (see above) doesn't
# just affect ax going into 6f1a -- by the time execution reaches the
# label draw, `pop cx` has *also* overwritten the live cx register with
# the pushed ax value, i.e. the water quantity byte, not the color v6 had
# just written to [0xfe17]. cbf8 then unconditionally does `mov
# [0xfe17],cx` using that corrupted cx -- so the label's colour ends up
# being driven by the water quantity NUMBER itself, which obviously
# differs sietch to sietch. (The digits render correctly and consistently
# because they draw *before* this corruption happens, using the still-
# correct [0xfe17] v6 set moments earlier.)
#
# v7 (current) reloads cx from [0xfe17] itself (still holding the correct
# colour, untouched since the preamble wrote it) immediately before the
# label draw, undoing the pop dance's side effect on cx for this one
# purpose without disturbing it for the 6f1a fix that relies on the same
# dance later.
#
#   50                          push ax        (replays the call site's own displaced instruction)
#   9A FC6D 0000                 call far trampoline[select_font]  (-> font_select_small_font)
#   B1 06                         mov cl,6
#   89 0E 17FE                    mov [0xfe17],cx   (v6 fix -- see above)
#   83 C3 0A                      add bx,0xa
#   8B 16 9116                    mov dx,[0x1691]
#   83 C2 04                      add dx,4
#   8A 45 1B                      mov al,[di+0x1b]      (water quantity byte)
#   BE 75 00                      mov si,0x75    (original position, never touched again below)
#   BD 63 00                      mov bp,0x63
#   F6 45 0A 20                   test byte [di+0xa],0x20
#   74 34                         jz NOWATER
#   -- HAS WATER --
#   9A 006E 0000                   call far trampoline[set_pos]   (-> font_set_draw_position(dx,bx) --
#                                                                    dda1 has no position arg of its own)
#   51 / 50 / 56                    push cx / push ax / push si
#   9A 086E 0000                     call far trampoline[digit_draw]   (-> draw the 3-digit quantity)
#   58 / 59                           pop ax / pop cx
#   9A 046E 0000                       call far trampoline[get_pos]   (-> refresh dx,bx from the pen's
#                                                                        CURRENT post-digit position --
#                                                                        cbf8 below reseeds from these)
#   8B 0E 17FE                           mov cx,[0xfe17]   (v7 fix -- see above; undoes the pop-dance's
#                                                            corruption of cx before cbf8 reads it)
#   B8 6200                              mov ax,0x62
#   9A 0C6E 0000                          call far trampoline[draw_phrase]   (-> "water:" label,
#                                                                              continuing after the digits)
#   83 C3 07                               add bx,7
#   8B 16 9116                              mov dx,[0x1691]
#   83 C2 04                                 add dx,4
#   8B 2E 9516                                mov bp,[0x1695]
#   53                                         push bx
#   8A 4D 1B                                    mov cl,[di+0x1b]   (water quantity -- see v5 fix
#                                                                    above; live-observed cx/ax
#                                                                    values, not hand-traced)
#   30 ED                                        xor ch,ch
#   B8 7500                                      mov ax,0x75
#   9A 106E 0000                                 call far trampoline[equip_row]   (-> equipment sprite row)
#   5B / 59                                     pop bx / pop cx
#   EB 19                                        jmp DONE
#   -- NOWATER --
#   B8 6200                       mov ax,0x62
#   9A 0C6E 0000                   call far trampoline[draw_phrase]   (-> "water:" label alone)
#   8B C5                           mov ax,bp
#   83 C3 07                         add bx,7
#   8B 16 9116                        mov dx,[0x1691]
#   83 C2 0A                           add dx,0xa
#   9A 0C6E 0000                        call far trampoline[draw_phrase]   (-> "No wind-trap" line)
#   -- DONE --
#   58                       pop ax   (replays the call site's own displaced instruction)
#   CB                        retf
WATER_ROW_CAVE = bytes.fromhex(
    "509afc6d0000b106890e17fe83c30a8b16911683c2048a451bbe7500bd6300"
    "f6450a2074409a006e00005150569a086e000058599a046e00008b0e17feb8"
    "62009a0c6e000083c3078b16911683c2048b2e9516538a4d1b30edb875009a"
    "106e00005b59eb19b862009a0c6e00008bc583c3078b16911683c20a9a0c6e"
    "000058cb"
)
WATER_ROW_CAVE_FARCALL_OFFSETS = [4, 40, 48, 55, 67, 95, 107, 124]

# The call site this cave replaces: location_0605c's caller (load offset
# 0x6DF1) does `push ax / call location_0605c / pop ax` (5 bytes) -- the
# push/pop are folded into the cave (replayed as its first/last
# instructions above) so the whole 5-byte span becomes far_call(cave_off),
# an exact-length swap using the same helper every other far-call site here
# uses.
WATER_ROW_SITE_OFFSET = 0x6DF1
WATER_ROW_SITE_ORIG = "50E8070058"

# location_0605c's own now-dead 80-byte body (load offset 0x6DFC-0x6E4C) --
# repurposed to hold the 6 dedicated near-call trampolines described above.
# Only the first 24 bytes are used; the remaining 56 are NOPed out (dead,
# but harmless-if-ever-reached rather than leftover live opcodes).
WATER_ROW_TRAMP = {
    "select_font": 0x6DFC,
    "set_pos": 0x6E00,
    "get_pos": 0x6E04,
    "digit_draw": 0x6E08,
    "draw_phrase": 0x6E0C,
    "equip_row": 0x6E10,
}
WATER_ROW_TRAMPOLINES_OFFSET = 0x6DFC
WATER_ROW_TRAMPOLINES_ORIG = (
    "e8d95cb10683c30a8b16911683c204b86200e8e75d8a451bbe7500bd6300f645"
    "0a20741d515056e87b6f585983c3078b16911683c2048b2e951653e8e0005b59"
    "c38bc583c3078b16911683c20ae9ac5d"
)
WATER_ROW_TRAMPOLINES_NEW = (
    "e8d95ccbe8ae5ccbe8bb5ccbe8966fcbe8e95dcbe80701cb"
    + "90" * (len(WATER_ROW_TRAMPOLINES_ORIG) // 2 - 24)
)

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
if ENABLE_MAP_BANNER_REVERSAL:
    CAVES += [
        ("banner_set", BANNER_SET_CAVE),
        ("banner_clear", BANNER_CLEAR_CAVE),
    ]
if ENABLE_WATER_ROW_REVERSAL:
    CAVES += [
        ("water_row", WATER_ROW_CAVE),
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
    bs = tsr_offsets.get("banner_set")
    bc = tsr_offsets.get("banner_clear")
    wr = tsr_offsets.get("water_row")

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

    # --- Map-destination-banner reveal (COMMAND1 line 76, "select a
    # destination on the map") -- see ENABLE_MAP_BANNER_REVERSAL/
    # BANNER_SET_CAVE/BANNER_CLEAR_CAVE above for the derivation. ---
    if ENABLE_MAP_BANNER_REVERSAL:
        # Starting pen X, in FUN_1000_4e71 (the callback's one-time setup):
        # `mov word [0x429f], 0x55` (the box's LEFT edge) -> 0x9A (154), the
        # exact X the OLD left-to-right reveal used to finish at (read live
        # via dosbox-mcp: DS-relative [0x429f] once the old, LTR reveal had
        # completed) -- i.e. the RTL text now occupies the SAME [0x55,0x9A]
        # footprint the original English version always used, just walked
        # in the opposite direction/order.
        #
        # A second attempt anchored to the header box's true right border
        # instead (screen-measured constant 0xF1 = 241) so the text would
        # hug the box edge properly -- live-tested and it DID look right on
        # its own, but uncovered a real bug in a completely different
        # system: the map screen's hover-name text (a sietch/location name,
        # drawn by the ordinary, unrelated draw_command_menu_item path when
        # hovering a grid cell) was NEVER an erase mechanism -- it only ever
        # relied on new text overwriting old text at the SAME starting X.
        # The original English default prompt and hover names both start
        # from nearly the same left edge (0x55 vs draw_command_menu_item's
        # own fixed 0x5D), so a shorter hover name still happened to
        # overwrite most of the old prompt's glyphs as a side effect. Once
        # our banner moved to the box's right edge, it no longer shares any
        # footprint with where hover names draw, so whichever of our
        # glyphs a given hover name's text doesn't reach far enough right
        # to cover stayed on screen as an orphaned red fragment (confirmed
        # live: a lone "ב" -- our first-drawn, now-rightmost letter --
        # sitting to the right of a short hover name like "מדבר"). Fixing
        # that properly needs an explicit clear-rect patch at the hover-
        # transition site, which needs its own dedicated RE session (an
        # unfamiliar generic blit primitive, not yet live-verified) --
        # reverted to 0x9A instead, matching the original engine's own
        # overwrite assumption exactly, zero new risk -- but user-tested
        # and found too far left (text still hugs the box's left half).
        # Compromise: 0xC6 (198), roughly centering the text's own midpoint
        # in the header box rather than flush against either edge -- solved
        # from the same screen-measured linear map (screen = 8.16 +
        # 1.9275*engine) for the engine-X whose corresponding text span
        # centers within the box's screen extent (146..499, center 322.5).
        # Sits past 0x9A, so may reintroduce some hover-transition residue
        # depending on hover-name width (see the clear-rect note above) --
        # not yet re-verified live as of this comment.
        sites.append(Site(
            "map-banner start pen X (left edge -> centered in header box)",
            0x4E89, "C7069F425500", lambda: bytes.fromhex("C7069F42C600"),
        ))

        # SET bracket, 0x4EEA: `mov dx,[0x429f]; mov bx,[0x42a1]` (8B) ->
        # far call banner_set_cave + 3 nops (replays both, then raises the
        # RTL flag). Left deliberately wide of the actual `call
        # font_draw_glyph_func_small` at 0x4F01 -- the font_set_draw_
        # position/font_select_small_font calls in between (0x4EF2/0x4EFD)
        # don't touch the shared pen-advance primitive, so having the flag
        # raised across them too is harmless, and it means neither of
        # those CALL instructions ever needs replaying inside a cave living
        # in a different segment (the TSR) -- see the cave derivation notes.
        sites.append(Site(
            "map-banner RTL flag set hook (frame-task callback pre-draw)",
            0x4EEA, "8B169F428B1EA142",
            (lambda o=bs: far_call(o) + b"\x90\x90\x90"),
            poke_field_rel=3,
        ))

        # CLEAR bracket, 0x4F07: `mov [0x429f],dx; mov [0x42a1],bx` (8B) ->
        # far call banner_clear_cave + 3 nops (replays both, then lowers the
        # RTL flag) -- symmetric with the SET bracket above. Deliberately
        # stops BEFORE the site's original trailing `pop ax` (0x4F0F, 1B),
        # which is left untouched in main -- see BANNER_CLEAR_CAVE's
        # comment for why replaying it inside the cave hung the game.
        sites.append(Site(
            "map-banner RTL flag clear hook (frame-task callback post-draw)",
            0x4F07, "89169F42891EA142",
            (lambda o=bc: far_call(o) + b"\x90\x90\x90"),
            poke_field_rel=3,
        ))

    # --- Sietch-info water-row reorder (digits drawn before the label
    # instead of after) -- see ENABLE_WATER_ROW_REVERSAL/WATER_ROW_CAVE
    # above for the full derivation. ---
    if ENABLE_WATER_ROW_REVERSAL:
        sites.append(Site(
            "water-row dedicated near-call trampolines (location_0605c's dead body, repurposed)",
            WATER_ROW_TRAMPOLINES_OFFSET, WATER_ROW_TRAMPOLINES_ORIG,
            (lambda: bytes.fromhex(WATER_ROW_TRAMPOLINES_NEW)),
        ))
        sites.append(Site(
            "sietch-info water-row call site -> far call cave (digits before label)",
            WATER_ROW_SITE_OFFSET, WATER_ROW_SITE_ORIG,
            (lambda o=wr: far_call(o)),
            poke_field_rel=3,
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
    if ENABLE_WATER_ROW_REVERSAL:
        farjmp_local_offsets.extend(
            tsr_offsets["water_row"] + off for off in WATER_ROW_CAVE_FARCALL_OFFSETS)
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
