"""Audit the explorer API in a disposable workspace and restore the active project."""
import argparse
import json
from pathlib import Path
import tempfile
import urllib.error
import urllib.request
from uuid import uuid4


def audit(base):
    rows = []

    def request(path, body, origin=None):
        headers = {"Content-Type": "application/json"}
        if origin:
            headers["Origin"] = origin
        req = urllib.request.Request(base + path, data=json.dumps(body).encode(), headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=90) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            return json.loads(error.read())

    def tool(name, args):
        return request('/api/v1/tools/call', {'tool': name, 'arguments': args})

    def record(name, action, check):
        try:
            observed = action()
            row = {'case': name, 'passed': bool(check(observed)), 'observed': observed}
        except Exception as error:
            row = {'case': name, 'passed': False, 'error': str(error)}
        rows.append(row)
        print(('PASS ' if row['passed'] else 'FAIL ') + name, flush=True)

    original = tool('inspect_project', {}).get('data', {}).get('workspace')
    if not original:
        raise RuntimeError('Could not identify the active workspace before the audit')
    with tempfile.TemporaryDirectory(prefix='ia-explorer-audit-') as temporary:
        root = Path(temporary)

        def entry(operation, **fields):
            return request('/api/v1/workspace/entries', {
                'schema': 'workspace-entry/v1', 'workspace_root': str(root),
                'operation': operation, 'request_id': 'audit-' + uuid4().hex, **fields})

        try:
            if not tool('set_workspace', {'path': str(root)}).get('ok'):
                raise RuntimeError('Could not select the disposable workspace')
            record('create-directory', lambda: entry('create_directory', path='dados'),
                   lambda r: r.get('ok') and (root / 'dados').is_dir())
            record('create-empty-file', lambda: entry('create_file', path='dados/vazio.txt', content=''),
                   lambda r: r.get('ok') and (root / 'dados/vazio.txt').read_text() == '')
            record('save-initial-empty-file', lambda: entry('edit_file', path='dados/vazio.txt', old_text='', new_text='olá\n'),
                   lambda r: r.get('ok') and (root / 'dados/vazio.txt').read_text() == 'olá\n')
            record('refuse-overwrite', lambda: entry('create_file', path='dados/vazio.txt', content='substituir'),
                   lambda r: r.get('ok') is False and (root / 'dados/vazio.txt').read_text() == 'olá\n')
            record('refuse-stale-save', lambda: entry('edit_file', path='dados/vazio.txt', old_text='', new_text='substituir'),
                   lambda r: r.get('ok') is False and (root / 'dados/vazio.txt').read_text() == 'olá\n')
            binary = bytes(range(256))
            (root / 'dados' / 'bytes.bin').write_bytes(binary)
            trashed = entry('trash', path='dados')
            record('trash-directory-with-binary', lambda: trashed,
                   lambda r: r.get('ok') and not (root / 'dados').exists())
            trash_id = trashed.get('data', {}).get('trash_id', '')
            record('list-recoverable-directory', lambda: entry('list_trash'),
                   lambda r: r.get('ok') and any(e['trash_id'] == trash_id for e in r['data']['entries']))
            record('restore-entire-directory', lambda: entry('restore', trash_id=trash_id),
                   lambda r: r.get('ok') and (root / 'dados/bytes.bin').read_bytes() == binary
                   and (root / 'dados/vazio.txt').read_text() == 'olá\n')
            record('restored-entry-leaves-list', lambda: entry('list_trash'),
                   lambda r: r.get('ok') and not r['data']['entries'])
            record('refuse-parent-traversal', lambda: entry('create_file', path='../escape.txt', content=''),
                   lambda r: r.get('ok') is False)
            record('protect-recovery-history', lambda: entry('trash', path='.ia-local-backups'),
                   lambda r: r.get('ok') is False)
            record('refuse-wrong-workspace', lambda: request('/api/v1/workspace/entries', {
                'schema': 'workspace-entry/v1', 'workspace_root': original,
                'operation': 'create_file', 'path': 'wrong.txt', 'content': ''}),
                   lambda r: r.get('ok') is False and not (root / 'wrong.txt').exists())
            record('refuse-external-origin', lambda: request('/api/v1/workspace/entries', {
                'schema': 'workspace-entry/v1', 'workspace_root': str(root),
                'operation': 'create_file', 'path': 'remote.txt', 'content': ''}, 'https://example.org'),
                   lambda r: r.get('ok') is False and not (root / 'remote.txt').exists())
            source = ('def total_pedido(itens, desconto=0):\n'
                      '    return round(sum(preco * quantidade for preco, quantidade in itens) * (1 - desconto), 2)\n')
            record('create-function-fixture', lambda: entry('create_file', path='teste_motor.py', content=source),
                   lambda r: r.get('ok') and (root / 'teste_motor.py').read_text() == source)
            record('new-chat-executes-workspace-function', lambda: request('/api/v1/agent/pursue', {
                'schema': 'agent-request/v2', 'objective': 'auto', 'workspace_root': str(root),
                'operation_id': 'agent-core-explorer-audit-' + uuid4().hex,
                'prompt': 'Execute total_pedido([[19.9, 3], [8.5, 2]], desconto=0.1) em teste_motor.py'}),
                   lambda r: r.get('report', {}).get('status') == 'completed'
                   and '69.03' in r['report'].get('finalText', '')
                   and any(e.get('payload', {}).get('tool') == 'evaluate_function'
                           for e in r['report'].get('events', [])))
        finally:
            restored = tool('set_workspace', {'path': original})
            if not restored.get('ok'):
                raise RuntimeError('Could not restore the original workspace')
    return {'schema': 'workspace-explorer-audit/v1', 'passed': all(r['passed'] for r in rows),
            'cases': rows, 'total': len(rows), 'successful': sum(bool(r['passed']) for r in rows)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--base', default='http://127.0.0.1:3000')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    report = audit(args.base)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(f"{report['successful']}/{report['total']} passed")
    raise SystemExit(0 if report['passed'] else 1)
