#!/usr/bin/env python3
"""Avalia currículos derivados de PDFs sem ingerir texto integral."""
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'python'))
from model_server import ModelService

MANIFEST = ROOT / 'python/data/attached_books_manifest_v1.json'
CURRICULA = [
    ROOT / 'python/data/databricks_genai_curriculum_v1.jsonl',
    ROOT / 'python/data/little_book_deep_learning_curriculum_v1.jsonl',
]
HELDOUT = ROOT / 'model/training/senior-creative-v1/attached-books-heldout-v1.jsonl'


def load_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


def run(service, rows, heldout=False):
    result = []
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
        result.append({'question': question, 'backend': backend,
                       'answer_chars': len(text), 'passed': passed, 'text': text})
    return result


def main():
    manifest = json.loads(MANIFEST.read_text(encoding='utf-8'))
    for source in manifest['sources']:
        path = Path(source['file'])
        assert hashlib.sha256(path.read_bytes()).hexdigest() == source['sha256']
    curriculum = [row for path in CURRICULA for row in load_jsonl(path)]
    heldout = load_jsonl(HELDOUT)
    known_ids = {source['id'] for source in manifest['sources']}
    assert all(row['source_id'] in known_ids for row in curriculum)
    assert all(row['license_policy'].endswith('original_paraphrase_only') for row in curriculum)
    service = ModelService('benchmark-suite')
    trained = run(service, curriculum)
    safety = run(service, heldout, heldout=True)
    report = {
        'schema': 'attached-books-eval/v1',
        'sources': [{'id': source['id'], 'sha256': source['sha256'], 'pages': source['pages']}
                    for source in manifest['sources']],
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
