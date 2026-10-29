# VGR Brand Search Interest — Handoff / State of the World

A single-file reconstruction of the whole project so work can resume after a
context clear. Location: `C:\Users\aresi\Claude\code\brand-index` (local git
repo, **no remote**; committer Pau <almarpau@gmail.com>, commits end with a
`Co-Authored-By: Claude Opus 4.8` trailer). Python 3.13 via **uv**; the project
venv is `.venv`.

> uv is NOT on the bash PATH. In PowerShell prepend:
> `$env:Path = "C:\Users\aresi\AppData\Roaming\Python\Python313\Scripts;$env:Path"`
> then `uv run ...`. Console scripts: `vgr-index`, `vgr-fetch-nightly`,
> `vgr-run-monthly`, `vgr-dashboard`, `vgr-resolve`.

---

## 1. What it is

A fashion-brand **attention index**: ranks the top **500** brands on one 0–100+
scale (rescaled so the top-5 average = 100), tiers them **A / B / C**, refreshed
**monthly**, from **free public data**. Deliverables: a `.docx` report
(Economist-navy style) and an interactive HTML dashboard. Trend history is kept
so month-over-month movement can be reported.

Live dashboard artifact: https://claude.ai/code/artifact/34908f83-41f3-4bb7-816a-08325ccf7222
(update by republishing the SAME file path, or pass its `url`).
Plan doc: `C:\Users\aresi\.claude\plans\why-google-trends-and-gentle-naur.md`.

There is a *separate, unrelated* project `polyamide-index` (a Zara t-shirt
"Big Mac" price index) — do not confuse them.

---

## 2. The scoring formula (current, hard-won)

```
Interest = 0.85 · Attention  +  0.15 · Breadth      (interest.py: ATTENTION_WEIGHT, BREADTH_WEIGHT)
           → rescaled so mean(top-5) = 100 → tiers A≥66 / B 33–65 / C<33 → top 500
```

- **Breadth** = `sqrt_ratio_to_top5(sitelink_count)` — how many Wikipedia
  language editions the brand has. A steadier, not a driver.
- **Attention** = per-brand weighted combine over the **eligible** dynamic
  sources, renormalised over whatever is present (`normalize.combine_sources`).
  Each source is `sqrt`-ratio-to-its-top-5 first (variance-stabilising).
  `SOURCE_WEIGHTS = {pv 0.65, trends 0.30, gdelt 0.03, reddit 0.02}`.
- **pv is the MEDIAN of monthly pageviews** (NOT the sum), summed across all
  language editions, trailing 12 months (`signals.pv_stats`). This is the single
  most important fix — see §4.

Pageviews are keyed on the **Wikidata Q-ID**, so they are never string-ambiguous.

---

## 3. The four signals and their status (READ THIS)

An attention signal must be BOTH **entity-keyed** (immune to common-word name
collisions) AND a **volume metric** (scales with size). Only entity-keyed volume
sources are trustworthy.

| Signal | Entity-keyed | Volume | Status | Weight |
|---|---|---|---|---|
| **Wikipedia pageviews** | ✅ Q-ID | ✅ | **live, clean, bulk-fetchable — carries the index** | 0.65 |
| **Google Trends (Search)** | ✅ topic mid | ✅ | Google **hard-blocks bulk** → only trickles via nightly (~4 brands cached) | 0.30 |
| **GDELT (News)** | ❌ string | ✅ | polluted for short/common names (C&A → 2.5M); throttles | 0.03 |
| **Reddit (mentions)** | ❌ string | ✅ | polluted (WE, Guess, Theory 100–500k); fetched via headless browser | 0.02 |

- Google Trends is disambiguated by **topic entity (`/m/…` mid)** resolved via
  pytrends `suggestions()` (so "HEAD" → the sporting-goods company, not the word).
  That's why it's entity-clean and earns a real 0.30 weight even though it's
  mostly empty today.
- Reddit + GDELT match on the brand **string** → common-word brands are
  polluted. They are (a) **de-weighted to ~0** and (b) made **ineligible for
  common-word names** (`signals._COMMON_WORD_BRANDS` + `_is_ambiguous_name`).
- **Nimble `seo-intel`** (keyword search volume) is the one clean paid source
  that could add real search demand — NOT installed, needs a paid account.
- **agent-reach / Exa was tested and CANNOT do it**: it's a content fetcher, not
  a metric (returns a fixed N results for Nike and niche Ganni alike). Dead end.

Data horizon everywhere: the trailing 12 complete months.

---

## 4. Key decisions and WHY (the red-team lessons)

1. **Universe is QUERIED from Wikidata, never hand-typed** — every brand arrives
   with a real Q-ID and groups auto-decompose to banners (Inditex → Zara,
   Bershka …). See `universe.py`.
2. **Median monthly pageviews, not the 12-month sum.** The sum banked one-off
   spikes as permanent attention: Giorgio Armani's death (Sept 2025) spiked his
   article to 817k in one month → sum inflated him to #4 above Zara. Median
   ignores the spike (Armani → #20, Zara → #6). THE fix.
3. **Breadth (sitelinks) is only 15%** — it's legacy notability, not attention;
   at 50% it buried low-Wikipedia hot brands (athleisure).
4. **Only entity-keyed volume signals are trusted.** String search (Reddit,
   GDELT) is polluted by common-word / person names ("WE" #1, "Guess", "Theory",
   "Bench", "George", "Mark's", "C&A"). No query trick fixes it → de-weighted.
5. **Defunct + non-fashion removed** via Wikidata dissolution date `P576`
   (`FILTER NOT EXISTS`) plus description regexes (drops Bonwit Teller, I. Magnin,
   Camaïeu, Toys "R" Us, grocery/electronics).
6. **Consumer signals can't be bulk-loaded for free.** Google blocks Trends in
   bulk; Reddit's JSON API is 403'd and its OAuth app was a pain; both only fill
   gently over the nightly runs. This is a property of the sources, not a bug.

Current top (clean): **Nike, Gucci, Louis Vuitton, Uniqlo, H&M, Zara, Hermès,
Hugo Boss, Shein, Victoria's Secret.** Tiers ≈ A 24 / B 99 / C 377.

---

## 5. Modules (`src/vgr_brand_index/`)

- **universe.py** — Wikidata SPARQL universe. Roots: instance-of {fashion brand
  Q1618899, fashion house Q1941779, clothing store chain Q76213285} + industry
  {fashion Q12684, clothing Q11828862, shoe Q5915560, sporting goods Q768186,
  sportswear Q645292, luxury goods Q949715} + a **keyword-filtered `retail`
  root** (Q126793, apparel-only by description regex, catches DTC/athleisure
  Wikidata files as generic retail). Excludes P576-dissolved + non-fashion +
  defunct-by-description. `EXTRA_QIDS` force-includes Lululemon/Alo Yoga/
  Gymshark/Fabletics. ~3177 viable brands. `SparqlClient` = per-root cached
  queries with backoff.
- **wikidata.py** — `WikidataClient` (search, `entities`, `wikipedia_sitelinks`
  = all editions), `USER_AGENT`, `LANGUAGES` (en es fr it de ja zh), plus the
  legacy `choose`/`Candidate` resolver.
- **pageviews.py** — `PageviewsClient` (Wikimedia REST per-article, all-access,
  agent=user, monthly, cached, 429 backoff + jitter, `cache_only`),
  `trailing_12_month_window`.
- **gdelt.py** — `GdeltClient` (DOC 2.0 `timelinevolraw`, monthly volume,
  throttle+jitter+backoff; a throttled give-up returns **None** = ineligible, not
  0; `cache_only`).
- **trends.py** — `TrendsClient`: `resolve_query` (brand → topic **mid** via
  pytrends `suggestions`, else cleaned keyword), anchor-chained batches (anchor
  "Adidas"), `score_brands(only_shard, n_shards, cache_only)` batched in the
  caller's order for cache stability; graceful degradation on block.
- **reddit.py** — `RedditClient` (OAuth app-only — NOT used now), `cache_file_for`
  (shared cache scheme), `FASHION_SUBS`, `load_credentials`.
- **reddit_browser.py** — `RedditBrowserClient`: **headless Playwright** reading
  Reddit's PUBLIC `search/` DOM (no login, no creds), comment-sum signal. This is
  the reddit fetcher used now. NOTE: currently **site-wide** search → polluted
  for common words (hence de-weight + exclusion). A fashion-subreddit restrict
  and the `.json` API both failed (multireddit renders empty; `.json` 403s).
- **signals.py** — `gather_signals` (per brand: `_pv_monthly` → `pv_stats`
  median/recent/12mo; gdelt; reddit; trends). Eligibility flags; common-word
  exclusion (`_COMMON_WORD_BRANDS`, `_is_ambiguous_name`) drops string-search
  sources for ambiguous names.
- **normalize.py** — `sqrt_ratio_to_top5`, `zscore`, `inverse_variance_weights`,
  `combine_sources` (renormalise over eligible per brand).
- **interest.py** — `build_index` (universe→editions→gather→score→enrich),
  `score_universe` (the composite), `tier_of`, `review_flag`/`_KNOWN_GROUPS`
  (parent-group flag → dropped in the published 500), constants, `main` writes
  `output/index_500.csv` + `index_full_ranked.csv`.
- **store.py** — `write_snapshot` (`output/<YYYY-MM>/` + `latest/`),
  `update_history` (`history/index_history.parquet`, idempotent on (qid,month)),
  `HISTORY_COLS`.
- **deltas.py** — month-over-month winner/growth, movers, tier migrations.
- **report.py** — Economist-navy `.docx` via vendored **economist_navy.py** +
  `assets/vgr-logo-black.png`; exec summary composed from deltas in vgr-voice
  register; economist-chart-style PNG; ledger tables.
- **economist_navy.py** — vendored docx engine (from the economist-word-style skill).
- **dashboard.py** + **dashboard_template.html** (repo root) — server-side-rendered
  `dashboard.html`; columns **Google Search · Wikipedia pageviews · News · Reddit
  mentions** (the "Signals" column was removed); a formula/weights method panel
  built from the live constants; JS only does search/filter/sort.
- **emailer.py** — SMTP send (external ini + `VBI_SMTP_*`/`ZARA_SMTP_*` env).
- **config.py** — `load_settings` (`config/settings.json`), `ROOT`.
- **fetch_nightly.py** — nightly shard: `hash(qid) % shards`; fetches pv+gdelt+
  reddit(browser) for the shard, trends batch-sharded over the full universe.
- **run_monthly.py** — assemble: `build_index(cache_only)` → drop groups → top 500
  → snapshot → history → deltas → report → dashboard → email. Flags
  `--dry-run/--no-email/--month`.
- **overnight_fetch.py** — one-time bulk staggered fetch of top-N by pv; `--hours H`
  for a start-now window (else stops at 09:00); Reddit+Trends first, GDELT fills
  the rest; cache-first, error-tolerant, deadline stop.
- **resolve.py** — legacy 60-brand resolver (superseded, kept).

---

## 6. Caching & outputs

- `cache/` (gitignored): `sparql/`, `wikidata/`, `pageviews/` (monthly per
  edition, thousands), `gdelt/`, `trends/` (`batch_*` — **228 are orphaned**
  raw-term leftovers the mid-based scorer can't read; safe to delete),
  `reddit/`. Everything is cache-first → re-runs are cheap and resumable.
- `output/` (gitignored): `output/<YYYY-MM>/{index_500.csv,.parquet,dashboard.html,
  VGR_Brand_Index_<month>.docx,chart-1.png}`, `output/latest/`, `output/dashboard.html`.
- `history/index_history.parquet` — **TRACKED** (the trend store).

---

## 7. Scheduling (Windows Task Scheduler, `schedule/`)

- **VBI-FetchNightly** — daily 02:30 → `fetch_nightly.bat` (one shard).
- **VBI-Monthly** — 1st 06:00 → `run_monthly.bat` (assemble + email). Moved from
  04:00 (2026-10-29) so it starts after even a slow nightly finishes; combined
  with the in-code nightly lock (§11) the monthly can no longer read a
  half-written shard.
- **VBI-Overnight**, **VBI-Campaign-Test**, **VBI-Assemble-Test** — one-time,
  already fired = **spent; delete them** (`schtasks /Delete /TN <name> /F`).
- `.bat` launchers use `.venv\Scripts\python.exe`; `register_schedule.ps1` /
  `unregister_schedule.ps1` create/remove the recurring pair.
- Tasks run **as the current user, "interactive only"** → the machine must be ON
  and LOGGED IN (browser-based Reddit/Trends need the session; disable sleep).
- SMTP from `config/settings.json` `smtp_ini` (reused
  `../parser/russia/zara/config/email.ini`). `config/reddit.ini` exists with
  placeholders but the browser Reddit path needs **no creds**. `config/*.ini` and
  `config/reddit_profile/` are gitignored.

### The backtracking campaign (designed, NOT fully built)
Work backward from a "ready by" time; each source gets a start-offset + spread
window (GDELT −48h slowest, Trends/Reddit −30/−36h, pageviews −3h, assemble at
deadline) with human-like jittered pacing. `overnight_fetch.py --hours` is the
start-now piece; a `register_campaign.ps1` laying down the staggered per-source
tasks was proposed but **not built**.

---

## 8. Open items / TODO

- **Next action — approve (or decline) 4 benchmark brands into the roster.** The
  monthly benchmark reconciliation (see §10) found Chanel (Q218115), Dior
  (Q542767), Coach (Q727697) and Khaite (Q136953861) on the Lyst Q3-26 list but
  NOT in the tracked 500 — they were misclassified in Wikidata (e.g. Dior as a
  "business"/organization) so the ontology roots missed them. They are proposed in
  `refresh_500/benchmarks/force_include_proposal.csv`. Adding them reshuffles ranks,
  so it needs **Pau's explicit go-ahead**: approve by copying rows into
  `force_include_approved.csv` (see §10 / that folder's README). Nothing enters the
  index until then.
- **Committed 2026-10-29 (local `master`, not pushed).** The nightly-resilience
  hardening (§11) and the benchmark-recall layer (§10) went in as one code commit
  — source + tests + HANDOFF only; the data/output churn (dashboard, JSON, history
  parquet, refreshed CSV) and the trends-campaign scratch files were left out.
  `pytest -q` = 20 pass; pushed to `origin/master` 2026-10-29 (rebased onto the
  SURFACE3 "align README" commit — disjoint files, no conflict).
- **proj-brand-index health check is NOT a registered scheduled task** — it is only
  a "suggested daily 08:00" skill. Until it's scheduled, the monthly email is the
  only channel that surfaces a coverage miss. Decide whether to register it.
- **Delete the 3 spent one-time tasks** (VBI-Overnight / -Campaign-Test / -Assemble-Test).
- Optionally **clear the 228 orphaned `cache/trends/batch_*`** (raw-term).
- **Google Search** fills only via the nightly (weeks) — Google blocks bulk.
- **Nimble `seo-intel` pilot** is the only way to add a clean search-volume
  signal; needs a paid Nimble account + CLI install. Held pending Pau's decision.
- **Backtest** (does the composite track reported quarterly sales — Inditex,
  Nike, LVMH…) was in the original spec and is **never done**.
- **README.md is stale** (Phase-2 era) vs this current red-team methodology;
  this HANDOFF supersedes it.
- Reddit browser search is site-wide (polluted); string ambiguity is fundamental
  so it stays de-weighted.
- Machine clock has been advancing irregularly across the session (test env).

---

## 9. How to run / verify

```powershell
$env:Path = "C:\Users\aresi\AppData\Roaming\Python\Python313\Scripts;$env:Path"
uv run pytest -q                              # 15 tests
uv run python -m vgr_brand_index.run_monthly --dry-run   # rebuild shot+report+dashboard, no email
uv run python -m vgr_brand_index.dashboard --month 2026-08
```
To refresh the dashboard artifact: strip `output/dashboard.html` from `<title>`
to the last `</script>`, drop `</head>`/`<body>`, and republish that fragment to
the SAME artifact URL.
</content>

## Surgery 2 (22 Sep 2026): 500 distinct brands
`refresh_500/surgery2.py`. Run order: `merge_and_rebuild.py` -> `surgery2.py apply` -> `dashboard.py` (surgery 1 rebuilds from the pre-surgery baseline, so surgery 2 must follow it).
- Removed duplicate items: Max Mara Q110225120, Cotton On Q5175717, Kapital Q11297188, Tailored Brands Q3305660. Removed non-brands: streetwear Q2072372, garment factory Q55748823.
- Added: Tous Q3815128, DeFacto Q6059861, Cold Culture Q138573425 (pageviews only so far), and Halara, Scuffers, Blue Banana (keys VGR-HALARA / VGR-SCUFFERS / VGR-BLUEBANANA, no Wikidata item, score 0 until Trends and News are collected into `trends_stagger/items/<key>.csv`).
- Result: 500 rows, 500 distinct names, basket unchanged. The same 500 is the social-leaders roster.
- Not committed or pushed. Push only with Pau's go-ahead, coordinated with the trends campaign session.

---

## 10. Benchmark recall layer — the brand-discovery safety net (Oct 2026)

**Problem it solves.** The universe is discovered by Wikidata ontology roots, which
miss two kinds of relevant brand: (A) houses **misclassified** in Wikidata so they
escape the fashion roots (Chanel, Coach, Dior all carry "business"/organization
P31s), and (B) correctly-classed brands with **0 Wikipedia sitelinks** dropped by
the `sitelinks>0` viability gate (e.g. Dries Van Noten). Industry lists like the
**Lyst Index** name exactly these brands, so they are the ground truth we check
against.

**How it works (monthly, staggered — NOT daily).**
- `src/vgr_brand_index/benchmark_reconcile.py` — `run_reconciliation(offline, keyword_rescue)`
  compares every benchmark CSV in `refresh_500/benchmarks/` against the built
  universe, resolves names → Q-IDs (cached in `name_to_qid.json`), and writes
  `force_include_proposal.csv` (missing brands) + `needs_manual_confirmation.csv`
  (ambiguous names). `assert_benchmark_coverage()` fails until each benchmark brand
  is either in the universe or in `force_include_approved.csv`.
- Viability was centralised in `universe.viable_items(items, approved_qids)`: a
  0-sitelink item now survives if it is a fashion brand/house, in `EXTRA_QIDS`, or
  in the **approved** benchmark set. `interest.build_index(..., deep=True)` loads
  the approved Q-IDs on the **monthly** path only; the nightly path stays strict.
- `run_monthly.reconcile_benchmarks()` runs after the report is built, wrapped so a
  coverage/API failure can never block the report or its email. When something needs
  action it returns an email-ready notice; `emailer.send_report(..., notice=...)`
  folds it into the monthly email as an **"Action needed — brand coverage"** block
  and adds " · action needed" to the subject. **The email is the reliable
  follow-up channel** (the health-check skill isn't scheduled).
- `discover_keyword_rescue()` / `--keyword-rescue` is an opt-in CirrusSearch sweep
  for fashion-described brands the benchmarks don't name. **Not** wired into the
  monthly job; validate precision before enabling.

**The approval loop (this is "followed up"):**
`VBI-Monthly` proposes → the monthly email tells Pau → Pau copies chosen rows from
`force_include_proposal.csv` into `force_include_approved.csv` (adds `approved_by`,
`approved_date`) and commits → the brand joins the index on the next rebuild; the
coverage test goes green. Full description: `refresh_500/benchmarks/README.md`.

**Status:** built, `pytest -q` = 20 pass, committed 2026-10-29 (see §8). The 4 Lyst
brands above are proposed and awaiting Pau (see §8).

> Engine note (per CLAUDE.md routing): the token-heavy multi-file build + tests
> were delegated to **Codex**; Claude did the planning, review, the test run Codex's
> sandbox blocked, and the email-notice wiring.

---

## 11. Nightly-fetch resilience (hardened 2026-10-29)

**Why.** The 2026-10-29 nightly (shard 0) died ~05:01 on a single
`httpx.ConnectTimeout` from GDELT: `_get` handled 429/5xx but not transport
errors, so one brand's timeout ended the whole run (rc=1) and the rest of the
shard never ran. Separately, every throttled GDELT brand used to wait a full
28-day cycle for its shard to come round again, so a large slice of the index
carried no news signal.

**What changed (one commit; engine note: delegated to Codex per CLAUDE.md, Claude
reviewed + ran the tests Codex's sandbox blocked).**
- **Transport errors never end the run.** `gdelt._get` and `pageviews._get` now
  catch `httpx.TransportError`/`TimeoutException`, back off and retry. GDELT gives
  up like a throttle (logs, records the brand, returns None). Pageviews is the
  backbone so it still *raises* on persistent 429/5xx, but a persistent transport
  error returns an empty result (brand misses that edition) instead of crashing.
  `signals.gather_signals` also wraps each brand so one exception can't abort the
  loop — the brand gets a null row and is simply ineligible.
- **GDELT backlog + end-of-run retry + adaptive rate.** Throttled/failed GDELT
  brands are persisted to `cache/gdelt_backlog.json`; each night drains up to
  `BACKLOG_BUDGET = 40` of them before the shard, drops ones that recover, bumps
  `attempts`, and permanently drops at `attempts >= BACKLOG_ATTEMPTS_CAP (6)`.
  After the shard it retries this shard's still-missing GDELT once more with a
  now-slower client. The client self-slows (`min_interval ×1.5`, cap 30s) under
  429/give-up.
- **Honest coverage + rc.** `main()` prints `coverage — pv X/N, gdelt Y/N,
  reddit Z/N, trends(batch) T, backlog B` and returns 1 only on a real failure
  (uncaught exception, or a non-empty shard with pv coverage 0). Partial
  gdelt/reddit/trends is normal, not a failure.
- **Monthly can't read a half shard.** `fetch_nightly` writes `logs/nightly.lock`
  (+ `logs/nightly_status.json`) at start and clears the lock in a `finally`.
  `run_monthly.wait_for_nightly()` waits up to 60 min for a fresh lock to clear;
  if it's stale (>4h) or times out it proceeds anyway and appends a gap line to
  the monthly email notice — the monthly always completes and emails. The 06:00
  start (§7) means this almost never has to wait.
- **Test:** `tests/test_nightly_resilience.py` — GDELT ConnectTimeout → give-up,
  not raise; pageviews ConnectTimeout → `[]`, not raise; a raising gdelt → the
  brand still lands with a null signal.

New constants/paths: `BACKLOG_BUDGET`, `BACKLOG_ATTEMPTS_CAP`,
`cache/gdelt_backlog.json`, `logs/nightly.lock`, `logs/nightly_status.json`.
