"""Integration smoke test against the running runtime; only modifies its own fixture."""
import argparse
import json
import tempfile
import time
import urllib.request
from urllib.parse import unquote
from uuid import uuid4
from pathlib import Path


def post(base, path, body):
    request = urllib.request.Request(base + path, data=json.dumps(body).encode(),
                                     headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.load(response)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-url', default='http://127.0.0.1:3000')
    parser.add_argument('--output', type=Path, default=Path('/tmp/ia-operational-smoke-report.json'))
    args = parser.parse_args()
    results = []
    with tempfile.TemporaryDirectory(prefix='ia-smoke-') as directory:
        root = Path(directory)
        (root / 'nested').mkdir()
        (root / 'tests').mkdir()
        (root / 'nested/config.json').write_text(json.dumps({'version': 'smoke-42', 'description': 'ação 🚀 ' * 60}, ensure_ascii=False))
        (root / 'tests/test_smoke.py').write_text(
            'import unittest\nclass Smoke(unittest.TestCase):\n'
            '    def test_addition(self):\n        self.assertEqual(2 + 2, 4)\n')
        cases = [
            ('research', 'Pesquise na documentação oficial do Python como funciona asyncio.TaskGroup.', 'auto', False),
            ('capabilities', 'Quais habilidades você pode usar para pesquisar e testar um projeto?', 'auto', False),
            ('resolved-file', 'Leia config.json e diga qual é a versão.', 'auto', True),
            ('file-recovery', 'Consulte config.json e diga qual é a versão.', 'conversation', True),
            ('project-tests', 'Execute os testes do projeto.', 'auto', True),
            ('page', 'Consulte https://example.com e diga o que encontrou.', 'conversation', False),
        ]
        for name, prompt, objective, workspace in cases:
            body = {'schema': 'agent-request/v2', 'prompt': prompt, 'objective': objective,
                    'approved': name == 'project-tests', 'operation_id': 'agent-core-smoke-' + uuid4().hex}
            if workspace:
                body['workspace_root'] = str(root)
            started = time.monotonic()
            try:
                report = post(args.base_url, '/api/v1/agent/pursue', body)['report']
                text = report.get('finalText') or ''
                passed = report['status'] == 'completed'
                if name == 'research':
                    passed &= 'TaskGroup' in text and 'https://docs.python.org/' in text
                elif name == 'capabilities':
                    passed &= all(tool in text for tool in ['research_web', 'open_page', 'project_checks'])
                    passed &= not any(event['kind'] == 'tool.executed' for event in report['events'])
                elif name in ('resolved-file', 'file-recovery'):
                    passed &= 'smoke-42' in text and 'nested/config.json' in text
                    if name == 'file-recovery':
                        # Deltas are transient; validate the actual UI activity feed.
                        with urllib.request.urlopen(args.base_url + '/api/activity?after=0', timeout=10) as response:
                            activity = json.load(response)
                        messages = [event.get('message', '') for event in activity.get('events', [])
                                    if event.get('operation') == body['operation_id']]
                        last_reset = max((index for index, message in enumerate(messages)
                                          if message.startswith('__ANSWER_RESET__')), default=-1)
                        deltas = [unquote(message.split(' · ', 1)[1]) for message in messages[last_reset + 1:]
                                  if message.startswith('__ANSWER_DELTA__ · ')]
                        passed &= bool(deltas) and ''.join(deltas) == text
                elif name == 'project-tests':
                    verification = report.get('verification') or {}
                    passed &= verification.get('executed') is True and verification.get('passed') is True
                    passed &= 'Concluí a implementação' not in text
                elif name == 'page':
                    passed &= 'documentation examples' in text and 'https://example.com/' in text
                result = {'case': name, 'passed': bool(passed), 'seconds': round(time.monotonic() - started, 2),
                          'status': report['status'], 'final_text': text, 'error': report.get('error'),
                          'verification': report.get('verification'), 'events': report['events']}
            except Exception as error:
                result = {'case': name, 'passed': False, 'error': str(error)}
            results.append(result)
            print(f"{'PASS' if result['passed'] else 'FAIL'} {name}", flush=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({'schema': 'operational-smoke/v1', 'results': results,
                                     'passed': all(row['passed'] for row in results)}, ensure_ascii=False, indent=2))
    return 0 if all(row['passed'] for row in results) else 1


if __name__ == '__main__':
    raise SystemExit(main())
