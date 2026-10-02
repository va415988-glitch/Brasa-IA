import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from app import create_server


class TaskAppTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.database = Path(self.directory.name) / 'tasks.sqlite3'
        self.start_server()

    def start_server(self):
        self.server = create_server(port=0, data_path=self.database)
        self.thread = threading.Thread(target=lambda: self.server.serve_forever(poll_interval=0.01), daemon=True)
        self.thread.start()
        self.base = f'http://127.0.0.1:{self.server.server_port}'

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def tearDown(self):
        self.stop_server()
        self.directory.cleanup()

    def request(self, path='/api/tasks', method='GET', body=None):
        raw = json.dumps(body).encode() if body is not None else None
        request = Request(self.base + path, data=raw, method=method, headers={'Content-Type': 'application/json'})
        try:
            response = urlopen(request, timeout=2)
        except HTTPError as error:
            response = error
        with response:
            return response.status, json.loads(response.read())

    def test_frontend_is_served(self):
        with urlopen(self.base + '/', timeout=2) as response:
            self.assertEqual(response.status, 200)
            self.assertIn('text/html', response.headers['Content-Type'])
            page = response.read().decode()
        self.assertIn('id="task-form"', page)
        self.assertIn('/api/tasks', page)

    def test_crud_and_persistence_after_server_restart(self):
        self.assertEqual(self.request(), (200, []))
        status, task = self.request(method='POST', body={'title': '  Escrever testes  '})
        self.assertEqual(status, 201)
        self.assertEqual(task['title'], 'Escrever testes')
        self.assertFalse(task['completed'])
        route = '/api/tasks/' + str(task['id'])
        self.assertEqual(self.request(route, 'PATCH', {'completed': True})[1]['completed'], True)
        self.stop_server()
        self.start_server()
        self.assertEqual(self.request()[1], [{**task, 'completed': True}])
        self.assertEqual(self.database.read_bytes()[:16], b'SQLite format 3\x00')
        self.assertEqual(self.request(route, 'PATCH', {'completed': False})[1]['completed'], False)
        self.assertEqual(self.request(route, 'DELETE')[0], 200)
        self.stop_server()
        self.start_server()
        self.assertEqual(self.request()[1], [])

    def test_rejects_invalid_titles_without_creating_tasks(self):
        for title in ('', '   ', None, 5, [], 'x' * 501):
            with self.subTest(title=title):
                status, error = self.request(method='POST', body={'title': title})
                self.assertEqual(status, 400)
                self.assertIn('error', error)
        self.assertEqual(self.request()[1], [])

    def test_missing_tasks_and_invalid_completion(self):
        self.assertEqual(self.request('/api/tasks/999', 'PATCH', {'completed': True})[0], 404)
        self.assertEqual(self.request('/api/tasks/999', 'DELETE')[0], 404)
        _, task = self.request(method='POST', body={'title': "SQLite ' título"})
        self.assertEqual(self.request('/api/tasks/' + str(task['id']), 'PATCH', {'completed': 'yes'})[0], 400)
        self.assertEqual(self.request()[1], [task])


if __name__ == '__main__':
    unittest.main()
