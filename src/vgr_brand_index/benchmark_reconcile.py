"""Monthly benchmark-to-universe reconciliation.

Benchmark lists are committed inputs.  Name resolution is cached and cautious:
ambiguous candidates are written for manual confirmation rather than guessed.
The optional generic-company keyword rescue is deliberately opt-in because it
uses broader Wikidata search classes than the ontology universe.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import re
import sys
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from .interest import CACHE
from .universe import (
    EXTRA_QIDS,
    FASHION_DESC_REGEX,
    SparqlClient,
    _DEFUNCT_DESC,
    _EXCLUDE_DESC,
    fetch_universe,
    load_approved_qids,
    viable_items,
)
from .wikidata import Candidate, WikidataClient

REPO_ROOT = Path(__file__).resolve().parents[2]
BENCHMARK_DIR = REPO_ROOT / "refresh_500" / "benchmarks"
RESOLUTION_MAP = BENCHMARK_DIR / "name_to_qid.json"
MANUAL_FILE = BENCHMARK_DIR / "needs_manual_confirmation.csv"
PROPOSAL_FILE = BENCHMARK_DIR / "force_include_proposal.csv"
APPROVED_FILE = BENCHMARK_DIR / "force_include_approved.csv"

_FASHION_DESC = re.compile(FASHION_DESC_REGEX, re.IGNORECASE)
_BENCHMARK_FIELDS = ("brand_name", "known_qid", "source", "quarter")
_PROPOSAL_FIELDS = ("brand_name", "qid", "source", "quarter", "reason", "description")
_MANUAL_FIELDS = ("brand_name", "source", "quarter", "reason", "candidates")


@dataclass(frozen=True)
class BenchmarkEntry:
    brand_name: str
    known_qid: str
    source: str
    quarter: str


@dataclass(frozen=True)
class Resolution:
    qid: str
    label: str
    description: str


@dataclass
class ReconciliationResult:
    benchmark_count: int
    resolved_count: int
    viable_count: int
    missing: list[dict[str, str]]
    manual: list[dict[str, str]]
    proposal_path: Path = PROPOSAL_FILE

    def summary(self) -> str:
        return (
            "  benchmark reconciliation: "
            f"benchmarks={self.benchmark_count} resolved={self.resolved_count} "
            f"viable={self.viable_count} missing={len(self.missing)} "
            f"manual={len(self.manual)}"
        )


def _normalise(value: str) -> str:
    folded = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", folded.lower()).strip()


def load_benchmarks(directory: Path = BENCHMARK_DIR) -> list[BenchmarkEntry]:
    entries: list[BenchmarkEntry] = []
    excluded = {PROPOSAL_FILE.name, APPROVED_FILE.name, MANUAL_FILE.name}
    for path in sorted(directory.glob("*.csv")):
        if path.name in excluded:
            continue
        with path.open(encoding="utf-8-sig", newline="") as fh:
            reader = csv.DictReader(fh)
            missing = set(_BENCHMARK_FIELDS) - set(reader.fieldnames or ())
            if missing:
                raise ValueError(f"{path}: missing columns {sorted(missing)}")
            for row in reader:
                name = row["brand_name"].strip()
                if name:
                    entries.append(BenchmarkEntry(
                        name, row["known_qid"].strip(), row["source"].strip(),
                        row["quarter"].strip(),
                    ))
    return entries


def _load_resolution_map(path: Path = RESOLUTION_MAP) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _write_csv(path: Path, fields: tuple[str, ...], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _candidate_is_fashion(candidate: Candidate) -> bool:
    desc = candidate.description or ""
    return bool(
        _FASHION_DESC.search(desc)
        and not _EXCLUDE_DESC.search(desc)
        and not _DEFUNCT_DESC.search(desc)
    )


def _automatic_choice(
    brand_name: str, candidates: list[Candidate], sitelinks: dict[str, int]
) -> Candidate | None:
    plausible = [candidate for candidate in candidates if _candidate_is_fashion(candidate)]
    if not plausible:
        return None
    target = _normalise(brand_name)
    plausible.sort(
        key=lambda candidate: (
            _normalise(candidate.label) == target,
            sitelinks.get(candidate.qid, 0),
            -candidate.rank,
        ),
        reverse=True,
    )
    best = plausible[0]
    if len(plausible) == 1:
        return best
    second = plausible[1]
    best_exact = _normalise(best.label) == target
    second_exact = _normalise(second.label) == target
    if best_exact and not second_exact:
        return best
    if sitelinks.get(best.qid, 0) >= max(2, 2 * sitelinks.get(second.qid, 0)):
        return best
    return None


def _resolve_live(
    entry: BenchmarkEntry, client: WikidataClient
) -> tuple[Resolution | None, str]:
    # Jitter supplements WikidataClient's own minimum-interval throttle.
    time.sleep(random.uniform(0.15, 0.45))
    candidates = client.search(entry.brand_name, limit=10)
    client.enrich(candidates)
    editions = client.wikipedia_sitelinks([candidate.qid for candidate in candidates])
    sitelinks = {qid: len(links) for qid, links in editions.items()}
    choice = _automatic_choice(entry.brand_name, candidates, sitelinks)
    if choice is None:
        rendered = "; ".join(
            f"{c.qid} | {c.label} | {c.description} | sitelinks={sitelinks.get(c.qid, 0)}"
            for c in candidates[:5]
        )
        return None, rendered or "no candidates"
    return Resolution(choice.qid, choice.label, choice.description), ""


def resolve_benchmarks(
    entries: list[BenchmarkEntry], *, offline: bool
) -> tuple[dict[str, Resolution], list[dict[str, str]]]:
    cached = _load_resolution_map()
    resolutions: dict[str, Resolution] = {}
    manual: list[dict[str, str]] = []
    client = None if offline else WikidataClient(CACHE / "wikidata", min_interval=1.0)
    changed = False
    try:
        for entry in entries:
            record = cached.get(entry.brand_name)
            if entry.known_qid:
                qid = entry.known_qid
                if record and record.get("qid") == qid:
                    resolutions[entry.brand_name] = Resolution(
                        qid, record.get("label", entry.brand_name), record.get("description", "")
                    )
                else:
                    resolutions[entry.brand_name] = Resolution(qid, entry.brand_name, "")
                continue
            if record and record.get("qid", "").startswith("Q"):
                resolutions[entry.brand_name] = Resolution(
                    record["qid"], record.get("label", entry.brand_name),
                    record.get("description", ""),
                )
                continue
            if offline:
                manual.append({
                    "brand_name": entry.brand_name, "source": entry.source,
                    "quarter": entry.quarter, "reason": "not cached (offline)",
                    "candidates": "",
                })
                continue
            assert client is not None
            resolved, candidates = _resolve_live(entry, client)
            if resolved is None:
                manual.append({
                    "brand_name": entry.brand_name, "source": entry.source,
                    "quarter": entry.quarter, "reason": "ambiguous or no fashion candidate",
                    "candidates": candidates,
                })
                continue
            resolutions[entry.brand_name] = resolved
            cached[entry.brand_name] = {
                "qid": resolved.qid, "label": resolved.label,
                "description": resolved.description, "resolution": "automatic",
            }
            changed = True
    finally:
        if client is not None:
            client.close()
    if changed:
        RESOLUTION_MAP.write_text(
            json.dumps(cached, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    return resolutions, manual


def coverage_failures(
    entries: list[BenchmarkEntry], resolutions: dict[str, Resolution],
    universe_qids: set[str], approved_qids: frozenset[str],
) -> list[dict[str, str]]:
    covered = universe_qids | set(EXTRA_QIDS) | set(approved_qids)
    failures: list[dict[str, str]] = []
    for entry in entries:
        resolved = resolutions.get(entry.brand_name)
        if resolved is not None and resolved.qid not in covered:
            failures.append({
                "brand_name": entry.brand_name, "qid": resolved.qid,
                "source": entry.source, "quarter": entry.quarter,
                "reason": "benchmark Q-ID absent from viable universe",
                "description": resolved.description,
            })
    return failures


def assert_benchmark_coverage(
    entries: list[BenchmarkEntry], resolutions: dict[str, Resolution],
    universe_qids: set[str], approved_qids: frozenset[str],
) -> None:
    unresolved = [entry.brand_name for entry in entries if entry.brand_name not in resolutions]
    failures = coverage_failures(entries, resolutions, universe_qids, approved_qids)
    if unresolved or failures:
        lines = ["Benchmark coverage failed:"]
        lines += [f"  UNRESOLVED {name}" for name in unresolved]
        lines += [f"  MISSING {row['brand_name']} ({row['qid']})" for row in failures]
        raise AssertionError("\n".join(lines))


def discover_keyword_rescue(
    universe_qids: set[str], *, max_results_per_query: int = 20
) -> list[dict[str, str]]:
    """Bounded opt-in CirrusSearch rescue for generic company classes.

    This is not called by the monthly job.  Four classes x three precise phrases,
    one page each, keeps the search bounded; descriptions receive the same strict
    fashion/defunct/exclusion filters as the ontology universe.
    """
    classes = ("Q4830453", "Q43229", "Q891723", "Q783794")
    phrases = ('"fashion house"', '"fashion brand"', '"luxury fashion"')
    client = WikidataClient(CACHE / "wikidata", min_interval=1.0)
    qids: set[str] = set()
    try:
        for class_qid in classes:
            for phrase in phrases:
                time.sleep(random.uniform(0.15, 0.45))
                data = client._get({  # cached/throttled low-level API wrapper
                    "action": "query", "list": "search", "srnamespace": 0,
                    "srlimit": max_results_per_query,
                    "srsearch": f"haswbstatement:P31={class_qid} {phrase}",
                })
                for hit in data.get("query", {}).get("search", []):
                    title = hit.get("title", "")
                    if re.fullmatch(r"Q\d+", title):
                        qids.add(title)
        candidates: list[dict[str, str]] = []
        for start in range(0, len(qids), 50):
            batch = sorted(qids)[start:start + 50]
            data = client._get({
                "action": "wbgetentities", "ids": "|".join(batch),
                "props": "labels|descriptions|sitelinks", "languages": "en",
            })
            for qid, entity in data.get("entities", {}).items():
                if qid in universe_qids:
                    continue
                label = entity.get("labels", {}).get("en", {}).get("value", "")
                desc = entity.get("descriptions", {}).get("en", {}).get("value", "")
                if not label or not _FASHION_DESC.search(desc):
                    continue
                if _EXCLUDE_DESC.search(desc) or _DEFUNCT_DESC.search(desc):
                    continue
                candidates.append({
                    "brand_name": label, "qid": qid, "source": "keyword-rescue",
                    "quarter": "", "reason": "generic-class CirrusSearch rescue",
                    "description": desc,
                })
        return candidates
    finally:
        client.close()


def run_reconciliation(
    *, offline: bool, keyword_rescue: bool = False
) -> ReconciliationResult:
    entries = load_benchmarks()
    resolutions, manual = resolve_benchmarks(entries, offline=offline)
    approved = load_approved_qids(APPROVED_FILE)
    sp = SparqlClient(CACHE / "sparql")
    try:
        universe = fetch_universe(sp, deep=True)
    finally:
        sp.close()
    viable = viable_items(universe, approved)
    viable_qids = {item.qid for item in viable}
    missing = coverage_failures(entries, resolutions, viable_qids, approved)
    proposals = list(missing)
    if keyword_rescue:
        existing = {row["qid"] for row in proposals}
        for row in discover_keyword_rescue(viable_qids):
            if row["qid"] not in existing:
                proposals.append(row)
                existing.add(row["qid"])
    _write_csv(PROPOSAL_FILE, _PROPOSAL_FIELDS, proposals)
    _write_csv(MANUAL_FILE, _MANUAL_FIELDS, manual)
    return ReconciliationResult(
        len(entries), len(resolutions), len(viable), missing, manual
    )


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Reconcile monthly brand benchmarks.")
    parser.add_argument("--check", action="store_true", help="fail if benchmark coverage regresses")
    parser.add_argument("--offline", action="store_true", help="use only cached universe and resolutions")
    parser.add_argument(
        "--keyword-rescue", action="store_true",
        help="opt in to bounded generic-company CirrusSearch (default: off)",
    )
    args = parser.parse_args(argv)
    result = run_reconciliation(offline=args.offline, keyword_rescue=args.keyword_rescue)
    print(result.summary())
    print(f"  proposal: {result.proposal_path}")
    if args.check:
        entries = load_benchmarks()
        resolutions, _ = resolve_benchmarks(entries, offline=True)
        approved = load_approved_qids(APPROVED_FILE)
        sp = SparqlClient(CACHE / "sparql")
        try:
            viable = viable_items(fetch_universe(sp, deep=True), approved)
        finally:
            sp.close()
        try:
            assert_benchmark_coverage(
                entries, resolutions, {item.qid for item in viable}, approved
            )
        except AssertionError as exc:
            print(exc)
            return 1
        print("Benchmark coverage passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
