"""Probe real local services and isolation; this does not certify model quality."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import re
from pathlib import Path
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def request(url, body=None, *, raw=None):
    data = raw if raw is not None else json.dumps(body, ensure_ascii=False).encode() if body is not None else None
    call = Request(url, data=data, headers={'Content-Type': 'application/json'} if data is not None else {})
    started = time.monotonic()
    try:
        response = urlopen(call, timeout=35)
    except HTTPError as error:
        response = error
    with response:
        result = {'http_status': response.code, 'elapsed_seconds': round(time.monotonic() - started, 3),
                  'cache_control': response.headers.get('Cache-Control'), 'body': json.loads(response.read())}
    return result


def verify():
    base = 'http://127.0.0.1:3000'
    report = {'schema': 'model-activation-check/v1', 'checked_at': datetime.now(timezone.utc).isoformat(),
              'scope': 'service availability and experimental isolation; not competence qualification'}
    report['services'] = {
        'runtime': request(base + '/api/health'),
        'worker': request('http://127.0.0.1:3101/health'),
        'agent': request('http://127.0.0.1:3200/health'),
    }
    for service in report['services'].values():
        assert service['http_status'] == 200 and service['body']['ok'] is True
    worker = report['services']['worker']['body']
    assert worker['free_generation'] is True and worker['error'] is None
    assert report['services']['agent']['body']['model'] == worker['checkpoint']
    report['cores'] = request(base + '/api/v1/cognition/cores')
    cores = report['cores']['body']
    assert report['cores']['http_status'] == 200 and not cores['evidence_errors']
    assert len(cores['cores']) == 10 and not any(c['dispatch_enabled'] for c in cores['cores'])
    report['experimental_before'] = request(base + '/api/v1/models/experimental')
    status = report['experimental_before']
    assert status['http_status'] == 200 and status['elapsed_seconds'] < 10
    assert status['cache_control'] == 'no-store'
    assert status['body']['enabled'] is True and status['body']['qualified'] is False
    assert list(status['body']['core_states'].values()).count('failed') == 5
    assert list(status['body']['core_states'].values()).count('not_evaluated') == 5
    report['examples'] = []
    for question, source, expected in [
            ('Qual é o limite de Pipa?', 'Pipa: limite = 42.', '42'),
            ('A versão 41 atende ao mínimo exigido por Pipa?', 'Pipa: versão mínima = 42.', 'Não.')]:
        body = {'schema': 'experimental-cognitive-request/v1',
                'messages': [{'role': 'user', 'content': question}, {'role': 'tool', 'content': json.dumps({
                    'tool': 'read_file', 'ok': True, 'data': {'path': 'dados.json', 'content': source}, 'error': ''},
                    ensure_ascii=False)}], 'cognition': {'schema': 'agent-cognition/v1', 'available_tools': []}}
        result = request(base + '/api/v1/models/experimental/decide', body)
        value = result['body']
        assert result['http_status'] in (200, 422) and result['cache_control'] == 'no-store'
        assert value['schema'] == 'experimental-cognitive-response/v1' and value['experimental'] is True
        assert value['qualified'] is False and value['tool_executed'] is False and value['execution_allowed'] is False
        assert value['generation_attempts'] == 1 and value['raw_output']
        result.update(question=question, source=source, expected=expected)
        # Record errors honestly; these two known examples are not a new reserved suite.
        proposal = value.get('decision') or {}
        text = str(proposal.get('text') or '').strip()
        result['example_correct'] = (proposal.get('decision') == 'answer'
            and (text.rstrip('.! ') == 'Não' if expected == 'Não.' else re.findall(r'\d+', text) == ['42']))
        report['examples'].append(result)
    invalid = {**body, 'messages': [{'role': 'user', 'content': 'Escreva um programa.'}]}
    report['out_of_scope'] = request(base + '/api/v1/models/experimental/decide', invalid)
    assert report['out_of_scope']['http_status'] == 422
    assert report['out_of_scope']['body']['generation_attempts'] == 0
    specialist = {**body, 'schema': 'cognitive-core-request/v1',
                  'scope': 'synthetic-numeric-evidence/v1', 'cores': ['numeric-comparison']}
    report['unproved_dispatch'] = request(base + '/api/v1/cognition/cores/decide', specialist)
    assert report['unproved_dispatch']['http_status'] == 422
    assert report['unproved_dispatch']['body']['generation_attempts'] == 0
    assert report['unproved_dispatch']['body']['error_code'] == 'core_competence_unproven'
    report['oversized'] = request(base + '/api/v1/models/experimental/decide', raw=b'x' * 65537)
    assert report['oversized']['http_status'] == 413
    report['experimental_after'] = request(base + '/api/v1/models/experimental')
    assert report['experimental_after']['body']['loaded'] is True
    report['availability_and_isolation_passed'] = True
    report['competence_qualified'] = False
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    result = verify()
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'availability_and_isolation_passed': True, 'competence_qualified': False,
                      'example_correct': [r['example_correct'] for r in result['examples']],
                      'first_experimental_status_seconds': result['experimental_before']['elapsed_seconds'],
                      'output': str(args.output) if args.output else None}, ensure_ascii=False))
