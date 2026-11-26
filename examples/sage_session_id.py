"""Sage rule-session isolation: a reference grouping, not true task identity."""
import argparse
import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analyze_agentx import run
from examples._support import FIXTURES, output_path, show


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--trace-dir', type=Path,
                        default=FIXTURES / 'sage' / '2026-11-18' / 'Sage-S2.1-flash' / 'trace')
    parser.add_argument('--output-dir', type=Path)
    args = parser.parse_args()
    paths = sorted(args.trace_dir.resolve().glob('*.json'))
    if not paths:
        parser.error('trace-dir has no JSON files')
    output = output_path(args.output_dir, 'sage-session-id')
    with TemporaryDirectory(prefix='sage-example-') as tmp:
        input_path = Path(tmp) / 'traces.jsonl'
        with input_path.open('wb') as target:
            for path in paths:
                target.write(json.dumps(json.loads(path.read_bytes()), separators=(',', ':')).encode() + b'\n')
        summary = run(input_path, len(paths), output)
    summary['source_trace_directory'] = str(args.trace_dir.resolve())
    summary['input_note'] = 'Temporary JSONL concatenated from source trace files; rerun this example to rebuild it.'
    summary['history_scope'] = 'Each rule-generated trace.id starts from empty history; not a true task label.'
    (output / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
    show(summary, output)
