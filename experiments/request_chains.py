"""Preliminary request associations, never ground-truth task labels.

Uses the exact accepted trace/schedule intersection of the previous experiment.
Current/earlier full input blocks must form an exact prefix relationship. Earlier
calls must have completed recently. The most specific prefix wins; tool_use is
preferred at equal prefix length. Conflicting prior group memberships abstain.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import heapq
import importlib.util
import json
import random
import sys
import time
from array import array
from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

sys.dont_write_bytecode = True
BLOCK = 64
MASK = (1 << 64) - 1
MULTIPLIER = 1099511628211
PROJECT = Path(__file__).resolve().parents[1]
DEFAULT_COMMON = PROJECT / "results" / "common"
DEFAULT_REFERENCE = PROJECT / "analyze_agentx.py"


@dataclass(slots=True)
class Request:
    index: int
    day: str
    request_id: str
    uid: str
    model: str
    start_ms: int
    duration_ms: int
    input_tokens: int
    output_tokens: int
    trace_group: str
    trace_index: int
    hashes: tuple = ()
    stop: str = 'unknown'
    prefix_hashes: array = field(default_factory=lambda: array('Q'))

    @property
    def scope(self):
        return self.uid, self.model

    @property
    def full_blocks(self):
        return self.input_tokens // BLOCK


@dataclass(frozen=True, slots=True)
class Config:
    name: str
    min_prefix_tokens: int = 1024
    window_seconds: float = 120
    require_tool_use: bool = False
    min_growth_tokens: int = 64


def prepare_prefix(r):
    assert len(r.hashes) == (r.input_tokens + BLOCK - 1) // BLOCK
    h = 14695981039346656037
    result = array('Q')
    for b in r.hashes[:r.full_blocks]:
        h = ((h ^ b) * MULTIPLIER) & MASK
        result.append(h)
    r.prefix_hashes = result


def load_reference(path):
    spec = importlib.util.spec_from_file_location('agentx_chain_reference', path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_requests(common):
    summary = json.loads((common / 'summary.json').read_text())
    source_sha = {x['path']: x['sha256'] for d in summary['daily_results']
                  for x in d['join_audit']['sources']}
    requests = []
    schedule_lookup = defaultdict(dict)
    trace_lookup = defaultdict(dict)
    with (common / 'matched_requests.csv').open() as source:
        for row in csv.DictReader(source):
            absolute = date.fromisoformat(row['date']).toordinal() * 86_400_000 + int(row['timestamp'])
            r = Request(len(requests), row['date'], row['request_id'], row['uid'], row['model'],
                        absolute, round(float(row['api_time']) * 1000),
                        int(row['input_tokens']), int(row['output_tokens']), row['trace_group'],
                        int(row['trace_request_index']) if row['trace_request_index'] else 0)
            assert r.duration_ms >= 0
            requests.append(r)
            assert int(row['schedule_line']) not in schedule_lookup[row['schedule_file']]
            schedule_lookup[row['schedule_file']][int(row['schedule_line'])] = r
            if r.trace_group:
                p = Path(row['schedule_file']).parent.parent / 'trace' / f'{r.trace_group}.json'
                assert r.trace_index not in trace_lookup[str(p)]
                trace_lookup[str(p)][r.trace_index] = r
    assert all(a.start_ms <= b.start_ms for a, b in zip(requests, requests[1:]))
    for filename, wanted in schedule_lookup.items():
        h = hashlib.sha256()
        with Path(filename).open('rb') as source:
            for line_number, raw in enumerate(source, 1):
                h.update(raw)
                if line_number in wanted:
                    r = wanted[line_number]
                    row = json.loads(raw)
                    assert row['request_id'] == r.request_id
                    assert row['input_length'] == r.input_tokens and row['output_length'] == r.output_tokens
                    assert row['model'] == r.model and round(row['api_time'] * 1000) == r.duration_ms
                    r.hashes = tuple(row['hash_ids'])
        assert h.hexdigest() == source_sha[filename], f'Source changed: {filename}'
    for filename, wanted in trace_lookup.items():
        raw = Path(filename).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == source_sha[filename]
        c = json.loads(raw)
        for index, r in wanted.items():
            row = c['requests'][index - 1]
            assert c['id'].rsplit('-', 1)[0] == r.uid
            assert row['model'] == r.model and tuple(row['hash_ids']) == r.hashes
            r.stop = row['stop']
    for r in requests:
        prepare_prefix(r)
        assert r.stop != 'unknown' or r.input_tokens < 512
    assert len(requests) == summary['totals']['requests']
    assert sum(r.input_tokens for r in requests) == summary['totals']['input_tokens']
    return requests, summary


def associate(requests, config, times=None):
    """One pass over actual or null-control time; exact verification after hashing."""
    if times is None:
        times = [r.start_ms for r in requests]
    order = sorted(range(len(requests)), key=lambda i: (times[i], i))
    min_blocks = config.min_prefix_tokens // BLOCK
    assert min_blocks > 0 and config.min_prefix_tokens % BLOCK == 0
    window_ms = round(config.window_seconds * 1000)
    index = defaultdict(dict)  # (uid,model) -> (length,prefix-hash) -> [exact prefix, deque]
    pending = []
    expiry = deque()
    roots = list(range(len(requests)))
    parents = [-1] * len(requests)
    evidence = [None] * len(requests)
    counters = Counter()
    for i in order:
        child = requests[i]
        now = times[i]
        # Only completed calls enter the searchable index.
        while pending and pending[0][0] <= now:
            end, p_i = heapq.heappop(pending)
            p = requests[p_i]
            if end < now - window_ms:
                continue
            key = (p.full_blocks, p.prefix_hashes[-1])
            buckets = index[p.scope]
            normalized = p.hashes[:p.full_blocks]
            if key not in buckets:
                buckets[key] = [normalized, deque()]
            else:
                # Refuse to coalesce unequal exact prefixes on a hash collision.
                if buckets[key][0] != normalized:
                    raise ValueError('Rolling-hash collision in active parent index')
            buckets[key][1].append(p_i)
            expiry.append((end, p_i, key))
        while expiry and expiry[0][0] < now - window_ms:
            _, p_i, key = expiry.popleft()
            buckets = index[requests[p_i].scope]
            bucket = buckets[key]
            assert bucket[1].popleft() == p_i
            if not bucket[1]:
                del buckets[key]

        candidates = []
        matched_blocks = 0
        max_blocks = min(child.full_blocks,
                         (child.input_tokens - config.min_growth_tokens) // BLOCK)
        buckets = index.get(child.scope, {})
        if child.full_blocks < min_blocks:
            decision = 'unknown_short_input'
        else:
            decision = 'unknown_no_eligible_predecessor'
            for n in range(max_blocks, min_blocks - 1, -1):
                bucket = buckets.get((n, child.prefix_hashes[n - 1]))
                if bucket is None:
                    continue
                if child.hashes[:n] != bucket[0]:
                    counters['rejected_hash_collision_lookups'] += 1
                    continue
                candidates = [p_i for p_i in bucket[1]
                              if child.input_tokens - requests[p_i].input_tokens >= config.min_growth_tokens]
                if candidates:
                    matched_blocks = n
                    break
            all_candidates = len(candidates)
            if candidates:
                tool_candidates = [p_i for p_i in candidates if requests[p_i].stop == 'tool_use']
                if tool_candidates:
                    candidates = tool_candidates
                distinct_roots = {roots[p_i] for p_i in candidates}
                if len(distinct_roots) == 1:
                    p_i = max(candidates, key=lambda k: (times[k] + requests[k].duration_ms, times[k], k))
                    p = requests[p_i]
                    assert times[p_i] + p.duration_ms <= now
                    assert times[p_i] <= now
                    assert now - (times[p_i] + p.duration_ms) <= window_ms
                    roots[i] = roots[p_i]
                    parents[i] = p_i
                    growth = child.input_tokens - p.input_tokens
                    decision = 'linked_tool_use' if p.stop == 'tool_use' else 'linked_prefix_time'
                    evidence[i] = {
                        'prefix_blocks': matched_blocks,
                        'prefix_tokens': matched_blocks * BLOCK,
                        'parent_input_coverage': matched_blocks * BLOCK / p.input_tokens,
                        'finish_to_start_ms': now - times[p_i] - p.duration_ms,
                        'input_growth_tokens': growth,
                        'parent_output_tokens': p.output_tokens,
                        'growth_compatible_with_parent_output': growth + BLOCK - 1 >= p.output_tokens,
                        'parent_stop': p.stop,
                        'max_prefix_candidates_before_tool_preference': all_candidates,
                        'candidates_in_selected_tier': len(candidates),
                        'selected_tier_distinct_groups': len(distinct_roots),
                        'parent_identity_unique': len(candidates) == 1,
                    }
                else:
                    decision = 'unknown_multiple_prior_groups'
                    evidence[i] = {
                        'prefix_blocks': matched_blocks,
                        'max_prefix_candidates_before_tool_preference': all_candidates,
                        'candidates_in_selected_tier': len(candidates),
                        'selected_tier_distinct_groups': len(distinct_roots),
                    }
        counters[decision] += 1
        if evidence[i] is None:
            evidence[i] = {}
        evidence[i]['decision'] = decision
        # Every request can seed later associations, subject to the same rules.
        if child.full_blocks >= min_blocks and (not config.require_tool_use or child.stop == 'tool_use'):
            heapq.heappush(pending, (now + child.duration_ms, i))
    return roots, parents, evidence, counters


def groups_of(roots):
    groups = defaultdict(list)
    for i, root in enumerate(roots):
        groups[root].append(i)
    return groups


def summarize_graph(requests, roots, parents, evidence, counters):
    groups = groups_of(roots)
    linked_groups = {k: v for k, v in groups.items() if len(v) >= 2}
    covered = [i for members in linked_groups.values() for i in members]
    total_tokens = sum(r.input_tokens for r in requests)
    children = Counter(p for p in parents if p >= 0)
    linked = [i for i, p in enumerate(parents) if p >= 0]
    return {
        'requests': len(requests), 'input_tokens': total_tokens,
        'linked_edges': len(linked), 'candidate_groups_with_at_least_2_requests': len(linked_groups),
        'requests_in_candidate_groups': len(covered),
        'request_coverage_percent': 100 * len(covered) / len(requests),
        'input_tokens_in_candidate_groups': sum(requests[i].input_tokens for i in covered),
        'input_token_coverage_percent': 100 * sum(requests[i].input_tokens for i in covered) / total_tokens,
        'unassigned_singleton_requests': len(requests) - len(covered),
        'linked_tool_use_edges': sum(requests[parents[i]].stop == 'tool_use' for i in linked),
        'linked_edges_growth_compatible_with_output': sum(evidence[i]['growth_compatible_with_parent_output'] for i in linked),
        'linked_edges_with_unique_parent_identity': sum(evidence[i]['parent_identity_unique'] for i in linked),
        'cross_date_edges': sum(requests[i].day != requests[parents[i]].day for i in linked),
        'branch_points': sum(n > 1 for n in children.values()),
        'maximum_direct_children': max(children.values(), default=0),
        'largest_candidate_group_requests': max(map(len, linked_groups.values()), default=0),
        'decisions': dict(counters),
    }


def score_groups(requests, roots, ref):
    """Same AgentX formula, with unknown singletons explicitly scored cold."""
    daily_cached = [0] * len(requests)
    continuous_cached = [0] * len(requests)
    for members in groups_of(roots).values():
        if len(members) < 2:
            continue  # One request with empty history has exactly zero hits.
        history = set()
        daily_history = set()
        previous_day = None
        for i in members:
            r = requests[i]
            if r.day != previous_day:
                daily_history.clear()
                previous_day = r.day
            daily_cached[i] = min(r.input_tokens, BLOCK * ref.count_prefix_hits(r.hashes, daily_history))
            continuous_cached[i] = min(r.input_tokens, BLOCK * ref.count_prefix_hits(r.hashes, history))
            daily_history.update(r.hashes)
            history.update(r.hashes)
    total_tokens = sum(r.input_tokens for r in requests)
    result = {
        'requests': len(requests), 'input_tokens': total_tokens,
        'daily_cached_tokens': sum(daily_cached), 'continuous_cached_tokens': sum(continuous_cached),
        'daily_rate': sum(daily_cached) / total_tokens,
        'continuous_rate': sum(continuous_cached) / total_tokens,
        'unknown_policy': 'Each unassigned singleton is scored with its own empty history; it is not declared a real one-request task.',
    }
    return result, daily_cached, continuous_cached


def null_times(requests, seed):
    rng = random.Random(seed)
    result = [r.start_ms for r in requests]
    by_scope_day = defaultdict(list)
    for r in requests:
        by_scope_day[r.day, r.uid, r.model].append(r.index)
    for indices in by_scope_day.values():
        values = [result[i] for i in indices]
        rng.shuffle(values)
        for i, value in zip(indices, values):
            result[i] = value
    return result


def profile_dict(config):
    return {k: getattr(config, k) for k in config.__dataclass_fields__}


def write_primary(out, requests, roots, parents, evidence, daily_hits, warm_hits):
    groups = groups_of(roots)
    group_names = {root: f'candidate_{n:06d}' for n, (root, members) in
                   enumerate(sorted(groups.items()), 1) if len(members) >= 2}
    fields = ['date', 'request_id', 'uid', 'model', 'timestamp_ms', 'input_tokens', 'output_tokens',
              'stop', 'candidate_group', 'candidate_group_size', 'unknown_scored_cold',
              'decision', 'parent_request_id', 'parent_date', 'prefix_tokens',
              'parent_input_coverage', 'finish_to_start_ms', 'parent_stop',
              'input_growth_tokens', 'parent_output_tokens', 'growth_compatible_with_parent_output',
              'max_prefix_candidates_before_tool_preference', 'candidates_in_selected_tier',
              'parent_identity_unique', 'daily_cached_tokens', 'continuous_cached_tokens']
    with (out / 'primary_requests.csv').open('w', encoding='utf-8', newline='') as source:
        writer = csv.DictWriter(source, fieldnames=fields)
        writer.writeheader()
        for i, r in enumerate(requests):
            p = requests[parents[i]] if parents[i] >= 0 else None
            ev = evidence[i]
            row = {k: ev.get(k, '') for k in fields}
            row.update(date=r.day, request_id=r.request_id, uid=r.uid, model=r.model,
                       timestamp_ms=r.start_ms - date.fromisoformat(r.day).toordinal() * 86_400_000,
                       input_tokens=r.input_tokens, output_tokens=r.output_tokens, stop=r.stop,
                       candidate_group=group_names.get(roots[i], ''),
                       candidate_group_size=len(groups[roots[i]]),
                       unknown_scored_cold=len(groups[roots[i]]) == 1,
                       decision=ev['decision'], parent_request_id=p.request_id if p else '',
                       parent_date=p.day if p else '', daily_cached_tokens=daily_hits[i],
                       continuous_cached_tokens=warm_hits[i])
            writer.writerow(row)
    with (out / 'candidate_groups.csv').open('w', encoding='utf-8', newline='') as source:
        fields = ['candidate_group', 'uid', 'model', 'requests', 'input_tokens',
                  'first_date', 'last_date', 'span_seconds', 'tool_use_parent_edges']
        writer = csv.DictWriter(source, fieldnames=fields)
        writer.writeheader()
        for root, name in sorted(group_names.items()):
            members = groups[root]
            first, last = requests[members[0]], requests[members[-1]]
            writer.writerow(dict(candidate_group=name, uid=first.uid, model=first.model,
                                 requests=len(members), input_tokens=sum(requests[i].input_tokens for i in members),
                                 first_date=first.day, last_date=last.day,
                                 span_seconds=(last.start_ms - first.start_ms) / 1000,
                                 tool_use_parent_edges=sum(parents[i] >= 0 and requests[parents[i]].stop == 'tool_use' for i in members)))


def run(args):
    if args.output_dir.exists():
        raise FileExistsError(args.output_dir)
    args.output_dir.mkdir(parents=True)
    begin = time.perf_counter()
    requests, prior = load_requests(args.common_dir)
    ref = load_reference(args.reference_script)
    print('LOADED', len(requests), sum(r.input_tokens for r in requests), flush=True)
    # Recompute the user baseline on exactly these same prepared records.
    user_roots = []
    scope_root = {}
    for r in requests:
        scope_root.setdefault(r.scope, r.index)
        user_roots.append(scope_root[r.scope])
    baseline, baseline_daily, baseline_warm = score_groups(requests, user_roots, ref)
    assert baseline['daily_cached_tokens'] == prior['totals']['user_daily_cached_tokens']
    assert baseline['continuous_cached_tokens'] == prior['totals']['user_continuous_cached_tokens']
    profiles = [Config('primary_1024tokens_120s'),
                Config('tool_use_required_1024tokens_120s', require_tool_use=True),
                Config('prefix_512tokens_120s', min_prefix_tokens=512),
                Config('prefix_2048tokens_120s', min_prefix_tokens=2048),
                Config('window_30s_1024tokens', window_seconds=30),
                Config('window_600s_1024tokens', window_seconds=600)]
    results = []
    primary_saved = None
    for config in profiles:
        start = time.perf_counter()
        roots, parents, ev, counters = associate(requests, config)
        graph = summarize_graph(requests, roots, parents, ev, counters)
        score, hits, warm = score_groups(requests, roots, ref)
        assert all(a <= b for a, b in zip(hits, baseline_daily))
        assert all(a <= b for a, b in zip(warm, baseline_warm))
        result = {'config': profile_dict(config), 'graph': graph, 'score': score,
                  'wall_seconds': time.perf_counter() - start}
        results.append(result)
        if config.name.startswith('primary_'):
            write_primary(args.output_dir, requests, roots, parents, ev, hits, warm)
            primary_saved = result
            # Evidence examples with the strongest tool-use signal and output growth.
            examples = []
            for i, p in enumerate(parents):
                if p >= 0 and requests[p].stop == 'tool_use' and ev[i]['growth_compatible_with_parent_output']:
                    parent, child = requests[p], requests[i]
                    examples.append({'parent': {'date':parent.day,'request_id':parent.request_id,'uid':parent.uid,'model':parent.model,'input_tokens':parent.input_tokens,'output_tokens':parent.output_tokens,'stop':parent.stop},
                                     'child': {'date':child.day,'request_id':child.request_id,'input_tokens':child.input_tokens},
                                     'evidence':ev[i]})
                    if len(examples) == 12:
                        break
            (args.output_dir / 'examples.json').write_text(json.dumps(examples, ensure_ascii=False, indent=2) + '\n')
        print('PROFILE', json.dumps(result, ensure_ascii=False), flush=True)
    controls = []
    for seed in [7, 19, 42]:
        start = time.perf_counter()
        times = null_times(requests, seed)
        roots, parents, ev, counters = associate(requests, profiles[0], times=times)
        graph = summarize_graph(requests, roots, parents, ev, counters)
        result = {'seed':seed, 'graph':graph, 'wall_seconds':time.perf_counter()-start}
        controls.append(result)
        print('NULL', json.dumps(result, ensure_ascii=False), flush=True)
    output = {
        'method':'Preliminary exact-full-prefix + completed-time + tool_use-preference associations',
        'not_ground_truth_tasks':True,
        'input_common_directory':str(args.common_dir),
        'matched_csv_sha256':hashlib.sha256((args.common_dir/'matched_requests.csv').read_bytes()).hexdigest(),
        'input_source_hashes_validated':True,
        'reference_script':str(args.reference_script),
        'reference_script_sha256':hashlib.sha256(args.reference_script.read_bytes()).hexdigest(),
        'baseline_user_model':baseline,
        'selection_rules':['Same uid and model.','Only previously completed requests whose completion is within the selected window.',
                           'The entire earlier input full-block prefix matches exactly; only the actual last partial block is ignored.',
                           'At least selected minimum prefix tokens; child input grows by at least 64 tokens.',
                           'Use longest eligible full prefix; at equal length prefer tool_use.',
                           'Accept only a unique existing candidate-group identity; choose latest completed representative within that same group.',
                           'Output-length compatibility is reported as supporting evidence, not a mandatory gate.',
                           'Exact repeated or nearly identical input is not itself considered task continuation.'],
        'rate_comparison_policy':'Same selected requests and input-token denominator as the user/model baseline. Inferred groups share histories; unassigned singleton requests are counted with empty histories. This is a grouping scenario, not a measured true within-task rate or a guaranteed lower bound on it.',
        'cache_assumptions':prior['cache_assumptions'],
        'profiles':results,
        'time_shuffle_controls':controls,
        'null_method':'Shuffle arrival timestamps within each day, uid and model; payload, duration, stop and marginal arrival times remain unchanged. Exploratory falsification check, not a calibrated accuracy test.',
        'wall_seconds':time.perf_counter()-begin,
    }
    (args.output_dir/'summary.json').write_text(json.dumps(output, ensure_ascii=False, indent=2)+'\n')
    print('DONE', json.dumps({'wall_seconds':output['wall_seconds'],'primary':primary_saved,'baseline':baseline},ensure_ascii=False),flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--common-dir',type=Path,default=DEFAULT_COMMON)
    parser.add_argument('--reference-script',type=Path,default=DEFAULT_REFERENCE)
    parser.add_argument('--output-dir',type=Path,required=True)
    run(parser.parse_args())
