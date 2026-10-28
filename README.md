# VGR Brand Search Interest: data pipeline

A fashion-brand *attention* tracker. It ranks the top **500** brands on one
interest scale and tiers them **A / B / C**. All inputs are free public sources.
No paid APIs. **Never fabricate, estimate or interpolate a data point. Missing is
missing.**

Stack: Python 3.13, [uv](https://docs.astral.sh/uv/), pandas, httpx, matplotlib,
python-docx, pytrends.

## What is published, and how often

The GitHub Pages site and the feed `brand_index_data.json` are built by
`dashboard.py` (project root). It reads the committed CSVs in `refresh_500/`
(mainly `index_500_refreshed.csv`, `gtrends_news_500.csv` and
`aug_vgr_reconstructed.csv`). It collects no data itself.

The workflow `.github/workflows/refresh-index.yml` runs on every push to `main`
or `master`, on manual dispatch, and **weekly on Monday at 06:00 UTC**. The
weekly run only re-publishes. The numbers change only when new CSVs are
committed. The current feed is the **September 2026** shot (`month` in the feed,
`MONTH` in `dashboard.py`).

## Method (published feed)

The score is computed by `score()` in `refresh_500/rebuild_roster.py`. The
published feed carries the same parameters in its `method` object.

1. **Sources and weights.** Three attention sources:
   * Google search interest (Google Trends, web): **50%**
   * Google News interest (Google Trends, news): **30%**
   * Wikipedia pageviews (median monthly views, all language editions): **20%**

   GDELT and Reddit are no longer used.
2. **Square-root scaling.** Each source value is square-rooted and divided by the
   mean of its top 5 brands (`sqrt_ratio_to_top5` in
   `src/vgr_brand_index/normalize.py`).
3. **Attention.** The scaled sources are blended with the 50/30/20 weights. The
   weights are renormalised per brand over the sources that brand has
   (`combine_sources`). A missing source is dropped, not counted as zero. A
   source counts only if at least 60 brands have it.
4. **Breadth.** Number of Wikipedia language editions (sitelinks), scaled the
   same way.
5. **Raw score.** `0.85 × attention + 0.15 × breadth` (attention 85%,
   breadth 15%). Brands are ranked on this.
6. **Scale.** `dashboard.py` rescales the index so the average of an 8-brand
   basket equals **1000** in the September 2026 base window. Basket: Nike,
   Adidas, Zara, H&M, Hermès, Louis Vuitton, Gucci, Uniqlo. The Google search
   and Google News levels are anchor-chained (to Adidas) and put on the same
   basket = 1000 scale. The rescale does not change ranks.
7. **Tiers.** Tiers are set on the older top-5 = 100 scale (A ≥ 66, B 33 to 65,
   C < 33) and the cut-offs are rescaled with the index. On the current feed
   that is about A ≥ 774, B 387 to 773, C < 387. Counts: A 9, B 55, C 436.
8. **Month-over-month.** "% vs last month" compares with an August 2026
   cross-section rebuilt with the same method (`aug_vgr_reconstructed.csv`).

Coverage in the current shot: 475 brands have pageviews, 269 have Google search,
135 have Google News. 13 brands have no attention source and score on breadth
only.

## Known data issues

* **Same name, two Wikidata IDs.** These brands appear twice in
  `brand_index_data.json`, each with a different Q-ID and score:
  * Max Mara: Q1151774 (rank 179) and Q110225120 (rank 463)
  * Cotton On: Q113992257 (rank 304) and Q5175717 (rank 498)
  * Kapital: Q119149859 (rank 377) and Q11297188 (rank 397)
  * Tailored Brands: Q108739951 (rank 386) and Q3305660 (rank 426)
* **Index 0, no signals.** Nude Project (Q124984211, rank 500) has index 0. It
  has no pageviews, no Google search, no Google News and no sitelinks.
* **Breadth only.** The 13 brands ranked 488 to 500 have no attention source.
  Some labels are not brands ("garment factory", "Ladies", "Beer") and look like
  wrong Wikidata matches.

## Local pipeline (src/)

The package in `src/vgr_brand_index/` is the original local pipeline. It does
not produce the published feed. It uses older settings: `interest.py` weights
pageviews 0.55, Trends 0.45, GDELT 0.03 and Reddit 0.02, with breadth 0.15, and
scales to top-5 average = 100. Its jobs are:

| Job | Cadence | Command | Does |
|---|---|---|---|
| **Nightly fetch** | daily 02:30 | `vgr-fetch-nightly` | fetches one shard (1/28th) of brand signals into cache |
| **Monthly assemble** | 1st, 04:00 | `vgr-run-monthly` | scores from cache, writes shot + history, report, email |

```bash
uv run vgr-fetch-nightly            # today's shard
uv run vgr-run-monthly --dry-run    # build this month's shot + report, no email
uv run vgr-run-monthly              # build + email the report
```

Every brand is keyed on its **Wikidata Q-ID**, so homonyms stay separate and
groups are split into banners. Google Trends is an unofficial endpoint: cached,
retried, and dropped when blocked.

## Following the trend (local pipeline)

Each run writes a dated shot (`output/<YYYY-MM>/`) and upserts the month into
`history/index_history.parquet`, keyed `(qid, month)` so re-running a month
corrects it in place. `deltas.py` reads history for the report: the winner and
its growth, biggest movers, and tier migrations (A↔B↔C, in/out of the 500) —
"who changed in the top and in the middle vs last month".

## The monthly report (local pipeline)

`report.py` builds a VGR/Economist-navy `.docx` (engine + logo vendored under the
package, so the headless job needs no skills plugin): cover with logo, an
executive summary in **vgr-voice register** composed from the deltas, an
economist-chart-style figure, ledger tables (top of index, movers), method and
colophon. A separate Claude pass can rewrite the summary in full five-moves voice;
the facts do not change.

## Deploy the local pipeline (Windows Task Scheduler)

```powershell
# elevated PowerShell (SYSTEM, unattended) — or add -CurrentUser to run as you
powershell -ExecutionPolicy Bypass -File schedule\register_schedule.ps1
schtasks /Run /TN VBI-FetchNightly     # trigger one shard now
schtasks /Run /TN VBI-Monthly          # build + email now
powershell -ExecutionPolicy Bypass -File schedule\unregister_schedule.ps1
```

SMTP is reused from the Zara parser's git-ignored `email.ini` (path in
`config/settings.json`), overridable by `VBI_SMTP_*` / `ZARA_SMTP_*` env vars. No
secrets in this repo; with no credentials the email is skipped and the report is
still built and saved.

## Layout

```
src/vgr_brand_index/
  universe.py     query the brand universe from Wikidata
  pageviews.py    Wikipedia pageviews (all editions), cache + backoff
  gdelt.py        GDELT news volume, gentle throttle
  trends.py       Google Trends, anchor-chained, best-effort
  signals.py      gather all signals per brand (cache_only for scoring)
  normalize.py    sqrt-ratio, z-score, weighted per-brand combination
  interest.py     local scoring: compose -> index -> A/B/C -> top 500   (vgr-index)
  store.py        dated shots + append-only history
  deltas.py       month-over-month movers + migrations
  report.py       Economist .docx + chart (vendored economist_navy.py)
  emailer.py      SMTP send (reused external ini + env)
  fetch_nightly.py  one staggered shard        (vgr-fetch-nightly)
  run_monthly.py    assemble + report + email  (vgr-run-monthly)
schedule/         Task Scheduler registrar + .bat launchers
refresh_500/      Sept 2026 refresh: collection scripts, scoring (rebuild_roster.py), CSVs read by dashboard.py
dashboard.py      builds Brand_Index_Dashboard.html + brand_index_data.json (the published feed)
config/           settings.json (recipients, cadence, smtp_ini path)
history/          index_history.parquet (tracked)
cache/ output/ logs/   (gitignored)
```

## Build phases

0. Universe + interest index ✅
1. ~~60-brand entity resolution~~ — superseded (kept as `resolve.py`).
2. Four sources + normalisation + monthly shots + report + schedule ✅
3. Backtest — does the composite track/lead reported quarterly sales? (next)
4. Optional cloud: Supabase + Edge Function cron.
5. September 2026 refresh: three sources (50/30/20), basket = 1000, published to GitHub Pages ✅
```
