"""Bounded, offline scaffold for a personal SQLite task web application."""

from pathlib import Path
import re
import unicodedata


def sqlite_task_plan(question, existing_paths=None):
    text = ''.join(char for char in unicodedata.normalize('NFKD', question.casefold())
                   if not unicodedata.combining(char))
    required = (r'\b(?:tarefas?|afazeres|to[ -]?do)\b',
                r'\b(?:crie|criar|construa|construir|implemente|implementar|desenvolva)\b',
                r'\b(?:web|navegador)\b', r'\bsqlite\b')
    if not all(re.search(pattern, text) for pattern in required):
        return None
    # Do not silently substitute another stack or omit explicitly requested features.
    positive = re.sub(r'\bsem\s+(?:login|autenticacao)(?:\s+nem\s+(?:login|autenticacao))?', '', text)
    unsupported = (r'\b(?:node|typescript|react|vue|angular|next|express|fastapi|flask|django|rust|java|go|'
                   r'postgres|mysql|login|autenticacao|multiusuario|sincronizacao|notificacoes|'
                   r'categorias|subtarefas|prioridades|anexos|upload|prazo|prazos|editar|edicao)\b')
    if re.search(unsupported, positive):
        return None
    known = {str(path).replace('\\', '/').strip('/') for path in (existing_paths or set())}
    allowed = {'readme', 'readme.md', 'readme.txt', 'readme.rst', '.gitignore',
               'license', 'license.md', 'license.txt'}
    if any(path.casefold() not in allowed for path in known):
        return None
    templates = Path(__file__).with_name('templates') / 'sqlite_tasks'
    paths = ['app.py', 'index.html', 'tests/test_app.py', 'README.md']
    operations = []
    for path in paths:
        target = 'TASKS.md' if path == 'README.md' and 'readme.md' in {p.casefold() for p in known} else path
        operations.append({'tool': 'create_file', 'arguments': {
            'path': target, 'content': (templates / path).read_text(encoding='utf-8'),
        }})
    return {'assumptions': [
        'Workspace sem código existente: Python 3 com http.server e sqlite3 da biblioteca padrão, sem dependências ou serviços externos.',
        'app.py fornece a API e persiste em tasks.sqlite3; index.html permite cadastrar, listar, concluir, reabrir e excluir tarefas.',
        'tests/test_app.py verifica HTTP, títulos inválidos, operações e persistência após reiniciar o servidor; execute python3 -m unittest discover -s tests -v.',
        'Após aprovação, criar os arquivos, executar os testes e observar o resultado. Inicie com python3 app.py e abra http://127.0.0.1:8765.',
    ], 'operations': operations}
