"""Monthly assemble + report — the deliverable half of the pipeline.

Cache-first (it reads what the nightly fetches accumulated, never hammering the
APIs live): refresh the universe, score the composite index, drop flagged parent
groups and backfill to keep 500, write the dated shot + append to history,
compute month-over-month deltas, render the .docx report, and (unless suppressed)
email it.

    uv run python -m vgr_brand_index.run_monthly --dry-run   # build, no email
    uv run python -m vgr_brand_index.run_monthly             # build + email
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from . import deltas as deltas_mod
from . import report as report_mod
from . import store
from .interest import OUTPUT, TOP_N, build_index, review_flag

NIGHTLY_LOCK = OUTPUT.parent / "logs" / "nightly.lock"
NIGHTLY_STALE_SECONDS = 4 * 60 * 60
NIGHTLY_WAIT_SECONDS = 60 * 60
NIGHTLY_POLL_SECONDS = 30


def current_month() -> str:
    return date.today().strftime("%Y-%m")


def wait_for_nightly(
    lock_path: Path = NIGHTLY_LOCK, *, stale_seconds: float = NIGHTLY_STALE_SECONDS,
    wait_seconds: float = NIGHTLY_WAIT_SECONDS, poll_seconds: float = NIGHTLY_POLL_SECONDS,
) -> str | None:
    """Wait for a live nightly lock; return a non-fatal assembly notice if needed."""
    if not lock_path.exists():
        return None
    shard = "?"
    try:
        data = json.loads(lock_path.read_text(encoding="utf-8"))
        shard = data.get("shard", "?")
        started = datetime.fromisoformat(str(data["started"]).replace("Z", "+00:00"))
        now = datetime.now().astimezone()
        if started.tzinfo is None:
            started = started.replace(tzinfo=now.tzinfo)
        fresh = (now - started).total_seconds() < stale_seconds
    except (KeyError, TypeError, ValueError, json.JSONDecodeError, OSError):
        fresh = False

    notice = (
        f"nightly shard still running/incomplete at assemble — index may miss shard {shard}"
    )
    if not fresh:
        print(notice)
        return notice

    deadline = time.monotonic() + wait_seconds
    while lock_path.exists() and time.monotonic() < deadline:
        time.sleep(min(poll_seconds, max(0.0, deadline - time.monotonic())))
    if lock_path.exists():
        print(notice)
        return notice
    return None


def assemble(month: str) -> pd.DataFrame:
    """Score from cache, drop flagged groups, keep the top 500 with fresh ranks."""
    df = build_index(cache_only=True, do_trends=True, verbose=True, deep=True)
    df["review_flag"] = [review_flag(b, d) for b, d in zip(df["brand"], df["description"].fillna(""))]
    kept = df[df["review_flag"] == ""].reset_index(drop=True)
    top = kept.head(TOP_N).copy().reset_index(drop=True)
    top["rank"] = top.index + 1
    return top


def _coverage_notice(result) -> str:
    """Human, email-ready follow-up for a failing/incomplete coverage audit."""
    brands = ", ".join(f"{m['brand_name']} ({m['qid']})" for m in result.missing)
    lines = [
        f"{len(result.missing)} benchmark brand(s) are on the tracked industry list "
        "(e.g. Lyst) but not yet in the index, pending your approval:",
        f"  {brands}",
        "To approve: copy the chosen rows from "
        "refresh_500/benchmarks/force_include_proposal.csv into "
        "force_include_approved.csv (fill approved_by and approved_date). "
        "Approved brands join the next monthly rebuild; the rest stay flagged here "
        "until actioned.",
    ]
    if result.manual:
        names = ", ".join(m["brand_name"] for m in result.manual)
        lines.append(
            f"Also needs a manual Q-ID confirmation: {names} "
            "(see refresh_500/benchmarks/needs_manual_confirmation.csv)."
        )
    return "\n".join(lines)


def reconcile_benchmarks() -> str | None:
    """Run the monthly coverage audit without jeopardising report delivery.

    Returns an email-ready notice string when there is something for Pau to act
    on (pending brands, items needing confirmation, or a run that errored), else
    None.  The caller folds it into the monthly email so the follow-up reaches
    Pau on the same cadence that generates the proposal — the daily health check
    is not a scheduled job, so the email is the one reliable channel.
    """
    try:
        from .benchmark_reconcile import run_reconciliation

        result = run_reconciliation(offline=False, keyword_rescue=False)
        print(result.summary())
        if result.missing or result.manual:
            print("  benchmark coverage: FAIL (proposal written; human approval required)")
            return _coverage_notice(result)
        print("  benchmark coverage: PASS")
        return None
    except Exception as exc:
        print(f"  benchmark reconciliation: ERROR (monthly work continues): {exc}")
        return f"Benchmark reconciliation did not complete this month: {exc}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Assemble the monthly shot and report.")
    ap.add_argument("--month", default=current_month(), help="YYYY-MM (default: this month)")
    ap.add_argument("--dry-run", action="store_true", help="build everything, send no email")
    ap.add_argument("--no-email", action="store_true", help="build + save, but do not email")
    args = ap.parse_args(argv)
    month = args.month

    print(f"=== VGR Brand Search Interest — monthly assemble {month} ===")
    nightly_notice = wait_for_nightly()
    top = assemble(month)
    counts = top["tier"].value_counts().reindex(["A", "B", "C"]).fillna(0).astype(int)
    print(f"  kept {len(top)} brands (groups dropped) — A={counts['A']} B={counts['B']} C={counts['C']}")

    # snapshot + history
    dest = store.write_snapshot(top, month)
    hist = store.update_history(top, month)
    d = deltas_mod.compute(hist, month)
    print(f"  history months: {store.months_in_history()}; prior = {d.prev_month or 'none (baseline)'}")

    # report + dashboard
    docx = dest / f"VGR_Brand_Index_{month}.docx"
    report_mod.build_report(top, d, docx)
    from . import dashboard
    dash = dashboard.write_dashboard(top, d, month)
    print(f"  wrote shot -> {dest}")
    print(f"  wrote report -> {docx}")
    print(f"  wrote dashboard -> {dash}")

    # Monthly only.  Deliberately caught so a coverage/API failure cannot block
    # the main report or its email; the result remains visible in the task log
    # and, when there is something to action, rides out in the monthly email.
    coverage_notice = reconcile_benchmarks()
    if nightly_notice:
        coverage_notice = "\n".join(filter(None, (coverage_notice, nightly_notice)))

    # email
    if args.dry_run or args.no_email:
        print("  email: skipped")
        if coverage_notice:
            print("  coverage follow-up (would email):\n" + coverage_notice)
        return 0
    try:
        from . import emailer
        rc = emailer.send_report(docx, top, d, notice=coverage_notice)
        print(f"  email: {'sent' if rc == 0 else 'not sent (rc %d)' % rc}")
        return rc
    except Exception as exc:  # never let email failure lose the built report
        print(f"  email: skipped ({exc})")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
