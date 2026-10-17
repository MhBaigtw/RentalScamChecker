# Labeling guide: `eval/private/text_labels.jsonl`

This file is the hand-labeled set that measures `text_red_flags` (SPEC 11: target 150-300
examples). Until it exists, every accuracy number for the AI path is unknown. The synthetic fixture
only tests the harness.

## Where it lives, and what never to do with it

- Path: `eval/private/text_labels.jsonl`. `eval/private/` is gitignored: the file holds other
  people's text and must never be committed, shared or published.
- Collect examples **by hand only**: read a listing in your browser, copy the text, paste it into
  the file. **Never scrape, never run a script against a listing site, never use a bulk export.**
  Facebook Marketplace, Kijiji and Craigslist forbid automated collection (CLAUDE.md).
- Before saving, delete people's names and anything that identifies a person. Phone numbers and
  emails can stay or go: the harness replaces them with `[phone]` / `[email]` before anything sees
  the text, exactly like the app does.

## Format (JSON Lines)

One JSON object per line, UTF-8, no trailing commas. Blank lines are allowed.

| Key | Required | Type | Meaning |
|---|---|---|---|
| `id` | yes | string, unique | Any stable name you like, e.g. `"k-2026-10-20-01"` |
| `text` | yes | non-empty string | The listing text as you copied it. Write line breaks as `\n` |
| `flags` | yes | list of flag names | Every warning sign the text shows; `[]` when there are none |
| `source` | no | string | Where it came from, for your own records: `"kijiji, copied by hand 2026-10-20"` |
| `notes` | no | string | Why you labeled it this way, especially for edge cases |

No other keys are allowed. The flag names are exactly these seven:

`deposit_before_viewing`, `owner_unavailable_abroad`, `keys_by_mail`, `off_platform_payment`,
`refuses_viewing`, `urgency_pressure`, `too_good_to_be_true_language`

A complete line:

```json
{"id": "k-2026-10-20-01", "text": "2 bed basement near Sheridan.\nI'm overseas for work, so send the deposit and I'll courier the keys.", "flags": ["owner_unavailable_abroad", "deposit_before_viewing", "keys_by_mail"], "source": "kijiji, copied by hand 2026-10-20"}
```

Check the file before running an eval. Every problem is listed with its line number, and nothing is
scored until the file is clean:

```
cd apps/api
.venv/Scripts/python.exe ../../eval/text_flags.py --validate-only
```

```
eval/private/text_labels.jsonl has 2 problem(s); fix them and run again:
  line 14: duplicate id "k-2026-10-20-01" (first used on line 1)
  line 22: unknown flag(s) ['deposit']
```

## One worked example per flag

These texts are made up for this guide.

| Flag | Example text | Label it because |
|---|---|---|
| `deposit_before_viewing` | "Send $500 to hold it and I'll set up a viewing next week." | Money is asked for before anyone has seen the unit (a "holding fee" counts) |
| `owner_unavailable_abroad` | "I'm currently in Dubai for work and can't meet in person." | The owner says they can't meet because they're away |
| `keys_by_mail` | "Once payment is received the keys will be sent by Canada Post." | Keys are mailed/couriered instead of handed over |
| `off_platform_payment` | "Payment by Apple gift cards or USDT only." | Gift cards, crypto, wire transfer (Western Union, MoneyGram, bank wire) |
| `refuses_viewing` | "No viewings for now, the tenant is shy, but I can send a video." | In-person viewing is refused or replaced by photos/video |
| `urgency_pressure` | "8 people asked today, first one to pay gets it." | Pressure to decide or pay fast |
| `too_good_to_be_true_language` | "Way below market, an unbeatable price for Oakville." | Explicit claim of an exceptional deal |

## A listing with no flags

Label it with an empty list. Normal listings matter as much as suspicious ones: without them
precision can't be measured. Aim for at least half of the set to have no flags.

```json
{"id": "k-2026-10-20-07", "text": "1 bed condo, Square One. Viewings Tue/Thu evenings with the property manager. First and last at lease signing.", "flags": []}
```

## Rules for edge cases

1. **Label the words, not your suspicion.** If you think a listing is fishy but can't point to a
   phrase for a flag, don't add that flag. Write your suspicion in `notes`.
2. **Interac e-Transfer alone is not `off_platform_payment`.** It's normal in Canada. Gift cards,
   crypto and wire transfers are.
3. **A deposit is only `deposit_before_viewing` if it comes before the viewing.** "First and last
   at lease signing" after a viewing: no flag. "Deposit to hold it" with no viewing mentioned:
   flag it (holding a unit you haven't seen *is* paying before viewing).
4. **"Photos/video only", "virtual tour only" = `refuses_viewing`.** "Viewings by appointment" =
   no flag.
5. **The owner is away but someone else shows the unit:** still `owner_unavailable_abroad` (the
   statement is in the text), but write it in `notes`. These cases tell us whether the flag is
   too broad.
6. **"Available now", "won't last long" in a normal tone:** not `urgency_pressure` unless there's
   pressure to *pay or decide* fast ("first to pay", "today only", "others are interested").
7. **A low price on its own is not `too_good_to_be_true_language`.** The price signal handles
   price. Only label explicit deal-claims.
8. **Several flags in one sentence:** list every flag it shows, once each.
9. **Text you didn't copy fully** (cut off, "see more"): skip that listing.
10. **Same listing twice** (reposts, near-identical text): keep one.
11. **Only the listing text.** Don't paste chat messages into `text` for now: the app sends only
    the listing text today.
12. **English only** for now; skip French listings (note them for later).
13. **Unsure after reading these rules?** Make your best call and explain it in `notes`. Consistent
    notes are more useful than a perfect label.

## Where to find examples legitimately

- **Listings you're looking at anyway** while searching for housing: copy them by hand as you go.
- **Listings friends or classmates received**, shared with their permission, with names removed.
- **Public warning material** quoting scam listings or messages: the Canadian Anti-Fraud Centre,
  your college's housing office, consumer-protection pages, news articles. Copy the quoted text by
  hand.
- **Public posts where people share a suspicious listing** they got (e.g. student forums): copy only
  the listing text, by hand.

Using a small number of hand-copied examples in a private research set is the point; republishing
them is not. Don't contact posters, don't create accounts to collect listings, and don't collect
more than the set needs.

## Size and balance

- Target 150-300 examples (SPEC 11). Precision and recall are reported **with the set size**, and
  with fewer than ~15 positives for a flag, that flag's numbers are too noisy to compare models.
- Roughly half with no flags, and try to reach 15+ examples for each flag.
- Label in one sitting per batch, then re-read a sample a few days later. If you'd change many
  labels, the rules above need tightening before the numbers mean much.

## Running the eval

```
cd apps/api
.venv/Scripts/python.exe ../../eval/text_flags.py --mode keyword                 # free
.venv/Scripts/python.exe ../../eval/text_flags.py --mode llm                     # default model
.venv/Scripts/python.exe ../../eval/text_flags.py --mode llm --model claude-opus-5-5
```

LLM mode makes one API call per example the first time (cached after that) and counts toward
`LLM_DAILY_CALL_CAP`. With 300 examples that's 300 calls per model, more than the default daily
cap of 200, so raise the cap for that run (after approving the spend) or split it over two days.
Reports are written to `eval/private/reports/`.
