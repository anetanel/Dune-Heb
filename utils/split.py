#!/usr/bin/env python3
import io
import sys
import argparse

CHAR_WIDTH = {
    ord("C"): 4,
    ord("F"): 2,
    ord("G"): 4,
    ord("J"): 2,
    ord("N"): 4,
    ord("U"): 6,
    ord("W"): 4,
    ord("Y"): 2,
    ord("Z"): 4,
    ord(","): 4,
    ord("."): 3,
    ord("!"): 3,
    ord("?"): 6,
    ord('"'): 4,
    ord("#"): 5,
    ord("$"): 6,
    ord("%"): 6,
    ord("-"): 4,
    ord(";"): 5,
    ord(":"): 4,
    ord(" "): 7,
    ord("'"): 3,
    ord("("): 4,
    ord(")"): 4}

LINE_LENGTH = 115
DEFAULT_CHAR_WIDTH = 5
TEST_LINE = "BNJ, ANF HJJBJX LKTFV AV EOX BESDX EAQUTJ... AHTV ESJOT PLFL LEHGJT AFVNF MHFLJV."

# Multi-byte control tokens (player-name/location/quantity substitution
# variables, line-break variants -- see utils/heb_encode.py's tokenizer for
# the canonical byte-level definitions) must never be split across a
# word-wrap boundary, nor have their internal byte order reversed: the
# game's phrase engine reads their bytes in a fixed order (e.g. opcode then
# operand) regardless of the surrounding Hebrew text being stored
# right-to-left. Ordered by descending length so a 3-byte match is tried
# before the 2-byte prefixes that could also match its first two bytes.
MULTI_BYTE_TOKENS = [
    b"\x80\x00",  # matched below with one extra operand byte (3 bytes total)
    b"\r\x06",
    b"\r\x08",
    b"\x91\x9d",
    b"\x91",
    b"\x92",
]


def scan_units(line):
    """Split `line` into a list of byte-strings: each atomic control token
    or digit run intact, everything else as single bytes."""
    units = []
    i = 0
    n = len(line)
    while i < n:
        b = line[i]
        if b == 0x80 and line[i + 1:i + 2] == b"\x00" and i + 2 < n:
            units.append(line[i:i + 3])
            i += 3
        elif line[i:i + 2] in (b"\r\x06", b"\r\x08", b"\x91\x9d"):
            units.append(line[i:i + 2])
            i += 2
        elif b in (0x91, 0x92) and i + 1 < n:
            units.append(line[i:i + 2])
            i += 2
        elif 0x30 <= b <= 0x39:
            j = i
            while j < n and 0x30 <= line[j] <= 0x39:
                j += 1
            units.append(line[i:j])
            i = j
        else:
            units.append(line[i:i + 1])
            i += 1
    return units


# A "m@@" location-code token (0x80 0x00 <letter>) is 3 raw bytes at
# wrap-calculation time, but the game substitutes it at runtime with an
# actual location name string -- so it must be assumed to take real screen
# width, not the 0 its control-byte encoding would otherwise suggest.
# Measured widths of every location name in COMMAND1.HEB range 14-26;
# using the max keeps the wrap point safely before the substituted text
# could overflow, at the cost of wrapping slightly early for short names.
LOCATION_TOKEN_WIDTH = 26

# A "mq"/"mr" quantity token (0x91/0x92 <char>) is 2 raw bytes at
# wrap-calculation time, but the game substitutes it at runtime with a
# numeric value (troop counts, percentages, spice amounts) -- so it must be
# assumed to take real screen width, not the 0 its control-byte encoding
# would otherwise suggest. Sized for a conservative 3-digit worst case at
# DEFAULT_CHAR_WIDTH, the same "widest observed/plausible value" approach
# used for LOCATION_TOKEN_WIDTH.
QUANTITY_TOKEN_WIDTH = 3 * DEFAULT_CHAR_WIDTH


def unit_width(unit):
    if unit == b"\r":
        return 0
    if len(unit) == 3 and unit[0] == 0x80:
        return LOCATION_TOKEN_WIDTH
    if len(unit) == 2 and unit[0] in (0x91, 0x92):
        return QUANTITY_TOKEN_WIDTH
    if 0x30 <= unit[0] <= 0x39:
        return sum(CHAR_WIDTH.get(b, DEFAULT_CHAR_WIDTH) for b in unit)
    if len(unit) > 1:
        return 0
    return CHAR_WIDTH.get(unit[0], DEFAULT_CHAR_WIDTH)


def pad_line(line, length):
    num_of_pads = 0
    if length < args.len + 20:
        num_of_pads = int((args.len + 20 - length) / CHAR_WIDTH.get(ord("#")))+1
    pad = num_of_pads * bytes("#", "ascii")
    return pad + line


def split_and_reverse(split_location, units):
    new_line = bytes()
    prev_split = None
    for s in split_location:
        location = s[0]
        length = s[1]
        segment_units = units[prev_split if prev_split is not None else 0:location]
        segment_units.reverse()
        current_line = b"".join(segment_units).strip()
        padded_line = pad_line(current_line, length)
        if 0xfe in padded_line:
            i = padded_line.index(0xfe)
            new_line += padded_line[:i] + padded_line[i+1:] + bytes.fromhex("fe")
        else:
            new_line += padded_line + bytes("\r", "ascii")
        # print(current_line)
        # print(padded_line)
        prev_split = location
    return new_line + bytes("\n", "ascii")


def count_length(line):
    count = 0
    for i in line:
        count += CHAR_WIDTH.get(ord(i), DEFAULT_CHAR_WIDTH)
    return count


def find_split(units, max_len=None, force_split_units=frozenset()):
    """Return [(location, count), ...] split points for word-wrapping
    `units`. A split is forced at a unit in `force_split_units` (a
    sentence-boundary marker, e.g. bare `\\r`/M) regardless of accumulated
    width, in addition to the normal length-based wrap at `max_len`."""
    if max_len is None:
        max_len = args.len
    count = 0
    location = 0
    splits = []
    for unit in units:
        count += unit_width(unit)
        if count >= max_len or unit == b"\xfe" or unit in force_split_units:
            if unit == b" " or unit == b"\xfe" or unit in force_split_units:
                splits.append((location, count))
                count = 0
        location += 1
    splits.append((location, count))
    # print(count)
    # print(splits)
    return splits


def create_new_line(line):
    units = scan_units(line)
    split_location = find_split(units)
    return split_and_reverse(split_location, units)


def create_wrapped_sentence_line(line, max_len):
    """Word-wrap a line at `max_len`, same as create_new_line, but also
    force a line break at each bare `\\r` (M marker) sentence boundary --
    for lines like encyclopedia entries that aren't spoken dialogue and
    use a wider box than the dialogue box's LINE_LENGTH, but still consist
    of multiple independent sentences that must never be word-wrapped
    together onto the same row."""
    units = scan_units(line)
    split_location = find_split(units, max_len=max_len, force_split_units={b"\r"})
    return split_and_reverse(split_location, units)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--len', help='specify line length', type=int, default=LINE_LENGTH)
    parser.add_argument('--count', help='count give text length', action=argparse.BooleanOptionalAction)
    # parser.add_argument('--split', help='split and reverse line',action=argparse.BooleanOptionalAction, deafult=True)
    parser.add_argument('--line', help='split and reverse line', type=str, default=TEST_LINE)
    parser.add_argument('--input', help='input file', type=str)
    parser.add_argument('--output', help='output file', type=str)
    parser.add_argument('--wide-lines', help='comma-separated 0-based line numbers to word-wrap at '
                                              '--wide-len instead of --len, with a forced line break '
                                              'at every sentence boundary (bare \\r/M marker) -- for '
                                              'non-dialogue entries in a wider box, e.g. PHRASE12\'s '
                                              'encyclopedia lines', type=str, default='')
    parser.add_argument('--wide-len', help='line length for --wide-lines', type=int, default=LINE_LENGTH)

    args = parser.parse_args()
    wide_lines = {int(n) for n in args.wide_lines.split(',') if n != ''}

    # if len(sys.argv) > 1:
    #     input_line = sys.argv[1]
    # else:
    #     input_line = TEST_LINE
    input_line = args.line
    if args.count:
        padding = (args.len - count_length(input_line))
        print(padding, file=sys.stderr)
        print(int(padding) * '#', end='')
        # print(int(padding) * '#' + input_line)
    elif args.input and args.output:
        out_file = open(args.output, 'wb')
        # in_file = open(args.input, 'r')
        # data = in_file.readlines()
        for line_no, input_line in enumerate(io.open(args.input, 'rb')):
            if line_no in wide_lines:
                out_file.write(create_wrapped_sentence_line(input_line, args.wide_len))
            else:
                out_file.write(create_new_line(input_line))
    else:
        for input_line in io.open(args.input, 'rb'):
            x = create_new_line(input_line)
            print(x)
