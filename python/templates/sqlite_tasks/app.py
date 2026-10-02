"""Personal task app: local HTTP API and SQLite, using the standard library."""

import argparse
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import sqlite3
from urllib.parse import urlsplit


class TaskStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.execute('CREATE TABLE IF NOT EXISTS tasks ('
                       'id INTEGER PRIMARY KEY AUTOINCREMENT, '
                       'title TEXT NOT NULL CHECK(length(trim(title)) > 0), '
                       'completed INTEGER NOT NULL DEFAULT 0 CHECK(completed IN (0, 1)))')

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def task(row):
        return {'id': row['id'], 'title': row['title'], 'completed': bool(row['completed'])}

    def list(self):
        with self.connection() as db:
            return [self.task(row) for row in db.execute('SELECT * FROM tasks ORDER BY id')]

    def add(self, title):
        if not isinstance(title, str) or not title.strip():
            raise ValueError('Digite um título para a tarefa.')
        title = title.strip()
        if len(title) > 500:
            raise ValueError('O título deve ter até 500 caracteres.')
        with self.connection() as db:
            task_id = db.execute('INSERT INTO tasks(title) VALUES (?)', (title,)).lastrowid
            return self.task(db.execute('SELECT * FROM tasks WHERE id = ?', (task_id,)).fetchone())

    def complete(self, task_id, completed):
        if not isinstance(completed, bool):
            raise ValueError('completed deve ser true ou false.')
        with self.connection() as db:
            cursor = db.execute('UPDATE tasks SET completed = ? WHERE id = ?', (int(completed), task_id))
            if cursor.rowcount == 0:
                raise LookupError('Tarefa não encontrada.')
            return self.task(db.execute('SELECT * FROM tasks WHERE id = ?', (task_id,)).fetchone())

    def delete(self, task_id):
        with self.connection() as db:
            if db.execute('DELETE FROM tasks WHERE id = ?', (task_id,)).rowcount == 0:
                raise LookupError('Tarefa não encontrada.')


class TaskHandler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def send_body(self, status, payload, content_type='application/json; charset=utf-8'):
        body = payload.encode('utf-8') if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(body)

    def local_request(self):
        try:
            host = urlsplit('http://' + self.headers.get('Host', ''))
            if host.hostname not in {'127.0.0.1', 'localhost'} or host.port != self.server.server_port:
                return False
            origin = self.headers.get('Origin')
            if origin:
                parsed = urlsplit(origin)
                if parsed.scheme != 'http' or parsed.hostname != host.hostname or parsed.port != host.port:
                    return False
            return True
        except ValueError:
            return False

    def read_json(self):
        if self.headers.get('Content-Type', '').split(';')[0].strip() != 'application/json':
            raise ValueError('Envie application/json.')
        length = int(self.headers.get('Content-Length', '0'))
        if not 0 < length <= 8192:
            raise ValueError('Corpo da requisição ausente ou grande demais.')
        value = json.loads(self.rfile.read(length).decode('utf-8'))
        if not isinstance(value, dict):
            raise ValueError('Envie um objeto JSON.')
        return value

    def handle_request(self):
        if not self.local_request():
            self.send_body(403, {'error': 'Acesso local necessário.'})
            return
        path = urlsplit(self.path).path
        try:
            if self.command == 'GET' and path == '/':
                page = Path(__file__).with_name('index.html').read_text(encoding='utf-8')
                self.send_body(200, page, 'text/html; charset=utf-8')
            elif self.command == 'GET' and path == '/api/tasks':
                self.send_body(200, self.server.store.list())
            elif self.command == 'POST' and path == '/api/tasks':
                self.send_body(201, self.server.store.add(self.read_json().get('title')))
            elif self.command in {'PATCH', 'DELETE'} and re.fullmatch(r'/api/tasks/[1-9][0-9]*', path):
                task_id = int(path.rsplit('/', 1)[1])
                if task_id > 2**63 - 1:
                    raise LookupError('Tarefa não encontrada.')
                if self.command == 'PATCH':
                    self.send_body(200, self.server.store.complete(task_id, self.read_json().get('completed')))
                else:
                    self.server.store.delete(task_id)
                    self.send_body(200, {'deleted': task_id})
            else:
                self.send_body(404, {'error': 'Rota não encontrada.'})
        except (ValueError, UnicodeDecodeError) as error:
            self.send_body(400, {'error': str(error)})
        except LookupError as error:
            self.send_body(404, {'error': str(error)})
        except (sqlite3.Error, OSError):
            self.send_body(500, {'error': 'Não foi possível acessar os dados locais.'})

    do_GET = do_POST = do_PATCH = do_DELETE = handle_request


def create_server(port=8765, data_path=None):
    store = TaskStore(data_path or Path(__file__).with_name('tasks.sqlite3'))
    server = ThreadingHTTPServer(('127.0.0.1', port), TaskHandler)
    server.store = store
    return server


def main():
    parser = argparse.ArgumentParser(description='Tarefas locais com SQLite')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--database', type=Path)
    args = parser.parse_args()
    server = create_server(args.port, args.database)
    print(f'Abra http://127.0.0.1:{server.server_port} — Ctrl+C para encerrar.', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
