from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from vgr_brand_index.score_index import (
    apply_movement,
    load_signal_csvs,
    percentile_rank,
    score_dimensions,
)


@pytest.fixture
def synthetic_signals(tmp_path):
    qids = [f"Q{i}" for i in range(10)]
    pageviews = pd.DataFrame({
        "qid": qids,
        "pv_level": np.arange(10, 110, 10, dtype=float),
        "pv_momentum": np.linspace(0.8, 1.7, 10),
        "n_editions": np.arange(1, 11, dtype=float),
    })
    gdelt = pd.DataFrame({
        "qid": qids,
        "gdelt_level": np.arange(10, 110, 10, dtype=float),
        "gdelt_volume": np.arange(10, 110, 10, dtype=float),
        "gdelt_tone": np.zeros(10),
        "gdelt_momentum": np.linspace(0.9, 1.8, 10),
    })
    trends = pd.DataFrame({
        "qid": qids,
        "trends_level": np.arange(10, 110, 10, dtype=float),
    })
    # Q5 deliberately lacks Trends: Demand must renormalize to Wikipedia only.
    trends.loc[5, "trends_level"] = np.nan
    pageviews.to_csv(tmp_path / "pageviews.csv", index=False, encoding="utf-8-sig")
    gdelt.to_csv(tmp_path / "gdelt.csv", index=False, encoding="utf-8-sig")
    trends.to_csv(tmp_path / "google_trends.csv", index=False, encoding="utf-8-sig")
    return tmp_path, qids


def test_percentile_rank_maps_observed_range_to_zero_and_one_hundred():
    values = pd.Series([10, 20, 20, 40], index=list("abcd"), dtype=float)
    got = percentile_rank(values)
    assert got["a"] == pytest.approx(0.0)
    assert got["b"] == pytest.approx(50.0)
    assert got["c"] == pytest.approx(50.0)
    assert got["d"] == pytest.approx(100.0)


def test_signal_csvs_missing_signal_renormalizes_and_heat_is_fixed_blend(synthetic_signals):
    metrics_dir, qids = synthetic_signals
    loaded = load_signal_csvs(metrics_dir)
    assert set(loaded["qid"]) == set(qids)

    base = pd.DataFrame({
        "qid": qids,
        "display": [f"Brand {i}" for i in range(10)],
        # Editorial raw values share the same ordering as the other signals.
        "ed_count": np.arange(1, 11, dtype=float),
        "lyst_top20": np.zeros(10, dtype=bool),
    }).merge(loaded, on="qid", how="left")
    scored, _ = score_dimensions(base)

    missing = scored.loc[scored["qid"].eq("Q5")].iloc[0]
    assert np.isnan(missing["google_trends_pct"])
    assert missing["demand"] == pytest.approx(missing["wiki_pageviews_pct"])

    complete = scored.loc[scored["qid"].eq("Q9")].iloc[0]
    expected = (
        0.50 * complete["demand"]
        + 0.30 * complete["desire"]
        + 0.20 * complete["discovery"]
    )
    assert complete["heat"] == pytest.approx(expected)


def test_rank_delta_new_and_reentry_against_prior_snapshot():
    ranked = pd.DataFrame({
        "qid": ["Q1", "Q2", "Q3", "Q4"],
        "rank": [1, 2, 3, 4],
        "heat_now": [90.0, 80.0, 70.0, 60.0],
    })
    previous = {
        "Q1": {"rank": 3, "heat": 75.0},
        "Q2": {"rank": 1, "heat": 95.0},
    }
    got = apply_movement(ranked, previous, seen_before={"Q3"}).set_index("qid")
    assert got.loc["Q1", "delta_rank"] == 2
    assert got.loc["Q2", "delta_rank"] == -1
    assert got.loc["Q1", "status"] == ""
    assert got.loc["Q3", "status"] == "RE-ENTRY"
    assert got.loc["Q4", "status"] == "NEW"
    assert np.isnan(got.loc["Q3", "delta_rank"])
