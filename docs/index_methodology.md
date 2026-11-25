# VGR Brand Search Interest — Index Methodology

**Status:** design spec (v1, 2026-11-25). Supersedes ad-hoc scoring in the
discovery scratch scripts. This is the reference for the monthly pipeline.

This index **enriches** the existing multi-signal attention index with a
Lyst-style Demand / Desire / Discovery structure. Nothing in the current system
is discarded — the four organic signals, inverse-variance fusion, the
pageviews→interest calibration, the editorial boost and the incumbency damping
are all preserved and reorganized, then two public signals and a movement/heat
output are added on top.

---

## 0. Principles

- **Transparent & reproducible.** Fixed trailing window, published signals,
  **published weights**. This auditability is the deliberate differentiator vs
  Lyst's undisclosed formula.
- **Enrich, don't replace.** The Lyst lens is organizing structure, not a new
  index. Existing validated components stay.
- **Two questions, two outputs.** "Who belongs in the published 500" (stable) is
  separated from "who is hot right now" (volatile). One engine, two lenses.
- **Roster before metrics.** Stage 1 freezes a Q-ID-keyed roster; Stage 2
  collects signals against it. No metric is ever collected against an unfrozen
  target.

---

## 1. Stage 1 — the frozen roster (already built)

`build_roster` produces a dated, Q-ID-keyed roster:
`output/roster/brand_roster_<month>.csv` + `roster_titles_<month>.json`
(Q-ID → {lang: Wikipedia title}, the exact Stage-2 pageview targets).

- Universe (resolved, active) + editorial gaps (fashion-matched, beauty/cosmetics
  and non-brand filtered) de-duped by Q-ID then normalized name.
- Beauty/cosmetics houses are **excluded** (this is a fashion/apparel index).
- Roster is frozen per month so movement arrows diff against a stable base.

---

## 2. Signals (6, full roster ~2,331)

| # | Signal | Key | Source | Block risk |
|---|---|---|---|---|
| 1 | Wikipedia pageviews | Q-ID (per-lang titles) | Wikimedia REST pageviews API | none |
| 2 | GDELT news volume + tone | brand string (+fashion term) | GDELT DOC 2.0 API | none |
| 3 | Reddit mentions | brand string (fashion subs) | Reddit search API | blockable |
| 4 | Meta Ad-Library active-ad count | advertiser name | Ad Library (API/UI) | blockable |
| 5 | Google Trends | Trends topic/entity ID | Trends (browser) | bot-walled |
| 6 | TikTok hashtag views | brand hashtag | TikTok Creative Center | bot-walled |

Only (1) is Q-ID-clean. (2)–(6) match on name → disambiguation required (§6).

---

## 3. The engine — three dimensions

For each brand, each signal → **percentile-rank 0–100** over the trailing window
(outlier-proof, interpretable, matches a relative heat ranking). **Within each
dimension, combine by inverse-variance weight** (existing method): reliable,
well-covered signals count more; missing signals renormalize over the rest.

```
Demand    = Σ wₛ·pₛ   for s ∈ {wiki_pageviews, google_trends}
Desire    = Σ wₛ·pₛ   for s ∈ {reddit, gdelt_tone_adj, tiktok_hashtag, editorial_authority}
Discovery = Σ wₛ·pₛ   for s ∈ {wiki_edition_count, meta_adlib_ads}
```

- `editorial_authority = norm( min(ed_count, 4) + 2·lyst_top20 )`
  — the curated editorial lists as the "tastemaker network" weight inside Desire.
- `gdelt_tone_adj = gdelt_volume · clip(1 + tone/10, 0.3, 1.0)`
  — GDELT average tone shrinks scandal/negative-driven volume (partial exclusion
  rule; discount-driven heat is not visible without sales data — known gap).

The existing **`interest_index`** (calibrated 4-signal fusion) is retained and
reported; by construction it ≈ the Demand+Desire core, giving continuity against
the validated prior score.

---

## 4. The total score (published weights)

```
HEAT = 0.50·Demand + 0.30·Desire + 0.20·Discovery
```

Weights mirror Lyst's emphasis (platform demand heaviest, social next, discovery
lightest). **Tune once** against the ~20 brands both we and Lyst cover (maximize
rank-correlation with Lyst's published top-20, then face-validity check), then
**freeze and publish**. Do not re-tune per run.

---

## 5. Two lenses

### Lens A — Membership (the published 500), stability-first
```
SELECT = HEAT_level + 4·in_pub500          # level = recency-weighted trailing mean
```
Rank → top 500 → swap in force-keepers. The incumbency term damps month-to-month
thrash (does not freeze the list). Force-keepers: see `KEEP` list in selection
script (Nude Project, American Vintage, Zadig & Voltaire, Zegna, Zara, …).

### Lens B — Heat ranking (Lyst-style), momentum-first, no incumbency
```
HEAT_now = 0.50·Demand + 0.30·Desire + 0.20·Discovery   # recency half-life ~6 wk
Δrank    = prev_rank − cur_rank                           # the arrows
momentum = last-4wk / trailing-baseline                   # "rising" flag
```
Tags: `NEW` (absent last snapshot), `RE-ENTRY` (returns after a gap). Diff against
the previous month's frozen roster snapshot.

### Recency
Each signal computed two ways over a trailing 3-month window: **level**
(exponentially recency-weighted mean, half-life ~6 wk) drives rank; **momentum**
(last-4wk vs baseline) drives the arrows.

---

## 6. Disambiguation (string-keyed signals 2–6)

Name-matched signals inherit pollution (Coach, Gap, Boss, Victoria, …). Per
source:
- **Google Trends** → query by **entity/topic ID + Fashion category**, not raw
  string (Trends' equivalent of a Q-ID; resolve once, cache).
- **Reddit** → restrict to a fashion-subreddit allowlist
  (femalefashionadvice, malefashionadvice, streetwear, luxury, …).
- **GDELT** → append a fashion context term for ambiguous names.
- **TikTok / Meta** → brand-handle match where available.
- Residual noise is **flagged, not trusted**; roster descriptions drive an
  ambiguity flag so polluted brands are discounted in review.

---

## 7. Scraping strategy — three tiers by block risk

| Tier | Signals | Runs how | ~wall-clock @ 2,331 |
|---|---|---|---|
| 1 Backbone | Wikipedia pageviews, GDELT | bulk + parallel, same day | 1–2 h |
| 2 Staggered scripted | Reddit, Meta Ad-Library | `stagger_run.py` subprocess | 12–20 h each |
| 3 Staggered browser | Google Trends, TikTok | agent-driven stagger (ScheduleWakeup) | 18–24 h each |

Staggered tiers use the **stagger-runner** pattern: random batch sizes, random
gaps, per-item block detection, resumable on the key, push alert on block/done.
A block is recorded as `block` (never a fake zero); re-run resumes only
unfinished items.

### Google Trends chaining (2,331 brands on ONE 0–100 scale)
Trends is relative within a ≤5-term query. Include **one fixed anchor brand** in
every batch of 4 + anchor; rescale each batch by the anchor → common scale.
Query by entity/topic ID + Fashion category. ~580 batches, staggered = the
longest single job.

---

## 8. Orchestration (monthly)

```
Day 0   Stage-1 roster freeze → Tier-1 backbone (Wikipedia, GDELT)   [same day]
Day 0–2 Tier-2 staggered (Reddit, Meta) parallel subprocesses        [overnight each]
Day 0–3 Tier-3 staggered (Trends longest, then TikTok), agent-paced  [2–3 days]
Day 3   Scoring → dimensions → HEAT → both lenses                    [minutes]
Day 3   Diff vs last snapshot → arrows/NEW/RE-ENTRY → human review → publish
```

- Checkpointed & resumable throughout.
- Outputs land Q-ID-keyed in `output/metrics/<month>/` (gitignored, System-B
  safe — the live published site is untouched until an approved flip).

---

## 9. Coverage, fallbacks, honest ceiling

- Blocks (Trends/Reddit/TikTok/Meta) → stagger pauses + pings; resume later.
- Partial coverage on social/ad sources is expected → inverse-variance weighting
  renormalizes over available signals; Wikipedia's calibration
  (`interest ≈ -22.39 + 5.82·log1p(pv_median)`, R²=0.60) backfills Demand.
- **Not replicable:** Lyst's first-party transactional demand (add-to-cart,
  conversion, sales). This index measures *searched / read / talked-about*, not
  *bought* — and says so. Auditability is the trade.
- Tier-3 browser staggers are best-effort and cannot be fully unattended (need a
  live session + signed-in browser).

---

## 10. System boundary

- **System A (live/published):** root `dashboard.py`, `brand_index_data.json`,
  `Brand_Index_Dashboard.html`, `refresh_500/*.csv`, `.github/workflows/` →
  GitHub Pages → Lovable iframe. **Frozen; not touched by this pipeline.**
- **System B (this work):** `src/vgr_brand_index/` package + `output/` scratch.
  The publish-path flip to System B is a separate, explicitly-approved step.
