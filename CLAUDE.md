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

## Testing the translation in-game (DOSBox-X + MCP)

A `dosbox-mcp` MCP server may be registered at **project scope** for
this repo (in `~/.claude.json`, not committed — a per-machine dev-tool
setup, not something the build pipeline depends on). When connected
(`claude mcp list` shows `dosbox: ... ✓ Connected`), it exposes
`dosbox_*` tools — `dosbox_start`/`dosbox_stop`, `dosbox_type`
(keystrokes), `dosbox_screenshot`, `dosbox_wait_for_text`, plus
memory/register/breakpoint tools — that drive a real
`dosbox-x-remotedebug` build (a DOSBox-X fork with GDB + QMP
automation; see [jdmichaud/dosbox-mcp](https://github.com/jdmichaud/dosbox-mcp))
running the actual game out of `game/`. Prefer this over asking the
user to check a rendering by hand when it's available.

If it isn't set up yet, offer to build it — see the "Setting up
DOSBox-X + dosbox-mcp" walkthrough this repo's history was built with:
apt-installing DOSBox-X's build deps, compiling
`lokkju/dosbox-x-remotedebug` with
`--enable-remotedebug --disable-libfluidsynth --disable-mt32`, cloning
`jdmichaud/dosbox-mcp`, and running its `install.py` at project scope
with a conf that mounts this repo's `game/` as `C:`.

Facts worth knowing when using it:
- The base conf auto-mounts `game/` as `C:` **and auto-runs `DUNE.BAT`**
  on boot — no manual `MOUNT` or typing needed, just
  `dosbox_wait_for_text("D U N E")` (or similar) and wait for the intro.
- Dune runs in VGA graphics mode, not text mode — use
  `dosbox_screenshot`, not `dosbox_screen`.
- On a desktop with `XMODIFIERS=@im=ibus` set (common with ibus input
  method), this SDL1 build segfaults on window init unless
  `XMODIFIERS` is cleared for the `dosbox-x` process — the MCP's env
  block needs `"XMODIFIERS": ""`. If the MCP is ever reinstalled,
  re-add that env var; `install.py` doesn't set it itself.
- `dosbox_screenshot` **used to** intermittently fail
  (`"screendump failed: ... no file created"`) — root cause is a race in
  `dosbox-x-remotedebug`'s QMP `handle_screendump` (`src/debug/qmp.cpp`):
  `CAPTURE_AddImage()` (`src/hardware/hardware.cpp`) clears the
  "screenshot pending" flag the instant PNG encoding *starts*, not when
  the file is actually finished and `last_screenshot_path` is set, so
  the handler's flat 50ms grace sleep after the flag clears often wasn't
  enough. The real fix is
  [lokkju/dosbox-x-remotedebug#3](https://github.com/lokkju/dosbox-x-remotedebug/pull/3)
  (by jdmichaud, `dosbox-mcp`'s own author — root-causes it properly by
  reordering the flag-clear in `hardware.cpp` itself, plus fixes a
  related fd leak) — **still unmerged** as of this writing. We initially
  patched around it locally with a narrower workaround scoped to just
  `qmp.cpp` (poll for the path instead of trusting the fixed sleep) and
  opened our own PR, then closed it in favor of #3 once we found it
  already existed and fixes the actual root cause rather than just the
  symptom our workaround targeted. Our local `~/dosbox-mcp-tools/`
  build still runs the narrower qmp.cpp-only patch (it works fine for
  our purposes); if you rebuild from a fresh clone before #3 merges,
  either re-apply that patch or cherry-pick #3 directly. If you're on
  an unpatched build, you'll still see the old behavior — fall back to
  reading the newest file in `~/dosbox-mcp-tools/dosbox-mcp/capture/*.png`
  (sorted by mtime), which DOSBox-X's internal auto-capture always
  writes even when the QMP handler's response errors.
- `utils/run_dune.sh` runs the exact same binary + conf without Claude
  or the MCP involved, for the user to check something manually.

### Navigating the game (controls & menus)

No mouse tool is exposed by the MCP (only `dosbox_type`/`dosbox_press_key`/
`dosbox_press_combo`), but the original 1992 instruction manual documents a
full keyboard-driven cursor, which is what makes automated navigation
possible at all:

- **Two cursor-movement modes exist — prefer plain arrows.**
  - Plain arrows (`dosbox_press_key("up"/"down"/"left"/"right")`) *cycle
    the cursor between the current screen's defined hotspots* — not
    continuous movement. This is why a single press can appear to jump
    a long distance: it's landing on the next hotspot in tab order, and
    **grayed-out/locked options are skipped entirely** (don't mistake a
    skip for a stuck cursor — check whether what it jumped over was
    grayed out). **In practice this is the faster, more reliable
    method** — a handful of presses reliably snaps straight onto a
    target hotspot (e.g. the History book icon, topic-list rows).
  - The manual also documents `Ctrl`+arrow
    (`dosbox_press_combo(["ctrl", "up"/"down"/...])`) as free,
    non-hotspot-locked cursor positioning. **Tried and found worse in
    practice**: aiming it by eye from screenshots was slow and imprecise
    — missed a target hotspot even after several correction attempts.
    Only reach for it if hotspot-jumping demonstrably can't reach a
    target at all.
  - `spc` or `ret` clicks whatever's currently under the cursor.
  - Screenshot after *every single keypress* when navigating by feel —
    batching presses before checking is how you overshoot a target.
- **Intro**: `spc` advances slides one at a time; `esc` skips straight
  to the first gameplay screen (the Palace throne room).
- **Main control panel** (bottom of screen, present in most gameplay
  views):
  - Top-left book icon opens the **"History"** window (not an
    "encyclopedia" — that's the correct in-universe term), which starts
    with basic background info and unlocks more sections as the game
    progresses.
  - Right side is the **compass** — available travel directions
    (N/S/E/W); its center shows a room map with characters when inside
    the Palace.
  - Center is the **options window** — available actions for the
    current scene, including "speech" to talk to whichever character is
    present.
  - Bottom-left has a time-of-day/day-count indicator plus up to two
    slots for companions currently following Paul.
- **Save/pause**: `P` pauses; looking in the bedroom mirror inside the
  Palace also opens SAVE/LOAD/RESTART. The game autosaves on entering a
  new location.
- **Dune Map / Globe**: reachable from the main control panel; shows
  Fremen sietches and Harkonnen forts, troop-chief icons colored by
  assigned occupation (yellow=spice mining, red=soldier, green=ecology),
  and (via the globe) overall game status — day count, charisma,
  percentage of Dune controlled (red=Atreides, blue=Harkonnen).
- **Core game loop**, useful for recognizing what screen you've landed
  on: recruit Fremen at sietches (requires contacting Stilgar first) →
  assign occupation → manage motivation (drops without regular visits)
  → locate Harkonnen forts via espionage → attack via the map → win by
  taking every Harkonnen fort.

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
