"""Measure InferenceX Cached input from local-scope AgentX conversations."""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from itertools import islice
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4


PROJECT = Path(__file__).resolve().parent
DEFAULT_INPUT = PROJECT / "data" / "traces.jsonl"
AGENTX_REVISION = "23f152f6f0f9399a85901b89a6458def0ef16729"
REQUEST_COLUMNS = (
    "conversation_id",
    "request_index",
    "branch",
    "agent_id",
    "request_type",
    "model",
    "trace_t_seconds",
    "input_tokens",
    "output_tokens",
    "block_size",
    "input_blocks",
    "hit_blocks",
    "cached_input_tokens",
    "uncached_input_tokens",
)


@dataclass
class Totals:
    conversations: int = 0
    main_requests: int = 0
    subagent_requests: int = 0
    subagent_groups: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0


def token_count(value: int | float | None) -> int:
    if value is None:
        return 0
    if type(value) is int:
        return max(0, value)
    if isinstance(value, float) and math.isfinite(value):
        return max(0, math.floor(value + 0.5))
    raise ValueError(f"Invalid token count {value!r}")


def count_prefix_hits(hash_ids: Sequence[int], history: set[int]) -> int:
    hits = 0
    for block_id in hash_ids:
        if block_id not in history:
            break
        hits += 1
    return hits


def analyze_conversation(conversation: dict, writer, totals: Totals) -> None:
    conversation_id = conversation["id"]
    if conversation.get("hash_id_scope", "local") != "local":
        raise ValueError("Expected a local-scope AgentX conversation")
    block_size = conversation.get("block_size")
    if block_size is None:
        block_size = 64
    if type(block_size) is not int or block_size <= 0:
        raise ValueError(f"Invalid block size {block_size!r}")

    main_history: set[int] = set()
    request_index = 0
    for entry in conversation.get("requests") or []:
        if entry.get("type") == "subagent":
            branch = "subagent"
            agent_id = entry.get("agent_id")
            requests = entry.get("requests") or []
            # InferenceX starts each child from a snapshot of the main history.
            history = main_history.copy()
            totals.subagent_groups += 1
            totals.subagent_requests += len(requests)
        else:
            branch = "main"
            agent_id = None
            requests = (entry,)
            history = main_history
            totals.main_requests += 1

        for request in requests:
            hashes = request.get("hash_ids")
            if hashes is None:
                hashes = ()
            elif not isinstance(hashes, list):
                raise ValueError("Expected hash_ids to be a list")

            input_tokens = token_count(request.get("in"))
            output_tokens = token_count(request.get("out"))
            hit_blocks = count_prefix_hits(hashes, history)
            cached_tokens = min(input_tokens, hit_blocks * block_size)
            history.update(hashes)

            request_index += 1
            totals.input_tokens += input_tokens
            totals.output_tokens += output_tokens
            totals.cached_input_tokens += cached_tokens
            writer.writerow(
                (
                    conversation_id,
                    request_index,
                    branch,
                    agent_id,
                    request.get("type"),
                    request.get("model"),
                    request.get("t"),
                    input_tokens,
                    output_tokens,
                    block_size,
                    len(hashes),
                    hit_blocks,
                    cached_tokens,
                    input_tokens - cached_tokens,
                )
            )
    totals.conversations += 1


def run(input_path: Path, limit: int, output_dir: Path) -> dict:
    input_bytes = input_path.stat().st_size
    if output_dir.exists():
        raise FileExistsError(f"Output directory already exists: {output_dir}")
    output_dir.parent.mkdir(parents=True, exist_ok=True)

    totals = Totals()
    with TemporaryDirectory(prefix=".agentx-", dir=output_dir.parent) as work:
        staged = Path(work) / "analysis"
        staged.mkdir()
        wall_start = time.perf_counter()
        cpu_start = time.process_time()
        with input_path.open("rb") as source, (staged / "requests.csv").open(
            "w", encoding="utf-8", newline=""
        ) as csv_file:
            writer = csv.writer(csv_file)
            writer.writerow(REQUEST_COLUMNS)
            for line_index, line in enumerate(islice(source, limit), start=1):
                try:
                    analyze_conversation(json.loads(line), writer, totals)
                except (AttributeError, KeyError, TypeError, ValueError) as exc:
                    raise ValueError(f"Invalid conversation on JSONL line {line_index}: {exc}") from exc
        cpu_seconds = time.process_time() - cpu_start
        wall_seconds = time.perf_counter() - wall_start
        if totals.conversations == 0:
            raise ValueError("The source contains no AgentX conversations")

        logical_cpus = os.cpu_count()
        cpu_percent_one_core = 100 * cpu_seconds / wall_seconds
        summary = {
            "metric": "InferenceX Cached input",
            "input_path": str(input_path),
            "input_file_bytes": input_bytes,
            "conversation_limit": limit,
            **asdict(totals),
            "request_count": totals.main_requests + totals.subagent_requests,
            "uncached_input_tokens": totals.input_tokens - totals.cached_input_tokens,
            "cached_input_rate": (
                totals.cached_input_tokens / totals.input_tokens if totals.input_tokens else 0.0
            ),
            "execution": {
                "wall_seconds": wall_seconds,
                "process_cpu_seconds": cpu_seconds,
                "logical_cpus": logical_cpus,
                "cpu_percent_one_core": cpu_percent_one_core,
                "cpu_percent_machine_capacity": (
                    cpu_percent_one_core / logical_cpus if logical_cpus else None
                ),
            },
        }
        if input_path == DEFAULT_INPUT:
            summary["dataset_revision"] = AGENTX_REVISION
        (staged / "summary.json").write_text(
            json.dumps(summary, indent=2) + "\n", encoding="utf-8"
        )
        staged.rename(output_dir)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT, help="AgentX traces.jsonl")
    parser.add_argument(
        "--limit", type=int, default=3, help="Number of complete conversations from the start"
    )
    parser.add_argument("--output-dir", type=Path, help="Directory for summary.json and requests.csv")
    args = parser.parse_args()
    if args.limit < 1:
        parser.error("--limit must be at least 1")

    output_dir = (
        args.output_dir or PROJECT / "results" / f"sample-{uuid4().hex[:8]}"
    ).resolve()
    try:
        summary = run(args.input.resolve(), args.limit, output_dir)
    except (OSError, ValueError) as exc:
        print(f"AgentX analysis failed. {exc}", file=sys.stderr)
        return 1

    print(f"Processed {summary['conversations']} conversations and {summary['request_count']} requests")
    print(
        f"Cached input {summary['cached_input_rate']:.6%} "
        f"({summary['cached_input_tokens']:,} / {summary['input_tokens']:,} tokens)"
    )
    print(f"Results saved to {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
