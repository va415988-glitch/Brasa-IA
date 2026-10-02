"""Compile explicit Python examples into tests and search bounded one-edit repairs.

This is symbolic tooling, not neural code generation. It uses the user's source
and input/output examples, preserves the initial implementation and test oracle,
and only proposes a repair; the runtime still performs approved writes and tests.
"""
from __future__ import annotations

import ast
import copy
from dataclasses import dataclass
import json
import math
from pathlib import PurePosixPath
import re
import time
import unicodedata

from execution_engine import Interpreter, check_value, json_value

MAX_REPAIR_CANDIDATES = 160
MAX_REPAIR_SECONDS = 2


@dataclass
class ExampleProgram:
    path: str
    test_path: str
    source: str
    function: str
    cases: list[dict]


def normalized(text):
    return ''.join(c for c in unicodedata.normalize('NFD', text.casefold()) if not unicodedata.combining(c))


def safe_path(path):
    parts = PurePosixPath(path)
    if (len(path) > 250 or not re.fullmatch(r'[\w./-]+\.py', path) or parts.is_absolute()
            or any(p in {'..', '.git', '.ia-local-backups'} for p in parts.parts)
            or len(parts.parts) > 6):
        raise ValueError('Caminho Python relativo inválido')
    return parts.as_posix()


def literal(text):
    text = text.strip().replace('−', '-')
    # Numeric units are labels on the example, not part of the function value.
    numeric = re.fullmatch(r'([+-]?\d+(?:[.,]\d+)?)(?:\s*°?[A-Za-zÀ-ÿ%]+)?', text)
    if numeric:
        text = numeric[1].replace(',', '.')
    if len(text) > 2048:
        raise ValueError('Exemplo acima do limite')
    try:
        value = json.loads(text)
    except ValueError:
        value = ast.literal_eval(text)
    check_value(value)
    if len(json.dumps(value)) > 4096:
        raise ValueError('Exemplo acima do limite')
    return value


def module(source):
    if len(source.encode()) > 8192:
        raise ValueError('Fonte acima do limite de 8 KiB')
    tree = ast.parse(source)
    if len(list(ast.walk(tree))) > 500:
        raise ValueError('Fonte acima do limite de AST')
    functions = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    if not functions or any(not isinstance(n, ast.FunctionDef) for n in tree.body):
        raise ValueError('Este motor aceita somente funções puras de nível superior')
    allowed_calls = set(Interpreter.builtins) | set(functions) | {
        'range', 'list', 'tuple', 'set', 'sum', 'all', 'any', 'sorted', 'reversed',
        'enumerate', 'min', 'max', 'zip',
    }
    allowed_methods = set().union(*Interpreter.methods.values())
    forbidden = (ast.Import, ast.ImportFrom, ast.ClassDef, ast.AsyncFunctionDef,
                 ast.With, ast.AsyncWith, ast.Try, ast.Raise, ast.Global, ast.Nonlocal,
                 ast.Lambda, ast.Yield, ast.YieldFrom, ast.Await)
    for n in ast.walk(tree):
        if isinstance(n, forbidden):
            raise ValueError('Operação não suportada pelo motor de exemplos')
        if isinstance(n, ast.FunctionDef) and (n.decorator_list or n.returns
                or any(a.annotation for a in n.args.posonlyargs + n.args.args + n.args.kwonlyargs)
                or n.args.vararg or n.args.kwarg):
            raise ValueError('Decoradores, anotações e argumentos variádicos não são suportados')
        if isinstance(n, ast.Name) and n.id.startswith('__'):
            raise ValueError('Acesso interno não suportado')
        if isinstance(n, ast.Attribute) and n.attr not in allowed_methods:
            raise ValueError('Método não suportado')
        if isinstance(n, ast.Call) and not (
                isinstance(n.func, ast.Name) and n.func.id in allowed_calls
                or isinstance(n.func, ast.Attribute) and n.func.attr in allowed_methods):
            raise ValueError('Chamada não suportada')
    return tree, functions


def observe(source, function, case):
    _, functions = module(source)
    interpreter = Interpreter(functions)
    interpreter.deadline = time.monotonic() + .05
    return json_value(check_value(interpreter.invoke(functions[function], {},
        copy.deepcopy(case['args']), copy.deepcopy(case['kwargs']))))


def matches(actual, expected):
    if isinstance(expected, list):
        return isinstance(actual, list) and len(actual) == len(expected) and all(matches(a, b) for a, b in zip(actual, expected))
    if isinstance(expected, dict):
        return isinstance(actual, dict) and actual.keys() == expected.keys() and all(matches(actual[k], expected[k]) for k in expected)
    if isinstance(expected, float):
        return type(actual) in {int, float} and math.isclose(actual, expected, rel_tol=1e-9, abs_tol=1e-9)
    return actual == expected and (type(actual) is bool) == (type(expected) is bool)


def parse_example_program(question):
    """Recognize a named, supplied function and explicit bullet input/output pairs."""
    try:
        if len(question) > 16000:
            return None
        lines = question.splitlines()
        start = next(i for i, line in enumerate(lines) if re.match(r'^def\s+\w+\s*\(', line))
        before = '\n'.join(lines[:start])
        paths = re.findall(r'(?<![\w./-])([\w./-]+\.py)\b', before)
        if len(set(paths)) != 1 or not re.search(r'\b(?:crie|criar|create|escreva|grave)\b', normalized(before)):
            return None
        previous = next((s.strip() for s in reversed(lines[:start]) if s.strip()), '')
        if re.match(r'^(?:import |from |@|\w+\s*=)', previous):
            return None
        end = start + 1
        while end < len(lines) and (not lines[end].strip() or lines[end][0].isspace()
                                   or lines[end].startswith('def ')):
            end += 1
        source = '\n'.join(lines[start:end]).rstrip() + '\n'
        tree, functions = module(source)
        function = tree.body[0].name
        path = safe_path(paths[0])
        rest = '\n'.join(lines[end:])
        test_paths = re.findall(r'(?<![\w./-])([\w./-]+\.py)\b', rest)
        if len(set(test_paths)) != 1:
            return None
        test_path = safe_path(test_paths[0])
        if not re.fullmatch(r'(?:tests?/)?test_[\w-]+\.py', test_path) or path == test_path:
            return None
        cases = []
        for line in lines[end:]:
            if not re.match(r'^\s*[-*]\s+', line):
                continue
            pair = re.split(r'\s+(?:deve\s+(?:resultar\s+em|retornar|produzir)|retorna|resulta\s+em)\s+|\s*(?:=>|->)\s*',
                            re.sub(r'^\s*[-*]\s+', '', line).rstrip('. '), maxsplit=1, flags=re.I)
            if len(pair) != 2:
                return None
            value, expected = pair
            args, kwargs = [], {}
            if re.match(r'^' + re.escape(function) + r'\s*\(', value):
                call = ast.parse(value, mode='eval').body
                if not isinstance(call, ast.Call) or any(k.arg is None for k in call.keywords):
                    return None
                args = [literal(ast.get_source_segment(value, a)) for a in call.args]
                kwargs = {k.arg: literal(ast.get_source_segment(value, k.value)) for k in call.keywords}
            else:
                args = [literal(value)]
            case = {'args': args, 'kwargs': kwargs, 'expected': literal(expected)}
            observe(source, function, case)  # Validate subset and argument binding, not correctness.
            cases.append(case)
        if not 2 <= len(cases) <= 12 or len({json.dumps((c['args'], c['kwargs']), sort_keys=True) for c in cases}) != len(cases):
            return None
        return ExampleProgram(path, test_path, source, function, cases)
    except (ValueError, TypeError, SyntaxError, KeyError, IndexError, StopIteration, ZeroDivisionError,
            OverflowError, RecursionError):
        return None


def implementation_from_examples(program, existing_paths):
    if program.path in existing_paths or program.test_path in existing_paths:
        return None
    parents = len(PurePosixPath(program.test_path).parts) - 1
    test = ('import importlib.util\nfrom pathlib import Path\nimport unittest\n\n'
            f'_path = Path(__file__).resolve().parents[{parents}] / {program.path!r}\n'
            '_spec = importlib.util.spec_from_file_location("example_subject", _path)\n'
            '_subject = importlib.util.module_from_spec(_spec)\n'
            '_spec.loader.exec_module(_subject)\n\n'
            'class ExplicitExamples(unittest.TestCase):\n')
    for i, case in enumerate(program.cases, 1):
        assertion = 'assertAlmostEqual' if isinstance(case['expected'], float) else 'assertEqual'
        tolerance = ', delta=' + repr(max(1e-9, abs(case['expected']) * 1e-9)) if assertion == 'assertAlmostEqual' else ''
        test += (f'    def test_example_{i}(self):\n'
                 f'        actual = _subject.{program.function}(*{case["args"]!r}, **{case["kwargs"]!r})\n')
        if isinstance(case['expected'], bool):
            test += '        self.assertIsInstance(actual, bool)\n'
        elif isinstance(case['expected'], (int, float)):
            test += '        self.assertNotIsInstance(actual, bool)\n'
        test += f'        self.{assertion}(actual, {case["expected"]!r}{tolerance})\n\n'
    return {'assumptions': ['Preservar a função fornecida até executar a primeira suíte.',
                            f'Gerar {len(program.cases)} testes separados a partir dos resultados esperados do pedido.'],
            'operations': [{'tool': 'create_file', 'arguments': {'path': program.path, 'content': program.source}},
                           {'tool': 'create_file', 'arguments': {'path': program.test_path, 'content': test}}]}


def one_edit_repair(program, source):
    """Propose one literal/operator change only if it uniquely fits every example."""
    started = time.monotonic()
    tree, _ = module(source)
    observed = [observe(source, program.function, c) for c in program.cases]
    if all(matches(a, c['expected']) for a, c in zip(observed, program.cases)):
        return None
    raw = source.encode()
    lines = raw.splitlines(keepends=True)
    candidates, solutions = set(), []
    for node in ast.walk(tree):
        if time.monotonic() - started > MAX_REPAIR_SECONDS or len(candidates) >= MAX_REPAIR_CANDIDATES:
            return None  # A partial search cannot establish that the repair is unique.
        replacements = []
        if isinstance(node, ast.Constant) and type(node.value) in {int, float} and abs(node.value) <= 1e6:
            values = {node.value - 1, node.value + 1, 0, 1, -1, 2}
            for actual, case in zip(observed, program.cases):
                expected = case['expected']
                if type(actual) in {int, float} and type(expected) in {int, float}:
                    values.add(node.value + expected - actual)
                    if actual:
                        values.add(node.value * expected / actual)
            replacements = [repr(int(v) if float(v).is_integer() else v)
                            for v in sorted(values) if math.isfinite(v) and abs(v) <= 1e6 and v != node.value]
        elif isinstance(node, ast.BinOp):
            for operator in (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod):
                if not isinstance(node.op, operator):
                    changed = copy.deepcopy(node); changed.op = operator()
                    replacements.append(ast.unparse(changed))
        elif isinstance(node, ast.Compare) and len(node.ops) == 1:
            for operator in (ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE):
                if not isinstance(node.ops[0], operator):
                    changed = copy.deepcopy(node); changed.ops = [operator()]
                    replacements.append(ast.unparse(changed))
        if not replacements:
            continue
        begin = sum(map(len, lines[:node.lineno - 1])) + node.col_offset
        end = sum(map(len, lines[:node.end_lineno - 1])) + node.end_col_offset
        for replacement in replacements:
            if time.monotonic() - started > MAX_REPAIR_SECONDS or len(candidates) >= MAX_REPAIR_CANDIDATES:
                return None
            candidate = (raw[:begin] + replacement.encode() + raw[end:]).decode()
            if candidate in candidates:
                continue
            candidates.add(candidate)
            try:
                if all(matches(observe(candidate, program.function, c), c['expected']) for c in program.cases):
                    solutions.append(candidate)
                    if len(solutions) > 1:
                        return None
            except (ValueError, TypeError, KeyError, IndexError, ZeroDivisionError, OverflowError, RecursionError):
                pass
    if len(solutions) != 1:
        return None
    return {'assumptions': [f'Uma única edição validada nos {len(program.cases)} exemplos explícitos.',
                            'A proposta preserva os testes; a suíte real precisa ser executada novamente.'],
            'operations': [{'tool': 'edit_file', 'arguments': {'path': program.path,
                           'old_text': source, 'new_text': solutions[0]}}]}
