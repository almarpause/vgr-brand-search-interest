"""Staggered nightly fetch — the gentle half of the pipeline.

The universe is partitioned into N shards by a hash of each brand's Q-ID. Each
night this fetches just one shard's signals (pageviews, GDELT, Trends) with
jittered throttling, so every brand refreshes roughly once a month and no
endpoint is ever hammered. Which shard runs is picked from the day of month by
default, so a plain daily schedule rotates through the whole universe.

Cache-first and resumable: anything already fetched is skipped, so a failed or
partial night simply resumes.

    uv run python -m vgr_brand_index.fetch_nightly            # today's shard
    uv run python -m vgr_brand_index.fetch_nightly --shard 3  # a specific shard
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import traceback
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from .gdelt import GdeltClient
from .interest import CACHE
from .pageviews import PageviewsClient, trailing_12_month_window
from .signals import _is_ambiguous_name, gather_signals
from .trends import TrendsClient
from .universe import SparqlClient, fetch_universe, viable_items
from .wikidata import WikidataClient

DEFAULT_SHARDS = 28
BACKLOG_BUDGET = 40
BACKLOG_ATTEMPTS_CAP = 6
BACKLOG_FILE = CACHE / "gdelt_backlog.json"
LOG_DIR = CACHE.parent / "logs"
NIGHTLY_LOCK = LOG_DIR / "nightly.lock"
NIGHTLY_STATUS = LOG_DIR / "nightly_status.json"


def shard_of(qid: str, n_shards: int) -> int:
    return int(hashlib.sha256(qid.encode("utf-8")).hexdigest(), 16) % n_shards


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat()


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _load_backlog(path: Path = BACKLOG_FILE) -> list[dict]:
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"GDELT backlog must be a JSON list: {path}")
    return [row for row in data if isinstance(row, dict) and row.get("label")]


def _remove_own_lock(pid: int) -> None:
    try:
        data = json.loads(NIGHTLY_LOCK.read_text(encoding="utf-8"))
        if data.get("pid") == pid:
            NIGHTLY_LOCK.unlink(missing_ok=True)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass


def main(argv: list[str] | None = None) -> int:
    pid = os.getpid()
    clients = []
    lock_written = False
    try:
        ap = argparse.ArgumentParser(description="Fetch one nightly shard of brand signals.")
        ap.add_argument("--shards", type=int, default=DEFAULT_SHARDS,
                        help="number of shards the universe is split into")
        ap.add_argument("--shard", type=int, default=None,
                        help="which shard to fetch (default: derived from day of month)")
        ap.add_argument("--no-trends", action="store_true", help="skip Google Trends this run")
        args = ap.parse_args(argv)

        shard = args.shard if args.shard is not None else (date.today().day - 1) % args.shards
        started = _now_iso()
        _write_json(NIGHTLY_LOCK, {"pid": pid, "shard": shard, "started": started})
        lock_written = True
        _write_json(NIGHTLY_STATUS, {"state": "running", "pid": pid, "shard": shard,
                                     "started": started})

        window = trailing_12_month_window(date.today())
        print(f"Nightly fetch — shard {shard}/{args.shards}, window {window[0]}..{window[1]}")

        sp = SparqlClient(CACHE / "sparql")
        try:
            items = fetch_universe(sp)
        finally:
            sp.close()
        viable = viable_items(items)
        mine = [i for i in viable if shard_of(i.qid, args.shards) == shard]
        print(f"  universe {len(viable)} viable; this shard {len(mine)} brands")

        backlog = _load_backlog()
        now = _now_iso()
        active_backlog: list[dict] = []
        for row in backlog:
            attempts = int(row.get("attempts", 0))
            if attempts >= BACKLOG_ATTEMPTS_CAP:
                print(f"    [gdelt] backlog dropped permanently: {row.get('qid', '')} {row['label']}")
                continue
            active_backlog.append({
                "qid": str(row.get("qid", "")), "label": str(row["label"]),
                "first_failed": str(row.get("first_failed") or now), "attempts": attempts,
            })

        gdelt = GdeltClient(CACHE / "gdelt")
        clients.append(gdelt)
        remaining_backlog: list[dict] = []
        attempts_bumped: set[str] = set()
        for pos, row in enumerate(active_backlog):
            if pos >= BACKLOG_BUDGET:
                remaining_backlog.append(row)
                continue
            total = gdelt.monthly_total(
                row["label"], window[0], window[1], cache_only=False, qid=row["qid"],
            )
            if total is not None:
                print(f"    [gdelt] backlog recovered: {row['qid']} {row['label']}")
                continue
            row["attempts"] += 1
            attempts_bumped.add(row["qid"])
            if row["attempts"] >= BACKLOG_ATTEMPTS_CAP:
                print(f"    [gdelt] backlog dropped permanently: {row['qid']} {row['label']}")
            else:
                remaining_backlog.append(row)
        gdelt.drain_targets()  # backlog failures are already accounted for above

        if mine:
            wd = WikidataClient(CACHE / "wikidata")
            try:
                editions = wd.wikipedia_sitelinks([i.qid for i in mine])
            finally:
                wd.close()

            from .reddit_browser import RedditBrowserClient
            pv = PageviewsClient(CACHE / "pageviews")
            trends = TrendsClient(CACHE / "trends")
            reddit = RedditBrowserClient(CACHE / "reddit")   # public search via headless browser
            clients.extend([pv, trends, reddit])

            # pv + GDELT + Reddit are per-brand: fetch this brand-shard. cache_only=False.
            df = gather_signals(
                mine, editions, pv, gdelt, trends, window,
                reddit=reddit, cache_only=False, do_trends=False, verbose=True,
            )

            # Retry this shard's GDELT misses after the first pass has adapted its rate.
            by_qid = {item.qid: item for item in mine}
            for qid in df.index[df["gdelt_12mo"].isna()]:
                item = by_qid[qid]
                total = gdelt.monthly_total(
                    item.label, window[0], window[1], cache_only=False, qid=qid,
                )
                if total is not None:
                    df.at[qid, "gdelt_12mo"] = total
                    df.at[qid, "elig_gdelt"] = not _is_ambiguous_name(item.label)

            # Trends is anchor-batched over the whole universe, not by brand shard.
            n_trends = 0
            if not args.no_trends:
                all_labels = [i.label for i in viable]
                tscores = trends.score_brands(all_labels, cache_only=False,
                                              only_shard=shard, n_shards=args.shards)
                n_trends = sum(1 for v in tscores.values() if v is not None)
        else:
            df = pd.DataFrame(columns=["gdelt_12mo", "elig_pv", "elig_gdelt", "elig_reddit"])
            n_trends = 0

        # A success anywhere in this run removes the target; a final miss is queued.
        backlog_by_qid = {row["qid"]: row for row in remaining_backlog}
        for item in mine:
            if pd.notna(df.at[item.qid, "gdelt_12mo"]):
                backlog_by_qid.pop(item.qid, None)
                continue
            row = backlog_by_qid.get(item.qid)
            if row is None:
                row = {"qid": item.qid, "label": item.label,
                       "first_failed": now, "attempts": 0}
            if item.qid not in attempts_bumped:
                row["attempts"] += 1
            if row["attempts"] >= BACKLOG_ATTEMPTS_CAP:
                print(f"    [gdelt] backlog dropped permanently: {item.qid} {item.label}")
                backlog_by_qid.pop(item.qid, None)
            else:
                backlog_by_qid[item.qid] = row
        gdelt.drain_targets()
        final_backlog = list(backlog_by_qid.values())
        _write_json(BACKLOG_FILE, final_backlog)

        coverage = {
            "pv": int(df["elig_pv"].sum()),
            "gdelt": int(df["elig_gdelt"].sum()),
            "reddit": int(df["elig_reddit"].sum()),
            "trends_batch": n_trends,
            "total": len(mine),
            "backlog": len(final_backlog),
        }
        print(
            f"  coverage — pv {coverage['pv']}/{len(mine)}, "
            f"gdelt {coverage['gdelt']}/{len(mine)}, reddit {coverage['reddit']}/{len(mine)}, "
            f"trends(batch) {n_trends}, backlog {len(final_backlog)}"
        )
        _write_json(NIGHTLY_STATUS, {
            "state": "complete", "pid": pid, "shard": shard, "started": started,
            "finished": _now_iso(), "coverage": coverage,
        })
        return 1 if mine and coverage["pv"] == 0 else 0
    except Exception:
        traceback.print_exc()
        return 1
    finally:
        for client in reversed(clients):
            try:
                client.close()
            except Exception:
                pass
        if lock_written:
            _remove_own_lock(pid)


if __name__ == "__main__":
    raise SystemExit(main())
