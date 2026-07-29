#!/usr/bin/env python3

"""
rtl_cave_tsr.py - builds DUNETSR.COM, a tiny DOS TSR (terminate-and-stay-
resident program) that holds the RTL-engine cave code (see
patch_rtl_engine.py) in its own, separately DOS-allocated memory block,
instead of that code being appended to DUNEPRG.EXE's own load module.

WHY A TSR AT ALL
-----------------
Appending the cave to DUNEPRG.EXE's own file -- whether placed right after
the load module's own end, or past a computed "safe" boundary derived from
e_minalloc plus a large margin -- was tried and, in both forms, confirmed
live (dosbox-mcp) to eventually get overwritten by the game's own runtime
resource loader (sprites/audio/dialogue text). That loader is a simple
bump allocator that claims scratch space directly within DUNEPRG.EXE's own
DOS-granted memory block, and does not ask DOS what's "free" before
writing -- so nothing placed inside that process's own memory, no matter
how far out, is safe from it forever. Confirmed twice: once at the
load-module-adjacent position (original design), once ~87KB further out
(e_minalloc + 32KB margin) -- both got clobbered, the second time within
seconds of skipping the intro.

A TSR sidesteps this architecturally rather than by picking a bigger
number: it is a SEPARATE DOS-allocated memory block, owned by a different
process. DOS's own memory allocator (unlike the game's internal bump
allocator) does respect ownership -- once the TSR is resident, the game's
own greedy e_maxalloc=0xFFFF allocation gets "whatever's left", and the
game's bump allocator, which only ever computes addresses relative to its
own segment registers, has no way to reach across into the TSR's
independently-owned block. This requires the TSR to load and go resident
BEFORE DUNEPRG.EXE starts (see build_translation.py's launcher-.BAT
handling -- DUNE.BAT).

PROTOCOL
--------
The TSR installs a handler on INT 60h (conventionally reserved for user
programs; chains to whatever was there before for any call that isn't
ours, so a coincidental unrelated INT 60h user -- unlikely in this
single-purpose pipeline, but cheap to handle correctly -- still works).
DUNEPRG.EXE's own init hook (see patch_rtl_engine.py) calls:

    mov ax, 0x4455        ; "are you the Dune RTL cave TSR?"
    int 0x60
    ; if this is really us: ax == 0x4456, bx == our own segment

and then pokes that segment directly into its own far-call sites' segment
operands, and into the two cave-internal far-jump-back-to-main segment
fields (which live inside the TSR's own memory, but need the GAME's load
segment, not the TSR's -- the game computes and pokes that value into its
own copy of the cave at TSR-query time, since the TSR itself has no way to
know the game's load segment in advance).

TSR memory-safety note: DOS enforces no process isolation in real mode, so
the querying game writing into the TSR's memory (to poke those two
far-jump fields) is unremarkable -- it's just an ordinary segmented write
to memory whose address happens to be owned by another process, which
real-mode DOS neither prevents nor needs to.

Usage: build_tsr_com(blob, blob_offsets) -> (com_bytes, cave_offsets)
       where cave_offsets maps the same cave names build_blob() uses to
       their .COM-relative offset (== the offset from the TSR's own CS
       once resident, since a .COM's own code starts at CS:0x100 and cave_
       offsets already includes that +0x100).
"""

import struct

# Hand-assembled with nasm from readable source (see this module's
# derivation session), then round-tripped through ndisasm to confirm it
# decodes back to the intended instructions before being frozen as hex
# here -- same process used for every cave in patch_rtl_engine.py.
#
# Frees its own inherited environment block before going resident (DOS
# doesn't do this automatically for a TSR the way it does for a normally-
# exiting program) -- found necessary live: a book-page-flip crash under
# COMM.BAT's SDB2207 sound-driver config traced back to DUNEPRG.EXE's own
# stock "Not enough standard memory to run Dune" bailout path corrupting
# the MCB chain when memory got razor-thin, and this TSR leaving a ~1.1KB
# environment block resident (visible in a `mem` MCB dump as a stray
# 'COMMAND'-named block owned by DUNETSR's own PSP) was eating into that
# already-thin margin for no reason -- the TSR never needs its inherited
# environment after install.
#
#   BITS 16
#   ORG 0x100
#   start:
#       mov bx, [0x2C]          ; word at PSP:0x2C = our environment segment
#       mov es, bx
#       mov ah, 0x49            ; free memory block (ES = block to free)
#       int 0x21
#       mov ax, 0x3560          ; AH=35h (get vector), AL=60h
#       int 0x21                ; -> ES:BX = current INT 60h handler
#       mov [old_60_off], bx
#       mov [old_60_seg], es
#       mov dx, handler         ; DS already == CS for a .COM at entry
#       mov ax, 0x2560          ; AH=25h (set vector), AL=60h
#       int 0x21
#       mov dx, resident_end
#       add dx, 15
#       mov cl, 4
#       shr dx, cl              ; dx = ceil(resident_end/16) paragraphs
#       mov ax, 0x3100          ; AH=31h AL=0 (exit code 0): TSR
#       int 0x21
#   handler:
#       cmp ax, 0x4455
#       je .respond
#       jmp far [cs:old_60_off]
#   .respond:
#       mov bx, cs
#       mov ax, 0x4456
#       iret
#   old_60_off: dw 0
#   old_60_seg: dw 0
#   cave_start:
#       db 0x90, 0x90, 0x90, 0x90   ; placeholder, replaced below
#   resident_end:
_TEMPLATE = bytes.fromhex(
    "8b1e2c008ec3b449cd21"
    "b86035cd21891e3e018c064001"
    "ba2e01b86025cd21"
    "ba460183c20fb104d3eab80031cd21"
    "3d554474052eff2e3e01"
    "8ccbb85644cf"
    "0000"
    "0000"
    "90909090"
)
_CAVE_PLACEHOLDER = b"\x90\x90\x90\x90"
_RESIDENT_END_MOV_OPCODE = 0xBA  # `mov dx, imm16` -- the resident_end reference
_RESIDENT_END_PLACEHOLDER = 0x0146  # resident_end's offset in the unmodified template

_COM_ORG = 0x100


def build_tsr_com(blob, blob_offsets):
    """blob/blob_offsets: the exact same cave blob build_blob() in
    patch_rtl_engine.py produces. Returns (com_bytes, cave_offsets) where
    cave_offsets[name] is that cave's absolute .COM-relative offset (i.e.
    its offset from the TSR's own CS once resident -- add nothing further)."""
    placeholder_idx = _TEMPLATE.index(_CAVE_PLACEHOLDER)
    assert _TEMPLATE.count(_CAVE_PLACEHOLDER) == 1, "cave placeholder must be unique in the template"

    com = bytearray(_TEMPLATE[:placeholder_idx])
    com += blob

    new_resident_end = _COM_ORG + len(com)

    # Patch the `mov dx, resident_end` immediate (found by opcode + the
    # template's own known placeholder value, not a hardcoded byte offset,
    # so this keeps working if the template above is ever edited).
    sig = bytes([_RESIDENT_END_MOV_OPCODE]) + struct.pack("<H", _RESIDENT_END_PLACEHOLDER)
    mov_idx = com.index(sig)
    assert com.count(sig) == 1, "resident_end reference must be unique"
    struct.pack_into("<H", com, mov_idx + 1, new_resident_end)

    cave_offsets = {name: _COM_ORG + placeholder_idx + off for name, off in blob_offsets.items()}
    return bytes(com), cave_offsets
