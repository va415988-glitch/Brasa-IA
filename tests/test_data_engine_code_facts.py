"""Testes do gerador code_facts (motor de dados verificados do pré-treino).

Os fatos de cada resposta são recalculados aqui de forma independente, com o módulo
``ast`` lendo o arquivo-fonte de novo; o gerador só é usado para produzir os exemplos.
"""

import ast
import hashlib
import inspect
import json
import random
import re
import subprocess
import sys
import sysconfig
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pretrain" / "data_engine"))
import code_facts  # noqa: E402

STDLIB = Path(sysconfig.get_paths()["stdlib"])
SMALL = ["json", "textwrap", "functools", "string", "enum", "contextlib", "dataclasses", "shlex", "abc"]
REAL_JSONL = ROOT / "pretrain_data_raw" / "engine" / "code_facts.jsonl"
REAL_TXT = ROOT / "pretrain_data_raw" / "engine" / "code_facts.txt"
SCOPE_TYPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
EXCHANGE = re.compile(r"<\|user\|>\n(.*?)\n<\|assistant\|>\n(.*?)\n(?=<\|user\|>\n|\Z)", re.S)


# ---- recálculo independente ---------------------------------------------------

def read_source(origin, path, sha1):
    """Relê o arquivo; o último valor diz se ele ainda é o mesmo da geração."""
    text = ((STDLIB if origin == "stdlib" else ROOT) / path).read_text(encoding="utf-8")
    return text, ast.parse(text), hashlib.sha1(text.encode("utf-8")).hexdigest() == sha1


def find_node(tree, line, qualified):
    parts = qualified.split(".")
    for top in tree.body:
        if isinstance(top, SCOPE_TYPES) and top.name == parts[0]:
            if len(parts) == 1 and top.lineno == line:
                return top
            if len(parts) == 2 and isinstance(top, ast.ClassDef):
                for child in top.body:
                    if isinstance(child, SCOPE_TYPES) and child.name == parts[1] and child.lineno == line:
                        return child
    raise AssertionError(f"{qualified} não encontrado na linha {line}")


def scope_nodes(nodes, skip_lambda=False):
    for node in nodes:
        if isinstance(node, SCOPE_TYPES) or (skip_lambda and isinstance(node, ast.Lambda)):
            continue
        yield node
        yield from scope_nodes(ast.iter_child_nodes(node), skip_lambda)


def params_of(node):
    args = node.args
    positional = args.posonlyargs + args.args
    first_default = len(positional) - len(args.defaults)
    text = lambda item: None if item is None else ast.unparse(item)  # noqa: E731
    rows = []
    for position, arg in enumerate(positional):
        default = args.defaults[position - first_default] if position >= first_default else None
        rows.append({"name": arg.arg, "kind": "posonly" if position < len(args.posonlyargs) else "normal",
                     "default": text(default), "annotation": text(arg.annotation)})
    if args.vararg:
        rows.append({"name": args.vararg.arg, "kind": "vararg", "default": None, "annotation": text(args.vararg.annotation)})
    for arg, default in zip(args.kwonlyargs, args.kw_defaults):
        rows.append({"name": arg.arg, "kind": "kwonly", "default": text(default), "annotation": text(arg.annotation)})
    if args.kwarg:
        rows.append({"name": args.kwarg.arg, "kind": "varkw", "default": None, "annotation": text(args.kwarg.annotation)})
    return rows


def signature_of(node):
    kinds = {"posonly": inspect.Parameter.POSITIONAL_ONLY, "normal": inspect.Parameter.POSITIONAL_OR_KEYWORD,
             "vararg": inspect.Parameter.VAR_POSITIONAL, "kwonly": inspect.Parameter.KEYWORD_ONLY,
             "varkw": inspect.Parameter.VAR_KEYWORD}
    return inspect.Signature([inspect.Parameter(row["name"], kinds[row["kind"]],
                                                default=inspect.Parameter.empty if row["default"] is None else None)
                              for row in params_of(node)])


PATH_REF = re.compile(r"`([\w./-]+\.py)(?::(\d+))?`")
LINE_REF = re.compile(r"linhas? (\d+)(?:\s*(?:–|a)\s*(\d+))?")
SYMBOL_DOMAINS = ("parameters", "returns", "docstring", "class_methods", "class_bases", "line_lookup", "calls",
                  "explain_small", "call_example")


def references(answer):
    """Caminhos e números de linha citados na prosa (fora de blocos de código e citações)."""
    text = re.sub(r"```.*?```", " ", answer, flags=re.S)
    text = "\n".join(line for line in text.split("\n") if not line.startswith(">"))
    paths, lines = [], []
    for match in PATH_REF.finditer(text):
        paths.append(match.group(1))
        if match.group(2):
            lines.append(int(match.group(2)))
    text = re.sub(r"`[^`]*`", " ", text)
    for match in LINE_REF.finditer(text):
        lines += [int(group) for group in match.groups() if group]
    return paths, lines


FIXED_TOKENS = {"None", "object", "getattr", "import", "from ... import", "python/", "python/*.py", "sys.path", "self", "cls",
                "yield", "yield from", "return", "async def", "await", "async for", "async with", "with", "as", "for", "if",
                "try", "pass", "...", "def", "class", "raise", "assert", "del", "nonlocal", "__init__", "_", "@property", "@classmethod",
                "@staticmethod", "@abstractmethod", "+=", "not", "lambda"}


def module_of(origin, path):
    if origin == "brasa":
        return Path(path).stem
    parts = list(Path(path).with_suffix("").parts)
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def prose_tokens(answer):
    text = re.sub(r"```.*?```", " ", answer, flags=re.S)
    text = "\n".join(line for line in text.split("\n") if not line.startswith(">"))
    return re.findall(r"`([^`\n]+)`", text)


def code_block(answer):
    match = re.search(r"```python\n(.*?)\n```", answer, re.S)
    assert match, "resposta sem bloco de código"
    return match.group(1)


class RecordChecker:
    """Confere um exemplo contra o código-fonte, sem usar os helpers do gerador."""

    def __init__(self, test):
        self.test = test

    def check(self, record):
        verification = record["verification"]
        domain = record["domain"]
        answer = record["messages"][1]["content"]
        source, tree, fresh = read_source(verification["origin"], verification["path"], verification["source_sha1"])
        if not fresh:
            return False  # o arquivo mudou depois da geração; o exemplo não pode ser reconferido
        kind = domain.split(".", 1)[1]
        allowed_lines = getattr(self, "check_" + kind)(verification, tree, source, answer)
        allowed_paths = ({item[1] for item in verification["facts"]["defs"]} if kind == "where_defined"
                         else {verification["path"]})
        paths, lines = references(answer)
        for path in paths:
            self.test.assertIn(path, allowed_paths)
        for line in lines:
            self.test.assertIn(line, allowed_lines)
        self.check_grounded_tokens(verification, kind, tree, source, answer)
        if kind in SYMBOL_DOMAINS:
            node = find_node(tree, verification["line"], verification["symbol"])
            start = min([node.lineno] + [item.lineno for item in node.decorator_list]) if kind == "explain_small" else node.lineno
            self.test.assertIn(start, lines)  # a resposta diz onde o símbolo está
            self.test.assertIn(verification["path"], paths)
        return True

    def check_grounded_tokens(self, verification, kind, tree, source, answer):
        """Todo trecho em crase da prosa vem do fonte, do exemplo verificado ou de um vocabulário fixo."""
        module = module_of(verification["origin"], verification["path"])
        allowed = set(FIXED_TOKENS) | {verification["path"], module, f"from {module} import ..."}
        sources = [source]
        if kind == "where_defined":
            name = verification["facts"]["name"]
            allowed.add(name)
            for origin, path, _, _, _ in verification["facts"]["defs"]:
                module = module_of(origin, path)
                allowed |= {path, module, f"from {module} import {name}"}
        elif kind == "imports" and verification["facts"]["variant"] == "check":
            allowed.add(verification["facts"]["module"])  # ausência/presença já conferida em check_imports
        elif kind in SYMBOL_DOMAINS:
            node = find_node(tree, verification["line"], verification["symbol"])
            qual = verification["symbol"]
            owner = qual.split(".")[0] if "." in qual else None
            allowed |= {qual, node.name, f"obj.{node.name}(...)", f"obj.{node.name}"}
            allowed |= {"@" + ast.unparse(item) for item in node.decorator_list}
            if owner:
                allowed |= {owner, f"{owner}(...)", f"{owner}()"}
            if not isinstance(node, ast.ClassDef):
                signature = f"{node.name}({ast.unparse(node.args)})" + (f" -> {ast.unparse(node.returns)}" if node.returns else "")
                allowed.add(signature)
                for row in params_of(node):
                    allowed |= {"*" + row["name"], "**" + row["name"]}
                    if row["default"] is not None:
                        allowed.add(f"{row['name']}={row['default']}")
        blocks = "\n".join(re.findall(r"```python\n(.*?)\n```", answer, re.S))
        unparsed = None
        for token in prose_tokens(answer):
            if token in allowed or token.startswith(verification["path"] + ":") or token in blocks or any(token in text for text in sources):
                continue
            if kind == "where_defined" and any(token.startswith(item[1] + ":") for item in verification["facts"]["defs"]):
                continue
            if unparsed is None:
                unparsed = {ast.unparse(item) for item in ast.walk(tree) if isinstance(item, (ast.expr, ast.stmt, ast.arguments))}
            self.test.assertIn(token, unparsed, "trecho de código sem origem no fonte")

    def assert_in(self, needle, haystack):
        self.test.assertIn(needle, haystack)

    def check_parameters(self, verification, tree, source, answer):
        node = find_node(tree, verification["line"], verification["symbol"])
        facts = verification["facts"]
        self.test.assertEqual(facts["params"], params_of(node))
        self.assert_in(verification["path"], answer)
        self.assert_in(str(verification["line"]), answer)
        rows = params_of(node)
        if facts["implicit"] is not None:
            self.test.assertEqual(rows[0]["name"], facts["implicit"])
            rows = rows[1:]
        for row in rows:
            prefix = {"vararg": "*", "varkw": "**"}.get(row["kind"], "")
            self.assert_in(f"`{prefix}{row['name']}`", answer)
            if row["default"] is not None:
                self.assert_in(f"`{row['default']}`", answer)
        if len(facts["signature"]) <= 160:
            self.assert_in(f"`{facts['signature']}`", answer)
        return {node.lineno}

    def check_returns(self, verification, tree, source, answer):
        node = find_node(tree, verification["line"], verification["symbol"])
        facts = verification["facts"]
        returns = [(ast.unparse(item.value), item.lineno) for item in scope_nodes(node.body)
                   if isinstance(item, ast.Return) and item.value is not None]
        self.test.assertEqual([tuple(item) for item in facts["returns"]], returns)
        yields = [item for item in scope_nodes(node.body, skip_lambda=True) if isinstance(item, (ast.Yield, ast.YieldFrom))]
        self.test.assertEqual(len(facts["yields"]), len(yields))
        annotation = None if node.returns is None else ast.unparse(node.returns)
        self.test.assertEqual(facts["annotation"], annotation)
        if annotation and "contextmanager" not in " ".join(facts["decorators"]):
            self.assert_in(f"`{annotation}`", answer)
        if not yields:
            seen = []
            for value, line in returns:
                if value not in seen:
                    seen.append(value)
                    if len(seen) <= 6:
                        self.assert_in(f"`{value}` (linha {line})", answer)
            if not returns and "levanta" not in answer:
                self.assert_in("`None`", answer)
        else:
            self.test.assertTrue("yield" in answer)
        return {node.lineno} | {item.lineno for item in scope_nodes(node.body)
                                if isinstance(item, (ast.Return, ast.Yield, ast.YieldFrom, ast.Raise))}

    def check_docstring(self, verification, tree, source, answer):
        node = find_node(tree, verification["line"], verification["symbol"])
        self.check_quote(verification["facts"], ast.get_docstring(node), answer)
        return {node.lineno, node.end_lineno}

    def check_module_docstring(self, verification, tree, source, answer):
        self.check_quote(verification["facts"], ast.get_docstring(tree), answer)
        return {tree.body[0].lineno} if tree.body else set()

    def check_quote(self, facts, doc, answer):
        if facts.get("quoted") is None:
            self.test.assertIsNone(doc)
            self.test.assertRegex(answer, r"não tem docstring|Não há docstring")
            return
        self.assert_in(facts["quoted"], doc)
        for line in facts["quoted"].split("\n"):
            self.assert_in(("> " + line) if line.strip() else ">", answer)

    def check_module_definitions(self, verification, tree, source, answer):
        facts = verification["facts"]
        defs = [["class" if isinstance(item, ast.ClassDef) else "function", item.name, item.lineno]
                for item in tree.body if isinstance(item, SCOPE_TYPES)]
        expected = {"all": defs, "count": defs, "classes": [item for item in defs if item[0] == "class"],
                    "functions": [item for item in defs if item[0] == "function"],
                    "public": [item for item in defs if not item[1].startswith("_")]}[facts["variant"]]
        self.test.assertEqual(facts["defs"], expected)
        self.test.assertEqual(facts["classes"], sum(1 for item in defs if item[0] == "class"))
        self.test.assertEqual(facts["functions"], sum(1 for item in defs if item[0] == "function"))
        if facts["variant"] != "count":
            for _, name, line in expected:
                self.assert_in(f"`{name}` (linha {line})", answer)
        else:
            self.assert_in(f"{facts['classes']} classe", answer)
        return {line for _, _, line in defs}

    def check_class_methods(self, verification, tree, source, answer):
        node = find_node(tree, verification["line"], verification["symbol"])
        methods = [[item.name, item.lineno] for item in node.body if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))]
        self.test.assertEqual([item[:2] for item in verification["facts"]["methods"]], methods)
        for name, line in methods:
            self.assert_in(f"- `{name}` (linha {line})", answer)
        if not methods:
            self.assert_in("def", answer)
        return {node.lineno} | {line for _, line in methods}

    def check_class_bases(self, verification, tree, source, answer):
        node = find_node(tree, verification["line"], verification["symbol"])
        bases = [ast.unparse(item) for item in node.bases]
        self.test.assertEqual(verification["facts"]["bases"], bases)
        for base in bases:
            self.assert_in(f"`{base}`", answer)
        if not bases:
            self.assert_in("`object`", answer)
        return {node.lineno}

    def check_line_lookup(self, verification, tree, source, answer):
        node = find_node(tree, verification["line"], verification["symbol"])
        header = source.split("\n")[node.lineno - 1].strip()
        self.test.assertEqual(verification["facts"]["header"], header[:240])
        self.assert_in(f"`{header}`", answer)
        if node.end_lineno != node.lineno:
            self.assert_in(str(node.end_lineno), answer)
        owner = [top.lineno for top in tree.body if isinstance(top, ast.ClassDef) and node in top.body]
        return {node.lineno, node.end_lineno, *owner, *(item.lineno for item in node.decorator_list)}

    def check_calls(self, verification, tree, source, answer):
        node = find_node(tree, verification["line"], verification["symbol"])
        calls = []
        for item in scope_nodes(node.body):
            if isinstance(item, ast.Call):
                name = ast.unparse(item.func)[:120]
                if name not in calls:
                    calls.append(name)
        self.test.assertEqual(verification["facts"]["calls"], calls[:16])
        self.test.assertEqual(verification["facts"]["total"], len(calls))
        for name in calls[:16]:
            self.assert_in(f"`{name}`", answer)
        return {node.lineno, node.end_lineno}

    def check_imports(self, verification, tree, source, answer):
        facts = verification["facts"]
        nodes = sorted((item for item in ast.walk(tree) if isinstance(item, (ast.Import, ast.ImportFrom))),
                       key=lambda item: (item.lineno, item.col_offset))
        if facts["variant"] == "check":
            target = facts["module"]
            def mentions(item):
                names = [alias.name for alias in item.names] if isinstance(item, ast.Import) else ["." * item.level + (item.module or "")]
                return any(name == target or name.startswith(target + ".") for name in names)
            hits = [[item.lineno, ast.unparse(item)] for item in nodes if mentions(item)]
            self.test.assertEqual(facts["hits"], hits)
            self.test.assertTrue(answer.startswith(("Sim", "Importa")) if hits else answer.startswith("Não"))
            for line, text in hits[:6]:
                self.assert_in(f"`{text}` (linha {line})", answer)
            return {line for line, _ in hits}
        expected = nodes if facts["variant"] == "all" else [item for item in nodes if item in tree.body]
        self.test.assertEqual(facts["imports"], [[item.lineno, ast.unparse(item)] for item in expected])
        for line, text in facts["imports"][:25]:
            self.assert_in(f"linha {line}: `{text}`", answer)
        return {item.lineno for item in nodes}

    def check_where_defined(self, verification, tree, source, answer):
        facts = verification["facts"]
        self.assert_in(f"`{facts['name']}`", answer)
        for origin, path, line, kind, sha1 in facts["defs"]:
            text, other_tree, fresh = read_source(origin, path, sha1)
            if not fresh:
                continue
            node = find_node(other_tree, line, facts["name"])
            self.test.assertEqual(kind, "class" if isinstance(node, ast.ClassDef) else "function")
            self.assert_in(f"`{path}`", answer)
            self.assert_in(f"linha {line}", answer)
        return {item[2] for item in facts["defs"]}

    def check_explain_small(self, verification, tree, source, answer):
        node = find_node(tree, verification["line"], verification["symbol"])
        snippet = code_block(answer)
        self.test.assertEqual(snippet, verification["facts"]["code"])
        start = min([node.lineno] + [item.lineno for item in node.decorator_list])
        expected = textwrap.dedent("\n".join(source.split("\n")[start - 1:node.end_lineno])).rstrip()
        self.test.assertEqual(snippet, expected)
        self.test.assertEqual(ast.dump(ast.parse(snippet).body[0]), ast.dump(node))
        return {start, node.lineno, node.end_lineno}

    def check_call_example(self, verification, tree, source, answer):
        node = find_node(tree, verification["line"], verification["symbol"])
        example = code_block(answer)
        module = ast.parse(example)
        imports = [item for item in module.body if isinstance(item, ast.ImportFrom)]
        self.test.assertEqual(len(imports), 1)
        module_path = imports[0].module.replace(".", "/")
        if verification["origin"] == "stdlib":
            self.test.assertIn(verification["path"], (module_path + ".py", module_path + "/__init__.py"))
        else:
            self.test.assertEqual(verification["path"], f"python/{module_path}.py")
        owner = verification["symbol"].split(".")[0]
        self.test.assertEqual(imports[0].names[0].name, owner)
        decorators = [ast.unparse(item) for item in node.decorator_list]
        is_method = "." in verification["symbol"]
        static = "staticmethod" in decorators
        if node.name == "__init__":
            wanted = lambda call: isinstance(call.func, ast.Name) and call.func.id == owner  # noqa: E731
        else:
            wanted = lambda call: (isinstance(call.func, ast.Attribute) and call.func.attr == node.name) or (  # noqa: E731
                isinstance(call.func, ast.Name) and call.func.id == node.name)
        calls = [item for item in ast.walk(module) if isinstance(item, ast.Call) and wanted(item)]
        self.test.assertTrue(calls, "exemplo não chama a função")
        signature = signature_of(node)
        for call in calls:
            leading = [object()] if is_method and not static else []
            signature.bind(*leading, *[object() for _ in call.args], **{item.arg: object() for item in call.keywords})
        names = [row["name"] for row in params_of(node)]
        for call in calls:  # placeholders do exemplo são os nomes dos próprios parâmetros
            for arg in call.args:
                self.test.assertIn(ast.unparse(arg), names)
        signature_text = f"{node.name}({ast.unparse(node.args)})" + (f" -> {ast.unparse(node.returns)}" if node.returns else "")
        if len(signature_text) <= 160:
            self.assert_in(f"`{signature_text}`", answer)
        return {node.lineno}


# ---- testes -------------------------------------------------------------------

class CodeFactsGeneratorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.records, cls.stats = code_facts.generate(500, 11, stdlib_include=SMALL)

    def test_generates_requested_amount_with_every_kind(self):
        self.assertEqual(len(self.records), 500)
        domains = {record["domain"] for record in self.records}
        self.assertEqual(domains, {f"code.{kind}" for kind in code_facts.KIND_WEIGHTS})
        origins = {record["verification"]["origin"] for record in self.records}
        self.assertEqual(origins, {"stdlib", "brasa"})

    def test_same_seed_is_deterministic(self):
        again, _ = code_facts.generate(500, 11, stdlib_include=SMALL)
        self.assertEqual(json.dumps(again, ensure_ascii=False), json.dumps(self.records, ensure_ascii=False))

    def test_other_seed_changes_selection(self):
        other, _ = code_facts.generate(500, 12, stdlib_include=SMALL)
        self.assertNotEqual([item["messages"] for item in other], [item["messages"] for item in self.records])

    def test_record_shape_and_roles(self):
        for record in self.records:
            self.assertEqual([message["role"] for message in record["messages"]], ["user", "assistant"])
            self.assertEqual(record["provenance"], "tool-verified-code_facts-v1")
            verification = record["verification"]
            self.assertEqual(verification["status"], "passed")
            self.assertEqual(verification["tool"], "python/code_intelligence.py:python_observations")
            self.assertTrue(verification["checks"])
            for message in record["messages"]:
                self.assertTrue(message["content"].strip())
                self.assertNotIn("\x00", message["content"])
                self.assertNotIn("<|", message["content"])

    def test_questions_and_answers_are_unique(self):
        questions = [record["messages"][0]["content"] for record in self.records]
        answers = [record["messages"][1]["content"] for record in self.records]
        self.assertEqual(len(set(questions)), len(questions))
        self.assertEqual(len(set(answers)), len(answers))

    def test_every_answer_matches_an_independent_recomputation(self):
        checker = RecordChecker(self)
        for record in self.records:
            with self.subTest(domain=record["domain"], path=record["verification"]["path"], line=record["verification"]["line"]):
                self.assertTrue(checker.check(record))

    def test_where_defined_uniqueness_claims_hold_for_indexed_files(self):
        counts = {}
        for source_file in code_facts.load_sources(stdlib_include=SMALL):
            for item in ast.parse(source_file.source).body:
                if isinstance(item, SCOPE_TYPES):
                    counts.setdefault(item.name, []).append(source_file.display)
        for record in self.records:
            if record["domain"] != "code.where_defined":
                continue
            facts = record["verification"]["facts"]
            found = counts[facts["name"]] if facts["scope"] == "all" else [path for path in counts[facts["name"]] if path.startswith("python/")]
            self.assertEqual(len(found), len(facts["defs"]))
            if len(facts["defs"]) == 1:
                self.assertIn("única definição", record["messages"][1]["content"])

    def test_held_out_evaluation_files_are_never_sources(self):
        displays = [display for _, display in code_facts.project_paths()]
        self.assertFalse([display for display in displays if "programming_qualification" in display])
        for record in self.records:
            self.assertNotIn("programming_qualification", json.dumps(record, ensure_ascii=False))

    def test_engine_disagreement_rejects_the_symbol(self):
        source_file = code_facts.load_sources(project=False, stdlib_include=["textwrap"])[0]
        node = next(item for item in source_file.tree.body if isinstance(item, ast.FunctionDef) and item.name == "dedent")
        row = next(item for item in source_file.observations["symbols"] if item["qualified_name"] == "dedent")
        self.assertTrue(code_facts.engine_agrees(source_file, node, row))
        self.assertFalse(code_facts.engine_agrees(source_file, node, {**row, "parameters": ["texto"]}))
        self.assertFalse(code_facts.engine_agrees(source_file, node, {**row, "line": row["line"] + 1}))


class CodeFactsCliTest(unittest.TestCase):
    def run_cli(self, out, seed):
        command = [sys.executable, str(ROOT / "pretrain" / "data_engine" / "code_facts.py"), "--out", str(out),
                   "--count", "150", "--seed", str(seed), "--stdlib-include", "json,textwrap,shlex"]
        completed = subprocess.run(command, capture_output=True, text=True, timeout=300, cwd=ROOT)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return json.loads(completed.stdout)

    def test_cli_outputs_are_deterministic_and_well_formed(self):
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            summary = self.run_cli(first, 5)
            self.run_cli(second, 5)
            for name in ("code_facts.jsonl", "code_facts.txt"):
                self.assertEqual((Path(first) / name).read_bytes(), (Path(second) / name).read_bytes())
            records = [json.loads(line) for line in (Path(first) / "code_facts.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(records), summary["examples"])
            check_txt(self, (Path(first) / "code_facts.txt").read_text(encoding="utf-8"), records, summary["documents"])


def check_txt(test, text, records, documents_expected=None):
    test.assertTrue(text.endswith("\x00"))
    documents = text.split("\x00")[:-1]
    if documents_expected is not None:
        test.assertEqual(len(documents), documents_expected)
    by_pair = {(record["messages"][0]["content"], record["messages"][1]["content"]): record for record in records}
    seen = 0
    for document in documents:
        test.assertTrue(document.startswith("<|user|>\n"))
        test.assertTrue(document.endswith("\n"))
        exchanges = EXCHANGE.findall(document)
        test.assertTrue(1 <= len(exchanges) <= 3)
        rebuilt = "".join(f"<|user|>\n{question}\n<|assistant|>\n{answer}\n" for question, answer in exchanges)
        test.assertEqual(rebuilt, document)
        test.assertEqual(document.count("<|user|>"), len(exchanges))
        test.assertEqual(document.count("<|assistant|>"), len(exchanges))
        paths = {by_pair[pair]["verification"]["path"] for pair in exchanges}
        test.assertEqual(len(paths), 1)  # trocas agrupadas são sobre o mesmo arquivo
        seen += len(exchanges)
    test.assertEqual(seen, len(records))


@unittest.skipUnless(REAL_JSONL.exists() and REAL_TXT.exists(), "saída completa ainda não gerada")
class CodeFactsRealOutputTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with REAL_JSONL.open(encoding="utf-8") as handle:
            cls.records = [json.loads(line) for line in handle]

    def test_sample_matches_independent_recomputation(self):
        checker = RecordChecker(self)
        sample = random.Random(0).sample(self.records, min(600, len(self.records)))
        checked = 0
        for record in sample:
            with self.subTest(domain=record["domain"], path=record["verification"]["path"], line=record["verification"]["line"]):
                checked += checker.check(record)
        self.assertGreater(checked, len(sample) // 2)

    def test_txt_mirrors_jsonl(self):
        check_txt(self, REAL_TXT.read_text(encoding="utf-8"), self.records)

    def test_no_duplicate_examples(self):
        questions = [record["messages"][0]["content"] for record in self.records]
        answers = [record["messages"][1]["content"] for record in self.records]
        self.assertEqual(len(set(questions)), len(questions))
        self.assertEqual(len(set(answers)), len(answers))


if __name__ == "__main__":
    unittest.main()
