"""Run actual creation, baseline failure, approved repair and recheck in temp projects.

The sources/examples below are audit fixtures, not recipes installed in the AI.
Only action proposals belonging to these disposable projects are approved.
"""
import argparse
import hashlib
import json
from pathlib import Path
import tempfile
import urllib.error
import urllib.request
from uuid import uuid4

FIXTURES = [
    ('converter', 'converter.py', 'tests/test_converter.py', 'celsius_para_fahrenheit',
     'def celsius_para_fahrenheit(c):\n    return c * 9 / 5 + 30\n',
     ['0 °C deve resultar em 32 °F.', '100 °C deve resultar em 212 °F.', '-40 °C deve resultar em -40 °F.'],
     [([37.5], {}, 99.5)]),
    ('keywords', 'src/prices.py', 'tests/test_prices.py', 'subtotal',
     'def subtotal(values, bonus=0):\n    return sum(values) * 2 + bonus\n',
     ['subtotal([2, 7], bonus=1) deve retornar 28', 'subtotal([5, -1], bonus=4) deve retornar 16',
      'subtotal([0, 4], bonus=-3) deve retornar 9'],
     [([[13, 2]], {'bonus': -4}, 41)]),
    ('comparison', 'boundary.py', 'test_boundary.py', 'accepted',
     'def accepted(x):\n    return x < 0\n', ['-0.1 => true', '0 => true', '0.1 => false'],
     [([-0.000001], {}, True), ([0.000001], {}, False)]),
    ('ambiguous', 'ambiguous.py', 'test_ambiguous.py', 'answer',
     'def answer(x):\n    return x * 2 + x * 2\n', ['1 => 5', '2 => 10', '3 => 15'], []),
]


def request(base, path, body=None):
    req = urllib.request.Request(base + path, data=json.dumps(body).encode() if body is not None else None,
                                 headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=90) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        raise RuntimeError(error.read().decode()[:1500]) from error


def audit(base, only=None):
    def tool(name, args):
        response = request(base, '/api/v1/tools/call', {'tool': name, 'arguments': args})
        if response.get('ok') is not True:
            raise RuntimeError(response.get('error') or name)
        return response.get('data', {})
    original = tool('inspect_project', {}).get('workspace')
    if not original:
        raise RuntimeError('Cannot identify the workspace before the audit')
    rows = []
    with tempfile.TemporaryDirectory(prefix='ia-example-programming-audit-') as temporary:
        try:
            for name, path, test_path, function, source, cases, heldout in FIXTURES:
                if only and name != only:
                    continue
                root = Path(temporary) / name; root.mkdir()
                tool('set_workspace', {'path': str(root)})
                prompt = (f'Crie {path} com esta função:\n\n{source}\nCrie {test_path} com três testes separados:\n'
                          + '\n'.join('- ' + case for case in cases)
                          + '\n\nExecute os testes antes de alterar a função. Depois investigue a falha, corrija o código e execute os testes novamente.\n'
                          + 'Entregue os arquivos e mostre as evidências das duas execuções.')
                result = request(base, '/api/v1/agent/pursue', {
                    'schema': 'agent-request/v2', 'objective': 'auto', 'workspace_root': str(root),
                    'operation_id': 'agent-core-example-audit-' + uuid4().hex, 'prompt': prompt})
                baselines, baseline_workflows, approval_previews, approvals, test_hash = [], [], [], 0, None
                for _ in range(4):
                    if not result.get('awaitingApproval'):
                        break
                    report = result['report']; verification = report.get('verification') or {}
                    if verification.get('executed') is True:
                        baselines.append(verification)
                        baseline_workflows.append(request(base, f"/api/v1/agent/tasks/{report['taskId']}/workflow").get('workflow', {}).get('verification'))
                        test_hash = hashlib.sha256((root / test_path).read_bytes()).hexdigest()
                    event = next(e for e in reversed(report['events']) if e.get('kind') == 'approval.required'
                                 and e.get('payload', {}).get('actionId'))
                    if event.get('payload', {}).get('tool') == 'apply_repair':
                        approval_previews.append({'path': event.get('detail'), 'diff': event['payload'].get('repairDiff')})
                    result = request(base, f"/api/v1/agent/tasks/{report['taskId']}/resume", {
                        'schema': 'agent-resume/v1', 'request_id': 'example-audit-' + uuid4().hex,
                        'kind': 'approval', 'action_id': event['payload']['actionId'], 'decision': 'approve'})
                    approvals += 1
                report = result.get('report', {})
                verification = report.get('verification') or {}
                workflow = request(base, f"/api/v1/agent/tasks/{report['taskId']}/workflow").get('workflow', {})
                files = {p.relative_to(root).as_posix(): p.read_text() for p in root.rglob('*.py')
                         if '.ia-local-backups' not in p.parts and '__pycache__' not in p.parts}
                independent = [tool('evaluate_function', {'path': path, 'function': function,
                    'args': args, 'kwargs': kwargs}) for args, kwargs, expected in heldout] if report.get('status') == 'completed' else []
                oracle_unchanged = bool(test_hash and (root / test_path).is_file()
                    and hashlib.sha256((root / test_path).read_bytes()).hexdigest() == test_hash)
                if name == 'ambiguous':
                    passed = report.get('status') == 'blocked' and files.get(path) == source \
                        and verification.get('executed') is True and verification.get('passed') is False \
                        and workflow.get('verification') == 'failed'
                else:
                    passed = (report.get('status') == 'completed' and approvals == 2 and len(baselines) == 1
                        and baselines[0].get('passed') is False and baselines[0].get('testsExecuted') == 3
                        and verification.get('executed') is True and verification.get('passed') is True
                        and verification.get('testsExecuted') == 3 and oracle_unchanged
                        and baseline_workflows == ['failed'] and workflow.get('verification') == 'passed'
                        and len(approval_previews) == 1 and approval_previews[0]['path'] == path
                        and approval_previews[0].get('diff', {}).get('oldText') == source
                        and approval_previews[0].get('diff', {}).get('newText') == files.get(path)
                        and 'Primeira execução: falhou' in report.get('finalText', '')
                        and 'Última execução: aprovada' in report.get('finalText', '')
                        and len(independent) == len(heldout)
                        and all(r.get('passed') is True and r.get('result') == spec[2] for r, spec in zip(independent, heldout)))
                rows.append({'case': name, 'passed': passed, 'status': report.get('status'), 'approvals': approvals,
                    'baseline': baselines, 'verification': verification, 'test_oracle_unchanged': oracle_unchanged,
                    'baseline_workflows': baseline_workflows, 'workflow': workflow, 'approval_previews': approval_previews,
                    'files': files, 'independent': independent, 'final_text': report.get('finalText'), 'error': report.get('error')})
                print(('PASS ' if passed else 'FAIL ') + name + ' · ' + str(report.get('status')), flush=True)
        finally:
            tool('set_workspace', {'path': original})
    return {'schema': 'example-programming-audit/v1', 'passed': bool(rows) and all(r['passed'] for r in rows),
            'successful': sum(r['passed'] for r in rows), 'total': len(rows), 'cases': rows}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--base', default='http://127.0.0.1:3000')
    parser.add_argument('--only', choices=[row[0] for row in FIXTURES])
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(); report = audit(args.base, args.only)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(f"{report['successful']}/{report['total']} passed")
    raise SystemExit(0 if report['passed'] else 1)
