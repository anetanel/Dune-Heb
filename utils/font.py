#!/usr/bin/env python3

"""
font.py - font util for DUNECHAR file (extracted from DUNECHAR.HSQ)

this util can:
	- dump current DUNECHAR file into image
	- load new character font from image into DUNECHAR file
"""

import sys
import argparse
from PIL import Image

__author__ = "Jan Havran"

DUMP_WIDTH = 16
DUMP_HEIGHT = 16
DUMP_ROW_WIDTH = 8
DUMP_ROW_HEIGHT = 10
CHAR_TABLE_CNT = DUMP_WIDTH * DUMP_HEIGHT
CHAR_UPP_TABLE_CNT = 128
CHAR_LOW_TABLE_CNT = CHAR_TABLE_CNT - CHAR_UPP_TABLE_CNT
CHAR_UPP_HEIGHT = 9
CHAR_LOW_HEIGHT = 7

FILE_TOTAL_SIZE = CHAR_TABLE_CNT + \
                  CHAR_UPP_TABLE_CNT * CHAR_UPP_HEIGHT + \
                  CHAR_LOW_TABLE_CNT * CHAR_LOW_HEIGHT

col_black = (0x00, 0x00, 0x00)
col_red = (0xD0, 0xA0, 0xA0)
col_green = (0xA0, 0xD0, 0xA0)


def char_base(char):
    """Byte offset into DUNECHAR data where a character's row bitmaps
    start, and its glyph height. Char width tags (1 byte each) come
    first for all CHAR_TABLE_CNT chars, then row bitmaps packed
    back-to-back per char (9 rows for chars <128, 7 rows after)."""
    is_upper = char < CHAR_UPP_TABLE_CNT
    height = CHAR_UPP_HEIGHT if is_upper else CHAR_LOW_HEIGHT
    base = CHAR_TABLE_CNT + \
           min(char, CHAR_UPP_TABLE_CNT) * CHAR_UPP_HEIGHT + \
           max(char - CHAR_UPP_TABLE_CNT, 0) * CHAR_LOW_HEIGHT
    return base, height


def dump(data, out_img):
    mask = [0] * CHAR_TABLE_CNT
    avail = list(range(0x15, 0x40)) + list(range(0x41, 0x60)) + \
            list(range(0x61, 0x80))
    for char in avail:
        mask[char] = 1

    base = CHAR_TABLE_CNT

    for char in range(0, CHAR_TABLE_CNT):
        width = data[char]
        is_upper = char < CHAR_UPP_TABLE_CNT
        height = CHAR_UPP_HEIGHT if is_upper else CHAR_LOW_HEIGHT
        color = col_red if mask[char] == 0 else col_green

        # Draw cell
        for y in range(0, height):
            row = data[base + y]

            # Draw row (background)
            for x in range(0, min(width, 8)):
                pos_x = (char % DUMP_WIDTH) * \
                        DUMP_ROW_WIDTH + x
                pos_y = (char // DUMP_HEIGHT) * \
                        DUMP_ROW_HEIGHT + y
                out_img.putpixel((pos_x, pos_y), color)

            # Draw row (character)
            for x in range(0, 8):
                if (row & 0b10000000):
                    pos_x = (char % DUMP_WIDTH) * \
                            DUMP_ROW_WIDTH + x
                    pos_y = (char // DUMP_HEIGHT) * \
                            DUMP_ROW_HEIGHT + y
                    out_img.putpixel((pos_x, pos_y), col_black)
                row = row << 1

        base += height


def dump_single(data, char):
    width = min(data[char], 8)
    base, height = char_base(char)

    img = Image.new('RGB', (max(width, 1), height), 'white')
    for y in range(0, height):
        row = data[base + y]
        for x in range(0, width):
            if row & (0b10000000 >> x):
                img.putpixel((x, y), col_black)

    return img


def load(data, img, pos, out_file):
    # RGBA pixels never equal the RGB col_black tuple below, which would
    # silently read every pixel as non-black and load a blank glyph.
    if img.mode != 'RGB':
        img = img.convert('RGB')

    if img.width == 0 or img.width > 8:
        sys.stderr.write("Wrong image width\n")
        return 1
    if pos < 0 or pos >= CHAR_TABLE_CNT:
        sys.stderr.write("Wrong character position (out of table)\n")
        return 1

    if pos < CHAR_UPP_TABLE_CNT and img.height != CHAR_UPP_HEIGHT:
        sys.stderr.write("Wrong image height (must be exactly "
                         + CHAR_UPP_HEIGHT + ")\n")
        return 1
    elif pos >= CHAR_UPP_TABLE_CNT and img.height != CHAR_LOW_HEIGHT:
        sys.stderr.write("Wrong image height (must be exactly "
                         + CHAR_LOW_HEIGHT + ")\n")
        return 1

    data_width = []
    data_width.append(img.width)

    # Write char width
    out_file.write(data[:pos] + bytes(data_width) + data[pos + 1:CHAR_TABLE_CNT])

    base, char_height = char_base(pos)

    data_font = []

    # Encode character
    for y in range(0, char_height):
        row = data[base + y]
        byte = 0
        for x in range(0, img.width):
            # print(f"{x},{y}")
            if img.getpixel((x, y)) == col_black:
                print("#", end="")
                byte = byte | (1 << (7 - x))
            else:
                print(" ",end="")
        print("")
        data_font.append(byte)

    # Write encoded chars
    out_file.write(data[CHAR_TABLE_CNT:base] + bytes(data_font) + \
                   data[base + char_height:])


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('DUNECHAR', help='file extracted from DUNECHAR.HSQ (stdin default)', nargs='?')
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--dump', help='dump DUNECHAR as image and print it to output (with --position, dumps only that one glyph instead of the whole table)', action='store_true')
    group.add_argument('--load', help='load character raster image into new DUNECHAR file')
    parser.add_argument('--position', help='character position in DUNECHAR (0-255): required target for --load (default 20), optional single-glyph selector for --dump (whole table if omitted)', type=int,
                        default=None)
    parser.add_argument('--output', help='specify output file (stdout default)')
    args = parser.parse_args()

    # Input file
    if args.DUNECHAR:
        in_file = open(args.DUNECHAR, 'rb')
    else:
        in_file = sys.stdin.buffer
    data = in_file.read()
    if args.DUNECHAR:
        in_file.close()
    if type(data) != bytes:
        sys.stderr.write("Wrong data type\n")
        sys.exit(1)
    # if len(data) != FILE_TOTAL_SIZE:
    #     sys.stderr.write("Wrong file size of DUNECHAR")
    #     sys.exit(1)

    # Output file
    if args.output:
        out_file = open(args.output, 'wb')
    else:
        out_file = sys.stdout.buffer

    if args.dump:
        if args.position is not None:
            if args.position < 0 or args.position >= CHAR_TABLE_CNT:
                sys.stderr.write("Wrong character position (out of table)\n")
                sys.exit(1)
            img = dump_single(data, args.position)
        else:
            img = Image.new('RGB', (DUMP_WIDTH * DUMP_ROW_WIDTH,
                                    DUMP_HEIGHT * DUMP_ROW_HEIGHT), 'white')
            dump(data, img)
        img.save(out_file, 'PNG')
    if args.load:
        img = Image.open(args.load)
        pos = args.position if args.position is not None else 20
        load(data, img, pos, out_file)

    if args.output:
        out_file.close()
