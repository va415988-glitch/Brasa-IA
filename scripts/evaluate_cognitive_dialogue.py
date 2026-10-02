#!/usr/bin/env python3
"""Offline neural decision evaluation. No tools, trace writes, training or promotion."""
import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'python'))
from active_checkpoint import selected_checkpoint
from model_server import ModelService


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--checkpoint', type=Path, default=selected_checkpoint())
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--max-tokens', type=int, default=384)
    args = parser.parse_args()
    os.environ['IA_LOCAL_NUM_PREDICT'] = str(args.max_tokens)
    service = ModelService(args.checkpoint, trace_path=None, cognitive_router_manifest=None)
    if service.local_model is None:
        raise SystemExit('Checkpoint indisponível: ' + str(service.local_model_error))
    cases = [
        {'id': 'direct', 'prompt': 'Quanto é 13 mais 29?', 'expected': 'answer', 'contains': '42'},
        {'id': 'external_gap', 'prompt': 'Qual é a versão estável mais recente da biblioteca LumenGrid?', 'expected': 'consult'},
        {'id': 'observed_requirement', 'prompt': 'O pacote Riacho funciona no Node 20?', 'expected': 'answer', 'contains': '24',
         'observation': {'tool': 'open_page', 'ok': True, 'data': {
             'url': 'https://example.org/riacho/docs', 'content': 'Riacho exige Node 24 ou posterior. Node 20 não é suportado.'}}},
        {'id': 'failed_source', 'prompt': 'A documentação confirma suporte ao Node 20?', 'expected': 'consult_or_blocked',
         'observation': {'tool': 'open_page', 'ok': False, 'error': 'HTTP 503: fonte indisponível.'}},
    ]
    rows = []
    for case in cases:
        messages = [{'role': 'user', 'content': case['prompt']}]
        if case.get('observation'):
            messages.append({'role': 'tool', 'content': json.dumps(case['observation'])})
        started = time.monotonic()
        response = service.cognitive_conversation(messages, cognition={
            'schema': 'agent-cognition/v1', 'available_tools': ['search_web', 'open_page', 'research_web']})
        kind = (response.get('cognition') or {}).get('decision')
        valid = response.get('backend') == 'cognitive-dialogue'
        passed = valid and (kind in ('consult', 'blocked') if case['expected'] == 'consult_or_blocked' else kind == case['expected'])
        if case.get('contains'):
            passed = passed and case['contains'] in response.get('text', '')
        row = {'id': case['id'], 'expected': case['expected'], 'passed': bool(passed),
               'valid_decision': valid, 'elapsed_seconds': round(time.monotonic() - started, 3), 'response': response}
        rows.append(row)
        print(case['id'], 'PASS' if passed else 'FAIL', flush=True)
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps({'mode': 'neural-decisions-with-simulated-observations',
            'checkpoint': str(args.checkpoint), 'checkpoint_sha256': hashlib.sha256(args.checkpoint.read_bytes()).hexdigest(),
            'generation_token_budget': args.max_tokens,
            'limitations': ['No live tool execution', 'Synthetic observations; no external facts verified',
                             'Protocol checks and narrow answer checks do not prove general comprehension'],
            'passed': sum(r['passed'] for r in rows), 'total': len(rows), 'cases': rows}, ensure_ascii=False, indent=2) + '\n')


if __name__ == '__main__':
    main()
