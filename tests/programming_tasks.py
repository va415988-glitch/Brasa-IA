"""Avaliação de tarefas práticas de programação contra o backend curado."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'python'))
from model_server import ModelService

def main():
    service = ModelService('task-evaluation')
    rows = [json.loads(line) for line in Path('corpus/eval/programming_tasks.jsonl').read_text().splitlines() if line.strip()]
    passed = 0
    for row in rows:
        result = service.reply([{'role': 'user', 'content': row['request']}])
        answer = result.get('text', '').lower()
        missing = [term for term in row['checks'] if term.lower() not in answer]
        ok = not missing and result.get('backend') == 'curated-memory'
        passed += ok
        print(f"{'OK' if ok else 'FAIL'} {row['id']} backend={result.get('backend')} missing={missing}")
    print(f'programming tasks: {passed}/{len(rows)}')
    return 0 if passed == len(rows) else 1

if __name__ == '__main__':
    raise SystemExit(main())
