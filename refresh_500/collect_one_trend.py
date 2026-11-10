"""Collect Google Trends (web) + News level for ONE roster-add brand.

Designed as a stagger-runner target: one invocation = one brand, so the paced
runner can space requests and stop on the first 429. Uses the SAME bridge-anchored
method as collect_new_trends.py, but with a 2-token batch [bridge, brand] (Trends
normalises within a batch, so brand/bridge * bridge_adidas100 is identical to the
5-token version). Cache-resumable: a re-run of an already-fetched brand is free.

Usage:  python collect_one_trend.py <QID> --out <path.csv>
Exit 0 and print "RESULT_OK" on a genuine write; exit 1 and print "RATE_LIMIT_BLOCK"
(a stagger block pattern) when Trends throttles, so the item is recorded, not faked.
"""
import sys, csv, argparse, json
from pathlib import Path

ROOT = Path(r"C:\Users\aresi\Claude\code\brand-index")
sys.path.insert(0, str(ROOT / "src"))
from vgr_brand_index.trends import TrendsClient, TrendsUnavailable

CACHE = ROOT / "cache" / "trends"
WEB_RESCALE, NEWS_RESCALE = 22.85714, 26.28149

BRANDS = [
    ("Jacquemus", "Q21030814", "/g/11g6qmgyn0"),
    ("De Beers", "Q471891", "/m/0f21v"),
    ("Nude Project", "Q124984211", "/g/11v3k7ljxb"),
    ("Polène", "Q122055705", "Polène"),
    ("Fashion Nova", "Q65090803", "/g/11h02ckjsb"),
    ("COS", "Q60772401", "/g/121hf5f0"),
    ("Brunello Cucinelli", "Q3645582", "/m/0105n26h"),
    ("Bally", "Q688195", "/m/06k8vt"),
    ("Charles & Keith", "Q3878818", "/m/0bmk0d9"),
    ("Scotch & Soda", "Q17141914", "/m/0y65pf5"),
    # Shared-list adds from the VGR Social Hubs surgery2 (dedup vs Social Audience Index).
    ("Tous", "Q3815128", "/m/0c41_60"),
    ("DeFacto", "Q6059861", "/g/11r80vmfn"),
    ("Cold Culture", "Q138573425", "Cold Culture"),   # keyword (no KG mid)
    ("Halara", "VGR-HALARA", "Halara"),               # keyword (no Wikidata item)
    ("Scuffers", "VGR-SCUFFERS", "Scuffers"),          # keyword (no Wikidata item)
    ("Blue Banana", "VGR-BLUEBANANA", "Blue Banana Brand"),  # keyword (no Wikidata item)
]
BRIDGE_MID   = ("/m/03wr6g", 12.36, 12.81)   # Giorgio Armani (web, news)
BRIDGE_SMALL = ("/m/05fnqj", 9.19, None)     # Swarovski (web floor-retry)
BY_QID = {q: (n, t) for n, q, t in BRANDS}


def is_mid(t): return isinstance(t, str) and t.startswith(("/m/", "/g/"))


def anchored(client, brand_tok, bridge, adidas_field):
    """brand-level on the bridge_adidas100 scale, via a 2-token [bridge, brand] batch.

    Returns None on a live cache-miss throttle (caller treats as block)."""
    btok, badi = bridge[0], bridge[adidas_field]
    toks = [btok, brand_tok]
    cf = client._cache_path(toks)
    if cf.exists():
        means = json.loads(cf.read_text(encoding="utf-8"))
    else:
        means = client._fetch_batch(toks, cache_only=False)   # may raise TrendsUnavailable
    bm = means.get(btok, 0.0)
    m = means.get(brand_tok, 0.0)
    return round(m / bm * badi, 3) if bm > 0 else 0.0


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("qid")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    if args.qid not in BY_QID:
        print(f"unknown qid {args.qid}", flush=True)
        sys.exit(2)
    name, token = BY_QID[args.qid]

    web  = TrendsClient(CACHE)
    news = TrendsClient(CACHE, gprop="news")

    try:
        w_adi = anchored(web, token, BRIDGE_MID, 1)
        if w_adi is not None and w_adi <= 0.05:
            r = anchored(web, token, BRIDGE_SMALL, 1)
            if r is not None and r > w_adi:
                w_adi = r
        n_adi = anchored(news, token, BRIDGE_MID, 2)
    except TrendsUnavailable as e:
        print(f"RATE_LIMIT_BLOCK 429 for {name}: {str(e)[:60]}", flush=True)
        sys.exit(1)

    w_basket = round(w_adi * WEB_RESCALE, 2) if w_adi and w_adi > 0.05 else ""
    trusted = 1 if (is_mid(token) and w_basket != "") else 0
    n_basket = round(n_adi * NEWS_RESCALE, 2) if (trusted and n_adi and n_adi > 0.05) else ""

    row = {"qid": args.qid, "brand": name, "token": token,
           "trends_level_adidas100": w_adi, "trends_level_basket1000": w_basket,
           "news_level_adidas100": n_adi, "news_level_basket1000": n_basket,
           "trusted": trusted}
    outp = Path(args.out)
    outp.parent.mkdir(parents=True, exist_ok=True)
    with open(outp, "w", newline="", encoding="utf-8") as f:
        wr = csv.DictWriter(f, fieldnames=list(row.keys()))
        wr.writeheader(); wr.writerow(row)
    print(f"  {name:<20} web={w_basket!s:<9} news={n_basket!s:<9} trusted={trusted}", flush=True)
    print(f"RESULT_OK wrote {outp}", flush=True)


if __name__ == "__main__":
    main()
