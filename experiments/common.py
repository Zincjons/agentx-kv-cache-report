"""Join reliable common requests from trace and schedule, then replay by user.

Strict fingerprints include model, input/output lengths, all ordered hashes,
streaming flag, and API duration. Duplicate rows are handled as occurrences.
No arbitrary assignment is made when common-row membership or uid is ambiguous.
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
from dataclasses import dataclass
from pathlib import Path

sys.dont_write_bytecode = True
PROJECT = Path(__file__).resolve().parents[1]
POLICIES = ("user_daily", "global_daily", "user_continuous", "global_continuous")


@dataclass(slots=True)
class Scheduled:
    row: dict
    ordinal: int
    source: str
    line: int
    key: bytes


@dataclass(slots=True)
class Traced:
    uid: str
    group: str
    index: int
    relative_ms: int
    model: str
    input_tokens: int

    @property
    def identity(self):
        return self.group, self.index


def fingerprint(row, trace=False):
    h = hashlib.sha256()
    h.update(json.dumps([
        row["model"], row["in" if trace else "input_length"],
        row["out" if trace else "output_length"], row["api_time"],
        row["type"] == "s" if trace else row["stream"]],
        separators=(",", ":")).encode())
    h.update(array("Q", row["hash_ids"]).tobytes())
    return h.digest()


def reference(path):
    spec = importlib.util.spec_from_file_location("agentx_original_reference", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def totals():
    return {"requests": 0, "input_tokens": 0, "output_tokens": 0,
            **{f"{p}_cached_tokens": 0 for p in POLICIES}}


def add(t, row, scores):
    t["requests"] += 1
    t["input_tokens"] += row["input_length"]
    t["output_tokens"] += row["output_length"]
    for p, n in scores.items():
        t[f"{p}_cached_tokens"] += n


def finish(t):
    return {**t, **{f"{p}_rate": (t[f"{p}_cached_tokens"] / t["input_tokens"]
                                  if t["input_tokens"] else 0.0) for p in POLICIES}}


def join_day(root, day):
    base = root / day / "Sage-S2.1-flash"
    schedule, trace = defaultdict(list), defaultdict(list)
    ordered = []
    groups, sources = {}, []
    for path in sorted((base / "schedule").glob("*.jsonl")):
        sha = hashlib.sha256()
        n = 0
        with path.open("rb") as source:
            for line, raw in enumerate(source, 1):
                sha.update(raw)
                row = json.loads(raw)
                key = fingerprint(row)
                r = Scheduled(row, len(ordered), str(path), line, key)
                schedule[key].append(r)
                ordered.append(r)
                n += 1
        sources.append({"path": str(path), "rows": n, "sha256": sha.hexdigest()})
    for path in sorted((base / "trace").glob("*.json")):
        raw = path.read_bytes()
        c = json.loads(raw)
        assert c["id"] == path.stem and c["block_size"] == 64
        uid = c["id"].rsplit("-", 1)[0]
        assert uid.isdecimal()
        records = []
        for index, row in enumerate(c["requests"], 1):
            assert row["type"] in ("n", "s")
            key = fingerprint(row, True)
            r = Traced(uid, c["id"], index, round(row["t"] * 1000),
                       row["model"], row["in"])
            trace[key].append(r)
            records.append((key, r))
        groups[c["id"]] = records
        sources.append({"path": str(path), "rows": len(records),
                        "sha256": hashlib.sha256(raw).hexdigest()})
    if not ordered or not trace:
        raise ValueError(f"Missing input data for {day}")

    # Derive group time origins only from fingerprints unique on BOTH sides.
    offsets = {}
    offset_anchor_counts = {}
    for group, records in groups.items():
        votes = Counter(schedule[key][0].row["timestamp"] - r.relative_ms
                        for key, r in records
                        if len(schedule.get(key, ())) == 1 and len(trace[key]) == 1)
        if len(votes) > 1:
            raise ValueError(f"Conflicting time origins: {day}/{group}: {votes}")
        if votes:
            offsets[group] = next(iter(votes))
            offset_anchor_counts[group] = sum(votes.values())

    matched = {}
    consumed_trace = set()
    methods = Counter()
    unresolved = []

    def assign(ss, tt, method):
        assert len(ss) == len(tt) and len({t.uid for t in tt}) == 1
        uid = tt[0].uid
        for s in ss:
            assert s.ordinal not in matched
            matched[s.ordinal] = {
                "uid": uid, "match_method": method,
                "trace_group": tt[0].group if len(tt) == 1 else "",
                "trace_request_index": tt[0].index if len(tt) == 1 else "",
                "trace_candidate_count": len(tt),
            }
        for t in tt:
            assert t.identity not in consumed_trace
            consumed_trace.add(t.identity)
        methods[method] += len(ss)

    for key in schedule.keys() & trace.keys():
        ss, tt = schedule[key], trace[key]
        by_time_s = defaultdict(list)
        by_time_t = defaultdict(list)
        for s in ss:
            by_time_s[s.row["timestamp"]].append(s)
        for t in tt:
            if t.group in offsets:
                by_time_t[offsets[t.group] + t.relative_ms].append(t)
        for ts in by_time_s.keys() & by_time_t.keys():
            st, tr = by_time_s[ts], by_time_t[ts]
            if len(st) == len(tr) and len({t.uid for t in tr}) == 1:
                assign(st, tr, "fingerprint_and_time" if len(st) == 1
                       else "same_uid_fingerprint_and_time")
        remaining_s = [s for s in ss if s.ordinal not in matched]
        remaining_t = [t for t in tt if t.identity not in consumed_trace]
        if (remaining_s and len(remaining_s) == len(remaining_t)
                and len({t.uid for t in remaining_t}) == 1):
            assign(remaining_s, remaining_t,
                   "unique_remaining_fingerprint" if len(remaining_s) == 1
                   else "same_uid_duplicate_fingerprint")
        elif remaining_s or remaining_t:
            unresolved.append({
                "fingerprint_sha256": key.hex(),
                "schedule_remaining": len(remaining_s),
                "trace_remaining": len(remaining_t),
                "candidate_common_occurrences": min(len(remaining_s), len(remaining_t)),
                "candidate_uids": dict(Counter(t.uid for t in remaining_t)),
                "input_tokens_per_request": ss[0].row["input_length"],
                "model": ss[0].row["model"],
                "reason": "occurrence_counts_differ" if len(remaining_s) != len(remaining_t)
                          else "uid_not_unique",
            })
    strict_n = sum(min(len(schedule[k]), len(trace[k])) for k in schedule.keys() & trace.keys())
    strict_tokens = sum(min(len(schedule[k]), len(trace[k])) * schedule[k][0].row["input_length"]
                        for k in schedule.keys() & trace.keys())
    accepted = sorted((s for s in ordered if s.ordinal in matched),
                      key=lambda s: (s.row["timestamp"], s.ordinal))
    accepted_tokens = sum(s.row["input_length"] for s in accepted)
    assert len(accepted) <= strict_n
    assert strict_n - len(accepted) == sum(x["candidate_common_occurrences"] for x in unresolved)
    accepted_fp = Counter(s.key for s in accepted)
    assert all(n <= len(trace[key]) and n <= len(schedule[key]) for key, n in accepted_fp.items())
    audit = {
        "day": day, "schedule_requests": len(ordered),
        "trace_requests": sum(map(len, trace.values())),
        "strict_common_requests": strict_n, "strict_common_input_tokens": strict_tokens,
        "accepted_requests": len(accepted), "accepted_input_tokens": accepted_tokens,
        "unresolved_common_requests": strict_n - len(accepted),
        "unresolved_common_input_tokens": strict_tokens - accepted_tokens,
        "schedule_only_occurrences": len(ordered) - strict_n,
        "trace_only_occurrences": sum(map(len, trace.values())) - strict_n,
        "match_methods": dict(methods), "anchored_groups": len(offsets),
        "total_groups": len(groups), "unresolved": unresolved,
        "models": dict(Counter(s.row["model"] for s in accepted)),
        "users": dict(Counter(matched[s.ordinal]["uid"] for s in accepted)),
        "sources": sources,
    }
    rejected = [s for s in ordered if s.ordinal not in matched]
    return accepted, matched, rejected, audit


def main(args):
    if args.output_dir.exists():
        raise FileExistsError(args.output_dir)
    args.output_dir.mkdir(parents=True)
    ref = reference(args.reference_script)
    warm_user, warm_global = defaultdict(set), defaultdict(set)
    total = totals()
    daily = []
    overall_users = defaultdict(totals)
    begin = time.perf_counter()
    columns = ["date", "request_id", "uid", "model", "timestamp", "src_time",
               "input_tokens", "output_tokens", "input_blocks", "stream", "api_time",
               "match_method", "trace_group", "trace_request_index", "trace_candidate_count",
               "schedule_file", "schedule_line", "fingerprint_sha256",
               *[f"{p}_cached_tokens" for p in POLICIES]]
    rejected_columns = ["date", "request_id", "timestamp", "model", "input_tokens",
                        "output_tokens", "schedule_file", "schedule_line", "fingerprint_sha256"]
    with (args.output_dir / "matched_requests.csv").open("w", newline="", encoding="utf-8") as out, \
         (args.output_dir / "excluded_schedule.csv").open("w", newline="", encoding="utf-8") as rej, \
         (args.output_dir / "joined_samples.jsonl").open("w", encoding="utf-8") as samples:
        writer = csv.DictWriter(out, fieldnames=columns)
        rejected_writer = csv.DictWriter(rej, fieldnames=rejected_columns)
        writer.writeheader()
        rejected_writer.writeheader()
        for day in sorted(args.days):
            accepted, assignments, rejected, audit = join_day(args.data_root, day)
            cold_user, cold_global = defaultdict(set), defaultdict(set)
            day_total = totals()
            day_users = defaultdict(totals)
            sampled = False
            for s in accepted:
                r = s.row
                a = assignments[s.ordinal]
                uid, model = a["uid"], r["model"]
                scores = {}
                for p, h in {
                    "user_daily": cold_user[uid, model], "global_daily": cold_global[model],
                    "user_continuous": warm_user[uid, model], "global_continuous": warm_global[model],
                }.items():
                    scores[p] = min(r["input_length"], 64 * ref.count_prefix_hits(r["hash_ids"], h))
                    h.update(r["hash_ids"])
                assert scores["global_daily"] >= scores["user_daily"]
                assert scores["global_continuous"] >= scores["user_continuous"]
                assert scores["user_continuous"] >= scores["user_daily"]
                for target in [total, day_total, overall_users[uid], day_users[uid]]:
                    add(target, r, scores)
                writer.writerow({
                    "date": day, "request_id": r["request_id"], "model": model,
                    "timestamp": r["timestamp"], "src_time": r["src_time"],
                    "input_tokens": r["input_length"], "output_tokens": r["output_length"],
                    "input_blocks": len(r["hash_ids"]), "stream": r["stream"],
                    "api_time": r["api_time"], **a,
                    "schedule_file": s.source, "schedule_line": s.line,
                    "fingerprint_sha256": s.key.hex(),
                    **{f"{p}_cached_tokens": n for p, n in scores.items()},
                })
                if not sampled and uid == "71" and r["input_length"] < 256:
                    samples.write(json.dumps({"date": day, **r, **a}, ensure_ascii=False) + "\n")
                    sampled = True
            for s in rejected:
                r = s.row
                rejected_writer.writerow({
                    "date": day, "request_id": r["request_id"], "timestamp": r["timestamp"],
                    "model": r["model"], "input_tokens": r["input_length"],
                    "output_tokens": r["output_length"], "schedule_file": s.source,
                    "schedule_line": s.line, "fingerprint_sha256": s.key.hex(),
                })
            assert day_total["requests"] == audit["accepted_requests"]
            assert day_total["input_tokens"] == audit["accepted_input_tokens"]
            result = {"day": day, "totals": finish(day_total),
                      "users": {u: finish(t) for u, t in sorted(day_users.items())},
                      "join_audit": audit}
            daily.append(result)
            print(json.dumps({"day": day, "totals": result["totals"],
                              "join": {k: v for k, v in audit.items()
                                       if k not in ("sources", "unresolved")}}, ensure_ascii=False), flush=True)
            del accepted, assignments, rejected
    report = {
        "metric": "Original AgentX Cached input, using reliable trace/schedule intersection",
        "matching_fields": ["date", "model", "input_length/in", "output_length/out",
                            "ordered hash_ids", "stream/type", "api_time"],
        "identity_policy": "Use exact group-relative/absolute time where anchored; otherwise require equal residual multiplicity and one unanimous uid. Exclude ambiguous common occurrences.",
        "duplicate_policy": "Each schedule row is counted once; no Cartesian joins or deduplication.",
        "trace_location_policy": "Leave group/index empty when uid is known but exact duplicate trace position is not identifiable.",
        "ordering": "date, schedule.timestamp; ties retain sorted source filename and line order",
        "cache_scope": "primary=(uid,model); controlled global comparison=(model)",
        "cache_assumptions": "64 tokens/block; unlimited history; no expiry or eviction; score then admit all inputs; no concurrent execution completion model.",
        "reference_script": str(args.reference_script),
        "reference_script_sha256": hashlib.sha256(args.reference_script.read_bytes()).hexdigest(),
        "daily_results": daily, "totals": finish(total),
        "users": {u: finish(t) for u, t in sorted(overall_users.items())},
        "wall_seconds": time.perf_counter() - begin,
    }
    (args.output_dir / "summary.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("TOTAL", json.dumps(report["totals"], ensure_ascii=False), flush=True)


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
    main(args)
