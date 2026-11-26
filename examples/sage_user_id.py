"""Sage: join schedule time with trace uid, then retain history by (uid,model)."""
import argparse
import json
import sys
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from experiments.common import main
from examples._support import FIXTURES, PROJECT, output_path, show


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-root', type=Path, default=FIXTURES / 'sage')
    parser.add_argument('--days', nargs='+', default=['2026-11-18'])
    parser.add_argument('--output-dir', type=Path)
    args = parser.parse_args()
    output = output_path(args.output_dir, 'sage-user-id')
    main(SimpleNamespace(data_root=args.data_root.resolve(), days=args.days,
                         reference_script=PROJECT / 'analyze_agentx.py', output_dir=output))
    summary = json.loads((output / 'summary.json').read_text())
    show({'history_scope': '(uid,model); rule-session changes do not reset history',
          'totals': {k:v for k,v in summary['totals'].items() if not k.startswith('global_')},
          'users': summary['users']}, output)
