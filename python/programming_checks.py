"""Fixed local verification profiles with bounded, concurrently drained output.

Project scripts execute only through recognized profiles. No shell, arbitrary
arguments, installation, or model downloads are accepted by this module.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
NODE_TEST = re.compile(r'(?:\.test|\.spec|^test[_-]).*\.(?:[cm]?js)$')
PROFILES = ('cargo-test', 'npm-test', 'npm-check', 'npm-build', 'pytest', 'unittest',
            'node-test', 'go-test', 'python-syntax', 'node-syntax', 'cpp-syntax', 'c-syntax', 'shell-syntax')
IGNORED = {'node_modules', 'target', 'dist', 'build', 'vendor', 'venv', '__pycache__', 'corpus', 'datasets', 'model', 'data'}


def inventory(root):
    files, stack = [], [(root, 0)]
    visited = 0
    while stack and len(files) < 1000 and visited < 10000:
        directory, depth = stack.pop()
        for path in sorted(directory.iterdir()):
            visited += 1
            if path.is_symlink() or path.name.startswith('.') or path.name in IGNORED: continue
            if path.is_dir() and depth < 5: stack.append((path, depth + 1))
            elif path.is_file(): files.append(path)
    return files


def discover(root):
    files = inventory(root)
    roots = {root} | {path.parent for path in files if path.name in {'Cargo.toml', 'package.json', 'pyproject.toml', 'go.mod'}}
    profiles = {name: [] for name in PROFILES}
    for project in sorted(roots):
        own = [path for path in files if path.is_relative_to(project)
               and not any(other != project and other.is_relative_to(project) and path.is_relative_to(other) for other in roots)]
        if (project / 'Cargo.toml').is_file(): profiles['cargo-test'].append(project)
        package = {}
        try: package = json.loads((project / 'package.json').read_text())
        except (OSError, ValueError): pass
        scripts = package.get('scripts', {}) if isinstance(package, dict) else {}
        if not isinstance(scripts, dict): scripts = {}
        for script, name in [('test', 'npm-test'), ('check', 'npm-check'), ('build', 'npm-build')]:
            if isinstance(scripts, dict) and isinstance(scripts.get(script), str) and scripts[script].strip(): profiles[name].append(project)
        python_tests = [path for path in own if path.suffix == '.py' and path.name.startswith('test')]
        if python_tests: profiles['unittest'].append(project)
        if any((project / name).is_file() for name in ('pyproject.toml', 'pytest.ini', 'tox.ini', 'setup.cfg')):
            profiles['pytest'].append(project)
        if 'test' not in scripts and any(NODE_TEST.search(path.name) for path in own):
            profiles['node-test'].append(project)
        if (project / 'go.mod').is_file(): profiles['go-test'].append(project)
        for suffixes, profile in [({'.py'}, 'python-syntax'), ({'.js', '.mjs', '.cjs'}, 'node-syntax'),
                ({'.cpp', '.cc', '.cxx'}, 'cpp-syntax'), ({'.c'}, 'c-syntax'), ({'.sh'}, 'shell-syntax')]:
            if any(path.suffix in suffixes for path in own): profiles[profile].append(project)
    return {name: paths for name, paths in profiles.items() if paths}, files


class BoundedOutput:
    def __init__(self): self.head, self.tail, self.total = b'', b'', 0
    def drain(self, stream):
        with stream:
            while chunk := stream.read(4096):
                self.total += len(chunk)
                self.head += chunk[:max(0, 3000 - len(self.head))]
                self.tail = (self.tail + chunk)[-9000:]
    def text(self):
        if self.total <= 9000: data = self.tail
        elif self.total <= 12000: data = self.head + self.tail[-(self.total - 3000):]
        else: data = self.head + b'\n[output truncated; tail preserved]\n' + self.tail
        return data.decode('utf-8', errors='replace')


def run_command(command, root, timeout=45, extra_env=None, fresh_python_cache=False):
    if fresh_python_cache:
        # Timestamp/size .pyc validation can reuse the previous implementation
        # after a same-size edit within one second. Each check reads fresh source.
        with tempfile.TemporaryDirectory(prefix='ia-python-check-cache-') as cache:
            return _run_command(command, root, timeout,
                {**(extra_env or {}), 'PYTHONPYCACHEPREFIX': cache, 'PYTHONDONTWRITEBYTECODE': '1'})
    return _run_command(command, root, timeout, extra_env)


def _run_command(command, root, timeout=45, extra_env=None):
    started = time.monotonic()
    env = {**os.environ, **(extra_env or {})}
    try:
        child = subprocess.Popen(command, cwd=root, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 env=env, start_new_session=os.name == 'posix')
    except OSError as error:
        return {'executed': False, 'passed': False, 'stderr': str(error), 'stdout': '',
                'elapsed_ms': 0, 'truncated': False, 'timed_out': False, 'exit_code': None}
    stdout, stderr = BoundedOutput(), BoundedOutput()
    readers = [threading.Thread(target=buffer.drain, args=(stream,), daemon=True)
               for buffer, stream in [(stdout, child.stdout), (stderr, child.stderr)]]
    for reader in readers: reader.start()
    timed_out = False
    try: code = child.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        if os.name == 'posix':
            try: os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError: pass
        else: child.kill()
        code = child.wait()
    # Descendants that retain a pipe also belong to this bounded execution.
    for reader in readers: reader.join(timeout=1)
    if any(reader.is_alive() for reader in readers):
        timed_out = True
        if os.name == 'posix':
            try: os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError: pass
        for reader in readers: reader.join(timeout=1)
    return {'executed': True, 'passed': code == 0 and not timed_out, 'exit_code': code,
            'timed_out': timed_out, 'stdout': stdout.text(), 'stderr': stderr.text(),
            'truncated': stdout.total > 12000 or stderr.total > 12000,
            'elapsed_ms': round((time.monotonic() - started) * 1000)}


def test_count(check, output):
    patterns = {'unittest': r'Ran (\d+) tests?\b', 'node-test': r'^# tests (\d+)',
                'npm-test': r'^(?:# tests |ℹ tests )(\d+)', 'go-test': r'^(?:ok\s+|--- PASS:)'}
    if check == 'cargo-test':
        counts = re.findall(r'test result: .*? (\d+) passed;', output)
        return sum(map(int, counts)) if counts else None
    if check == 'go-test':
        if '[no test files]' in output and not re.search(r'^ok\s+', output, re.M): return 0
        counts = re.findall(r'^--- (?:PASS|FAIL):', output, re.M)
        return len(counts) if counts else None
    if check == 'pytest' and 'no tests ran' in output: return 0
    pattern = patterns.get(check)
    matches = re.findall(pattern, output, re.M) if pattern else []
    return int(matches[-1]) if matches else None


def verify(root, check='auto', changed_path='', budget_seconds=55, _project=None):
    started = time.monotonic()
    root = Path(root).resolve(strict=True)
    if check not in {'auto', 'all', 'list', *PROFILES}: raise ValueError('Verificação fora dos perfis registrados.')
    requested_path = Path(changed_path)
    if requested_path.is_absolute() or '..' in requested_path.parts or '\\' in changed_path:
        raise ValueError('Arquivo deve estar dentro do workspace.')
    (root / requested_path).resolve().relative_to(root)
    profiles, files = discover(root)
    roots = {root} | {path.parent for path in files if path.name in {'Cargo.toml', 'package.json', 'pyproject.toml', 'go.mod'}}
    target = root / changed_path if changed_path else root
    if changed_path and _project is None:
        owner = max((path for path in roots if target.is_relative_to(path)), key=lambda path: len(path.parts))
        profiles = {name: [owner] for name, projects in profiles.items() if owner in projects}
    locations = {name: [str(path.relative_to(root)) or '.' for path in paths] for name, paths in profiles.items()}
    base = {'available': list(profiles), 'locations': locations, 'workspace': str(root), 'changed_path': changed_path}
    if check == 'list': return base
    if check == 'all':
        results = [verify(root, name, changed_path, max(0, budget_seconds - (time.monotonic() - started)), project)
                   for name, projects in profiles.items() for project in projects]
        return {**base, 'check': 'all', 'executed': bool(results) and all(row['executed'] for row in results),
                'passed': bool(results) and all(row['passed'] for row in results), 'results': results,
                'elapsed_ms': round((time.monotonic() - started) * 1000), 'verification_kind': 'combined',
                'behavior_verification_executed': any(row.get('behavior_verification_executed') for row in results),
                'summary': f"{sum(row['passed'] for row in results)}/{len(results)} verificações aprovadas."}
    suffix = requested_path.suffix
    preferred = {'.py': ('pytest', 'unittest', 'python-syntax'), '.go': ('go-test',), '.rs': ('cargo-test',),
                 '.cpp': ('cpp-syntax',), '.cc': ('cpp-syntax',), '.c': ('c-syntax',), '.sh': ('shell-syntax',)}.get(suffix,
                 ('npm-test', 'node-test', 'npm-check', 'npm-build', 'node-syntax') if suffix in {'.js', '.cjs', '.mjs', '.ts', '.tsx', '.jsx'} else ())
    if check == 'auto': check = next((name for name in (*preferred, *PROFILES) if name in profiles), 'none')
    if check not in profiles:
        return {**base, 'check': check, 'executed': False, 'passed': False, 'elapsed_ms': 0,
                'summary': 'Nenhum verificador reconhecido disponível para esse projeto ou perfil.'}
    projects = profiles[check]
    project = _project or max((path for path in projects if target.is_relative_to(path)), key=lambda path: len(path.parts), default=projects[0])
    relative_project = project.relative_to(root).as_posix()
    own_files = [path for path in files if path.is_relative_to(project)
                 and not any(other != project and other.is_relative_to(project) and path.is_relative_to(other) for other in roots)]
    python = str(project / '.venv/bin/python') if (project / '.venv/bin/python').is_file() else sys.executable
    commands, extra_env = [], None
    kind = 'syntax' if check.endswith('-syntax') else 'build' if check == 'npm-build' else 'typecheck' if check == 'npm-check' else 'test'
    if check == 'cargo-test': commands = [['cargo', 'test', '--all-targets', '--offline']]
    elif check.startswith('npm-'): commands = [['npm', 'test'] if check == 'npm-test' else ['npm', 'run', check.split('-', 1)[1]]]
    elif check == 'pytest': commands = [[python, '-m', 'pytest', '-q']]
    elif check == 'unittest':
        test_dir = 'tests' if (project / 'tests').is_dir() else 'test' if (project / 'test').is_dir() else '.'
        commands = [[python, '-m', 'unittest', 'discover', '-s', test_dir, '-p', 'test*.py', '-v']]
    elif check == 'node-test':
        tests = [str(path.relative_to(project)) for path in own_files if NODE_TEST.search(path.name)]
        commands = [['node', '--test', '--test-reporter=tap', *tests]]
    elif check == 'go-test':
        commands = [['go', 'test', '-v', './...']]
        extra_env = {'GOPROXY': 'off', 'GOSUMDB': 'off', 'GOTOOLCHAIN': 'local', 'GOCACHE': '/tmp/ia-local-go-check-cache'}
    elif check == 'python-syntax':
        commands = [[python, str(ROOT / 'python/syntax_check.py'), *[str(path) for path in own_files if path.suffix == '.py']]]
    else:
        extensions, command = {'node-syntax': ({'.js', '.mjs', '.cjs'}, ['node', '--check']),
                              'cpp-syntax': ({'.cpp', '.cc', '.cxx'}, ['g++', '-fsyntax-only']),
                              'c-syntax': ({'.c'}, ['gcc', '-fsyntax-only']),
                              'shell-syntax': ({'.sh'}, ['bash', '-n'])}[check]
        commands = [[*command, str(path)] for path in own_files if path.suffix in extensions]
    observations = []
    for command in commands[:100]:
        remaining = max(0, budget_seconds - (time.monotonic() - started))
        if remaining <= 0: break
        observation = run_command(command, project, timeout=min(45, remaining), extra_env=extra_env,
                                  fresh_python_cache=check in {'pytest', 'unittest', 'python-syntax'})
        observation['command'] = ' '.join(command)
        observations.append(observation)
        if not observation['passed']: break
    result = {**base, 'check': check, 'verification_kind': kind, 'project_path': relative_project,
              'executed': bool(observations) and all(row['executed'] for row in observations),
              'passed': bool(observations) and len(observations) == len(commands) and all(row['passed'] for row in observations),
              'command': ('cd ' + relative_project + ' && ' if relative_project != '.' else '') + ' ; '.join(row['command'] for row in observations),
              'stdout': '\n'.join(row['stdout'] for row in observations)[-12000:],
              'stderr': '\n'.join(row['stderr'] for row in observations)[-12000:],
              'truncated': any(row['truncated'] for row in observations), 'timed_out': any(row.get('timed_out') for row in observations),
              'elapsed_ms': round((time.monotonic() - started) * 1000)}
    result['tests_executed'] = test_count(check, result['stdout'] + '\n' + result['stderr']) if kind == 'test' else None
    result['behavior_verification_executed'] = kind == 'test' and result['executed'] and result['tests_executed'] != 0
    if result['tests_executed'] == 0: result['passed'] = False
    result['summary'] = ('Verificação aprovada.' if result['passed'] else 'Verificação falhou ou não pôde ser executada.')
    if result['tests_executed'] == 0: result['summary'] = 'A suíte executou zero testes; isso não comprova o comportamento do código.'
    if kind == 'syntax': result['summary'] += ' Escopo: sintaxe/compilação; testes de comportamento não foram executados.'
    result['evidence'] = [result['command'], result['summary']]
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--workspace', required=True)
    parser.add_argument('--check', default='auto')
    parser.add_argument('--path', default='')
    args = parser.parse_args()
    print(json.dumps(verify(args.workspace, args.check, args.path), ensure_ascii=False))
