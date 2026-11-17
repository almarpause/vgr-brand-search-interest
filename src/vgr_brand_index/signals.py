"""Gather the per-brand attention signals into one table.

One function, two modes:
  * `cache_only=True`  — the scoring pass. Reads only what the nightly fetches
    have already cached; a brand missing a source is simply flagged ineligible.
  * `cache_only=False` — the fetch pass. Actually hits the APIs (used by
    fetch_nightly on one shard at a time, so it stays gentle).

Signals: Wikipedia pageviews summed across all editions, GDELT monthly news
volume, and Google Trends search interest. Eligibility is explicit per source so
the composite can renormalise over what is available — missing is missing.
"""

from __future__ import annotations

import statistics
from collections import defaultdict

import pandas as pd

from .gdelt import GdeltClient
from .pageviews import PageviewsClient
from .trends import TrendsClient
from .universe import UniverseItem


# Single-word brand names that ARE common English words — string-search signals
# can't tell the brand from the word, so those sources are dropped for them.
# (Distinctive short names like Nike/Zara/Puma/Fila are NOT here — length is not
# the test, being an actual common word is.)
_COMMON_WORD_BRANDS = {
    "we", "on", "gap", "cos", "next", "head", "boss", "coach", "guess", "theory",
    "bench", "supreme", "stadium", "mango", "mark", "marks", "reserved", "champion",
    "element", "obey", "everyday", "common", "pull", "off", "true", "life", "diesel",
    "weekday", "quiet", "case", "born",
    # Generic single words whose Google-Trends topic mid mis-resolves to a huge
    # unrelated entity (observed in the top 50 when the pageviews gate was loosened).
    # Stripping their string/topic sources leaves them to rank on pageviews alone.
    "alma", "ladies", "away", "australian", "island", "blue", "george", "about",
    "only", "you",
}

# Legal-entity / corporate-form suffixes. A brand entered under its holding or
# legal name ("Adidas AG", "Blue Inc", "Island Company") has no Wikipedia
# pageviews of its own — those sit under the brand's common name — and its Trends /
# Reddit queries resolve to the parent company or the ordinary word. So the
# string/topic sources are drop-only for these; a real-attention corporate name is
# unaffected because pageviews are never gated by ambiguity (elig_pv ignores amb).
_CORP_SUFFIXES = {
    "ag", "sa", "nv", "plc", "gmbh", "inc", "corp", "corporation", "ltd", "limited",
    "llc", "group", "holding", "holdings", "company", "bv", "spa", "srl", "kgaa",
    "sas", "oyj",
}


def _is_ambiguous_name(label: str) -> bool:
    t = str(label).strip().lower()
    if " " not in t and "-" not in t:
        # Very short single tokens ("GU", "AG", "Y3") are too short for a string
        # search or a Trends topic to resolve reliably — they collide with acronyms,
        # game terms and non-Latin homographs, inflating the signal wildly (e.g.
        # GU's Trends came back at 75x Gucci). Drop the string/topic sources; they
        # still rank on pageviews, which are Q-ID-keyed and unambiguous.
        core = t.replace("'", "").replace(".", "")
        if len(core) <= 2:
            return True
        return t in _COMMON_WORD_BRANDS
    # Multi-word names: judge by COMPOSITION, not by having a space. A name that
    # ends in a legal-entity suffix is the corporate/holding form (it mis-resolves
    # to the parent topic or is a registry duplicate of the common name); a name
    # whose every token is an ordinary word can't be told from language ("About
    # You", "Blue Inc"). Distinctive multi-word brands (Louis Vuitton, Air Jordan)
    # have no common/suffix tokens and pass through untouched.
    toks = [w for w in t.replace("-", " ").replace(".", "").split() if w]
    if not toks:
        return False
    if toks[-1] in _CORP_SUFFIXES:
        return True
    generic = _COMMON_WORD_BRANDS | _CORP_SUFFIXES
    if all(w in generic for w in toks):
        return True
    return False


def _pv_monthly(
    editions: dict[str, str], pv: PageviewsClient, window, cache_only: bool
) -> dict[str, int]:
    """All-edition pageviews per calendar month: {YYYY-MM: total views}."""
    monthly: dict[str, int] = defaultdict(int)
    for lang, title in editions.items():
        for ts, v in pv.fetch(title, lang, window[0], window[1], "monthly", cache_only=cache_only):
            monthly[ts[:7]] += v
    return monthly


def pv_stats(monthly: dict[str, int]) -> tuple[int, float, int]:
    """(median monthly, recent-3-month mean, 12-month total) from the series.

    The MEDIAN is the level signal — robust to one-off spikes (a designer's death,
    a scandal, a viral collab) that a 12-month sum would bank as lasting attention.
    """
    series = [monthly[k] for k in sorted(monthly)]
    if not series:
        return 0, 0.0, 0
    median = int(statistics.median(series))
    recent = statistics.mean(series[-3:]) if len(series) >= 3 else statistics.mean(series)
    return median, round(recent, 1), sum(series)


def gather_signals(
    items: list[UniverseItem],
    editions_by_qid: dict[str, dict[str, str]],
    pv: PageviewsClient,
    gdelt: GdeltClient | None,
    trends: TrendsClient | None,
    window: tuple[str, str],
    *,
    reddit=None,
    cache_only: bool = True,
    do_trends: bool = True,
    verbose: bool = False,
) -> pd.DataFrame:
    """Return a DataFrame indexed by qid with the raw signals + eligibility flags.

    Columns: brand, description, sitelinks, en_title, pv_12mo, gdelt_12mo,
    reddit_vol, trends_score, elig_pv/gdelt/reddit/trends.
    """
    gstart, gend = window
    rows: list[dict] = []
    for n, item in enumerate(items, 1):
        try:
            editions = editions_by_qid.get(item.qid, {})
            monthly = _pv_monthly(editions, pv, window, cache_only)
            pv_median, pv_recent, pv_total = pv_stats(monthly)

            gdelt_total: int | None = None
            if gdelt is not None:
                gdelt_total = gdelt.monthly_total(
                    item.label, gstart, gend, cache_only=cache_only, qid=item.qid,
                )

            reddit_vol: int | None = None
            if reddit is not None:
                reddit_vol = reddit.fetch(item.label, cache_only=cache_only)

            row = {
                "qid": item.qid,
                "brand": item.label,
                "description": item.description,
                "sitelinks": item.sitelinks,
                "en_title": item.en_title,
                "pv_median": pv_median,
                "pv_recent": pv_recent,
                "pv_12mo": pv_total,
                "gdelt_12mo": gdelt_total,
                "reddit_vol": reddit_vol,
            }
        except Exception as exc:
            print(f"    [signals] skipped {item.qid} {item.label}: {exc}", flush=True)
            row = {
                "qid": item.qid,
                "brand": item.label,
                "description": item.description,
                "sitelinks": item.sitelinks,
                "en_title": item.en_title,
                "pv_median": 0,
                "pv_recent": 0.0,
                "pv_12mo": 0,
                "gdelt_12mo": None,
                "reddit_vol": None,
            }
        rows.append(row)
        if verbose and (n % 200 == 0 or n == len(items)):
            print(f"    signals {n}/{len(items)}")

    df = pd.DataFrame(rows).set_index("qid")

    # Trends is anchor-chained in batches, and a batch's cache key IS its ordered
    # token list — so the fetch pass and the scoring pass MUST batch in the exact
    # same order or every cached batch is orphaned and Trends silently scores 0
    # (which is what happened). The nightly fetch batches in the universe's natural
    # order (`[i.label for i in viable]`), so score in that same order — i.e. the
    # items' order, which is this frame's natural (unsorted) order. It is also
    # independent of pageviews, so batch keys stay valid month to month instead of
    # drifting every time pv changes.
    trends_by_label: dict[str, float | None] = {}
    if trends is not None and do_trends:
        labels_stable = df["brand"].tolist()
        trends_by_label = trends.score_brands(labels_stable, cache_only=cache_only)
    df["trends_score"] = df["brand"].map(lambda b: trends_by_label.get(b))

    # Eligibility: a source counts for a brand only when it returned a real value.
    # Pageviews are keyed on the Wikidata Q-ID, so they are never ambiguous. The
    # string-search sources (News, Search, Reddit) are made INELIGIBLE for
    # common-word brand names ("WE", "On", "Gap", "Guess", "Theory", "Bench"…),
    # where the query counts unrelated posts about the word, not the brand.
    amb = df["brand"].map(_is_ambiguous_name)
    df["elig_pv"] = (df["pv_median"].fillna(0) > 0)
    df["elig_gdelt"] = df["gdelt_12mo"].notna() & ~amb
    df["elig_trends"] = df["trends_score"].notna() & ~amb
    df["elig_reddit"] = df["reddit_vol"].notna() & ~amb
    return df
