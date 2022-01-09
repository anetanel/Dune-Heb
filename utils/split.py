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


def pad_line(line, length):
    num_of_pads = 0
    if length < args.len + 20:
        num_of_pads = int((args.len + 20 - length) / CHAR_WIDTH.get(ord("#")))+1
    pad = num_of_pads * bytes("#", "ascii")
    return pad + line


def split_and_reverse(split_location, line):
    new_line = bytes()
    prev_split = None
    for s in split_location:
        location = s[0]
        length = s[1]
        current_line = line[location:prev_split:-1].strip()
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


def find_split(line):
    count = 0
    location = 0
    splits = []
    for i in line:
        count += CHAR_WIDTH.get(i, DEFAULT_CHAR_WIDTH)
        if count >= args.len or i == 0xfe:
            if i == ord(" ") or i == 0xfe:
                splits.append((location, count))
                count = 0
        location += 1
    splits.append((location, count))
    # print(count)
    # print(splits)
    return splits


def create_new_line(line):
    split_location = find_split(line)
    return split_and_reverse(split_location, line)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--len', help='specify line length', type=int, default=LINE_LENGTH)
    parser.add_argument('--count', help='count give text length', action=argparse.BooleanOptionalAction)
    # parser.add_argument('--split', help='split and reverse line',action=argparse.BooleanOptionalAction, deafult=True)
    parser.add_argument('--line', help='split and reverse line', type=str, default=TEST_LINE)
    parser.add_argument('--input', help='input file', type=str)
    parser.add_argument('--output', help='output file', type=str)

    args = parser.parse_args()

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
        for input_line in io.open(args.input, 'rb'):
            out_file.write(create_new_line(input_line))
    else:
        for input_line in io.open(args.input, 'rb'):
            x = create_new_line(input_line)
            print(x)
