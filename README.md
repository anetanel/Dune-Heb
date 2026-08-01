# Dune (1992) Hebrew Translation

Tooling to build a Hebrew-translated version of the 1992 DOS game *Dune*
(Cryo Interactive / Virgin Games) from a copy of the original game files.

This repo ships no original game assets. You provide your own legally
obtained copy of the game; the scripts here patch it with a Hebrew font and
translated text.

## Credits

The core file-format tooling — `utils/hsq.py` (HSQ compression), `utils/tu.py`
(phrase-binary packing), and `utils/font.py` (font-table editing) — is based
on [sonicpp/Dune-game-translations](https://github.com/sonicpp/Dune-game-translations),
adapted here for the Hebrew translation pipeline. Many thanks to that
project for reverse-engineering these formats. `hsq.py`/`tu.py` are a
pure-Python port of that project's `hsq.c`/`tu.c` (kept in `utils/` for
reference), so the pipeline needs no C compiler.

Several in-game engine behaviors that aren't visible anywhere in this
repo's tooling — notably how the save/load screen injects the live day
count and time-of-day into its "Log N: DAY D / TIME" labels — were
understood by reading [madmoose/dune-chani](https://github.com/madmoose/dune-chani),
an annotated disassembly of the game's CD build, and its rendered
listing at [thomas.fach-pedersen.net](https://thomas.fach-pedersen.net/dune/cryo-dune-3.7-cd-dncdprg.html).
Thanks to Thomas Fach-Pedersen for that work.

`utils/bigs_sprite.py`'s picture-resource sprite format (used by
`utils/patch_intro_title.py`) was reverse-engineered from the description
and reference `dump.c` decoder at [bigs.fr's Dune data file docs](https://www.bigs.fr/dune_old/),
mirrored at the [Dune Editor community wiki](https://sites.google.com/site/duneeditor/bigs_fr-mirror).

`fonts/AharoniCLM-Book.ttf` is "Aharoni CLM" from the [Culmus project](https://culmus.sourceforge.net/),
licensed under the GPLv2 (see `fonts/AharoniCLM-LICENSE`).

`fonts/PublicPixel.ttf` is "Public Pixel Font" by [GGBotNet](https://ggbot.itch.io/public-pixel-font),
licensed CC0 1.0 Universal (see `fonts/PublicPixel-LICENSE-CC0.txt`).

## Directory layout

| Directory | Contents | Committed? |
|---|---|---|
| `translations/` | Hebrew `.HEB` source files (pasted from the translation spreadsheet) | yes |
| `font_png/` | Hebrew glyph images loaded into the game's font table | yes |
| `generic_png/` | Hand-edited Hebrew letter/geresh glyph images for the outro's `GENERIC.HSQ` credit-name sprites — see "The outro credit screens" below | yes |
| `final_png/` | Hand-authored Hebrew artwork for `FINAL.HSQ`'s two outro title/caption sprites — see "The outro credit screens" below | yes |
| `fonts/` | Third-party font files used to render non-DUNECHAR graphics (the intro title card and, as a fallback when a letter has no hand-edited `generic_png/` file, the outro credit-name sprites) | yes |
| `assets/` | Other source images spliced into game graphics (currently just the intro credits' logo badge) | yes |
| `utils/` | All scripts and tools: `build_translation.py`, `translate_phrase.py`, `heb_encode.py`, `load_heb_font.sh`, plus `hsq.py` (compress/decompress), `tu.py` (pack/unpack phrase binaries), `font.py`, `split.py`, `bigs_sprite.py`, `verify_bigs_sprite_roundtrip.py`, `patch_intro_title.py`, `patch_intro_logo.py`, `patch_generic_letters.py`, `patch_final_credits.py`, `extract_sprites.py` | yes |
| `org_files/` | Unmodified original files, verified by checksum: the seven `.HSQ` files this pipeline replaces, plus `DUNEPRG.EXE` (patched in place rather than replaced, so it needs its own pristine reference too) | **no** (gitignored) |
| `game/` | Your copy of the full game install; also the final install target | **no** (gitignored) |
| `build/` | Final translated `.HSQ` outputs, ready to install | yes |
| `tmp/` | Intermediate working files, including each `<NAME>.TXT` (English reference text, regenerated on demand — see below) | **no** (gitignored) |

## Quick start

1. Copy your game install into `game/` (needs a full original install —
   `DUNEPRG.EXE` and every original `.HSQ` file this pipeline touches:
   `COMMAND1`, `DUNECHAR`, `PHRASE11`, `PHRASE12`, `INTDS`, `GENERIC`,
   `FINAL`, or their `.BAK` equivalents).
2. Run the full pipeline:
   ```
   ./utils/build_translation.py
   ```
   This verifies/repairs `org_files/` from `game/`, builds the Hebrew font
   (once — skipped on later runs), rebuilds all translated files, and
   installs everything into `game/`.
3. Launch the game from `game/` as usual.

Re-run `./utils/build_translation.py` any time a translation file under
`translations/` changes — the font step is skipped automatically once
`build/DUNECHAR.HSQ` exists. Force a font rebuild (e.g. after editing
`font_png/`) with `./utils/build_translation.py --rebuild-font`.

## Translating text

Each phrase/command file has three parts:

- `tmp/<NAME>.TXT` — the original English text, one line per in-game phrase.
  **Generated, not committed** — extracted automatically the first time it's
  needed by decompressing `org_files/<NAME>.HSQ` (`utils/hsq.py -d`) and
  unpacking it (`utils/tu.py -u`). Read-only reference; never edit it directly,
  and never commit it — if you need a fresh copy, just delete it from `tmp/`
  and re-run the pipeline.
- `translations/<NAME>.HEB` — the Hebrew translation, same line count and
  order as the extracted `.TXT` file. This is the file you edit (typically
  by pasting from a spreadsheet).
- Special in-game control codes (line breaks, player-name/location/quantity
  placeholders) appear in the English `.TXT` as raw control bytes and in the
  Hebrew `.HEB` as short ASCII tokens (`M`, `FE`, `mk`, `mq'`, `m@@b`, `mr¶`,
  etc.) at the matching position. Keep these tokens in the translation,
  moving them to wherever they belong in the Hebrew sentence — `heb_encode.py`
  restores them to real control bytes automatically.

Currently translated: `PHRASE11`, `PHRASE12`, `COMMAND1`.

To (re-)generate a single file's English reference text on its own:
```
python3 -c "import sys; sys.path.insert(0, 'utils'); import build_translation; build_translation.ensure_english_txt('PHRASE11')"
```

To rebuild a single translated file without touching the font or `game/`:
```
./utils/translate_phrase.py PHRASE11
./utils/translate_phrase.py COMMAND1 --no-split   # short UI-label files: no word-wrap
```
This extracts the matching `tmp/<NAME>.TXT` automatically if it isn't
already present. Output lands in `build/<NAME>.HSQ`; run `utils/build_translation.py`
afterward to install it into `game/`.

## How a build is assembled

```
org_files/<NAME>.HSQ
        │  utils/hsq.py -d, then utils/tu.py -u  (only if tmp/<NAME>.TXT is missing)
        ▼
tmp/<NAME>.TXT  +  translations/<NAME>.HEB
        │  heb_encode.py (Hebrew letters -> font bytes, tokens -> control bytes)
        ▼
tmp/<NAME>_HEB.TXT
        │  utils/split.py (word-wrap + reverse for the game's RTL renderer)
        │  or a plain reverse-only pass for --no-split files
        ▼
tmp/<NAME>_HEB_SPLIT.BIN
        │  utils/tu.py -p (pack lines into a phrase-table binary)
        ▼
tmp/<NAME>_HEB.BIN
        │  utils/hsq.py -c (compress)
        ▼
build/<NAME>.HSQ  ──install──▶  game/<NAME>.HSQ
```

The Hebrew font is assembled the same way, but from `org_files/DUNECHAR.HSQ`
and `font_png/*.png` via `utils/hsq.py -d`, `utils/font.py --load`, and
`utils/hsq.py -c`, landing at `build/DUNECHAR.HSQ`. As part of this step,
`build_translation.py` also renders `tmp/DUNECHAR_before.png` (glyph table
before any changes) and `tmp/DUNECHAR_after.png` (after loading the Hebrew
glyphs) so you can visually diff the two.

To render either glyph table PNG by hand from a `.BIN` font file:
```
./utils/font.py <input.bin> --dump --output out.png
```
The file to render comes from the positional argument, not from `--dump`
itself. For example, to render the original (unmodified) glyph table:
```
./utils/hsq.py -d org_files/DUNECHAR.HSQ -o /tmp/DUNECHAR.BIN
./utils/font.py /tmp/DUNECHAR.BIN --dump --output /tmp/before.png
```
Add `--position N` to dump just glyph `N` at its native size instead of the
whole table:
```
./utils/font.py /tmp/DUNECHAR.BIN --dump --position 173 --output /tmp/dash.png
```

## The intro title card

Unlike the dialogue/menu text, the boot intro's studio-credit and title
screens ("Virgin Games" / "presents" / "A production from" / "CRYO" /
"Interactive Entertainment Systems" / "DUNE") aren't drawn with the DUNECHAR
bitmap font at all — they're pre-rendered picture sprites baked into
`INTDS.HSQ` ("INTro Data Sequence"), one of `DUNEPRG.EXE`'s picture-resource
files (a distinct container format from both the plain single-blob `.HSQ`
files and the `LOGO.HNM` video — see `utils/bigs_sprite.py`'s docstring for
the full format writeup). `utils/patch_intro_title.py` regenerates just the
"DUNE" sprite as "חולית" (rendered from `fonts/AharoniCLM-Book.ttf`, chosen to
echo the bold block lettering on the actual Israeli retail release's box
art) and splices it back in, producing `build/INTDS.HSQ`. `utils/patch_intro_logo.py`
then chains off that same file, adding this translation's own
`assets/hebrew_adv_pixel.png` logo badge below the "Interactive Entertainment
Systems" credit line (sprite 10), growing that sprite's own bitmap downward
in place rather than touching its screen position. `build_translation.py`
runs both on every build; run `./utils/patch_intro_title.py` then
`./utils/patch_intro_logo.py` directly to regenerate just this file.

Both scripts keep `INTDS.HSQ`'s own decompressed size under a live-tested
ceiling (`patch_intro_logo.INTDS_DECOMPRESSED_SAFE_CEILING`) and refuse to
build past it: this file stays resident when the History book's
page-exhaustion `play_credits`/`CREDITS.HNM` easter egg loads (reading a
topic to its last page, then clicking "next" once more), and growing it
past that point was confirmed live to starve that allocation into an
out-of-memory crash-to-DOS — reproducible even on the pristine, unmodified
original game/assets, so it's an inherent 1992-engine memory margin, not a
bug in anything this pipeline adds. If a future change to the title card
or logo trips this ceiling, the fix belongs in those assets' own pixel
dimensions/detail level (see `patch_intro_title.TITLE_SCALE` and the
logo's own native size), not in a code workaround — and a hand-drawn
pixel-art asset should be authored at its exact intended on-screen size
rather than rendered large and downscaled at build time, since
antialiasing/blending during a runtime resize both looks worse at this
scale and fragments this sprite format's per-row RLE encoding, inflating
the very size this ceiling is guarding.

## The outro credit screens

The game's ending sequence — "THE END", "with (in order of Appearance)",
then a scrolling list of character names — is drawn from two more
picture-resource files, on top of the intro's `INTDS.HSQ` above.

**`GENERIC.HSQ`** holds a sprite alphabet: indices 33-58 are individual
English capital-letter glyphs ('A'-'Z'), baked with a 14-step gold
gradient and per-sprite transparency. The credit-name sequence composes
these letter-by-letter to spell each name — and turns out to already read
the *Hebrew-translated* `COMMAND1` text doing so: each Hebrew letter's
`heb_encode.py` byte value (the same 65-91 range DUNECHAR uses) selects a
`GENERIC.HSQ` sprite via `index = byte - 32`, so replacing sprites 33-58
with Hebrew letterforms alone makes the credit names render correctly in
Hebrew, no string-table patch needed. `utils/patch_generic_letters.py`
does this, plus fills in sprite 7 (blank in the original) with a geresh
mark for names like "ג׳סיקה" (byte 39/apostrophe → index 7, a real gap in
the original English content, not one this pipeline had to invent).

Each letter's shape comes from `generic_png/<index>_<letter>.png` if
present (hand-edited, same black-ink-on-white-background convention as
`font_png/`), otherwise from a live render of `fonts/PublicPixel.ttf`.
Edit only the letters you care about — `patch_generic_letters.py` falls
back to the font per-glyph, so this can be done incrementally. Regenerate
the editable PNGs (e.g. after switching fonts) with:
```
./utils/patch_generic_letters.py --dump-pngs
```

**`FINAL.HSQ`** holds two whole-phrase picture sprites instead of
individual letters: sprite 4 ("THE END") and sprite 5 ("with" / "(in
order of Appearance)", two lines). These have no shared per-letter
baseline to derive shading from, so `utils/patch_final_credits.py`
doesn't render anything itself — it quantizes hand-authored artwork from
`final_png/04_the_end.png` / `final_png/05_with_in_order_of_appearance.png`
(RGBA, any pixel size) against the sprites' own existing gold-gradient
palette via nearest-color match. Either file can be dropped in
independently; whichever sprite has no matching file is left as the
original English art. `FINAL.HSQ`'s on-screen sprite positioning hasn't
been investigated (unlike `INTDS.HSQ`'s sprites 5/10 above) — a
replacement with a very different aspect ratio might land or crop
differently than expected, so check live in-game after a first draft.

Both scripts always encode replacement sprites **uncompressed**
(`compressed=False`), not with `bigs_sprite.py`'s RLE path, even though
RLE is smaller and is what `patch_intro_title.py`/`patch_intro_logo.py`
use for `INTDS.HSQ`. This was a real, live-confirmed finding, not a
style choice: RLE-re-encoding a `GENERIC.HSQ` letter sprite — even
re-encoding a sprite's own *unchanged* pixel content, no Hebrew content
involved at all — crashed the real game engine (control flow hijacked
into garbage/uninitialized memory) despite round-tripping correctly
through this repo's own decoder. The real engine's RLE decoder disagrees
with our `dump.c`-derived one on some token layout our packer produces
that the original hand-authored sprites never happened to exercise; not
root-caused at the instruction level, so treat `compressed=False` as
required for any *newly generated* "bigs"-format sprite content until
that's actually pinned down, not just a `GENERIC.HSQ`-specific quirk.
`utils/verify_bigs_sprite_roundtrip.py` is a permanent regression guard
on `bigs_sprite.py`'s own encode/decode round-trip (both paths, several
real sprite files) — run it after touching that module.

## Scripts

All scripts live in `utils/`.

- **`utils/build_translation.py`** — top-level entry point; see Quick start above. Also exposes `ensure_english_txt(name)`, which extracts `tmp/<name>.TXT` from `org_files/<name>.HSQ` if not already present.
- **`utils/translate_phrase.py`** — builds one phrase/command file (font untouched, no install step); extracts its English reference via `build_translation.ensure_english_txt()` if `--english` isn't given.
- **`utils/heb_encode.py`** — the Hebrew-letter/control-byte encoder used by `translate_phrase.py`; also runnable standalone.
- **`utils/load_heb_font.sh`** — the original manual font-loading shell script; superseded by `build_translation.py`'s font step, kept for reference.
- **`utils/font.py`** — dumps/loads glyphs in the game's font-table binary format.
- **`utils/split.py`** — word-wraps and reverses text lines for the game's RTL text renderer.
- **`utils/hsq.py`** / **`utils/tu.py`** — pure-Python HSQ compression/decompression and phrase-binary pack/unpack, ported from the upstream `hsq.c`/`tu.c` (kept in `utils/` for reference, along with the `Makefile`, but no longer built or used — no C compiler required).
- **`utils/bigs_sprite.py`** — decode/encode for the picture-resource sprite container format (see "The intro title card" above). `encode_sprite()` supports arbitrary widths, not just multiples of 4 (needed for `GENERIC.HSQ`'s individual letter sprites, unlike `INTDS.HSQ`'s fixed-320px ones) — see its own docstring for the row-padding details this required.
- **`utils/verify_bigs_sprite_roundtrip.py`** — permanent regression guard on `bigs_sprite.py`'s `encode_sprite()`/`decode_sprite_pixels()` round-trip (both compressed and uncompressed paths), checked against real sprites from `INTDS.HSQ` and `GENERIC.HSQ` plus a synthetic odd-width edge case. Run it after touching `bigs_sprite.py`.
- **`utils/patch_intro_title.py`** — regenerates the intro's "DUNE" -> "חולית" title sprite; see "The intro title card" above.
- **`utils/patch_intro_logo.py`** — adds the `assets/hebrew_adv_pixel.png` logo badge below the "Interactive Entertainment Systems" credit line; see "The intro title card" above. Also enforces `INTDS_DECOMPRESSED_SAFE_CEILING`, a live-tested ceiling on `INTDS.HSQ`'s own decompressed size: going over it was confirmed (by live bisection in DOSBox-X) to starve the History-book's page-exhaustion `play_credits`/`CREDITS.HNM` easter egg into an out-of-memory crash-to-DOS that corrupts the DOS MCB chain. The fix is keeping this sprite file's own size under that ceiling (currently: a shrunk title card plus a logo hand-authored at its exact on-screen pixel size, no runtime rescaling) — not a code patch, since the crash reproduces even on the pristine, unmodified original game/assets once this file's size crosses the same threshold.
- **`utils/patch_generic_letters.py`** — regenerates the outro's `GENERIC.HSQ` credit-name letter sprites (33-58) and geresh mark (7); see "The outro credit screens" above. `--dump-pngs` (re)writes `generic_png/`'s hand-editable source from the current font render.
- **`utils/patch_final_credits.py`** — regenerates `FINAL.HSQ`'s "THE END" (sprite 4) and "with (in order of Appearance)" (sprite 5) picture sprites from hand-authored `final_png/` artwork; see "The outro credit screens" above.
- **`utils/extract_sprites.py`** — dumps every sprite from every `game/*.HSQ` file that uses the picture-resource format as individually named PNGs (`tmp/sprites/<NAME>/<index>_<w>x<h>.png`), for browsing the game's art assets. Skips files in other formats (text tables, font table, sound/music data, a few unidentified containers) and lists them in `tmp/sprites/EXTRACTION_REPORT.txt` along with why. Read-only — never touches `org_files/`, `translations/`, or `game/`.
- **`utils/run_dune.sh`** — launches the game under DOSBox-X for visual QA; see below.
- **`utils/setup_dosbox_mcp.sh`** — one-shot bootstrap for the DOSBox-X + dosbox-mcp dev toolchain on a fresh machine; see "Setting up DOSBox-X" below.

## Testing in-game

`./utils/run_dune.sh` boots `game/` under DOSBox-X (mounted as `C:`,
straight to the `C:\>` prompt — run `DUNE.BAT` to start the game) so you
can check how a rebuilt translation actually renders. It expects a
DOSBox-X binary and conf at `~/dosbox-mcp-tools/` by default
(overridable via the `DOSBOX_X_BIN` / `DOSBOX_X_CONF` env vars); see
"Setting up DOSBox-X" below if those don't exist yet on your machine.
Plain `dosbox`/`dosbox-x` from your package manager works too, if you'd
rather not set that up — `dosbox game/DUNE.BAT`.

If you're driving the translation loop through Claude Code, it can
optionally control DOSBox-X directly — typing keystrokes, navigating to
a specific screen, and taking screenshots — via the `dosbox-mcp` MCP
server (a wrapper around a GDB/QMP-automatable DOSBox-X fork,
[jdmichaud/dosbox-mcp](https://github.com/jdmichaud/dosbox-mcp) /
[lokkju/dosbox-x-remotedebug](https://github.com/lokkju/dosbox-x-remotedebug)).
This is an optional, per-machine dev-tool setup — nothing in the build
pipeline depends on it. Ask Claude to set it up if you want automated
visual QA instead of running the game by hand.

## Setting up DOSBox-X

`./utils/setup_dosbox_mcp.sh` bootstraps everything `run_dune.sh` and the
`dosbox-mcp` MCP server need on a fresh machine: system build dependencies
(apt on Linux, Homebrew on macOS — installing Homebrew itself first if
needed), a patched `dosbox-x-remotedebug` clone (built to
`~/dosbox-mcp-tools/dosbox-x-remotedebug/src/dosbox-x`), a `dosbox-mcp`
clone, a base conf that mounts this repo's `game/` as `C:` and autoruns
`DUNE.BAT`, and the MCP server registration in Claude Code. On Linux it
also patches in the `XMODIFIERS=""` workaround for an SDL1+ibus crash (not
applicable on macOS — see the script's own header comment for why). Works
on Linux and macOS (arm64 or x86_64, native build — `build-macos` on
Apple Silicon, no Rosetta needed). It's safe to re-run any time; every
step is idempotent. Run it with no arguments for an interactive first
pass (prompts before installing system packages), or `--yes` for
unattended use. `--skip-deps` skips the system-package step entirely;
`--rebuild` forces a rebuild of the DOSBox-X binary even if one already
exists.

The `dosbox-x-remotedebug` build defaults to a personal fork
([anetanel/dosbox-x-remotedebug](https://github.com/anetanel/dosbox-x-remotedebug),
branch `fix-gdb-breakpoint-address-decomposition`) rather than upstream
directly, because it carries a GDB breakpoint-address fix not yet merged
upstream — see the script header for the full rationale and override env
vars (`DOSBOX_TOOLS_DIR`, `DOSBOX_REMOTEDEBUG_REPO`,
`DOSBOX_REMOTEDEBUG_BRANCH`, `DOSBOX_MCP_REPO`).
