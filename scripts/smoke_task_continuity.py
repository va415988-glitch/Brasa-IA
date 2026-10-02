"""Restart the local application during a controlled read and resume without history.

The test only interrupts its own accepted task. It uses the project's start.sh
to replace the verified local runtime, preserving the normal checkpoint files.
"""
import argparse
import json
import subprocess
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]


def request(base, path, body=None, timeout=120):
    req = urllib.request.Request(base + path, data=json.dumps(body).encode() if body is not None else None,
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.load(response)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='http://127.0.0.1:3000')
    parser.add_argument('--output', type=Path, default=Path('/tmp/ia-continuity-report.json'))
    args = parser.parse_args()
    observed = threading.Event()
    release = threading.Event()
    hits = []

    class Source(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            hits.append(self.path)
            if len(hits) == 1:
                observed.set()
                release.wait(40)
            body = b'<html><title>Continuity fixture</title><main>The verified marker is CONTINUITY-42.</main></html>'
            try:
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.end_headers()
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

    source = ThreadingHTTPServer(('127.0.0.1', 0), Source)
    threading.Thread(target=source.serve_forever, daemon=True).start()
    conversation = 'continuity-' + uuid4().hex
    operation = 'agent-core-continuity-' + uuid4().hex
    url = f'http://127.0.0.1:{source.server_port}/document'
    prompt = f'Consulte {url} e diga qual marcador está registrado.'
    result = {'schema': 'task-continuity-smoke/v1', 'checks': []}
    first_result = {}

    def pursue():
        try:
            first_result['response'] = request(args.base_url, '/api/v1/agent/pursue', {
                'schema': 'agent-request/v2', 'prompt': prompt, 'objective': 'conversation',
                'attachments': [{'path': 'context.txt', 'content': 'Controlled continuity test fixture.'}],
                'conversation_id': conversation, 'operation_id': operation})
        except Exception as error:
            first_result['interruption'] = str(error)

    thread = threading.Thread(target=pursue, daemon=True)
    thread.start()
    try:
        if not observed.wait(20):
            raise RuntimeError('A tarefa não iniciou a leitura controlada: ' + str(first_result))
        tasks = request(args.base_url, '/api/v1/agent/tasks?operation_id=' + operation)['tasks']
        if len(tasks) != 1:
            raise RuntimeError('A execução controlada não possui uma identidade persistida única.')
        task_id = tasks[0]['taskId']
        before = request(args.base_url, '/api/v1/agent/tasks/' + task_id)
        checkpoint = before.get('checkpoint') or {}
        result['task_id'] = task_id
        result['checks'].append({'id': 'persist_before_effect', 'passed': checkpoint.get('phase') == 'executing'
                                 and checkpoint.get('inFlight', {}).get('tool') == 'open_page'})
        if not result['checks'][-1]['passed']:
            raise RuntimeError('Não há checkpoint em voo; o teste não reiniciará a aplicação.')
        log_path = Path('/tmp/ia-continuity-runtime.log')
        with log_path.open('w') as log:
            process = subprocess.Popen(['bash', 'start.sh'], cwd=ROOT, stdout=log, stderr=log, start_new_session=True)
        result['restart_process_id'] = process.pid
        deadline = time.monotonic() + 40
        after = None
        while time.monotonic() < deadline:
            try:
                after = request(args.base_url, '/api/v1/agent/tasks/' + task_id, timeout=2)
                if after['task']['status'] == 'interrupted':
                    break
            except (OSError, urllib.error.URLError, ValueError):
                pass
            time.sleep(.25)
        if not after or after['task']['status'] != 'interrupted':
            raise RuntimeError('A aplicação não restaurou a tarefa como interrompida.')
        release.set()
        result['checks'].append({'id': 'goal_survives_restart', 'passed': after['checkpoint']['request']['prompt'] == prompt})
        resumed = request(args.base_url, '/api/v1/agent/pursue', {
            'schema': 'agent-request/v2', 'prompt': 'Continue de onde parou.', 'objective': 'auto',
            'conversation_id': conversation, 'operation_id': 'agent-core-continued-' + uuid4().hex})
        report = resumed.get('report') or {}
        result['status'] = report.get('status')
        result['final_text'] = report.get('finalText')
        result['error'] = report.get('error')
        result['checks'].append({'id': 'same_task_without_client_history', 'passed': report.get('taskId') == task_id})
        result['checks'].append({'id': 'verified_source_delivered', 'passed': report.get('status') == 'completed'
                                 and 'CONTINUITY-42' in str(report.get('finalText')) and url in str(report.get('finalText'))})
        final = request(args.base_url, '/api/v1/agent/tasks/' + task_id)
        receipts = final['checkpoint']['resume']['completedCalls']
        result['checks'].append({'id': 'receipt_persisted', 'passed': final['checkpoint']['phase'] != 'executing'
                                 and len(receipts) == 1 and receipts[0]['ok'] is True
                                 and receipts[0]['tool'] == 'open_page'})
    except Exception as error:
        result['error'] = str(error)
        result['checks'].append({'id': 'integration', 'passed': False})
    finally:
        release.set()
        source.shutdown()
        result['passed'] = bool(result['checks']) and all(row['passed'] for row in result['checks'])
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
        for check in result['checks']:
            print(('PASS' if check['passed'] else 'FAIL') + ' ' + check['id'], flush=True)
        if result.get('error'):
            print(result['error'], flush=True)
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
