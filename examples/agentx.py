"""AgentX: true conversation isolation and child-agent history snapshots."""
import argparse
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analyze_agentx import run
from examples._support import FIXTURES, output_path, show


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, default=FIXTURES / 'agentx.jsonl')
    parser.add_argument('--limit', type=int, default=2)
    parser.add_argument('--output-dir', type=Path)
    args = parser.parse_args()
    if args.limit < 1:
        parser.error('limit must be positive')
    output = output_path(args.output_dir, 'agentx')
    show(run(args.input.resolve(), args.limit, output), output)
