#!/usr/bin/env python3

"""
tu.py - Text Util for Cryo's game Dune (pure-Python port of utils/tu.c).

Unpack - Extract sentences from Dune files (COMMAND{1,3}, PHRASE{1,3}{1,2})
Pack - Pack files created by this util back to the game format.

Mirrors the CLI of the compiled utils/tu binary:
    tu.py -p|-u FILE1 FILE2
    tu.py -c FILE
"""

import argparse
import sys
from pathlib import Path

USAGE = (
    "usage: [OPTION]... [FILE]...\n"
    " -v, --verbose\tverbose mode\n"
    " -p, --pack\tpack FILE1 and save as FILE2\n"
    " -u, --unpack\tunpack FILE1 and save as FILE2\n"
    " -c, --check\tcheck text file FILE\n"
    " -h, --help\tprint this help\n"
)


def to_u16(buf, i):
    """Read a little-endian uint16 at byte offset i, or a sentinel if out of range."""
    if i + 1 >= len(buf):
        return 0xFFFF
    return buf[i] | (buf[i + 1] << 8)


def check(buf):
    """Validate the phrase-offset table against the 0xFF sentence terminators."""
    length = len(buf)
    if length < 3:
        return False

    offset0 = to_u16(buf, 0)
    text_bgn = offset0
    if offset0 > length or length > 65535:
        return False

    ptr_pos = 2
    offset_prev = offset0
    offset = to_u16(buf, ptr_pos)
    while offset < length and ptr_pos != text_bgn:
        idx = buf.find(b"\xff", offset_prev)
        if idx == -1 or idx + 1 != offset:
            return False
        ptr_pos += 2
        offset_prev = offset
        offset = to_u16(buf, ptr_pos)

    idx = buf.find(b"\xff", offset_prev)
    if idx == -1 or idx + 1 != length:
        return False

    return True


def unpack(buf, out_path):
    offset = to_u16(buf, 0)
    if b"\n" in buf[offset:]:
        raise ValueError("Char '\\n' is used!")

    content = bytearray(buf[offset:])
    for i in range(len(content)):
        if content[i] == 0xFF:
            content[i] = ord("\n")

    Path(out_path).write_bytes(bytes(content))


def pack(buf, out_path, verbose=False):
    content = bytearray(buf)
    phrases = 0
    for i in range(len(content)):
        if content[i] == ord("\n"):
            content[i] = 0xFF
            phrases += 1

    length = len(content)
    if phrases * 2 + length > 65535:
        raise ValueError("Input file is too big")

    off_start = phrases * 2
    offsets = []
    content_idx = 0
    offset = off_start
    for _ in range(phrases):
        offsets.append(offset)
        term_idx = content.find(b"\xff", content_idx)
        if term_idx == -1:
            raise ValueError("Unknown error (input file corrupted?)")
        cumulative_len = term_idx + 1
        offset = off_start + cumulative_len
        content_idx = cumulative_len

    if verbose:
        print(f"{phrases} phrases found")

    out = bytearray()
    for off in offsets:
        out += off.to_bytes(2, "little")
    out += content
    Path(out_path).write_bytes(bytes(out))

    if verbose:
        print(f"Created file '{out_path}' of size: {len(out)}")

    return phrases


def main():
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("-v", "--verbose", action="store_true")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("-p", "--pack", action="store_true")
    group.add_argument("-u", "--unpack", action="store_true")
    group.add_argument("-c", "--check", action="store_true")
    parser.add_argument("-h", "--help", action="store_true")
    parser.add_argument("files", nargs="*")
    args = parser.parse_args()

    op_count = sum([args.pack, args.unpack, args.check])
    if args.help or op_count != 1:
        print(USAGE)
        return 0

    if args.pack or args.unpack:
        if len(args.files) != 2:
            print(USAGE)
            return 0
        fin, fout = args.files
    else:
        if len(args.files) != 1:
            print(USAGE)
            return 0
        fin, fout = args.files[0], None

    try:
        buf = bytearray(Path(fin).read_bytes())
    except OSError as e:
        print(f"Opening file '{fin}' failed: {e.strerror}", file=sys.stderr)
        return 0

    if not args.pack:
        if not check(buf):
            print(f"Text file '{fin}' is not valid!")
            return 1
        if args.unpack:
            try:
                unpack(buf, fout)
            except ValueError as e:
                print(f"Error occured while unpacking file: {e}", file=sys.stderr)
                return 1
    else:
        try:
            pack(buf, fout, verbose=args.verbose)
        except ValueError as e:
            print(f"Error occured while packing file: {e}", file=sys.stderr)
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
