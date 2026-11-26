"""Replay schedule directly with shared/LRU caches and cache-aware routing.

The router chooses the replica with the longest cached prefix, probing at most
probe_blocks. Equal best scores use the least-routed replica, then rotation.
This maximizes a cache-locality signal, not service throughput or load balance.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import time
from collections import OrderedDict
from pathlib import Path

from analyze_agentx import count_prefix_hits
from experiments.schedule import load_day, load_reference

PROJECT = Path(__file__).resolve().parents[1]


def replay(stream, *, capacity_blocks=None, replicas=1, routing='rr', probe_blocks=256, seed=7):
    if replicas < 1 or probe_blocks < 1:
        raise ValueError('replicas and probe_blocks must be positive')
    if capacity_blocks is not None and capacity_blocks < 1:
        raise ValueError('capacity_blocks must be positive')
    if routing not in ('rr', 'random', 'prefix'):
        raise ValueError(f'Unknown routing policy: {routing}')
    started = time.perf_counter()
    caches = [set() if capacity_blocks is None else OrderedDict() for _ in range(replicas)]
    counts, inputs = [0] * replicas, [0] * replicas
    total_in = total_cached = 0
    rng = random.Random(seed)
    cursor = 0
    requests = 0
    for ordinal, r in enumerate(stream):
        keys = tuple((r.model, b) for b in r.hashes)
        if routing == 'rr':
            selected = ordinal % replicas
        elif routing == 'random':
            selected = rng.randrange(replicas)
        else:
            scores = [count_prefix_hits(keys[:probe_blocks], cache) for cache in caches]
            best = max(scores)
            tied = [i for i, score in enumerate(scores) if score == best]
            least = min(counts[i] for i in tied)
            tied = [i for i in tied if counts[i] == least]
            selected = min(tied, key=lambda i: (i - cursor) % replicas)
            cursor = (selected + 1) % replicas
        cache = caches[selected]
        hits = count_prefix_hits(keys, cache)
        cached = min(r.input_tokens, hits * 64)
        if capacity_blocks is None:
            cache.update(keys)
        else:
            for key in keys:
                if key in cache:
                    cache.move_to_end(key)
                else:
                    cache[key] = None
                    if len(cache) > capacity_blocks:
                        cache.popitem(last=False)
        counts[selected] += 1
        inputs[selected] += r.input_tokens
        requests += 1
        total_in += r.input_tokens
        total_cached += cached
    return {
        'requests': requests, 'input_tokens': total_in,
        'cached_input_tokens': total_cached,
        'uncached_input_tokens': total_in - total_cached,
        'cached_input_rate': total_cached / total_in if total_in else 0.0,
        'capacity_blocks_per_replica': capacity_blocks, 'replicas': replicas,
        'routing': routing, 'routing_probe_blocks': probe_blocks,
        'replica_requests': counts, 'replica_input_tokens': inputs,
        'resident_blocks_per_replica': [len(c) for c in caches],
        'wall_seconds': time.perf_counter() - started,
    }


def run_day(day_dir, output_dir, *, capacity_blocks=1_000_000, replicas=2, probe_blocks=256):
    day_dir = Path(day_dir).resolve()
    output_dir = Path(output_dir).resolve()
    if output_dir.exists():
        raise FileExistsError(output_dir)
    reference = load_reference(PROJECT / 'analyze_agentx.py')
    stream, sources = load_day(day_dir.parent.parent, day_dir.parent.name, reference)
    results = []
    for label, kw in [
        ('shared-unlimited', {}),
        ('shared-lru', {'capacity_blocks': capacity_blocks}),
        ('replicas-round-robin', {'capacity_blocks': capacity_blocks, 'replicas': replicas}),
        ('replicas-random', {'capacity_blocks': capacity_blocks, 'replicas': replicas, 'routing': 'random'}),
        ('replicas-cache-aware', {'capacity_blocks': capacity_blocks, 'replicas': replicas, 'routing': 'prefix'}),
    ]:
        result = {'label': label, **replay(stream, probe_blocks=probe_blocks, **kw)}
        results.append(result)
        print(f"{label}: {result['cached_input_rate']:.6%}; replica_requests={result['replica_requests']}", flush=True)
    report = {
        'input': str(day_dir / 'schedule'), 'input_metadata': sources,
        'metric': 'AgentX/InferenceX Cached input formula',
        'cache_namespace': '(model, hash_id); no session or user labels loaded',
        'block_size': 64,
        'assumptions': [
            'Rows replayed in timestamp order; each request immediately admits all input blocks.',
            'Finite capacities use block LRU; routing changes cache locality only.',
            'No output admission, expiry, concurrent completion model, or throughput simulation.',
            'Pure prefix-aware routing can concentrate traffic; replica counts must be inspected.',
            'Different models never share KV, even if their integer hash IDs match.',
        ],
        'reference_script_sha256': hashlib.sha256((PROJECT / 'analyze_agentx.py').read_bytes()).hexdigest(),
        'results': results,
    }
    output_dir.mkdir(parents=True)
    (output_dir / 'summary.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--day-dir', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--capacity-blocks', type=int, default=1_000_000)
    parser.add_argument('--replicas', type=int, default=2)
    parser.add_argument('--probe-blocks', type=int, default=256)
    args = parser.parse_args()
    if min(args.capacity_blocks, args.replicas, args.probe_blocks) < 1:
        parser.error('capacity-blocks, replicas and probe-blocks must be positive')
    run_day(args.day_dir, args.output_dir, capacity_blocks=args.capacity_blocks,
            replicas=args.replicas, probe_blocks=args.probe_blocks)
