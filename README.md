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

## Directory layout

| Directory | Contents | Committed? |
|---|---|---|
| `translations/` | Hebrew `.HEB` source files (pasted from the translation spreadsheet) | yes |
| `font_png/` | Hebrew glyph images loaded into the game's font table | yes |
| `utils/` | All scripts and tools: `build_translation.py`, `translate_phrase.py`, `heb_encode.py`, `load_heb_font.sh`, plus `hsq.py` (compress/decompress), `tu.py` (pack/unpack phrase binaries), `font.py`, `split.py` | yes |
| `org_files/` | Unmodified original `.HSQ` files, verified by checksum | **no** (gitignored) |
| `game/` | Your copy of the full game install; also the final install target | **no** (gitignored) |
| `build/` | Final translated `.HSQ` outputs, ready to install | **no** (gitignored) |
| `tmp/` | Intermediate working files, including each `<NAME>.TXT` (English reference text, regenerated on demand — see below) | **no** (gitignored) |

## Quick start

1. Copy your game install into `game/` (needs at least `DUNEPRG.EXE` and the
   original `COMMAND1.HSQ`, `DUNECHAR.HSQ`, `PHRASE11.HSQ`, `PHRASE12.HSQ` or
   their `.BAK` equivalents).
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
./utils/font.py <input.bin> --dump x --output out.png
```
`--dump` requires a value but the value itself is unused (only checked for
conflicts with `--load`) — the file to render comes from the positional
argument. For example, to render the original (unmodified) glyph table:
```
./utils/hsq.py -d org_files/DUNECHAR.HSQ -o /tmp/DUNECHAR.BIN
./utils/font.py /tmp/DUNECHAR.BIN --dump x --output /tmp/before.png
```

## Scripts

All scripts live in `utils/`.

- **`utils/build_translation.py`** — top-level entry point; see Quick start above. Also exposes `ensure_english_txt(name)`, which extracts `tmp/<name>.TXT` from `org_files/<name>.HSQ` if not already present.
- **`utils/translate_phrase.py`** — builds one phrase/command file (font untouched, no install step); extracts its English reference via `build_translation.ensure_english_txt()` if `--english` isn't given.
- **`utils/heb_encode.py`** — the Hebrew-letter/control-byte encoder used by `translate_phrase.py`; also runnable standalone.
- **`utils/load_heb_font.sh`** — the original manual font-loading shell script; superseded by `build_translation.py`'s font step, kept for reference.
- **`utils/font.py`** — dumps/loads glyphs in the game's font-table binary format.
- **`utils/split.py`** — word-wraps and reverses text lines for the game's RTL text renderer.
- **`utils/hsq.py`** / **`utils/tu.py`** — pure-Python HSQ compression/decompression and phrase-binary pack/unpack, ported from the upstream `hsq.c`/`tu.c` (kept in `utils/` for reference, along with the `Makefile`, but no longer built or used — no C compiler required).
- **`utils/run_dune.sh`** — launches the game under DOSBox-X for visual QA; see below.

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
