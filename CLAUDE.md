# CLAUDE.md

Guidance for working in this repo. See `README.md` for the translator-facing
usage guide — read that first for the full pipeline picture.

## What this repo is

Build tooling for a Hebrew fan translation of the 1992 DOS game *Dune*. It
never contains the original game's assets or executables — those are
gitignored (`/game/`, `/org_files/`, `/build/`, `/tmp/`) and expected to be
supplied locally by whoever runs the pipeline. Only the translation source
(`translations/*.HEB`), font glyph images (`font_png/`), and the Python
tooling under `utils/` are committed. The English reference text
(`<NAME>.TXT`) is derived, never committed — see below.

All scripts (`build_translation.py`, `translate_phrase.py`, `heb_encode.py`,
`load_heb_font.sh`, plus the pre-existing `font.py`/`split.py`/`hsq.py`/`tu.py`)
live in `utils/`. Nothing runs from the repo root. `hsq.c`/`tu.c`/`Makefile`
are still present as the original upstream reference but are no longer part
of the pipeline — see below.

## Directory roles (do not blur these)

- `org_files/` — unmodified original `.HSQ` files, identified and repaired by
  **content hash**, never by filename. `utils/build_translation.py`'s
  `ensure_org_files()` is the only code that should write here.
- `translations/` — human-edited Hebrew source. Never write to these
  programmatically except via the translator's own edits.
- `build/` — only ever contains *final* installable `.HSQ` outputs. Never put
  intermediate/working files here — that's what `tmp/` is for.
- `tmp/` — all working/intermediate files: the font assembly chain,
  per-phrase encode/split/pack stages, and each `<NAME>.TXT` (English
  reference text, extracted on demand from `org_files/<NAME>.HSQ` by
  `build_translation.ensure_english_txt()`). Safe to delete at any time —
  everything here regenerates.
- `game/` — the translator's own full game install; also the copy
  destination for the final build. Only the files this pipeline replaces
  should ever be touched here — never delete or modify unrelated game assets.

## English reference text is generated, never committed

`<NAME>.TXT` (e.g. `PHRASE11.TXT`) is the original English text, one line
per in-game phrase — it's the *shipped* content of `org_files/<NAME>.HSQ`,
so there's no reason to hand-maintain a separate copy at the repo root.
`build_translation.ensure_english_txt(name)` produces `tmp/<name>.TXT` by
running `utils/hsq.py -d` then `utils/tu.py -u` against `org_files/<name>.HSQ`,
and both `translate_phrase.py` and `build_translation.py` call it
automatically rather than expecting the file to already exist. Don't
reintroduce a committed `<NAME>.TXT` at the repo root — if a script needs
the English reference, it should call `ensure_english_txt()`, not assume a
static path.

## Key scripts and what not to duplicate

- `utils/heb_encode.py` — the single source of truth for the Hebrew-letter →
  font-byte mapping and the control-byte/placeholder-token scheme (`M`, `FE`,
  `FF`, `H`, `mk`, `mq<char>`, `m@@<letter>`, `mr<char>`, etc.). If you find
  yourself hand-rolling similar byte-mapping logic elsewhere, import from here
  instead.
- `utils/translate_phrase.py` exposes `build_phrase(name, heb_path,
  english_path, no_split=..., out_dir=...)` — call this directly from other
  scripts rather than shelling out to the CLI.
- `utils/build_translation.py` is the only place that should touch
  `org_files/` or copy into `game/`. Don't add ad-hoc copy logic to other
  scripts.
- `utils/font.py`'s `--dump` flag requires a value, but that value is only
  used to check for conflicts with `--load` — the actual glyph-table bytes
  to render come from the positional input file argument (e.g.
  `./utils/font.py <input.bin> --dump x --output out.png`). Don't pass the
  input file as `--dump`'s value; it'll silently read an empty buffer.
  `build_translation.py`'s font step uses this to render
  `tmp/DUNECHAR_before.png`/`tmp/DUNECHAR_after.png` for a visual diff.
- `utils/hsq.py` and `utils/tu.py` are pure-Python ports of the upstream
  `Dune-game-translations` C tools (`utils/hsq.c`, `utils/tu.c`, still kept
  in the repo for reference alongside the `Makefile`, but no longer built or
  invoked). They exist because this pipeline needs to run on machines with
  no C compiler available; `build_translation.py` and `translate_phrase.py`
  now invoke them via `[sys.executable, "utils/hsq.py", ...]` /
  `[sys.executable, "utils/tu.py", ...]` instead of shelling out to compiled
  binaries. `hsq.py`'s compressor was validated by decompressing real shipped
  `.HSQ` files from `game/` and recompressing them, confirming byte-identical
  output to the original C binary (including a file spanning multiple
  32KB-window compression chunks) — re-run that check if you touch the
  compression logic, since any regression would produce a `.HSQ` the actual
  DOS game engine can't decode. `tu.py`'s pack/unpack/check logic is a
  straightforward line-for-line port with no such risk. Don't reintroduce a
  dependency on `make`/a C compiler for this pipeline.
- All scripts import each other as sibling modules within `utils/` (e.g.
  `translate_phrase.py` does `import heb_encode` and, lazily,
  `import build_translation`) — this only works because they all live in the
  same directory. Don't split them across directories again without also
  fixing these imports.

## Working with the control-byte/placeholder scheme

The English `.TXT` files use raw single/multi-byte control codes (line
breaks, player-name/location/quantity substitution variables) that can't be
typed in a plain UTF-8 editor. The Hebrew `.HEB` translations represent the
same codes as short ASCII tokens at the matching position in the line. When
extending support to a new phrase/command file:

1. Read both files as raw bytes, split on `\n` only — `\r` bytes appear
   *inside* lines as meaningful control codes, not as line separators.
2. Any new control-byte pattern needs a corresponding entry in
   `utils/heb_encode.py`'s tokenizer (`tokenize_txt`) and `TOKEN_RE`. Byte↔token
   meaning is positional per file — the same byte value can mean different
   things in different phrase files (verified true for `PHRASE11` vs
   `PHRASE12`), so don't assume a fixed global table.
3. Known source-file transcription slips (corrupted or missing tokens in a
   `.HEB` file) are handled via the small `KNOWN_CORRUPTIONS` substitution
   table and the bare-marker-fallback logic in `heb_encode.py` — prefer
   fixing the source file when the fix is a content/formatting decision
   (e.g. where a line break belongs), and only add code-level tolerance for
   pure format-level slips.
4. Verify a new file's encoding with a control-byte-count cross-check
   (per-line count of each token category should match between the `.TXT`
   and the encoded output) before trusting the pipeline output.

## Boundaries

- Don't reproduce or quote the game's dialogue/story text in commits, PRs,
  commit messages, or conversation — it's copyrighted content. Byte-level or
  structural analysis (control-code patterns, line counts, hex/md5 values) is
  fine; the English/Hebrew sentences themselves are not something to
  transcribe into documentation, code comments, or chat output.
- Treat any editorial decision about the translated text itself (word
  choice, where a line break goes, tone) as the translator's call — scripts
  should only ever apply mechanical, format-level transformations that are
  fully specified up front, never guess at content changes.
