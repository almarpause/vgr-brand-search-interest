from __future__ import annotations

import pytest

from vgr_brand_index.benchmark_reconcile import (
    BenchmarkEntry,
    Resolution,
    assert_benchmark_coverage,
)
from vgr_brand_index.universe import UniverseItem, is_viable


def test_zero_sitelink_fashion_brand_is_viable_but_broad_root_is_not() -> None:
    precise = UniverseItem(
        "Q124984014", "Dries Van Noten", "fashion label", 0, "",
        frozenset({"instance:fashion_brand"}),
    )
    broad = UniverseItem(
        "Q1", "Broad company", "clothing company", 0, "",
        frozenset({"industry:Q11828862"}),
    )
    chain = UniverseItem(
        "Q2", "Store chain", "clothing store chain", 0, "",
        frozenset({"instance:clothing_store_chain"}),
    )
    assert is_viable(precise)
    assert not is_viable(broad)
    assert not is_viable(chain)
    assert is_viable(broad, frozenset({"Q1"}))


def test_coverage_accepts_approved_and_fails_loudly_for_missing() -> None:
    entries = [BenchmarkEntry("Chanel", "", "Lyst", "Q3-2026")]
    resolutions = {"Chanel": Resolution("Q218115", "Chanel", "French fashion house")}
    assert_benchmark_coverage(entries, resolutions, set(), frozenset({"Q218115"}))
    with pytest.raises(AssertionError, match=r"MISSING Chanel \(Q218115\)"):
        assert_benchmark_coverage(entries, resolutions, set(), frozenset())
