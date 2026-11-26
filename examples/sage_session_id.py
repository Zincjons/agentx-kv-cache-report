"""Sage rule-session isolation: a reference grouping, not true task identity."""
import argparse
import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analyze_agentx import run
from examples._support import DATA, FIXTURES, REFERENCE_DAY, SAGE_DAYS, missing_data, output_path, save_mode, show


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--demo', action='store_true', help='explicit synthetic smoke test')
    mode.add_argument('--data-root', type=Path, help='real data root; default: data/sage, all three dates')
    mode.add_argument('--trace-dir', type=Path, help='explicit single trace directory instead of the full dataset')
    parser.add_argument('--days', nargs='+', help='default: all three real dates, or 2026-11-18 with --demo')
    parser.add_argument('--output-dir', type=Path)
    args = parser.parse_args()
    if args.trace_dir and args.days:
        parser.error('--trace-dir cannot be combined with --days')
    days = args.days or ([REFERENCE_DAY] if args.demo else SAGE_DAYS)
    if len(set(days)) != len(days):
        parser.error('days must not repeat')
    days = sorted(days)
    root = (args.data_root or (FIXTURES if args.demo else DATA) / 'sage').resolve()
    trace_dirs = ([args.trace_dir.resolve()] if args.trace_dir else
                  [root / day / 'Sage-S2.1-flash' / 'trace' for day in days])
    paths = []
    for directory in trace_dirs:
        day_paths = sorted(directory.glob('*.json'))
        if not day_paths:
            missing_data(parser, directory)
        paths.extend(day_paths)
    output = output_path(args.output_dir, 'sage-session-id')
    with TemporaryDirectory(prefix='sage-example-') as tmp:
        input_path = Path(tmp) / 'traces.jsonl'
        with input_path.open('wb') as target:
            for path in paths:
                target.write(json.dumps(json.loads(path.read_bytes()), separators=(',', ':')).encode() + b'\n')
        summary = run(input_path, len(paths), output)
    summary['source_trace_directories'] = [str(p) for p in trace_dirs]
    summary['days'] = days if not args.trace_dir else None
    summary['input_note'] = 'Temporary JSONL concatenated from source trace files; rerun this example to rebuild it.'
    summary['history_scope'] = 'Each rule-generated trace.id starts from empty history; not a true task label.'
    save_mode(summary, output, args.demo)
    show(summary, output)
