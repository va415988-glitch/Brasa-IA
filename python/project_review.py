"""Análise estática de material fornecido: observações sempre ligadas a evidências.

Não executa código e não constitui geração livre nem auditoria completa.
"""
import ast
import json
import re
from pathlib import PurePosixPath


def review_attachments(question, attachments):
    files = [file for item in attachments for file in item.get('files', [])
             if isinstance(file.get('content'), str)]
    omitted = sum(item.get('omitted', 0) if isinstance(item.get('omitted', 0), int) else 0 for item in attachments)
    if not files:
        names = ', '.join(item.get('name', 'anexo') for item in attachments)
        return {'text': f'Recebi {names}, mas nenhum conteúdo de texto legível chegou nesta mensagem. '
                'O compositor aceita texto/código. Para PDF e formatos Office, deixe o documento no workspace ativo e peça a leitura pelo nome; imagens precisam de OCR local. '
                'Para analisar um projeto anexado, inclua arquivos de código ou documentos de texto.',
                'findings': [], 'coverage': {'files_read': 0, 'omitted': omitted}}

    findings = []
    def add(file, line, message, category='structure'):
        findings.append({'path': file['path'], 'line': line, 'message': message, 'category': category})
    manifests, languages, routes = [], set(), []
    for file in files:
        path, content = file['path'], file['content']
        name = PurePosixPath(path).name.lower()
        ext = PurePosixPath(path).suffix.lower()
        language = {'.js': 'JavaScript', '.ts': 'TypeScript', '.tsx': 'TypeScript', '.jsx': 'JavaScript',
                    '.py': 'Python', '.rs': 'Rust', '.sql': 'SQL', '.html': 'HTML', '.css': 'CSS'}.get(ext)
        if language:
            languages.add(language)
        if name == 'package.json':
            try:
                package = json.loads(content)
                if not isinstance(package, dict):
                    raise ValueError('manifesto precisa ser objeto')
                manifests.append(path)
                production = package.get('dependencies', {})
                development = package.get('devDependencies', {})
                scripts = package.get('scripts', {})
                if not all(isinstance(value, dict) for value in (production, development, scripts)):
                    raise ValueError('dependencies, devDependencies e scripts precisam ser objetos')
                if not all(isinstance(value, str) for value in scripts.values()):
                    raise ValueError('comandos precisam ser strings')
                deps = {**production, **development}
                known = [d for d in ['express', 'fastify', 'react', 'vue', 'next', 'sqlite3', 'better-sqlite3', 'pg', 'mysql2', 'prisma'] if d in deps]
                if known:
                    add(file, 1, 'Dependências declaradas: ' + ', '.join(known) + '.')
                if scripts:
                    add(file, 1, 'Comandos disponíveis: ' + ', '.join(f'npm run {key}' for key in list(scripts)[:8]) + '.')
                test = scripts.get('test', '')
                if not test or 'no test specified' in test:
                    add(file, 1, 'O manifesto não define um comando de teste funcional. Vale adicioná-lo antes de automatizar alterações.', 'testing')
            except (ValueError, TypeError) as error:
                add(file, getattr(error, 'lineno', 1), 'Não consegui interpretar este package.json como manifesto JSON válido.', 'validation')
        if ext == '.py':
            try:
                tree = ast.parse(content)
                count = sum(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) for node in ast.walk(tree))
                if count:
                    add(file, 1, f'Encontrei {count} funções neste módulo Python.')
            except SyntaxError as error:
                add(file, error.lineno or 1, 'O analisador Python encontrou um erro de sintaxe neste trecho.', 'validation')
        if ext in {'.js', '.ts'}:
            for number, line in enumerate(content.splitlines(), 1):
                match = re.match(r'^\s*(?:app|router)\.(get|post|put|patch|delete)\s*\(\s*[\"\x27]([^\"\x27]+)', line)
                if match:
                    routes.append((file, number, match.group(1).upper(), match.group(2)))
        lines = content.count('\n') + 1
        if lines > 500:
            add(file, 1, f'Este arquivo reúne {lines} linhas. É um candidato a separar por responsabilidade, após verificar suas dependências.', 'maintainability')
    for file, number, method, path in routes[:6]:
        add(file, number, f'Declaração compatível com rota HTTP: {method} {path}.', 'routes')
    tests = [f for f in files if re.search(r'(^|/)(tests?|__tests__)/|(^|/)(test_).*|\.(test|spec)\.', f['path'])]
    docs = [f for f in files if PurePosixPath(f['path']).name.lower().startswith('readme')]
    if tests:
        add(tests[0], 1, f'Há {len(tests)} arquivo(s) de teste no material recebido; eles ainda não foram executados.', 'testing')
    if docs:
        number, heading = next(((i, l.lstrip('# ').strip()) for i, l in enumerate(docs[0]['content'].splitlines(), 1) if l.strip()), (1, ''))
        add(docs[0], number, f'A documentação apresenta o projeto como: {heading[:160]}.', 'documentation')

    focus = question.lower()
    if re.search(r'rotas?|endpoints?', focus):
        findings = [f for f in findings if f['category'] == 'routes']
    elif re.search(r'test|qualidade', focus):
        findings = [f for f in findings if f['category'] in {'testing', 'validation'}]
    elif re.search(r'problema|erro|risco', focus):
        findings = [f for f in findings if f['category'] in {'validation', 'maintainability', 'testing'}]
    else:
        findings.sort(key=lambda f: f['category'] not in {'structure', 'documentation'})
    chosen = findings[:10]
    stack = ', '.join(sorted(languages)) or 'documentos de texto'
    name = attachments[0].get('name', 'projeto')
    intro = f'Li {len(files)} arquivo(s) de {name}. O material inclui {stack}.'
    if manifests and routes:
        intro += f' Há um projeto Node.js e encontrei {len(routes)} declaração(ões) de rotas HTTP; isso aponta para uma aplicação com servidor web.'
    lines = [intro, 'Estas são as observações que consegui sustentar no conteúdo:']
    if chosen:
        lines.extend(f"• {f['message']} [{f['path']}:{f['line']}]" for f in chosen)
    else:
        for file in files[:3]:
            excerpt = next((line.strip() for line in file['content'].splitlines() if line.strip()), '')[:180]
            lines.append(f"• {file['path']}: {excerpt}")
    problems = [f for f in chosen if f['category'] in {'validation', 'testing', 'maintainability'}]
    if problems:
        lines.append('Eu começaria conferindo os pontos acima em uma cópia do projeto e executando os testes existentes.')
    lines.append(f'Esta é uma leitura estática dos arquivos recebidos. Não executei o sistema.' +
                 (f' {omitted} arquivo(s) ficaram fora da leitura por formato, tamanho ou filtros de pasta.' if omitted else '') +
                 ' A leitura não permite afirmar que o projeto inteiro está correto ou seguro.')
    return {'text': '\n\n'.join(lines), 'findings': chosen,
            'coverage': {'files_read': len(files), 'omitted': omitted,
                         'paths': [file['path'] for file in files]}}
