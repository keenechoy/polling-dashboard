"""
Validation for the polling scraper.

The split is deliberate: these checks DETECT anomalies automatically; a human
(you, in chat) RESOLVES them. Anything that returns a non-empty flag list is
quarantined rather than merged, and the dashboard keeps showing the last clean
dataset. No judgement about *why* a poll looks odd is made here.

Each check returns a short human-readable string when it trips, else None.
"""

import datetime as dt


class RollingStats:
    """Rolling per-party averages over the most recent `window` existing polls."""

    def __init__(self, existing_rows, poll_keys, window=5):
        self.keys = poll_keys
        recent = existing_rows[-window:] if existing_rows else []
        self.avg = {}
        for i, key in enumerate(poll_keys):
            vals = [r[i + 2] for r in recent if isinstance(r[i + 2], (int, float))]
            self.avg[key] = sum(vals) / len(vals) if vals else None

    def rolling(self, key):
        return self.avg.get(key)


def _check_sum(row, tolerance):
    total = sum(v for v in row[2:] if isinstance(v, (int, float)))
    if abs(total - 100.0) > tolerance:
        return f"base-consistency: shares sum to {total:.1f}% (expected ~100 \u00b1{tolerance})"
    return None


def _check_outlier(row, poll_keys, stats, threshold):
    hits = []
    for i, key in enumerate(poll_keys):
        base = stats.rolling(key)
        if base is None:
            continue
        move = abs(row[i + 2] - base)
        if move > threshold:
            hits.append(f"{key} {row[i + 2]:.1f} vs rolling {base:.1f} (\u0394{move:.1f})")
    if hits:
        return "outlier: " + "; ".join(hits)
    return None


def _check_duplicate(row, stats, existing_keys):
    if (row[0], row[1]) in existing_keys:
        return f"duplicate: {row[0]} {row[1]} already present"
    return None


def _check_date(row, max_age_days):
    try:
        d = dt.datetime.strptime(row[1], "%Y-%m-%d").date()
    except ValueError:
        return f"date-sanity: unparseable date '{row[1]}'"
    today = dt.date.today()
    if d > today:
        return f"date-sanity: fieldwork date {row[1]} is in the future"
    if (today - d).days > max_age_days:
        return f"date-sanity: {row[1]} is older than {max_age_days} days"
    return None


def _check_pollster(row, whitelist):
    if whitelist and not any(w.lower() in row[0].lower() for w in whitelist):
        return f"pollster: '{row[0]}' not on the whitelist {whitelist}"
    return None


def validate_poll(row, poll_keys, stats, params):
    """Run every check; return the list of flags that tripped (empty = clean)."""
    flags = []
    for check in (
        _check_sum(row, params["sum_tolerance"]),
        _check_outlier(row, poll_keys, stats, params["outlier_threshold"]),
        _check_date(row, params["max_age_days"]),
        _check_pollster(row, params["whitelist"]),
    ):
        if check:
            flags.append(check)
    return flags
