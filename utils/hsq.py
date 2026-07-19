#!/usr/bin/env python3

"""
hsq.py - HSQ compression and decompression util (pure-Python port of utils/hsq.c).

Produces output in the exact bitstream format the original Cryo/DOS engine
(and the C reference decompressor) expects: a 6-byte header (checksum'd
decompressed/compressed sizes) followed by 16-bit flag words, each governing
16 following tokens that are either a raw literal byte or a back-reference
into a small (<=256 byte) or big (<=8192 byte) sliding window.

decompress_bytes() mirrors utils/hsq.c's decompress() line-for-line.
compress_bytes() follows the same greedy longest-match-first strategy as
compress()/compress_chunk()/save_lookh_buf() in utils/hsq.c, with one
deliberate deviation: the C reference passes its output-buffer pointer as
the window's lower bound in a couple of internal calls (compress_chunk ->
save_lookh_buf), which only "works" by accident of stack layout since C
pointers from unrelated arrays aren't ordering-comparable per the standard.
That bound is reimplemented here using the actual input-stream lower bound,
which is what the algorithm is clearly meant to do. This only affects which
particular match a tie is broken toward -- any match search_window() returns
still yields a spec-valid HSQ bitstream, and validity (not bit-identical
output to the C binary) is what the game's own decompressor requires.

Mirrors the CLI of the compiled utils/hsq binary:
    hsq.py -c|-d [-o FILE] [FILE]
"""

import argparse
import sys
from pathlib import Path

WINDOW_SMALL = 0x100
WINDOW_BIG = 0x2000
FILE_CHUNK = 0x6000
HSQ_CHECKSUM = 0xAB
HSQ_MAX_SIZE = 0x10000

USAGE = (
    "usage: [OPTION]... [FILE]...\n"
    "If FILE is not specified, then standard input will be used\n\n"
    " -v, --verbose\tverbose mode\n"
    " -c, --compress\tcompress input file and write to the output\n"
    " -d, --decompress\tdecompress input file and write to the output\n"
    " -o, --out FILE\tsave output to FILE instead to stdout\n"
    " -h, --help\tprint this help\n"
)


class BitReader:
    def __init__(self, buf, pos):
        self.buf = buf
        self.pos = pos
        self.flag = 0
        self.flag_size = 0

    def get_bit(self):
        if self.flag_size == 0:
            self.flag = self.buf[self.pos] | (self.buf[self.pos + 1] << 8)
            self.pos += 2
            self.flag_size = 16
        bit = self.flag & 1
        self.flag >>= 1
        self.flag_size -= 1
        return bit

    def get_raw(self):
        b = self.buf[self.pos]
        self.pos += 1
        return b


class BitWriter:
    def __init__(self):
        self.out = bytearray()
        self.flag = 0
        self.flag_size = 0
        self.buffer = bytearray()

    def put_bit(self, bit):
        """Returns True iff this call triggered a flush to self.out."""
        flushed = False
        if self.flag_size == 16:
            self.out += self.flag.to_bytes(2, "little")
            self.out += self.buffer
            self.flag = 0
            self.buffer = bytearray()
            self.flag_size = 0
            flushed = True
        self.flag = (self.flag >> 1) | ((bit & 1) << 15)
        self.flag_size += 1
        return flushed

    def put_raw(self, byte):
        self.buffer.append(byte & 0xFF)


def decompress_bytes(data):
    if len(data) < 6:
        raise ValueError("HSQ file truncated (missing header)")

    header = data[:6]
    out_len = header[0] | (header[1] << 8) | (header[2] << 16)
    enc_len = header[3] | (header[4] << 8)
    if enc_len > HSQ_MAX_SIZE:
        raise ValueError("invalid HSQ header (encoded size too large)")
    if (sum(header) & 0xFF) != HSQ_CHECKSUM:
        raise ValueError("invalid HSQ header checksum")
    if len(data) < enc_len:
        raise ValueError("HSQ file truncated (missing data)")

    dec_len = out_len
    r = BitReader(data, 6)
    out_buf = bytearray(dec_len)
    written = 0
    done = False

    while not done:
        bit = r.get_bit()

        if bit == 1:
            if written == dec_len:
                break
            out_buf[written] = r.get_raw()
            written += 1
        else:
            bit2 = r.get_bit()

            if bit2 == 0:
                b1 = r.get_bit()
                b2 = r.get_bit()
                lookahead_size = ((b1 << 1) | b2) + 2
                byte = r.get_raw()
                offset = (WINDOW_SMALL - byte) & 0xFFFF
            else:
                byte1 = r.get_raw()
                lookahead_size = byte1 & 7
                word = byte1
                byte2 = r.get_raw()
                word = ((byte2 << 8) | word) & 0xFFFF
                offset = (WINDOW_BIG - (word >> 3)) & 0xFFFF

                if lookahead_size == 0:
                    lookahead_size = r.get_raw()
                if lookahead_size == 0:
                    done = True
                else:
                    lookahead_size += 2

            for _ in range(lookahead_size):
                if written == dec_len:
                    break
                out_buf[written] = out_buf[written - offset]
                written += 1

    if not done:
        print("Warning: wrong encoded size in HSQ header", file=sys.stderr)
    elif dec_len != written:
        print("Warning: wrong decoded size in HSQ header", file=sys.stderr)

    return bytes(out_buf[:written])


def compress_pattern(bw, buf, lookahead_bgn, lookahead_size, match):
    if lookahead_size == 1:
        bw.put_bit(1)
        bw.put_raw(buf[lookahead_bgn])
        return

    bw.put_bit(0)
    if match is None:
        raise RuntimeError("internal error: expected a match for a multi-byte pattern")
    offset = lookahead_bgn - match

    if lookahead_size <= 5 and offset <= WINDOW_SMALL:
        bw.put_bit(0)
        bw.put_bit(((lookahead_size - 2) >> 1) & 1)
        bw.put_bit((lookahead_size - 2) & 1)
        bw.put_raw((WINDOW_SMALL - offset) & 0xFF)
    elif lookahead_size <= 257:
        bw.put_bit(1)
        word = (lookahead_size - 2) & 7 if lookahead_size <= 9 else 0
        word = (((WINDOW_BIG - offset) << 3) | word) & 0xFFFF
        bw.put_raw(word & 0xFF)
        bw.put_raw((word >> 8) & 0xFF)
        if lookahead_size > 9:
            bw.put_raw((lookahead_size - 2) & 0xFF)


def search_window(buf, istream_idx, lookahead_bgn, lookahead_end, lookahead_size, window_size):
    """Rightmost (nearest) occurrence of buf[lookahead_bgn:lookahead_bgn+lookahead_size]
    within the window [max(istream_idx, lookahead_bgn-window_size), lookahead_end-1)."""
    search_bgn = max(istream_idx, lookahead_bgn - window_size)
    length = lookahead_end - search_bgn - 1
    if lookahead_size == 0 or length < lookahead_size:
        return None
    pattern = bytes(buf[lookahead_bgn:lookahead_bgn + lookahead_size])
    idx = buf.rfind(pattern, search_bgn, search_bgn + length)
    return idx if idx != -1 else None


def save_lookh_buf(bw, buf, istream_idx, lookh):
    match = search_window(buf, istream_idx, lookh["bgn"], lookh["end"] - 1,
                           lookh["size"] - 1, WINDOW_BIG)
    offset = (lookh["bgn"] - match) if match is not None else 0

    if lookh["size"] > 3 or (lookh["size"] == 3 and offset <= WINDOW_SMALL):
        compress_pattern(bw, buf, lookh["bgn"], lookh["size"] - 1, match)
        lookh["bgn"] += lookh["size"] - 1
        lookh["end"] -= 1
    else:
        compress_pattern(bw, buf, lookh["bgn"], 1, match)
        lookh["bgn"] += 1
        lookh["end"] -= lookh["size"] - 1


def compress_chunk(bw, buf, istream_idx, istream_pos, istream_len):
    lookh = {"size": 0, "bgn": istream_pos, "end": istream_pos}
    match = None

    while lookh["end"] - istream_idx < istream_len:
        lookh["end"] += 1
        lookh["size"] += 1

        match = search_window(buf, istream_idx, lookh["bgn"], lookh["end"],
                               lookh["size"], WINDOW_BIG)
        if match is None or lookh["size"] > 257:
            save_lookh_buf(bw, buf, istream_idx, lookh)
            lookh["size"] = 0

    if lookh["size"] != 0:
        save_lookh_buf(bw, buf, istream_idx, lookh)
        lookh["size"] = 0

    while lookh["end"] - istream_idx < istream_len:
        compress_pattern(bw, buf, lookh["end"], 1, match)
        lookh["end"] += 1


def compress_bytes(data):
    bw = BitWriter()
    chunk_buf = bytearray(FILE_CHUNK + WINDOW_BIG)
    inlen = 0
    offset = WINDOW_BIG
    pos = 0

    while True:
        chunk = data[pos:pos + FILE_CHUNK]
        chunk_len = len(chunk)
        pos += chunk_len
        chunk_buf[WINDOW_BIG:WINDOW_BIG + chunk_len] = chunk
        inlen += chunk_len

        compress_chunk(bw, chunk_buf, offset, WINDOW_BIG, chunk_len + WINDOW_BIG - offset)

        chunk_buf[0:WINDOW_BIG] = chunk_buf[FILE_CHUNK:FILE_CHUNK + WINDOW_BIG]
        offset = 0

        if chunk_len != FILE_CHUNK:
            break

    # End-of-stream marker: a big-window pointer with lookahead_size == 0.
    bw.put_bit(0)
    bw.put_bit(1)
    bw.put_raw(0)
    bw.put_raw(0)
    bw.put_raw(0)

    while not bw.put_bit(0):
        pass

    out = bw.out
    total_len = 6 + len(out)
    header = bytearray(6)
    header[0] = inlen & 0xFF
    header[1] = (inlen >> 8) & 0xFF
    header[2] = (inlen >> 16) & 0xFF
    header[3] = total_len & 0xFF
    header[4] = (total_len >> 8) & 0xFF
    header[5] = (HSQ_CHECKSUM - header[0] - header[1] - header[2] - header[3] - header[4]) & 0xFF

    return bytes(header) + bytes(out)


def main():
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("-v", "--verbose", action="store_true")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("-c", "--compress", action="store_true")
    group.add_argument("-d", "--decompress", action="store_true")
    parser.add_argument("-o", "--out", dest="out")
    parser.add_argument("-h", "--help", action="store_true")
    parser.add_argument("file", nargs="?")
    args = parser.parse_args()

    if args.help or (args.compress + args.decompress != 1):
        print(USAGE)
        return 0

    try:
        data = Path(args.file).read_bytes() if args.file else sys.stdin.buffer.read()
    except OSError as e:
        print(f"Opening file '{args.file}' failed: {e.strerror}", file=sys.stderr)
        return 1

    if args.compress:
        out_data = compress_bytes(data)
    else:
        try:
            out_data = decompress_bytes(data)
        except ValueError as e:
            print(f"Decompression failed: {e}", file=sys.stderr)
            return 1

    if args.out:
        Path(args.out).write_bytes(out_data)
    else:
        sys.stdout.buffer.write(out_data)

    return 0


if __name__ == "__main__":
    sys.exit(main())
