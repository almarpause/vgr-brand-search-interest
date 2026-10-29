from pathlib import Path

import httpx

from vgr_brand_index.gdelt import GdeltClient
from vgr_brand_index.pageviews import PageviewsClient
from vgr_brand_index.signals import gather_signals
from vgr_brand_index.universe import UniverseItem


def test_gdelt_connect_timeout_gives_up_without_raising(tmp_path, monkeypatch) -> None:
    client = GdeltClient(tmp_path / "gdelt", min_interval=0, jitter=0, max_retries=2)
    monkeypatch.setattr("vgr_brand_index.gdelt.time.sleep", lambda _: None)

    def timeout(*args, **kwargs):
        raise httpx.ConnectTimeout("boom")

    monkeypatch.setattr(client._client, "get", timeout)
    result = client._get(
        {"query": '"Test Brand"'}, "test-key", giveup_target=("Q1", "Test Brand")
    )

    assert result is None
    assert client.drain_targets() == [{"qid": "Q1", "label": "Test Brand"}]
    client.close()


def test_pageviews_connect_timeout_returns_empty(tmp_path, monkeypatch) -> None:
    client = PageviewsClient(tmp_path / "pageviews", min_interval=0, max_retries=2)
    monkeypatch.setattr("vgr_brand_index.pageviews.time.sleep", lambda _: None)

    def timeout(*args, **kwargs):
        raise httpx.ConnectTimeout("boom")

    monkeypatch.setattr(client._client, "get", timeout)

    assert client.fetch("Test Brand", "en", "20250101", "20250131") == []
    assert not list((tmp_path / "pageviews").glob("*.json"))
    client.close()


def test_gather_signals_skips_one_brand_when_gdelt_raises() -> None:
    item = UniverseItem("Q1", "Test Brand", "brand", 3, "Test Brand")

    class FakePageviews:
        def fetch(self, *args, **kwargs):
            return [("2025010100", 10)]

    class BrokenGdelt:
        def monthly_total(self, *args, **kwargs):
            raise RuntimeError("gdelt exploded")

    df = gather_signals(
        [item], {"Q1": {"en": "Test Brand"}}, FakePageviews(), BrokenGdelt(), None,
        ("20250101", "20251231"), cache_only=False, do_trends=False,
    )

    assert list(df.index) == ["Q1"]
    assert df.at["Q1", "brand"] == "Test Brand"
    assert df.at["Q1", "pv_12mo"] == 0
    assert df.at["Q1", "gdelt_12mo"] is None
    assert not df.at["Q1", "elig_gdelt"]
