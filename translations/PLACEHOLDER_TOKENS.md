# Placeholder tokens in the `.HEB` files

The original English text contains raw control bytes the game engine reads
to insert line breaks, character names, locations, and numbers at
runtime. Those bytes can't be typed in a plain UTF-8 editor, so each
`.HEB` file represents them as short ASCII tokens instead, at the same
position in the sentence as the English source. `utils/heb_encode.py`
converts each token back into the real control byte(s) when building the
final file.

**Keep every token from the source line somewhere in your translation.**
You can move a token to wherever it belongs in the Hebrew word order —
that's expected, since Hebrew often reorders a sentence relative to
English — but don't delete it, retype it differently, or leave it out.
The build fails loudly (with the line number) if a token is missing,
extra, or misspelled, so a mistake here is safe to catch — it just won't
silently produce a broken game text.

Tokens are case-sensitive except `mr<letter>` (`mrH` and `mrh` are both
accepted). Byte value → token meaning is **positional per file**: the
same token can mean different things in `PHRASE11.HEB` vs `PHRASE12.HEB`
vs `COMMAND1.HEB` (e.g. `mf` is a smuggler region in one file and a
spice-skill level in another) — never assume a token's meaning carries
over between files.

## Line-break / formatting markers

These can also be **added** anywhere in your translation beyond what the
English source has, if the Hebrew line needs a different break for
readability — unlike every other token category below, which must only
appear where the source already has one.

| Token | Meaning |
|---|---|
| `M` | Line break |
| `FF` | Formatting control byte |
| `H` | Formatting control byte |
| `FE` | Formatting control byte |
| `MFF` | `M` and `FF` combined (appears as one unit) |
| `MH` | `M` and `H` combined (appears as one unit) |

## Name-substitution tokens

The game fills these in at runtime with an actual name — a player
character, a location, a sietch. They take real on-screen width even
though they're only 1-3 bytes in the file, so `utils/split.py` accounts
for that when word-wrapping (see its own comments for the exact pixel
widths used per token, measured against the longest real name each one
can be substituted with).

| Token | Meaning |
|---|---|
| `ma` … `ml` | Single-letter name variables. Meaning is positional per file (see above) — could be a player name, a character, or something else file-specific. |
| `ma`/`mb` pair | A special case: in `PHRASE11`/`PHRASE12` specifically, `ma` and `mb` together (joined by a literal `-` in the Hebrew, e.g. `ma-mb`) form an area/site location pair, used in troop-status radio messages ("My troop is in *area*-*site*..."). Keep the `-` between them. |
| `m@@a`, `m@@b`, `m@@c`, … | Location-code tokens. Substituted at runtime with a location name string. |

## Quantity-substitution tokens

The game fills these in at runtime with a number — a troop count, a
percentage, a spice amount. Each one is always immediately followed by a
single literal digit in the source (e.g. `mq<0`, `mr¼0`) — **that digit
must stay immediately after the token**, never separated from it or
reordered before it. The engine scans forward from the token to find
that digit and overwrites it with the real number; if the digit ends up
somewhere else, the displayed number comes out scrambled (this actually
happened once — a troop count rendered as "0190" instead of "1900" until
the digit's position was fixed).

| Token | Meaning |
|---|---|
| `mq<char>` | A quantity token, e.g. `mq<`, `mqa`. `<char>` varies — copy it exactly as it appears in the source. |
| `mr<char>` | Same as `mq<char>`, a different quantity token. `mrH`/`mrh` are treated as the same token. |
| `mqm]` | A special case used only in `PHRASE12.HEB` (a quantity token whose raw byte has no clean printable character to represent it, so this fixed mnemonic is used instead). |

## Punctuation that isn't a placeholder token

These aren't control-byte placeholders — they're real Hebrew punctuation
characters, typed as themselves, which `heb_encode.py` maps to the
equivalent ASCII punctuation byte automatically:

| Character | Name | Maps to |
|---|---|---|
| `״` | Gershayim | `"` (used like quotation marks, e.g. in acronyms) |
| `׳` | Geresh | `'` (used like an apostrophe, e.g. `ג׳סיקה`) |

## If something goes wrong

Running the build (`./utils/build_translation.py` or
`./utils/translate_phrase.py <NAME>`) reports the exact line number and
what token was expected vs. found for any mismatch. A few known,
already-tracked source-file typos are auto-corrected in
`heb_encode.py`'s `KNOWN_CORRUPTIONS` table — if you hit a *new* one
that looks like a one-off transcription slip rather than an editorial
choice, fix it directly in the `.HEB` file rather than asking for a
code-level workaround (see `CLAUDE.md`'s "Working with the
control-byte/placeholder scheme" section).
