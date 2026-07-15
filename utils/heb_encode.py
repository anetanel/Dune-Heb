#!/usr/bin/env python3

"""
heb_encode.py - build PHRASE11_HEB.TXT by combining PHRASE11.HEB (Hebrew
translation, UTF-8, with ASCII placeholder tokens standing in for special
control bytes) with PHRASE11.TXT (original English, single-byte encoding,
containing the real control bytes) into a single-byte-per-character file:
Hebrew letters are mapped to their DUNECHAR font-table byte values and the
placeholder tokens are replaced back with the original control bytes.
"""

import re
import sys

TXT_PATH = "tmp/PHRASE11.TXT"
HEB_PATH = "translations/PHRASE11.HEB"
OUT_PATH = "tmp/PHRASE11_HEB.TXT"

HEB_LETTER_TO_BYTE = {
    "א": 65, "ב": 66, "ג": 67, "ד": 68, "ה": 69, "ו": 70, "ז": 71, "ח": 72,
    "ט": 73, "י": 74, "כ": 75, "ל": 76, "מ": 77, "נ": 78, "ס": 79, "ע": 80,
    "פ": 81, "צ": 82, "ק": 83, "ר": 84, "ש": 85, "ת": 86, "ך": 87, "ם": 88,
    "ן": 89, "ף": 90, "ץ": 91,
    "״": ord('"'),  # gershayim, used like quotation marks
    "׳": ord("'"),  # geresh, used like an apostrophe
}

# Ordered by descending token length so combos (MFF, MH) are matched before
# their prefixes (M, FF, H) when scanning the HEB placeholder stream. The
# "[latin1 special]mr" alternative handles the one known reversed-order token
# (line 228, a bidi copy/paste artifact: "<char>0mr" instead of "mr<char>0")
# — restricted to the specific Latin-1 superscript/fraction chars used by
# spice-quantity tokens, so it can't swallow an unrelated Hebrew word ending
# right before a normal "mr<char>" token (e.g. "...ב" + "mr 0").
LATIN1_QTY_CHARS = "®°²´¶¸º¼"

# Single-byte variables 0x81-0x8C map sequentially to placeholders ma..ml
# (ma=0x81 ... mk=0x8B, ml=0x8C). Byte value <-> letter is positional per
# file, not a shared global meaning: e.g. PHRASE11's "mf" (0x86, smuggler
# region) and PHRASE12's "mf" (0x86, spice-skill level) share a byte value
# by coincidence only, not because they mean the same thing.
SINGLE_BYTE_VARS = "abcdefghijkl"  # ma, mb, mc, ..., ml
SINGLE_BYTE_BASE = 0x81

TOKEN_RE = re.compile(
    r"MFF|MH|FE|FF|H|M"
    r"|mqm\]"  # PHRASE12 special case: \x91\x9d, no clean latin-1 char
    r"|m[" + SINGLE_BYTE_VARS + r"]"
    r"|mq."
    r"|m@@."
    r"|mr."
    r"|[" + LATIN1_QTY_CHARS + r"]0mr"
    , re.DOTALL
)


def normalize_placeholder(matched):
    """Reorder a reversed '<char>0mr' match back to canonical 'mr<char>' form."""
    if matched.endswith("mr") and len(matched) == 4:
        return "mr" + matched[0]
    return matched


def placeholders_equal(expected, matched):
    """Compare a TXT-derived placeholder against the matched HEB text.

    Case-insensitive specifically for 'mr<letter>' tokens, since the HEB
    source has an inconsistent-case token (lowercase "mrh" vs. the correct
    uppercase "mrH" used elsewhere for the same byte, e.g. PHRASE12 lines
    286-288 vs. 289).
    """
    if expected == matched:
        return True
    if len(expected) == 3 and expected.startswith("mr") and expected[2].isalpha():
        return expected.lower() == matched.lower()
    return False


def tokenize_txt(line):
    """Scan a raw TXT line and return an ordered list of (placeholder, raw_bytes)."""
    tokens = []
    i = 0
    n = len(line)
    while i < n:
        b = line[i]
        if line[i:i + 2] == b"\r\x06":
            tokens.append(("MFF", line[i:i + 2]))
            i += 2
        elif line[i:i + 2] == b"\r\x08":
            tokens.append(("MH", line[i:i + 2]))
            i += 2
        elif b == 0xFE:
            tokens.append(("FE", line[i:i + 1]))
            i += 1
        elif b == 0x0D:
            tokens.append(("M", line[i:i + 1]))
            i += 1
        elif b == 0x06:
            tokens.append(("FF", line[i:i + 1]))
            i += 1
        elif b == 0x08:
            tokens.append(("H", line[i:i + 1]))
            i += 1
        elif SINGLE_BYTE_BASE <= b < SINGLE_BYTE_BASE + len(SINGLE_BYTE_VARS):
            letter = SINGLE_BYTE_VARS[b - SINGLE_BYTE_BASE]
            tokens.append(("m" + letter, line[i:i + 1]))
            i += 1
        elif b == 0x80 and line[i + 1:i + 2] == b"\x00":
            byte3 = line[i + 2]
            letter = chr(ord("a") + byte3 - 1)
            tokens.append(("m@@" + letter, line[i:i + 3]))
            i += 3
        elif b == 0x91 and line[i + 1:i + 2] == b"\x9d":
            # PHRASE12 special case: byte2 0x9D has no clean latin-1 char,
            # translator used the arbitrary mnemonic "mqm]" instead.
            tokens.append(("mqm]", line[i:i + 2]))
            i += 2
        elif b == 0x91:
            byte2 = line[i + 1]
            placeholder_char = " " if byte2 == 0xA0 else bytes([byte2]).decode("latin-1")
            tokens.append(("mq" + placeholder_char, line[i:i + 2]))
            i += 2
        elif b == 0x92:
            byte2 = line[i + 1]
            placeholder_char = " " if byte2 == 0xA0 else bytes([byte2]).decode("latin-1")
            tokens.append(("mr" + placeholder_char, line[i:i + 2]))
            i += 2
        elif b < 0x20 and b != 0x09:
            raise ValueError(f"Unhandled control byte {b:#x} in line: {line!r}")
        elif b >= 0x80:
            raise ValueError(f"Unhandled high byte {b:#x} in line: {line!r}")
        else:
            i += 1
    return tokens


def char_to_byte(ch):
    if ch in HEB_LETTER_TO_BYTE:
        return HEB_LETTER_TO_BYTE[ch]
    code = ord(ch)
    if code > 0xFF:
        raise ValueError(f"Unmapped non-Hebrew, non-Latin1 character {ch!r} ({code:#x})")
    return code


def text_to_bytes(text):
    return bytes(char_to_byte(ch) for ch in text)


# Bare control-marker tokens (M=\r, FF=\x06, H=\x08, FE=\xfe) the translator
# is allowed to add anywhere in a line beyond what the English source has --
# these represent deliberate formatting choices (e.g. an extra line break
# for Hebrew readability) and are inserted as their literal control byte
# whenever there's no matching TXT token left to consume at that position.
BARE_MARKER_BYTES = {"M": b"\r", "FF": b"\x06", "H": b"\x08", "FE": b"\xfe"}


def encode_line(txt_line, heb_line_str):
    tokens = tokenize_txt(txt_line)
    next_token = 0  # index into tokens of the next one to consume

    out = bytearray()
    pos = 0
    for m in TOKEN_RE.finditer(heb_line_str):
        out += text_to_bytes(heb_line_str[pos:m.start()])
        raw_match = m.group(0)
        matched = normalize_placeholder(raw_match)

        if next_token < len(tokens) and placeholders_equal(tokens[next_token][0], matched):
            placeholder, raw_bytes = tokens[next_token]
            next_token += 1
            out += raw_bytes
            if raw_match.endswith("mr") and len(raw_match) == 4:
                out += b"0"  # reversed-order token: keep the digit it carried
        elif matched in BARE_MARKER_BYTES:
            out += BARE_MARKER_BYTES[matched]
        else:
            expected = tokens[next_token][0] if next_token < len(tokens) else None
            raise ValueError(
                f"Placeholder mismatch: HEB has {matched!r} but TXT expects "
                f"{expected!r} (line={heb_line_str!r})"
            )
        pos = m.end()
    out += text_to_bytes(heb_line_str[pos:])

    remaining = tokens[next_token:]
    # A translated line sometimes drops the single trailing "M" marker for a
    # bare \r at the very end of the TXT line (a speaker-label line-break the
    # translator omitted when typing). Safe to auto-append only in that exact
    # trailing position -- never inferred anywhere else in the line.
    if remaining == [("M", b"\r")]:
        out += b"\r"
        remaining = []
    if remaining:
        raise ValueError(f"TXT had unconsumed tokens {remaining} for line: {heb_line_str!r}")

    return bytes(out)


# Known source-file corruptions (transcription slips in the HEB sheet):
# these are substituted on sight so the pipeline can run before the sheet
# is corrected; remove each entry once the corresponding source line is fixed.
KNOWN_CORRUPTIONS = {
    # PHRASE12.HEB line 27: a caret-notation hex-viewer artifact pasted in
    # place of a "m@@<letter>-m@@<letter>" location-code pair.
    "M-^@^@^E-M-^@^@^O": "m@@e-m@@o",
    # PHRASE12.HEB line 148: trailing letter dropped from an "mq<letter>"
    # troop-count token, leaving "mq " (mq + space) instead of "mqa".
    "mq גייסות": "mqa גייסות",
}


def encode_file(txt_path, heb_path):
    """Return (output_bytes, errors) for the given TXT/HEB source file pair."""
    with open(txt_path, "rb") as f:
        txt_lines = f.read().split(b"\n")
    with open(heb_path, "rb") as f:
        heb_lines = f.read().split(b"\n")

    if len(txt_lines) != len(heb_lines):
        raise SystemExit(f"Line count mismatch: TXT={len(txt_lines)} HEB={len(heb_lines)}")

    out_lines = []
    errors = []
    for lineno, (txt_line, heb_line) in enumerate(zip(txt_lines, heb_lines), start=1):
        if not heb_line and not txt_line:
            out_lines.append(b"")
            continue
        if not heb_line or heb_line == txt_line:
            # Untranslated line (blank HEB) or an unmodified debug/dev-menu
            # string (HEB byte-identical to TXT): keep the original bytes
            # as-is rather than running placeholder/letter substitution.
            out_lines.append(txt_line)
            continue
        try:
            heb_str = heb_line.decode("utf-8")
            for corrupted, fixed in KNOWN_CORRUPTIONS.items():
                if corrupted in heb_str:
                    print(f"line {lineno}: substituting known corrupted placeholder", file=sys.stderr)
                    heb_str = heb_str.replace(corrupted, fixed)
            out_lines.append(encode_line(txt_line, heb_str))
        except Exception as e:
            errors.append(f"line {lineno}: {e}")
            out_lines.append(heb_line)

    return b"\n".join(out_lines), errors


def main():
    output, errors = encode_file(TXT_PATH, HEB_PATH)

    if errors:
        for e in errors:
            print(e, file=sys.stderr)
        sys.exit(f"{len(errors)} line(s) failed to encode; aborting without writing output.")

    with open(OUT_PATH, "wb") as f:
        f.write(output)

    print(f"Wrote {OUT_PATH} ({len(output)} bytes)")


if __name__ == "__main__":
    main()
