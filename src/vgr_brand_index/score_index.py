"""Score the monthly VGR Brand Search Interest metrics.

Expected per-signal CSV columns (only ``qid`` is universally required):

* pageviews.csv: ``pv_level``, ``pv_momentum``, ``pv_median``, ``n_editions``
* gdelt.csv: ``gdelt_volume``, ``gdelt_tone``, ``gdelt_level``, ``gdelt_momentum``
* google_trends.csv: ``trends_level`` (or ``google_trends_level``), momentum
* reddit.csv: ``reddit_level`` (or ``reddit_volume``), momentum
* tiktok.csv: ``tiktok_level`` (or ``tiktok_hashtag``), momentum
* meta.csv: ``meta_level`` (or ``meta_adlib_ads``), momentum

Additional signal files may be present; unknown columns are preserved on merge.
Missing signals remain missing and weights renormalize within each dimension.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from .normalize import combine_sources, inverse_variance_weights

ROOT = Path(__file__).resolve().parents[2]
METRICS_ROOT = ROOT / "output" / "metrics"
ROSTER_PATH = ROOT / "output" / "roster" / "brand_roster_latest.csv"
CSV_ENCODING = "utf-8-sig"
TOP_N = 500

# TODO tune once vs Lyst top-20 overlap, then freeze.
W = dict(demand=0.50, desire=0.30, discovery=0.20)

DIMENSION_SIGNALS = {
    "demand": ["wiki_pageviews", "google_trends"],
    "desire": ["reddit", "gdelt_tone_adj", "tiktok_hashtag", "editorial_authority"],
    "discovery": ["wiki_edition_count", "meta_adlib_ads"],
}


def percentile_rank(values: pd.Series) -> pd.Series:
    """Average-tie percentile ranks with the observed range mapped to 0..100."""
    numeric = pd.to_numeric(values, errors="coerce")
    out = pd.Series(np.nan, index=values.index, dtype=float)
    valid = numeric.dropna()
    if valid.empty:
        return out
    if len(valid) == 1 or valid.nunique() == 1:
        out.loc[valid.index] = 50.0
        return out
    ranks = valid.rank(method="average")
    out.loc[valid.index] = (ranks - ranks.min()) / (ranks.max() - ranks.min()) * 100.0
    return out


def _first_column(frame: pd.DataFrame, names: list[str]) -> pd.Series:
    for name in names:
        if name in frame:
            return pd.to_numeric(frame[name], errors="coerce")
    return pd.Series(np.nan, index=frame.index, dtype=float)


def load_signal_csvs(metrics_dir: Path) -> pd.DataFrame:
    """Outer-join every source CSV in a metrics directory on Q-ID."""
    skip = {"metrics_long.csv", "published_500.csv", "heat_ranking.csv"}
    merged: pd.DataFrame | None = None
    for path in sorted(metrics_dir.glob("*.csv")):
        if path.name in skip:
            continue
        frame = pd.read_csv(path, encoding=CSV_ENCODING, dtype={"qid": str})
        if "qid" not in frame:
            continue
        frame = frame.drop_duplicates("qid", keep="last")
        # Avoid duplicate descriptive fields while retaining every metric.
        frame = frame.drop(columns=[c for c in ("display", "brand", "query") if c in frame])
        merged = frame if merged is None else merged.merge(frame, on="qid", how="outer")
    return merged if merged is not None else pd.DataFrame(columns=["qid"])


def _editorial_fields(roster: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame({"qid": roster["qid"].astype(str)})
    source_count = roster.get("ed_sources", pd.Series("", index=roster.index)).fillna("").map(
        lambda value: len([x for x in str(value).split(",") if x.strip()])
    )
    out["ed_count"] = source_count.astype(float)
    out["lyst_top20"] = False
    candidates = [
        ROOT / "output" / "index_full_ranked.csv",
        ROOT / "output" / "editorial_discovery" / "gaps_scored.csv",
    ]
    for path in candidates:
        if not path.exists():
            continue
        frame = pd.read_csv(path, encoding=CSV_ENCODING, dtype={"qid": str})
        if "qid" not in frame:
            continue
        cols = [c for c in ("qid", "ed_count", "lyst_top20") if c in frame]
        if len(cols) == 1:
            continue
        frame = frame[cols].drop_duplicates("qid", keep="last").set_index("qid")
        qids = out["qid"]
        if "ed_count" in frame:
            mapped = qids.map(pd.to_numeric(frame["ed_count"], errors="coerce"))
            out["ed_count"] = mapped.fillna(out["ed_count"])
        if "lyst_top20" in frame:
            truth = frame["lyst_top20"].astype(str).str.lower().isin({"1", "true", "yes"})
            mapped = qids.map(truth)
            out["lyst_top20"] = mapped.fillna(out["lyst_top20"]).astype(bool)
    return out


def build_raw_signals(frame: pd.DataFrame) -> pd.DataFrame:
    """Create the six methodology signals plus editorial and edition breadth."""
    raw = pd.DataFrame(index=frame.index)
    raw["wiki_pageviews"] = _first_column(frame, ["pv_level", "wiki_pageviews_level", "pv_median"])
    raw["google_trends"] = _first_column(frame, ["google_trends_level", "trends_level", "trends_score"])
    raw["reddit"] = _first_column(frame, ["reddit_level", "reddit_volume", "reddit_vol"])
    gdelt_volume = _first_column(frame, ["gdelt_level", "gdelt_volume", "gdelt_12mo"])
    gdelt_tone = _first_column(frame, ["gdelt_tone"]).fillna(0.0)
    raw["gdelt_tone_adj"] = gdelt_volume * (1.0 + gdelt_tone / 10.0).clip(0.3, 1.0)
    raw["tiktok_hashtag"] = _first_column(frame, ["tiktok_level", "tiktok_hashtag", "tiktok_views"])
    raw["editorial_authority"] = (
        _first_column(frame, ["ed_count"]).clip(upper=4).fillna(0.0)
        + 2.0 * _first_column(frame, ["lyst_top20"]).fillna(0.0)
    )
    raw["wiki_edition_count"] = _first_column(frame, ["n_editions", "wiki_edition_count"])
    raw["meta_adlib_ads"] = _first_column(frame, ["meta_level", "meta_adlib_ads", "meta_ads"])
    return raw


def score_dimensions(frame: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, dict[str, float]]]:
    """Percentile-rank signals, then inverse-variance fuse each dimension."""
    out = frame.copy()
    raw = build_raw_signals(out)
    percentiles = {name: percentile_rank(raw[name]) for name in raw}
    for name, values in raw.items():
        out[f"{name}_raw"] = values
        out[f"{name}_pct"] = percentiles[name]

    all_weights: dict[str, dict[str, float]] = {}
    for dimension, names in DIMENSION_SIGNALS.items():
        available = [name for name in names if percentiles[name].notna().any()]
        if not available:
            out[dimension] = np.nan
            all_weights[dimension] = {}
            continue
        weights = inverse_variance_weights(percentiles, available)
        eligible = {name: percentiles[name].notna() for name in available}
        out[dimension] = combine_sources(
            {name: percentiles[name] for name in available}, eligible, weights,
        )
        all_weights[dimension] = weights

    # The three published dimension weights are fixed.  Unlike within-dimension
    # coverage, missing an entire dimension is not silently reweighted.
    parts = [out[name] * weight for name, weight in W.items()]
    out["heat"] = pd.concat(parts, axis=1).sum(axis=1, min_count=len(W))
    out["heat_level"] = out["heat"]
    out["heat_now"] = out["heat"]

    momentum_cols = [c for c in out if c.endswith("_momentum")]
    if momentum_cols:
        out["momentum"] = out[momentum_cols].apply(pd.to_numeric, errors="coerce").mean(axis=1)
    else:
        out["momentum"] = np.nan
    out["momentum_flag"] = np.where(out["momentum"] > 1.0, "RISING", "")
    return out, all_weights


def _previous_month(month: str) -> str:
    stamp = pd.Period(month, freq="M") - 1
    return str(stamp)


def _load_snapshot(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {str(qid): value for qid, value in data.items() if isinstance(value, dict)}


def apply_movement(
    ranked: pd.DataFrame, previous: dict[str, dict], seen_before: set[str] | None = None,
) -> pd.DataFrame:
    """Add prior-rank delta and NEW/RE-ENTRY tags to an already ranked frame."""
    seen_before = seen_before or set()
    out = ranked.copy()
    prev_rank = out["qid"].map(lambda qid: previous.get(str(qid), {}).get("rank"))
    out["previous_rank"] = pd.to_numeric(prev_rank, errors="coerce")
    out["delta_rank"] = out["previous_rank"] - out["rank"]
    out["status"] = [
        "" if str(qid) in previous else ("RE-ENTRY" if str(qid) in seen_before else "NEW")
        for qid in out["qid"]
    ]
    return out


def _incumbent_qids() -> set[str]:
    path = ROOT / "output" / "index_500.csv"
    if not path.exists():
        return set()
    frame = pd.read_csv(path, encoding=CSV_ENCODING, dtype={"qid": str})
    return set(frame.get("qid", pd.Series(dtype=str)).dropna().astype(str))


def load_keepers(path: Path | None) -> set[str]:
    if path is None:
        return set()
    if not path.exists():
        raise FileNotFoundError(path)
    if path.suffix.lower() == ".csv":
        frame = pd.read_csv(path, encoding=CSV_ENCODING, dtype=str)
        values: set[str] = set()
        for col in ("qid", "display", "brand", "name"):
            if col in frame:
                values.update(frame[col].dropna().str.strip())
        return values
    return {line.strip() for line in path.read_text(encoding="utf-8-sig").splitlines()
            if line.strip() and not line.lstrip().startswith("#")}


def select_published(scored: pd.DataFrame, keepers: set[str]) -> pd.DataFrame:
    out = scored.copy()
    incumbents = _incumbent_qids()
    out["in_pub500"] = out["qid"].astype(str).isin(incumbents).astype(int)
    out["select_score"] = out["heat_level"] + 4.0 * out["in_pub500"]
    out = out.sort_values(["select_score", "qid"], ascending=[False, True], na_position="last")
    eligible = out[out["select_score"].notna()].copy()
    selected = eligible.head(TOP_N).copy()
    keep_mask = out["qid"].astype(str).isin(keepers) | out["display"].astype(str).isin(keepers)
    forced = out[keep_mask & out["select_score"].notna()]
    for idx, row in forced.iterrows():
        if row["qid"] in set(selected["qid"]):
            continue
        droppable = selected[
            ~selected["qid"].astype(str).isin(keepers)
            & ~selected["display"].astype(str).isin(keepers)
        ]
        if droppable.empty:
            break
        selected = selected.drop(droppable.tail(1).index)
        selected = pd.concat([selected, out.loc[[idx]]])
    selected = selected.sort_values(["select_score", "qid"], ascending=[False, True])
    selected["published_rank"] = np.arange(1, len(selected) + 1)
    return selected


def run_scoring(month: str, keep_file: Path | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    metrics_dir = METRICS_ROOT / month
    roster = pd.read_csv(ROSTER_PATH, encoding=CSV_ENCODING, dtype={"qid": str})
    roster = roster[roster["status"].fillna("active").eq("active")].copy()
    editorial = _editorial_fields(roster)
    metrics = load_signal_csvs(metrics_dir)
    # The frozen roster is authoritative for descriptive/static fields such as
    # n_editions.  Dropping duplicates prevents pandas suffixes from hiding the
    # canonical column from the signal mapping below.
    metrics = metrics.drop(
        columns=[c for c in metrics.columns if c != "qid" and c in roster.columns],
        errors="ignore",
    )
    base = roster.merge(metrics, on="qid", how="left").merge(editorial, on="qid", how="left")

    interest_path = ROOT / "output" / "index_full_ranked.csv"
    if interest_path.exists():
        interest = pd.read_csv(interest_path, encoding=CSV_ENCODING, dtype={"qid": str})
        if "interest_index" in interest:
            base = base.merge(interest[["qid", "interest_index"]].drop_duplicates("qid"),
                              on="qid", how="left")
    scored, weights = score_dimensions(base)
    ranked = scored[scored["heat_now"].notna()].sort_values(
        ["heat_now", "qid"], ascending=[False, True]
    ).reset_index(drop=True)
    ranked["rank"] = np.arange(1, len(ranked) + 1)

    previous = _load_snapshot(METRICS_ROOT / _previous_month(month) / "snapshot.json")
    seen_before: set[str] = set()
    for path in METRICS_ROOT.glob("*/snapshot.json"):
        if path.parent.name < _previous_month(month):
            seen_before.update(_load_snapshot(path))
    ranked = apply_movement(ranked, previous, seen_before)

    keepers = load_keepers(keep_file)
    published = select_published(scored, keepers)
    metrics_dir.mkdir(parents=True, exist_ok=True)
    published.to_csv(metrics_dir / "published_500.csv", index=False, encoding=CSV_ENCODING)
    ranked.to_csv(metrics_dir / "heat_ranking.csv", index=False, encoding=CSV_ENCODING)
    snapshot = {
        str(row.qid): {"rank": int(row.rank), "heat": round(float(row.heat_now), 6)}
        for row in ranked.itertuples()
    }
    (metrics_dir / "snapshot.json").write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    print(f"Scored {len(roster)} roster rows; heat={len(ranked)}; published={len(published)}")
    print(f"Inverse-variance weights: {json.dumps(weights, sort_keys=True)}")
    for row in ranked.head(5).itertuples():
        print(f"  {row.rank:>3} {row.display:<28} HEAT={row.heat_now:.3f}")
    return published, ranked


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--month", required=True, help="snapshot month, YYYY-MM")
    parser.add_argument("--keep-file", type=Path, help="optional CSV/text force-keeper list")
    args = parser.parse_args(argv)
    run_scoring(args.month, args.keep_file)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
