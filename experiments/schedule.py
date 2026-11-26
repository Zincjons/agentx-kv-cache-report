"""Replay Sage schedule directly with the original AgentX Cached input metric.

Only schedule/*.jsonl is read. Rule-generated session labels are never loaded.
Writes per-request results for daily cold starts and one continuous multi-day run.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import sys
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

sys.dont_write_bytecode = True
PROJECT = Path(__file__).resolve().parents[1]


@dataclass(slots=True)
class Request:
    timestamp: int
    ordinal: int
    request_id: str
    model: str
    input_tokens: int
    output_tokens: int
    hashes: tuple[int, ...]


def load_reference(path: Path):
    spec = importlib.util.spec_from_file_location("agentx_metric_reference", path)
    if spec is None or spec.loader is None:
        raise ValueError(f"Cannot import reference: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def empty_totals() -> dict:
    return dict(requests=0, input_tokens=0, output_tokens=0, cached_input_tokens=0,
                zero_cached_requests=0, fully_cached_requests=0,
                partially_cached_requests=0)


def record(totals: dict, request: Request, cached: int) -> None:
    assert 0 <= cached <= request.input_tokens
    totals["requests"] += 1
    totals["input_tokens"] += request.input_tokens
    totals["output_tokens"] += request.output_tokens
    totals["cached_input_tokens"] += cached
    if cached == 0:
        totals["zero_cached_requests"] += 1
    elif cached == request.input_tokens:
        totals["fully_cached_requests"] += 1
    else:
        totals["partially_cached_requests"] += 1


def finish(totals: dict) -> dict:
    return {
        **totals,
        "uncached_input_tokens": totals["input_tokens"] - totals["cached_input_tokens"],
        "cached_input_rate": (totals["cached_input_tokens"] / totals["input_tokens"]
                              if totals["input_tokens"] else 0.0),
    }


def load_day(root: Path, day: str, reference) -> tuple[list[Request], dict]:
    paths = sorted((root / day / "Sage-S2.1-flash" / "schedule").glob("*.jsonl"))
    if not paths:
        raise FileNotFoundError(f"No schedule JSONL files for {day}")
    requests = []
    sources = []
    for path in paths:
        sha = hashlib.sha256()
        rows = 0
        with path.open("rb") as source:
            for line_number, line in enumerate(source, 1):
                sha.update(line)
                row = json.loads(line)
                timestamp = row["timestamp"]
                hashes = row["hash_ids"]
                if type(timestamp) is not int or not 0 <= timestamp < 86_400_000:
                    raise ValueError(f"Invalid daily timestamp at {path}:{line_number}")
                if not isinstance(hashes, list) or any(type(b) is not int for b in hashes):
                    raise ValueError(f"Invalid hash_ids at {path}:{line_number}")
                requests.append(Request(
                    timestamp, len(requests), row["request_id"], row["model"],
                    reference.token_count(row["input_length"]),
                    reference.token_count(row["output_length"]), tuple(hashes)))
                rows += 1
        sources.append({"path": str(path), "rows": rows,
                        "bytes": path.stat().st_size, "sha256": sha.hexdigest()})
    # A deterministic order for simultaneous arrivals, with no deduplication.
    requests.sort(key=lambda r: (r.timestamp, r.ordinal))
    return requests, {
        "files": sources,
        "models": dict(Counter(r.model for r in requests)),
        "duplicate_request_id_rows": len(requests) - len({r.request_id for r in requests}),
        "same_timestamp_adjacent_pairs": sum(
            a.timestamp == b.timestamp for a, b in zip(requests, requests[1:])),
        "empty_hash_requests": sum(not r.hashes for r in requests),
        "hash_count_not_ceiling_input_blocks": sum(
            len(r.hashes) != (r.input_tokens + 63) // 64 for r in requests),
        "first_timestamp_ms": requests[0].timestamp,
        "last_timestamp_ms": requests[-1].timestamp,
        "block_accesses": sum(len(r.hashes) for r in requests),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path,
                        default=PROJECT / "data" / "sage")
    parser.add_argument("--reference-script", type=Path,
                        default=PROJECT / "analyze_agentx.py")
    parser.add_argument("--days", nargs="+",
                        default=["2026-11-16", "2026-11-17", "2026-11-18"])
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if len(set(args.days)) != len(args.days):
        parser.error("Days must not repeat")
    if args.output_dir.exists():
        raise FileExistsError(f"Output directory already exists: {args.output_dir}")
    reference = load_reference(args.reference_script)
    args.output_dir.mkdir(parents=True)
    started = time.perf_counter()
    continuous_history: set[int] = set()
    continuous_totals = empty_totals()
    independent_totals = empty_totals()
    daily_results = []
    columns = ["date", "request_id", "timestamp_ms", "model", "input_tokens",
               "output_tokens", "input_blocks", "daily_hit_blocks",
               "daily_cached_input_tokens", "daily_uncached_input_tokens",
               "continuous_hit_blocks", "continuous_cached_input_tokens",
               "continuous_uncached_input_tokens"]
    with (args.output_dir / "requests.csv").open("w", newline="", encoding="utf-8") as out:
        writer = csv.writer(out)
        writer.writerow(columns)
        for day in sorted(args.days):
            day_start = time.perf_counter()
            stream, metadata = load_day(args.data_root, day, reference)
            daily_history: set[int] = set()
            daily_totals = empty_totals()
            warm_totals = empty_totals()
            for r in stream:
                # Exactly the helper and update rule used by analyze_agentx.py.
                daily_hits = reference.count_prefix_hits(r.hashes, daily_history)
                daily_cached = min(r.input_tokens, daily_hits * 64)
                daily_history.update(r.hashes)
                continuous_hits = reference.count_prefix_hits(r.hashes, continuous_history)
                continuous_cached = min(r.input_tokens, continuous_hits * 64)
                continuous_history.update(r.hashes)
                assert continuous_cached >= daily_cached
                for totals in [daily_totals, independent_totals]:
                    record(totals, r, daily_cached)
                for totals in [warm_totals, continuous_totals]:
                    record(totals, r, continuous_cached)
                writer.writerow([
                    day, r.request_id, r.timestamp, r.model, r.input_tokens,
                    r.output_tokens, len(r.hashes), daily_hits, daily_cached,
                    r.input_tokens - daily_cached, continuous_hits, continuous_cached,
                    r.input_tokens - continuous_cached])
            result = {
                "day": day, **metadata,
                "unique_blocks_in_day": len(daily_history),
                "daily_cold_start": finish(daily_totals),
                "continuous_run_day_slice": finish(warm_totals),
                "extra_cached_tokens_from_prior_days": (
                    warm_totals["cached_input_tokens"] - daily_totals["cached_input_tokens"]),
                "wall_seconds": time.perf_counter() - day_start,
            }
            daily_results.append(result)
            print(json.dumps({k: v for k, v in result.items() if k != "files"},
                             ensure_ascii=False), flush=True)
            del stream, daily_history
    report = {
        "metric": "InferenceX Cached input / original AgentX method",
        "input_representation": "schedule/*.jsonl only",
        "reference_script": str(args.reference_script),
        "reference_script_sha256": hashlib.sha256(args.reference_script.read_bytes()).hexdigest(),
        "block_size": 64,
        "history_scope": "one shared input-block history; no session labels",
        "ordering": "date, timestamp; ties retain sorted source filename and line order",
        "cache_assumptions": [
            "Unlimited capacity, no eviction or TTL.",
            "A replayed request immediately adds all its input blocks to history.",
            "Only input hashes are admitted; output token counts do not populate history.",
            "All schedule rows are retained without filtering or deduplication.",
            "Hash IDs are assumed comparable across files and days as in the handover.",
            "This is an offline reuse metric, without concurrent execution timing."],
        "daily_results": daily_results,
        "daily_cold_starts_weighted_total": finish(independent_totals),
        "continuous_three_day_run": finish(continuous_totals),
        "continuous_unique_blocks": len(continuous_history),
        "wall_seconds": time.perf_counter() - started,
    }
    (args.output_dir / "summary.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("TOTAL", json.dumps({
        "daily_cold_starts_weighted_total": report["daily_cold_starts_weighted_total"],
        "continuous_three_day_run": report["continuous_three_day_run"],
        "wall_seconds": report["wall_seconds"]}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
