"""Dataset paths and output helpers; synthetic data requires explicit --demo."""
import json
from pathlib import Path
from uuid import uuid4

PROJECT = Path(__file__).resolve().parents[1]
FIXTURES = PROJECT / 'examples' / 'fixtures'
DATA = PROJECT / 'data'
SAGE_DAYS = ['2026-11-16', '2026-11-17', '2026-11-18']
REFERENCE_DAY = '2026-11-18'


def output_path(requested, name):
    return (requested or PROJECT / 'results' / 'examples' / f'{name}-{uuid4().hex[:8]}').resolve()


def show(summary, output):
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f'Output: {output}')


def save_mode(summary, output, demo):
    summary['example_data_mode'] = 'synthetic-demo' if demo else 'dataset'
    (output / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n')


def missing_data(parser, path):
    parser.error(f'未找到真实数据：{path}。请按 README 准备 data/ 或显式指定数据路径；仅演示代码请加 --demo。')
