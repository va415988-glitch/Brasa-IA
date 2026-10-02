"""Audit actual local programming operations in disposable projects.

The fixtures and assertions are authored here; no fixture is installed in the
agent's recipes. Passing this audit does not establish general coding mastery.
"""
import argparse
import json
from pathlib import Path
import tempfile
import subprocess
import sys
import time
import urllib.request
import urllib.error
from uuid import uuid4


def request(base, path, body=None):
    req = urllib.request.Request(base + path, data=json.dumps(body).encode() if body is not None else None,
                                 headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=180) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        raise RuntimeError(error.read().decode()[:2000]) from error


def audit(base):
    results = []
    def tool(name, arguments):
        response = request(base, '/api/v1/tools/call', {'tool': name, 'arguments': arguments})
        if not response.get('ok'):
            raise RuntimeError(response.get('error') or 'Tool failed: ' + name)
        return response.get('data') or {}
    try:
        original = tool('inspect_project', {}).get('workspace')
    except RuntimeError:
        # An interrupted older fixture may have left a deleted active root.
        original = str(Path(__file__).resolve().parents[1])
        tool('set_workspace', {'path': original})
    def record(name, action, check):
        started = time.monotonic()
        try:
            observed = action()
            passed = bool(check(observed))
            result = {'case': name, 'passed': passed, 'observed': observed}
        except Exception as error:
            result = {'case': name, 'passed': False, 'error': str(error)}
        result['seconds'] = round(time.monotonic() - started, 3)
        results.append(result)
        print(('PASS' if result['passed'] else 'FAIL') + ' ' + name, flush=True)

    def implement_and_verify(directory):
        result = request(base, '/api/v1/agent/pursue', {
            'schema': 'agent-request/v2',
            'prompt': 'Implemente em Python uma função interval_union que una intervalos fechados sobrepostos e escreva testes para entradas vazias, disjuntas, negativas e embaralhadas. Use apenas a biblioteca padrão. Execute os testes.',
            'objective': 'build', 'workspace_root': str(directory),
            'operation_id': 'agent-core-audit-' + uuid4().hex})
        # The audit authorizes only the disposable project, through real action approvals.
        for _ in range(8):
            if not result.get('awaitingApproval'): break
            report = result['report']
            action = next(event['payload']['actionId'] for event in reversed(report['events'])
                          if event.get('kind') == 'approval.required' and (event.get('payload') or {}).get('actionId'))
            result = request(base, f"/api/v1/agent/tasks/{report['taskId']}/resume", {
                'schema': 'agent-resume/v1', 'request_id': 'audit-resume-' + uuid4().hex,
                'kind': 'approval', 'action_id': action, 'decision': 'approve'})
        if (result.get('report') or {}).get('status') != 'completed': return result
        # Independent cases never supplied to the generator or installed as recipes.
        validation = '''import ast, importlib.util, pathlib
files=[p for p in pathlib.Path('.').rglob('*.py') if not p.name.startswith('test')]
target=next(p for p in files if any(isinstance(n,ast.FunctionDef) and n.name=='interval_union' for n in ast.walk(ast.parse(p.read_text()))))
spec=importlib.util.spec_from_file_location('candidate',target); module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
for source,expected in [([],[]), ([(7,9),(1,2),(2,5)],[(1,5),(7,9)]), ([(-8,-3),(-6,0)], [(-8,0)]), ([(2,3),(0,10),(4,4)],[(0,10)]), ([(5,6),(1,2),(5,6)],[(1,2),(5,6)])]:
    actual=[tuple(row) for row in module.interval_union(source)]
    assert actual==expected, (source, actual, expected)
print('5 independent interval_union cases passed')
'''
        checked = subprocess.run([sys.executable, '-c', validation], cwd=directory, text=True,
                                 capture_output=True, timeout=15)
        result['independent_verification'] = {'passed': checked.returncode == 0,
            'stdout': checked.stdout[-2000:], 'stderr': checked.stderr[-2000:], 'cases': 5}
        return result

    with tempfile.TemporaryDirectory(prefix='ia-programming-audit-') as temporary:
        root = Path(temporary)
        def project(name, files):
            directory = root / name
            directory.mkdir()
            for path, content in files.items():
                target = directory / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content)
            tool('set_workspace', {'path': str(directory)})
            return directory
        try:
            python = project('python', {'logic.py': 'import math\n\nasync def carregar(valor):\n    return math.ceil(valor)\n\nclass Caixa:\n    def total(self, valores):\n        return sum(valores)\n',
                'test_logic.py': 'import unittest\nfrom logic import Caixa\nclass Contract(unittest.TestCase):\n    def test_total(self): self.assertEqual(Caixa().total([3, 8]), 11)\n'})
            record('inspect-python-file', lambda: tool('inspect_code', {'path': 'logic.py'}),
                   lambda data: data.get('files_scanned') == 1 and {'carregar', 'Caixa', 'total'} <= {row['name'] for row in data.get('symbols', [])})
            record('agent-explains-python-file', lambda: request(base, '/api/v1/agent/pursue', {
                'schema': 'agent-request/v2', 'prompt': 'Leia logic.py e explique as funções existentes, sem alterar arquivos.',
                'objective': 'analyze', 'workspace_root': str(python), 'operation_id': 'agent-core-audit-' + uuid4().hex}),
                lambda data: (data.get('report') or {}).get('status') == 'completed'
                and all(term in str(data['report'].get('finalText')) for term in ('logic.py', 'carregar', 'total')))
            project('typescript', {'logic.ts': 'export function juntar(a: string, b: string): string { return a + b; }\nexport const dobro = (n: number): number => n * 2;\nexport class Fila { tamanho(): number { return 0; } }\n'})
            record('inspect-typescript-declarations', lambda: tool('inspect_code', {'path': 'logic.ts'}),
                   lambda data: {'juntar', 'dobro', 'Fila', 'tamanho'} <= {row['name'] for row in data.get('symbols', [])})
            project('node-tests', {'app.cjs': 'exports.total = rows => rows.reduce((a, b) => a + b, 0);\n',
                'tests/app.test.cjs': "const {test}=require('node:test'); const assert=require('node:assert/strict'); const {total}=require('../app.cjs'); test('total',()=>assert.equal(total([5,7]),12));\n"})
            record('node-tests-without-manifest', lambda: tool('project_checks', {'check': 'auto'}),
                   lambda data: data.get('executed') is True and data.get('passed') is True and data.get('check') == 'node-test')
            project('empty-tests', {'app.py': 'def total(a,b): return a+b\n', 'tests/test_empty.py': 'import unittest\nclass Empty(unittest.TestCase): pass\n'})
            record('zero-tests-is-not-success', lambda: tool('project_checks', {'check': 'unittest'}),
                   lambda data: data.get('executed') is True and data.get('passed') is False and data.get('tests_executed') == 0)
            project('large-output', {'test_output.py': "import sys, unittest\nclass Output(unittest.TestCase):\n    def test_stream(self):\n        sys.stderr.write('x'*200000)\n        self.assertEqual(6*7,42)\n"})
            record('large-output-does-not-deadlock', lambda: tool('project_checks', {'check': 'unittest'}),
                   lambda data: data.get('executed') is True and data.get('passed') is True and data.get('truncated') is True and not data.get('timed_out'))
            project('go', {'go.mod': 'module local/audit\n\ngo 1.20\n', 'sum.go': 'package audit\nfunc Sum(a,b int) int { return a+b }\n',
                'sum_test.go': 'package audit\nimport "testing"\nfunc TestSum(t *testing.T) { if Sum(4,9)!=13 { t.Fatal("sum") } }\n'})
            record('go-tests', lambda: tool('project_checks', {'check': 'auto', 'path': 'sum.go'}),
                   lambda data: data.get('executed') is True and data.get('passed') is True and data.get('check') == 'go-test')
            project('cpp', {'calculation.cpp': 'int sum(int a, int b) { return a + b; }\n'})
            record('cpp-compiler-check', lambda: tool('project_checks', {'check': 'auto', 'path': 'calculation.cpp'}),
                   lambda data: data.get('executed') is True and data.get('passed') is True and data.get('verification_kind') == 'syntax')
            fresh = project('new-algorithm', {})
            record('novel-python-implementation', lambda: implement_and_verify(fresh),
                lambda data: (data.get('report') or {}).get('status') == 'completed'
                and (data['report'].get('verification') or {}).get('passed') is True
                and (data.get('independent_verification') or {}).get('passed') is True)
        finally:
            if original:
                tool('set_workspace', {'path': original})
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='http://127.0.0.1:3000')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    rows = audit(args.base_url)
    report = {'schema': 'programming-workflows-audit/v1', 'cases': rows,
              'passed': sum(row['passed'] for row in rows), 'total': len(rows),
              'general_programming_mastery': False,
              'limitations': ['Ferramentas e geração são avaliadas separadamente; os casos não cobrem qualquer cenário.',
                              'A implementação nova é confrontada com cinco casos funcionais independentes.']}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"{report['passed']}/{report['total']}", flush=True)
    return 0 if report['passed'] == report['total'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
