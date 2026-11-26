"""Direct schedule replay: shared caches versus cache-aware replica routing."""
import argparse
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from experiments.cache_aware import run_days
from examples._support import DATA, FIXTURES, REFERENCE_DAY, SAGE_DAYS, missing_data, output_path, save_mode, show


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--demo', action='store_true', help='explicit synthetic smoke test')
    mode.add_argument('--data-root', type=Path, help='real data root; default: data/sage, all three dates')
    mode.add_argument('--day-dir', type=Path, help='explicit single date instead of the full dataset')
    parser.add_argument('--days', nargs='+', help='default: all three real dates, or 2026-11-18 with --demo')
    parser.add_argument('--capacity-blocks', type=int, help='per replica; default: 500000 real, 4 demo')
    parser.add_argument('--replicas', type=int, help='default: 4 real, 2 demo')
    parser.add_argument('--probe-blocks', type=int, default=256)
    parser.add_argument('--output-dir', type=Path)
    args = parser.parse_args()
    capacity = args.capacity_blocks if args.capacity_blocks is not None else (4 if args.demo else 500000)
    replicas = args.replicas if args.replicas is not None else (2 if args.demo else 4)
    if min(capacity, replicas, args.probe_blocks) < 1:
        parser.error('capacity, replicas and probe size must be positive')
    if args.day_dir and args.days:
        parser.error('--day-dir cannot be combined with --days')
    data_root = (args.data_root or (FIXTURES if args.demo else DATA) / 'sage').resolve()
    days = args.days or ([REFERENCE_DAY] if args.demo else SAGE_DAYS)
    if args.day_dir:
        day_dir = args.day_dir.resolve()
        data_root, days = day_dir.parent.parent, [day_dir.parent.name]
    if len(set(days)) != len(days):
        parser.error('days must not repeat')
    for day in days:
        directory = data_root / day / 'Sage-S2.1-flash' / 'schedule'
        if not any(directory.glob('*.jsonl')):
            missing_data(parser, directory)
    output = output_path(args.output_dir, 'sage-cache-aware')
    summary = run_days(data_root, days, output, capacity_blocks=capacity,
                       replicas=replicas, probe_blocks=args.probe_blocks)
    primary = next(r for r in summary['results'] if r['label'] == 'replicas-cache-aware')
    summary['primary_result_label'] = primary['label']
    summary['primary_cached_input_rate'] = primary['cached_input_rate']
    summary['primary_cached_input_tokens'] = primary['cached_input_tokens']
    save_mode(summary, output, args.demo)
    show({'history_scope': summary['cache_namespace'],
          'primary_result_label': primary['label'],
          'primary_cached_input_rate': primary['cached_input_rate'],
          'days': summary['days'], 'history_mode': summary['history_mode'],
          'results': summary['results'],
          'daily_cold_starts_weighted_results': summary['daily_cold_starts_weighted_results']}, output)
