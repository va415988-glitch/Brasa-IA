"""Gerador ``code_facts`` do motor de dados do Brasa.

Produz perguntas e respostas em português sobre código Python real, ancoradas em
fatos estáticos de duas fontes locais:

* a biblioteca padrão instalada (caminho ``sysconfig`` ``stdlib``, sem pastas de teste);
* os módulos ``python/*.py`` do próprio projeto (exceto os da bancada de qualificação,
  que são dados de avaliação reservados).

Cada resposta é montada a partir das observações de ``python/code_intelligence.py``
(o motor da ferramenta ``inspect_code`` do runtime) e conferida com o módulo ``ast``.
Um símbolo só entra quando as duas leituras concordam (parâmetros, retornos,
chamadas, docstring e cabeçalho); exemplos cuja conferência falha são descartados.
Nenhum código analisado é importado ou executado: exemplos de chamada são validados
com ``inspect.Signature.bind`` sobre uma assinatura reconstruída do AST.

Saída (em ``--out``):
  code_facts.jsonl  um exemplo por linha: messages, domain, provenance, verification
  code_facts.txt    os mesmos exemplos no formato de pré-treino
                    ``<|user|>\\n...\\n<|assistant|>\\n...\\n``, documentos separados por NUL

Uso:
  python pretrain/data_engine/code_facts.py --out pretrain_data_raw/engine --count 30000 --seed 0
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import inspect
import json
import keyword
import random
import re
import sys
import sysconfig
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "python") not in sys.path:
    sys.path.insert(0, str(ROOT / "python"))
import code_intelligence  # noqa: E402  (motor da ferramenta inspect_code)

NAME = "code_facts"
PROVENANCE = "tool-verified-code_facts-v1"
DOC_SEP = "\x00"
STDLIB = Path(sysconfig.get_paths()["stdlib"])
PY_VERSION = f"{sys.version_info.major}.{sys.version_info.minor}"
PY_FULL = ".".join(str(part) for part in sys.version_info[:3])
ENGINE = "python/code_intelligence.py:python_observations"
SKIP_PARTS = ("test", "tests", "idle_test", "idlelib", "site-packages", "dist-packages",
              "__pycache__", "lib2to3", "turtledemo", "__phello__", "pydoc_data")
HELD_OUT = ("programming_qualification",)  # bancada de avaliação: nunca vira treino
MAX_FILE_BYTES = 600_000
CODEC_MARKER = "Python Character Mapping Codec"  # codecs gerados, repetitivos
BRASA_SHARE = 0.3
SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
FUNCS = (ast.FunctionDef, ast.AsyncFunctionDef)

KIND_WEIGHTS = {
    "parameters": 12, "returns": 10, "docstring": 9, "explain_small": 12, "call_example": 11,
    "where_defined": 9, "module_definitions": 6, "class_methods": 7, "calls": 6,
    "imports": 4, "class_bases": 5, "line_lookup": 5, "module_docstring": 4,
}


class Unsupported(Exception):
    """O fato não pode ser dito com segurança; o exemplo é descartado."""


# ---------------------------------------------------------------------------
# Fontes e índice
# ---------------------------------------------------------------------------

class SourceFile:
    def __init__(self, origin, display, module, path, source):
        self.origin, self.display, self.module, self.path = origin, display, module, path
        self.source = source
        self.lines = source.split("\n")  # mesma numeração do ast (splitlines quebra em \x0c)
        self.sha1 = hashlib.sha1(source.encode("utf-8")).hexdigest()
        self.tree = ast.parse(source, filename=display)
        self.observations = code_intelligence.python_observations(display, source)


class Symbol:
    def __init__(self, source_file, node, cls_node, row):
        self.file, self.node, self.cls, self.row = source_file, node, cls_node, row
        self.name = node.name
        self.qual = f"{cls_node.name}.{node.name}" if cls_node is not None else node.name
        if isinstance(node, ast.ClassDef):
            self.kind = "class"
        else:
            self.kind = "method" if cls_node is not None else "function"
        self.is_async = isinstance(node, ast.AsyncFunctionDef)
        self.line, self.end = node.lineno, node.end_lineno
        self.key = f"{source_file.display}:{self.line}:{self.qual}"


def module_name(relative):
    parts = list(Path(relative).with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    if not parts or not all(part.isidentifier() and not keyword.iskeyword(part) for part in parts):
        return None
    return ".".join(parts)


def stdlib_paths(include=None):
    for path in sorted(STDLIB.rglob("*.py")):
        relative = path.relative_to(STDLIB)
        if any(part in SKIP_PARTS for part in relative.parts[:-1]) or relative.name == "__main__.py":
            continue
        if relative.name.startswith("test_") or relative.parts[0] in SKIP_PARTS:
            continue
        top = relative.parts[0].removesuffix(".py")
        if include is not None and top not in include:
            continue
        yield path, relative.as_posix()


def imports_held_out(tree):
    for node in ast.walk(tree):
        names = []
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""] + [alias.name for alias in node.names]
        if any(name.startswith(HELD_OUT) for name in names):
            return True
    return False


def project_paths():
    for path in sorted((ROOT / "python").glob("*.py")):
        if path.name.startswith(HELD_OUT):
            continue
        yield path, f"python/{path.name}"


def load_sources(stdlib=True, project=True, stdlib_include=None):
    files = []
    pairs = []
    if stdlib:
        pairs += [("stdlib", path, display) for path, display in stdlib_paths(stdlib_include)]
    if project:
        pairs += [("brasa", path, display) for path, display in project_paths()]
    for origin, path, display in pairs:
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                continue
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        if CODEC_MARKER in source[:2000]:
            continue
        module = module_name(display if origin == "stdlib" else path.name)
        if module is None:
            continue
        try:
            source_file = SourceFile(origin, display, module, path, source)
        except (SyntaxError, ValueError):
            continue
        if origin == "brasa" and imports_held_out(source_file.tree):
            continue
        files.append(source_file)
    return files


def walk_scope(statements, lambdas=True):
    """Percorre o escopo próprio em pré-ordem (a mesma do NodeVisitor do motor)."""
    stack = list(reversed(statements))
    while stack:
        current = stack.pop()
        if isinstance(current, SCOPES) or (not lambdas and isinstance(current, ast.Lambda)):
            continue
        yield current
        stack.extend(reversed(list(ast.iter_child_nodes(current))))


def own_returns(node):
    return [item for item in walk_scope(node.body) if isinstance(item, ast.Return)]


def own_calls(node):
    calls = []
    for item in walk_scope(node.body):
        if isinstance(item, ast.Call):
            name = ast.unparse(item.func)[:120]
            if name not in calls:
                calls.append(name)
    return calls


def own_yields(node):
    return [item for item in walk_scope(node.body, lambdas=False) if isinstance(item, (ast.Yield, ast.YieldFrom))]


def engine_agrees(source_file, node, row):
    """Cruza a linha do motor inspect_code com o AST; qualquer divergência descarta o símbolo."""
    expected_kind = "class" if isinstance(node, ast.ClassDef) else "function"
    if row.get("kind") != expected_kind or row.get("line") != node.lineno or row.get("end_line") != node.end_lineno:
        return False
    if row.get("signature") != source_file.lines[node.lineno - 1].strip()[:240]:
        return False
    if row.get("docstring") != (ast.get_docstring(node) or "")[:500]:
        return False
    if isinstance(node, FUNCS):
        args = node.args
        if row.get("parameters") != [arg.arg for arg in [*args.posonlyargs, *args.args, *args.kwonlyargs]]:
            return False
        returns = [ast.unparse(item.value)[:240] for item in own_returns(node) if item.value is not None][:4]
        if row.get("returns") != returns or row.get("calls") != own_calls(node)[:16]:
            return False
        if row.get("async") != isinstance(node, ast.AsyncFunctionDef):
            return False
    return True


class Index:
    def __init__(self, files):
        self.files = files
        self.symbols = []
        self.rejected = 0
        self.module_level = {}  # nome -> [Symbol] (filhos diretos do módulo que passaram na conferência)
        self.module_level_all = {}  # nome -> total de definições diretas, conferidas ou não
        for source_file in files:
            rows = {(row["line"], row["qualified_name"]): row for row in source_file.observations["symbols"]}
            for top in source_file.tree.body:
                if not isinstance(top, SCOPES):
                    continue
                self.module_level_all[top.name] = self.module_level_all.get(top.name, 0) + 1
                row = rows.get((top.lineno, top.name))
                if row is None or not engine_agrees(source_file, top, row):
                    self.rejected += 1
                    continue
                symbol = Symbol(source_file, top, None, row)
                self.symbols.append(symbol)
                self.module_level.setdefault(top.name, []).append(symbol)
                if isinstance(top, ast.ClassDef):
                    for child in top.body:
                        if not isinstance(child, FUNCS):
                            continue
                        child_row = rows.get((child.lineno, f"{top.name}.{child.name}"))
                        if child_row is None or not engine_agrees(source_file, child, child_row):
                            self.rejected += 1
                            continue
                        self.symbols.append(Symbol(source_file, child, top, child_row))
        self.by_key = {symbol.key: symbol for symbol in self.symbols}
        self.file_by_display = {source_file.display: source_file for source_file in files}


# ---------------------------------------------------------------------------
# Fatos
# ---------------------------------------------------------------------------

def decorator_names(node):
    return [ast.unparse(item) for item in node.decorator_list]


def decorator_flags(decorators):
    flags = {"staticmethod": False, "classmethod": False, "property": False, "accessor": False,
             "overload": False, "abstract": False, "other": []}
    for text in decorators:
        last = text.split("(")[0].rsplit(".", 1)[-1]
        if last == "staticmethod":
            flags["staticmethod"] = True
        elif last == "classmethod":
            flags["classmethod"] = True
        elif last in ("property", "cached_property"):
            flags["property"] = True
        elif last in ("setter", "getter", "deleter"):
            flags["accessor"] = True
        elif last == "overload":
            flags["overload"] = True
        elif last == "abstractmethod":
            flags["abstract"] = True
        else:
            flags["other"].append(text)
    return flags


TRANSPARENT = ("final", "override", "wraps", "no_type_check")
CONTEXT_MANAGERS = ("contextmanager", "asynccontextmanager")


def decorator_last(text):
    return text.split("(")[0].rsplit(".", 1)[-1]


def opaque_decorators(flags):
    """Decoradores que podem mudar assinatura ou retorno do nome decorado."""
    return [text for text in flags["other"] if decorator_last(text) not in TRANSPARENT]


def parameter_rows(node):
    args = node.args
    positional = [*args.posonlyargs, *args.args]
    defaults = [None] * (len(positional) - len(args.defaults)) + list(args.defaults)
    rows = []
    for position, (arg, default) in enumerate(zip(positional, defaults)):
        rows.append({"name": arg.arg, "kind": "posonly" if position < len(args.posonlyargs) else "normal",
                     "default": None if default is None else ast.unparse(default),
                     "annotation": None if arg.annotation is None else ast.unparse(arg.annotation)})
    if args.vararg:
        rows.append({"name": args.vararg.arg, "kind": "vararg", "default": None,
                     "annotation": None if args.vararg.annotation is None else ast.unparse(args.vararg.annotation)})
    for arg, default in zip(args.kwonlyargs, args.kw_defaults):
        rows.append({"name": arg.arg, "kind": "kwonly", "default": None if default is None else ast.unparse(default),
                     "annotation": None if arg.annotation is None else ast.unparse(arg.annotation)})
    if args.kwarg:
        rows.append({"name": args.kwarg.arg, "kind": "varkw", "default": None,
                     "annotation": None if args.kwarg.annotation is None else ast.unparse(args.kwarg.annotation)})
    return rows


def sibling_names(symbol):
    body = symbol.cls.body if symbol.cls is not None else symbol.file.tree.body
    names = []
    for item in body:
        if isinstance(item, SCOPES):
            names.append(item.name)
        elif isinstance(item, ast.Assign):
            names += [target.id for target in item.targets if isinstance(target, ast.Name)]
        elif isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
            names.append(item.target.id)
    return names


def function_facts(symbol):
    """Fatos de uma função/método; Unsupported quando a leitura estática seria ambígua."""
    node = symbol.node
    decorators = decorator_names(node)
    flags = decorator_flags(decorators)
    if flags["overload"] or flags["accessor"]:
        raise Unsupported("sobrecarga ou acessor de property")
    if sibling_names(symbol).count(symbol.name) != 1:
        raise Unsupported("nome redefinido no mesmo escopo")
    params = parameter_rows(node)
    implicit = None
    if symbol.kind == "method" and not flags["staticmethod"]:
        positional = [row for row in params if row["kind"] in ("posonly", "normal")]
        if not positional:
            raise Unsupported("método sem parâmetro para a instância")
        implicit = positional[0]["name"]
    returns = [(ast.unparse(item.value), item.lineno) for item in own_returns(node) if item.value is not None]
    bare = sum(1 for item in own_returns(node) if item.value is None)
    yields = [(None if item.value is None else ast.unparse(item.value), item.lineno,
               "yield from" if isinstance(item, ast.YieldFrom) else "yield") for item in own_yields(node)]
    return {"params": params, "implicit": implicit, "decorators": decorators, "flags": flags,
            "annotation": None if node.returns is None else ast.unparse(node.returns),
            "returns": returns, "bare_returns": bare, "yields": yields, "async": symbol.is_async,
            "signature": f"{node.name}({ast.unparse(node.args)})" + (f" -> {ast.unparse(node.returns)}" if node.returns else "")}


def body_without_docstring(node):
    body = list(node.body)
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
        body = body[1:]
    return body


ENUM_LIKE = ("Enum", "IntEnum", "StrEnum", "Flag", "IntFlag", "NamedTuple", "TypedDict", "Protocol", "type")


def constructor_ok(cls_node):
    """Classe em que ``Cls(...)`` chama diretamente o ``__init__`` declarado no corpo."""
    if cls_node.decorator_list or cls_node.keywords:
        return False
    names = [item.name for item in cls_node.body if isinstance(item, FUNCS)]
    if names.count("__init__") != 1 or "__new__" in names:
        return False
    for base in cls_node.bases:
        text = ast.unparse(base)
        if text.rsplit(".", 1)[-1].split("[")[0] in ENUM_LIKE:
            return False
    return True


def looks_english(text):
    words = re.findall(r"[a-zA-Z]+", text.lower())
    english = sum(1 for word in words if word in ("the", "is", "of", "and", "to", "this", "returns", "return", "an", "for", "be"))
    portuguese = sum(1 for word in words if word in ("de", "que", "para", "com", "uma", "um", "os", "das", "dos", "nao"))
    return english >= 2 and portuguese == 0 and not re.search(r"[ãáàâçéêíóôõú]", text.lower())


# ---------------------------------------------------------------------------
# Texto em português
# ---------------------------------------------------------------------------

def tick(text):
    text = str(text)
    if "`" in text or "\n" in text or "\r" in text:
        raise Unsupported("código não cabe em crase")
    return f"`{text}`"


def cap(text):
    return text[:1].upper() + text[1:] if text[:1].isalpha() else text


def lower_first(text):
    if len(text) > 1 and text[0].isupper() and text[1].islower():
        return text[0].lower() + text[1:]
    return text


def prep(word, ref):
    """Contrai preposição e artigo: ``de`` + ``a função`` -> ``da função``."""
    table = {"de": ("da", "do"), "em": ("na", "no"), "por": ("pela", "pelo"), "a": ("à", "ao")}
    if ref.startswith("a "):
        return f"{table[word][0]} {ref[2:]}"
    if ref.startswith("o "):
        return f"{table[word][1]} {ref[2:]}"
    return f"{word} {ref}"


def plural(count, one, many):
    return f"{count} {one if count == 1 else many}"


def join_pt(items, conj="e"):
    items = list(items)
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + f" {conj} " + items[-1]


OPENERS = [("Oi! ", "keep"), ("Olá. ", "keep"), ("Bom dia! ", "keep"), ("Boa tarde. ", "keep"), ("Boa noite! ", "keep"),
           ("Uma dúvida: ", "lower"), ("Pergunta rápida: ", "lower"), ("Rapidinho: ", "lower"), ("Ei, ", "lower"),
           ("Opa, ", "lower"), ("Me ajuda numa coisa? ", "keep"), ("Estou estudando esse código. ", "keep"),
           ("Tô revisando um projeto. ", "keep"), ("Dúvida de leitura de código: ", "lower"), ("Por favor, ", "lower"),
           ("Preciso confirmar uma coisa. ", "keep"), ("Sem rodar nada, só lendo o fonte: ", "lower")]
CLOSERS = [" Obrigado!", " Valeu.", " Obrigada.", " Agradeço.", " Valeu!", " Pode ser objetivo.",
           " Cite o arquivo e a linha, por favor.", " Grato.", " Brigado!", " Se puder, com a linha."]


def dress_question(core, rng):
    text = core.strip()
    if rng.random() < 0.28:
        opener, mode = rng.choice(OPENERS)
        text = opener + (lower_first(text) if mode == "lower" else text)
    if rng.random() < 0.18 and text.endswith(("?", ".")):
        text += rng.choice(CLOSERS)
    return text


def sym_ref(symbol, rng, constructor=False):
    """(referência, pronome, artigo-com-preposição, particípio) com gênero coerente."""
    if constructor:
        return f"o construtor de `{symbol.cls.name}`", "ele", "do", "definido"
    if symbol.kind == "class":
        return rng.choice([f"a classe `{symbol.name}`", f"`{symbol.name}`"]), "ela", "da", "definida"
    if symbol.kind == "method":
        text = rng.choice([f"o método `{symbol.name}` da classe `{symbol.cls.name}`", f"o método `{symbol.qual}`", f"`{symbol.qual}`"])
        return text, "ele", "do", "definido"
    noun = "a função assíncrona" if symbol.is_async else "a função"
    return rng.choice([f"{noun} `{symbol.name}`", f"`{symbol.name}`", f"{noun} `{symbol.name}`"]), "ela", "da", "definida"


def place_q(source_file, rng):
    arq, mod = source_file.display, source_file.module
    if source_file.origin == "stdlib":
        return rng.choice([f"em `{arq}`", f"do módulo `{mod}`", f"no arquivo `{arq}`", f"(`{arq}`, biblioteca padrão)",
                           f"da stdlib, em `{arq}`", f"lá do `{mod}`", f"do `{mod}` da biblioteca padrão"])
    return rng.choice([f"em `{arq}`", f"do módulo `{mod}` do Brasa", f"aqui do projeto (`{arq}`)", f"no `{arq}` do Brasa",
                       f"no arquivo `{arq}`", f"do nosso `{arq}`"])


def origin_phrase(source_file, rng):
    if source_file.origin == "stdlib":
        return rng.choice([f"na biblioteca padrão do Python {PY_VERSION}", f"na stdlib do Python {PY_VERSION}"])
    return rng.choice(["no código do próprio Brasa", "aqui no Brasa"])


def place_a(source_file, line, rng, paren_ok=True):
    """Local para usar depois de um verbo: ``em `arq`, linha N``."""
    arq = source_file.display
    if rng.random() < 0.3:
        return rng.choice([f"em `{arq}`, linha {line}, {origin_phrase(source_file, rng)}",
                           f"em `{arq}:{line}`, {origin_phrase(source_file, rng)}"])
    options = [f"em `{arq}`, linha {line}", f"no arquivo `{arq}`, na linha {line}", f"em `{arq}:{line}`"]
    if paren_ok:
        options.append(f"em `{arq}` (linha {line})")
    return rng.choice(options)


def place_p(source_file, line, rng):
    """Local para ficar entre parênteses, sem parênteses aninhados."""
    arq = source_file.display
    return rng.choice([f"`{arq}`, linha {line}", f"`{arq}:{line}`", f"linha {line} de `{arq}`"])


def span(symbol):
    return f"linhas {symbol.line}–{symbol.end}" if symbol.end != symbol.line else f"linha {symbol.line}"


def maybe_note(rng, options, chance=0.15):
    return rng.choice(options) if rng.random() < chance else ""


# ---------------------------------------------------------------------------
# Construtores de exemplo (um por tipo de pergunta)
# ---------------------------------------------------------------------------

def describe_param(row, rng):
    name = row["name"]
    annotation = f", anotado como {tick(row['annotation'])}" if row["annotation"] else ""
    if row["kind"] == "vararg":
        return f"{tick('*' + name)}: recolhe argumentos posicionais extras{annotation}"
    if row["kind"] == "varkw":
        return f"{tick('**' + name)}: recolhe argumentos nomeados extras{annotation}"
    if row["default"] is not None and len(row["default"]) > 80:
        raise Unsupported("padrão longo demais")
    status = "obrigatório" if row["default"] is None else rng.choice(["opcional, padrão ", "opcional (padrão ", "padrão "]) + tick(row["default"])
    if status.startswith("opcional (padrão"):
        status += ")"
    extra = ""
    if row["kind"] == "posonly":
        extra = ", somente posicional"
    elif row["kind"] == "kwonly":
        extra = rng.choice([", só pode ser passado por nome", ", somente nomeado (keyword-only)"])
    return f"{tick(name)}: {status}{extra}{annotation}"


def implicit_note(symbol, facts, rng):
    first = facts["implicit"]
    if first is None:
        return ""
    if facts["flags"]["classmethod"]:
        return rng.choice([f"O primeiro, {tick(first)}, recebe a própria classe automaticamente (é um `@classmethod`).",
                           f"Por ser `@classmethod`, {tick(first)} é preenchido com a classe `{symbol.cls.name}`; você não o passa."])
    return rng.choice([f"O primeiro, {tick(first)}, é a instância de `{symbol.cls.name}` e é passado automaticamente.",
                       f"{tick(first)} é a própria instância: em `obj.{symbol.name}(...)` o Python o preenche sozinho.",
                       f"Ao chamar `{symbol.name}`, não conte {tick(first)}: é a instância, e entra automaticamente.",
                       f"{tick(first)} não é passado à mão: em `{symbol.cls.name}`, ele recebe o objeto em que `{symbol.name}` é chamado."])


def build_parameters(index, symbol, rng):
    if symbol.kind == "class":
        raise Unsupported
    constructor = symbol.name == "__init__"
    if constructor and not constructor_ok(symbol.cls):
        raise Unsupported
    if symbol.name.startswith("__") and not constructor:
        raise Unsupported
    facts = function_facts(symbol)
    if facts["flags"]["property"]:
        raise Unsupported
    source_file = symbol.file
    ref, pron, do, _ = sym_ref(symbol, rng, constructor)
    where = place_q(source_file, rng)
    if constructor:
        cls = symbol.cls.name
        core = rng.choice([f"Quais argumentos o construtor de `{cls}` {where} recebe?",
                           f"Pra instanciar `{cls}` {where}, quais parâmetros eu passo?",
                           f"O que preciso passar ao criar um objeto `{cls}` {where}?",
                           f"Quais são os parâmetros do `__init__` de `{cls}` {where}? Algum tem padrão?",
                           f"Me mostra a assinatura do construtor da classe `{cls}` {where}."])
    else:
        core = rng.choice([f"Quais parâmetros {ref} {where} recebe?",
                           f"Que argumentos {ref} {where} aceita, e quais têm valor padrão?",
                           f"Me diz a assinatura de `{symbol.qual}` {where}.",
                           f"Quais são os parâmetros de `{symbol.qual}` {where}? Algum tem default?",
                           f"{cap(ref)} {where} recebe quais argumentos?",
                           f"Preciso chamar `{symbol.qual}` {where}. Quais parâmetros são obrigatórios e quais são opcionais?",
                           f"vc sabe quais params `{symbol.qual}` {where} recebe?",
                           f"Assinatura de `{symbol.qual}` {where}?",
                           f"Estou lendo `{source_file.display}` e cheguei em `{symbol.qual}`. Que parâmetros {pron} recebe?",
                           f"Quais valores padrão {ref} {where} define para os parâmetros?",
                           f"Olhando `{source_file.display}`: o que `{symbol.qual}` espera receber como argumentos?",
                           f"Liste os parâmetros de `{symbol.qual}` (`{source_file.display}`) com seus defaults.",
                           f"Pode detalhar os argumentos aceitos {prep('por', ref)} {where}?"])
    params = facts["params"][1:] if facts["implicit"] is not None else facts["params"]
    loc = place_a(source_file, symbol.line, rng)
    signature = facts["signature"]
    lines = []
    if len(signature) <= 160:
        if constructor:
            lines.append(rng.choice([f"Ao criar `{symbol.cls.name}(...)`, os argumentos vão para `__init__`, {loc}: `{signature}`.",
                                     f"O construtor de `{symbol.cls.name}` é o `__init__` {loc}, com a assinatura `{signature}`."]))
        else:
            lines.append(rng.choice([f"A assinatura de `{symbol.qual}` ({place_p(source_file, symbol.line, rng)}) é `{signature}`.",
                                     f"`{symbol.qual}` está {loc}, com a assinatura `{signature}`.",
                                     f"{cap(loc)}, `{symbol.qual}` aparece assim: `{signature}`."]))
    else:
        if constructor:
            lines.append(f"O `__init__` de `{symbol.cls.name}` fica {loc}.")
        else:
            lines.append(rng.choice([f"`{symbol.qual}` está {loc}.", f"{'O método' if symbol.kind == 'method' else 'A função'} `{symbol.qual}` fica {loc}."]))
    if not params:
        extra = f" além de {tick(facts['implicit'])}" if facts["implicit"] else ""
        lines.append(rng.choice([f"`{symbol.name}` não recebe nenhum parâmetro{extra}.",
                                 f"Não há parâmetros{extra}: `{symbol.qual}` é chamad{'o' if pron == 'ele' else 'a'} sem argumentos.",
                                 f"A lista de parâmetros de `{symbol.name}` está vazia{extra}."]))
    else:
        described = [describe_param(row, rng) for row in params]
        required = [tick(row["name"]) for row in params if row["kind"] in ("posonly", "normal", "kwonly") and row["default"] is None]
        if len(params) <= 3 and rng.random() < 0.5:
            pieces = []
            for row, text in zip(params, described):
                name_part, _, detail = text.partition(": ")
                pieces.append(f"{name_part} ({detail})")
            lines.append(f"Parâmetros: {join_pt(pieces)}.")
        else:
            header = rng.choice([f"Parâmetros de `{symbol.name}`:", "Os parâmetros são:", "Em detalhe:", f"O que `{symbol.name}` aceita:",
                                 f"{plural(len(params), 'parâmetro', 'parâmetros')}:"])
            lines.append(header + "\n" + "\n".join(f"- {text}" for text in described))
        if required and len(params) > 2 and rng.random() < 0.5:
            lines.append(rng.choice([f"Obrigatórios: {join_pt(required)}.", f"Só {join_pt(required)} {'é obrigatório' if len(required) == 1 else 'são obrigatórios'}."]))
        elif not required and rng.random() < 0.6:
            lines.append(f"Todos têm padrão (ou são variádicos), então `{symbol.qual}` pode ser chamad{'o' if pron == 'ele' else 'a'} sem argumentos."
                         if not constructor else f"Todos têm padrão (ou são variádicos), então `{symbol.cls.name}()` funciona sem argumentos.")
    for text in opaque_decorators(facts["flags"])[:1]:
        lines.append(f"Atenção: `{symbol.name}` passa pelo decorador {tick('@' + text)}; o que descrevi é a assinatura escrita no `def`, e o decorador pode mudar a forma de chamar.")
    note = implicit_note(symbol, facts, rng) if facts["implicit"] and not constructor and params else ""
    if constructor and facts["implicit"] and params:
        note = rng.choice([f"O {tick(facts['implicit'])} do `__init__` é o `{symbol.cls.name}` recém-criado; você não o passa.",
                           f"Em `{symbol.cls.name}(...)`, o {tick(facts['implicit'])} é preenchido com o novo objeto automaticamente."])
    if note:
        lines.append(note)
    lines.append(maybe_note(rng, [f"Se quiser, monto um exemplo de chamada de `{symbol.name}`.",
                                  f"Posso mostrar também o que `{symbol.qual}` devolve."]))
    record_facts = {"params": facts["params"], "implicit": facts["implicit"], "signature": signature}
    return core, "\n".join(line for line in lines if line), record_facts, ["engine.parameters==ast", "defaults:ast.unparse"]


def build_returns(index, symbol, rng):
    if symbol.kind == "class" or symbol.name == "__init__":
        raise Unsupported
    facts = function_facts(symbol)
    source_file = symbol.file
    ref, pron, do, defined = sym_ref(symbol, rng)
    where = place_q(source_file, rng)
    core = rng.choice([f"O que {ref} {where} retorna?",
                       f"Qual é o retorno de `{symbol.qual}` {where}?",
                       f"`{symbol.qual}` {where} devolve o quê?",
                       f"O que sai de uma chamada a `{symbol.qual}` {where}?",
                       f"Que valor {ref} {where} devolve?",
                       f"Estou usando `{symbol.qual}` ({source_file.display}). O que {pron} retorna?",
                       f"retorno de `{symbol.qual}` {where}?",
                       f"Pode me dizer o que `{symbol.qual}` {where} devolve, pelo código?",
                       f"Qual o tipo de retorno de {ref} {where}?"])
    loc = place_a(source_file, symbol.line, rng)
    lines = []
    name = tick(symbol.qual)
    obj = "lo" if pron == "ele" else "la"
    annotation = facts["annotation"]
    body = body_without_docstring(symbol.node)
    opaque = opaque_decorators(facts["flags"])
    managers = [text for text in opaque if decorator_last(text) in CONTEXT_MANAGERS]
    if managers and (not facts["yields"] or len(opaque) > 1):
        raise Unsupported
    if managers:
        yields = facts["yields"]
        word = "assíncrono (para `async with`)" if decorator_last(managers[0]) == "asynccontextmanager" else "(para usar com `with`)"
        lines.append(f"{name} ({place_p(source_file, symbol.line, rng)}) é decorad{'o' if pron == 'ele' else 'a'} com {tick('@' + managers[0])}: "
                     f"chamá-{obj} devolve um gerenciador de contexto {word}, não o valor direto.")
        shown = [(tick(value) if value is not None else "`None`") + f" (linha {line})" for value, line, kind in yields if kind == "yield"]
        if len(shown) == 1:
            lines.append(f"O valor do `yield`, {shown[0]}, é o que o `as` recebe dentro do bloco.")
        else:
            lines.append(f"O corpo tem {len(yields)} `yield`: {join_pt(shown) or 'só com yield from'}; o valor produzido é o que o `as` recebe.")
        opaque = []
    elif facts["yields"]:
        yields = facts["yields"]
        if facts["async"]:
            lines.append(f"{name} ({place_p(source_file, symbol.line, rng)}) é um gerador assíncrono: é `async def` com `yield`, então a chamada devolve um gerador assíncrono, consumido com `async for`.")
        else:
            lines.append(rng.choice([f"{name} ({place_p(source_file, symbol.line, rng)}) é {'um método gerador' if pron == 'ele' else 'uma função geradora'}: o corpo usa `yield`, então chamá-{obj} devolve um gerador, e os valores saem sob demanda.",
                                     f"Como o corpo de {name} tem `yield`, a chamada não executa o corpo de imediato: devolve um gerador. O código está {loc}."]))
        shown = []
        for value, line, word in yields[:5]:
            shown.append(f"`{word}`" + (f" {tick(value)}" if value is not None else " sem valor") + f" (linha {line})")
        lines.append(f"{'O `yield` do corpo' if len(yields) == 1 else 'Os `yield` do corpo'}: {join_pt(shown)}" + (f", e mais {len(yields) - 5}." if len(yields) > 5 else "."))
        if annotation:
            lines.append(f"A anotação de retorno é {tick(annotation)}.")
    else:
        if facts["async"]:
            lines.append(rng.choice([f"{name} é `async def` ({place_p(source_file, symbol.line, rng)}): chamá-{obj} devolve uma corrotina, e o valor descrito abaixo é o que se obtém com `await`.",
                                     f"Atenção: {name}, {loc}, é {'assíncrono' if pron == 'ele' else 'assíncrona'}; a chamada devolve uma corrotina e o resultado vem com `await`."]))
        else:
            lines.append(rng.choice([f"{name} está {loc}.", f"Olhei {name} {loc}.", f"{cap(sym_ref(symbol, rng)[0])} fica {loc}."]))
        if annotation:
            lines.append(rng.choice([f"A anotação de retorno é {tick(annotation)}.", f"Pela anotação, o retorno é {tick(annotation)}."]))
        returns = facts["returns"]
        if returns:
            unique = []
            for value, line in returns:
                if value not in [item[0] for item in unique]:
                    unique.append((value, line))
            shown = [f"{tick(value)} (linha {line})" for value, line in unique[:6]]
            if len(returns) == 1:
                lines.append(rng.choice([f"O único `return` com valor devolve {shown[0]}.", f"Há um `return`, que devolve {shown[0]}."]))
            else:
                text = f"Há {len(returns)} `return` com valor; as expressões devolvidas são: {join_pt(shown)}"
                lines.append(text + (f", além de mais {len(unique) - 6} expressões diferentes." if len(unique) > 6 else "."))
            if facts["bare_returns"]:
                lines.append(f"Também há {plural(facts['bare_returns'], '`return` sem valor, que devolve', '`return` sem valor, que devolvem')} `None`.")
        elif len(body) == 1 and isinstance(body[0], ast.Raise) and body[0].exc is not None:
            lines.append(f"O corpo de `{symbol.name}` só levanta {tick(ast.unparse(body[0].exc))} (linha {body[0].lineno}); não há `return`, então a chamada nunca devolve valor.")
        elif len(body) == 1 and (isinstance(body[0], ast.Pass) or (isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and body[0].value.value is Ellipsis)):
            stub = "pass" if isinstance(body[0], ast.Pass) else "..."
            lines.append(f"O corpo de `{symbol.name}` é só `{stub}`, sem `return`; chamá-{obj} devolve `None`.")
            if facts["flags"]["abstract"]:
                lines.append(f"{cap(pron)} está marcad{'o' if pron == 'ele' else 'a'} com `@abstractmethod`, o sinal de que subclasses devem implementar `{symbol.name}`.")
        else:
            if facts["bare_returns"]:
                lines.append(f"`{symbol.name}` só tem `return` sem valor; ao terminar sem exceção, devolve `None`.")
            else:
                lines.append(rng.choice([f"Não há `return` com valor no corpo de `{symbol.name}`; ao terminar sem exceção, a chamada devolve `None`.",
                                         f"`{symbol.name}` não tem nenhum `return` com valor, então o resultado da chamada é `None` (se não houver exceção)."]))
    if facts["flags"]["property"]:
        lines.append(f"Como `{symbol.name}` é uma property, esse valor vem de ler `obj.{symbol.name}`, sem parênteses.")
    for text in opaque[:1]:
        lines.append(f"Atenção: `{symbol.name}` é decorad{'o' if pron == 'ele' else 'a'} com {tick('@' + text)}, que pode substituir esse comportamento; o retorno acima é o do corpo como está escrito.")
    facts_out = {"annotation": annotation, "returns": facts["returns"], "bare_returns": facts["bare_returns"],
                 "yields": [[value, line] for value, line, _ in facts["yields"]], "async": facts["async"],
                 "decorators": facts["decorators"]}
    return core, "\n".join(lines), facts_out, ["engine.returns==ast[:4]", "ast.own_scope_returns"]


def first_paragraph(doc):
    paragraphs = re.split(r"\n\s*\n", doc.strip())
    return paragraphs[0].strip(), len(paragraphs) > 1


def quote_block(text):
    return "\n".join("> " + line if line.strip() else ">" for line in text.split("\n"))


def build_docstring(index, symbol, rng):
    if symbol.name.startswith("__") and symbol.name != "__init__":
        raise Unsupported
    if symbol.kind != "class":
        function_facts(symbol)  # mesmas exclusões (sobrecargas, nomes repetidos)
    source_file = symbol.file
    ref, pron, do, _ = sym_ref(symbol, rng)
    where = place_q(source_file, rng)
    core = rng.choice([f"O que diz a docstring {prep('de', ref)} {where}?",
                       f"{cap(ref)} {where} tem docstring? O que ela diz?",
                       f"Qual é a documentação de `{symbol.qual}` {where}?",
                       f"Me mostra a docstring de `{symbol.qual}` {where}.",
                       f"O que a documentação embutida de `{symbol.qual}` ({source_file.display}) explica?",
                       f"Lê pra mim a docstring de `{symbol.qual}` {where}.",
                       f"Como {ref} {where} é descrit{'o' if pron == 'ele' else 'a'} na própria docstring?"])
    doc = ast.get_docstring(symbol.node)
    loc = place_a(source_file, symbol.line, rng)
    if not doc:
        if rng.random() > 0.3:
            raise Unsupported("poucas respostas negativas")
        answer = rng.choice([f"`{symbol.qual}` não tem docstring ({place_p(source_file, symbol.line, rng)}). Para saber o que faz, é preciso ler o corpo, nas {span(symbol)}.",
                             f"Não há docstring em `{symbol.qual}`, {loc}. A definição ocupa as {span(symbol)}; posso explicar o código se quiser."])
        return core, answer, {"docstring": None, "quoted": None}, ["engine.docstring==ast.get_docstring"]
    quoted, partial = first_paragraph(doc)
    if len(quoted) > 700:
        raise Unsupported("docstring longa")
    english = looks_english(quoted)
    tag = rng.choice([", em inglês", ", no original em inglês"]) if english and rng.random() < 0.5 else ""
    if partial:
        lead = rng.choice([f"A docstring de `{symbol.qual}` ({place_p(source_file, symbol.line, rng)}) começa assim{tag}:",
                           f"{cap(loc)}, a documentação de `{symbol.qual}` abre com este parágrafo{tag}:"])
    else:
        lead = rng.choice([f"A docstring de `{symbol.qual}` ({place_p(source_file, symbol.line, rng)}) diz{tag}:",
                           f"{cap(loc)}, `{symbol.qual}` traz esta docstring{tag}:",
                           f"Esta é a docstring completa de `{symbol.qual}`, {loc}{tag}:"])
    parts = [lead, "", quote_block(quoted)]
    if partial:
        parts += ["", rng.choice([f"Esse é só o primeiro parágrafo; a docstring de `{symbol.name}` tem {plural(len(doc.splitlines()), 'linha', 'linhas')} ao todo.",
                                  f"A docstring de `{symbol.name}` continua depois disso ({plural(len(doc.splitlines()), 'linha', 'linhas')} no total)."])]
    return core, "\n".join(parts), {"docstring_sha1": hashlib.sha1(doc.encode()).hexdigest(), "quoted": quoted, "partial": partial}, ["engine.docstring==ast.get_docstring[:500]", "quote⊂docstring"]


def build_module_docstring(index, source_file, rng):
    tree = source_file.tree
    doc = ast.get_docstring(tree)
    mod, arq = source_file.module, source_file.display
    core = rng.choice([f"Pra que serve o módulo `{mod}`, segundo a docstring dele?",
                       f"O que diz a docstring do arquivo `{arq}`?",
                       f"Qual é a descrição que o próprio `{arq}` dá de si mesmo?",
                       f"O módulo `{mod}` tem docstring? O que ela diz?",
                       f"Me mostra o docstring de módulo de `{arq}`."])
    if not doc:
        functions = sum(1 for item in tree.body if isinstance(item, FUNCS))
        classes = sum(1 for item in tree.body if isinstance(item, ast.ClassDef))
        answer = (f"`{arq}` não tem docstring de módulo, então o arquivo não traz uma descrição própria. "
                  f"No nível do módulo ele define {plural(classes, 'classe', 'classes')} e {plural(functions, 'função', 'funções')}; posso listá-las.")
        return core, answer, {"docstring": None, "classes": classes, "functions": functions}, ["ast.get_docstring(module) is None"], 1
    quoted, partial = first_paragraph(doc)
    if len(quoted) > 900:
        raise Unsupported
    line = tree.body[0].lineno
    english = looks_english(quoted)
    tag = ", em inglês" if english and rng.random() < 0.4 else ""
    lead = rng.choice([f"Segundo a própria docstring de `{arq}` (linha {line}){tag}",
                       f"A docstring do módulo `{mod}`, na linha {line} de `{arq}`, diz{tag}",
                       f"`{arq}` se descreve assim na docstring da linha {line}{tag}"])
    lead += rng.choice([", no primeiro parágrafo:", " — primeiro parágrafo:"]) if partial else ":"
    parts = [lead, "", quote_block(quoted)]
    if partial:
        parts += ["", f"O texto completo continua por mais {plural(len(doc.splitlines()) - len(quoted.splitlines()), 'linha', 'linhas')}."]
    return core, "\n".join(parts), {"quoted": quoted, "partial": partial, "line": line}, ["ast.get_docstring(module)", "quote⊂docstring"], line


def direct_definitions(source_file):
    return [("class" if isinstance(item, ast.ClassDef) else "function", item.name, item.lineno)
            for item in source_file.tree.body if isinstance(item, SCOPES)]


def build_module_definitions(index, source_file, variant, rng):
    defs = direct_definitions(source_file)
    engine_top = [row for row in source_file.observations["symbols"] if "." not in row["qualified_name"]]
    engine_lines = [(row["kind"], row["name"], row["line"]) for row in engine_top]
    if any(item not in engine_lines for item in defs):
        raise Unsupported("motor e ast divergem")
    conditional = len(engine_top) - len(defs)
    if conditional < 0:
        raise Unsupported
    mod, arq = source_file.module, source_file.display
    classes = [item for item in defs if item[0] == "class"]
    functions = [item for item in defs if item[0] == "function"]
    if variant == "all":
        core = rng.choice([f"Quais funções e classes o módulo `{mod}` define?", f"O que `{arq}` define no nível do módulo?",
                           f"Lista as definições de topo de `{arq}`.", f"Que classes e funções existem em `{arq}`?",
                           f"me passa as funções e classes de `{mod}`"])
        chosen = defs
    elif variant == "classes":
        core = rng.choice([f"Quais classes estão definidas em `{arq}`?", f"O módulo `{mod}` declara quais classes?",
                           f"Tem alguma classe em `{arq}`? Quais?", f"Quais são as classes de `{mod}`?"])
        chosen = classes
    elif variant == "functions":
        core = rng.choice([f"Quais funções de nível de módulo `{arq}` tem?", f"Me lista as funções soltas (fora de classes) de `{mod}`.",
                           f"Que funções o arquivo `{arq}` define fora de classes?"])
        chosen = functions
    elif variant == "public":
        core = rng.choice([f"Quais nomes públicos (sem `_` no começo) `{arq}` define no topo?",
                           f"Das definições de `{mod}`, quais são públicas, isto é, não começam com `_`?"])
        chosen = [item for item in defs if not item[1].startswith("_")]
    else:  # count
        core = rng.choice([f"Quantas funções e quantas classes `{arq}` define no nível do módulo?",
                           f"Quantas classes e funções de topo tem o módulo `{mod}`?"])
        chosen = defs
    if len(chosen) > 40 and variant != "count":
        raise Unsupported("lista grande demais")
    label = {"all": "definições", "classes": "classes", "functions": "funções", "public": "definições públicas", "count": "definições"}[variant]
    lines = []
    if variant == "count":
        lines.append(rng.choice([f"No nível do módulo, `{arq}` define {plural(len(classes), 'classe', 'classes')} e {plural(len(functions), 'função', 'funções')}.",
                                 f"`{mod}` tem {plural(len(classes), 'classe', 'classes')} e {plural(len(functions), 'função', 'funções')} diretamente no corpo do módulo ({arq})."]))
        if 0 < len(defs) <= 12:
            lines.append(rng.choice(["São elas: ", "Nomes: ", f"Em `{source_file.module}`: "]) + join_pt(f"`{name}` (linha {line})" for _, name, line in defs) + ".")
    elif not chosen:
        empty = {"classes": f"`{arq}` não define nenhuma classe no nível do módulo.",
                 "functions": f"`{arq}` não define nenhuma função no nível do módulo (fora de classes).",
                 "public": f"Todas as definições de topo de `{arq}` começam com `_`; não há nomes públicos ali.",
                 "all": f"`{arq}` não tem nenhum `def` ou `class` diretamente no corpo do módulo."}[variant]
        lines.append(empty)
        if variant == "classes" and functions:
            lines.append(f"Ele tem, porém, {plural(len(functions), 'função', 'funções')}.")
        if variant == "functions" and classes:
            lines.append(f"Há, sim, {plural(len(classes), 'classe', 'classes')} definidas ali.")
    else:
        singular = {"all": "definição", "classes": "classe", "functions": "função", "public": "definição pública"}[variant]
        head = rng.choice([f"No nível do módulo, `{arq}` define {plural(len(chosen), singular, label)}:",
                           f"`{mod}` (`{arq}`) tem estas {len(chosen)} {label} no topo do arquivo:",
                           f"Em `{arq}` encontrei {plural(len(chosen), 'item', 'itens')} ({label}):"])
        if len(chosen) == 1:
            head = f"`{arq}` tem uma única {'classe' if chosen[0][0] == 'class' else 'função'} nesse recorte:" if variant != "public" else f"`{arq}` tem um único nome público no topo:"
        rows = []
        for kind, name, line in chosen:
            tag = "" if variant in ("classes", "functions") else (" — classe" if kind == "class" else " — função")
            rows.append(f"- `{name}` (linha {line}){tag}")
        lines.append(head + "\n" + "\n".join(rows))
    if conditional > 0 and variant != "public":
        lines.append(rng.choice([f"Além disso, há {plural(conditional, 'definição', 'definições')} dentro de blocos no nível do módulo (como `if` ou `try`), que não entraram na contagem.",
                                 f"Fora isso, {plural(conditional, 'definição fica', 'definições ficam')} dentro de blocos condicionais (`if`/`try`) no topo do arquivo."]))
    facts = {"variant": variant, "defs": [list(item) for item in chosen], "classes": len(classes), "functions": len(functions), "conditional": conditional}
    return core, "\n".join(lines), facts, ["engine.top_level⊇ast.module.body", "counts:ast"]


def build_class_methods(index, symbol, rng):
    if symbol.kind != "class":
        raise Unsupported
    node = symbol.node
    methods = []
    for item in node.body:
        if isinstance(item, FUNCS):
            flags = decorator_flags(decorator_names(item))
            tags = [tag for tag, on in (("property", flags["property"] or flags["accessor"]), ("classmethod", flags["classmethod"]),
                                         ("staticmethod", flags["staticmethod"]), ("async", isinstance(item, ast.AsyncFunctionDef))) if on]
            methods.append((item.name, item.lineno, tags))
    if len(methods) > 40:
        raise Unsupported
    source_file = symbol.file
    where = place_q(source_file, rng)
    core = rng.choice([f"Quais métodos a classe `{symbol.name}` {where} tem?", f"Que métodos `{symbol.name}` {where} implementa?",
                       f"Lista os métodos de `{symbol.name}` (`{source_file.display}`).", f"A classe `{symbol.name}` {where} define quais métodos?",
                       f"quais metodos tem a `{symbol.name}` {where}?", f"Me dá um panorama dos métodos definidos em `{symbol.name}` {where}."])
    loc = place_a(source_file, symbol.line, rng)
    bases = [ast.unparse(base) for base in node.bases]
    lines = []
    if not methods:
        lines.append(rng.choice([f"`{symbol.name}` ({place_p(source_file, symbol.line, rng)}) não tem nenhum `def` no próprio corpo.",
                                 f"No corpo de `{symbol.name}`, {loc}, não há métodos definidos."]))
        if bases:
            lines.append(f"Tudo o que ela oferece de métodos vem das bases: {join_pt(tick(base) for base in bases)}.")
    else:
        head = rng.choice([f"`{symbol.name}` ({place_p(source_file, symbol.line, rng)}) define {plural(len(methods), 'método', 'métodos')} no próprio corpo:",
                           f"{cap(loc)}, a classe `{symbol.name}` declara {plural(len(methods), 'método', 'métodos')}:"])
        rows = [f"- `{name}` (linha {line})" + (f" — {', '.join(tags)}" if tags else "") for name, line, tags in methods]
        lines.append(head + "\n" + "\n".join(rows))
        if bases:
            lines.append(rng.choice([f"Métodos herdados de {join_pt(tick(base) for base in bases)} não entram nessa lista.",
                                     f"A lista não inclui o que `{symbol.name}` herda de {join_pt(tick(base) for base in bases)}."]))
    facts = {"methods": [[name, line, tags] for name, line, tags in methods], "bases": bases}
    return core, "\n".join(lines), facts, ["ast.ClassDef.body", "engine.class_row"]


def build_class_bases(index, symbol, rng):
    if symbol.kind != "class":
        raise Unsupported
    node = symbol.node
    bases = [ast.unparse(base) for base in node.bases]
    keywords = [ast.unparse(item) for item in node.keywords]
    source_file = symbol.file
    where = place_q(source_file, rng)
    core = rng.choice([f"De quais classes `{symbol.name}` {where} herda?", f"Qual é a classe base de `{symbol.name}` {where}?",
                       f"`{symbol.name}` {where} estende alguma classe?", f"Quais são as superclasses diretas de `{symbol.name}` {where}?",
                       f"A classe `{symbol.name}` ({source_file.display}) herda de quem?", f"herança de `{symbol.name}` {where}?"])
    loc = place_a(source_file, symbol.line, rng)
    lines = []
    if bases:
        lines.append(rng.choice([f"`{symbol.name}` ({place_p(source_file, symbol.line, rng)}) herda de {join_pt(tick(base) for base in bases)}.",
                                 f"{cap(loc)}, a declaração de `{symbol.name}` lista {'a base' if len(bases) == 1 else 'as bases'} {join_pt(tick(base) for base in bases)}."]))
    else:
        lines.append(rng.choice([f"`{symbol.name}` ({place_p(source_file, symbol.line, rng)}) não declara base explícita, então herda diretamente de `object`.",
                                 f"Nenhuma: a classe `{symbol.name}`, {loc}, é declarada sem bases, o que no Python 3 significa herdar só de `object`."]))
    if keywords:
        lines.append(f"A declaração também passa {join_pt(tick(item) for item in keywords)}.")
    if node.decorator_list:
        lines.append(f"Ela é decorada com {join_pt(tick('@' + ast.unparse(item)) for item in node.decorator_list)}.")
    header = symbol.row["signature"]
    if len(header) <= 140 and rng.random() < 0.5:
        lines.append(rng.choice([f"Cabeçalho: {tick(header)}.", f"A linha {symbol.line} é {tick(header)}.", f"A declaração completa é {tick(header)}."]))
    return core, "\n".join(lines), {"bases": bases, "keywords": keywords}, ["ast.ClassDef.bases", "engine.signature==source_line"]


def build_line_lookup(index, symbol, rng):
    source_file = symbol.file
    arq, line = source_file.display, symbol.line
    core = rng.choice([f"O que é definido na linha {line} de `{arq}`?", f"Na linha {line} do arquivo `{arq}` começa o quê?",
                       f"Tem alguma função ou classe começando na linha {line} de `{arq}`?", f"`{arq}`, linha {line}: o que tem ali?",
                       f"Que definição aparece em `{arq}:{line}`?"])
    header = symbol.row["signature"]
    if symbol.kind == "class":
        what = f"a classe `{symbol.name}`"
    elif symbol.kind == "method":
        what = f"o método `{symbol.name}` da classe `{symbol.cls.name}` (que começa na linha {symbol.cls.lineno})"
    else:
        what = f"a função {'assíncrona ' if symbol.is_async else ''}`{symbol.name}`"
    lines = [rng.choice([f"Na linha {line} de `{arq}` começa a definição {prep('de', what)}: {tick(header)}.",
                         f"A linha {line} de `{arq}` abre {what}: {tick(header)}."])]
    lines.append(rng.choice([f"A definição vai até a linha {symbol.end}.", f"Ela termina na linha {symbol.end}."]) if symbol.end != line
                 else "É uma definição de uma linha só.")
    decorators = symbol.node.decorator_list
    if decorators:
        lines.append(f"Logo acima há {'o decorador' if len(decorators) == 1 else 'os decoradores'} "
                     + join_pt(f"{tick('@' + ast.unparse(item))} (linha {item.lineno})" for item in decorators) + ".")
    return core, "\n".join(lines), {"kind": symbol.kind, "qual": symbol.qual, "end_line": symbol.end, "header": header}, ["engine.signature==source_line", "ast.lineno"]


def build_calls(index, symbol, rng):
    if symbol.kind == "class":
        raise Unsupported
    function_facts(symbol)
    calls = own_calls(symbol.node)
    engine_calls = symbol.row["calls"]
    if engine_calls != calls[:16]:
        raise Unsupported
    if any(len(item) > 90 for item in engine_calls):
        raise Unsupported("alvo de chamada longo (o motor trunca em 120 caracteres)")
    source_file = symbol.file
    ref, pron, _, _ = sym_ref(symbol, rng)
    where = place_q(source_file, rng)
    core = rng.choice([f"Quais funções {ref} {where} chama?", f"Que chamadas aparecem no corpo de `{symbol.qual}` {where}?",
                       f"De quais funções `{symbol.qual}` {where} depende diretamente?", f"O que `{symbol.qual}` ({source_file.display}) invoca por dentro?",
                       f"Lista as chamadas feitas dentro {prep('de', ref)} {where}."])
    lines = []
    if not calls:
        lines.append(rng.choice([f"`{symbol.qual}` ({span(symbol)} de `{source_file.display}`) não faz nenhuma chamada de função no próprio corpo.",
                                 f"Nenhuma: o corpo de `{symbol.qual}`, em `{source_file.display}` ({span(symbol)}), não contém chamadas."]))
    else:
        shown = [tick(item) for item in engine_calls]
        if len(shown) == 1:
            lines.append(rng.choice([f"Pela análise estática de `{source_file.display}` ({span(symbol)}), o corpo de `{symbol.qual}` faz uma única chamada: {shown[0]}.",
                                     f"Só uma: lendo `{symbol.qual}` em `{source_file.display}` ({span(symbol)}), a única chamada no corpo é {shown[0]}."]))
        elif len(shown) <= 4 and rng.random() < 0.5:
            lines.append(rng.choice([f"Pela análise estática de `{source_file.display}` ({span(symbol)}), o corpo de `{symbol.qual}` chama {join_pt(shown)}.",
                                     f"Lendo o fonte de `{symbol.qual}` em `{source_file.display}` ({span(symbol)}), aparecem estas chamadas, na ordem: {join_pt(shown)}."]))
        else:
            lead = rng.choice([f"Pela análise estática de `{source_file.display}` ({span(symbol)}), o corpo de `{symbol.qual}` chama:",
                               f"Lendo o fonte de `{symbol.qual}` em `{source_file.display}` ({span(symbol)}), aparecem estas chamadas, na ordem:"])
            lines.append(lead + "\n" + "\n".join(f"- {item}" for item in shown))
        if len(calls) > 16:
            lines.append(f"Listei as 16 primeiras; ao todo são {len(calls)} alvos de chamada distintos.")
        lines.append(maybe_note(rng, [f"É leitura do código de `{symbol.name}`, sem executá-lo: chamadas indiretas (por `getattr`, callbacks etc.) não aparecem como nomes.",
                                      f"Isso vem do código-fonte de `{symbol.name}`, sem rodar nada."], 0.3))
    return core, "\n".join(line for line in lines if line), {"calls": engine_calls, "total": len(calls)}, ["engine.calls==ast[:16]"]


def import_modules(node):
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    base = "." * node.level + (node.module or "")
    return [base]


def build_imports(index, source_file, variant, rng):
    engine = source_file.observations["imports"]
    nodes = sorted((item for item in ast.walk(source_file.tree) if isinstance(item, (ast.Import, ast.ImportFrom))), key=lambda item: (item.lineno, item.col_offset))
    if sorted((item["line"], item["text"]) for item in engine) != [(item.lineno, ast.unparse(item)[:240]) for item in nodes]:
        raise Unsupported("motor e ast divergem")
    arq, mod = source_file.display, source_file.module
    top_ids = [id(item) for item in source_file.tree.body]
    if variant == "check":
        imported = []
        for item in nodes:
            imported += [name.split(".")[0] for name in import_modules(item) if not name.startswith(".")]
        candidates = sorted({name for name in imported})
        common = ["re", "os", "sys", "json", "typing", "pathlib", "itertools", "functools", "collections", "subprocess", "time", "math", "dataclasses", "logging", "random"]
        absent = [name for name in common if name not in imported]
        if candidates and (not absent or rng.random() < 0.6):
            target = rng.choice(candidates)
        elif absent:
            target = rng.choice(absent)
        else:
            raise Unsupported
        hits = [item for item in nodes if any(name == target or name.startswith(target + ".") for name in import_modules(item))]
        core = rng.choice([f"O arquivo `{arq}` importa `{target}`?", f"`{mod}` usa import de `{target}`?",
                           f"Tem algum `import {target}` (ou `from {target} ...`) em `{arq}`?"])
        if hits:
            rows = join_pt(f"{tick(ast.unparse(item))} (linha {item.lineno})" for item in hits[:6])
            answer = rng.choice([f"Sim. `{arq}` importa `{target}` em: {rows}.", f"Importa, sim: {rows}, em `{arq}`."])
            if len(hits) > 6:
                answer += f" Há mais {len(hits) - 6} ocorrências."
        else:
            answer = rng.choice([f"Não. Nenhuma instrução de import em `{arq}` traz o módulo `{target}`.",
                                 f"Não importa: procurei todos os `import`/`from ... import` de `{arq}` e nenhum é de `{target}`."])
        facts = {"variant": "check", "module": target, "hits": [[item.lineno, ast.unparse(item)] for item in hits]}
        return core, answer, facts, ["engine.imports==ast.Import*"]
    if variant == "top":
        chosen = [item for item in nodes if id(item) in top_ids]
        core = rng.choice([f"Quais imports ficam no topo de `{arq}`, fora de funções e blocos?", f"O que `{mod}` importa diretamente no corpo do módulo?"])
    else:
        chosen = nodes
        core = rng.choice([f"O que o arquivo `{arq}` importa?", f"Quais são os imports de `{mod}`?", f"Me lista as instruções de import de `{arq}`.",
                           f"Quais módulos `{arq}` traz com `import`?", f"que imports tem no `{arq}`?"])
    if not chosen:
        answer = f"`{arq}` não tem nenhuma instrução de import {'diretamente no corpo do módulo' if variant == 'top' else ''}".rstrip() + "."
        if variant == "top" and nodes:
            answer += f" Os {plural(len(nodes), 'import existente fica', 'imports existentes ficam')} dentro de funções ou blocos."
        return core, answer, {"variant": variant, "imports": [], "total": len(nodes)}, ["engine.imports==ast.Import*"]
    shown = chosen[:25]
    head = rng.choice([f"`{arq}` tem {plural(len(chosen), 'instrução de import', 'instruções de import')}" + (" diretamente no corpo do módulo:" if variant == "top" else ":"),
                       f"Encontrei {plural(len(chosen), 'import', 'imports')} em `{arq}`" + (" no nível do módulo:" if variant == "top" else ":")])
    rows = [f"- linha {item.lineno}: {tick(ast.unparse(item))}" for item in shown]
    lines = [head + "\n" + "\n".join(rows)]
    if len(chosen) > 25:
        lines.append(f"Mostrei os 25 primeiros; há mais {len(chosen) - 25}.")
    nested = len(nodes) - len([item for item in nodes if id(item) in top_ids])
    if variant == "all" and nested:
        lines.append(f"Dess{'e' if len(chosen) == 1 else 'es'}, {plural(nested, 'fica', 'ficam')} dentro de funções, classes ou blocos (import tardio ou condicional).")
    if variant == "top" and nested:
        lines.append(f"Há mais {plural(nested, 'import', 'imports')} dentro de funções, classes ou blocos `if`/`try`.")
    facts = {"variant": variant, "imports": [[item.lineno, ast.unparse(item)] for item in chosen], "total": len(nodes)}
    return core, "\n".join(lines), facts, ["engine.imports==ast.Import*"]


def build_where_defined(index, name, scope, rng):
    defs = index.module_level.get(name, [])
    if len(defs) != index.module_level_all.get(name, 0):
        raise Unsupported("alguma definição com esse nome não passou na conferência")
    if scope == "brasa":
        defs = [item for item in defs if item.file.origin == "brasa"]
    if not defs or len(defs) > 6:
        raise Unsupported
    kinds = [item.kind for item in defs]
    if all(kind == "class" for kind in kinds):
        noun, done = "a classe", "definida"
    elif all(kind == "function" for kind in kinds):
        noun, done = "a função", "definida"
    else:
        noun, done = "o nome", "definido"
    if scope == "brasa":
        core = rng.choice([f"Em que arquivo do Brasa fica {noun} `{name}`?", f"Onde, no código do projeto, está {done} {noun} `{name}`?",
                           f"Qual módulo de `python/` define `{name}`?", f"Procura pra mim onde `{name}` é {done} no Brasa."])
        scope_text = rng.choice(["No índice dos módulos `python/*.py` do Brasa", "Olhando só o índice do `python/` do projeto"])
    else:
        core = rng.choice([f"Em qual módulo está {done} {noun} `{name}`?", f"Onde fica {noun} `{name}`?", f"Qual arquivo define `{name}`?",
                           f"De onde vem {noun} `{name}`? Em que módulo é {done}?",
                           f"Preciso achar a definição de `{name}`. Em que módulo está?", f"`{name}` é definid{'o' if noun == 'o nome' else 'a'} onde?"])
        scope_text = rng.choice([f"No índice que consultei (stdlib do Python {PY_VERSION} sem testes, mais os módulos de `python/` do Brasa)",
                                 f"Pelo índice estático (biblioteca padrão do Python {PY_VERSION}, fora testes e demos, e o `python/` do projeto)"])
    def describe(item):
        kind = "classe" if item.kind == "class" else ("função assíncrona" if item.is_async else "função")
        origin = "stdlib" if item.file.origin == "stdlib" else "Brasa"
        return f"`{item.file.display}`, linha {item.line} — {kind}, módulo `{item.file.module}` ({origin})"
    if len(defs) == 1:
        item = defs[0]
        kind = "a classe" if item.kind == "class" else "a função"
        lines = [rng.choice([f"{cap(kind)} `{name}` está definida em `{item.file.display}`, linha {item.line} (módulo `{item.file.module}`).",
                             f"`{name}` é {'uma classe' if item.kind == 'class' else 'uma função'} do módulo `{item.file.module}`: fica em `{item.file.display}`, na linha {item.line}."])]
        lines.append(f"{scope_text}, essa é a única definição de `{name}` no nível de módulo.")
        if item.file.origin == "stdlib" and rng.random() < 0.5:
            lines.append(rng.choice([f"Para usar: `from {item.file.module} import {name}`.", f"Importe com `from {item.file.module} import {name}`."]))
    else:
        lines = [f"{scope_text}, há {len(defs)} definições de `{name}` no nível de módulo:"]
        lines[0] += "\n" + "\n".join(f"- {describe(item)}" for item in defs)
        lines.append(rng.choice([f"Qual `{name}` interessa depende do módulo que você importa.", f"Se você me disser o contexto, digo qual `{name}` está em jogo."]))
    facts = {"name": name, "scope": scope, "defs": [[item.file.origin, item.file.display, item.line, item.kind, item.file.sha1] for item in defs]}
    return core, "\n".join(lines), facts, ["engine.symbols", "ast.module.body", "index:stdlib+python/"], defs[0]


# ---- explicação literal de funções curtas ---------------------------------

def code_of(node, limit=90):
    text = ast.unparse(node)
    if len(text) > limit:
        raise Unsupported("expressão longa")
    return tick(text)


def return_clause(value):
    if value is None:
        return "encerra sem valor (devolve `None`)"
    text = code_of(value)
    if isinstance(value, ast.Constant):
        return f"devolve a constante {text}"
    if isinstance(value, ast.Name):
        return f"devolve o valor de {text}"
    if isinstance(value, ast.Attribute):
        return f"devolve o atributo {text}"
    if isinstance(value, ast.Call):
        return f"devolve o resultado da chamada {text}"
    if isinstance(value, ast.Compare):
        return f"devolve o resultado da comparação {text}"
    if isinstance(value, ast.BoolOp):
        return f"devolve o resultado da expressão lógica {text}"
    if isinstance(value, ast.UnaryOp) and isinstance(value.op, ast.Not):
        return f"devolve {text}, a negação lógica de {code_of(value.operand)}"
    if isinstance(value, ast.IfExp):
        return f"devolve {code_of(value.body)} quando {code_of(value.test)} é verdadeiro e {code_of(value.orelse)} caso contrário"
    if isinstance(value, ast.Tuple):
        return f"devolve a tupla {text}"
    if isinstance(value, ast.List):
        return f"devolve a lista {text}"
    if isinstance(value, ast.Dict):
        return f"devolve o dicionário {text}"
    if isinstance(value, (ast.ListComp, ast.SetComp, ast.DictComp)):
        kind = {ast.ListComp: "uma lista", ast.SetComp: "um conjunto", ast.DictComp: "um dicionário"}[type(value)]
        return f"devolve {kind} montad{'a' if kind.startswith('uma') else 'o'} por compreensão: {text}"
    if isinstance(value, ast.GeneratorExp):
        return f"devolve uma expressão geradora: {text}"
    if isinstance(value, ast.JoinedStr):
        return f"devolve a f-string {text}"
    if isinstance(value, ast.Lambda):
        return f"devolve uma função anônima: {text}"
    if isinstance(value, ast.Await):
        return f"aguarda {code_of(value.value)} e devolve o resultado"
    return f"devolve {text}"


def target_clause(target, value_text):
    if isinstance(target, ast.Attribute):
        return f"guarda {value_text} no atributo {code_of(target)}"
    if isinstance(target, ast.Name):
        return f"atribui {value_text} à variável {code_of(target)}"
    if isinstance(target, ast.Subscript):
        return f"grava {value_text} em {code_of(target)}"
    if isinstance(target, (ast.Tuple, ast.List)):
        return f"desempacota {value_text} em {code_of(target)}"
    raise Unsupported


def statement_clause(statement, depth):
    if isinstance(statement, ast.Return):
        return return_clause(statement.value)
    if isinstance(statement, ast.Assign):
        value = code_of(statement.value)
        if len(statement.targets) == 1:
            return target_clause(statement.targets[0], value)
        return f"atribui {value} a {join_pt(code_of(item) for item in statement.targets)}"
    if isinstance(statement, ast.AnnAssign):
        text = f"declara {code_of(statement.target)} com a anotação {code_of(statement.annotation)}"
        return text + (f" e atribui {code_of(statement.value)}" if statement.value is not None else "")
    if isinstance(statement, ast.AugAssign):
        return f"atualiza {code_of(statement.target)} com {code_of(statement)}"
    if isinstance(statement, ast.Expr):
        value = statement.value
        if isinstance(value, ast.Call):
            return f"chama {code_of(value)}"
        if isinstance(value, ast.Constant) and value.value is Ellipsis:
            return "não tem implementação: o corpo é só `...`"
        if isinstance(value, ast.Await):
            return f"aguarda {code_of(value.value)}"
        if isinstance(value, ast.Yield):
            return "executa um `yield` sem valor" if value.value is None else f"produz {code_of(value.value)} com `yield`"
        if isinstance(value, ast.YieldFrom):
            return f"repassa os valores de {code_of(value.value)} com `yield from`"
        raise Unsupported
    if isinstance(statement, ast.Pass):
        return "não faz nada (`pass`)"
    if isinstance(statement, ast.Raise):
        if statement.exc is None:
            return "relança a exceção que está sendo tratada (`raise`)"
        text = f"levanta {code_of(statement.exc)}"
        return text + (f" a partir de {code_of(statement.cause)}" if statement.cause is not None else "")
    if isinstance(statement, ast.Assert):
        return f"confere com `assert` que {code_of(statement.test)}" + (f" (mensagem: {code_of(statement.msg)})" if statement.msg else "")
    if isinstance(statement, ast.Delete):
        return f"remove {join_pt(code_of(item) for item in statement.targets)} com `del`"
    if isinstance(statement, ast.Global):
        return f"declara {join_pt(tick(name) for name in statement.names)} como global"
    if isinstance(statement, ast.Nonlocal):
        return f"declara {join_pt(tick(name) for name in statement.names)} como `nonlocal`"
    if isinstance(statement, (ast.Import, ast.ImportFrom)):
        return f"executa {code_of(statement)}"
    if depth > 0:
        raise Unsupported
    if isinstance(statement, ast.If):
        text = f"se a condição {code_of(statement.test)} for verdadeira, {block_clause(statement.body)}"
        orelse = statement.orelse
        if len(orelse) == 1 and isinstance(orelse[0], ast.If):
            inner = orelse[0]
            text += f"; senão, se {code_of(inner.test)} for verdadeira, {block_clause(inner.body)}"
            if inner.orelse:
                if len(inner.orelse) == 1 and isinstance(inner.orelse[0], ast.If):
                    raise Unsupported
                text += f"; caso contrário, {block_clause(inner.orelse)}"
        elif orelse:
            text += f"; caso contrário, {block_clause(orelse)}"
        return text
    if isinstance(statement, (ast.For, ast.AsyncFor)) and not statement.orelse:
        word = "async for" if isinstance(statement, ast.AsyncFor) else "for"
        return f"percorre {code_of(statement.iter)} com `{word}` e, para cada {code_of(statement.target)}, {block_clause(statement.body)}"
    if isinstance(statement, (ast.With, ast.AsyncWith)):
        items = join_pt(code_of(item.context_expr) + (f" (como {code_of(item.optional_vars)})" if item.optional_vars is not None else "") for item in statement.items)
        return f"entra no contexto de {items} e, dentro do bloco, {block_clause(statement.body)}"
    raise Unsupported


def block_clause(statements):
    if not 1 <= len(statements) <= 2:
        raise Unsupported
    clauses = [statement_clause(item, 1) for item in statements]
    return " e depois ".join(clauses)


def build_explain_small(index, symbol, rng):
    if symbol.kind == "class":
        raise Unsupported
    facts = function_facts(symbol)
    node = symbol.node
    body = body_without_docstring(node)
    if not 1 <= len(body) <= 4:
        raise Unsupported
    source_file = symbol.file
    start = min([node.lineno] + [item.lineno for item in node.decorator_list])
    if node.end_lineno - start + 1 > 14:
        raise Unsupported
    snippet = textwrap.dedent("\n".join(source_file.lines[start - 1:node.end_lineno])).rstrip()
    try:
        reparsed = ast.parse(snippet).body
    except SyntaxError:
        raise Unsupported
    if len(reparsed) != 1 or ast.dump(reparsed[0]) != ast.dump(node):
        raise Unsupported("trecho não reproduz o AST")
    doc = ast.get_docstring(node)
    if doc and len(doc.splitlines()) > 6:
        raise Unsupported
    if "```" in snippet or "\t" in snippet:
        raise Unsupported
    clauses = [statement_clause(item, 0) for item in body]
    ref, pron, do, _ = sym_ref(symbol, rng)
    where = place_q(source_file, rng)
    core = rng.choice([f"O que {ref} {where} faz? Mostra o código.", f"Explica o que {ref} {where} faz.",
                       f"Pode me mostrar e explicar o código de `{symbol.qual}` {where}?", f"Me explica, passo a passo, {ref} {where}.",
                       f"vc consegue me dizer o que `{symbol.qual}` faz? tá {where}", f"Qual é a implementação de `{symbol.qual}` {where}?",
                       f"Lê o código de `{symbol.qual}` ({source_file.display}) e me diz o que ele faz, sem inventar.",
                       f"Como funciona {ref} {where}?"])
    lead = rng.choice([f"Este é o código de `{symbol.qual}`, em `{source_file.display}` ({span(symbol) if not node.decorator_list else f'linhas {start}–{node.end_lineno}'}):",
                       f"`{symbol.qual}` fica em `{source_file.display}`, linhas {start} a {node.end_lineno}:",
                       f"Trecho de `{source_file.display}`, linhas {start}–{node.end_lineno}:"])
    parts = [lead, "", "```python", snippet, "```", ""]
    after_doc = "Depois da docstring, " if doc else ""
    single = body[0] if len(body) == 1 else None
    if single is not None and (isinstance(single, ast.Pass) or (isinstance(single, ast.Expr) and isinstance(single.value, ast.Constant) and single.value.value is Ellipsis)):
        stub = "pass" if isinstance(single, ast.Pass) else "..."
        parts.append(f"{after_doc}{'o' if after_doc else 'O'} corpo é só `{stub}`: não há implementação aqui, e uma chamada a `{symbol.name}` devolve `None`.")
    elif single is not None and isinstance(single, (ast.If, ast.For, ast.AsyncFor, ast.With, ast.AsyncWith)):
        parts.append(f"{after_doc or 'No corpo, '}{clauses[0]}.")
    elif single is not None:
        parts.append(rng.choice([f"{after_doc}{'o' if after_doc else 'O'} corpo só {clauses[0]}.",
                                 f"{after_doc or 'Literalmente, '}{pron} só {clauses[0]}." if after_doc == "" else f"Fora a docstring, {pron} só {clauses[0]}."]))
    else:
        intro = rng.choice(["Em ordem, o corpo:", "Passo a passo:", f"O que `{symbol.name}` faz, na ordem:"])
        if after_doc:
            intro = "Depois da docstring, " + lower_first(intro)
        parts.append(intro + "\n" + "\n".join(f"{position}. {cap(clause)}." for position, clause in enumerate(clauses, 1)))
    flags = facts["flags"]
    notes = []
    if flags["property"]:
        notes.append(f"Por causa do decorador de property, `{symbol.name}` é lido como atributo (`obj.{symbol.name}`), sem parênteses.")
    if flags["staticmethod"]:
        notes.append(f"Com `@staticmethod`, `{symbol.name}` não recebe a instância automaticamente.")
    if flags["classmethod"]:
        notes.append(f"Com `@classmethod`, o primeiro parâmetro ({tick(facts['implicit'])}) recebe a classe, não a instância.")
    if flags["abstract"]:
        notes.append(f"O `@abstractmethod` indica que subclasses devem implementar `{symbol.name}`.")
    for other in flags["other"][:2]:
        notes.append(f"`{symbol.name}` também passa pelo decorador {tick('@' + other)}, que pode alterar o comportamento final; isso não aparece no corpo.")
    if facts["async"]:
        notes.append(f"Por ser `async def`, chamar `{symbol.name}` devolve uma corrotina; o corpo roda quando ela é aguardada.")
    if facts["yields"]:
        notes.append(f"Como há `yield`, `{symbol.name}` é um gerador: o corpo só roda conforme os valores são pedidos.")
    if notes:
        parts += [""] + notes
    facts_out = {"code": snippet, "start": start, "end": node.end_lineno, "statements": len(body)}
    return core, "\n".join(parts), facts_out, ["ast.dump(reparse(snippet))==ast.dump(node)", "clauses:ast"]


# ---- exemplo de chamada ------------------------------------------------------

def snake(name):
    text = re.sub(r"(?<=[a-z0-9])([A-Z])", r"_\1", name).lower().strip("_")
    return text if text.isidentifier() and not keyword.iskeyword(text) else "obj"


def signature_from_ast(node):
    """inspect.Signature reconstruída do AST, sem importar nem executar o código analisado."""
    kinds = {"posonly": inspect.Parameter.POSITIONAL_ONLY, "normal": inspect.Parameter.POSITIONAL_OR_KEYWORD,
             "vararg": inspect.Parameter.VAR_POSITIONAL, "kwonly": inspect.Parameter.KEYWORD_ONLY,
             "varkw": inspect.Parameter.VAR_KEYWORD}
    params = [inspect.Parameter(row["name"], kinds[row["kind"]],
                                default=inspect.Parameter.empty if row["default"] is None else object())
              for row in parameter_rows(node)]
    return inspect.Signature(params)


def build_call_example(index, symbol, rng):
    if symbol.kind == "class":
        raise Unsupported
    constructor = symbol.name == "__init__"
    if constructor and not constructor_ok(symbol.cls):
        raise Unsupported
    if symbol.name.startswith("__") and not constructor:
        raise Unsupported
    facts = function_facts(symbol)
    flags = facts["flags"]
    if flags["property"] or flags["other"] or flags["abstract"]:
        raise Unsupported
    source_file = symbol.file
    params = facts["params"]
    implicit = facts["implicit"]
    explicit = list(params)
    if implicit is not None:
        explicit = explicit[1:]
    required_pos = [row for row in explicit if row["kind"] in ("posonly", "normal") and row["default"] is None]
    required_kw = [row for row in explicit if row["kind"] == "kwonly" and row["default"] is None]
    if len(required_pos) + len(required_kw) > 6:
        raise Unsupported
    owner = symbol.cls.name if symbol.cls is not None else None
    top = owner or symbol.name
    instance = snake(owner) if owner else None
    names = [row["name"] for row in params]
    if instance and (instance in names or instance == top):
        instance = "obj" if "obj" not in names else "instancia"
    args = [row["name"] for row in required_pos] + [f"{row['name']}={row['name']}" for row in required_kw]
    if constructor:
        target = owner
    elif symbol.kind == "method" and (flags["classmethod"] or flags["staticmethod"]):
        target = f"{owner}.{symbol.name}"
    elif symbol.kind == "method":
        target = f"{instance}.{symbol.name}"
    else:
        target = symbol.name
    call = f"{target}({', '.join(args)})"
    optional = [row for row in explicit if row["kind"] in ("normal", "kwonly") and row["default"] is not None
                and re.fullmatch(r"-?\d+(\.\d+)?|True|False|None|'[^'\\]{0,20}'", row["default"])]
    alt_call = None
    if optional and rng.random() < 0.6:
        chosen = rng.choice(optional)
        alt_call = f"{target}({', '.join(args + [chosen['name'] + '=' + chosen['default']])})"
    # Confere a chamada contra a assinatura reconstruída.
    signature = signature_from_ast(symbol.node)
    leading = ["<instancia>"] if implicit is not None else []
    try:
        signature.bind(*leading, *[row["name"] for row in required_pos], **{row["name"]: row["name"] for row in required_kw})
        if alt_call:
            signature.bind(*leading, *[row["name"] for row in required_pos],
                           **{row["name"]: row["name"] for row in required_kw}, **{chosen["name"]: chosen["default"]})
    except TypeError:
        raise Unsupported("chamada não casa com a assinatura")
    code = [f"from {source_file.module} import {top}", ""]
    has_value = bool(facts["returns"]) and not facts["yields"]
    if constructor:
        code.append(f"{instance} = {call}")
    elif facts["async"] and not facts["yields"]:
        code += ["async def principal():", f"    resultado = await {call}" if has_value else f"    await {call}"]
    elif facts["async"]:
        code += ["async def principal():", f"    async for item in {call}:", "        ..."]
    elif facts["yields"]:
        code += [f"for item in {call}:", "    ..."]
    else:
        code.append(f"resultado = {call}" if has_value else call)
    if alt_call and not facts["async"] and not facts["yields"]:
        code.append(f"{'resultado = ' if has_value and not constructor else (instance + ' = ' if constructor else '')}{alt_call}  # igual a omitir {chosen['name']}")
    else:
        alt_call = None
    example = "\n".join(code)
    ast.parse(example)
    where = place_q(source_file, rng)
    ref, pron, _, _ = sym_ref(symbol, rng, constructor)
    if constructor:
        core = rng.choice([f"Como crio uma instância de `{owner}` {where}?", f"Me dá um exemplo de como instanciar `{owner}` {where}.",
                           f"Como eu construo um objeto `{owner}` {where}? Quais argumentos são necessários?"])
    else:
        core = rng.choice([f"Como eu chamo {ref} {where}?", f"Me dá um exemplo de uso de `{symbol.qual}` {where}.",
                           f"Como se usa `{symbol.qual}` {where}? Mostra uma chamada.", f"Escreve um exemplo mínimo chamando `{symbol.qual}` {where}.",
                           f"exemplo de chamada pra `{symbol.qual}` {where}?", f"Qual é o jeito certo de invocar {ref} {where}?"])
    loc = place_a(source_file, symbol.line, rng, paren_ok=False)
    lines = [rng.choice([f"A assinatura, {loc}, é `{facts['signature']}`." if len(facts["signature"]) <= 160 else f"A definição está {loc}.",
                         f"Pela definição {loc}" + (f" (`{facts['signature']}`)" if len(facts["signature"]) <= 160 else "") + ", uma chamada mínima fica assim:"])]
    if not lines[0].endswith(":"):
        lines.append(rng.choice(["Uma chamada mínima:", "Exemplo:", "Um uso mínimo seria:"]))
    lines += ["", "```python", example, "```", ""]
    explain = []
    if constructor:
        explain.append(f"Os argumentos de `{owner}(...)` vão para `__init__`; o {tick(implicit)} é o próprio objeto e não é passado.")
    elif symbol.kind == "method" and flags["classmethod"]:
        explain.append(f"Por ser `@classmethod`, a chamada é feita na classe; {tick(implicit)} recebe `{owner}` sozinho.")
    elif symbol.kind == "method" and flags["staticmethod"]:
        explain.append(f"Por ser `@staticmethod`, `{symbol.name}` não usa instância: chama-se direto em `{owner}`.")
    elif symbol.kind == "method":
        explain.append(f"Aqui `{instance}` representa uma instância de `{owner}` que você já tenha; {tick(implicit)} é preenchido por ela.")
    placeholders = [tick(row["name"]) for row in required_pos + required_kw]
    if placeholders:
        has_optional = any(row["default"] is not None for row in explicit)
        if len(placeholders) == 1:
            explain.append(f"{placeholders[0]} é um valor seu" + (" (o único obrigatório)." if has_optional else "."))
        else:
            explain.append(f"{join_pt(placeholders)} são valores seus" + (" (os obrigatórios)." if has_optional else "."))
    else:
        explain.append(f"Nenhum argumento é obrigatório para criar `{owner}`." if constructor else f"`{symbol.qual}` não tem argumentos obrigatórios.")
    if required_kw:
        explain.append(f"{join_pt(tick(row['name']) for row in required_kw)} só {'pode' if len(required_kw) == 1 else 'podem'} ser passado{'s' if len(required_kw) > 1 else ''} por nome.")
    if alt_call:
        explain.append(f"A última linha passa {tick(chosen['name'] + '=' + chosen['default'])}, que é o valor padrão; na prática é a mesma chamada.")
    if facts["async"]:
        explain.append("Como é `async def`, a chamada precisa estar dentro de código assíncrono.")
    if source_file.origin == "brasa":
        explain.append(rng.choice([f"O `from {source_file.module} import ...` funciona com a pasta `python/` no `sys.path`, como fazem os testes do projeto.",
                                   f"Para o import de `{source_file.module}` dar certo, `python/` precisa estar no `sys.path` (os testes do Brasa fazem isso).",
                                   f"`{source_file.module}` é um módulo de `python/`: inclua essa pasta no `sys.path` antes de importar."]))
    if top.startswith("_"):
        explain.append(f"O nome `{top}` começa com `_`: por convenção é de uso interno do módulo.")
    lines += explain
    facts_out = {"example": example, "call": call, "alt_call": alt_call, "import": f"from {source_file.module} import {top}",
                 "required": [row["name"] for row in required_pos + required_kw], "implicit": implicit}
    return core, "\n".join(lines), facts_out, ["inspect.Signature(ast).bind(call)", "ast.parse(example)"]


# ---------------------------------------------------------------------------
# Montagem
# ---------------------------------------------------------------------------

def content_ok(text):
    if not text or "\x00" in text or "<|" in text or "|>" in text:
        return False
    if any(ord(char) < 32 and char not in "\n" for char in text):
        return False
    return text.count("```") % 2 == 0


def make_record(kind, question, answer, source_file, line, symbol, facts, checks):
    verification = {"tool": ENGINE, "cross_check": "ast", "python": PY_FULL, "origin": source_file.origin,
                    "path": source_file.display, "line": line, "symbol": symbol, "source_sha1": source_file.sha1,
                    "checks": checks, "facts": facts, "status": "passed"}
    return {"messages": [{"role": "user", "content": question}, {"role": "assistant", "content": answer}],
            "domain": f"code.{kind}", "provenance": PROVENANCE, "verification": verification}


def build_candidates(index):
    pools = {kind: {"brasa": [], "stdlib": []} for kind in KIND_WEIGHTS}
    for symbol in index.symbols:
        origin = symbol.file.origin
        if symbol.kind == "class":
            for kind in ("class_methods", "class_bases", "docstring", "line_lookup"):
                pools[kind][origin].append((kind, symbol.key, None))
        else:
            for kind in ("parameters", "returns", "docstring", "explain_small", "call_example", "calls", "line_lookup"):
                pools[kind][origin].append((kind, symbol.key, None))
    for source_file in index.files:
        origin = source_file.origin
        for variant in ("all", "classes", "functions", "public", "count"):
            pools["module_definitions"][origin].append(("module_definitions", source_file.display, variant))
        for variant in ("all", "top", "check"):
            pools["imports"][origin].append(("imports", source_file.display, variant))
        pools["module_docstring"][origin].append(("module_docstring", source_file.display, None))
    for name in sorted(index.module_level):
        if name.startswith("__") and name.endswith("__"):
            continue
        defs = index.module_level[name]
        origin = "brasa" if any(item.file.origin == "brasa" for item in defs) else "stdlib"
        pools["where_defined"][origin].append(("where_defined", name, "all"))
        if origin == "brasa" and any(item.file.origin == "stdlib" for item in defs):
            pools["where_defined"]["brasa"].append(("where_defined", name, "brasa"))
    return pools


def build_one(index, candidate, seed):
    kind, key, variant = candidate
    rng = random.Random(f"{seed}|{kind}|{key}|{variant}")
    try:
        if kind in ("module_definitions", "imports", "module_docstring"):
            source_file = index.file_by_display[key]
            if kind == "module_definitions":
                core, answer, facts, checks = build_module_definitions(index, source_file, variant, rng)
            elif kind == "imports":
                core, answer, facts, checks = build_imports(index, source_file, variant, rng)
            else:
                core, answer, facts, checks, line = build_module_docstring(index, source_file, rng)
                return make_record(kind, dress_question(core, rng), answer, source_file, line, None, facts, checks)
            return make_record(kind, dress_question(core, rng), answer, source_file, 1, None, facts, checks)
        if kind == "where_defined":
            core, answer, facts, checks, first = build_where_defined(index, key, variant, rng)
            return make_record(kind, dress_question(core, rng), answer, first.file, first.line, key, facts, checks)
        symbol = index.by_key[key]
        builder = {"parameters": build_parameters, "returns": build_returns, "docstring": build_docstring,
                   "explain_small": build_explain_small, "call_example": build_call_example, "calls": build_calls,
                   "class_methods": build_class_methods, "class_bases": build_class_bases, "line_lookup": build_line_lookup}[kind]
        core, answer, facts, checks = builder(index, symbol, rng)
        return make_record(kind, dress_question(core, rng), answer, symbol.file, symbol.line, symbol.qual, facts, checks)
    except (Unsupported, SyntaxError, ValueError, KeyError):
        return None


def generate(count, seed, stdlib=True, project=True, stdlib_include=None, index=None):
    """Gera até ``count`` exemplos verificados; determinístico para o mesmo ``seed`` e as mesmas fontes."""
    index = index or Index(load_sources(stdlib, project, stdlib_include))
    pools = build_candidates(index)
    rng = random.Random(seed)
    for kind in KIND_WEIGHTS:
        for origin in ("brasa", "stdlib"):
            rng.shuffle(pools[kind][origin])
    records, questions, answers = [], set(), set()
    stats = {"dropped": 0, "duplicates": 0, "rejected_symbols": index.rejected}
    kinds = list(KIND_WEIGHTS)
    while len(records) < count:
        alive = [kind for kind in kinds if pools[kind]["brasa"] or pools[kind]["stdlib"]]
        if not alive:
            break
        kind = rng.choices(alive, weights=[KIND_WEIGHTS[item] for item in alive])[0]
        brasa, std = pools[kind]["brasa"], pools[kind]["stdlib"]
        source = brasa if brasa and (not std or rng.random() < BRASA_SHARE) else std
        record = build_one(index, source.pop(), seed)
        if record is None:
            stats["dropped"] += 1
            continue
        question, answer = (message["content"] for message in record["messages"])
        if not (content_ok(question) and content_ok(answer)):
            stats["dropped"] += 1
            continue
        if question in questions or answer in answers:
            stats["duplicates"] += 1
            continue
        questions.add(question)
        answers.add(answer)
        records.append(record)
    return records, stats


def render_exchange(record):
    question, answer = (message["content"] for message in record["messages"])
    return f"<|user|>\n{question}\n<|assistant|>\n{answer}\n"


def pack_documents(records, seed):
    """Agrupa de 1 a 3 trocas sobre o mesmo arquivo em um documento de pré-treino."""
    rng = random.Random(f"{seed}|pack")
    pending, documents = {}, []
    for record in records:
        path = record["verification"]["path"]
        bucket = pending.setdefault(path, {"target": rng.choice([1, 1, 2, 2, 3]), "items": []})
        bucket["items"].append(record)
        if len(bucket["items"]) >= bucket["target"]:
            documents.append("".join(render_exchange(item) for item in bucket["items"]))
            del pending[path]
    for bucket in pending.values():
        documents.append("".join(render_exchange(item) for item in bucket["items"]))
    return documents


def write_outputs(records, out_dir, seed):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / f"{NAME}.jsonl").open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    documents = pack_documents(records, seed)
    with (out_dir / f"{NAME}.txt").open("w", encoding="utf-8") as handle:
        for document in documents:
            handle.write(document + DOC_SEP)
    return len(documents)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--out", required=True)
    parser.add_argument("--count", type=int, default=30000)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--stdlib-include", default=None, help="módulos/pacotes da stdlib separados por vírgula (para rodadas leves)")
    parser.add_argument("--no-stdlib", action="store_true")
    parser.add_argument("--no-project", action="store_true")
    args = parser.parse_args(argv)
    include = None if args.stdlib_include is None else [item.strip() for item in args.stdlib_include.split(",") if item.strip()]
    records, stats = generate(args.count, args.seed, stdlib=not args.no_stdlib, project=not args.no_project, stdlib_include=include)
    documents = write_outputs(records, args.out, args.seed)
    domains = {}
    origins = {}
    for record in records:
        domains[record["domain"]] = domains.get(record["domain"], 0) + 1
        origin = record["verification"]["origin"]
        origins[origin] = origins.get(origin, 0) + 1
    print(json.dumps({"generator": NAME, "examples": len(records), "documents": documents, "requested": args.count,
                      "domains": dict(sorted(domains.items())), "origins": origins, **stats}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
