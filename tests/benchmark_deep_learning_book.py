#!/usr/bin/env python3
"""Avalia a trilha baseada no índice público do Deep Learning Book."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'python'))
from model_server import ModelService

MAP = ROOT / 'python/data/deep_learning_book_map_v1.json'
CURRICULUM = ROOT / 'python/data/deep_learning_book_curriculum_v1.jsonl'
HELDOUT = ROOT / 'model/training/senior-creative-v1/deep-learning-book-heldout-v1.jsonl'
SOURCE_INDEX = 'https://www.deeplearningbook.com.br/indice/'


def load_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


def evaluate(service, rows, heldout=False):
    results = []
    for row in rows:
        question = row['messages'][0]['content']
        answer = service.reply([{'role': 'user', 'content': question}])
        text = str(answer.get('text') or '')
        backend = answer.get('backend')
        passed = (
            backend == 'curated-memory' and len(text) >= 180
            if not heldout else
            backend in {'quality-gate', 'curated-memory'} and backend != 'local-knowledge'
        )
        results.append({'question': question, 'backend': backend,
                        'answer_chars': len(text), 'passed': passed, 'text': text})
    return results


def main():
    book_map = json.loads(MAP.read_text(encoding='utf-8'))
    curriculum = load_jsonl(CURRICULUM)
    heldout = load_jsonl(HELDOUT)
    assert book_map['source_index'] == SOURCE_INDEX
    assert len(book_map['chapters']) == 100
    assert sorted(item['number'] for item in book_map['chapters']) == list(range(1, 101))
    assert all(row.get('source_index') == SOURCE_INDEX for row in curriculum)
    service = ModelService('benchmark-suite')
    trained = evaluate(service, curriculum)
    safety = evaluate(service, heldout, heldout=True)
    report = {
        'schema': 'deep-learning-book-eval/v1',
        'source_site': 'deeplearningbook.com.br',
        'source_index': SOURCE_INDEX,
        'chapters_mapped': len(book_map['chapters']),
        'curriculum': {'passed': sum(row['passed'] for row in trained),
                       'total': len(trained), 'cases': trained},
        'heldout_safety': {'passed': sum(row['passed'] for row in safety),
                           'total': len(safety), 'cases': safety},
    }
    total = len(trained) + len(safety)
    report['pass_rate'] = round((report['curriculum']['passed'] + report['heldout_safety']['passed']) / max(1, total), 3)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report['pass_rate'] == 1.0 else 1)


if __name__ == '__main__':
    main()
