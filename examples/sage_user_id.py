"""Sage: join schedule time with trace uid, then retain history by (uid,model)."""
import argparse
import json
import sys
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from experiments.common import main
from examples._support import DATA, FIXTURES, PROJECT, REFERENCE_DAY, SAGE_DAYS, missing_data, output_path, save_mode, show


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--demo', action='store_true', help='explicit synthetic smoke test')
    mode.add_argument('--data-root', type=Path, help='real data root; default: data/sage')
    parser.add_argument('--days', nargs='+', help='default: all three real dates, or 2026-11-18 with --demo')
    parser.add_argument('--output-dir', type=Path)
    args = parser.parse_args()
    data_root = (args.data_root or (FIXTURES if args.demo else DATA) / 'sage').resolve()
    days = args.days or ([REFERENCE_DAY] if args.demo else SAGE_DAYS)
    if len(set(days)) != len(days):
        parser.error('days must not repeat')
    for day in days:
        for kind, pattern in [('trace', '*.json'), ('schedule', '*.jsonl')]:
            directory = data_root / day / 'Sage-S2.1-flash' / kind
            if not any(directory.glob(pattern)):
                missing_data(parser, directory)
    output = output_path(args.output_dir, 'sage-user-id')
    main(SimpleNamespace(data_root=data_root, days=days,
                         reference_script=PROJECT / 'analyze_agentx.py', output_dir=output))
    summary = json.loads((output / 'summary.json').read_text())
    summary['days'] = sorted(days)
    summary['primary_result_label'] = 'user_continuous'
    summary['primary_cached_input_tokens'] = summary['totals']['user_continuous_cached_tokens']
    summary['primary_cached_input_rate'] = summary['totals']['user_continuous_rate']
    summary['history_mode'] = 'continuous across selected dates'
    save_mode(summary, output, args.demo)
    show({'history_scope': '(uid,model); rule-session and date changes do not reset primary history',
          'primary_cached_input_rate': summary['primary_cached_input_rate'],
          'totals': {k:v for k,v in summary['totals'].items() if not k.startswith('global_')},
          'users': summary['users']}, output)
