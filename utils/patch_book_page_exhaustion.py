#!/usr/bin/env python3

"""
patch_book_page_exhaustion.py - stop the History/"book" window's page-turn
handler from ever loading the CREDITS.HNM video in game/DUNEPRG.EXE.

THE BUG
-------
Reading a book topic to its true last page and clicking "next page" one
more time hits the engine's page-lookup-failed handler. Going *backward*
off the start (dx negative) takes a harmless "can't go further" branch
(a page-turn animation + sound). Going *forward* off the end takes a
different branch: it sets a one-shot latch bit in data_c6 and calls
play_credits, loading CREDITS.HNM as an easter egg for finishing a topic.

Confirmed via the CD build's disassembly (madmoose/dune-chani) and cross-
checked live in DOSBox-X against both the original English EXE/assets and
our translated build: play_credits's own memory needs are already
marginal in the base 1992 engine (the pristine English game partially
glitches/hangs there too under the SDB2207 sound-driver config). Our
build's RTL-cave TSR (see rtl_cave_tsr.py) holds a small amount of
resident memory that's just enough to tip loading CREDITS.HNM over the
edge into an outright allocation failure -- which hits DUNEPRG.EXE's own
"Not enough standard memory to run Dune" bailout, and that bailout path
corrupts the DOS MCB chain on its way out (confirmed present in the
unpatched original EXE too), crashing to DOS with a corrupted memory
chain that persists until DOS is rebooted.

THE FIX
-------
One instruction, one byte: the forward-exhaustion branch's
`jnz <return-early>` (skip past the play_credits call if the latch bit is
already set from an earlier visit) becomes an unconditional `jmp` to that
same return-early point -- i.e. the branch that was already proven safe
(it's the very same "just return" path taken every time *after* the
first) now runs *every* time, first visit included. play_credits is never
reached from this call site again. No other byte moves; the instruction
stays the same length (both are 2-byte short jumps: opcode + rel8
displacement, and the displacement is unchanged).

Found in the floppy DUNEPRG.EXE by structural instruction-pattern search
(same technique as the location-name-order patch and the charisma live
patch, see repo history/memory) rather than byte-signature matching,
since the floppy build's own addresses differ from the CD build's:
ui_draw_book_turning_page's very distinctive body (immediates 0xb/0x9e/
0x1b/0xa/0xb/0x2, draw_sprite + draw_sprite_clobbering_bx_dx + wait_a_bit
calls) has exactly one match in the floppy load module, and its known
callers reproduce the CD's forward/backward-exhaustion branch structure
byte-for-byte once translated through ndisasm.

Usage:
    ./patch_book_page_exhaustion.py [--exe PATH]

Idempotent: does nothing if already patched. Refuses (and makes no
change) if the byte at this offset matches neither the known original nor
the known patched value, since that means this isn't the DUNEPRG.EXE
build this offset was derived from. Always backs up the pre-patch file
once, next to the original, before the first patch of a fresh run.
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

# Load-module offset of the `jnz <return-early>` in the book page-turn
# handler's forward-exhaustion branch (floppy load offset 0xafbc):
#   ...
#   test byte [0xc6],0x4      ; latch bit already set from an earlier visit?
#   jnz  0xaf73               ; <- patched: JNZ (0x75) -> JMP (0xeb)
#   or   byte [0xc6],0x4      ; (unreached once patched) set the latch
#   call 0xa1f                ; (unreached once patched) play_credits
#   ...
# 0xaf73 is a shared "just return" exit also used by an adjacent check a
# few instructions earlier, confirming it's an already-safe, well-trodden
# path -- not a bailout invented for this patch.
_LOAD_OFFSET = 0xAFBC
_FILE_OFFSET = MZ_HEADER_SIZE + _LOAD_OFFSET
_ORIG = bytes.fromhex("75B5")  # jnz 0xaf73
_NEW = bytes.fromhex("EBB5")  # jmp short 0xaf73 (same displacement byte)

assert len(_ORIG) == len(_NEW)


def apply_patches(exe_path):
    """Patch exe_path in place. Returns True if a change was made, False
    if it was already patched. Exits without writing anything if the byte
    at this offset matches neither the known original nor the known
    patched value.
    """
    with open(exe_path, "rb") as f:
        data = bytearray(f.read())

    current = bytes(data[_FILE_OFFSET:_FILE_OFFSET + len(_ORIG)])
    if current == _NEW:
        print(f"[patch] {exe_path.name}: book page-exhaustion credits-crash already patched")
        return False
    if current != _ORIG:
        sys.exit(
            f"{exe_path}: bytes at file offset 0x{_FILE_OFFSET:x} (book page-exhaustion "
            f"credits-crash) match neither the known original nor the known patched "
            f"sequence (found {current.hex()}). Refusing to patch anything -- this offset "
            f"was derived from a specific DUNEPRG.EXE build and may not apply here."
        )

    backup_path = exe_path.with_suffix(exe_path.suffix + ".orig-backup")
    if not backup_path.exists():
        shutil.copy2(exe_path, backup_path)
        print(f"[patch] backed up {exe_path.name} -> {backup_path.name}")

    data[_FILE_OFFSET:_FILE_OFFSET + len(_NEW)] = _NEW
    print(f"[patch] {exe_path.name}: applied book page-exhaustion credits-crash fix at file offset 0x{_FILE_OFFSET:x}")

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
