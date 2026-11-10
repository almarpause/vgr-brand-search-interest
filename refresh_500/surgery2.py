"""Roster surgery 2 (2026-09-22): make the 500 brands 500 DIFFERENT brands.

The feed carried four brands twice (a parent-group item and a brand item that
both display as the same name), so only 496 were distinct. This step:
  1. drops the lower-scoring item of each duplicate pair (same brand, two Q-IDs)
     and two Wikidata items that are not brands at all ("streetwear", a style,
     rank 80; "garment factory", a kind of factory);
  2. adds six brands from Pau's list (Tous, DeFacto, LC Waikiki, Halara,
     Nude Project, Scuffers, Cold Culture, Blue Banana, Gods -- taken in that
     order, skipping LC Waikiki and Nude Project, already in the index);
  3. re-scores the 500 with the refresh engine (rebuild_roster.score).

The same 500 is the roster of the VGR Social Audience Index (social-leaders).

Idempotent: it applies on top of whatever index_500_refreshed.csv holds, so it
must run AFTER merge_and_rebuild.py (which rebuilds from the pre-surgery-1
baseline). Re-running it is safe.

  python refresh_500/surgery2.py collect   # Wikidata breadth + pageviews for the adds
  python refresh_500/surgery2.py apply     # remove/add/re-score, write the CSV

Halara, Scuffers and Blue Banana have no Wikidata item, so they carry a local
key (VGR-...), no pageviews and no breadth; it scores on Google Trends/News only once collected
(until then it scores 0, as Nude Project did after surgery 1).
"""
import sys, csv, json, statistics, urllib.request
from pathlib import Path
from collections import defaultdict
import numpy as np, pandas as pd

ROOT = Path(r"C:\Users\aresi\Claude\code\brand-index")
R = ROOT / "refresh_500"
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(R))
CSV = R / "index_500_refreshed.csv"
WIKI = R / "new_brands2_wiki.csv"
TRENDS_ITEMS = R / "trends_stagger" / "items"      # collect_one_trend.py outputs, one CSV per QID

# duplicate pairs: (keep, drop). Keep = higher interest_index in the 2026-09 feed.
DUPES = {
    "Q110225120": ("Max Mara", "Q1151774", "Max Mara Fashion Group item; brand item Q1151774 kept"),
    "Q5175717": ("Cotton On", "Q113992257", "brand item; Cotton On Group Q113992257 scores higher, kept"),
    "Q11297188": ("Kapital", "Q119149859", "second Kapital item; Q119149859 scores higher, kept"),
    "Q3305660": ("Tailored Brands", "Q108739951", "second Tailored Brands item; Q108739951 scores higher, kept"),
}
# not brands: generic concepts that entered via the Wikidata universe
NOT_BRANDS = {
    "Q2072372": ("streetwear", "a style of clothing, not a brand"),
    "Q55748823": ("garment factory", "a kind of factory, not a brand"),
}
# adds: qid -> (display name, Google Trends token pinned from Wikidata P646/P2671, else keyword)
ADDS = {
    "Q3815128": ("Tous", "/m/0c41_60"),
    "Q6059861": ("DeFacto", "/g/11r80vmfn"),
    "Q138573425": ("Cold Culture", "Cold Culture"),
    "VGR-HALARA": ("Halara", "Halara"),
    "VGR-SCUFFERS": ("Scuffers", "Scuffers"),
    "VGR-BLUEBANANA": ("Blue Banana", "Blue Banana Brand"),
}
BASKET = ["Nike", "Adidas", "Zara", "H&M", "Hermès", "Louis Vuitton", "Gucci", "Uniqlo"]


def num(x):
    try:
        f = float(x)
        return None if np.isnan(f) else f
    except (TypeError, ValueError):
        return None


def collect():
    from vgr_brand_index.pageviews import PageviewsClient, trailing_12_month_window
    from vgr_brand_index.wikidata import wikipedia_edition, USER_AGENT
    win = trailing_12_month_window()
    pv = PageviewsClient(ROOT / "cache" / "pageviews")
    rows = []
    for qid, (name, _) in ADDS.items():
        if not qid.startswith("Q"):
            rows.append({"qid": qid, "brand": name, "description": "Spanish fashion brand (no Wikidata item)" if name != "Halara" else "activewear brand (no Wikidata item)",
                         "sitelinks": 0, "en_title": name, "n_wikipedias": 0,
                         "pv_median": 0, "pv_latest": 0, "months": 0})
            print(f"  {name:<14} no Wikidata item -> breadth 0, pageviews 0")
            continue
        url = f"https://www.wikidata.org/wiki/Special:EntityData/{qid}.json"
        ent = json.load(urllib.request.urlopen(
            urllib.request.Request(url, headers={"User-Agent": USER_AGENT}), timeout=30))["entities"][qid]
        sl = ent.get("sitelinks", {})
        editions = {wikipedia_edition(s): v.get("title", "") for s, v in sl.items() if wikipedia_edition(s)}
        monthly = defaultdict(int)
        for lang, title in editions.items():
            for ts, v in pv.fetch(title, lang, win[0], win[1], "monthly", cache_only=False):
                monthly[ts[:7]] += v
        series = [monthly[k] for k in sorted(monthly)]
        desc = (ent.get("descriptions", {}).get("en") or ent.get("descriptions", {}).get("es") or {}).get("value", "")
        rows.append({"qid": qid, "brand": name, "description": desc or "Spanish streetwear brand",
                     "sitelinks": len(sl), "en_title": editions.get("en", name),
                     "n_wikipedias": len(editions),
                     "pv_median": int(statistics.median(series)) if series else 0,
                     "pv_latest": series[-1] if series else 0, "months": len(series)})
        r = rows[-1]
        print(f"  {name:<14} sitelinks={r['sitelinks']:>3} n_wiki={r['n_wikipedias']:>2} "
              f"pv_median={r['pv_median']:>7,} ({r['months']}mo)")
    with open(WIKI, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader(); w.writerows(rows)
    print("wrote", WIKI)


def trends_for(qid):
    """Trends/News basket-1000 levels from collect_one_trend.py, if it has run for this key."""
    p = TRENDS_ITEMS / f"{qid}.csv"
    if not p.exists():
        return None, None
    r = next(csv.DictReader(open(p, encoding="utf-8")))
    return num(r.get("trends_level_basket1000")), num(r.get("news_level_basket1000"))


def apply():
    from rebuild_roster import score
    df = pd.read_csv(CSV)
    cols = list(df.columns)
    before = df.set_index("qid")
    assert len(df) == 500, len(df)

    drop = set(DUPES) | set(NOT_BRANDS) | set(ADDS)                    # idempotent
    df = df[~df["qid"].isin(drop)].copy()
    for keep in {v[1] for v in DUPES.values()}:
        assert keep in set(df["qid"]), f"kept item {keep} missing"
    wiki = pd.read_csv(WIKI).set_index("qid")
    rows = []
    for qid, (name, _) in ADDS.items():
        w = wiki.loc[qid]
        t, n = trends_for(qid)
        pv_median = num(w["pv_median"]) or 0.0
        row = {c: np.nan for c in cols}
        row.update({"qid": qid, "brand": name, "description": w["description"],
                    "sitelinks": float(w["sitelinks"]), "en_title": w["en_title"],
                    "n_wikipedias": int(w["n_wikipedias"]), "pv_median": pv_median,
                    "trends_score": t if t else np.nan, "news_score": n if n else np.nan,
                    "wiki_latest": num(w["pv_latest"]), "elig_pv": pv_median > 0,
                    "elig_trends": bool(t and t > 0), "elig_news": bool(n and n > 0)})
        rows.append(row)
    df = pd.concat([df, pd.DataFrame(rows)], ignore_index=True)
    assert len(df) == 500, len(df)
    scored = score(df)[cols]

    # integrity gates --------------------------------------------------------------
    assert scored["qid"].is_unique, "duplicate Q-ID"
    names = scored["brand"].str.strip().str.casefold()
    assert names.is_unique, f"duplicate names: {sorted(set(names[names.duplicated()]))}"
    for b in BASKET:
        o = before.loc[before["brand"] == b, "interest_index"]
        s = scored.loc[scored["brand"] == b, "interest_index"]
        print(f"  basket {b:<14} {o.iloc[0]:>6} -> {s.iloc[0]:>6}")
    print("\nremoved:")
    for q, (name, keep, why) in DUPES.items():
        if q in before.index:
            print(f"  {name:<16} {q} (was rank {int(before.loc[q, 'rank'])}) -- {why}")
    for q, (name, why) in NOT_BRANDS.items():
        if q in before.index:
            print(f"  {name:<16} {q} (was rank {int(before.loc[q, 'rank'])}) -- {why}")
    print("added:")
    for qid in ADDS:
        r = scored[scored["qid"] == qid].iloc[0]
        print(f"  rank {int(r['rank']):>3}  {r['brand']:<14} interest={r['interest_index']:>6} "
              f"sources={r['sources'] or 'none yet'}")
    scored.to_csv(CSV, index=False)
    print(f"\nwrote {CSV.name}: {len(scored)} rows, {scored['brand'].nunique()} distinct brands")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    {"collect": collect, "apply": apply}[sys.argv[1]]()
