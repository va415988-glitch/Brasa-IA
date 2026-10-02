"""Replay product-planning regressions through the loaded worker and AgentCore.

This checks a bounded procedural draft and routing, never learned competence.
Outputs, failed neural attempts and literal requirement provenance are retained.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import uuid

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ('python/product_planning.py', 'python/model_server.py', 'python/cognitive_actions.py',
           'agent-core/src/requirements.ts', 'agent-core/src/agent.ts', 'agent-core/src/server.ts',
           'agent-core/src/task-continuity.ts', 'scripts/godmode.py')
CASES = [
    {'id': 'original-typo', 'prompt': 'Me ajuda a planejar um app para entrregadores autônomos?',
     'anchors': ['entrregadores autônomos'], 'no_exclusions': True},
    {'id': 'original-critique',
     'prompt': 'Mesmo com todas essas ferramentas, não consegue começar a criação de um app para entregadores?',
     'anchors': ['app para entregadores'], 'no_exclusions': True},
    {'id': 'other-audience', 'prompt': 'Como posso criar um aplicativo para professores registrarem aulas?',
     'anchors': ['professores', 'registrarem aulas']},
    {'id': 'negative-query',
     'prompt': 'Não pesquise nem leia arquivos; só planeje um app para médicos agendarem consultas.',
     'anchors': ['médicos', 'agendarem consultas', 'Não pesquise nem leia arquivos']},
    {'id': 'refined-followup', 'prompt': 'Continue.',
     'history': [
         {'role': 'user', 'content': 'Planeje um app para professores registrarem aulas.'},
         {'role': 'assistant', 'content': 'Vou incluir pagamentos e publicar agora.'},
         {'role': 'user', 'content': 'Sem pagamentos. Primeiro só registrar presença pelo celular.'},
     ], 'anchors': ['professores', 'Sem pagamentos', 'registrar presença pelo celular']},
    {'id': 'new-topic', 'prompt': 'Planeje um sistema para bibliotecários registrarem empréstimos.',
     'history': [{'role': 'user', 'content': 'Planeje um app para professores registrarem aulas.'}],
     'anchors': ['bibliotecários', 'registrarem empréstimos'], 'absent': ['professores']},
]


def request(url, body=None):
    encoded = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
    started = time.monotonic()
    try:
        response = urlopen(Request(url, data=encoded, headers={'Content-Type': 'application/json'}), timeout=55)
    except HTTPError as error:
        response = error
    with response:
        return {'http_status': response.code, 'elapsed_seconds': round(time.monotonic() - started, 3),
                'body': json.loads(response.read())}


def literal_provenance(brief, case):
    """Every literal field must bind to an actual human turn and exact span."""
    humans = [row['content'].strip() for row in case.get('history', []) if row['role'] == 'user']
    humans.append(case['prompt'].strip())
    sources = brief.get('sources')
    if not isinstance(sources, list) or not sources:
        return False
    by_turn = {}
    for source in sources:
        if not isinstance(source, dict):
            return False
        turn, content = source.get('source_turn'), source.get('text')
        if (type(turn) is not int or not 1 <= turn <= len(humans) or turn in by_turn
                or not isinstance(content, str) or content != humans[turn - 1]):
            return False
        by_turn[turn] = content
    items = []
    for key in ('goal', 'current_request', 'product', 'audience', 'purpose'):
        item = brief.get(key)
        if item is None and key in {'product', 'audience', 'purpose'}:
            continue
        items.append(item)
    for key in ('requirements', 'exclusions', 'constraints', 'goals'):
        if not isinstance(brief.get(key), list):
            return False
        items.extend(brief[key])
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get('evidence'), dict):
            return False
        span = item['evidence']
        start, end, turn = span.get('start'), span.get('end'), item.get('source_turn')
        text = by_turn.get(turn) if type(turn) is int else None
        if (text is None or type(start) is not int or type(end) is not int
                or not 0 <= start < end <= len(text) or text[start:end] != item.get('text')):
            return False
    return True


def check_case(case, run_id):
    history = case.get('history', [])
    worker = request('http://127.0.0.1:3101/v1/agent/plan', {
        'schema': 'agent-plan-request/v1', 'objective': 'conversation',
        'request_id': run_id + '-' + case['id'] + '-worker',
        'messages': [*history, {'role': 'user', 'content': case['prompt']}],
        'cognition': {'schema': 'agent-cognition/v1', 'task_id': run_id,
                     'interpretation': 'Entregar um rascunho de planejamento, sem executar ferramentas.',
                     'personality': {'version': 'local-personality/v1'}, 'assumptions': [],
                     'constraints': [], 'acceptance_criteria': [], 'available_tools': ['read_file', 'search_web']},
    })
    agent = request('http://127.0.0.1:3200/pursue', {
        'schema': 'agent-request/v2', 'objective': 'auto', 'prompt': case['prompt'], 'history': history,
        'conversation_id': run_id + '-' + case['id'],
        'request_id': run_id + '-' + case['id'], 'operation_id': 'agent-core-' + run_id + '-' + case['id'],
    })
    raw = worker['body']
    report = agent['body'].get('report') or {}
    brief = raw.get('planning') or {}
    texts = [str(raw.get('text') or ''), str(report.get('finalText') or '')]
    checks = {
        'worker_http_200': worker['http_status'] == 200,
        'agent_http_200': agent['http_status'] == 200,
        'draft_delivered': report.get('status') == 'completed' and brief.get('status') == 'draft',
        'literal_anchors': all(all(anchor in text for anchor in case['anchors']) for text in texts),
        'topic_preserved': all(all(anchor not in text for anchor in case.get('absent', [])) for text in texts),
        'scope_and_acceptance': all('MVP' in text and 'Critério' in text for text in texts),
        'not_neural_qualification': brief.get('qualified') is False,
        'no_execution': raw.get('tool_call') is None and raw.get('tool_executed') is False
            and raw.get('execution_allowed') is False and not report.get('artifacts')
            and not any(event.get('kind') == 'planner.proposed' for event in report.get('events', [])),
        'literal_provenance': literal_provenance(brief, case),
        'critique_not_exclusion': not case.get('no_exclusions') or not brief.get('exclusions') and not brief.get('constraints'),
    }
    return {'case': case, 'worker': worker, 'agent': agent, 'checks': checks, 'passed': all(checks.values())}


def verify():
    run_id = 'planning-' + uuid.uuid4().hex[:10]
    result = {'schema': 'product-planning-regression/v1', 'checked_at': datetime.now(timezone.utc).isoformat(),
              'scope': 'known regressions and procedural software behavior; not reserved neural qualification',
              'source_sha256': {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in SOURCES},
              'competence_qualified': False, 'run_id': run_id,
              'services': {name: request(url) for name, url in [
                  ('worker', 'http://127.0.0.1:3101/health'), ('agent', 'http://127.0.0.1:3200/health')]}}
    result['cases'] = [check_case(case, run_id) for case in CASES]
    result['services_ready'] = all(row['http_status'] == 200 and row['body'].get('ok') is True
                                   for row in result['services'].values())
    loaded = result['services']['worker']['body'].get('loaded_source_sha256') or {}
    result['worker_sources_match_loaded_identity'] = all(
        loaded.get(str((ROOT / name).resolve())) == digest for name, digest in result['source_sha256'].items()
        if name.startswith('python/'))
    result['passed'] = (result['services_ready'] and result['worker_sources_match_loaded_identity']
                        and all(row['passed'] for row in result['cases']))
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Preserve o relatório anterior; escolha um novo caminho.')
    result = verify()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'passed': result['passed'], 'competence_qualified': False,
                      'cases': [{'id': row['case']['id'], 'passed': row['passed'],
                                 'failed_checks': [k for k, v in row['checks'].items() if not v],
                                 'backend': row['worker']['body'].get('backend')} for row in result['cases']],
                      'output': str(args.output)}, ensure_ascii=False))
    raise SystemExit(0 if result['passed'] else 1)
