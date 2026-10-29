# Benchmark reconciliation — the monthly brand-discovery safety net

This folder is the "did we miss a relevant brand?" check that runs every month as
part of `VBI-Monthly` (`schedule\run_monthly.bat`, 1st of the month). It compares
industry benchmark lists (Lyst Index, etc.) against the brands the index actually
tracks, and flags any that fall through the ontology-based discovery in
`universe.py`.

It never changes the tracked 500 on its own. It only *proposes*; a human approves.

## Files

| File | Role | Who writes it |
|---|---|---|
| `lyst.csv` (and any other `*.csv` benchmark list) | The benchmark input — brands we believe must be covered. Columns: `brand_name,known_qid,source,quarter`. | **You**, when a new index publishes |
| `name_to_qid.json` | Cached name → Q-ID resolutions (so the monthly run rarely hits Wikidata). | The job (cached), seeded by hand |
| `force_include_proposal.csv` | Benchmark brands NOT in the index this month — the month's proposal. Overwritten each run. | The job |
| `needs_manual_confirmation.csv` | Benchmark names whose Q-ID was ambiguous/unresolved. | The job |
| `force_include_approved.csv` | Brands you have approved to force-include. **This is the only file that changes the index.** Columns: `brand_name,qid,source,quarter,approved_by,approved_date`. | **You** |

## The loop (what "followed up" means)

1. `VBI-Monthly` runs the reconciliation after it builds the report.
2. If any benchmark brand is missing, it writes `force_include_proposal.csv` **and
   the monthly email to Pau gains an "Action needed — brand coverage" block** listing
   the pending brands and this instruction. (The email is the reliable channel; the
   daily `proj-brand-index` health check is only a suggested skill and is not a
   scheduled job.)
3. **To approve a brand:** copy its row from `force_include_proposal.csv` into
   `force_include_approved.csv`, add `approved_by` and `approved_date`, and commit.
   It joins the index on the next monthly rebuild. Leave the rest; they re-appear in
   next month's proposal until actioned or removed from the benchmark list.
4. `pytest tests/test_benchmark_reconcile.py` (and `python -m vgr_brand_index.benchmark_reconcile --check`)
   treat an unapproved benchmark miss as a coverage failure — so the regression test
   stays red until every benchmark brand is either in the universe or approved.

## Adding a new benchmark list

Drop a CSV with the `brand_name,known_qid,source,quarter` columns into this folder
(leave `known_qid` blank if unknown — the monthly run resolves it, or files it under
manual confirmation). It is picked up automatically next run.

## Keyword rescue (off by default)

`benchmark_reconcile.py` has an opt-in `--keyword-rescue` that searches generic
Wikidata company classes for fashion-described brands the benchmarks don't name. It
is **not** wired into the monthly job and must be validated for precision before use.
