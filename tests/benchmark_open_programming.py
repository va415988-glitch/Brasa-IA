#!/usr/bin/env python3
"""Avalia respostas de programação curadas e variações não vistas."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'python'))
from model_server import ModelService

CURRICULUM = ROOT / 'python/data/open_programming_curriculum_v1.jsonl'
HELDOUT = ROOT / 'model/training/senior-creative-v1/open-programming-heldout-v1.jsonl'


def load(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


def evaluate(service, cases):
    rows = []
    for case in cases:
        question = case['messages'][0]['content']
        result = service.reply([{'role': 'user', 'content': question}])
        text = str(result.get('text') or '')
        rows.append({
            'question': question,
            'backend': result.get('backend'),
            'answer_chars': len(text),
            'has_actionable_answer': result.get('backend') == 'curated-memory' and len(text) >= 100,
        })
    return rows


def main():
    service = ModelService('benchmark-suite')
    curriculum = evaluate(service, load(CURRICULUM))
    heldout = evaluate(service, load(HELDOUT))
    report = {
        'schema': 'open-programming-eval/v1',
        'curriculum': {'passed': sum(row['has_actionable_answer'] for row in curriculum), 'total': len(curriculum), 'cases': curriculum},
        'heldout': {'passed': sum(row['has_actionable_answer'] for row in heldout), 'total': len(heldout), 'cases': heldout},
    }
    report['pass_rate'] = round((report['curriculum']['passed'] + report['heldout']['passed']) / max(1, len(curriculum) + len(heldout)), 3)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report['pass_rate'] == 1.0 else 1)


if __name__ == '__main__':
    main()
