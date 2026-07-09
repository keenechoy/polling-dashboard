#!/usr/bin/env python3
"""
Polling Dashboard — automated poll scraper (Phase 3).

Fetches the latest polls for each configured country from its source page,
validates every new poll, merges the clean ones into data.json, and quarantines
anything that trips a validation flag for human review.

Design principles
-----------------
* FAIL SAFE: data.json is only rewritten if at least one country parsed cleanly.
  A parse error for one country never corrupts the others or the existing file.
* AUTOMATIC FLAGGING, HUMAN RESOLUTION: suspect polls go to review/ and the
  dashboard keeps showing the last clean dataset. You adjudicate flags in chat.
* CONFIG-DRIVEN: adding a country is a config edit (scraper_config.json), not a
  code change. The column map is the part most likely to need tuning on first run.

Usage
-----
    python scraper.py                # scrape all countries in the config
    python scraper.py --country denmark
    python scraper.py --dry-run      # parse + validate, but do not write data.json

Run it once by hand after editing the config and eyeball the output before
wiring up the scheduler. See PHASE3_README.md.
"""

import argparse
import json
import sys
import datetime as dt
from pathlib import Path

import pandas as pd          # parses HTML tables
import requests

from validate import validate_poll, RollingStats

ROOT = Path(__file__).resolve().parent
DATA_FILE = ROOT / "data.json"
CONFIG_FILE = ROOT / "scraper_config.json"
REVIEW_DIR = ROOT / "review"
HEADERS = {"User-Agent": "polling-dashboard-scraper/1.0 (personal project)"}


def load_json(path, default=None):
    if not path.exists():
        return default
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def save_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def fetch_tables(url):
    """Return all HTML tables on a page as DataFrames."""
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    # flavor='lxml' is robust for Wikipedia's wikitables
    return pd.read_html(resp.text, flavor="lxml")


def parse_country(cfg):
    """
    Parse a country's poll table into a list of rows:
        [pollster, 'YYYY-MM-DD', v1, v2, ... in pollKeys order]
    Returns (rows, warnings). Raises on a hard parse failure.
    """
    tables = fetch_tables(cfg["url"])
    return _rows_from_tables(tables, cfg)


def _flatten_columns(df):
    """Flatten pandas MultiIndex / messy Wikipedia headers into plain strings."""
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [" ".join(str(x) for x in tup
                               if str(x) != "nan" and not str(x).startswith("Unnamed")).strip()
                      for tup in df.columns]
    df.columns = [str(c).strip() for c in df.columns]
    return df


def _select_table(tables, cfg):
    """
    Pick the poll table. Preferred: `table_match` — a list of header anchor
    strings; the FIRST table whose flattened headers contain all anchors wins
    (Wikipedia lists the current period's table first). Robust against tables
    being added/removed above the poll table. Fallback: numeric `table_index`.
    """
    anchors = cfg.get("table_match")
    if anchors:
        for df in tables:
            df = _flatten_columns(df)
            headers = " | ".join(df.columns)
            if all(a in headers for a in anchors):
                return df
        raise RuntimeError(f"no table matched anchors {anchors} — the page structure "
                           f"likely changed; retune table_match in config")
    try:
        return _flatten_columns(tables[cfg.get("table_index", 0)])
    except IndexError:
        raise RuntimeError(f"table_index {cfg.get('table_index', 0)} out of range "
                           f"({len(tables)} tables found) — retune table_index in config")


def _find_col(df, name):
    """Find a column by exact name, falling back to substring containment
    (Wikipedia headers often gain footnote markers or merged-header prefixes)."""
    if name in df.columns:
        return name
    for c in df.columns:
        if name in c:
            return c
    return None


def _rows_from_tables(tables, cfg):
    """
    Core parse logic, separated from fetching so it can be tested offline
    against HTML fixtures (the sandbox cannot reach Wikipedia; the first
    live run happens in GitHub Actions).
    """
    warnings = []
    df = _select_table(tables, cfg)

    col_map = cfg.get("column_map", {})        # {source header: our key}
    idx_map = cfg.get("column_index_map", {})  # {our key: positional column index}
    defaults = cfg.get("defaults", {})         # {our key: value when cell is blank}
    poll_keys = cfg["poll_keys"]
    pollster_col = _find_col(df, cfg["pollster_col"])
    date_col = _find_col(df, cfg["date_col"])
    if pollster_col is None or date_col is None:
        raise RuntimeError(f"pollster/date column not found (looked for "
                           f"'{cfg['pollster_col']}' / '{cfg['date_col']}' in {list(df.columns)}) "
                           f"— retune config")

    # Detect unmapped party-looking columns (possible NEW PARTY — flag, don't ingest).
    # Skipped when column_index_map is used: those tables have unlabelled party
    # columns (logos), so header names carry no party information.
    if not idx_map:
        mapped_sources = set(col_map) | {pollster_col, date_col} | set(cfg.get("ignore_cols", []))
        for c in df.columns:
            if c not in mapped_sources and _looks_like_party_col(c):
                warnings.append(f"[new-column] unmapped column '{c}' — possible new party; "
                                f"add it to column_map / poll_keys after review")

    rows = []
    for pos, r in df.iterrows():
        pollster = _clean_pollster(r.get(pollster_col, ""))
        date = _parse_date(r.get(date_col, ""), cfg.get("year"))
        if not pollster or not date:
            continue  # header rows, separators, undated rows
        if cfg.get("pollster_skip") and any(s.lower() in pollster.lower()
                                            for s in cfg["pollster_skip"]):
            continue  # e.g. 'election' benchmark rows, excluded pollsters
        values = []
        ok = True
        for key in poll_keys:
            if key in idx_map:                       # positional (unlabelled headers)
                i = idx_map[key]
                raw = r.iloc[i] if i < len(r) else None
                val = _to_float(raw)
            else:                                    # name-based
                src = next((s for s, k in col_map.items() if k == key), None)
                src = _find_col(df, src) if src else None
                val = _to_float(r.get(src)) if src else None
            if val is None:
                if key in defaults:                  # optional key (e.g. Spain's UPN)
                    val = defaults[key]
                else:
                    ok = False
                    break
            values.append(val)
        if not ok:
            continue
        rows.append([pollster, date] + values)

    if not rows:
        raise RuntimeError("no poll rows parsed — the page structure likely changed; "
                           "retune table_match/column_map in config")
    # Wikipedia lists newest first; store oldest-first to match the dashboard
    rows.sort(key=lambda x: x[1])
    return rows, warnings


def _looks_like_party_col(name):
    n = name.strip()
    return 0 < len(n) <= 6 and not any(w in n.lower()
                                       for w in ("date", "firm", "poll", "sample", "lead", "ref", "n="))


def _clean_pollster(v):
    s = str(v).strip()
    return "" if s.lower() in ("nan", "") else s.split("[")[0].strip()


def _parse_date(v, year_hint):
    """Best-effort date parse; returns 'YYYY-MM-DD' for the fieldwork END date."""
    s = str(v).strip()
    if not s or s.lower() == "nan":
        return None
    s = s.split("[")[0].strip()
    # ranges like '25-31 May 2026' or '28 Apr-5 May 2026' -> take the end
    if "-" in s or "\u2013" in s:
        s = s.replace("\u2013", "-").split("-")[-1].strip()
    for fmt in ("%d %b %Y", "%d %B %Y", "%Y-%m-%d", "%d %b", "%d %B"):
        try:
            d = dt.datetime.strptime(s, fmt)
            if d.year == 1900 and year_hint:
                d = d.replace(year=year_hint)
            return d.strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def _to_float(v):
    if v is None:
        return None
    s = str(v).strip().replace(",", ".").replace("%", "").split("[")[0].strip()
    s = s.lstrip("<>~")                      # '<1' (Wikipedia's sub-1% marker) -> '1'
    if s in ("", "nan", "\u2013", "-", "\u2014") or "n/a" in s.lower() or s == "?":
        return None
    try:
        return round(float(s), 2)
    except ValueError:
        # combined cells like '32.0 133' (vote% + seats) -> take the leading number
        head = s.split()[0] if s.split() else ""
        try:
            return round(float(head), 2)
        except ValueError:
            return None


def merge_country(name, cfg, parsed_rows, data, review):
    """Validate parsed rows against the existing dataset; merge clean, quarantine flagged."""
    existing = data.get(name, {}).get("polls", [])
    seen = {(r[0], r[1]) for r in existing}            # dedupe on pollster+date
    poll_keys = cfg["poll_keys"]
    stats = RollingStats(existing, poll_keys, window=cfg.get("rolling_window", 5))
    whitelist = cfg.get("pollster_whitelist", [])
    params = {
        "sum_tolerance": cfg.get("sum_tolerance", 3.0),
        "outlier_threshold": cfg.get("outlier_threshold", 4.0),
        "max_age_days": cfg.get("max_age_days", 200),
        "whitelist": whitelist,
    }

    accepted, flagged = [], []
    for row in parsed_rows:
        key = (row[0], row[1])
        if key in seen:
            continue  # already have it
        flags = validate_poll(row, poll_keys, stats, params)
        if flags:
            flagged.append({"row": row, "flags": flags})
        else:
            accepted.append(row)
            seen.add(key)

    if accepted:
        merged = existing + accepted
        merged.sort(key=lambda x: x[1])
        data.setdefault(name, {})["polls"] = merged
        latest = merged[-1]
        data[name]["updated"] = (f"Last updated: <b>{_human_date(latest[1])}</b> "
                                 f"(latest poll \u00b7 {latest[0]})")
    if flagged:
        review[name] = flagged

    return len(accepted), len(flagged)


def _human_date(iso):
    d = dt.datetime.strptime(iso, "%Y-%m-%d")
    return d.strftime("%-d %B %Y") if sys.platform != "win32" else d.strftime("%#d %B %Y")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--country", help="scrape only this country key")
    ap.add_argument("--dry-run", action="store_true", help="parse + validate, do not write")
    args = ap.parse_args()

    config = load_json(CONFIG_FILE)
    data = load_json(DATA_FILE, default={})
    if config is None:
        sys.exit("scraper_config.json not found")

    countries = config["countries"]
    if args.country:
        countries = {args.country: countries[args.country]}

    review, changed, any_success = {}, False, False
    for name, cfg in countries.items():
        print(f"\n=== {name} ===")
        try:
            rows, warns = parse_country(cfg)
            for w in warns:
                print("  WARN", w)
            n_ok, n_flag = merge_country(name, cfg, rows, data, review)
            any_success = True
            changed = changed or n_ok > 0
            print(f"  parsed {len(rows)} rows · {n_ok} new accepted · {n_flag} flagged for review")
        except Exception as exc:                       # fail safe per country
            print(f"  ERROR {exc}")

    if review:
        REVIEW_DIR.mkdir(exist_ok=True)
        stamp = dt.date.today().isoformat()
        save_json(REVIEW_DIR / f"quarantine_{stamp}.json", review)
        print(f"\n{sum(len(v) for v in review.values())} poll(s) quarantined "
              f"-> review/quarantine_{stamp}.json  (bring these to chat to adjudicate)")

    if args.dry_run:
        print("\n[dry-run] data.json not written")
    elif changed and any_success:
        save_json(DATA_FILE, data)
        print("\ndata.json updated")
    else:
        print("\nno new clean polls — data.json unchanged")


if __name__ == "__main__":
    main()
