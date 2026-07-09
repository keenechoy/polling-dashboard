# Phase 3 — Automated Poll Updates (setup &amp; tuning)

This is the automation layer. Once it's running, polls update themselves; you only
step in when the scraper flags something. These files sit in your GitHub repo
alongside `polling_dashboard.html` and `data.json`.

**Coverage (v1.0):** 10 of the dashboard's 11 configured countries are auto-scraped
(Sweden, Netherlands, Denmark, Finland, Germany, Poland, Spain, Italy, Austria,
Portugal). **Belgium** is the deliberate exception — Belgian pollsters publish
regionally, not nationally, so there is no national poll table to scrape. Its data
structure is preserved in `data.json` and the dashboard config but it's hidden from
the country dropdown, pending the planned v2.0 split into Flanders and Wallonia.

## Files

| File | Goes where | Role |
|---|---|---|
| `scraper.py` | repo root | Fetches, parses, validates, merges into `data.json` |
| `validate.py` | repo root | The six deterministic validation checks |
| `scraper_config.json` | repo root | Per-country source URL + column map + validation params |
| `requirements.txt` | repo root | Python dependencies |
| `update-polls.yml` | `.github/workflows/` | The daily scheduler (rename on the way in if you like) |

## What it does, end to end

1. For each country in `scraper_config.json`, fetch the source page and read its tables with pandas.
2. Parse the polling table into rows: `[pollster, "YYYY-MM-DD", …values in poll_keys order]`.
3. Run every new row through the validation gauntlet.
4. **Clean** rows are merged into `data.json`; **flagged** rows are written to `review/quarantine_<date>.json`.
5. The GitHub Action commits `data.json` if it changed and attaches any quarantine file as a downloadable artifact.

The dashboard always reads `data.json`, so the moment a clean poll merges, the live site reflects it. A suspect poll never auto-merges — the dashboard keeps showing the last clean dataset until you adjudicate.

## The validation gauntlet (automatic flag, human resolve)

| Check | Trips when | Default |
|---|---|---|
| base-consistency | party shares don't sum to ~100 | ±3–4 pts |
| outlier | a party moves more than N pts from its rolling average | 4 pts |
| duplicate | same pollster + date already stored | — |
| date-sanity | date unparseable, in the future, or too old | 200 days |
| pollster | pollster not on the country whitelist | per country |
| new-column | an unmapped party-looking column appears | — |

Tune any of these per country in `scraper_config.json`. A flag doesn't mean "wrong" — it means "a human should look." The new-column check is what catches things like a fresh party (the Dutch DNA situation): the scraper won't silently ingest it, it'll flag it and wait for you to decide how to handle it.

## First-run setup

```bash
pip install -r requirements.txt
python scraper.py --dry-run          # parse + validate everything, write nothing
```

Read the output. For each country you want to see something like
`parsed 26 rows · 0 new accepted · 0 flagged`. If you instead see
`ERROR table_index … out of range` or `no poll rows parsed`, that's the expected
tuning step — go to **Tuning** below.

When the dry run looks right:

```bash
python scraper.py                    # actually writes data.json
git add data.json && git commit -m "Refresh polls" && git push
```

## Tuning (the one part that needs your eye)

Source pages are human-edited HTML tables, so a handful of config fields occasionally need adjusting:

- **`table_match`** (preferred) — a list of header anchor strings; the scraper picks the *first* table on the page whose headers contain all of them. This survives tables being added or removed above the poll table, and on pages that split polls into yearly tables (Austria) it naturally selects the current period. Used by Spain, Italy, Austria and Portugal.
- **`table_index`** — the older positional fallback: which table on the page is the polling table. Still used by the original six countries. If parsing fails, open the page, count the tables, and try `0`, `1`, `2`…
- **`column_map`** — maps the page's column headers to our party keys, for tables with *text* headers. If the page renames a column (e.g. `GL-PvdA` → `PRO`), add the new header as another key pointing to the same value.
- **`column_index_map`** — positional mapping (`key → column number`, 0-based) for tables whose party columns are *logos with no text header* (Spain, Austria, Portugal). pandas reads those headers as unnamed, so names are useless — position is the only handle. If the page adds or removes a party column, these indices shift and need re-counting; each country's `note` in the config records how the order was confirmed (usually the election benchmark row).
- **`defaults`** — per-key fallback values for optional columns that individual pollsters leave blank (Spain's UPN, which several houses simply don't report). Without a default, a blank cell drops the whole row.
- **`pollster_skip`** — substrings that exclude rows entirely before validation: election benchmark rows (`"election"`) and pollsters whose methodology makes their numbers non-comparable (Poland's CBOS, which doesn't redistribute undecideds).

The new-column check is automatically disabled for `column_index_map` countries (unnamed headers carry no party information to inspect). Everything else (the engine, the dashboard, the other countries) is untouched by a retune.

**A note on how these configs were built:** the four newest countries were configured from the live page structures observed via web research, and the parse logic was proven against offline HTML fixtures replicating each page's quirks (combined `% seats` cells, logo headers, `<1` and `?` cells, aggregator tables ahead of poll tables). The development sandbox cannot reach Wikipedia directly, so **your first live run is the final confirmation step** — if a country errors or quarantines heavily on run one, that's the expected tuning moment, not a defect. Bring the output to chat.

## Scheduling

`update-polls.yml` runs daily at 06:00 UTC and can also be triggered by hand from the repo's **Actions** tab. To change cadence, edit the `cron` line. Free GitHub Actions minutes cover a daily run comfortably. The Action needs `contents: write` permission (already set in the file) to commit `data.json` back.

## When something gets flagged

1. The Action run produces a `quarantine` artifact (download it from the run page), or you'll see `review/quarantine_<date>.json` locally.
2. Open it — each entry has the row and the flags that tripped.
3. Bring it to chat. I'll help you decide: real swing, typo, renamed/new party, or pollster to add. Once resolved, the corrected row goes into `data.json`.

## Adding a country later

Two edits, no code change: add the country's block to `scraper_config.json` (URL, `poll_keys`, `column_map`, whitelist), and add its config + an initial `data.json` block to the dashboard. Ask me in chat and I'll generate both.
