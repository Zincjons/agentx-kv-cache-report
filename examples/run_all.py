"""Reproduce the four actual dataset results; --demo is explicitly synthetic."""
import argparse
import csv
import json
import subprocess
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from examples._support import DATA, PROJECT, SAGE_DAYS, missing_data, output_path


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--demo', action='store_true', help='synthetic smoke tests, never the production results')
    parser.add_argument('--agentx-input', type=Path, help='default: data/traces.jsonl')
    parser.add_argument('--sage-root', type=Path, help='default: data/sage')
    parser.add_argument('--output-dir', type=Path)
    parser.add_argument('--skip-reference-check', action='store_true', help='allow a different dataset; still compute actual results')
    args = parser.parse_args()
    if args.demo and (args.agentx_input or args.sage_root):
        parser.error('--demo cannot be combined with real dataset paths')
    agentx = (args.agentx_input or DATA / 'traces.jsonl').resolve()
    sage = (args.sage_root or DATA / 'sage').resolve()
    if not args.demo:
        if not agentx.is_file():
            missing_data(parser, agentx)
        for day in SAGE_DAYS:
            for kind, pattern in [('trace','*.json'),('schedule','*.jsonl')]:
                directory = sage / day / 'Sage-S2.1-flash' / kind
                if not any(directory.glob(pattern)):
                    missing_data(parser, directory)
    output = output_path(args.output_dir, 'demo-suite' if args.demo else 'real-dataset-suite')
    if output.exists():
        raise FileExistsError(output)
    output.mkdir(parents=True)
    references = json.loads((PROJECT/'reports/reference_results.json').read_text())['example_real_defaults']
    scopes = {
        'agentx': 'AgentX full: 393 conversations' if not args.demo else 'synthetic AgentX',
        'sage_session_id': 'Sage full trace: 2026-11-16..18, isolated rule sessions' if not args.demo else 'synthetic rule sessions',
        'sage_user_id': 'Sage reliable common: 2026-11-16..18, continuous uid/model history' if not args.demo else 'synthetic uid/model',
        'sage_cache_aware': 'Sage full schedule: 2026-11-16..18, continuous, 4 replicas x 500000 blocks' if not args.demo else 'synthetic cache-aware',
    }
    commands = {
        'agentx': ['--input',str(agentx)],
        'sage_session_id': ['--data-root',str(sage),'--days',*SAGE_DAYS],
        'sage_user_id': ['--data-root',str(sage),'--days',*SAGE_DAYS],
        'sage_cache_aware': ['--data-root',str(sage),'--days',*SAGE_DAYS],
    }
    demo_expected = {'agentx':(6,960,384),'sage_session_id':(5,896,256),
                     'sage_user_id':(5,896,448),'sage_cache_aware':(5,896,576)}
    results = []
    for name, inputs in commands.items():
        target = output/name
        command = [sys.executable,'-B',str(PROJECT/'examples'/f'{name}.py'),
                   *(['--demo'] if args.demo else inputs),'--output-dir',str(target)]
        print(f'Running {name}: {scopes[name]}',flush=True)
        completed = subprocess.run(command,cwd=PROJECT,capture_output=True,text=True)
        if completed.returncode:
            raise RuntimeError(completed.stdout+'\n'+completed.stderr)
        (target/'run.log').write_text(completed.stdout+completed.stderr)
        report = json.loads((target/'summary.json').read_text())
        assert report['example_data_mode'] == ('synthetic-demo' if args.demo else 'dataset')
        cold_tokens = None
        if name == 'sage_user_id':
            t=report['totals']
            measured=(t['requests'],t['input_tokens'],t['user_continuous_cached_tokens'])
            cold_tokens=t['user_daily_cached_tokens']
        elif name == 'sage_cache_aware':
            t=next(r for r in report['results'] if r['label']=='replicas-cache-aware')
            measured=(t['requests'],t['input_tokens'],t['cached_input_tokens'])
            cold_tokens=next(r['cached_input_tokens'] for r in report['daily_cold_starts_weighted_results']
                             if r['label']=='replicas-cache-aware')
        else:
            measured=(report['request_count'],report['input_tokens'],report['cached_input_tokens'])
        expected = demo_expected[name] if args.demo else tuple(references[name][k] for k in ['requests','input_tokens','cached_input_tokens'])
        matches=measured==expected
        if cold_tokens is not None:
            expected_cold = demo_expected[name][2] if args.demo else references[name]['daily_cold_start_cached_input_tokens']
            matches = matches and cold_tokens == expected_cold
        if not args.skip_reference_check and not matches:
            raise ValueError(f'{name}: measured {measured}, expected {expected}; check dataset/scope, or use --skip-reference-check for a different dataset')
        result=dict(example=name,dataset_scope=scopes[name],requests=measured[0],
                    input_tokens=measured[1],cached_input_tokens=measured[2],
                    cached_input_rate=measured[2]/measured[1] if measured[1] else 0.0,
                    daily_cold_start_cached_input_tokens=cold_tokens,
                    daily_cold_start_cached_input_rate=(cold_tokens/measured[1] if cold_tokens is not None and measured[1] else None),
                    reference_matches=matches)
        results.append(result)
        print(f"{name}: {100*result['cached_input_rate']:.6f}% ({measured[2]:,}/{measured[1]:,})",flush=True)
        if cold_tokens is not None:
            print(f"  Daily cold starts, token weighted: {result['daily_cold_start_cached_input_rate']:.6%}",flush=True)
    summary={'data_mode':'synthetic-demo' if args.demo else 'dataset','reference_check_enabled':not args.skip_reference_check,'results':results}
    (output/'comparison.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
    with (output/'comparison.csv').open('w',encoding='utf-8',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(results[0]));writer.writeheader();writer.writerows(results)
    print(f'Comparison: {output / "comparison.json"}')
