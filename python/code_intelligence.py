"""Bounded static code observations. Never imports or executes workspace code."""
from __future__ import annotations
import argparse
import ast
import json
from pathlib import Path
import re
import shutil
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
EXTENSIONS = {'.py', '.js', '.jsx', '.mjs', '.cjs', '.ts', '.tsx', '.rs', '.go', '.java', '.kt', '.rb', '.php', '.c', '.h', '.cpp', '.hpp', '.cs', '.swift', '.sql'}
IGNORED = {'node_modules', 'target', 'dist', 'build', 'vendor', 'venv', '__pycache__', 'corpus', 'datasets', 'model'}
MAX_FILE = 128 * 1024


def python_observations(path, source):
    result = {'symbols': [], 'imports': [], 'diagnostics': [], 'parser': 'python-ast'}
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as error:
        result['diagnostics'].append({'path': path, 'line': error.lineno, 'message': error.msg, 'severity': 'error'})
        return result
    scopes = []
    class Visitor(ast.NodeVisitor):
        def declaration(self, node, kind):
            qualified = '.'.join([*scopes, node.name])
            header = source.splitlines()[node.lineno - 1].strip()[:240]
            row = {'name': node.name, 'qualified_name': qualified, 'kind': kind,
                   'path': path, 'line': node.lineno, 'end_line': node.end_lineno, 'signature': header,
                   'docstring': (ast.get_docstring(node) or '')[:500]}
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                row['async'] = isinstance(node, ast.AsyncFunctionDef)
                row['parameters'] = [arg.arg for arg in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]]
                class Body(ast.NodeVisitor):
                    def visit_FunctionDef(self, child): pass
                    def visit_AsyncFunctionDef(self, child): pass
                    def visit_ClassDef(self, child): pass
                    def visit_Return(self, child):
                        if child.value is not None and len(returns) < 4:
                            returns.append(ast.unparse(child.value)[:240])
                        self.generic_visit(child)
                    def visit_Call(self, child):
                        name = ast.unparse(child.func)[:120]
                        if name not in calls and len(calls) < 16: calls.append(name)
                        self.generic_visit(child)
                returns, calls = [], []
                body = Body()
                for statement in node.body: body.visit(statement)
                row.update(returns=returns, calls=calls)
            result['symbols'].append(row)
            scopes.append(node.name)
            self.generic_visit(node)
            scopes.pop()
        def visit_FunctionDef(self, node): self.declaration(node, 'function')
        def visit_AsyncFunctionDef(self, node): self.declaration(node, 'function')
        def visit_ClassDef(self, node): self.declaration(node, 'class')
        def visit_Import(self, node): self.add_import(node)
        def visit_ImportFrom(self, node): self.add_import(node)
        def add_import(self, node):
            result['imports'].append({'path': path, 'line': node.lineno, 'text': ast.unparse(node)[:240]})
    Visitor().visit(tree)
    return result


def lexical_observations(path, source):
    symbols, imports = [], []
    patterns = [r'\b(?:fn|func|def|function|class|struct|enum|trait|interface|type|record)\s+(\w+)',
                r'^\s*(?:export\s+)?(?:public\s+|private\s+|static\s+|inline\s+|virtual\s+|final\s+)*(?:[\w:*<>\[\]]+\s+)+(\w+)\s*\([^;]*\)\s*(?:const\s*)?\{',
                r'^\s*CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)']
    for line, text in enumerate(source.splitlines(), 1):
        stripped = text.strip()
        if stripped.startswith(('//', '/*', '*', '# ')): continue
        for pattern in patterns:
            match = re.search(pattern, text, re.I if path.endswith('.sql') else 0)
            if match:
                symbols.append({'path': path, 'line': line, 'name': match[1], 'kind': 'declaration', 'signature': stripped[:240]})
                break
        if re.match(r'(?:use |import |from |require\(|#include)', stripped):
            imports.append({'path': path, 'line': line, 'text': stripped[:240]})
    return {'symbols': symbols, 'imports': imports, 'diagnostics': [], 'parser': 'lexical-navigation-only'}


def inspect(workspace, relative=''):
    workspace = Path(workspace).resolve(strict=True)
    requested = Path(relative)
    if requested.is_absolute() or '..' in requested.parts or '\\' in relative:
        raise ValueError('Caminho deve ser relativo ao workspace.')
    start = workspace / requested
    start.resolve(strict=True).relative_to(workspace)
    if start.is_symlink(): raise ValueError('Links não são inspecionados.')
    candidates, stack = [], [start]
    visited, truncated, deadline = 0, False, time.monotonic() + 4
    while stack:
        path = stack.pop()
        visited += 1
        if visited > 10000 or len(candidates) >= 250 or time.monotonic() > deadline:
            truncated = True; break
        if path.is_symlink(): continue
        if path.is_dir():
            stack.extend(sorted((item for item in path.iterdir() if not item.name.startswith('.') and item.name not in IGNORED), reverse=True))
        elif path.suffix.lower() in EXTENSIONS:
            if path.stat().st_size > MAX_FILE:
                truncated = True; continue
            candidates.append(path)
    result = {'schema': 'local-code-observations/v1', 'workspace': str(workspace), 'path': relative,
              'files_scanned': 0, 'symbols': [], 'imports': [], 'diagnostics': [], 'parsers': {},
              'truncated': truncated, 'executed_workspace_code': False,
              'limitations': ['Análise estática; chamadas e expressões não comprovam comportamento em execução.',
                              'Linguagens sem parser conectado usam somente navegação lexical.']}
    javascript = []
    total_bytes = 0
    def merge(path, facts):
        result['parsers'][path] = facts.get('parser', 'unknown')
        for key in ('symbols', 'imports', 'diagnostics'): result[key].extend(facts.get(key, []))
    for path in candidates:
        if total_bytes + path.stat().st_size > 8 * 1024 * 1024:
            result['truncated'] = True; break
        try: source = path.read_text(encoding='utf-8')
        except UnicodeError: result['truncated'] = True; continue
        total_bytes += path.stat().st_size
        name = path.relative_to(workspace).as_posix()
        result['files_scanned'] += 1
        if path.suffix in {'.js', '.jsx', '.mjs', '.cjs', '.ts', '.tsx'}:
            javascript.append({'path': name, 'content': source})
        else: merge(name, python_observations(name, source) if path.suffix == '.py' else lexical_observations(name, source))
    if javascript:
        try:
            node = shutil.which('node')
            if not node: raise OSError('Node indisponível')
            completed = subprocess.run([node, str(ROOT / 'agent-core/tools/code-intelligence.mjs')],
                input=json.dumps(javascript), text=True, capture_output=True, timeout=6, check=True)
            for path, facts in json.loads(completed.stdout).items(): merge(path, facts)
        except (OSError, ValueError, subprocess.SubprocessError):
            result['limitations'].append('Parser JavaScript/TypeScript indisponível; navegação lexical utilizada.')
            for item in javascript: merge(item['path'], lexical_observations(item['path'], item['content']))
    result['symbols'].sort(key=lambda row: (row['path'], row['line']))
    for key, maximum in [('symbols', 1500), ('imports', 500), ('diagnostics', 100)]:
        result['truncated'] |= len(result[key]) > maximum
        result[key] = result[key][:maximum]
    result['symbol_count'], result['import_count'] = len(result['symbols']), len(result['imports'])
    return result


def describe(data):
    """Render only source observations, keeping their paths and static scope visible."""
    rows = [f"Inspecionei {data.get('files_scanned', 0)} arquivo(s) por análise estática."]
    for symbol in data.get('symbols', [])[:40]:
        label = symbol.get('qualified_name') or symbol['name']
        details = [f"`{symbol.get('signature') or label}`"]
        if symbol.get('returns'): details.append('expressões de retorno: ' + ', '.join(f'`{value}`' for value in symbol['returns']))
        if symbol.get('calls'): details.append('chamadas observadas: ' + ', '.join(f'`{value}`' for value in symbol['calls']))
        rows.append(f"- **{label}** — `{symbol['path']}:{symbol['line']}`: " + '; '.join(details) + '.')
    if not data.get('symbols'): rows.append('Nenhuma declaração identificada nesse recorte.')
    for item in data.get('diagnostics', [])[:10]:
        rows.append(f"- Erro de sintaxe em `{item['path']}:{item.get('line', '?')}`: {item['message']}.")
    imports = data.get('imports', [])[:10]
    if imports: rows.append('Imports: ' + '; '.join(f"`{item['path']}:{item['line']}`: `{item['text']}`" for item in imports) + '.')
    if any(parser == 'lexical-navigation-only' for parser in data.get('parsers', {}).values()):
        rows.append('Alguns arquivos usam somente navegação lexical; suas declarações precisam de leitura e verificação adicional.')
    if data.get('truncated') or len(data.get('symbols', [])) > 40:
        rows.append('O recorte foi limitado. Aprofunde a inspeção em um arquivo ou pasta específico.')
    rows.append('Essas observações não executam o código nem comprovam seu comportamento.')
    return '\n'.join(rows)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--workspace', required=True)
    parser.add_argument('--path', default='')
    args = parser.parse_args()
    print(json.dumps(inspect(args.workspace, args.path), ensure_ascii=False))
