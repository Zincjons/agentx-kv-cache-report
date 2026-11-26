"""AgentX: true conversation isolation and child-agent history snapshots."""
import argparse
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analyze_agentx import run
from examples._support import DATA, FIXTURES, missing_data, output_path, save_mode, show


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--demo', action='store_true', help='explicit synthetic smoke test; not the 98% dataset result')
    mode.add_argument('--input', type=Path, help='real dataset JSONL; default: data/traces.jsonl')
    parser.add_argument('--limit', type=int, help='default: 393 real conversations, or 2 with --demo')
    parser.add_argument('--output-dir', type=Path)
    args = parser.parse_args()
    limit = args.limit if args.limit is not None else (2 if args.demo else 393)
    if limit < 1:
        parser.error('limit must be positive')
    input_path = (FIXTURES / 'agentx.jsonl' if args.demo else args.input or DATA / 'traces.jsonl').resolve()
    if not input_path.is_file():
        missing_data(parser, input_path)
    output = output_path(args.output_dir, 'agentx')
    summary = run(input_path, limit, output)
    save_mode(summary, output, args.demo)
    show(summary, output)
