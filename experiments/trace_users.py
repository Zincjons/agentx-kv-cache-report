"""Measure AgentX-style Cached input from trace, retaining history by user.

Schedule is used only to validate group chronology, never to supply scoring rows.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import sys
import time
from array import array
from collections import Counter, defaultdict
from pathlib import Path

sys.dont_write_bytecode = True
PROJECT = Path(__file__).resolve().parents[1]
POLICIES = ("rule_group", "user_daily", "user_model_daily",
            "user_continuous", "user_model_continuous")


def load_reference(path):
    spec = importlib.util.spec_from_file_location("original_agentx_metric", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def fingerprint(row, trace=False):
    h = hashlib.sha256()
    h.update(json.dumps([
        row["model"], row["in" if trace else "input_length"],
        row["out" if trace else "output_length"], row["api_time"],
        row["type"] == "s" if trace else row["stream"]],
        separators=(",", ":")).encode())
    h.update(array("Q", row["hash_ids"]).tobytes())
    return h.digest()


def metric():
    return {"requests": 0, "input_tokens": 0, "output_tokens": 0,
            **{f"{p}_cached_tokens": 0 for p in POLICIES}}


def finalize(t):
    return {**t, **{f"{p}_rate": (t[f"{p}_cached_tokens"] / t["input_tokens"]
                                  if t["input_tokens"] else 0.0) for p in POLICIES}}


def add(t, tin, tout, scores):
    t["requests"] += 1
    t["input_tokens"] += tin
    t["output_tokens"] += tout
    for p, cached in scores.items():
        t[f"{p}_cached_tokens"] += cached


def validate_order(groups, schedule, trace_counts):
    unanchored = []
    conflicts = []
    for g in groups:
        offsets = Counter(schedule[fp][0] - t for fp, t in g.pop("records")
                          if len(schedule.get(fp, ())) == 1 and trace_counts[fp] == 1)
        g["unique_time_anchors"] = sum(offsets.values())
        if not offsets:
            unanchored.append(g)
        elif len(offsets) != 1:
            conflicts.append({"id": g["id"], "offsets": dict(offsets)})
        else:
            g["start_ms"] = next(iter(offsets))
            g["end_ms"] = g["start_ms"] + g["last_relative_ms"]
    inversions, overlaps = [], []
    for uid in sorted({g["uid"] for g in groups}):
        known = sorted((g for g in groups if g["uid"] == uid and "start_ms" in g),
                       key=lambda g: g["sequence"])
        for a, b in zip(known, known[1:]):
            if a["start_ms"] > b["start_ms"]:
                inversions.append([a["id"], b["id"]])
            if a["end_ms"] > b["start_ms"]:
                overlaps.append([a["id"], b["id"]])
    return {
        "groups": len(groups), "anchored_groups": len(groups) - len(unanchored) - len(conflicts),
        "unanchored_groups": len(unanchored),
        "unanchored_requests": sum(g["requests"] for g in unanchored),
        "unanchored_input_tokens": sum(g["input_tokens"] for g in unanchored),
        "unanchored_users": dict(Counter(g["uid"] for g in unanchored)),
        "unanchored_all_requests_are_single_block_three_tokens": all(
            g["all_single_block_three_tokens"] for g in unanchored),
        "offset_conflicts": conflicts, "sequence_inversions": inversions,
        "anchored_group_overlaps": overlaps,
    }


def run(args):
    started = time.perf_counter()
    ref = load_reference(args.reference_script)
    if args.output_dir.exists():
        raise FileExistsError(args.output_dir)
    args.output_dir.mkdir(parents=True)
    continuous_user = defaultdict(set)
    continuous_user_model = defaultdict(set)
    total = metric()
    all_users = defaultdict(metric)
    all_models = defaultdict(metric)
    daily = []
    with (args.output_dir / "requests.csv").open("w", newline="", encoding="utf-8") as out:
        columns = ["date", "uid", "model", "trace_group", "request_index",
                   "relative_t_ms", "input_tokens", "output_tokens", "input_blocks",
                   *[f"{p}_cached_tokens" for p in POLICIES]]
        writer = csv.DictWriter(out, fieldnames=columns)
        writer.writeheader()
        for day in sorted(args.days):
            begin = time.perf_counter()
            base = args.data_root / day / "Sage-S2.1-flash"
            schedule = defaultdict(list)
            for path in sorted((base / "schedule").glob("*.jsonl")):
                with path.open() as source:
                    for line in source:
                        row = json.loads(line)
                        schedule[fingerprint(row)].append(row["timestamp"])
            if not schedule:
                raise ValueError(f"No schedule to validate chronology for {day}")
            user_history = defaultdict(set)
            user_model_history = defaultdict(set)
            users = defaultdict(metric)
            models = defaultdict(metric)
            day_total = metric()
            groups, sources = [], []
            trace_counts = Counter()
            paths = sorted((base / "trace").glob("*.json"),
                           key=lambda p: tuple(map(int, p.stem.rsplit("-", 1))))
            if not paths:
                raise ValueError(f"No trace files for {day}")
            for path in paths:
                raw = path.read_bytes()
                c = json.loads(raw)
                assert c["id"] == path.stem and c["block_size"] == 64
                uid, sequence = c["id"].rsplit("-", 1)
                assert uid.isdecimal() and sequence.isdecimal()
                rs = c["requests"]
                assert rs and all(r["type"] in ("s", "n") for r in rs)
                records = [(fingerprint(r, True), round(r["t"] * 1000)) for r in rs]
                assert records[0][1] == 0
                assert all(a[1] <= b[1] for a, b in zip(records, records[1:]))
                trace_counts.update(fp for fp, _ in records)
                groups.append({
                    "id": c["id"], "uid": uid, "sequence": int(sequence),
                    "requests": len(rs), "input_tokens": sum(ref.token_count(r["in"]) for r in rs),
                    "last_relative_ms": records[-1][1], "records": records,
                    "all_single_block_three_tokens": all(
                        r["in"] == 3 and len(r["hash_ids"]) == 1 for r in rs),
                    "model_counts": dict(Counter(r["model"] for r in rs)),
                })
                group_history = set()
                for index, r in enumerate(rs, 1):
                    tin, tout = ref.token_count(r["in"]), ref.token_count(r["out"])
                    model = r["model"]
                    hashes = r["hash_ids"]
                    histories = {
                        "rule_group": group_history,
                        "user_daily": user_history[uid],
                        "user_model_daily": user_model_history[uid, model],
                        "user_continuous": continuous_user[uid],
                        "user_model_continuous": continuous_user_model[uid, model],
                    }
                    scores = {}
                    for policy, history in histories.items():
                        scores[policy] = min(tin, 64 * ref.count_prefix_hits(hashes, history))
                        history.update(hashes)
                    assert scores["user_daily"] >= scores["rule_group"]
                    assert scores["user_daily"] >= scores["user_model_daily"]
                    assert scores["user_continuous"] >= scores["user_daily"]
                    assert scores["user_model_continuous"] >= scores["user_model_daily"]
                    for target in [day_total, total, users[uid], models[model],
                                   all_users[uid], all_models[model]]:
                        add(target, tin, tout, scores)
                    writer.writerow({
                        "date": day, "uid": uid, "model": model, "trace_group": c["id"],
                        "request_index": index, "relative_t_ms": round(r["t"] * 1000),
                        "input_tokens": tin, "output_tokens": tout, "input_blocks": len(hashes),
                        **{f"{p}_cached_tokens": v for p, v in scores.items()},
                    })
                sources.append({"path": str(path), "requests": len(rs),
                                "sha256": hashlib.sha256(raw).hexdigest()})
            order = validate_order(groups, schedule, trace_counts)
            assert not order["offset_conflicts"] and not order["sequence_inversions"]
            assert not order["anchored_group_overlaps"]
            result = {"day": day, "totals": finalize(day_total),
                      "users": {k: finalize(v) for k, v in sorted(users.items())},
                      "models": {k: finalize(v) for k, v in sorted(models.items())},
                      "order_validation": order, "groups": groups, "sources": sources,
                      "wall_seconds": time.perf_counter() - begin}
            daily.append(result)
            print(json.dumps({k: v for k, v in result.items() if k not in ("groups", "sources")},
                             ensure_ascii=False), flush=True)
    report = {
        "metric": "Original AgentX Cached input formula; histories retained across rule groups",
        "scoring_input": "trace/*.json only",
        "primary_cache_scope": "(uid, model); independent daily cold starts and continuous multi-day history",
        "comparison_cache_scopes": list(POLICIES),
        "ordering": "date, numeric uid, numeric group suffix, original in-group request order",
        "ordering_note": "Inter-user order is immaterial because user histories are isolated. Group order is checked using unique schedule anchors; unanchored groups remain in numeric suffix order.",
        "assumptions": ["64 tokens per block; unlimited history; no eviction or TTL.",
                        "Score first, then admit every input hash, exactly as the AgentX reference.",
                        "No outputs admitted; no inference about real task boundaries.",
                        "Hash IDs assumed comparable across rule groups and dates.",
                        "No concurrent KV completion timing is modeled."],
        "reference_script": str(args.reference_script),
        "reference_script_sha256": hashlib.sha256(args.reference_script.read_bytes()).hexdigest(),
        "daily_results": daily, "totals": finalize(total),
        "users": {k: finalize(v) for k, v in sorted(all_users.items())},
        "models": {k: finalize(v) for k, v in sorted(all_models.items())},
        "wall_seconds": time.perf_counter() - started,
    }
    (args.output_dir / "summary.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("TOTAL", json.dumps({k: report[k] for k in ("totals", "users", "models", "wall_seconds")},
                              ensure_ascii=False), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path,
                        default=PROJECT / "data" / "sage")
    parser.add_argument("--reference-script", type=Path,
                        default=PROJECT / "analyze_agentx.py")
    parser.add_argument("--days", nargs="+", default=["2026-11-16", "2026-11-17", "2026-11-18"])
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if len(set(args.days)) != len(args.days):
        parser.error("Days must not repeat")
    run(args)
