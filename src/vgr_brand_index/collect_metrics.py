"""Collect the deterministic Stage-2 Tier-1 metrics for a frozen roster.

Usage::

    python -m vgr_brand_index.collect_metrics --month 2026-11 [--limit 10]

Only the unblocked backbone is collected here: Wikipedia pageviews and GDELT.
Each output is checkpointed one Q-ID at a time and written with ``utf-8-sig``.
"""

from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from .gdelt import GdeltClient
from .pageviews import PageviewsClient

ROOT = Path(__file__).resolve().parents[2]
ROSTER_DIR = ROOT / "output" / "roster"
METRICS_ROOT = ROOT / "output" / "metrics"
CACHE = ROOT / "cache"
CSV_ENCODING = "utf-8-sig"
FASHION_CONTEXT = "(fashion OR clothing OR apparel)"
HALF_LIFE_DAYS = 42.0


def collection_windows(month: str) -> tuple[tuple[str, str], tuple[str, str]]:
    """Return deterministic trailing 3- and 12-month complete-month windows."""
    try:
        anchor = datetime.strptime(month, "%Y-%m").date().replace(day=1)
    except ValueError as exc:
        raise ValueError("month must be YYYY-MM") from exc
    end = anchor - timedelta(days=1)

    def step_months_back(d: date, count: int) -> date:
        out = d.replace(day=1)
        for _ in range(count):
            out = (out - timedelta(days=1)).replace(day=1)
        return out

    start3 = step_months_back(end, 2)
    start12 = step_months_back(end, 11)
    fmt = lambda d: d.strftime("%Y%m%d")
    return (fmt(start3), fmt(end)), (fmt(start12), fmt(end))


def _daily_stats(values: dict[date, float], start: date, end: date) -> tuple[float, float, float]:
    """Return recent weekly mean, recency-weighted weekly level, and momentum.

    Missing calendar days inside a successfully fetched series are true zeroes.
    ``recent`` is the mean of the last four weekly totals.  The baseline is all
    preceding days in the three-month window, expressed on the same weekly scale.
    """
    days = pd.date_range(start, end, freq="D")
    series = np.array([float(values.get(ts.date(), 0.0)) for ts in days], dtype=float)
    if not len(series):
        return 0.0, 0.0, 0.0
    recent_days = min(28, len(series))
    recent = float(series[-recent_days:].sum() / recent_days * 7.0)
    baseline = series[:-recent_days]
    baseline_weekly = float(baseline.mean() * 7.0) if len(baseline) else 0.0
    momentum = recent / baseline_weekly if baseline_weekly > 0 else (1.0 if recent == 0 else math.nan)
    age = np.arange(len(series) - 1, -1, -1, dtype=float)
    weights = np.power(0.5, age / HALF_LIFE_DAYS)
    level = float(np.average(series, weights=weights) * 7.0)
    return recent, level, momentum


def _load_roster(limit: int | None = None) -> pd.DataFrame:
    path = ROSTER_DIR / "brand_roster_latest.csv"
    roster = pd.read_csv(path, encoding=CSV_ENCODING, dtype={"qid": str})
    roster = roster[roster["status"].fillna("active").eq("active")].copy()
    if limit is not None:
        roster = roster.head(limit).copy()
    return roster


def _load_titles() -> dict[str, dict[str, str]]:
    path = ROSTER_DIR / "roster_titles_latest.json"
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _read_checkpoint(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path, encoding=CSV_ENCODING, dtype={"qid": str})


def _checkpoint(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).drop_duplicates("qid", keep="last").to_csv(
        path, index=False, encoding=CSV_ENCODING,
    )


def _pv_monthly(
    editions: dict[str, str], client: PageviewsClient,
    window12: tuple[str, str], cache_only: bool = False,
) -> dict[str, float]:
    """Monthly total user pageviews summed across all language editions.

    Returns {YYYYMM: total_views} over the trailing-12-month window. The
    Wikimedia timestamp is ``YYYYMMDDHH``; the first six chars are the month.
    """
    totals: dict[str, float] = defaultdict(float)
    for lang, title in editions.items():
        for timestamp, value in client.fetch(
            title, lang, window12[0], window12[1], "monthly", cache_only=cache_only,
        ):
            totals[timestamp[:6]] += value
    return dict(totals)


def pv_stats(monthly: dict[str, float]) -> tuple[float, float, float]:
    """(median, mean, total) of the monthly pageview totals."""
    values = list(monthly.values())
    if not values:
        return 0.0, 0.0, 0.0
    return (
        float(np.median(values)),
        float(np.mean(values)),
        float(np.sum(values)),
    )


def _pageview_row(
    brand, editions: dict[str, str], client: PageviewsClient,
    window3: tuple[str, str], window12: tuple[str, str], start3: date, end3: date,
) -> dict:
    monthly = _pv_monthly(editions, client, window12, cache_only=False)
    pv_median, _, _ = pv_stats(monthly)
    daily: dict[date, float] = defaultdict(float)
    for lang, title in editions.items():
        for timestamp, value in client.fetch(
            title, lang, window3[0], window3[1], "daily", cache_only=False,
        ):
            daily[datetime.strptime(timestamp[:8], "%Y%m%d").date()] += value
    recent, level, momentum = _daily_stats(daily, start3, end3)
    edition_value = getattr(brand, "n_editions", None)
    n_editions = len(editions) if pd.isna(edition_value) else int(edition_value)
    return {
        "qid": str(brand.qid), "display": brand.display, "pv_median": pv_median,
        "pv_recent": round(recent, 3), "pv_level": round(level, 3),
        "pv_momentum": round(momentum, 6) if not math.isnan(momentum) else np.nan,
        "n_editions": n_editions,
    }


def collect_pageviews(
    roster: pd.DataFrame, titles: dict[str, dict[str, str]], out_path: Path,
    window3: tuple[str, str], window12: tuple[str, str],
) -> pd.DataFrame:
    existing = _read_checkpoint(out_path)
    rows = existing.to_dict("records") if not existing.empty else []
    done = set(existing.get("qid", pd.Series(dtype=str)).astype(str))
    client = PageviewsClient(CACHE / "pageviews")
    start3 = datetime.strptime(window3[0], "%Y%m%d").date()
    end3 = datetime.strptime(window3[1], "%Y%m%d").date()
    try:
        for pos, brand in enumerate(roster.itertuples(index=False), 1):
            qid = str(brand.qid)
            if qid in done:
                continue
            editions = titles.get(qid, {})
            try:
                rows.append(_pageview_row(
                    brand, editions, client, window3, window12, start3, end3,
                ))
            except Exception as exc:
                print(f"  pageviews retry-later {pos}/{len(roster)} {brand.display}: {exc}",
                      flush=True)
                continue
            _checkpoint(out_path, rows)
            print(f"  pageviews {pos}/{len(roster)} {brand.display}", flush=True)
    finally:
        client.close()
    return _read_checkpoint(out_path)


def collect_gdelt(
    roster: pd.DataFrame, out_path: Path, window3: tuple[str, str],
) -> pd.DataFrame:
    existing = _read_checkpoint(out_path)
    rows = existing.to_dict("records") if not existing.empty else []
    done = set(existing.get("qid", pd.Series(dtype=str)).astype(str))
    client = GdeltClient(CACHE / "gdelt")
    start = datetime.strptime(window3[0], "%Y%m%d").date()
    end = datetime.strptime(window3[1], "%Y%m%d").date()
    try:
        for pos, brand in enumerate(roster.itertuples(index=False), 1):
            qid = str(brand.qid)
            if qid in done:
                continue
            query = f'"{brand.display}" {FASHION_CONTEXT}'
            try:
                volume = client.fetch_timeline(
                    query, *window3, mode="timelinevolraw", qid=qid,
                )
                tone = client.fetch_timeline(
                    query, *window3, mode="timelinetone", qid=qid,
                )
            except Exception as exc:
                print(f"  gdelt retry-later {pos}/{len(roster)} {brand.display}: {exc}",
                      flush=True)
                continue
            if volume is None or tone is None:
                print(f"  gdelt retry-later {pos}/{len(roster)} {brand.display}", flush=True)
                continue
            by_day: dict[date, float] = defaultdict(float)
            for timestamp, value in volume:
                by_day[datetime.strptime(timestamp, "%Y-%m-%d").date()] += value
            _, level, momentum = _daily_stats(by_day, start, end)
            tone_values = [value for _, value in tone]
            rows.append({
                "qid": qid, "display": brand.display,
                "gdelt_volume": round(sum(value for _, value in volume), 3),
                "gdelt_tone": round(float(np.mean(tone_values)), 6) if tone_values else np.nan,
                "gdelt_level": round(level, 3),
                "gdelt_momentum": round(momentum, 6) if not math.isnan(momentum) else np.nan,
                "query": query,
            })
            _checkpoint(out_path, rows)
            print(f"  gdelt {pos}/{len(roster)} {brand.display}", flush=True)
    finally:
        client.close()
    return _read_checkpoint(out_path)


def write_metrics_long(out_dir: Path) -> pd.DataFrame:
    records: list[dict] = []
    for signal, filename in (("wiki_pageviews", "pageviews.csv"), ("gdelt", "gdelt.csv")):
        path = out_dir / filename
        if not path.exists():
            continue
        frame = pd.read_csv(path, encoding=CSV_ENCODING, dtype={"qid": str})
        id_cols = {"qid", "display", "query"}
        for row in frame.to_dict("records"):
            for metric, value in row.items():
                if metric not in id_cols:
                    records.append({"qid": row["qid"], "signal": signal,
                                    "metric": metric, "value": value})
    long = pd.DataFrame(records, columns=["qid", "signal", "metric", "value"])
    long.to_csv(out_dir / "metrics_long.csv", index=False, encoding=CSV_ENCODING)
    return long


def collect_metrics(month: str, limit: int | None = None) -> dict[str, int]:
    roster = _load_roster(limit)
    titles = _load_titles()
    window3, window12 = collection_windows(month)
    out_dir = METRICS_ROOT / month
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"Tier-1 collection {month}: {len(roster)} brands; 3mo={window3[0]}..{window3[1]}")
    pageviews = collect_pageviews(roster, titles, out_dir / "pageviews.csv", window3, window12)
    gdelt = collect_gdelt(roster, out_dir / "gdelt.csv", window3)
    long = write_metrics_long(out_dir)
    counts = {"pageviews": len(pageviews), "gdelt": len(gdelt), "metrics_long": len(long)}
    print(f"Wrote {out_dir}: {counts}")
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--month", required=True, help="snapshot month, YYYY-MM")
    parser.add_argument("--limit", type=int, help="collect only the first N roster rows")
    args = parser.parse_args(argv)
    collect_metrics(args.month, args.limit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
