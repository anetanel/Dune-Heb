#!/usr/bin/env python3

"""
bigs_sprite.py - decode/encode for the "bigs" sprite/graphics container format
found inside decompressed picture-resource .HSQ files (e.g. INTDS.HSQ,
CRYO.HSQ, SHAI.HSQ, FRM1.HSQ). This is distinct from the HNM video format
(LOGO.HNM) and from the plain single-blob .HSQ files (phrase/command/font
tables) -- it's what DUNEPRG.EXE uses specifically for the intro montage's
full-screen pictures and text-overlay assets.

Format, reverse-engineered from sites.google.com/site/duneeditor/bigs_fr-mirror's
description and cross-verified byte-for-byte against dump.c (the reference
decoder linked from that same page, compiled locally and run against real
.HSQ files from this game -- see patch_intro_title.py for how that
verification loop works):

    offset_A (u16)          byte offset, from the start of this (decompressed)
                            buffer, where the palette section ends and the
                            sprite offset table begins. offset_A == 2 means
                            "no palette here" -- this picture relies on
                            whatever palette an earlier-drawn picture already
                            loaded into the VGA DAC, rather than defining its
                            own.
    [palette section]       present only if offset_A != 2. Zero or more
                            blocks of (start: u8, count: u8, RGB * count
                            6-bit-per-channel triples), terminated by a
                            start=count=0xFF sentinel.
    [sprite offset table]   n * u16, each relative to offset_A itself (i.e.
                            add offset_A to get an absolute buffer offset).
                            n is never stored explicitly -- the first table
                            entry always equals n*2 (the table's own byte
                            length), so n = first_entry // 2.
    [sprite data blocks]    n of these, one per offset table entry:
        width_lo (u8)
        compression_byte (u8)  bit7 = compressed flag; bits0-6 = width's
                                high 7 bits (i.e. read the pair as a 16-bit
                                little-endian word and mask bit15/bits0-14)
        height (u8)
        palbase (u8)            add to each 4-bit pixel nibble to get the
                                real palette index: nibble 0-15 -> palbase
                                .. palbase+15
        pixel data              4bpp packed, 2 pixels/byte (low nibble
                                first). If compressed, row-by-row RLE: a
                                signed repeat-indicator byte R, then either
                                one byte repeated (-R)+1 times (R negative)
                                or (R)+1 literal bytes (R non-negative).

All widths this pipeline deals with are 320 (the full VGA mode-13h width),
which is always a multiple of 4 -- so the row-realignment padding some
implementations insert between rows never actually triggers here, and this
module doesn't implement it. Don't reuse encode_sprite() for a width that
isn't a multiple of 4 without adding that padding back.
"""

import struct


def parse_sprites(dec):
    """Parse a decompressed picture-resource buffer.

    Returns (offset_A, palette, sprites) where:
      - palette is a dict of {index: (r, g, b)} (0-255 scale), only entries
        actually defined by a palette block (empty if offset_A == 2).
      - sprites is a list of dicts, one per sprite, each with:
          pos        absolute byte offset of this sprite's pixel data
                     (i.e. right after its 4-byte header)
          width, height, compressed, palbase
    """
    offset_A = struct.unpack_from("<H", dec, 0)[0]
    palette = {}
    if offset_A != 2:
        pos = 2
        while True:
            start, count = dec[pos], dec[pos + 1]
            pos += 2
            if start == 0xFF and count == 0xFF:
                break
            n = 256 if count == 0 else count
            for i in range(n):
                r, g, b = dec[pos + i * 3], dec[pos + 1 + i * 3], dec[pos + 2 + i * 3]
                palette[(start + i) & 0xFF] = (r * 4, g * 4, b * 4)
            pos += n * 3

    base = offset_A
    first_off = struct.unpack_from("<H", dec, base)[0]
    n = first_off // 2
    offsets = [struct.unpack_from("<H", dec, base + i * 2)[0] for i in range(n)]

    sprites = []
    for off in offsets:
        p = base + off
        size_x_read, compression, size_y, palbase = dec[p], dec[p + 1], dec[p + 2], dec[p + 3]
        width = size_x_read + ((compression & 0x7F) << 8)
        compressed = bool(compression & 0x80)
        sprites.append({
            "pos": p + 4,
            "width": width,
            "height": size_y,
            "compressed": compressed,
            "palbase": palbase,
        })
    return offset_A, palette, sprites


def decode_sprite_pixels(dec, sprite):
    """Decode one sprite's pixel data into a list-of-rows nibble grid
    (each cell 0-15; add sprite['palbase'] for the real palette index).
    Faithful port of dump.c's per-row RLE/4bpp decode loop.
    """
    w, h = sprite["width"], sprite["height"]
    pos = sprite["pos"]
    grid = [[0] * w for _ in range(h)]
    y = x = 0

    def put(nib):
        nonlocal x, y
        if x < w:
            grid[y][x] = nib
        x += 1

    if sprite["compressed"]:
        while True:
            rep = dec[pos]; pos += 1
            rep_signed = rep - 256 if rep >= 128 else rep
            if rep_signed < 0:
                b = dec[pos]; pos += 1
                for _ in range(-rep_signed + 1):
                    put(b & 0x0F)
                    put(b >> 4)
            else:
                for _ in range(rep_signed + 1):
                    b = dec[pos]; pos += 1
                    put(b & 0x0F)
                    put(b >> 4)
            if x >= w:
                x = 0
                y += 1
            if y >= h:
                break
    else:
        while True:
            b1 = dec[pos]; pos += 1
            b2 = dec[pos]; pos += 1
            put(b1 & 0x0F); put(b1 >> 4)
            put(b2 & 0x0F); put(b2 >> 4)
            if x >= w:
                x = 0
                y += 1
            if y >= h:
                break
    return grid


def _pack_row(nibbles):
    """RLE-encode one row of nibbles (width must be a multiple of 2)."""
    assert len(nibbles) % 2 == 0
    row_bytes = bytearray()
    for i in range(0, len(nibbles), 2):
        row_bytes.append((nibbles[i] & 0x0F) | ((nibbles[i + 1] & 0x0F) << 4))

    out = bytearray()
    i, n = 0, len(row_bytes)
    while i < n:
        j = i + 1
        while j < n and row_bytes[j] == row_bytes[i] and (j - i) < 129:
            j += 1
        run_len = j - i
        if run_len >= 2:
            out.append((-(run_len - 1)) & 0xFF)
            out.append(row_bytes[i])
            i = j
        else:
            k = i
            lit = bytearray()
            while k < n and len(lit) < 128:
                m = k + 1
                while m < n and row_bytes[m] == row_bytes[k] and (m - k) < 129:
                    m += 1
                if (m - k) >= 2:
                    break
                lit.append(row_bytes[k])
                k += 1
            out.append((len(lit) - 1) & 0xFF)
            out.extend(lit)
            i = k
    return bytes(out)


def encode_sprite(nibble_grid, width, height, palbase, compressed=True):
    """Encode a nibble grid (list of `height` rows, each `width` nibbles
    0-15) into a full sprite block: 4-byte header + pixel data. `width`
    must be a multiple of 4 (see module docstring re: row padding).
    """
    assert width % 4 == 0, "width must be a multiple of 4 (no row-padding support)"
    if compressed:
        body = b"".join(_pack_row(row) for row in nibble_grid)
    else:
        body = bytearray()
        for row in nibble_grid:
            for i in range(0, width, 2):
                body.append((row[i] & 0x0F) | ((row[i + 1] & 0x0F) << 4))
        body = bytes(body)

    width_hi = (width >> 8) & 0x7F
    width_lo = width & 0xFF
    compression_byte = width_hi | (0x80 if compressed else 0)
    header = bytes([width_lo, compression_byte, height & 0xFF, palbase & 0xFF])
    return header + body


def splice_sprite(dec, sprite_index, new_block):
    """Replace sprite `sprite_index`'s data block in a decompressed
    picture-resource buffer with `new_block` (as produced by
    encode_sprite()), adjusting the sprite offset table for every sprite
    that comes after it. Returns the new decompressed buffer.
    """
    offset_A, _palette, sprites = parse_sprites(dec)
    base = offset_A
    n = len(sprites)
    first_off = struct.unpack_from("<H", dec, base)[0]
    offsets = [struct.unpack_from("<H", dec, base + i * 2)[0] for i in range(n)]

    old_start = sprites[sprite_index]["pos"] - 4
    old_end = base + offsets[sprite_index + 1] if sprite_index + 1 < n else len(dec)
    delta = len(new_block) - (old_end - old_start)

    new_offsets = list(offsets)
    for i in range(sprite_index + 1, n):
        new_offsets[i] += delta

    header_and_palette = dec[:base]
    new_offset_table = b"".join(struct.pack("<H", v) for v in new_offsets)
    before = dec[base + n * 2:old_start]
    after = dec[old_end:]
    return header_and_palette + new_offset_table + before + new_block + after
