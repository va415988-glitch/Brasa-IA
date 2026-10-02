#!/usr/bin/env python3
"""Avaliação comportamental do currículo agêntico held-out."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'python'))
from model_server import ModelService

CASES = [json.loads(line) for line in (ROOT / 'model/training/senior-creative-v1/agentic-heldout-v1.jsonl').read_text(encoding='utf-8').splitlines() if line.strip()]

REQUIRED = {
    'behavior': [('fonte', 'dado'), ('escopo', 'permiss'), ('não', 'nao')],
    'tools': [('dois', 'arquivos'), ('test', 'verif'), ('evidência', 'resultado')],
    'reasoning': [('medir', 'gargalo'), ('custo', 'risco'), ('causa', 'contrato')],
    'programming': [('teste', 'integração'), ('timeout', 'erro'), ('segredo', 'logs')],
}

def main():
    service = ModelService('benchmark-only')
    rows = []
    for case in CASES:
        question = case['messages'][0]['content']
        result = service.reply(case['messages'])
        text = str(result.get('text') or '').lower()
        alternatives = REQUIRED.get(case['domain'], [])
        hits = [any(term in text for term in group) for group in alternatives]
        if case['domain'] == 'tools' and result.get('tool_call'):
            hits = [True, True, True]
        rows.append({'domain': case['domain'], 'question': question, 'backend': result.get('backend'), 'tool': (result.get('tool_call') or {}).get('tool'), 'hits': hits, 'passed': all(hits), 'text': result.get('text', '')})
    passed = sum(row['passed'] for row in rows)
    report = {'schema': 'agentic-curriculum-eval/v1', 'passed': passed, 'total': len(rows), 'pass_rate': round(passed / max(1, len(rows)), 3), 'cases': rows}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if passed == len(rows) else 1)

if __name__ == '__main__':
    main()
