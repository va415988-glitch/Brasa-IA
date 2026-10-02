#!/usr/bin/env python3
"""Avalia o currículo derivado do repositório huggingface/datasets."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'python'))
from model_server import ModelService

CURRICULUM = ROOT / 'python/data/hf_datasets_curriculum_v1.jsonl'
HELDOUT = ROOT / 'model/training/senior-creative-v1/hf-datasets-heldout-v1.jsonl'
REVISION = '3e2c1a6c33b883fd9710f55f7b6fa4ab64d0ee07'


def load(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


def run(service, cases, heldout=False):
    rows = []
    for case in cases:
        question = case['messages'][0]['content']
        result = service.reply([{'role': 'user', 'content': question}])
        text = str(result.get('text') or '')
        passed = (
            result.get('backend') == 'curated-memory' and len(text) >= 180
            if not heldout else
            result.get('backend') in {'quality-gate', 'curated-memory'}
            and result.get('backend') != 'local-knowledge'
        )
        rows.append({'question': question, 'backend': result.get('backend'), 'answer_chars': len(text), 'passed': passed, 'text': text})
    return rows


def main():
    cases = load(CURRICULUM)
    heldout = load(HELDOUT)
    assert all(row.get('source_revision') == REVISION for row in cases)
    service = ModelService('benchmark-suite')
    curriculum = run(service, cases)
    safety = run(service, heldout, heldout=True)
    report = {
        'schema': 'hf-datasets-curriculum-eval/v1',
        'source_repo': 'huggingface/datasets',
        'source_revision': REVISION,
        'curriculum': {'passed': sum(row['passed'] for row in curriculum), 'total': len(curriculum), 'cases': curriculum},
        'heldout_safety': {'passed': sum(row['passed'] for row in safety), 'total': len(safety), 'cases': safety},
    }
    total = len(curriculum) + len(safety)
    report['pass_rate'] = round((report['curriculum']['passed'] + report['heldout_safety']['passed']) / max(1, total), 3)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report['pass_rate'] == 1.0 else 1)


if __name__ == '__main__':
    main()
