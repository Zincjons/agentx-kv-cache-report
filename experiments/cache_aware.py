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
from itertools import chain
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


def run_days(data_root, days, output_dir, *, capacity_blocks=1_000_000, replicas=2, probe_blocks=256):
    data_root = Path(data_root).resolve()
    if not days or len(set(days)) != len(days):
        raise ValueError('Select at least one date, without repetitions')
    days = sorted(days)
    output_dir = Path(output_dir).resolve()
    if output_dir.exists():
        raise FileExistsError(output_dir)
    reference = load_reference(PROJECT / 'analyze_agentx.py')
    streams, sources = [], []
    for day in days:
        stream, metadata = load_day(data_root, day, reference)
        streams.append(stream)
        sources.append({'day': day, **metadata})
    results = []
    daily = [{'day': day, 'results': []} for day in days]
    weighted = []
    for label, kw in [
        ('shared-unlimited', {}),
        ('shared-lru', {'capacity_blocks': capacity_blocks}),
        ('replicas-round-robin', {'capacity_blocks': capacity_blocks, 'replicas': replicas}),
        ('replicas-random', {'capacity_blocks': capacity_blocks, 'replicas': replicas, 'routing': 'random'}),
        ('replicas-cache-aware', {'capacity_blocks': capacity_blocks, 'replicas': replicas, 'routing': 'prefix'}),
    ]:
        # One replay retains caches, routing counts and rotation across midnight.
        result = {'label': label, **replay(chain.from_iterable(streams), probe_blocks=probe_blocks, **kw)}
        results.append(result)
        for stream, day_report in zip(streams, daily):
            cold = (dict(result) if len(days) == 1 else
                    {'label': label, **replay(stream, probe_blocks=probe_blocks, **kw)})
            day_report['results'].append(cold)
        total = {'label': label, 'capacity_blocks_per_replica': result['capacity_blocks_per_replica'],
                 'replicas': result['replicas'], 'routing': result['routing'],
                 'routing_probe_blocks': probe_blocks}
        for key in ('requests', 'input_tokens', 'cached_input_tokens', 'uncached_input_tokens'):
            total[key] = sum(d['results'][-1][key] for d in daily)
        total['cached_input_rate'] = (total['cached_input_tokens'] / total['input_tokens']
                                     if total['input_tokens'] else 0.0)
        weighted.append(total)
        print(f"{label}: continuous={result['cached_input_rate']:.6%}; daily-cold-weighted={total['cached_input_rate']:.6%}; replica_requests={result['replica_requests']}", flush=True)
    report = {
        'input': [str(data_root / day / 'Sage-S2.1-flash' / 'schedule') for day in days],
        'days': days, 'input_metadata': sources,
        'metric': 'AgentX/InferenceX Cached input formula',
        'cache_namespace': '(model, hash_id); no session or user labels loaded',
        'history_mode': 'continuous across selected dates',
        'ordering': 'date, timestamp; equal timestamps retain source filename/line order',
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
        'daily_results': daily,
        'daily_cold_starts_weighted_results': weighted,
    }
    output_dir.mkdir(parents=True)
    (output_dir / 'summary.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    return report


def run_day(day_dir, output_dir, **kwargs):
    """Compatibility entry point for an explicitly selected single date."""
    day_dir = Path(day_dir).resolve()
    return run_days(day_dir.parent.parent, [day_dir.parent.name], output_dir, **kwargs)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group()
    source.add_argument('--day-dir', type=Path, help='explicit single date instead of the full dataset')
    source.add_argument('--data-root', type=Path, default=PROJECT / 'data' / 'sage')
    parser.add_argument('--days', nargs='+', help='default: 2026-11-16 2026-11-17 2026-11-18')
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--capacity-blocks', type=int, default=1_000_000)
    parser.add_argument('--replicas', type=int, default=2)
    parser.add_argument('--probe-blocks', type=int, default=256)
    args = parser.parse_args()
    if min(args.capacity_blocks, args.replicas, args.probe_blocks) < 1:
        parser.error('capacity-blocks, replicas and probe-blocks must be positive')
    if args.day_dir and args.days:
        parser.error('--day-dir cannot be combined with --days')
    options = dict(capacity_blocks=args.capacity_blocks, replicas=args.replicas,
                   probe_blocks=args.probe_blocks)
    if args.day_dir:
        run_day(args.day_dir, args.output_dir, **options)
    else:
        run_days(args.data_root, args.days or ['2026-11-16', '2026-11-17', '2026-11-18'],
                 args.output_dir, **options)
