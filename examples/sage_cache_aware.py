"""Direct schedule replay: shared caches versus cache-aware replica routing."""
import argparse
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from experiments.cache_aware import run_day
from examples._support import FIXTURES, output_path, show


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--day-dir', type=Path,
                        default=FIXTURES / 'sage' / '2026-11-18' / 'Sage-S2.1-flash')
    parser.add_argument('--capacity-blocks', type=int, default=4)
    parser.add_argument('--replicas', type=int, default=2)
    parser.add_argument('--probe-blocks', type=int, default=256)
    parser.add_argument('--output-dir', type=Path)
    args = parser.parse_args()
    if min(args.capacity_blocks, args.replicas, args.probe_blocks) < 1:
        parser.error('capacity, replicas and probe size must be positive')
    output = output_path(args.output_dir, 'sage-cache-aware')
    summary = run_day(args.day_dir, output, capacity_blocks=args.capacity_blocks,
                      replicas=args.replicas, probe_blocks=args.probe_blocks)
    show({'history_scope': summary['cache_namespace'], 'results': summary['results']}, output)
