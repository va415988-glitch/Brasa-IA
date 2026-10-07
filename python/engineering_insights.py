"""Análises estáticas de engenharia sobre o workspace ativo.

Nenhuma operação importa ou executa código do workspace. Cada uma é limitada
em arquivos, bytes e tempo, e toda conclusão aponta caminho e linha. Os
resultados são evidência sintática: não substituem compilador, testes nem
revisão humana.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
import time
import tomllib

SOURCE_EXTENSIONS = {'.py', '.js', '.jsx', '.mjs', '.cjs', '.ts', '.tsx', '.rs', '.go', '.java', '.kt', '.rb',
                     '.php', '.c', '.h', '.cpp', '.hpp', '.cs', '.swift', '.sh', '.sql'}
CONFIG_EXTENSIONS = {'.json', '.toml', '.yaml', '.yml', '.env', '.ini', '.cfg', '.conf', '.properties'}
IGNORED = {'node_modules', 'target', 'dist', 'build', 'vendor', 'venv', '.venv', 'env', '__pycache__', '.git',
           'coverage', '.next', '.nuxt', '.cache', 'corpus', 'datasets', 'model'}
LOCKFILES = {'package-lock.json', 'yarn.lock', 'pnpm-lock.yaml', 'bun.lockb', 'npm-shrinkwrap.json',
             'Cargo.lock', 'poetry.lock', 'uv.lock', 'Pipfile.lock', 'pdm.lock', 'go.sum'}
MAX_FILE_BYTES = 1024 * 1024
MAX_FILES = 2500
MAX_TOTAL_BYTES = 48 * 1024 * 1024
DEADLINE_SECONDS = 6.0
IDENTIFIER = re.compile(r'[A-Za-z_$][\w$]*(?:(?:\.|::)[A-Za-z_$][\w$]*)*')
TEST_NAME = re.compile(r'(?:^test_.*\.py$|_test\.(?:py|go)$|\.(?:test|spec)\.[cm]?[jt]sx?$|^test.*\.(?:sh|js)$)')


class Scan:
    """Inventário limitado de arquivos textuais do workspace."""

    def __init__(self, workspace: str, start: str = '', extensions: set[str] | None = None):
        self.root = Path(workspace).resolve(strict=True)
        requested = Path(start or '')
        if requested.is_absolute() or '..' in requested.parts or '\\' in str(start):
            raise ValueError('Caminho deve ser relativo ao workspace.')
        base = (self.root / requested).resolve(strict=True)
        base.relative_to(self.root)
        self.truncated = False
        self.files: list[Path] = []
        deadline = time.monotonic() + DEADLINE_SECONDS / 2
        stack = [base]
        total = 0
        while stack:
            path = stack.pop()
            if time.monotonic() > deadline or len(self.files) >= MAX_FILES:
                self.truncated = True
                break
            if path.is_symlink():
                continue
            if path.is_dir():
                try:
                    children = sorted(path.iterdir(), reverse=True)
                except OSError:
                    continue
                stack.extend(child for child in children
                             if child.name not in IGNORED and not (child.name.startswith('.') and child.is_dir()))
            elif path.is_file() and (extensions is None or path.suffix.lower() in extensions or path.name in extensions):
                size = path.stat().st_size
                if size > MAX_FILE_BYTES or total + size > MAX_TOTAL_BYTES:
                    self.truncated = True
                    continue
                total += size
                self.files.append(path)

    def rel(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix()

    @staticmethod
    def read(path: Path) -> str | None:
        try:
            return path.read_text(encoding='utf-8')
        except (OSError, UnicodeError):
            return None


def is_test_path(relative: str) -> bool:
    name = relative.rsplit('/', 1)[-1]
    parts = relative.split('/')[:-1]
    return bool(TEST_NAME.search(name)) or any(part in {'tests', 'test', '__tests__', 'spec'} for part in parts)


def is_comment(stripped: str, suffix: str) -> bool:
    if suffix in {'.py', '.sh', '.rb'} and stripped.startswith('#'):
        return True
    return stripped.startswith(('//', '/*', '* ', '--' if suffix == '.sql' else '//'))


def definition_pattern(suffix: str, name: str) -> re.Pattern:
    escaped = re.escape(name)
    if suffix == '.py':
        body = rf'^\s*(?:async\s+def|def|class)\s+{escaped}\b|^\s*{escaped}\s*(?::[^=]+)?=(?!=)'
    elif suffix == '.rs':
        body = rf'\b(?:fn|struct|enum|trait|type|const|static|mod|macro_rules!)\s+{escaped}\b'
    elif suffix == '.go':
        body = rf'^\s*(?:func\s+(?:\([^)]*\)\s*)?{escaped}\b|type\s+{escaped}\b|(?:var|const)\s+{escaped}\b)'
    else:
        body = (rf'\b(?:function\*?|class|interface|type|enum|struct|def|fn)\s+{escaped}\b'
                rf'|\b(?:const|let|var)\s+{escaped}\s*[:=]|^\s*(?:public|private|protected|static|async|export|\s)*{escaped}\s*\([^;]*\)\s*(?::[^={{]+)?\{{')
    return re.compile(body)


def classify_line(line: str, suffix: str, name: str, definition: re.Pattern) -> str:
    stripped = line.strip()
    if definition.search(line):
        return 'definition'
    if re.match(r'(?:import\s|from\s+\S+\s+import\s|use\s|mod\s|#include|export\s+\{|export\s+\*)', stripped) \
            or re.search(r'\brequire\s*\(', stripped):
        return 'import'
    if re.search(rf'(?<![\w$]){re.escape(name)}\s*(?:::<[^>]*>)?\s*[!(]', line):
        return 'call'
    return 'reference'


def find_references(workspace: str, symbol: str, path: str = '', limit: int = 200) -> dict:
    symbol = str(symbol or '').strip()
    if not IDENTIFIER.fullmatch(symbol) or len(symbol) > 120:
        raise ValueError('symbol deve ser um identificador (ex.: parse_config, Router.handle, crate::app).')
    name = re.split(r'\.|::', symbol)[-1]
    limit = max(1, min(int(limit or 200), 500))
    scan = Scan(workspace, path, SOURCE_EXTENSIONS)
    token = re.compile(rf'(?<![\w$]){re.escape(name)}(?![\w$])')
    started = time.monotonic()
    rows, counts, truncated = [], {'definition': 0, 'import': 0, 'call': 0, 'reference': 0}, scan.truncated
    for file in scan.files:
        if time.monotonic() - started > DEADLINE_SECONDS:
            truncated = True
            break
        source = scan.read(file)
        if source is None or name not in source:
            continue
        relative = scan.rel(file)
        suffix = file.suffix.lower()
        definition = definition_pattern(suffix, name)
        for number, line in enumerate(source.splitlines(), 1):
            stripped = line.strip()
            if not token.search(line) or is_comment(stripped, suffix):
                continue
            kind = classify_line(line, suffix, name, definition)
            counts[kind] += 1
            if len(rows) < limit:
                rows.append({'path': relative, 'line': number, 'kind': kind, 'in_test': is_test_path(relative),
                             'text': stripped[:240]})
            else:
                truncated = True
    files = sorted({row['path'] for row in rows})
    return {
        'schema': 'engineering-code-references/v1', 'symbol': symbol, 'searched_name': name,
        'files_scanned': len(scan.files), 'truncated': truncated,
        'definitions': [row for row in rows if row['kind'] == 'definition'],
        'references': [row for row in rows if row['kind'] != 'definition'],
        'counts': counts, 'files': files,
        'test_files': [item for item in files if is_test_path(item)],
        'executed_workspace_code': False,
        'limitations': ['Correspondência sintática por nome; homônimos em escopos diferentes aparecem juntos.',
                        'Chamadas dinâmicas, reflexão e macros podem não ser detectadas.'],
    }


def module_specifiers(relative: str) -> dict:
    path = Path(relative)
    stem = path.stem
    parts = list(path.with_suffix('').parts)
    if parts and parts[-1] in {'__init__', 'index', 'mod'}:
        parts = parts[:-1]
        stem = parts[-1] if parts else stem
    dotted = {'.'.join(parts[index:]) for index in range(len(parts))} if parts else set()
    return {'stem': stem, 'python_modules': sorted(item for item in dotted if item)}


def import_targets_file(line: str, suffix: str, target: dict) -> bool:
    stripped = line.strip()
    stem = target['stem']
    if not stem:
        return False
    if suffix == '.py':
        match = re.match(r'from\s+([.\w]+)\s+import\s+(.+)|import\s+([\w., ]+)', stripped)
        if not match:
            return False
        if match[1]:
            module = match[1].lstrip('.')
            imported = {item.strip().split(' as ')[0] for item in match[2].strip('() ').split(',')}
            return (module.split('.')[-1] == stem or module in target['python_modules']
                    or (stem in imported and (not module or module in target['python_modules'] or match[1].startswith('.'))))
        modules = {item.strip().split(' as ')[0] for item in match[3].split(',')}
        return any(item.split('.')[-1] == stem or item in target['python_modules'] for item in modules)
    if suffix in {'.js', '.jsx', '.mjs', '.cjs', '.ts', '.tsx'}:
        for specifier in re.findall(r'''(?:from\s+|require\s*\(\s*|import\s*\(\s*|import\s+)['"]([^'"]+)['"]''', stripped):
            if specifier.startswith('.'):
                base = specifier.rstrip('/').rsplit('/', 1)[-1]
                base = re.sub(r'\.(?:[cm]?[jt]sx?)$', '', base)
                if base == stem or (base in {'', '.', '..'} and stem == 'index'):
                    return True
        return False
    if suffix == '.rs':
        return bool(re.match(rf'(?:pub\s+)?mod\s+{re.escape(stem)}\s*;', stripped)
                    or re.search(rf'\buse\s+(?:crate|super|self)::(?:\w+::)*{re.escape(stem)}\b', stripped))
    if suffix == '.go':
        return bool(re.search(rf'"[^"]*/{re.escape(stem)}"', stripped))
    return False


def change_impact(workspace: str, path: str = '', symbol: str = '') -> dict:
    if not path and not symbol:
        raise ValueError('Informe path ou symbol para calcular o impacto.')
    scan = Scan(workspace, '', SOURCE_EXTENSIONS)
    dependents: dict[str, list[dict]] = {}
    exported: list[str] = []
    target_relative = ''
    if path:
        requested = Path(path)
        if requested.is_absolute() or '..' in requested.parts:
            raise ValueError('Caminho deve ser relativo ao workspace.')
        file = (scan.root / requested).resolve(strict=True)
        file.relative_to(scan.root)
        if not file.is_file():
            raise ValueError('path deve apontar para um arquivo.')
        target_relative = file.relative_to(scan.root).as_posix()
        target = module_specifiers(target_relative)
        for other in scan.files:
            relative = scan.rel(other)
            if relative == target_relative:
                continue
            source = scan.read(other)
            if source is None or target['stem'] not in source:
                continue
            for number, line in enumerate(source.splitlines()[:5000], 1):
                if import_targets_file(line, other.suffix.lower(), target):
                    dependents.setdefault(relative, []).append({'line': number, 'reason': 'import', 'text': line.strip()[:240]})
        source = scan.read(file) or ''
        if file.suffix == '.py':
            exported = re.findall(r'^(?:async\s+def|def|class)\s+([A-Za-z]\w*)', source, re.M)
        else:
            exported = re.findall(r'^\s*(?:export\s+(?:default\s+)?(?:async\s+)?(?:function\*?|class|const|let|interface|type|enum)|pub\s+(?:async\s+)?(?:fn|struct|enum|trait|type|const|mod))\s+([A-Za-z_]\w*)', source, re.M)
        exported = [name for name in dict.fromkeys(exported) if not name.startswith('_')][:25]
    names = list(dict.fromkeys(re.split(r'\.|::', item)[-1] for item in ([symbol] if symbol else []) + exported))[:26]
    if symbol and not IDENTIFIER.fullmatch(symbol):
        raise ValueError('symbol deve ser um identificador.')
    truncated = scan.truncated
    if names:
        # Uma única leitura por arquivo cobre todos os símbolos do alvo.
        combined = re.compile(r'(?<![\w$])(' + '|'.join(re.escape(name) for name in names) + r')(?![\w$])')
        started = time.monotonic()
        for other in scan.files:
            if time.monotonic() - started > DEADLINE_SECONDS:
                truncated = True
                break
            relative = scan.rel(other)
            if relative == target_relative:
                continue
            source = scan.read(other)
            if source is None or not combined.search(source):
                continue
            suffix = other.suffix.lower()
            for number, line in enumerate(source.splitlines()[:5000], 1):
                stripped = line.strip()
                match = combined.search(line)
                if not match or is_comment(stripped, suffix):
                    continue
                kind = classify_line(line, suffix, match[1], definition_pattern(suffix, match[1]))
                if kind != 'definition':
                    dependents.setdefault(relative, []).append({'line': number, 'reason': f'{kind}:{match[1]}',
                                                                'text': stripped[:240]})
    stem = Path(target_relative).stem if target_relative else re.split(r'\.|::', symbol)[-1].lower()
    name_matched_tests = [scan.rel(item) for item in scan.files if is_test_path(scan.rel(item))
                          and stem and stem.lower() in item.name.lower()]
    affected_tests = sorted({item for item in dependents if is_test_path(item)} | set(name_matched_tests))
    production = sorted(item for item in dependents if not is_test_path(item))
    risk = 'high' if len(production) > 5 else 'medium' if production else 'low'
    if target_relative and re.search(r'(?:^|/)(?:main|app|server|index|lib|mod)\.\w+$', target_relative) and production:
        risk = 'high'
    reasons = []
    if production:
        reasons.append(f'{len(production)} arquivo(s) de produção dependem do alvo.')
    if not affected_tests:
        reasons.append('Nenhum teste referencia o alvo; crie uma rede de segurança antes de alterar.')
    return {
        'schema': 'engineering-change-impact/v1', 'target': {'path': target_relative or None, 'symbol': symbol or None},
        'exported_symbols': exported, 'risk': risk, 'reasons': reasons,
        'dependents': [{'path': key, 'evidence': value[:5]} for key, value in sorted(dependents.items())][:200],
        'production_dependents': production, 'affected_tests': affected_tests,
        'recommended_checks': recommended_checks(workspace, target_relative),
        'files_scanned': len(scan.files), 'truncated': truncated, 'executed_workspace_code': False,
        'limitations': ['Dependências resolvidas por imports e nomes; injeção de dependência e chamadas dinâmicas não aparecem.'],
    }


def recommended_checks(workspace: str, relative: str) -> list[dict]:
    try:
        from programming_checks import discover
        profiles, _ = discover(Path(workspace).resolve())
    except Exception:
        return []
    root = Path(workspace).resolve()
    target = (root / relative) if relative else root
    rows = []
    for profile, locations in profiles.items():
        if profile.endswith('-syntax'):
            continue
        for location in locations:
            if not relative or target.is_relative_to(location):
                rows.append({'check': profile, 'location': location.relative_to(root).as_posix() or '.'})
    return rows[:12]


TEST_COUNT = re.compile(r'^\s*(?:async\s+)?def\s+test_|^\s*(?:it|test)\s*\(|^\s*#\[(?:tokio::)?test\]|^\s*func\s+Test[A-Z_]', re.M)


def discover_tests(workspace: str) -> dict:
    scan = Scan(workspace, '', SOURCE_EXTENSIONS | {'.json', '.toml', '.ini', '.cfg'})
    tests, sources, frameworks = [], [], set()
    total_cases = 0
    for file in scan.files:
        relative = scan.rel(file)
        name = file.name
        if name == 'package.json':
            try:
                package = json.loads(scan.read(file) or '{}')
            except ValueError:
                package = {}
            deps = {**(package.get('dependencies') or {}), **(package.get('devDependencies') or {})} if isinstance(package, dict) else {}
            for framework in ('jest', 'vitest', 'mocha', 'ava', 'playwright', '@playwright/test', 'cypress'):
                if framework in deps:
                    frameworks.add(framework.split('/')[-1])
            continue
        if name in {'pytest.ini', 'conftest.py', 'tox.ini'}:
            frameworks.add('pytest')
        if name == 'pyproject.toml' and '[tool.pytest' in (scan.read(file) or ''):
            frameworks.add('pytest')
        if file.suffix.lower() not in SOURCE_EXTENSIONS:
            continue
        source = scan.read(file) or ''
        in_rust_tests = file.suffix == '.rs' and '#[test]' in source
        if is_test_path(relative) or in_rust_tests:
            cases = len(TEST_COUNT.findall(source))
            total_cases += cases
            tests.append({'path': relative, 'cases': cases})
            if file.suffix == '.py':
                frameworks.add('unittest' if 'import unittest' in source else 'pytest')
            elif file.suffix == '.rs':
                frameworks.add('cargo-test')
            elif file.suffix == '.go':
                frameworks.add('go-test')
            elif 'node:test' in source:
                frameworks.add('node:test')
        if not is_test_path(relative) and file.suffix in {'.py', '.js', '.ts', '.tsx', '.jsx', '.mjs', '.rs', '.go'}:
            sources.append(relative)
    tested_stems = {re.sub(r'^test_|_test$|\.(?:test|spec)$', '', Path(item['path']).stem.lower()) for item in tests}
    ignored_stems = {'__init__', 'index', 'main', 'setup', 'conftest', 'config', 'settings', 'mod', 'lib'}
    untested = [item for item in sources if Path(item).stem.lower() not in tested_stems | ignored_stems
                and not any(Path(item).stem.lower() in stem for stem in tested_stems)]
    return {
        'schema': 'engineering-tests-discovery/v1', 'frameworks': sorted(frameworks),
        'checks': recommended_checks(workspace, ''), 'test_files': tests[:300], 'test_file_count': len(tests),
        'estimated_test_cases': total_cases, 'source_file_count': len(sources),
        'untested_candidates': untested[:60], 'truncated': scan.truncated or len(tests) > 300,
        'executed_workspace_code': False,
        'limitations': ['A cobertura é estimada por nome de arquivo; use a ferramenta de cobertura do projeto para medir linhas.'],
    }


PLACEHOLDER = re.compile(r'(?i)example|exemplo|changeme|change-me|your[_-]|sua[_-]|seu[_-]|xxx|placeholder|dummy|fake|<[^>]+>|\$\{|\{\{|process\.env|os\.environ|getenv|\*\*\*')
SECURITY_RULES: list[dict] = [
    {'id': 'secret-private-key', 'severity': 'critical', 'category': 'secret', 'languages': None, 'secret': True,
     'pattern': r'-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----',
     'message': 'Chave privada versionada no código.', 'fix': 'Remova a chave, revogue-a e carregue-a de um cofre ou variável de ambiente.'},
    {'id': 'secret-cloud-token', 'severity': 'critical', 'category': 'secret', 'languages': None, 'secret': True,
     'pattern': r'\b(?:AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{40,}|xox[baprs]-[A-Za-z0-9-]{10,}|AIza[0-9A-Za-z_-]{35}|sk-ant-[A-Za-z0-9_-]{20,}|sk-[A-Za-z0-9]{32,})\b',
     'message': 'Token de serviço com formato reconhecido.', 'fix': 'Revogue o token e leia-o do ambiente; adicione o arquivo ao .gitignore se for local.'},
    {'id': 'secret-hardcoded-credential', 'severity': 'high', 'category': 'secret', 'languages': None, 'secret': True,
     'pattern': r'''(?i)\b(?:api[_-]?key|secret(?:[_-]?key)?|access[_-]?token|auth[_-]?token|password|passwd|senha)\b["']?\s*[:=]\s*["']([^"'\s]{8,})["']''',
     'message': 'Credencial literal atribuída no código.', 'fix': 'Leia a credencial de variável de ambiente ou cofre de segredos.'},
    {'id': 'py-eval-exec', 'severity': 'high', 'category': 'injection', 'languages': {'.py'},
     'pattern': r'(?<![\w.])(?:eval|exec)\s*\((?!\s*["\'])',
     'message': 'eval/exec com valor não literal permite execução arbitrária.', 'fix': 'Use ast.literal_eval, um parser dedicado ou um mapa explícito de operações.'},
    {'id': 'py-shell-true', 'severity': 'high', 'category': 'injection', 'languages': {'.py'},
     'pattern': r'\bsubprocess\.\w+\([^)]*shell\s*=\s*True|\bos\.(?:system|popen)\s*\(',
     'message': 'Comando executado por shell; entrada externa pode injetar comandos.', 'fix': 'Passe uma lista de argumentos sem shell=True e valide as entradas.'},
    {'id': 'py-unsafe-deserialization', 'severity': 'high', 'category': 'deserialization', 'languages': {'.py'},
     'pattern': r'\bpickle\.loads?\s*\(|\bmarshal\.loads?\s*\(|\byaml\.load\s*\((?![^)]*Loader\s*=\s*(?:yaml\.)?SafeLoader)|\btorch\.load\s*\((?![^)]*weights_only\s*=\s*True)',
     'message': 'Desserialização que pode executar código ao carregar dados não confiáveis.', 'fix': 'Use json/yaml.safe_load, safetensors ou torch.load(weights_only=True).'},
    {'id': 'sql-string-building', 'severity': 'high', 'category': 'injection', 'languages': {'.py', '.js', '.ts', '.tsx', '.jsx', '.mjs', '.cjs', '.go', '.java', '.php', '.rb'},
     'pattern': r'''(?i)\.(?:execute|executemany|query|raw|exec)\s*\(\s*(?:f["']|["'][^"']*\b(?:select|insert|update|delete)\b[^"']*["']\s*(?:%|\+|\.format)|`[^`]*\b(?:select|insert|update|delete)\b[^`]*\$\{)''',
     'message': 'SQL montado por interpolação de strings.', 'fix': 'Use parâmetros vinculados (placeholders) do driver.'},
    {'id': 'tls-verification-disabled', 'severity': 'high', 'category': 'tls', 'languages': None,
     'pattern': r'verify\s*=\s*False|rejectUnauthorized\s*:\s*false|NODE_TLS_REJECT_UNAUTHORIZED\s*=\s*["\']?0|danger_accept_invalid_certs\s*\(\s*true|InsecureSkipVerify\s*:\s*true|CERT_NONE',  # brasa-security: ignore
     'message': 'Verificação de certificado TLS desativada.', 'fix': 'Mantenha a verificação ativa e configure a CA correta.'},
    {'id': 'js-eval', 'severity': 'high', 'category': 'injection', 'languages': {'.js', '.ts', '.tsx', '.jsx', '.mjs', '.cjs'},
     'pattern': r'(?<![\w.])eval\s*\(|\bnew\s+Function\s*\(',
     'message': 'eval/new Function executa texto como código.', 'fix': 'Substitua por JSON.parse ou um mapa explícito de operações.'},
    {'id': 'js-child-process-interpolation', 'severity': 'high', 'category': 'injection', 'languages': {'.js', '.ts', '.mjs', '.cjs'},
     'pattern': r'\b(?:exec|execSync)\s*\(\s*(?:`[^`]*\$\{|["\'][^"\']*["\']\s*\+)',
     'message': 'Comando de shell montado com dados interpolados.', 'fix': 'Use execFile/spawn com lista de argumentos e valide as entradas.'},
    {'id': 'xss-raw-html', 'severity': 'medium', 'category': 'xss', 'languages': {'.js', '.ts', '.tsx', '.jsx', '.mjs', '.html', '.vue', '.svelte'},
     'pattern': r'dangerouslySetInnerHTML|document\.write\s*\(|v-html\s*=|\{@html\s',
     'message': 'HTML inserido sem escape pode executar scripts injetados.', 'fix': 'Use textContent ou sanitize o HTML com uma biblioteca mantida.'},
    {'id': 'xss-inner-html', 'severity': 'low', 'category': 'xss', 'languages': {'.js', '.ts', '.tsx', '.jsx', '.mjs', '.html'},
     'pattern': r'\.(?:innerHTML|outerHTML)\s*\+?=(?!\s*["\'`]\s*["\'`]?\s*;?\s*$)|insertAdjacentHTML\s*\(',
     'message': 'Atribuição de HTML: confirme que todo dado dinâmico é escapado antes de entrar na marcação.', 'fix': 'Prefira textContent/createElement ou um helper de escape único.'},
    {'id': 'cors-wildcard', 'severity': 'medium', 'category': 'config', 'languages': None,
     'pattern': r'''Access-Control-Allow-Origin["']?\s*[:,]\s*["']\*|origin\s*:\s*["']\*["']|allow_origins\s*=\s*\[\s*["']\*''',
     'message': 'CORS aberto para qualquer origem.', 'fix': 'Restrinja as origens permitidas às do produto.'},
    {'id': 'debug-mode-enabled', 'severity': 'medium', 'category': 'config', 'languages': {'.py'},
     'pattern': r'\.run\([^)]*debug\s*=\s*True|^\s*DEBUG\s*=\s*True',
     'message': 'Modo debug ativo expõe detalhes internos e, no Flask, um console remoto.', 'fix': 'Leia o modo debug do ambiente e desative-o em produção.'},
    {'id': 'weak-hash', 'severity': 'low', 'category': 'crypto', 'languages': {'.py', '.js', '.ts', '.go', '.java', '.rs'},
     'pattern': r'''\bhashlib\.(?:md5|sha1)\s*\(|createHash\s*\(\s*["'](?:md5|sha1)["']|\bmd5::|\bSha1::new''',
     'message': 'MD5/SHA-1 não são adequados para senhas nem assinaturas.', 'fix': 'Use SHA-256 para integridade e argon2/bcrypt/scrypt para senhas.'},
    {'id': 'insecure-permissions', 'severity': 'medium', 'category': 'config', 'languages': None,
     'pattern': r'chmod\s+(?:-R\s+)?777|0o?777\b',
     'message': 'Permissão de escrita para todos os usuários.', 'fix': 'Use a permissão mínima necessária (ex.: 0o755 ou 0o600).'},
    {'id': 'insecure-temp-file', 'severity': 'low', 'category': 'config', 'languages': {'.py'},
     'pattern': r'\btempfile\.mktemp\s*\(',
     'message': 'tempfile.mktemp tem condição de corrida.', 'fix': 'Use tempfile.NamedTemporaryFile ou mkstemp.'},
]
SEVERITY_ORDER = ['critical', 'high', 'medium', 'low', 'info']
# Marcador explícito por linha para falsos positivos revisados por uma pessoa.
SUPPRESSION = 'brasa-security: ignore'


def redact(text: str) -> str:
    def hide(match: re.Match) -> str:
        value = match.group(0)
        return value[:4] + '…' + f'[{len(value)} caracteres ocultos]'
    return re.sub(r'[A-Za-z0-9_\-+/=]{16,}', hide, text)


def security_scan(workspace: str, path: str = '', min_severity: str = 'low') -> dict:
    if min_severity not in SEVERITY_ORDER:
        raise ValueError('min_severity deve ser critical, high, medium, low ou info.')
    threshold = SEVERITY_ORDER.index(min_severity)
    scan = Scan(workspace, path, SOURCE_EXTENSIONS | CONFIG_EXTENSIONS | {'.html', '.vue', '.svelte'})
    compiled = [(rule, re.compile(rule['pattern'], re.M)) for rule in SECURITY_RULES]
    findings, started, truncated = [], time.monotonic(), scan.truncated
    for file in scan.files:
        if time.monotonic() - started > DEADLINE_SECONDS:
            truncated = True
            break
        if file.name in LOCKFILES or file.name.endswith('.min.js'):
            continue
        source = scan.read(file)
        if source is None:
            continue
        relative = scan.rel(file)
        in_test = is_test_path(relative)
        suffix = file.suffix.lower()
        lines = source.splitlines()
        for rule, pattern in compiled:
            if rule['languages'] is not None and suffix not in rule['languages']:
                continue
            for match in pattern.finditer(source):
                number = source.count('\n', 0, match.start()) + 1
                line = lines[number - 1] if number - 1 < len(lines) else ''
                if rule.get('secret') and PLACEHOLDER.search(line):
                    continue
                if SUPPRESSION in line:
                    continue
                if line.lstrip().startswith(('#', '//')) and not rule.get('secret'):
                    continue
                severity = rule['severity']
                if in_test and severity in {'high', 'medium'}:
                    severity = SEVERITY_ORDER[SEVERITY_ORDER.index(severity) + 1]
                if SEVERITY_ORDER.index(severity) > threshold:
                    continue
                excerpt = line.strip()[:200]
                findings.append({'rule_id': rule['id'], 'severity': severity, 'category': rule['category'],
                                 'path': relative, 'line': number, 'in_test': in_test,
                                 'excerpt': redact(excerpt) if rule.get('secret') else excerpt,
                                 'message': rule['message'], 'recommendation': rule['fix']})
                if len(findings) >= 400:
                    truncated = True
                    break
    findings.sort(key=lambda row: (SEVERITY_ORDER.index(row['severity']), row['path'], row['line']))
    summary = {level: sum(row['severity'] == level for row in findings) for level in SEVERITY_ORDER}
    return {
        'schema': 'security-code-review/v1', 'files_scanned': len(scan.files), 'truncated': truncated,
        'summary': summary, 'findings': findings, 'rules_applied': len(SECURITY_RULES),
        'status': 'findings' if findings else 'clean', 'executed_workspace_code': False,
        'limitations': ['Regras estáticas por padrão; não substituem análise de fluxo de dados, SAST dedicado nem revisão humana.',
                        'Segredos são mascarados no resultado; trate qualquer achado de segredo como comprometido.'],
    }


def classify_npm_spec(spec: str) -> str:
    value = str(spec).strip()
    if value in {'', '*', 'latest', 'x', 'next'}:
        return 'unpinned'
    if re.match(r'(?:git\+|git:|github:|https?:|file:|link:|workspace:)', value) or '/' in value and not value.startswith('@'):
        return 'non-registry'
    if re.fullmatch(r'v?\d+\.\d+\.\d+(?:[-+][\w.]+)?', value):
        return 'pinned'
    return 'range'


def classify_pep508(requirement: str) -> tuple[str, str, str]:
    text = requirement.split(';', 1)[0].strip()
    match = re.match(r'([A-Za-z0-9][A-Za-z0-9._-]*)(\[[^\]]*\])?\s*(.*)', text)
    if not match:
        return text, '', 'unparsed'
    name, spec = match[1], match[3].strip()
    if spec.startswith('@') or re.match(r'(?:git\+|https?:|file:)', spec):
        return name, spec, 'non-registry'
    if not spec:
        return name, spec, 'unpinned'
    if re.fullmatch(r'===?\s*[\w.+!-]+', spec):
        return name, spec, 'pinned'
    return name, spec, 'range'


def parse_manifest(file: Path, relative: str) -> dict | None:
    name = file.name
    try:
        text = file.read_text(encoding='utf-8')
    except (OSError, UnicodeError):
        return None
    deps: list[dict] = []
    ecosystem = ''
    if name == 'package.json':
        ecosystem = 'npm'
        try:
            payload = json.loads(text)
        except ValueError:
            return {'path': relative, 'ecosystem': ecosystem, 'error': 'JSON inválido', 'dependencies': []}
        for section in ('dependencies', 'devDependencies', 'peerDependencies', 'optionalDependencies'):
            for dep, spec in (payload.get(section) or {}).items() if isinstance(payload, dict) else []:
                deps.append({'name': dep, 'spec': str(spec), 'section': section, 'status': classify_npm_spec(spec)})
    elif re.fullmatch(r'requirements[\w.-]*\.txt', name):
        ecosystem = 'pypi'
        for raw in text.splitlines():
            line = raw.split(' #', 1)[0].strip()
            if not line or line.startswith('#') or line.startswith(('-r', '--', '-c')):
                continue
            if line.startswith('-e') or re.match(r'(?:git\+|https?:)', line):
                deps.append({'name': line[:120], 'spec': line[:200], 'section': 'requirements', 'status': 'non-registry'})
                continue
            dep, spec, status = classify_pep508(line)
            deps.append({'name': dep, 'spec': spec, 'section': 'requirements', 'status': status})
    elif name == 'pyproject.toml':
        ecosystem = 'pypi'
        try:
            payload = tomllib.loads(text)
        except tomllib.TOMLDecodeError:
            return {'path': relative, 'ecosystem': ecosystem, 'error': 'TOML inválido', 'dependencies': []}
        project = payload.get('project') or {}
        for requirement in project.get('dependencies') or []:
            dep, spec, status = classify_pep508(str(requirement))
            deps.append({'name': dep, 'spec': spec, 'section': 'project.dependencies', 'status': status})
        for group, items in (project.get('optional-dependencies') or {}).items():
            for requirement in items or []:
                dep, spec, status = classify_pep508(str(requirement))
                deps.append({'name': dep, 'spec': spec, 'section': f'optional.{group}', 'status': status})
        poetry = ((payload.get('tool') or {}).get('poetry') or {}).get('dependencies') or {}
        for dep, spec in poetry.items():
            if dep.lower() == 'python':
                continue
            if isinstance(spec, dict):
                status = 'non-registry' if {'git', 'path', 'url'} & set(spec) else classify_npm_spec(str(spec.get('version', '')))
                spec = json.dumps(spec)[:200]
            else:
                status = classify_npm_spec(str(spec))
            deps.append({'name': dep, 'spec': str(spec), 'section': 'tool.poetry.dependencies', 'status': status})
    elif name == 'Cargo.toml':
        ecosystem = 'crates'
        try:
            payload = tomllib.loads(text)
        except tomllib.TOMLDecodeError:
            return {'path': relative, 'ecosystem': ecosystem, 'error': 'TOML inválido', 'dependencies': []}
        for section in ('dependencies', 'dev-dependencies', 'build-dependencies'):
            for dep, spec in (payload.get(section) or {}).items():
                if isinstance(spec, dict):
                    if {'git', 'path'} & set(spec):
                        status = 'non-registry'
                    elif spec.get('workspace') is True:
                        status = 'workspace'
                    else:
                        status = 'unpinned' if str(spec.get('version', '')).strip() in {'', '*'} else 'range'
                    shown = json.dumps(spec, ensure_ascii=False)[:200]
                else:
                    status = 'unpinned' if str(spec).strip() in {'', '*'} else 'pinned' if str(spec).startswith('=') else 'range'
                    shown = str(spec)
                deps.append({'name': dep, 'spec': shown, 'section': section, 'status': status})
    elif name == 'go.mod':
        ecosystem = 'go'
        for match in re.finditer(r'^\s*(?:require\s+)?([\w.\-]+(?:/[\w.\-~]+)+)\s+(v[\w.\-+]+)', text, re.M):
            deps.append({'name': match[1], 'spec': match[2], 'section': 'require', 'status': 'pinned'})
    else:
        return None
    return {'path': relative, 'ecosystem': ecosystem, 'dependencies': deps}


def dependency_audit(workspace: str) -> dict:
    scan = Scan(workspace, '', {'package.json', 'pyproject.toml', 'Cargo.toml', 'go.mod', '.txt'} | LOCKFILES)
    present = {scan.rel(file) for file in scan.files}
    manifests, findings = [], []
    lock_for = {'npm': ('package-lock.json', 'yarn.lock', 'pnpm-lock.yaml', 'bun.lockb', 'npm-shrinkwrap.json'),
                'crates': ('Cargo.lock',), 'pypi': ('poetry.lock', 'uv.lock', 'Pipfile.lock', 'pdm.lock'), 'go': ('go.sum',)}
    for file in scan.files:
        relative = scan.rel(file)
        if file.suffix == '.txt' and not re.fullmatch(r'requirements[\w.-]*\.txt', file.name):
            continue
        manifest = parse_manifest(file, relative)
        if manifest is None:
            continue
        directory = relative.rsplit('/', 1)[0] + '/' if '/' in relative else ''
        ecosystem = manifest['ecosystem']
        # Cargo e npm podem ter o lockfile na raiz de um workspace.
        lockfile = next((directory + lock for lock in lock_for.get(ecosystem, ()) if directory + lock in present), None) \
            or next((lock for lock in lock_for.get(ecosystem, ()) if lock in present), None)
        manifest['lockfile'] = lockfile
        deps = manifest['dependencies']
        if ecosystem == 'pypi' and file.name.startswith('requirements') and deps and all(dep['status'] == 'pinned' for dep in deps):
            manifest['lockfile'] = manifest['lockfile'] or 'pinned-requirements'
        manifests.append(manifest)
        if manifest.get('error'):
            findings.append({'severity': 'high', 'path': relative, 'kind': 'invalid-manifest', 'message': manifest['error']})
            continue
        for dep in deps:
            if dep['status'] == 'unpinned':
                findings.append({'severity': 'high', 'path': relative, 'kind': 'unpinned', 'dependency': dep['name'],
                                 'message': f"{dep['name']} não fixa versão ({dep['spec'] or 'vazio'}); builds podem mudar sem aviso."})
            elif dep['status'] == 'non-registry':
                findings.append({'severity': 'medium', 'path': relative, 'kind': 'non-registry', 'dependency': dep['name'],
                                 'message': f"{dep['name']} vem de git, URL ou caminho local; confira origem e revisão fixada."})
        if deps and not manifest['lockfile'] and ecosystem in lock_for:
            findings.append({'severity': 'medium', 'path': relative, 'kind': 'missing-lockfile',
                             'message': 'Manifesto sem lockfile: instalações diferentes podem resolver versões diferentes.'})
    by_name: dict[tuple[str, str], set[str]] = {}
    for manifest in manifests:
        for dep in manifest['dependencies']:
            by_name.setdefault((manifest['ecosystem'], dep['name'].lower()), set()).add(dep['spec'])
    for (ecosystem, name), specs in sorted(by_name.items()):
        if len(specs) > 1:
            findings.append({'severity': 'low', 'path': None, 'kind': 'version-drift', 'dependency': name,
                             'message': f'{name} ({ecosystem}) aparece com versões diferentes: ' + ', '.join(sorted(specs))[:300]})
    order = {'high': 0, 'medium': 1, 'low': 2}
    findings.sort(key=lambda row: (order.get(row['severity'], 3), str(row.get('path')), str(row.get('dependency', ''))))
    statuses = [dep['status'] for manifest in manifests for dep in manifest['dependencies']]
    return {
        'schema': 'engineering-dependency-audit/v1', 'manifests': manifests,
        'summary': {'manifests': len(manifests), 'dependencies': len(statuses),
                    **{status: statuses.count(status) for status in ('pinned', 'range', 'unpinned', 'non-registry', 'workspace')}},
        'findings': findings[:300], 'truncated': scan.truncated or len(findings) > 300, 'network_used': False,
        'limitations': ['Auditoria offline: não consulta vulnerabilidades conhecidas nem versões mais novas.',
                        'Para versões atuais, use research_web com sources=["package-registry"].'],
    }


def run(workspace: str, operation: str, arguments: dict) -> dict:
    if not isinstance(arguments, dict):
        raise ValueError('arguments deve ser um objeto JSON.')
    allowed = {'code_references': {'symbol', 'path', 'limit'}, 'change_impact': {'path', 'symbol'},
               'discover_tests': set(), 'security_scan': {'path', 'min_severity'}, 'dependency_audit': set()}
    if operation not in allowed:
        raise ValueError('Operação de engenharia desconhecida.')
    unknown = sorted(set(arguments) - allowed[operation])
    if unknown:
        raise ValueError('Argumentos não aceitos: ' + ', '.join(unknown))
    for key in ('symbol', 'path', 'min_severity'):
        if key in arguments and not isinstance(arguments[key], str):
            raise ValueError(f'{key} deve ser texto.')
    if operation == 'code_references':
        limit = arguments.get('limit', 200)
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise ValueError('limit deve ser inteiro.')
        return find_references(workspace, arguments.get('symbol', ''), arguments.get('path', ''), limit)
    if operation == 'change_impact':
        return change_impact(workspace, arguments.get('path', ''), arguments.get('symbol', ''))
    if operation == 'discover_tests':
        return discover_tests(workspace)
    if operation == 'security_scan':
        return security_scan(workspace, arguments.get('path', ''), arguments.get('min_severity', 'low'))
    return dependency_audit(workspace)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--workspace', required=True)
    parser.add_argument('--operation', required=True)
    parser.add_argument('--arguments', default='{}')
    options = parser.parse_args()
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        payload = json.loads(options.arguments)
        print(json.dumps(run(options.workspace, options.operation, payload), ensure_ascii=False))
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(str(error)[:1000], file=sys.stderr)
        sys.exit(1)
