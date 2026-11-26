"""Paths and output helpers shared by the synthetic examples."""
import json
from pathlib import Path
from uuid import uuid4

PROJECT = Path(__file__).resolve().parents[1]
FIXTURES = PROJECT / 'examples' / 'fixtures'


def output_path(requested, name):
    return (requested or PROJECT / 'results' / 'examples' / f'{name}-{uuid4().hex[:8]}').resolve()


def show(summary, output):
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f'Output: {output}')
