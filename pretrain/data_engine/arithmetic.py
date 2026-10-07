"""Motor de dados verificados: diálogos de matemática do dia a dia em português.

Cada exemplo nasce de números sorteados (semente fixa) e é resolvido duas vezes:

1. pelo próprio gerador, com aritmética racional exata (``fractions.Fraction``);
2. pela ferramenta ``calculate`` do runtime, isto é, ``run_engine`` de
   ``python/execution_engine.py`` chamado exatamente como o servidor faz
   (resultado validado contra ``contracts/calculate.json``). Uma amostra também é
   reexecutada pela linha de comando do motor, como faz o runtime em Rust.

Todo número calculado que aparece na resposta passa pelo motor. Se o motor falhar,
divergir do valor exato, ou arredondar diferente na casa exibida, o exemplo é
descartado. O gerador também confere que os dados da pergunta aparecem no texto da
pergunta e que o valor final aparece no texto da resposta.

Uso (a partir da raiz do repositório):
  python pretrain/data_engine/arithmetic.py --out pretrain_data_raw/engine --count 40000 --seed 1

Saídas em --out:
  arithmetic.jsonl          mensagens + domínio + proveniência + registro de verificação
  arithmetic.txt            texto de pré-treino (<|user|>/<|assistant|>), documentos separados por NUL
  arithmetic.manifest.json  contagens, descartes e hashes
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import re
import subprocess
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, localcontext
from fractions import Fraction
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ENGINE_PATH = ROOT / "python" / "execution_engine.py"
if str(ROOT / "python") not in sys.path:
    sys.path.insert(0, str(ROOT / "python"))
from execution_engine import run_engine  # noqa: E402
from tool_registry import ToolRegistry  # noqa: E402

NAME = "arithmetic"
PROVENANCE = "tool-verified-arithmetic-v1"
DOC_SEP = "\x00"
TOLERANCE = Fraction(1, 10**9)
METHOD = (
    "Cada passo numérico foi executado pela ferramenta calculate (run_engine de "
    "python/execution_engine.py, resposta validada por contracts/calculate.json) e comparado "
    "com aritmética racional exata do gerador (tolerância relativa 1e-9 e mesmo arredondamento "
    "half-up na casa exibida). Exemplos com falha ou divergência foram descartados."
)


class Reject(Exception):
    """O exemplo não passou na verificação e deve ser descartado."""


# --------------------------------------------------------------------------- números

def frac(value) -> Fraction:
    if isinstance(value, bool):
        raise TypeError("bool não é número aqui")
    if isinstance(value, Fraction):
        return value
    if isinstance(value, int):
        return Fraction(value)
    if isinstance(value, (str, Decimal)):
        return Fraction(str(value))
    if isinstance(value, float):
        return Fraction(repr(value))
    raise TypeError(type(value).__name__)


def to_decimal(value) -> Decimal:
    value = frac(value)
    with localcontext() as ctx:
        ctx.prec = 80
        return Decimal(value.numerator) / Decimal(value.denominator)


def quant(value, places: int) -> Decimal:
    with localcontext() as ctx:
        ctx.prec = 80
        return to_decimal(value).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)


def exact_at(value, places: int) -> bool:
    return Fraction(quant(value, places)) == frac(value)


def terminating(value) -> bool:
    den = frac(value).denominator
    for prime in (2, 5):
        while den % prime == 0:
            den //= prime
    return den == 1


def exact_str(value) -> str:
    value = frac(value)
    if value.denominator == 1:
        return str(value.numerator)
    if terminating(value):
        text = format(to_decimal(value), "f")
        return text.rstrip("0").rstrip(".") if "." in text else text
    return f"{value.numerator}/{value.denominator}"


def group(digits: str) -> str:
    out = []
    while len(digits) > 3:
        out.append(digits[-3:])
        digits = digits[:-3]
    out.append(digits)
    return ".".join(reversed(out))


def br(value, places: int = 2, fixed: bool = False, grouping: bool = True) -> str:
    """Número no padrão brasileiro: 1.234,5 (vírgula decimal, ponto de milhar)."""
    text = format(quant(value, places), "f")
    negative = text.startswith("-")
    text = text.lstrip("-")
    whole, _, decimals = text.partition(".")
    if not fixed:
        decimals = decimals.rstrip("0")
    whole = group(whole) if grouping else whole
    out = whole + ("," + decimals if decimals else "")
    if negative and any(ch not in "0,." for ch in out):
        out = "-" + out
    return out


def N(value, places: int = 2) -> str:
    return br(value, places)


def M(value) -> str:
    return "R$ " + br(value, 2, fixed=True)


def P(value, places: int = 2) -> str:
    return br(value, places) + "%"


def approx(value, places: int = 2) -> str:
    """Prefixo honesto: '≈ ' quando o valor exibido foi arredondado."""
    return "" if exact_at(value, places) else "≈ "


def eq_or_approx(value, places: int = 2) -> str:
    return "=" if exact_at(value, places) else "≈"


def lit(value) -> str:
    """Literal Python exato para uma expressão do motor (só decimais finitos)."""
    value = frac(value)
    if not terminating(value):
        raise ValueError(f"literal sem representação decimal finita: {value}")
    text = exact_str(value)
    return f"({text})" if text.startswith("-") else text


def jnum(value):
    value = frac(value)
    if value.denominator == 1:
        return value.numerator
    if not terminating(value) or len(exact_str(value).split(".")[-1]) > 10:
        raise ValueError(f"variável sem representação decimal curta: {value}")
    return float(exact_str(value))


NUMBER_RE = re.compile(r"(?<![\d])(\d{1,3}(?:\.\d{3})+|\d+)(?:,(\d+))?(?![\d])")


def numbers_in(text: str) -> set[Fraction]:
    found = set()
    for match in NUMBER_RE.finditer(text):
        whole = match.group(1).replace(".", "")
        value = Fraction(whole + ("." + match.group(2) if match.group(2) else ""))
        found.add(value)
    return found


def clock(minutes: int) -> str:
    minutes %= 24 * 60
    return f"{minutes // 60}h{minutes % 60:02d}"


def duration(minutes: int) -> str:
    hours, rest = divmod(int(minutes), 60)
    if hours and rest:
        return f"{hours} h {rest} min"
    if hours:
        return f"{hours} h"
    return f"{rest} min"


SUP = str.maketrans("0123456789", "⁰¹²³⁴⁵⁶⁷⁸⁹")


# --------------------------------------------------------------------------- motor

class Engine:
    """Executa ``calculate`` como o servidor e valida a resposta pelo contrato."""

    def __init__(self):
        self.registry = ToolRegistry(ROOT / "contracts")
        if not self.registry.has("calculate"):
            raise RuntimeError("contrato calculate ausente ou inválido")
        self.calls = 0
        self.cli_calls = 0

    def _accept(self, data, arguments):
        valid, error = self.registry.validate_result("calculate", data)
        if not valid or data.get("tool") != "calculate" or data.get("request") != arguments \
                or data.get("passed") is not True or data.get("executed") is not True \
                or data.get("error") is not None or "result" not in data:
            message = error or (data.get("error") or {}).get("message") or "resultado inválido"
            raise Reject("engine-error: " + str(message)[:80])
        return data["result"]

    def calculate(self, expression: str, variables: dict):
        arguments = {"expression": expression, "variables": variables}
        self.calls += 1
        return self._accept(run_engine("calculate", arguments), arguments)

    def calculate_cli(self, expression: str, variables: dict):
        arguments = {"expression": expression, "variables": variables}
        encoded = json.dumps(arguments, ensure_ascii=False)
        command = [sys.executable, str(ENGINE_PATH), "--workspace", str(ROOT),
                   "--tool", "calculate", "--arguments", encoded]
        self.cli_calls += 1
        completed = subprocess.run(command, capture_output=True, text=True, timeout=60, check=False)
        if completed.returncode != 0:
            raise Reject("engine-cli-exit")
        return self._accept(json.loads(completed.stdout), arguments)


class Draft:
    """Registro de verificação de um exemplo em construção."""

    def __init__(self, engine: Engine):
        self.engine = engine
        self.inputs: dict = {}
        self.constants: dict = {}
        self.params: dict = {}
        self.checks: list = []
        self.final = None

    @staticmethod
    def _encode(value):
        if isinstance(value, (list, tuple)):
            return [Draft._encode(item) for item in value]
        return exact_str(frac(value))

    def given(self, **values):
        """Números que aparecem em algarismos na pergunta."""
        for key, value in values.items():
            self.inputs[key] = self._encode(value)

    def given_coef(self, **values):
        """Coeficientes: ±1 não aparece em algarismos ('x', '-x'), então vira constante."""
        for key, value in values.items():
            (self.const if abs(frac(value)) == 1 else self.given)(**{key: value})

    def const(self, **values):
        """Números usados mas não escritos em algarismos (1 km = 1.000 m, 'o dobro'...)."""
        for key, value in values.items():
            self.constants[key] = self._encode(value)

    def param(self, **values):
        self.params.update(values)

    def calc(self, step: str, expression: str, exact, variables: dict | None = None, places: int | None = None):
        exact = frac(exact)
        variables = {key: jnum(value) for key, value in (variables or {}).items()}
        value = self.engine.calculate(expression, variables)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise Reject("engine-type")
        if isinstance(value, int) and exact.denominator == 1:
            agree = value == exact.numerator
        else:
            agree = abs(Fraction(value) - exact) <= TOLERANCE * max(1, abs(exact))
        if agree and places is not None:
            agree = Decimal(repr(value)).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP) \
                == quant(exact, places)
        if not agree:
            raise Reject("divergence")
        record = {"step": step, "expression": expression, "variables": variables,
                  "engine_result": value, "exact": exact_str(exact)}
        if places is not None:
            record["display_places"] = places
        self.checks.append(record)
        return exact

    def test(self, step: str, expression: str, expected: bool, variables: dict | None = None):
        variables = {key: jnum(value) for key, value in (variables or {}).items()}
        value = self.engine.calculate(expression, variables)
        if value is not expected:
            raise Reject("divergence-bool")
        self.checks.append({"step": step, "expression": expression, "variables": variables,
                            "engine_result": value, "exact": expected})
        return expected

    def result(self, name: str, exact, display):
        if isinstance(exact, (list, tuple)):
            self.final = {"name": name, "exact": [exact_str(frac(v)) for v in exact], "display": list(display)}
        else:
            self.final = {"name": name, "exact": exact_str(frac(exact)), "display": display}


@dataclass
class Out:
    question: str
    steps: list
    final: str
    short: str


@dataclass
class Kind:
    name: str
    domain: str
    weight: float
    school: bool
    func: object


KINDS: list[Kind] = []


def kind(name, domain, weight=1.0, school=False):
    def register(func):
        KINDS.append(Kind(name, domain, weight, school, func))
        return func
    return register


# --------------------------------------------------------------------------- texto

NAMES = [
    ("Ana", "f"), ("João", "m"), ("Maria", "f"), ("Pedro", "m"), ("Luíza", "f"), ("Gabriel", "m"),
    ("Beatriz", "f"), ("Rafael", "m"), ("Camila", "f"), ("Lucas", "m"), ("Fernanda", "f"), ("Thiago", "m"),
    ("Juliana", "f"), ("Mateus", "m"), ("Larissa", "f"), ("Bruno", "m"), ("Patrícia", "f"), ("Diego", "m"),
    ("Aline", "f"), ("Felipe", "m"), ("Renata", "f"), ("Gustavo", "m"), ("Carla", "f"), ("Rodrigo", "m"),
    ("Vanessa", "f"), ("Eduardo", "m"), ("Tatiane", "f"), ("Marcelo", "m"), ("Jéssica", "f"), ("Leandro", "m"),
    ("Bianca", "f"), ("Caio", "m"), ("Daniela", "f"), ("Vinícius", "m"), ("Priscila", "f"), ("Otávio", "m"),
    ("Sônia", "f"), ("Raimundo", "m"), ("Francisca", "f"), ("Antônio", "m"), ("Yasmin", "f"), ("Enzo", "m"),
    ("Valentina", "f"), ("Davi", "m"), ("Isadora", "f"), ("Ícaro", "m"), ("Joana", "f"), ("Severino", "m"),
    ("Cláudia", "f"), ("Wesley", "m"), ("Natália", "f"), ("Heitor", "m"), ("Lívia", "f"), ("Murilo", "m"),
    ("Helena", "f"), ("Samuel", "m"), ("Alice", "f"), ("Benedito", "m"), ("Rosângela", "f"), ("Iago", "m"),
]


def names(rng, count):
    return rng.sample(NAMES, count)


PREFIXES = [
    "Oi! ", "Olá! ", "Bom dia! ", "Boa tarde! ", "Boa noite! ", "Oi, tudo bem? ", "E aí! ",
    "Me ajuda numa conta: ", "Pergunta rápida: ", "Uma dúvida: ", "Preciso de uma ajuda aqui. ",
    "Tô fazendo umas contas e travei. ", "Rapidinho: ", "Ei, ", "Socorro, matemática: ",
    "Pode me ajudar? ", "Estou organizando as finanças e surgiu uma dúvida: ", "Conta do dia: ",
    "Queria conferir uma coisa: ", "Fiz essa conta de cabeça e quero conferir: ",
]
SCHOOL_PREFIXES = [
    "Estou estudando para a prova: ", "Caiu isso na lição de casa: ", "Exercício da apostila: ",
    "Revisando matemática: ", "Meu filho trouxe essa da escola: ", "Questão do simulado: ",
    "Professor passou essa: ", "Treinando para o concurso: ",
]
SUFFIX_NEUTRAL = [" Obrigado!", " Obrigada!", " Valeu!", " Desde já, obrigado.", " Agradeço!",
                  " Valeu demais!", " Brigada!"]
SUFFIX_TERSE = [" Só o resultado, por favor.", " Me passa só o valor final.", " Só preciso do número.",
                " Sem explicação, só o resultado.", " Resposta curta, por favor."]
SUFFIX_STEPS = [" Pode mostrar a conta?", " Explica o passo a passo?", " Mostra como chegou no resultado, por favor.",
                " Quero entender a conta, não só o resultado.", " Detalha o raciocínio?"]

LOWER_STARTERS = {
    "Quanto", "Quantos", "Quantas", "Qual", "Quais", "Calcule", "Calcula", "Como", "Se", "Um", "Uma",
    "O", "A", "Os", "As", "Converta", "Converte", "Resolva", "Resolve", "Divida", "Divide", "Dá", "Me",
    "Tenho", "Tirei", "Vou", "Quero", "Comprei", "Paguei", "Fui", "Pensei", "Dois", "Duas", "Três",
    "Na", "No", "Num", "Numa", "Em", "Encontre", "Escreva", "Transforma", "Transforme", "Simplifique",
    "Simplifica", "Some", "Multiplique", "Multiplica", "Faz", "Recebo", "Ganho", "Gastei", "Peguei",
    "Saio", "Saí", "Coloquei", "Minha", "Meu", "Minhas", "Meus", "Eu", "Uns", "Para", "Pra", "Acertei",
    "Dos", "Das", "Já", "Depois", "Com", "Estava", "De", "Cobro", "Trabalho", "Daqui", "Avalie",
    "Ache", "Achar", "Determine", "Aumente", "Reduza", "Preciso", "Vamos", "Andei", "Corri", "Percorri",
    "Achei", "Liquidação", "Juros", "Investi", "Apliquei", "Rachando", "Somos", "Cada", "Mãe", "Lá",
}

ITEMS_LOJA = [
    ("tênis", "m", 150, 700), ("geladeira", "f", 1800, 6000), ("notebook", "m", 2200, 7000),
    ("celular", "m", 900, 6000), ("sofá", "m", 1200, 5000), ("bicicleta", "f", 600, 3500),
    ("fone de ouvido", "m", 80, 900), ("jaqueta", "f", 120, 600), ("panela de pressão", "f", 90, 350),
    ("micro-ondas", "m", 450, 1200), ("ventilador", "m", 120, 450), ("liquidificador", "m", 100, 400),
    ("mochila", "f", 80, 400), ("perfume", "m", 120, 700), ("relógio", "m", 150, 1500),
    ("smart TV", "f", 1500, 5000), ("impressora", "f", 500, 1500), ("aspirador de pó", "m", 250, 900),
    ("colchão", "m", 700, 3500), ("guarda-roupa", "m", 900, 3000), ("cafeteira", "f", 120, 800),
    ("air fryer", "f", 300, 900), ("furadeira", "f", 150, 600), ("cadeira de escritório", "f", 400, 1600),
    ("vestido", "m", 90, 400), ("calça jeans", "f", 100, 350), ("bolsa", "f", 90, 600),
    ("mesa de jantar", "f", 700, 3000), ("tablet", "m", 900, 4000), ("videogame", "m", 2500, 4500),
    ("máquina de lavar", "f", 1700, 4200), ("fogão", "m", 800, 3200), ("óculos de sol", "m", 120, 800),
]


def art(gender, definite=False):
    if definite:
        return "o" if gender == "m" else "a"
    return "um" if gender == "m" else "uma"


def price(rng, lo, hi):
    reais = rng.randint(lo, hi)
    ending = rng.choices(["00", "90", "99", "50", "any", "49", "95"], [30, 22, 20, 10, 10, 4, 4])[0]
    cents = rng.randint(1, 99) if ending == "any" else int(ending)
    if cents >= 49 and reais > lo and ending != "any":
        reais -= 1
    return Fraction(reais * 100 + cents, 100)


def qn(rng, value, places=3):
    """Número como um usuário escreveria (às vezes sem ponto de milhar)."""
    value = frac(value)
    grouping = not (1000 <= abs(value) < 100000 and rng.random() < 0.3)
    return br(value, places, grouping=grouping)


def qm(rng, value):
    """Dinheiro como um usuário escreveria."""
    value = frac(value)
    whole = value.denominator == 1
    roll = rng.random()
    if whole and roll < 0.3:
        return "R$ " + br(value, 0, grouping=rng.random() < 0.75)
    if roll < 0.78 or value < 2:
        return M(value)
    if roll < 0.86:
        return "R$" + br(value, 2, fixed=True)
    unit = "real" if value == 1 else "reais"
    return f"{br(value, 0) if whole else br(value, 2, fixed=True)} {unit}"


def listing(items, sep=", "):
    items = list(items)
    if len(items) == 1:
        return items[0]
    return sep.join(items[:-1]) + " e " + items[-1]


def cap(text: str) -> str:
    match = re.match(r"([a-záéíóúâêôãõçà]+)", text)
    if match and len(match.group(1)) >= 2 and match.group(1) not in {"km", "kg", "mm", "cm", "ml", "min"}:
        return text[0].upper() + text[1:]
    return text


PERSON_NAMES = {name for name, _ in NAMES}


def lower_start(text: str) -> str:
    """Minúscula após 'Prefixo: ' a menos que comece com nome próprio, sigla ou símbolo."""
    first = text.split(" ", 1)[0].rstrip(",:?!.")
    if not first or not first[0].isalpha() or not first[0].isupper():
        return text
    if first in PERSON_NAMES or (len(first) > 1 and first.isupper()) or any(ch in first for ch in "$€£"):
        return text
    return text[0].lower() + text[1:]


def compose(rng, steps, final, mode):
    if mode == "terse" or not steps:
        return final
    styles = ["lines", "lines", "lines", "bullets", "numbered", "inline", "inline"]
    if mode == "steps":
        styles = ["lines", "lines", "bullets", "numbered"]
    style = rng.choice(styles)
    if style == "inline" and (len(steps) > 3 or sum(len(s) for s in steps) > 220):
        style = "lines"
    if style == "lines":
        return "\n".join(cap(s) for s in steps) + "\n" + final
    if style == "bullets":
        return "\n".join("- " + cap(s) for s in steps) + "\n" + final
    if style == "numbered":
        sep = rng.choice([". ", ") "])
        return "\n".join(f"{i}{sep}{cap(s)}" for i, s in enumerate(steps, 1)) + "\n" + final
    return cap("; ".join(steps)) + ". " + final


def terse(rng, short):
    return rng.choice([f"{short}.", f"Resultado: {short}.", f"Dá {short}.", f"Resposta: {short}.", f"{short}"])


def wrap_question(rng, question, school):
    prefix = ""
    roll = rng.random()
    if school and roll < 0.2:
        prefix = rng.choice(SCHOOL_PREFIXES)
    elif roll < 0.38:
        prefix = rng.choice(PREFIXES)
    if prefix.endswith((": ", ", ")):
        question = lower_start(question)
    mode = "default"
    roll = rng.random()
    suffix = ""
    if roll < 0.06:
        suffix, mode = rng.choice(SUFFIX_TERSE), "terse"
    elif roll < 0.14:
        suffix, mode = rng.choice(SUFFIX_STEPS), "steps"
    elif roll < 0.22:
        suffix = rng.choice(SUFFIX_NEUTRAL)
    if mode == "default" and rng.random() < 0.07:
        mode = "terse-light"
    return prefix + question + suffix, mode


# =========================================================================== tipos
# --------------------------------------------------------------- inteiros

@kind("soma_subtracao", "aritmetica/inteiros", 0.9)
def k_soma_subtracao(rng, d):
    stories = [
        ("No estoque havia {a} unidades. Chegaram mais {b} e foram vendidas {c}. Quantas unidades restaram?",
         "Restaram {v} unidades no estoque.", 3000),
        ("Eu tinha {a} figurinhas, ganhei {b} do meu primo e dei {c} para minha irmã. Com quantas fiquei?",
         "Você ficou com {v} figurinhas.", 900),
        ("Uma biblioteca tinha {a} livros, recebeu {b} de doação e descartou {c} danificados. Quantos livros ficaram?",
         "Ficaram {v} livros no acervo.", 5000),
        ("Na campanha do agasalho a escola já tinha {a} peças, arrecadou mais {b} e entregou {c} a um abrigo. Quantas peças sobraram?",
         "Sobraram {v} peças.", 2000),
        ("Um aplicativo tinha {a} usuários ativos; entraram {b} novos e {c} cancelaram. Quantos ativos ficaram?",
         "Ficaram {v} usuários ativos.", 9000),
        ("Minha conta tinha {A}. Caiu um pix de {B} e paguei um boleto de {C}. Qual é o saldo?",
         "Seu saldo é de {V}.", 6000),
    ]
    story = rng.random() < 0.35
    if story:
        template, final_tpl, top = rng.choice(stories)
        a, b = rng.randint(20, top), rng.randint(5, top // 2)
        c = rng.randint(1, a + b)
        terms, ops = [a, b, c], ["+", "-"]
    else:
        count = rng.choice([2, 2, 3, 3, 3, 4, 5])
        terms, ops, total = [rng.randint(10, 9999)], [], None
        total = terms[0]
        for _ in range(count - 1):
            op = "-" if total > 20 and rng.random() < 0.4 else "+"
            value = rng.randint(1, min(total, 5000)) if op == "-" else rng.randint(1, 5000)
            ops.append(op)
            terms.append(value)
            total = total + value if op == "+" else total - value
    d.given(termos=terms)
    d.param(operacoes=ops)
    steps, acc = [], terms[0]
    for index, (op, value) in enumerate(zip(ops, terms[1:]), 1):
        new = acc + value if op == "+" else acc - value
        d.calc(f"parcial_{index}", f"{acc} {op} {value}", new)
        steps.append(f"{N(acc)} {op} {N(value)} = {N(new)}")
        acc = new
    total = d.calc("total", " ".join([str(terms[0])] + [f"{o} {t}" for o, t in zip(ops, terms[1:])]), acc)
    if story:
        if "{A}" in template:
            question = template.format(A=qm(rng, terms[0]), B=qm(rng, terms[1]), C=qm(rng, terms[2]))
            display = M(total)
            final = final_tpl.format(V=display)
        else:
            question = template.format(a=qn(rng, terms[0]), b=qn(rng, terms[1]), c=qn(rng, terms[2]))
            display = N(total)
            final = final_tpl.format(v=display)
    else:
        words = rng.random() < 0.2
        parts = [qn(rng, terms[0])]
        for op, value in zip(ops, terms[1:]):
            sign = ("mais" if op == "+" else "menos") if words else op
            parts.append(f"{sign} {qn(rng, value)}")
        expr = " ".join(parts)
        question = rng.choice([
            f"Quanto é {expr}?", f"Calcule {expr}.", f"Quanto dá {expr}?", f"Qual o resultado de {expr}?",
            f"{expr} = ?", f"Faz essa conta pra mim: {expr}", f"Me diz quanto dá {expr}.",
        ])
        display = N(total)
        final = rng.choice([f"Resultado: {display}.", f"O resultado é {display}.", f"Dá {display}.",
                            f"Total: {display}.", f"Fica {display}."])
    d.result("total", total, display)
    return Out(question, steps if len(steps) > 1 or rng.random() < 0.5 else [], final, display)


@kind("multiplicacao", "aritmetica/inteiros", 0.8)
def k_multiplicacao(rng, d):
    stories = [
        (lambda: (rng.randint(5, 400), rng.choice([6, 10, 12, 20, 24, 30, 48, 50])),
         "Uma caixa tem {b} unidades. Quantas unidades há em {a} caixas?", "São {v} unidades."),
        (lambda: (rng.randint(8, 40), rng.randint(10, 35)),
         "Um auditório tem {a} fileiras com {b} cadeiras cada. Quantas cadeiras há ao todo?", "Há {v} cadeiras."),
        (lambda: (rng.randint(5, 120), rng.randint(12, 60)),
         "Uma impressora faz {b} páginas por minuto. Quantas páginas ela imprime em {a} minutos?", "Ela imprime {v} páginas."),
        (lambda: (rng.randint(12, 52), rng.randint(3, 25)),
         "Corro {b} km por semana. Quantos km corro em {a} semanas mantendo o ritmo?", "Seriam {v} km."),
        (lambda: (rng.randint(2, 60), rng.randint(25, 400)),
         "Um prédio tem {a} andares e cada andar usa {b} lâmpadas. Quantas lâmpadas são no total?", "São {v} lâmpadas."),
    ]
    if rng.random() < 0.35:
        make, template, final_tpl = rng.choice(stories)
        a, b = make()
    else:
        template = None
        a = rng.choice([rng.randint(12, 99), rng.randint(100, 999), rng.randint(1000, 9999)])
        b = rng.choice([rng.randint(3, 9), rng.randint(11, 99), rng.randint(11, 99), rng.randint(100, 400)])
    d.given(a=a, b=b)
    prod = d.calc("produto", f"{a} * {b}", a * b)
    if 10 < b < 100 and b % 10 and rng.random() < 0.6:
        tens, units = b - b % 10, b % 10
        p1 = d.calc("parcial_dezenas", f"{a} * {tens}", a * tens)
        p2 = d.calc("parcial_unidades", f"{a} * {units}", a * units)
        d.calc("soma_parciais", f"{p1} + {p2}", p1 + p2)
        steps = [f"{N(a)} × {b} = {N(a)} × {tens} + {N(a)} × {units}", f"{N(a)} × {tens} = {N(p1)}",
                 f"{N(a)} × {units} = {N(p2)}", f"{N(p1)} + {N(p2)} = {N(prod)}"]
    else:
        steps = [f"{N(a)} × {N(b)} = {N(prod)}"]
    display = N(prod)
    if template:
        question = template.format(a=qn(rng, a), b=qn(rng, b))
        final = final_tpl.format(v=display)
    else:
        x, y = qn(rng, a), qn(rng, b)
        question = rng.choice([f"Quanto é {x} × {y}?", f"Quanto dá {x} vezes {y}?", f"Multiplica {x} por {y} pra mim.",
                               f"Qual o produto de {x} por {y}?", f"{x} x {y} = ?", f"Calcule {x} · {y}."])
        final = rng.choice([f"Resultado: {display}.", f"{N(a)} × {N(b)} = {display}.", f"O produto é {display}."])
        if len(steps) == 1:
            steps = []
    d.result("produto", prod, display)
    return Out(question, steps, final, display)


@kind("divisao", "aritmetica/inteiros", 1.0)
def k_divisao(rng, d):
    mode = rng.choices(["resto", "exata", "decimal", "teto"], [30, 20, 20, 30])[0]
    d.param(modo=mode)
    if mode == "teto":
        stories = [
            ((8, 20), (20, 300), "Vão {a} pessoas para a excursão e cada van leva {b} passageiros. Quantas vans precisamos contratar?",
             "pessoas", "van", "vans", "São necessárias {k} vans."),
            ((12, 40), (50, 900), "Tenho {a} livros para encaixotar e cabem {b} em cada caixa. Quantas caixas vou usar?",
             "livros", "caixa", "caixas", "Você vai usar {k} caixas."),
            ((4, 10), (30, 250), "Na festa teremos {a} convidados e cada mesa tem {b} lugares. Quantas mesas preciso alugar?",
             "convidados", "mesa", "mesas", "Precisa alugar {k} mesas."),
            ((40, 50), (100, 900), "A escola vai levar {a} alunos ao museu em ônibus de {b} lugares. Quantos ônibus são necessários?",
             "alunos", "ônibus", "ônibus", "São necessários {k} ônibus."),
            ((15, 60), (100, 2000), "Preciso guardar {a} fotos e cada página do álbum comporta {b}. Quantas páginas vou ocupar?",
             "fotos", "página", "páginas", "Vai ocupar {k} páginas."),
        ]
        (blo, bhi), (alo, ahi), template, things, one, many, final_tpl = rng.choice(stories)
        b = rng.randint(blo, bhi)
        a = rng.randint(max(alo, b + 1), ahi)
        if a % b == 0:
            a += rng.randint(1, b - 1)
        q, r = divmod(a, b)
        k = q + 1
        d.given(a=a, b=b)
        d.calc("quociente", f"{a} // {b}", q)
        d.calc("resto", f"{a} % {b}", r)
        d.calc("arredonda_para_cima", f"-(-{a} // {b})", k)
        steps = [f"{N(a)} ÷ {b} = {N(q)}, sobrando {r} {things}",
                 f"essas {r} {things} ainda precisam de mais uma {one}: {N(q)} + 1 = {N(k)}"
                 if one in ("van", "caixa", "mesa", "página") else
                 f"esses {r} {things} ainda precisam de mais um {one}: {N(q)} + 1 = {N(k)}"]
        question = template.format(a=qn(rng, a), b=b)
        display = N(k)
        d.result("arredondado_para_cima", k, display)
        return Out(question, steps, final_tpl.format(k=display), f"{display} {many}")

    b = rng.randint(2, 60)
    q = rng.randint(2, 2500)
    r = 0 if mode == "exata" else rng.randint(1, b - 1)
    a = b * q + r
    if mode == "resto":
        stories = [
            ("Tenho {a} balas para dividir igualmente entre {b} crianças. Quantas cada uma recebe e quantas sobram?",
             "Cada criança recebe {q} balas e sobram {r}.", None),
            ("Vou distribuir {a} folhetos igualmente entre {b} voluntários. Quantos fica para cada um e quantos sobram?",
             "Cada voluntário fica com {q} folhetos e sobram {r}.", None),
            ("{a} dias equivalem a quantas semanas completas e quantos dias?",
             "{a} dias = {q} semanas e {r} dias.", 7),
            ("Tenho {a} ovos e vou guardar em caixas de uma dúzia. Quantas caixas completas e quantos ovos soltos?",
             "Ficam {q} caixas completas e {r} ovos soltos.", 12),
        ]
        template, final_tpl, fixed_b = rng.choice(stories + [(None, None, None)] * 3)
        if fixed_b:
            b, q = fixed_b, rng.randint(2, 400)
            r = rng.randint(1, b - 1)
            a = b * q + r
            d.given(a=a)
            d.const(b=b)
        else:
            if template:
                b, q = rng.randint(2, 30), rng.randint(2, 200)
                r = rng.randint(1, b - 1)
                a = b * q + r
            d.given(a=a, b=b)
        d.calc("quociente", f"{a} // {b}", q)
        d.calc("resto", f"{a} % {b}", r)
        prova = d.calc("prova", f"{b} * {q} + {r}", a)
        steps = [f"{N(a)} ÷ {b} = {N(q)}, com resto {r}", f"prova: {b} × {N(q)} + {r} = {N(prova)}"]
        if template:
            question = template.format(a=qn(rng, a), b=b)
            final = final_tpl.format(a=N(a), q=N(q), r=r)
        else:
            x = qn(rng, a)
            question = rng.choice([f"Qual o quociente e o resto da divisão de {x} por {b}?",
                                   f"Divide {x} por {b}: quanto dá e quanto sobra?",
                                   f"Quanto é {x} ÷ {b}, com resto?", f"Faça a divisão inteira de {x} por {b}."])
            final = rng.choice([f"Quociente {N(q)} e resto {r}.", f"Dá {N(q)} e sobra {r}.",
                                f"{N(a)} ÷ {b} = {N(q)}, resto {r}."])
        d.result("quociente_resto", [q, r], [N(q), str(r)])
        return Out(question, steps, final, f"quociente {N(q)}, resto {r}")
    d.given(a=a, b=b)
    if mode == "exata":
        story = rng.random() < 0.3
        value = d.calc("divisao", f"{a} / {b}", q)
        d.calc("prova", f"{b} * {q}", a)
        steps = [f"{N(a)} ÷ {b} = {N(q)}", f"conferindo: {b} × {N(q)} = {N(a)}"]
        if story:
            question = (f"Um prêmio de {qm(rng, a)} vai ser dividido igualmente entre {b} ganhadores. "
                        "Quanto recebe cada um?")
            display = M(value)
            final = f"Cada ganhador recebe {display}."
        else:
            x = qn(rng, a)
            question = rng.choice([f"Quanto é {x} dividido por {b}?", f"Calcule {x} ÷ {b}.", f"Quanto dá {x} / {b}?",
                                   f"Divide {x} por {b} pra mim."])
            display = N(value)
            final = rng.choice([f"Resultado: {display}.", f"{N(a)} ÷ {b} = {display}.", f"Dá {display}, divisão exata."])
        d.result("divisao", value, display)
        return Out(question, steps, final, display)
    # decimal
    exact = Fraction(a, b)
    v4 = d.calc("divisao_4_casas", f"{a} / {b}", exact, places=4)
    d.calc("divisao_2_casas", f"{a} / {b}", exact, places=2)
    d.calc("quociente", f"{a} // {b}", q)
    d.calc("resto", f"{a} % {b}", r)
    steps = [f"{N(a)} ÷ {b} = {N(q)} e sobra {r}", f"seguindo com as casas decimais: {N(a)} ÷ {b} {eq_or_approx(v4, 4)} {N(v4, 4)}"]
    x = qn(rng, a)
    question = rng.choice([f"Quanto dá {x} dividido por {b}, com duas casas decimais?",
                           f"Divide {x} por {b} e me dá o resultado com vírgula.",
                           f"Qual o resultado decimal de {x} ÷ {b}?", f"{x} / {b} dá quanto em decimal?"])
    display = N(exact, 2)
    final = f"Com duas casas decimais: {approx(exact)}{display}."
    d.result("divisao_decimal", exact, display)
    return Out(question, steps, final, f"{approx(exact)}{display}")


@kind("expressao", "aritmetica/inteiros", 1.0, school=True)
def k_expressao(rng, d):
    form = rng.randint(1, 8)
    r = rng.randint
    if form == 1:  # a + b × c
        a, b, c = r(2, 99), r(2, 30), r(2, 30)
        bc = d.calc("multiplicacao", f"{b} * {c}", b * c)
        res = d.calc("soma", f"{a} + {bc}", a + bc)
        shown, py = f"{a} + {b} × {c}", f"{a} + {b} * {c}"
        steps = [f"primeiro a multiplicação: {b} × {c} = {N(bc)}", f"depois a soma: {a} + {N(bc)} = {N(res)}"]
        values = [a, b, c]
    elif form == 2:  # (a + b) × c
        a, b, c = r(2, 60), r(2, 60), r(2, 25)
        s = d.calc("parenteses", f"{a} + {b}", a + b)
        res = d.calc("multiplicacao", f"{s} * {c}", s * c)
        shown, py = f"({a} + {b}) × {c}", f"({a} + {b}) * {c}"
        steps = [f"primeiro os parênteses: {a} + {b} = {s}", f"depois: {s} × {c} = {N(res)}"]
        values = [a, b, c]
    elif form == 3:  # a × b - c ÷ e
        a, b, e = r(2, 30), r(2, 30), r(2, 12)
        c = e * r(2, 20)
        p = d.calc("multiplicacao", f"{a} * {b}", a * b)
        qt = d.calc("divisao", f"{c} // {e}", c // e)
        res = d.calc("subtracao", f"{p} - {qt}", p - qt)
        shown, py = f"{a} × {b} - {c} ÷ {e}", f"{a} * {b} - {c} / {e}"
        steps = [f"multiplicação: {a} × {b} = {N(p)}", f"divisão: {c} ÷ {e} = {N(qt)}", f"subtração: {N(p)} - {N(qt)} = {N(res)}"]
        values = [a, b, c, e]
    elif form == 4:  # a - (b - c) × e
        a, b, c, e = r(10, 200), r(5, 40), r(1, 30), r(2, 9)
        if c >= b:
            b, c = c + r(1, 10), b
        s = d.calc("parenteses", f"{b} - {c}", b - c)
        p = d.calc("multiplicacao", f"{s} * {e}", s * e)
        res = d.calc("subtracao", f"{a} - {p}", a - p)
        shown, py = f"{a} - ({b} - {c}) × {e}", f"{a} - ({b} - {c}) * {e}"
        steps = [f"parênteses: {b} - {c} = {s}", f"multiplicação: {s} × {e} = {N(p)}", f"subtração: {a} - {N(p)} = {N(res)}"]
        values = [a, b, c, e]
    elif form == 5:  # (a + b)² - c
        a, b, c = r(1, 12), r(1, 12), r(1, 99)
        s = d.calc("parenteses", f"{a} + {b}", a + b)
        sq = d.calc("potencia", f"{s} ** 2", s * s)
        res = d.calc("subtracao", f"{sq} - {c}", sq - c)
        shown, py = f"({a} + {b})² - {c}", f"({a} + {b}) ** 2 - {c}"
        steps = [f"parênteses: {a} + {b} = {s}", f"potência: {s}² = {sq}", f"subtração: {sq} - {c} = {N(res)}"]
        values = [a, b, c]
    elif form == 6:  # a ÷ b + c × e
        b, c, e = r(2, 12), r(2, 20), r(2, 20)
        a = b * r(2, 30)
        qt = d.calc("divisao", f"{a} // {b}", a // b)
        p = d.calc("multiplicacao", f"{c} * {e}", c * e)
        res = d.calc("soma", f"{qt} + {p}", qt + p)
        shown, py = f"{a} ÷ {b} + {c} × {e}", f"{a} / {b} + {c} * {e}"
        steps = [f"divisão: {a} ÷ {b} = {qt}", f"multiplicação: {c} × {e} = {p}", f"soma: {qt} + {p} = {N(res)}"]
        values = [a, b, c, e]
    elif form == 7:  # [a + (b - c)] × e
        a, b, c, e = r(1, 40), r(5, 40), r(1, 30), r(2, 9)
        if c >= b:
            b, c = c + r(1, 10), b
        inner = d.calc("parenteses", f"{b} - {c}", b - c)
        br_ = d.calc("colchetes", f"{a} + {inner}", a + inner)
        res = d.calc("multiplicacao", f"{br_} * {e}", br_ * e)
        shown, py = f"[{a} + ({b} - {c})] × {e}", f"({a} + ({b} - {c})) * {e}"
        steps = [f"parênteses: {b} - {c} = {inner}", f"colchetes: {a} + {inner} = {br_}", f"multiplicação: {br_} × {e} = {N(res)}"]
        values = [a, b, c, e]
    else:  # a² + b² - c
        a, b, c = r(2, 15), r(2, 15), r(0, 50)
        a2 = d.calc("quadrado_a", f"{a} ** 2", a * a)
        b2 = d.calc("quadrado_b", f"{b} ** 2", b * b)
        res = d.calc("soma_subtracao", f"{a2} + {b2} - {c}", a2 + b2 - c)
        shown, py = f"{a}² + {b}² - {c}", f"{a} ** 2 + {b} ** 2 - {c}"
        steps = [f"potências: {a}² = {a2} e {b}² = {b2}", f"{a2} + {b2} - {c} = {N(res)}"]
        values = [a, b, c]
    d.given(numeros=values)
    d.param(forma=form)
    total = d.calc("expressao", py, res)
    display = N(total)
    question = rng.choice([
        f"Resolva a expressão {shown}.", f"Quanto vale {shown}?", f"Calcule {shown}, respeitando a ordem das operações.",
        f"Qual o valor de {shown}?", f"Sempre me confundo com a ordem das contas: quanto dá {shown}?",
        f"Resolve {shown} pra mim?", f"Qual o resultado de {shown}?",
    ])
    final = rng.choice([f"Resultado: {display}.", f"Portanto, {shown} = {display}.", f"O valor da expressão é {display}."])
    d.result("valor", total, display)
    return Out(question, steps, final, display)


@kind("potencia_raiz", "aritmetica/inteiros", 0.7, school=True)
def k_potencia_raiz(rng, d):
    mode = rng.choices(["potencia", "raiz_exata", "raiz_aprox"], [40, 35, 25])[0]
    d.param(modo=mode)
    if mode == "potencia":
        if rng.random() < 0.3:
            base, exp = 2, rng.randint(5, 16)
        else:
            base, exp = rng.randint(2, 20), rng.choice([2, 2, 3, 3, 4])
        value = d.calc("potencia", f"{base} ** {exp}", base ** exp)
        shown = rng.choice([f"{base}{str(exp).translate(SUP)}", f"{base}^{exp}", f"{base} elevado a {exp}"])
        if str(exp).translate(SUP) in shown:
            d.given(base=base)
            d.const(expoente=exp)
        else:
            d.given(base=base, expoente=exp)
        if exp <= 4:
            steps = [f"{base}{str(exp).translate(SUP)} = " + " × ".join([str(base)] * exp) + f" = {N(value)}"]
        else:
            half = exp // 2
            h = d.calc("metade", f"{base} ** {half}", base ** half)
            rest = exp - 2 * half
            if rest:
                steps = [f"{base}^{exp} = {base}^{half} × {base}^{half} × {base}",
                         f"{base}^{half} = {N(h)}", f"{N(h)} × {N(h)} × {base} = {N(value)}"]
                d.calc("recomposicao", f"{h} * {h} * {base}", h * h * base)
            else:
                steps = [f"{base}^{exp} = {base}^{half} × {base}^{half}", f"{base}^{half} = {N(h)}",
                         f"{N(h)} × {N(h)} = {N(value)}"]
                d.calc("recomposicao", f"{h} * {h}", h * h)
        question = rng.choice([f"Quanto é {shown}?", f"Calcule {shown}.", f"Qual o valor de {shown}?",
                               f"Me diz quanto dá {shown}."])
        display = N(value)
        d.result("potencia", value, display)
        return Out(question, steps, rng.choice([f"Resultado: {display}.", f"{shown} = {display}."]), display)
    if mode == "raiz_exata":
        k = rng.randint(2, 120)
        n = k * k
        d.given(n=n)
        root = d.calc("raiz", f"{n} ** 0.5", k)
        d.calc("prova", f"{k} * {k}", n)
        question = rng.choice([f"Qual a raiz quadrada de {qn(rng, n)}?", f"Quanto é √{n}?", f"Calcule a raiz quadrada de {qn(rng, n)}.",
                               f"√{n} dá quanto?"])
        steps = [f"procuro o número que multiplicado por ele mesmo dá {N(n)}: {k} × {k} = {N(n)}"]
        display = N(root)
        d.result("raiz", root, display)
        return Out(question, steps, rng.choice([f"√{N(n)} = {display}.", f"A raiz quadrada de {N(n)} é {display}."]), display)
    while True:
        n = rng.randint(2, 999)
        if math.isqrt(n) ** 2 != n:
            break
    low = math.isqrt(n)
    t = math.isqrt(n * 10**6)  # floor(sqrt(n) * 1000)
    rounded = Fraction(t // 10 + (1 if t % 10 >= 5 else 0), 100)
    d.given(n=n)
    d.calc("quadrado_abaixo", f"{low} * {low}", low * low)
    d.calc("quadrado_acima", f"{low + 1} * {low + 1}", (low + 1) ** 2)
    value = d.calc("raiz_arredondada", f"round({n} ** 0.5, 2)", rounded)
    lo_b, hi_b = rounded - Fraction(1, 200), rounded + Fraction(1, 200)
    d.test("limites", f"{lit(lo_b)} ** 2 <= {n} < {lit(hi_b)} ** 2", lo_b ** 2 <= n < hi_b ** 2)
    steps = [f"{low}² = {N(low * low)} e {low + 1}² = {N((low + 1) ** 2)}, então √{N(n)} fica entre {low} e {low + 1}",
             f"com duas casas decimais, √{N(n)} ≈ {N(value)}"]
    question = rng.choice([f"Qual a raiz quadrada de {qn(rng, n)}, aproximadamente?", f"Quanto dá √{n} com duas casas decimais?",
                           f"Me ajuda a estimar a raiz quadrada de {qn(rng, n)}."])
    display = N(value)
    d.result("raiz_aproximada", value, display)
    return Out(question, steps, f"√{N(n)} ≈ {display}.", f"≈ {display}")


@kind("expressao_direta", "aritmetica/inteiros", 0.6)
def k_expressao_direta(rng, d):
    r = rng.randint
    forms = [
        lambda: (f"({r(100, 5000)} - {r(1, 99)}) / {r(2, 9)}"),
        lambda: (f"{r(10, 999)} * {r(2, 99)} + {r(1, 999)}"),
        lambda: (f"({r(1, 99)} + {r(1, 99)} + {r(1, 99)}) / 3"),
        lambda: (f"{r(2, 30)} ** 2 - {r(1, 200)}"),
        lambda: (f"{r(1000, 99999)} // {r(2, 99)}"),
        lambda: (f"{r(1000, 99999)} % {r(2, 99)}"),
        lambda: (f"({r(10, 500)} + {r(10, 500)}) * {r(2, 12)} - {r(1, 300)}"),
    ]
    expr = rng.choice(forms)()
    exact = eval_exact(expr)
    d.given(numeros=[int(x) for x in re.findall(r"\d+", expr)])
    d.param(expressao=expr)
    places = 4
    value = d.calc("expressao", expr, exact, places=places)
    display = N(value, places)
    question = rng.choice([f"calcule {expr}", f"Calcule {expr}", f"avalie a expressão {expr}", f"quanto é {expr}?",
                           f"Qual o resultado de {expr}?", f"Quanto dá {expr}?"])
    prefix = approx(value, places)
    final = rng.choice([f"{expr} {eq_or_approx(value, places)} {display}.", f"Resultado: {prefix}{display}."])
    d.result("valor", value, display)
    return Out(question, [], final, f"{prefix}{display}")


def eval_exact(expression: str) -> Fraction:
    """Avaliador exato mínimo, usado só para montar os valores esperados acima."""
    import ast

    def ev(node):
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, int):
            return Fraction(node.value)
        if isinstance(node, ast.BinOp):
            left, right = ev(node.left), ev(node.right)
            ops = {ast.Add: lambda: left + right, ast.Sub: lambda: left - right, ast.Mult: lambda: left * right,
                   ast.Div: lambda: left / right, ast.FloorDiv: lambda: Fraction(left // right),
                   ast.Mod: lambda: left % right, ast.Pow: lambda: left ** int(right)}
            return ops[type(node.op)]()
        raise ValueError("forma não suportada")
    return ev(ast.parse(expression, mode="eval"))


# --------------------------------------------------------------- decimais e frações

def rand_dec(rng, lo, hi, places):
    scale = 10 ** places
    return Fraction(rng.randint(int(lo * scale), int(hi * scale)), scale)


def places_of(value) -> int:
    text = exact_str(value)
    return len(text.split(".")[1]) if "." in text else 0


@kind("decimais", "aritmetica/decimais", 1.0, school=True)
def k_decimais(rng, d):
    op = rng.choice(["+", "-", "×", "÷"])
    d.param(operacao=op)
    if op in "+-":
        a = rand_dec(rng, 0.1, 999, rng.choice([1, 2]))
        b = rand_dec(rng, 0.1, 500, rng.choice([1, 2]))
        if op == "-" and b > a:
            a, b = b, a
        if a == b:
            raise Reject("trivial")
        k = max(places_of(a), places_of(b), 1)
        value = a + b if op == "+" else a - b
        res = d.calc("resultado", f"{lit(a)} {op} {lit(b)}", value)
        steps = [f"alinhando as vírgulas: {br(a, k, fixed=True)} {op} {br(b, k, fixed=True)} = {br(res, k, fixed=True)}"]
        word = "Some" if op == "+" else "Subtraia"
        x, y = N(a), N(b)
        question = rng.choice([f"Quanto é {x} {op} {y}?", f"{word} {x} {'com' if op == '+' else 'de'} {y}." if op == "+" else f"Subtraia {y} de {x}.",
                               f"Quanto dá {x} {'mais' if op == '+' else 'menos'} {y}?",
                               f"Qual a {'soma' if op == '+' else 'diferença'} entre {x} e {y}?"])
    elif op == "×":
        a = rand_dec(rng, 0.1, 99, 1)
        b = rand_dec(rng, 0.1, 50, rng.choice([1, 2]))
        if a.denominator == 1 and b.denominator == 1:
            raise Reject("trivial")
        ka, kb = places_of(a), places_of(b)
        ia, ib = int(a * 10**ka), int(b * 10**kb)
        whole = d.calc("sem_virgula", f"{ia} * {ib}", ia * ib)
        res = d.calc("resultado", f"{lit(a)} * {lit(b)}", a * b)
        casas = ka + kb
        steps = [f"sem as vírgulas: {N(ia)} × {N(ib)} = {N(whole)}",
                 f"são {casas} casa{'s' if casas > 1 else ''} decimal{'is' if casas > 1 else ''} no total, então fica {br(res, casas, fixed=True)}"
                 + (f" = {N(res, casas)}" if br(res, casas, fixed=True) != N(res, casas) else "")]
        x, y = N(a), N(b)
        question = rng.choice([f"Quanto é {x} × {y}?", f"Quanto dá {x} vezes {y}?", f"Multiplique {x} por {y}.",
                               f"Qual o produto de {x} e {y}?"])
    else:
        q = rand_dec(rng, 0.5, 80, rng.choice([0, 1, 1, 2]))
        b = rand_dec(rng, 0.2, 9.9, 1)
        a = q * b
        if places_of(a) > 3 or b.denominator == 1:
            raise Reject("feio")
        k = max(places_of(a), places_of(b))
        ia, ib = int(a * 10**k), int(b * 10**k)
        res = d.calc("resultado", f"{lit(a)} / {lit(b)}", q)
        d.calc("sem_virgula", f"{ia} / {ib}", q)
        steps = [f"multiplicando os dois por {N(10 ** k)} para tirar a vírgula: {N(ia)} ÷ {N(ib)} = {N(q, 3)}"]
        x, y = N(a, 3), N(b)
        question = rng.choice([f"Quanto é {x} ÷ {y}?", f"Divide {x} por {y} pra mim.", f"{x} dividido por {y} dá quanto?",
                               f"Calcule {x} / {y}."])
        a = frac(a)
    d.given(a=a, b=b)
    display = N(res, 4)
    d.result("resultado", res, display)
    final = rng.choice([f"Resultado: {display}.", f"Dá {display}.", f"A resposta é {display}."])
    return Out(question, steps, final, display)


def euclid_steps(d, a, b):
    """MDC pelo algoritmo de Euclides, cada resto conferido pelo motor."""
    steps = []
    x, y = max(a, b), min(a, b)
    while y:
        rest = d.calc(f"resto_{x}_{y}", f"{x} % {y}", x % y)
        steps.append(f"{x} ÷ {y} deixa resto {rest}")
        x, y = y, int(rest)
    return x, steps


@kind("fracoes", "aritmetica/fracoes", 0.9, school=True)
def k_fracoes(rng, d):
    mode = rng.choices(["terminante", "periodica", "simplificar"], [40, 25, 35])[0]
    d.param(modo=mode)
    if mode == "simplificar":
        g = rng.randint(2, 15)
        n0, m0 = rng.randint(1, 15), rng.randint(2, 20)
        if math.gcd(n0, m0) != 1 or n0 == m0:
            raise Reject("feio")
        n, m = n0 * g, m0 * g
        d.given(numerador=n, denominador=m)
        gcd, steps = euclid_steps(d, n, m)
        if gcd != g:
            raise Reject("mdc")
        a = d.calc("numerador_simplificado", f"{n} // {gcd}", n0)
        b = d.calc("denominador_simplificado", f"{m} // {gcd}", m0)
        d.test("mesma_razao", f"{n} / {m} == {n0} / {m0}", True)
        steps = steps[:3] + [f"o MDC de {n} e {m} é {gcd}", f"{n} ÷ {gcd} = {a} e {m} ÷ {gcd} = {b}"]
        question = rng.choice([f"Simplifique a fração {n}/{m}.", f"Qual a forma irredutível de {n}/{m}?",
                               f"Como fica {n}/{m} simplificada ao máximo?"])
        display = f"{a}/{b}"
        d.result("fracao", [a, b], [display])
        return Out(question, steps, rng.choice([f"{n}/{m} = {display}.", f"A fração irredutível é {display}."]), display)
    if mode == "terminante":
        m = rng.choice([2, 4, 5, 8, 10, 16, 20, 25, 40, 50])
        n = rng.randint(1, 2 * m)
    else:
        m = rng.choice([3, 6, 7, 9, 11, 12, 15])
        n = rng.randint(1, 2 * m)
    if n % m == 0:
        raise Reject("trivial")
    value = Fraction(n, m)
    d.given(numerador=n, denominador=m)
    if mode == "terminante":
        dec = d.calc("decimal", f"{n} / {m}", value)
        pct = d.calc("porcentagem", f"{n} / {m} * 100", value * 100)
        steps = [f"{n} ÷ {m} = {N(dec, 6)}", f"{N(dec, 6)} × 100 = {N(pct, 4)}%"]
        short = f"{N(dec, 6)} ({N(pct, 4)}%)"
        displays = [N(dec, 6), N(pct, 4) + "%"]
    else:
        dec = d.calc("decimal", f"{n} / {m}", value, places=4)
        pct = d.calc("porcentagem", f"{n} / {m} * 100", value * 100, places=2)
        steps = [f"{n} ÷ {m} = {N(dec, 4)}… (a divisão não termina)",
                 f"arredondando: ≈ {N(dec, 2)}, ou cerca de {N(pct, 2)}%"]
        d.calc("decimal_2_casas", f"{n} / {m}", value, places=2)
        short = f"≈ {N(dec, 2)} (≈ {N(pct, 2)}%)"
        displays = [N(dec, 2), N(pct, 2) + "%"]
    question = rng.choice([f"Quanto é {n}/{m} em decimal e em porcentagem?", f"Transforma a fração {n}/{m} em número decimal e em porcentagem.",
                           f"{n}/{m} corresponde a quantos por cento? E em decimal?", f"Escreva {n}/{m} como decimal e como porcentagem."])
    d.result("decimal_porcentagem", [value, value * 100], displays)
    final = f"{n}/{m} {'=' if mode == 'terminante' else '≈'} {displays[0]} {'=' if mode == 'terminante' else '≈'} {displays[1]}."
    return Out(question, steps, final, short)


# --------------------------------------------------------------- porcentagem

PCT_COMMON = [5, 10, 12, 15, 20, 25, 30, 40, 50, 60, 75, 8, 18, 35, 45, 3, 2, 1]
PCT_DEC = [Fraction(5, 2), Fraction(15, 2), Fraction(25, 2), Fraction(1, 2), Fraction(3, 2), Fraction(7, 2), Fraction(45, 2)]


def pick_pct(rng):
    roll = rng.random()
    if roll < 0.6:
        return Fraction(rng.choice(PCT_COMMON))
    if roll < 0.8:
        return rng.choice(PCT_DEC)
    return Fraction(rng.randint(1, 99))


@kind("porcentagem_de", "porcentagem", 1.3)
def k_porcentagem_de(rng, d):
    for _ in range(30):
        p = pick_pct(rng)
        context = rng.choice(["plain", "plain", "money", "money", "people"])
        if context == "people":
            base = rng.choice([rng.randint(20, 900), rng.randint(100, 50000)])
        elif context == "money":
            base = price(rng, 10, 9000)
        else:
            base = Fraction(rng.choice([rng.randint(10, 999), rng.randint(1000, 99999), rng.randint(10, 9999) * 10]))
        value = base * p / 100
        if context == "people" and value.denominator != 1:
            continue
        if context == "money" and not exact_at(value, 2) and rng.random() < 0.7:
            continue
        if context == "plain" and not exact_at(value, 3):
            continue
        break
    else:
        raise Reject("sem-exemplo")
    d.given(porcentagem=p, base=base)
    d.param(contexto=context)
    places = 2 if context == "money" else 3
    rate = p / 100
    style = rng.choice(["fator", "divide100", "regra10"] if p in (10, 20, 30, 5) else ["fator", "divide100"])
    if style == "fator" and terminating(rate):
        d.calc("taxa", f"{lit(p)} / 100", rate)
        v = d.calc("valor", f"{lit(base)} * {lit(rate)}", value, places=places)
        fmt_base = M(base) if context == "money" else N(base)
        steps = [f"{P(p)} = {N(rate, 4)}", f"{fmt_base} × {N(rate, 4)} {eq_or_approx(v, places)} {M(v) if context == 'money' else N(v, places)}"]
    elif style == "regra10":
        ten = d.calc("dez_por_cento", f"{lit(base)} / 10", base / 10)
        v = d.calc("valor", f"{lit(base)} / 10 * {lit(p / 10)}", value, places=places)
        fmt = M if context == "money" else (lambda x: N(x, 3))
        if p == 5:
            steps = [f"10% de {fmt(base)} = {fmt(ten)}", f"5% é a metade disso: {fmt(ten)} ÷ 2 {eq_or_approx(v, places)} {fmt(v)}"]
        elif p == 10:
            steps = [f"10% é só dividir por 10: {fmt(base)} ÷ 10 {eq_or_approx(v, places)} {fmt(v)}"]
        else:
            steps = [f"10% de {fmt(base)} = {fmt(ten)}", f"{P(p)} = {N(p / 10)} × {fmt(ten)} {eq_or_approx(v, places)} {fmt(v)}"]
    else:
        one = d.calc("um_por_cento", f"{lit(base)} / 100", base / 100, places=4)
        v = d.calc("valor", f"{lit(base)} / 100 * {lit(p)}", value, places=places)
        fmt = M if context == "money" else (lambda x: N(x, 3))
        steps = [f"1% de {fmt(base)} {eq_or_approx(one, 4)} {N(one, 4)}", f"{N(one, 4)} × {N(p)} {eq_or_approx(v, places)} {fmt(v)}"]
    pp = P(p)
    if context == "money":
        b = qm(rng, base)
        question, final_tpl = rng.choice([
            (f"Quanto é {pp} de {b}?", "{pp} de {b} = {v}."),
            (f"A taxa é de {pp} sobre {b}. Quanto dá?", "A taxa fica em {v}."),
            (f"Quero deixar {pp} de gorjeta numa conta de {b}. Quanto deixo?", "A gorjeta é de {v}."),
            (f"Vou dar {pp} de entrada num carro de {b}. Qual o valor da entrada?", "A entrada é de {v}."),
            (f"A comissão é de {pp} sobre as vendas. Vendi {b} este mês. Quanto recebo de comissão?", "Sua comissão é de {v}."),
        ])
        display = M(value)
        final = final_tpl.format(pp=pp, b=M(base), v=approx(value) + display)
    elif context == "people":
        b = qn(rng, base)
        question, final_tpl = rng.choice([
            (f"Numa escola com {b} alunos, {pp} usam transporte escolar. Quantos alunos são?", "São {v} alunos."),
            (f"{pp} dos {b} funcionários da empresa vão trabalhar remoto. Quantas pessoas são?", "São {v} pessoas."),
            (f"Numa pesquisa com {b} pessoas, {pp} disseram que preferem café a chá. Quantas pessoas responderam isso?", "{v} pessoas."),
            (f"De {b} inscritos, {pp} faltaram à prova. Quantos faltaram?", "Faltaram {v} inscritos."),
        ])
        display = N(value)
        final = final_tpl.format(v=display)
    else:
        b = qn(rng, base)
        question = rng.choice([f"Quanto é {pp} de {b}?", f"Calcula {pp} de {b}.", f"{N(p)} por cento de {b} dá quanto?",
                               f"Qual o valor de {pp} sobre {b}?", f"Me diz quanto é {pp} de {b}."])
        display = N(value, 3)
        final = rng.choice([f"{pp} de {N(base)} = {display}.", f"Resultado: {display}.", f"Dá {display}."])
    d.result("valor", value, display)
    return Out(question, steps, final, approx(value, places) + display)


@kind("que_porcentagem", "porcentagem", 1.0)
def k_que_porcentagem(rng, d):
    whole = rng.choice([rng.randint(8, 100), rng.randint(100, 5000)])
    part = rng.randint(1, whole - 1)
    context = rng.choice(["plain", "questoes", "convidados", "orcamento", "livro"])
    if context == "questoes":
        whole = rng.choice([10, 20, 25, 30, 40, 45, 50, 60, 80, 90, 100, 120, 180])
        part = rng.randint(1, whole)
    ratio = Fraction(part, whole)
    pct_exact = ratio * 100
    d.given(parte=part, total=whole)
    d.param(contexto=context)
    r4 = d.calc("razao", f"{part} / {whole}", ratio, places=4)
    pct = d.calc("porcentagem", f"{part} / {whole} * 100", pct_exact, places=2)
    steps = [f"{N(part)} ÷ {N(whole)} {eq_or_approx(r4, 4)} {N(r4, 4)}", f"{N(r4, 4)} × 100 {eq_or_approx(pct, 2)} {P(pct)}"]
    shown = approx(pct) + P(pct)
    if context == "questoes":
        question = rng.choice([f"Acertei {part} de {whole} questões. Qual foi meu percentual de acerto?",
                               f"Numa prova de {whole} questões eu acertei {part}. Quantos por cento é isso?"])
        final = f"Você acertou {shown} das questões."
    elif context == "convidados":
        question = f"Dos {qn(rng, whole)} convidados, {qn(rng, part)} confirmaram presença. Que porcentagem confirmou?"
        final = f"Confirmaram {shown} dos convidados."
    elif context == "orcamento":
        question = f"Já gastei {qm(rng, part)} de um orçamento de {qm(rng, whole)}. Quantos por cento já usei?"
        steps = [f"{M(part)} ÷ {M(whole)} {eq_or_approx(r4, 4)} {N(r4, 4)}", steps[1]]
        final = f"Você já usou {shown} do orçamento."
    elif context == "livro":
        question = f"Já li {qn(rng, part)} das {qn(rng, whole)} páginas do livro. Quantos por cento eu li?"
        final = f"Você leu {shown} do livro."
    else:
        a, b = qn(rng, part), qn(rng, whole)
        question = rng.choice([f"{a} é quanto por cento de {b}?", f"Que porcentagem {a} representa de {b}?",
                               f"Quantos % {a} é de {b}?", f"Qual a porcentagem de {a} em relação a {b}?"])
        final = rng.choice([f"{N(part)} é {shown} de {N(whole)}.", f"Resposta: {shown}."])
    d.result("porcentagem", pct, P(pct))
    return Out(question, steps, final, shown)


@kind("variacao_percentual", "porcentagem", 1.0)
def k_variacao_percentual(rng, d):
    contexts = [
        ("money", "O preço da gasolina passou de {o} para {n} o litro. Qual foi a variação percentual?", (4, 7)),
        ("money", "Minha conta de luz foi de {o} para {n}. Quanto por cento ela mudou?", (80, 600)),
        ("money", "O aluguel era {o} e agora está {n}. Qual foi o reajuste em porcentagem?", (700, 4000)),
        ("money", "Uma ação caiu de {o} para {n}. Qual foi a variação em porcentagem?", (5, 120)),
        ("kg", "Meu peso foi de {o} kg para {n} kg. Quantos por cento isso representa?", (55, 130)),
        ("count", "As vendas da loja passaram de {o} para {n} unidades no mês. Qual a variação percentual?", (100, 9000)),
        ("count", "O perfil tinha {o} seguidores e agora tem {n}. Quanto cresceu ou caiu em porcentagem?", (200, 90000)),
    ]
    unit, template, (lo, hi) = rng.choice(contexts)
    for _ in range(30):
        if unit == "money":
            old = price(rng, lo, hi) if hi > 10 else rand_dec(rng, lo, hi, 2)
            change = Fraction(rng.choice([-1, 1]) * rng.choice([2, 3, 4, 5, 6, 8, 10, 12, 15, 20, 25, 30, rng.randint(1, 40)]), 100)
            if rng.random() < 0.5:
                new = Fraction(quant(old * (1 + change), 2))
            else:
                new = old + rand_dec(rng, -float(old) * 0.3, float(old) * 0.3, 2)
        elif unit == "kg":
            old = rand_dec(rng, lo, hi, 1)
            new = old + rand_dec(rng, -12, 12, 1)
        else:
            old = Fraction(rng.randint(lo, hi))
            new = Fraction(max(1, int(old * Fraction(rng.randint(50, 180), 100))))
        if new != old and new > 0:
            break
    else:
        raise Reject("sem-exemplo")
    d.given(antes=old, depois=new)
    d.param(unidade=unit)
    fmt = M if unit == "money" else (lambda x: N(x, 2))
    diff = d.calc("diferenca", f"{lit(new)} - {lit(old)}", new - old)
    ratio = d.calc("razao", f"({lit(new)} - {lit(old)}) / {lit(old)}", (new - old) / old, places=4)
    pct = d.calc("variacao", f"({lit(new)} - {lit(old)}) / {lit(old)} * 100", (new - old) / old * 100, places=2)
    up = diff > 0
    steps = [f"diferença: {fmt(new)} - {fmt(old)} = {'-' if not up else ''}{fmt(abs(diff))}".replace("-R$ ", "-R$ "),
             f"divido pelo valor inicial: {'-' if not up else ''}{N(abs(diff), 2)} ÷ {N(old, 2)} {eq_or_approx(ratio, 4)} {N(ratio, 4)}",
             f"{N(ratio, 4)} × 100 {eq_or_approx(pct, 2)} {P(pct)}"]
    word = rng.choice(["aumento", "alta"] if up else ["queda", "redução"])
    shown = approx(pct) + P(abs(pct))
    final = rng.choice([f"Houve {'um' if word == 'aumento' else 'uma'} {word} de {shown}.",
                        f"Variação de {approx(pct)}{P(pct)} ({word})."])
    question = template.format(o=qm(rng, old) if unit == "money" else qn(rng, old), n=qm(rng, new) if unit == "money" else qn(rng, new))
    d.result("variacao_percentual", pct, P(abs(pct)))
    return Out(question, steps, final, f"{approx(pct)}{P(pct)}")


@kind("aplicar_percentual", "porcentagem", 1.0)
def k_aplicar_percentual(rng, d):
    contexts = [
        ("money", +1, "Meu aluguel de {v} vai subir {p}. Qual será o novo valor?", "O novo aluguel fica em {r}.", (700, 4500)),
        ("money", +1, "A loja vai aumentar os preços em {p}. Uma peça de {v} vai para quanto?", "A peça passa a custar {r}.", (20, 600)),
        ("money", -1, "Quero reduzir em {p} minha conta de luz, que hoje é de {v}. Qual deve ser a nova conta?", "A meta é uma conta de {r}.", (80, 700)),
        ("money", -1, "Uma mensalidade de {v} vai ter redução de {p}. Quanto fica?", "A mensalidade fica em {r}.", (90, 3000)),
        ("count", +1, "Uma cidade tinha {v} habitantes e cresceu {p}. Quantos habitantes tem agora?", "Agora são {r} habitantes.", (2000, 900000)),
        ("count", -1, "Uma fábrica produzia {v} peças por mês e a produção caiu {p}. Quantas peças produz agora?", "Agora produz {r} peças por mês.", (500, 90000)),
        ("plain", +1, "Aumente {v} em {p}.", "Resultado: {r}.", (10, 99999)),
        ("plain", -1, "Diminua {p} de {v}.", "Resultado: {r}.", (10, 99999)),
    ]
    unit, sign, template, final_tpl, (lo, hi) = rng.choice(contexts)
    for _ in range(40):
        p = pick_pct(rng)
        value = price(rng, lo, hi) if unit == "money" else Fraction(rng.randint(lo, hi))
        delta = value * p / 100
        result = value + sign * delta
        if unit == "count" and result.denominator != 1:
            continue
        if unit == "money" and not exact_at(result, 2):
            continue
        if unit == "plain" and not exact_at(result, 3):
            continue
        break
    else:
        raise Reject("sem-exemplo")
    d.given(valor=value, porcentagem=p)
    d.param(sinal=sign, unidade=unit)
    fmt = M if unit == "money" else (lambda x: N(x, 3))
    op = "+" if sign > 0 else "-"
    factor = 1 + sign * p / 100
    if rng.random() < 0.5:
        dv = d.calc("variacao", f"{lit(value)} * {lit(p)} / 100", delta)
        res = d.calc("resultado", f"{lit(value)} {op} {lit(value)} * {lit(p)} / 100", result)
        steps = [f"{P(p)} de {fmt(value)} = {fmt(dv)}", f"{fmt(value)} {op} {fmt(dv)} = {fmt(res)}"]
    else:
        f = d.calc("fator", f"1 {op} {lit(p)} / 100", factor)
        res = d.calc("resultado", f"{lit(value)} * {lit(f)}", result)
        steps = [f"{'aumentar' if sign > 0 else 'diminuir'} {P(p)} é multiplicar por {N(f, 4)}",
                 f"{fmt(value)} × {N(f, 4)} = {fmt(res)}"]
    question = template.format(v=qm(rng, value) if unit == "money" else qn(rng, value), p=P(p))
    display = fmt(res)
    d.result("resultado", res, display)
    return Out(question, steps, final_tpl.format(r=display), display)


@kind("valor_original", "porcentagem", 0.9)
def k_valor_original(rng, d):
    sign = rng.choice([1, -1])
    for _ in range(40):
        p = Fraction(rng.choice([5, 10, 15, 20, 25, 30, 40, 50, 12, 8]))
        original = Fraction(rng.randint(4, 900) * rng.choice([1, 5, 10]))
        final_price = original * (1 + sign * p / 100)
        if exact_at(final_price, 2):
            break
    else:
        raise Reject("sem-exemplo")
    d.given(preco_atual=final_price, porcentagem=p)
    d.param(sinal=sign)
    factor = 1 + sign * p / 100
    f = d.calc("fator", f"1 {'+' if sign > 0 else '-'} {lit(p)} / 100", factor)
    orig = d.calc("original", f"{lit(final_price)} / {lit(f)}", original)
    d.calc("conferencia", f"{lit(orig)} * {lit(f)}", final_price)
    wrong = final_price * (1 - sign * p / 100)
    w = d.calc("conta_errada", f"{lit(final_price)} * (1 {'-' if sign > 0 else '+'} {lit(p)} / 100)", wrong)
    pct_now = 100 + sign * p
    steps = [f"o preço atual corresponde a {N(pct_now)}% do original, ou seja, original × {N(f)} = {M(final_price)}",
             f"original = {M(final_price)} ÷ {N(f)} = {M(orig)}",
             f"conferindo: {M(orig)} × {N(f)} = {M(final_price)}"]
    if rng.random() < 0.5:
        steps.append(f"atenção: {'tirar' if sign > 0 else 'somar'} {P(p)} de {M(final_price)} daria {M(w)}, que não é o preço original")
    if sign > 0:
        question = rng.choice([
            f"Depois de um aumento de {P(p)}, um produto passou a custar {qm(rng, final_price)}. Quanto custava antes?",
            f"Com reajuste de {P(p)}, a mensalidade foi para {qm(rng, final_price)}. Qual era o valor antigo?",
            f"Já com {P(p)} de acréscimo, paguei {qm(rng, final_price)}. Qual era o preço sem o acréscimo?",
        ])
        final = rng.choice([f"O preço antes do aumento era {M(orig)}.", f"Custava {M(orig)}."])
    else:
        question = rng.choice([
            f"Paguei {qm(rng, final_price)} com {P(p)} de desconto. Qual era o preço cheio?",
            f"Na promoção de {P(p)} off, a blusa saiu por {qm(rng, final_price)}. Quanto custava sem desconto?",
            f"Com {P(p)} de desconto o boleto ficou {qm(rng, final_price)}. Qual o valor original?",
        ])
        final = rng.choice([f"O preço cheio era {M(orig)}.", f"Sem desconto, custava {M(orig)}."])
    d.result("original", orig, M(orig))
    return Out(question, steps, final, M(orig))


@kind("descontos_sucessivos", "porcentagem", 0.6)
def k_descontos_sucessivos(rng, d):
    d1, d2 = (Fraction(rng.choice([5, 10, 15, 20, 25, 30, 40, 50])) for _ in range(2))
    with_price = rng.random() < 0.5
    keep = (1 - d1 / 100) * (1 - d2 / 100)
    total = 1 - keep
    d.given(desconto1=d1, desconto2=d2)
    k1 = d.calc("sobra_1", f"1 - {lit(d1)} / 100", 1 - d1 / 100)
    k2 = d.calc("sobra_2", f"1 - {lit(d2)} / 100", 1 - d2 / 100)
    kp = d.calc("sobra_total", f"{lit(k1)} * {lit(k2)}", keep)
    tot = d.calc("desconto_total", f"(1 - {lit(kp)}) * 100", total * 100)
    naive = d.calc("soma_ingenua", f"{lit(d1)} + {lit(d2)}", d1 + d2)
    steps = [f"depois do primeiro desconto sobra {N(k1)} do preço; depois do segundo, {N(k1)} × {N(k2)} = {N(kp, 4)}",
             f"desconto total: 1 - {N(kp, 4)} = {N(1 - kp, 4)}, ou {P(tot)}"]
    question = rng.choice([
        f"Uma loja dá {P(d1)} de desconto e, no caixa, mais {P(d2)} sobre o novo valor. Qual o desconto total?",
        f"Se um produto tem desconto de {P(d1)} e depois outro de {P(d2)}, isso equivale a quanto de desconto?",
        f"Descontos de {P(d1)} e {P(d2)} em sequência dão {P(naive)} de desconto?",
    ])
    displays = [P(tot)]
    exacts = [total * 100]
    if with_price:
        for _ in range(60):
            base = price(rng, 50, 3000)
            if exact_at(base * keep, 2):
                break
        else:
            raise Reject("centavos")
        final_value = d.calc("preco_final", f"{lit(base)} * {lit(kp)}", base * keep)
        d.given(preco=base)
        question += f" O produto custa {qm(rng, base)}; quanto vou pagar?"
        steps.append(f"preço final: {M(base)} × {N(kp, 4)} = {M(final_value)}")
        displays.append(M(final_value))
        exacts.append(base * keep)
    final = f"O desconto equivalente é de {P(tot)}, e não de {P(naive)}." + (f" Você paga {displays[1]}." if with_price else "")
    d.result("desconto_equivalente", exacts, displays)
    return Out(question, steps, final, " / ".join(displays))


# --------------------------------------------------------------- finanças

def pick_item(rng):
    name, gender, lo, hi = rng.choice(ITEMS_LOJA)
    return name, gender, price(rng, lo, hi)


@kind("desconto", "financas/descontos", 1.4)
def k_desconto(rng, d):
    for _ in range(30):
        item, gender, base = pick_item(rng)
        p = Fraction(rng.choice([5, 10, 12, 15, 20, 25, 30, 35, 40, 50, 60, 70])) if rng.random() < 0.9 else rng.choice(PCT_DEC)
        disc = base * p / 100
        if exact_at(disc, 2) or rng.random() < 0.15:
            break
    final_exact = base - Fraction(quant(disc, 2))
    rounded = not exact_at(disc, 2)
    d.given(preco=base, desconto=p)
    pr, pp = qm(rng, base), P(p)
    um, o = art(gender), art(gender, True)
    question = rng.choice([
        f"{um.capitalize()} {item} custa {pr} e está com {pp} de desconto. Quanto vou pagar?",
        f"Quanto fica {pr} com {pp} de desconto?",
        f"Tenho um cupom de {pp} e a compra deu {pr}. Qual o valor final?",
        f"Pagando no Pix tem {pp} de desconto. {o.capitalize()} {item} sai por {pr} no preço normal. Quanto pago no Pix?",
        f"Na Black Friday, {o} {item} de {pr} está com {pp} off. Qual o preço com desconto?",
        f"Liquidação: tudo com {pp} de desconto. Quanto custa {um} {item} que custava {pr}?",
        f"Qual o valor de {pr} menos {pp}?",
        f"Vi {um} {item} por {pr} e o vendedor ofereceu {pp} de desconto à vista. Quanto sai?",
    ])
    if rng.random() < 0.55 or rounded:
        dv = d.calc("desconto", f"{lit(base)} * {lit(p)} / 100", disc, places=2)
        fv = d.calc("valor_final", f"{lit(base)} - {lit(quant(disc, 2))}", final_exact)
        steps = [f"desconto: {pp} de {M(base)} {eq_or_approx(dv)} {M(dv)}", f"valor final: {M(base)} - {M(dv)} = {M(fv)}"]
        if rounded:
            steps[0] += " (arredondado para centavos)"
    else:
        f = d.calc("fator", f"1 - {lit(p)} / 100", 1 - p / 100)
        fv = d.calc("valor_final", f"{lit(base)} * {lit(f)}", final_exact)
        dv = d.calc("economia", f"{lit(base)} - {lit(fv)}", base - final_exact)
        steps = [f"com {pp} de desconto você paga {N(100 - p)}% do preço: {M(base)} × {N(f, 4)} = {M(fv)}",
                 f"economia: {M(base)} - {M(fv)} = {M(dv)}"]
    display = M(fv)
    final = rng.choice([f"Você paga {display}.", f"Valor com desconto: {display}.", f"Sai por {display} (economia de {M(dv)}).",
                        f"Fica {display}."])
    d.result("valor_final", fv, display)
    return Out(question, steps, final, display)


@kind("desconto_percentual", "financas/descontos", 0.8)
def k_desconto_percentual(rng, d):
    item, gender, base = pick_item(rng)
    cut = Fraction(rng.randint(3, 60), 100)
    new = Fraction(quant(base * (1 - cut), 0)) - Fraction(rng.choice([0, 0, 10, 1]), 100)
    if new <= 0 or new >= base:
        raise Reject("sem-exemplo")
    d.given(antes=base, depois=new)
    diff = d.calc("diferenca", f"{lit(base)} - {lit(new)}", base - new)
    ratio = (base - new) / base
    pct = d.calc("desconto_percentual", f"({lit(base)} - {lit(new)}) / {lit(base)} * 100", ratio * 100, places=2)
    steps = [f"diferença: {M(base)} - {M(new)} = {M(diff)}",
             f"{M(diff)} ÷ {M(base)} × 100 {eq_or_approx(pct)} {P(pct)}"]
    o = art(gender, True)
    question = rng.choice([
        f"De {qm(rng, base)} por {qm(rng, new)}: quantos por cento de desconto é isso?",
        f"{o.capitalize()} {item} baixou de {qm(rng, base)} para {qm(rng, new)}. Qual foi o desconto em porcentagem?",
        f"Estava {qm(rng, base)} e agora está {qm(rng, new)}. Quanto % de desconto deram?",
    ])
    shown = approx(pct) + P(pct)
    final = rng.choice([f"O desconto foi de {shown}.", f"Isso dá {shown} de desconto ({M(diff)} a menos)."])
    d.result("desconto_percentual", pct, P(pct))
    return Out(question, steps, final, shown)


RATES_MONTH = [Fraction(x, 10) for x in (5, 6, 8, 10, 12, 15, 18, 20, 25, 30, 35, 40, 50)]
RATES_YEAR = [Fraction(x) for x in (4, 5, 6, 8, 9, 10, 11, 12, 13, 15)]


@kind("juros_simples", "financas/juros", 1.0)
def k_juros_simples(rng, d):
    period = rng.choice(["mes", "mes", "ano"])
    for _ in range(40):
        capital = Fraction(rng.choice([rng.randint(5, 500) * 100, rng.randint(10, 2000) * 50, rng.randint(1000, 90000)]))
        rate = rng.choice(RATES_MONTH if period == "mes" else RATES_YEAR)
        time = rng.randint(2, 36) if period == "mes" else rng.randint(1, 6)
        interest = capital * rate / 100 * time
        if exact_at(interest, 2):
            break
    else:
        raise Reject("sem-exemplo")
    d.given(capital=capital, taxa=rate, tempo=time)
    d.param(periodo=period)
    unit_s, unit_p = ("mês", "meses") if period == "mes" else ("ano", "anos")
    taxa_txt = f"{P(rate)} ao {unit_s}" if rng.random() < 0.7 else f"{P(rate)} {'a.m.' if period == 'mes' else 'a.a.'}"
    i = d.calc("taxa_decimal", f"{lit(rate)} / 100", rate / 100)
    j = d.calc("juros", f"C * i / 100 * t", interest, {"C": capital, "i": rate, "t": time})
    m = d.calc("montante", f"{lit(capital)} + {lit(j)}", capital + interest)
    tt = f"{time} {unit_s if time == 1 else unit_p}"
    c = qm(rng, capital)
    question = rng.choice([
        f"Quanto rendem {c} aplicados a juros simples de {taxa_txt} durante {tt}?",
        f"Peguei {c} emprestados a juros simples de {taxa_txt}. Quanto pago de juros em {tt}? E o total?",
        f"Qual o montante de {c} a {taxa_txt}, juros simples, em {tt}?",
        f"Juros simples: capital de {c}, taxa de {taxa_txt}, prazo de {tt}. Quanto dá de juros?",
    ])
    steps = [f"J = C × i × t = {N(capital)} × {N(i, 4)} × {time} = {M(j)}", f"montante: {M(capital)} + {M(j)} = {M(m)}"]
    final = rng.choice([f"Juros de {M(j)}; montante de {M(m)}.", f"Ao final de {tt}: {M(j)} de juros, total de {M(m)}."])
    d.result("juros_montante", [j, m], [M(j), M(m)])
    return Out(question, steps, final, f"juros {M(j)}, montante {M(m)}")


@kind("juros_compostos", "financas/juros", 1.0)
def k_juros_compostos(rng, d):
    period = rng.choice(["mes", "mes", "ano"])
    capital = Fraction(rng.choice([rng.randint(5, 300) * 100, rng.randint(10, 2000) * 50, rng.randint(1, 50) * 1000]))
    rate = rng.choice(RATES_MONTH if period == "mes" else RATES_YEAR)
    n = rng.randint(2, 36) if period == "mes" else rng.randint(2, 10)
    factor = (1 + rate / 100) ** n
    amount = capital * factor
    d.given(capital=capital, taxa=rate, periodos=n)
    d.param(periodo=period)
    vars_ = {"C": capital, "i": rate, "n": n}
    fac = d.calc("fator", "(1 + i / 100) ** n", factor, {"i": rate, "n": n}, places=6)
    mt = d.calc("montante", "C * (1 + i / 100) ** n", amount, vars_, places=2)
    jr = d.calc("juros", "C * (1 + i / 100) ** n - C", amount - capital, vars_, places=2)
    unit_s, unit_p = ("mês", "meses") if period == "mes" else ("ano", "anos")
    c = qm(rng, capital)
    question = rng.choice([
        f"Se eu aplicar {c} a juros compostos de {P(rate)} ao {unit_s}, quanto terei depois de {n} {unit_p}?",
        f"Quanto vira {c} em {n} {unit_p} a {P(rate)} ao {unit_s}, com juros compostos?",
        f"Uma dívida de {c} cresce {P(rate)} ao {unit_s} (juros compostos). Quanto ela vira em {n} {unit_p} sem pagamento nenhum?",
        f"Qual o montante de um investimento de {c} a {P(rate)} {'a.m.' if period == 'mes' else 'a.a.'}, juros compostos, por {n} {unit_p}?",
    ])
    base = N(1 + rate / 100, 4)
    steps = [f"M = C × (1 + i)^n = {N(capital)} × {base}^{n}",
             f"{base}^{n} {eq_or_approx(fac, 6)} {N(fac, 6)}",
             f"M {eq_or_approx(mt)} {M(mt)}",
             f"juros: {M(mt)} - {M(capital)} {eq_or_approx(jr)} {M(jr)}"]
    final = rng.choice([
        f"Montante de {approx(mt)}{M(mt)} (juros de {M(jr)}), com a taxa de {P(rate)} fixa nos {n} {unit_p}.",
        f"Ao final dos {n} {unit_p}: {approx(mt)}{M(mt)}, dos quais {M(jr)} são juros (sem descontar impostos ou taxas).",
    ])
    d.result("montante_juros", [amount, amount - capital], [M(mt), M(jr)])
    return Out(question, steps, final, f"{approx(mt)}{M(mt)}")


@kind("parcelamento", "financas/parcelas", 1.0)
def k_parcelamento(rng, d):
    item, gender, base = pick_item(rng)
    mode = rng.choice(["sem_juros", "comparar"])
    d.param(modo=mode)
    n = rng.choice([2, 3, 4, 5, 6, 8, 10, 12, 18, 24])
    um = art(gender)
    if mode == "sem_juros":
        parcela = Fraction(quant(base / n, 2))
        total = parcela * n
        d.given(total=total, parcelas=n)
        p = d.calc("parcela", f"{lit(total)} / {n}", parcela)
        d.calc("conferencia", f"{lit(p)} * {n}", total)
        t = qm(rng, total)
        question = rng.choice([f"Comprei {um} {item} de {t} em {n}x sem juros. Qual o valor de cada parcela?",
                               f"{t} em {n} vezes dá quanto por mês?",
                               f"Quanto fica cada prestação de {t} dividido em {n} parcelas iguais?"])
        steps = [f"{M(total)} ÷ {n} = {M(p)}", f"conferindo: {n} × {M(p)} = {M(total)}"]
        final = rng.choice([f"Cada parcela fica em {M(p)}.", f"São {n} parcelas de {M(p)}."])
        d.result("parcela", p, M(p))
        return Out(question, steps, final, f"{n}x de {M(p)}")
    juros = Fraction(rng.randint(3, 35), 100)
    parcela = Fraction(quant(base * (1 + juros) / n, 2))
    d.given(a_vista=base, parcelas=n, parcela=parcela)
    tot = d.calc("total_parcelado", f"{n} * {lit(parcela)}", n * parcela)
    extra = d.calc("diferenca", f"{n} * {lit(parcela)} - {lit(base)}", n * parcela - base)
    pct = d.calc("percentual_a_mais", f"({n} * {lit(parcela)} - {lit(base)}) / {lit(base)} * 100", (n * parcela - base) / base * 100, places=2)
    question = rng.choice([
        f"{um.capitalize()} {item} custa {qm(rng, base)} à vista ou {n}x de {qm(rng, parcela)}. Quanto pago a mais parcelando?",
        f"Na loja: {qm(rng, base)} à vista ou {n} parcelas de {qm(rng, parcela)}. Qual a diferença e quantos por cento a mais?",
        f"Vale a conta: {n} prestações de {qm(rng, parcela)} contra {qm(rng, base)} à vista. Quanto o parcelado custa a mais?",
    ])
    steps = [f"parcelado: {n} × {M(parcela)} = {M(tot)}", f"diferença: {M(tot)} - {M(base)} = {M(extra)}",
             f"em relação ao preço à vista: {M(extra)} ÷ {M(base)} × 100 {eq_or_approx(pct)} {P(pct)}"]
    final = f"Parcelando você paga {M(extra)} a mais, {approx(pct)}{P(pct)} acima do preço à vista."
    d.result("diferenca_percentual", [n * parcela - base, (n * parcela - base) / base * 100], [M(extra), P(pct)])
    return Out(question, steps, final, f"{M(extra)} a mais")


# --------------------------------------------------------------- médias

def fmt_list(values, fmt):
    shown = [fmt(v) for v in values]
    sep = "; " if any("," in s for s in shown) else ", "
    return listing(shown, sep)


@kind("media_simples", "medias", 1.0)
def k_media_simples(rng, d):
    contexts = [
        ("notas", lambda: rand_dec(rng, 3, 10, 1), (3, 5), lambda s: N(s),
         ["Tirei {l} nas provas. Qual a minha média?", "Minhas notas no bimestre foram {l}. Qual é a média?"],
         "Sua média é {m}."),
        ("temperaturas", lambda: rand_dec(rng, 12, 38, 1), (4, 7), lambda s: N(s) + " °C",
         ["As temperaturas máximas da semana foram {l}. Qual foi a média?", "Anotei estas temperaturas: {l}. Qual a temperatura média?"],
         "A temperatura média foi de {m} °C."),
        ("gastos", lambda: price(rng, 300, 1500), (3, 6), lambda s: M(s),
         ["Gastei com mercado nos últimos meses: {l}. Qual foi o gasto médio?", "Minhas contas de mercado foram {l}. Qual a média mensal?"],
         "O gasto médio foi de R$ {m}."),
        ("idades", lambda: Fraction(rng.randint(1, 90)), (3, 8), lambda s: N(s),
         ["As idades dos meus primos são {l} anos. Qual a idade média?", "Num grupo, as idades são {l}. Qual a média de idade?"],
         "A idade média é {m} anos."),
        ("vendas", lambda: Fraction(rng.randint(0, 400)), (4, 7), lambda s: N(s),
         ["Vendi {l} bolos nos últimos dias. Quantos vendi em média por dia?", "As vendas diárias foram {l} unidades. Qual a média?"],
         "A média é de {m} unidades por dia."),
        ("plain", lambda: rand_dec(rng, 0, 500, rng.choice([0, 0, 1])), (3, 8), lambda s: N(s),
         ["Qual a média de {l}?", "Calcule a média aritmética de {l}.", "Me diz a média entre {l}."], "A média é {m}."),
    ]
    name, gen, (lo, hi), fmt, templates, final_tpl = rng.choice(contexts)
    values = [gen() for _ in range(rng.randint(lo, hi))]
    count = len(values)
    total = sum(values, Fraction(0))
    mean = total / count
    d.given(valores=values)
    d.param(contexto=name)
    s = d.calc("soma", " + ".join(lit(v) for v in values), total)
    m = d.calc("media", f"({' + '.join(lit(v) for v in values)}) / {count}", mean, places=2)
    money = name == "gastos"
    fs = M if money else (lambda x: N(x, 2))
    steps = [f"soma: {' + '.join(fs(v) for v in values)} = {fs(s)}", f"média: {fs(s)} ÷ {count} {eq_or_approx(m)} {fs(m)}"]
    question = rng.choice(templates).format(l=fmt_list(values, fmt))
    display = N(m, 2)
    if money:
        display = br(m, 2, fixed=True)
    final = final_tpl.format(m=approx(m) + display if not money else display)
    if money and not exact_at(m, 2):
        final = final.replace("R$ ", "aproximadamente R$ ", 1)
    d.result("media", mean, display)
    return Out(question, steps, final, approx(m) + display)


@kind("media_ponderada", "medias", 0.9)
def k_media_ponderada(rng, d):
    if rng.random() < 0.65:
        parts = rng.sample(["prova", "trabalho", "seminário", "lista de exercícios", "projeto", "prova final", "participação"], rng.randint(2, 4))
        grades = [rand_dec(rng, 3, 10, 1) for _ in parts]
        weights = [Fraction(rng.randint(1, 5)) for _ in parts]
        d.param(contexto="notas")
        desc = listing([f"{p} (peso {N(w)}): {N(g)}" for p, w, g in zip(parts, weights, grades)], "; ")
        question = rng.choice([f"Minhas notas foram: {desc}. Qual a média ponderada?",
                               f"Calcule minha média ponderada: {desc}.", f"Como fica a média com pesos? {desc}."])
        fmt, final_tpl = (lambda x: N(x, 2)), "Sua média ponderada é {m}."
    else:
        lots = rng.randint(2, 3)
        grades = [rand_dec(rng, 5, 80, 2) for _ in range(lots)]
        weights = [Fraction(rng.choice([10, 20, 25, 50, 100, 150, 200, 300, 500])) for _ in range(lots)]
        d.param(contexto="acoes")
        desc = listing([f"{N(w)} ações a {qm(rng, g)}" for w, g in zip(weights, grades)], ", ")
        question = rng.choice([f"Comprei {desc}. Qual o meu preço médio por ação?",
                               f"Qual o preço médio de compra se eu comprei {desc}?"])
        fmt, final_tpl = M, "Seu preço médio é de {m} por ação."
    d.given(valores=grades, pesos=weights)
    products = []
    steps = []
    for g, w in zip(grades, weights):
        p = d.calc("produto", f"{lit(g)} * {lit(w)}", g * w)
        products.append(p)
        steps.append(f"{fmt(g)} × {N(w)} = {fmt(p)}")
    sp = d.calc("soma_produtos", " + ".join(lit(p) for p in products), sum(products, Fraction(0)))
    sw = d.calc("soma_pesos", " + ".join(lit(w) for w in weights), sum(weights, Fraction(0)))
    mean = sp / sw
    m = d.calc("media_ponderada", f"({' + '.join(lit(p) for p in products)}) / ({' + '.join(lit(w) for w in weights)})", mean, places=2)
    steps += [f"soma dos produtos: {fmt(sp)}; soma dos pesos: {N(sw)}", f"média: {fmt(sp)} ÷ {N(sw)} {eq_or_approx(m)} {fmt(m)}"]
    display = fmt(m)
    final = final_tpl.format(m=approx(m) + display)
    d.result("media_ponderada", mean, display)
    return Out(question, steps, final, approx(m) + display)


@kind("nota_necessaria", "medias", 0.7)
def k_nota_necessaria(rng, d):
    for _ in range(40):
        k = rng.randint(1, 3)
        grades = [rand_dec(rng, 3, 10, 1) for _ in range(k)]
        target = Fraction(rng.choice([5, 6, 7, 7, 8]))
        need = target * (k + 1) - sum(grades, Fraction(0))
        if 0 <= need <= 10:
            break
    else:
        raise Reject("sem-exemplo")
    total = k + 1
    d.given(notas=grades, media_alvo=target, provas=total)
    pts = d.calc("pontos_necessarios", f"{lit(target)} * {total}", target * total)
    have = d.calc("pontos_atuais", " + ".join(lit(g) for g in grades), sum(grades, Fraction(0)))
    n = d.calc("nota_necessaria", f"{lit(pts)} - ({' + '.join(lit(g) for g in grades)})", need)
    d.calc("conferencia", f"({' + '.join(lit(g) for g in grades)} + {lit(n)}) / {total}", target)
    notas = fmt_list(grades, lambda x: N(x))
    question = rng.choice([
        f"Tirei {notas} {'na primeira prova' if k == 1 else 'nas primeiras provas'}. Para fechar média {N(target)} nas {total} provas, quanto preciso tirar na última?",
        f"São {total} provas e quero média {N(target)}. Já tenho {notas}. Qual nota preciso na próxima?",
        f"Minhas notas até agora: {notas}. Quanto preciso tirar na {total}ª prova para ficar com média {N(target)}?",
    ])
    steps = [f"para média {N(target)} em {total} provas, a soma precisa ser {N(target)} × {total} = {N(pts)}",
             f"você já tem {' + '.join(N(g) for g in grades)} = {N(have)}" if k > 1 else f"você já tem {N(have)}",
             f"falta: {N(pts)} - {N(have)} = {N(n)}"]
    final = rng.choice([f"Você precisa tirar {N(n)} na última prova.", f"Precisa de pelo menos {N(n)}."])
    d.result("nota", n, N(n))
    return Out(question, steps, final, N(n))


# --------------------------------------------------------------- unidades

@kind("comprimento", "unidades/comprimento", 1.0)
def k_comprimento(rng, d):
    conversions = [
        ("km", "m", 1000, "quilômetros", "metros", (0.1, 300, 3)), ("m", "km", Fraction(1, 1000), "metros", "quilômetros", (50, 90000, 0)),
        ("m", "cm", 100, "metros", "centímetros", (0.05, 50, 2)), ("cm", "m", Fraction(1, 100), "centímetros", "metros", (1, 5000, 0)),
        ("cm", "mm", 10, "centímetros", "milímetros", (0.1, 300, 1)), ("mm", "cm", Fraction(1, 10), "milímetros", "centímetros", (1, 2000, 0)),
        ("km", "cm", 100000, "quilômetros", "centímetros", (0.01, 5, 2)),
    ]
    src, dst, factor, src_w, dst_w, (lo, hi, pl) = rng.choice(conversions)
    value = rand_dec(rng, lo, hi, pl) if pl else Fraction(rng.randint(int(lo), int(hi)))
    if value == 0:
        raise Reject("zero")
    factor = Fraction(factor)
    result = value * factor
    d.given(valor=value)
    d.const(fator=factor)
    d.param(de=src, para=dst)
    op = "×" if factor > 1 else "÷"
    k = factor if factor > 1 else 1 / factor
    res = d.calc("conversao", f"{lit(value)} {'*' if factor > 1 else '/'} {lit(k)}", result)
    v = qn(rng, value)
    question = rng.choice([
        f"Quantos {dst_w} tem {v} {src}?", f"Converta {v} {src} para {dst}.", f"{v} {src} são quantos {dst_w}?",
        f"Quanto é {v} {src_w} em {dst_w}?",
    ] + ([f"Minha corrida de hoje foi de {v} km. Quantos metros eu corri?"] if (src, dst) == ("km", "m") else [])
      + ([f"A sala tem {v} cm de largura. Quanto dá em metros?"] if (src, dst) == ("cm", "m") else [])
      + ([f"Andei {v} metros até o trabalho. Quantos km são?"] if (src, dst) == ("m", "km") else []))
    steps = [f"como 1 {src if factor > 1 else dst} = {N(k)} {dst if factor > 1 else src}: {N(value, 3)} {op} {N(k)} = {N(res, 5)}"]
    display = f"{N(res, 5)} {dst}"
    final = rng.choice([f"{N(value, 3)} {src} = {display}.", f"São {display}.", f"Dá {display}."])
    d.result("convertido", res, display)
    return Out(question, steps, final, display)


@kind("tempo", "unidades/tempo", 1.2)
def k_tempo(rng, d):
    mode = rng.choice(["min_para_h", "h_dec_para_min", "hmin_para_min", "hmin_para_s", "somar_duracoes", "horario_chegada", "dias_horas"])
    d.param(modo=mode)
    if mode == "min_para_h":
        total = rng.randint(61, 1500)
        h, m = divmod(total, 60)
        d.given(minutos=total)
        d.const(minutos_por_hora=60)
        hh = d.calc("horas", f"{total} // 60", h)
        mm = d.calc("minutos", f"{total} % 60", m)
        dec = d.calc("horas_decimais", f"{total} / 60", Fraction(total, 60), places=2)
        question = rng.choice([f"Quanto é {total} minutos em horas?", f"{total} minutos dão quantas horas?",
                               f"Converta {total} min para horas e minutos.", f"Um filme de {total} minutos tem quantas horas?"])
        steps = [f"{total} ÷ 60 = {hh}, com resto {mm}", f"em horas decimais: {total} ÷ 60 {eq_or_approx(dec)} {N(dec)} h"]
        display = duration(total)
        d.result("duracao_min", total, display)
        return Out(question, steps, f"{total} minutos = {display}.", display)
    if mode == "h_dec_para_min":
        hours = rand_dec(rng, 0.1, 12, rng.choice([1, 2]))
        minutes = hours * 60
        if minutes.denominator != 1:
            raise Reject("fracao-minuto")
        d.given(horas=hours)
        d.const(minutos_por_hora=60)
        mins = d.calc("minutos", f"{lit(hours)} * 60", minutes)
        question = rng.choice([f"{N(hours)} horas são quantos minutos?", f"Quanto é {N(hours)} h em minutos?",
                               f"Trabalhei {N(hours)} horas hoje. Quantos minutos foram?"])
        steps = [f"{N(hours)} × 60 = {N(mins)} minutos"]
        if mins >= 60:
            d.calc("h", f"{int(mins)} // 60", int(mins) // 60)
            d.calc("m", f"{int(mins)} % 60", int(mins) % 60)
            steps.append(f"{N(mins)} min = {duration(int(mins))}")
        display = f"{N(mins)} minutos"
        d.result("minutos", mins, display)
        return Out(question, steps, f"{N(hours)} h = {display}" + (f" ({duration(int(mins))})." if mins >= 60 else "."), display)
    if mode in ("hmin_para_min", "hmin_para_s"):
        h, m = rng.randint(1, 23), rng.randint(1, 59)
        d.given(horas=h, minutos=m)
        total = d.calc("total_minutos", f"{h} * 60 + {m}", h * 60 + m)
        hm = f"{h} hora{'s' if h > 1 else ''} e {m} minuto{'s' if m > 1 else ''}"
        if mode == "hmin_para_min":
            d.const(minutos_por_hora=60)
            question = rng.choice([f"{hm} dão quantos minutos?", f"Quantos minutos tem {h}h{m:02d}?", f"Converta {hm} em minutos."])
            steps = [f"{h} × 60 + {m} = {N(total)} minutos"]
            display = f"{N(total)} minutos"
            d.result("minutos", total, display)
            return Out(question, steps, f"São {display}.", display)
        d.const(minutos_por_hora=60, segundos_por_minuto=60)
        secs = d.calc("segundos", f"({h} * 60 + {m}) * 60", (h * 60 + m) * 60)
        question = rng.choice([f"Quantos segundos tem {hm}?", f"{hm} são quantos segundos?"])
        steps = [f"em minutos: {h} × 60 + {m} = {N(total)}", f"em segundos: {N(total)} × 60 = {N(secs)}"]
        display = f"{N(secs)} segundos"
        d.result("segundos", secs, display)
        return Out(question, steps, f"São {display}.", display)
    if mode == "somar_duracoes":
        parts = [(rng.randint(0, 5), rng.randint(5, 59)) for _ in range(rng.randint(2, 3))]
        d.given(duracoes=[x for p in parts for x in p])
        minutes = [h * 60 + m for h, m in parts]
        total = d.calc("total_minutos", " + ".join(f"({h} * 60 + {m})" for h, m in parts), sum(minutes))
        hh = d.calc("horas", f"{int(total)} // 60", int(total) // 60)
        mm = d.calc("minutos", f"{int(total)} % 60", int(total) % 60)
        shown = [f"{h}h{m:02d}" if h else f"{m} min" for h, m in parts]
        sh = sum(h for h, _ in parts)
        sm = sum(m for _, m in parts)
        d.calc("soma_horas", " + ".join(str(h) for h, _ in parts), sh)
        d.calc("soma_minutos", " + ".join(str(m) for _, m in parts), sm)
        question = rng.choice([f"Gastei {listing(shown)} em três tarefas. Quanto tempo foi no total?" if len(parts) == 3 else
                               f"Gastei {listing(shown)} em duas tarefas. Quanto tempo deu no total?",
                               f"Quanto dá {' + '.join(shown)}?", f"Some os tempos: {listing(shown)}."])
        steps = [f"horas: {' + '.join(str(h) for h, _ in parts)} = {sh} h", f"minutos: {' + '.join(str(m) for _, m in parts)} = {sm} min"]
        if sm >= 60:
            steps.append(f"{sm} min = {duration(sm)}, então o total é {duration(int(total))}")
        display = duration(int(total))
        d.result("duracao_min", total, display)
        return Out(question, steps, f"Total: {display}.", display)
    if mode == "horario_chegada":
        start = rng.randint(0, 23) * 60 + rng.choice([0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55, rng.randint(0, 59)])
        dur = rng.randint(10, 14 * 60)
        end = start + dur
        d.given(saida=[start // 60, start % 60], duracao=[dur // 60, dur % 60] if dur >= 60 else [dur])
        e = d.calc("chegada_minutos", f"({start // 60} * 60 + {start % 60} + {dur}) % 1440", end % 1440)
        nxt = d.test("dia_seguinte", f"{start} + {dur} >= 1440", end >= 1440)
        dur_txt = duration(dur)
        template = rng.choice([
            "Saio às {s} e a viagem dura {d}. Que horas chego?",
            "O filme começa às {s} e dura {d}. A que horas termina?",
            "Coloquei o pão no forno às {s}; ele precisa de {d}. Que horas tiro?",
            "Começo um plantão às {s} e ele dura {d}. Que horas sai o plantão?",
        ])
        question = template.format(s=clock(start), d=dur_txt)
        steps = [f"{clock(start)} + {dur_txt} = {clock(int(e))}" + (" do dia seguinte" if nxt else "")]
        if dur >= 60 and dur % 60:
            mid = start + (dur // 60) * 60
            d.calc("passo_horas", f"({start} + {(dur // 60) * 60}) % 1440", mid % 1440)
            steps = [f"{clock(start)} + {duration((dur // 60) * 60)} = {clock(mid)}",
                     f"{clock(mid)} + {dur % 60} min = {clock(int(e))}" + (" do dia seguinte" if nxt else "")]
        display = clock(int(e))
        final = f"{'Chega' if 'viagem' in template else 'Fica pronto' if 'forno' in template else 'Termina'} às {display}" + (" do dia seguinte." if nxt else ".")
        if "plantão" in template:
            final = f"O plantão termina às {display}" + (" do dia seguinte." if nxt else ".")
        d.result("horario_min", e, display)
        return Out(question, steps, final, display)
    # dias_horas
    if rng.random() < 0.5:
        days = rng.randint(2, 60)
        d.given(dias=days)
        d.const(horas_por_dia=24)
        h = d.calc("horas", f"{days} * 24", days * 24)
        question = rng.choice([f"Quantas horas tem {days} dias?", f"{days} dias equivalem a quantas horas?"])
        display = f"{N(h)} horas"
        d.result("horas", h, display)
        return Out(question, [f"{days} × 24 = {N(h)}"], f"{days} dias = {display}.", display)
    hours = rng.randint(25, 2000)
    d.given(horas=hours)
    d.const(horas_por_dia=24)
    dd = d.calc("dias", f"{hours} // 24", hours // 24)
    hh = d.calc("horas_restantes", f"{hours} % 24", hours % 24)
    question = rng.choice([f"{N(hours)} horas são quantos dias?", f"Quantos dias completos cabem em {N(hours)} horas?"])
    display = f"{N(dd)} dias" + (f" e {hh} horas" if hh else "")
    d.result("dias_horas", [dd, hh], [display])
    return Out(question, [f"{N(hours)} ÷ 24 = {N(dd)}, com resto {hh}"], f"{N(hours)} horas = {display}.", display)


@kind("velocidade", "unidades/velocidade", 0.6)
def k_velocidade(rng, d):
    to_ms = rng.random() < 0.6
    d.param(sentido="kmh_para_ms" if to_ms else "ms_para_kmh")
    d.const(fator=Fraction(18, 5))
    if to_ms:
        v = Fraction(rng.choice([18, 36, 54, 72, 90, 108, 126, 144, rng.randint(5, 300)]))
        res = d.calc("ms", f"{lit(v)} / 3.6", v / Fraction(18, 5), places=2)
        d.given(kmh=v)
        question = rng.choice([f"Quanto é {N(v)} km/h em m/s?", f"Um carro a {N(v)} km/h está a quantos metros por segundo?",
                               f"Converta {N(v)} km/h para metros por segundo."])
        steps = [f"de km/h para m/s divide-se por 3,6 (1.000 m ÷ 3.600 s): {N(v)} ÷ 3,6 {eq_or_approx(res)} {N(res)}"]
        display = f"{N(res)} m/s"
        d.result("ms", res, display)
        return Out(question, steps, f"{N(v)} km/h {eq_or_approx(res)} {display}.", approx(res) + display)
    v = rand_dec(rng, 1, 60, rng.choice([0, 1]))
    res = d.calc("kmh", f"{lit(v)} * 3.6", v * Fraction(18, 5))
    d.given(ms=v)
    question = rng.choice([f"Um atleta corre a {N(v)} m/s. Quanto é isso em km/h?", f"{N(v)} m/s dá quantos km/h?",
                           f"Converta {N(v)} m/s em km/h."])
    steps = [f"de m/s para km/h multiplica-se por 3,6: {N(v)} × 3,6 = {N(res)}"]
    display = f"{N(res)} km/h"
    d.result("kmh", res, display)
    return Out(question, steps, f"{N(v)} m/s = {display}.", display)


@kind("massa_volume", "unidades/massa-volume", 0.9)
def k_massa_volume(rng, d):
    mode = rng.choice(["kg_g", "g_kg", "l_ml", "ml_l", "t_kg", "copos"])
    d.param(modo=mode)
    if mode == "copos":
        cup = rng.choice([150, 180, 200, 250, 300, 350])
        liters = rand_dec(rng, 0.5, 5, rng.choice([0, 1]))
        ml = liters * 1000
        d.given(copo_ml=cup, litros=liters)
        d.const(ml_por_litro=1000)
        total = d.calc("ml", f"{lit(liters)} * 1000", ml)
        full = d.calc("copos_cheios", f"{int(ml)} // {cup}", int(ml) // cup)
        rest = d.calc("sobra", f"{int(ml)} % {cup}", int(ml) % cup)
        question = rng.choice([f"Quantos copos de {cup} mL enchem uma garrafa de {N(liters)} L?",
                               f"Tenho {N(liters)} litros de suco. Quantos copos de {cup} mL consigo servir?"])
        steps = [f"{N(liters)} L = {N(liters)} × 1.000 = {N(total)} mL", f"{N(total)} ÷ {cup} = {N(full)}, sobrando {N(rest)} mL"]
        display = f"{N(full)} copos"
        final = f"Dá para encher {display} cheios" + (f" e sobram {N(rest)} mL." if rest else ".")
        d.result("copos", full, display)
        return Out(question, steps, final, display)
    table = {
        "kg_g": ("kg", "g", 1000, "quilos", "gramas", (0.05, 50, 3)),
        "g_kg": ("g", "kg", Fraction(1, 1000), "gramas", "quilos", (10, 90000, 0)),
        "l_ml": ("L", "mL", 1000, "litros", "mililitros", (0.05, 20, 3)),
        "ml_l": ("mL", "L", Fraction(1, 1000), "mililitros", "litros", (5, 20000, 0)),
        "t_kg": ("t", "kg", 1000, "toneladas", "quilos", (0.1, 40, 2)),
    }
    src, dst, factor, src_w, dst_w, (lo, hi, pl) = table[mode]
    value = rand_dec(rng, lo, hi, pl) if pl else Fraction(rng.randint(int(lo), int(hi)))
    factor = Fraction(factor)
    d.given(valor=value)
    d.const(fator=factor)
    k = factor if factor > 1 else 1 / factor
    res = d.calc("conversao", f"{lit(value)} {'*' if factor > 1 else '/'} {lit(k)}", value * factor)
    v = qn(rng, value)
    question = rng.choice([f"Quantos {dst_w} há em {v} {src}?", f"Converta {v} {src} para {dst}.", f"{v} {src_w} equivalem a quantos {dst_w}?",
                           f"Uma receita pede {v} {src}. Quanto é isso em {dst_w}?"])
    steps = [f"1 {src if factor > 1 else dst} = {N(k)} {dst if factor > 1 else src}, então {N(value, 3)} {'×' if factor > 1 else '÷'} {N(k)} = {N(res, 4)}"]
    display = f"{N(res, 4)} {dst}"
    d.result("convertido", res, display)
    return Out(question, steps, rng.choice([f"{N(value, 3)} {src} = {display}.", f"São {display}."]), display)


CURRENCIES = [("dólar", "US$", (4.6, 6.3)), ("euro", "€", (5.1, 6.9)), ("libra", "£", (6.0, 8.0))]


@kind("cambio", "unidades/dinheiro", 0.8)
def k_cambio(rng, d):
    name, symbol, (lo, hi) = rng.choice(CURRENCIES)
    rate = rand_dec(rng, lo, hi, 2)
    mode = rng.choice(["para_reais", "para_reais", "para_estrangeira", "com_iof"])
    d.param(modo=mode, moeda=name)
    art_ = "o" if name != "libra" else "a"
    if mode == "para_estrangeira":
        budget = Fraction(rng.randint(2, 200) * 50)
        d.given(cotacao=rate, reais=budget)
        foreign = budget / rate
        v = d.calc("convertido", f"{lit(budget)} / {lit(rate)}", foreign, places=2)
        question = rng.choice([f"Tenho {qm(rng, budget)}. Com {art_} {name} a {qm(rng, rate)}, quanto de {name} consigo comprar?",
                               f"Quantos {'dólares' if name == 'dólar' else 'euros' if name == 'euro' else 'libras'} compro com {qm(rng, budget)} se a cotação está em {qm(rng, rate)}?"])
        shown = f"{symbol} {br(v, 2, fixed=True)}"
        steps = [f"{M(budget)} ÷ {M(rate)} {eq_or_approx(v)} {shown}"]
        final = f"Com a cotação de {M(rate)} que você informou, dá {approx(v)}{shown}, sem contar taxas da casa de câmbio."
        d.result("estrangeira", foreign, shown)
        return Out(question, steps, final, approx(v) + shown)
    amount = Fraction(rng.choice([rng.randint(5, 3000), rng.randint(1, 60) * 50]))
    d.given(cotacao=rate, valor=amount)
    brl = d.calc("em_reais", f"{lit(amount)} * {lit(rate)}", amount * rate)
    a_txt = f"{symbol} {br(amount, 0) if rng.random() < 0.5 else br(amount, 2, fixed=True)}"
    if mode == "para_reais":
        question = rng.choice([f"{art_.capitalize()} {name} está a {qm(rng, rate)}. Quanto custam {a_txt} em reais?",
                               f"Quero comprar {a_txt} e a cotação está em {qm(rng, rate)}. Quanto vou gastar em reais?",
                               f"Quanto dá {a_txt} em reais com {art_} {name} a {qm(rng, rate)}?"])
        steps = [f"{a_txt} × {M(rate)} = {M(brl)}"]
        final = rng.choice([f"Com a cotação de {M(rate)} que você passou, dá {M(brl)}.",
                            f"{a_txt} = {M(brl)} (cotação de {M(rate)}, sem taxas)."])
        d.result("reais", brl, M(brl))
        return Out(question, steps, final, M(brl))
    iof = rng.choice([Fraction(438, 100), Fraction(35, 10), Fraction(11, 10), Fraction(338, 100)])
    d.given(iof=iof)
    tax = d.calc("iof", f"{lit(amount)} * {lit(rate)} * {lit(iof)} / 100", amount * rate * iof / 100, places=2)
    tot = d.calc("total", f"{lit(amount)} * {lit(rate)} * (1 + {lit(iof)} / 100)", amount * rate * (1 + iof / 100), places=2)
    question = (f"Vou pagar {a_txt} no cartão. {art_.capitalize()} {name} está a {qm(rng, rate)} e o IOF é de {P(iof)}. "
                "Quanto vai dar em reais?")
    steps = [f"conversão: {a_txt} × {M(rate)} = {M(brl)}", f"IOF: {P(iof)} de {M(brl)} {eq_or_approx(tax)} {M(tax)}",
             f"total: {M(brl)} + {M(tax)} {eq_or_approx(tot)} {M(tot)}"]
    final = f"No total, {approx(tot)}{M(tot)}, usando a cotação e o IOF que você informou."
    d.result("total_reais", tot, M(tot))
    return Out(question, steps, final, approx(tot) + M(tot))


@kind("notas_moedas", "unidades/dinheiro", 0.6)
def k_notas_moedas(rng, d):
    mode = rng.choice(["moedas_para_valor", "centavos", "contar_moedas", "notas_troco"])
    d.param(modo=mode)
    if mode == "moedas_para_valor":
        coin = rng.choice([Fraction(1, 4), Fraction(1, 2), Fraction(1, 10), Fraction(1, 20), Fraction(1)])
        count = rng.randint(4, 400)
        total = coin * count
        d.given(moeda=coin, valor=total)
        n = d.calc("moedas", f"{lit(total)} / {lit(coin)}", count)
        question = rng.choice([f"Quantas moedas de {M(coin)} formam {qm(rng, total)}?", f"Preciso juntar {qm(rng, total)} só com moedas de {M(coin)}. Quantas são?"])
        steps = [f"{M(total)} ÷ {M(coin)} = {N(n)}"]
        display = f"{N(n)} moedas"
        d.result("moedas", n, display)
        return Out(question, steps, f"São {display}.", display)
    if mode == "centavos":
        value = price(rng, 0, 900)
        d.given(valor=value)
        d.const(centavos_por_real=100)
        c = d.calc("centavos", f"{lit(value)} * 100", value * 100)
        question = rng.choice([f"Quantos centavos há em {qm(rng, value)}?", f"{qm(rng, value)} são quantos centavos?"])
        display = f"{N(c)} centavos"
        d.result("centavos", c, display)
        return Out(question, [f"{M(value)} × 100 = {N(c)}"], f"São {display}.", display)
    if mode == "contar_moedas":
        coins = rng.sample([Fraction(1, 20), Fraction(1, 10), Fraction(1, 4), Fraction(1, 2), Fraction(1)], rng.randint(2, 4))
        counts = [rng.randint(1, 40) for _ in coins]
        d.given(moedas=coins, quantidades=counts)
        parts = []
        steps = []
        for c, k in zip(coins, counts):
            v = d.calc("parcial", f"{k} * {lit(c)}", k * c)
            parts.append(v)
            steps.append(f"{k} × {M(c)} = {M(v)}")
        total = d.calc("total", " + ".join(f"{k} * {lit(c)}" for c, k in zip(coins, counts)), sum(parts, Fraction(0)))
        desc = listing([f"{k} moeda{'s' if k > 1 else ''} de {M(c)}" for c, k in zip(coins, counts)])
        question = rng.choice([f"Tenho {desc} no cofrinho. Quanto tenho?", f"Quanto dá {desc}?"])
        steps.append(f"total: {M(total)}")
        d.result("total", total, M(total))
        return Out(question, steps, f"Você tem {M(total)}.", M(total))
    note = rng.choice([2, 5, 10, 20, 50, 100, 200])
    bill = price(rng, note + 1, note * 15)
    k = math.ceil(bill / note)
    d.given(nota=note, conta=bill)
    notes = d.calc("notas", f"-(-{lit(bill)} // {note})", k)
    change = d.calc("troco", f"{note} * {k} - {lit(bill)}", note * k - bill)
    question = rng.choice([f"Só tenho notas de R$ {note}. Quantas preciso para pagar {qm(rng, bill)}? E quanto volta de troco?",
                           f"Para pagar uma conta de {qm(rng, bill)} com notas de R$ {note}, quantas notas uso e qual o troco?"])
    steps = [f"{M(bill)} ÷ R$ {note} dá {N(bill / note, 2) if exact_at(bill / note, 2) else '≈ ' + N(bill / note, 2)}, então são necessárias {N(notes)} notas",
             f"troco: {N(notes)} × R$ {note} - {M(bill)} = {M(change)}"]
    d.calc("divisao_conta", f"{lit(bill)} / {note}", bill / note, places=2)
    d.result("notas_troco", [k, note * k - bill], [f"{N(notes)} notas", M(change)])
    return Out(question, steps, f"Use {N(notes)} notas de R$ {note}; o troco é de {M(change)}.", f"{N(notes)} notas, troco de {M(change)}")


# --------------------------------------------------------------- proporção

DIRECT = [
    ("{a} kg de {x} custam {B}. Quanto custam {c} kg?", "money", ["carne", "queijo", "café em grão", "castanha", "camarão", "frango", "tomate"],
     (1, 5, 1), (8, 120), (1, 9, 1), "{c} kg custam {v}."),
    ("{a} metros de tecido custam {B}. Quanto custam {c} metros?", "money", None, (1, 8, 1), (12, 300), (1, 20, 1), "{c} metros custam {v}."),
    ("Uma máquina produz {b} peças em {a} horas. Quantas peças produz em {c} horas, no mesmo ritmo?", "count", None,
     (1, 8, 0), (20, 2000), (1, 24, 0), "Produz {v} peças."),
    ("Com {a} litros de tinta pinto {b} m². Quantos m² consigo pintar com {c} litros?", "m2", None,
     (1, 18, 0), (10, 200), (1, 36, 0), "Dá para pintar {v} m²."),
    ("Para {a} pessoas uso {b} g de macarrão. Quanto uso para {c} pessoas?", "g", None,
     (2, 8, 0), (150, 1000), (1, 30, 0), "Use {v} g de macarrão."),
    ("Meu carro gasta {b} litros para rodar {a} km. Quantos litros gasta em {c} km?", "l", None,
     (50, 600, 0), (4, 50), (10, 2000, 0), "Gasta {v} litros."),
    ("Digito {b} palavras em {a} minutos. Quantas palavras digito em {c} minutos?", "count", None,
     (2, 10, 0), (60, 600), (1, 90, 0), "Você digita {v} palavras."),
    ("Uma torneira pingando desperdiça {b} litros em {a} dias. Quanto desperdiça em {c} dias?", "l", None,
     (1, 10, 0), (5, 200), (1, 365, 0), "Desperdiça {v} litros."),
]


def bounded(rng, lo, hi, places):
    return rand_dec(rng, lo, hi, places) if places else Fraction(rng.randint(lo, hi))


@kind("regra_tres_direta", "proporcao", 1.3)
def k_regra_tres_direta(rng, d):
    template, unit, things, (alo, ahi, apl), (blo, bhi), (clo, chi, cpl), final_tpl = rng.choice(DIRECT)
    for _ in range(40):
        a = bounded(rng, alo, ahi, apl)
        c = bounded(rng, clo, chi, cpl)
        b = price(rng, blo, bhi) if unit == "money" else Fraction(rng.randint(blo, bhi))
        if a == c or a == 0:
            continue
        x = b * c / a
        if unit == "money" and not exact_at(x, 2):
            continue
        if unit == "count" and x.denominator != 1:
            continue
        if unit in ("g", "l", "m2") and not exact_at(x, 2):
            continue
        break
    else:
        raise Reject("sem-exemplo")
    d.given(a=a, b=b, c=c)
    fmt = M if unit == "money" else (lambda v: N(v, 2))
    res = d.calc("x", f"{lit(b)} * {lit(c)} / {lit(a)}", x)
    steps = [f"montando a regra de três: {N(a)} → {fmt(b)}; {N(c)} → x", f"x = {fmt(b)} × {N(c)} ÷ {N(a)} = {fmt(res)}"]
    unit_rate = b / a
    if terminating(unit_rate) and exact_at(unit_rate, 4) and rng.random() < 0.4:
        u = d.calc("unitario", f"{lit(b)} / {lit(a)}", unit_rate)
        d.calc("x_por_unitario", f"{lit(u)} * {lit(c)}", x)
        steps = [f"por unidade: {fmt(b) if unit == 'money' else N(b)} ÷ {N(a)} = {M(u) if unit == 'money' else N(u, 4)}",
                 f"para {N(c)}: {M(u) if unit == 'money' else N(u, 4)} × {N(c)} = {fmt(res)}"]
    question = template.format(a=qn(rng, a), b=qn(rng, b), B=qm(rng, b), c=qn(rng, c), x=rng.choice(things) if things else "")
    display = fmt(res)
    d.result("x", res, display)
    return Out(question, steps, final_tpl.format(c=N(c), v=display), display)


INVERSE = [
    ("{a} pedreiros fazem uma obra em {b} dias. Em quantos dias {c} pedreiros fariam a mesma obra, no mesmo ritmo?",
     "pedreiros", "dias", "A obra levaria {v} dias."),
    ("Um saco de ração dura {b} dias para {a} cachorros. Para {c} cachorros, dura quantos dias?",
     "cachorros", "dias", "A ração dura {v} dias."),
    ("{a} torneiras iguais enchem um tanque em {b} minutos. Quanto tempo levam {c} torneiras?",
     "torneiras", "minutos", "Levam {v} minutos."),
    ("{a} impressoras imprimem um lote em {b} horas. Em quantas horas {c} impressoras iguais imprimem o mesmo lote?",
     "impressoras", "horas", "Levam {v} horas."),
    ("A {a} km/h uma viagem leva {b} horas. Quanto tempo leva a {c} km/h?", "km/h", "horas", "Leva {v} horas."),
    ("Com {a} pessoas trabalhando, a colheita termina em {b} dias. Com {c} pessoas, termina em quantos dias?",
     "pessoas", "dias", "Termina em {v} dias."),
]


@kind("regra_tres_inversa", "proporcao", 1.0)
def k_regra_tres_inversa(rng, d):
    template, who, unit, final_tpl = rng.choice(INVERSE)
    for _ in range(60):
        if who == "km/h":
            a, c = rng.choice([40, 50, 60, 70, 80, 90, 100, 110, 120]), rng.choice([40, 50, 60, 70, 80, 90, 100, 110, 120])
            b = rng.randint(1, 10)
        else:
            a, c = rng.randint(2, 20), rng.randint(2, 30)
            b = rng.randint(2, 90)
        x = Fraction(a * b, c)
        if a != c and exact_at(x, 2) and (unit != "dias" or x.denominator == 1):
            break
    else:
        raise Reject("sem-exemplo")
    d.given(a=a, b=b, c=c)
    k = d.calc("constante", f"{a} * {b}", a * b)
    res = d.calc("x", f"{a} * {b} / {c}", x)
    more = c > a
    steps = [f"é proporção inversa: {'mais' if more else 'menos'} {who} ({c} em vez de {a}) significa {'menos' if more else 'mais'} {unit}",
             f"{a} × {b} = {c} × x, então x = {N(k)} ÷ {c} = {N(res)}"]
    if who == "km/h":
        steps[0] = f"é proporção inversa: a {c} km/h em vez de {a} km/h, o tempo {'diminui' if more else 'aumenta'}"
    display = N(res)
    final = final_tpl.format(v=display)
    if unit == "horas" and x.denominator != 1:
        minutes = x * 60
        if minutes.denominator == 1:
            d.calc("minutos", f"{a} * {b} / {c} * 60", minutes)
            final = final_tpl.format(v=display).rstrip(".") + f" ({duration(int(minutes))})."
    question = template.format(a=a, b=b, c=c)
    d.result("x", res, display)
    return Out(question, steps, final, f"{display} {unit}")


RECIPES = [
    ("bolo de cenoura", [(3, "cenouras médias", True), (4, "ovos", True), (1, "xícara de óleo", False),
                         (2, "xícaras de açúcar", False), (Fraction(5, 2), "xícaras de farinha de trigo", False)]),
    ("pão de queijo", [(500, "g de polvilho", False), (250, "mL de leite", False), (2, "ovos", True), (150, "g de queijo ralado", False)]),
    ("brigadeiro", [(1, "lata de leite condensado", True), (1, "colher de sopa de manteiga", False), (4, "colheres de chocolate em pó", False)]),
    ("panqueca", [(2, "xícaras de farinha", False), (2, "ovos", True), (Fraction(3, 2), "xícara de leite", False)]),
    ("arroz", [(2, "xícaras de arroz", False), (4, "xícaras de água", False), (1, "colher de chá de sal", False)]),
    ("feijoada", [(1000, "g de feijão preto", False), (500, "g de costela", False), (300, "g de linguiça", False), (200, "g de bacon", False)]),
    ("mousse de maracujá", [(1, "lata de leite condensado", True), (1, "caixa de creme de leite", True), (200, "mL de suco de maracujá", False)]),
    ("vitamina de banana", [(3, "bananas", True), (500, "mL de leite", False), (2, "colheres de aveia", False)]),
    ("farofa", [(500, "g de farinha de mandioca", False), (100, "g de manteiga", False), (1, "cebola", True)]),
    ("lasanha", [(500, "g de massa", False), (400, "g de molho", False), (300, "g de queijo", False), (300, "g de presunto", False)]),
    ("cuscuz", [(500, "g de flocão de milho", False), (300, "mL de água", False), (1, "colher de chá de sal", False)]),
]
FACTOR_WORDS = [("metade", Fraction(1, 2)), ("o dobro", Fraction(2)), ("o triplo", Fraction(3)), ("uma vez e meia", Fraction(3, 2))]


@kind("receita_escala", "problemas/receitas", 1.0)
def k_receita_escala(rng, d):
    recipe, ingredients = rng.choice(RECIPES)
    chosen = ingredients if len(ingredients) <= 3 else rng.sample(ingredients, 3)
    by_word = rng.random() < 0.3
    for _ in range(40):
        n1 = rng.choice([2, 4, 5, 6, 8, 10, 12])
        if by_word:
            word, factor = rng.choice(FACTOR_WORDS)
            n2 = n1 * factor
        else:
            n2 = Fraction(rng.choice([2, 3, 4, 5, 6, 8, 9, 10, 12, 15, 16, 18, 20, 24, 25, 30, 40]))
            factor = n2 / n1
        if n2 == n1 or n2.denominator != 1:
            continue
        ok = all((q * factor).denominator == 1 if whole else exact_at(q * factor, 2) for q, _, whole in chosen)
        if ok:
            break
    else:
        raise Reject("sem-exemplo")
    desc = listing([f"{N(q)} {name}" for q, name, _ in chosen])
    d.given(porcoes=n1, quantidades=[q for q, _, _ in chosen], **({} if by_word else {"porcoes_novas": n2}))
    if by_word:
        d.const(fator=factor)
        question = rng.choice([f"Uma receita de {recipe} rende {n1} porções e leva {desc}. Quero fazer {word} da receita. Quanto uso de cada?",
                               f"Vou fazer {word} da receita de {recipe}. A original (para {n1} pessoas) leva {desc}. Como fica?"])
        f = d.calc("fator", f"{n2} / {n1}", factor)
        steps = [f"{word} da receita significa multiplicar tudo por {N(f, 4)}"]
    else:
        question = rng.choice([f"Uma receita de {recipe} para {n1} pessoas leva {desc}. Quanto preciso de cada ingrediente para {N(n2)} pessoas?",
                               f"Vou fazer {recipe} para {N(n2)} pessoas, mas a receita é para {n1}: {desc}. Como ajusto as quantidades?"])
        f = d.calc("fator", f"{lit(n2)} / {n1}", factor, places=4)
        steps = [f"fator: {N(n2)} ÷ {n1} {eq_or_approx(f, 4)} {N(f, 4)}"]
    results, displays = [], []
    for q, name, _ in chosen:
        v = d.calc("ingrediente", f"{lit(q)} * {lit(n2)} / {n1}", q * factor)
        steps.append(f"{name.split(' de ')[-1] if ' de ' in name else name}: {N(q)} × {N(n2)}/{n1} = {N(v)}" if not by_word or factor.denominator != 1 else
                     f"{name.split(' de ')[-1] if ' de ' in name else name}: {N(q)} × {N(factor)} = {N(v)}")
        results.append(v)
        displays.append(f"{N(v)} {name}")
    who = f"{N(n2)} pessoas" if not by_word else f"{word} da receita"
    final = f"Para {who}: {listing(displays)}."
    d.result("quantidades", results, displays)
    return Out(question, steps, final, listing(displays))


@kind("divisao_proporcional", "proporcao", 0.9)
def k_divisao_proporcional(rng, d):
    mode = rng.choice(["razao", "investimento"])
    d.param(modo=mode)
    people = names(rng, rng.choice([2, 2, 3]))
    for _ in range(40):
        if mode == "razao":
            ratios = [Fraction(rng.randint(1, 9)) for _ in people]
        else:
            ratios = [Fraction(rng.randint(1, 40) * 1000) for _ in people]
        total_parts = sum(ratios, Fraction(0))
        amount = Fraction(rng.randint(10, 3000) * rng.choice([1, 10, 50]))
        shares = [amount * r / total_parts for r in ratios]
        if len(set(ratios)) > 1 and all(exact_at(s, 2) for s in shares):
            break
    else:
        raise Reject("sem-exemplo")
    d.given(total=amount, partes=ratios)
    tp = d.calc("total_partes", " + ".join(lit(r) for r in ratios), total_parts)
    who = [p[0] for p in people]
    if mode == "razao":
        unit = amount / total_parts
        u = d.calc("valor_parte", f"{lit(amount)} / {lit(tp)}", unit, places=4)
        question = rng.choice([f"Quero dividir {qm(rng, amount)} entre {listing(who)} na proporção {':'.join(N(r) for r in ratios)}. Quanto fica para cada um?",
                               f"Uma premiação de {qm(rng, amount)} vai ser dividida entre {listing(who)} na razão {':'.join(N(r) for r in ratios)}. Quanto recebe cada?"])
        steps = [f"total de partes: {' + '.join(N(r) for r in ratios)} = {N(tp)}", f"cada parte vale {M(amount)} ÷ {N(tp)} {eq_or_approx(u)} {M(u) if exact_at(u, 2) else N(u, 4)}"]
    else:
        invest = listing([f"{w} investiu {qm(rng, r)}" for w, r in zip(who, ratios)], ", ")
        question = f"Numa sociedade, {invest}. O lucro de {qm(rng, amount)} será dividido proporcionalmente ao investimento. Quanto recebe cada um?"
        steps = [f"investimento total: {' + '.join(M(r) for r in ratios)} = {M(tp)}"]
    results = []
    for w, r in zip(who, ratios):
        s = d.calc("parte", f"{lit(amount)} * {lit(r)} / {lit(tp)}", amount * r / total_parts)
        results.append(s)
        if mode == "razao":
            steps.append(f"{w}: {N(r)} × {M(amount)} ÷ {N(tp)} = {M(s)}")
        else:
            steps.append(f"{w}: {M(amount)} × {M(r)} ÷ {M(tp)} = {M(s)}")
    d.calc("soma_partes", " + ".join(lit(s) for s in results), amount)
    final = "Fica assim: " + listing([f"{w}, {M(s)}" for w, s in zip(who, results)], "; ") + "."
    d.result("partes", results, [M(s) for s in results])
    return Out(question, steps, final, listing([f"{w}: {M(s)}" for w, s in zip(who, results)], "; "))


@kind("escala_mapa", "proporcao", 0.6)
def k_escala_mapa(rng, d):
    scale = rng.choice([50, 100, 200, 500, 1000, 25000, 50000, 100000, 250000, 1000000])
    measure = rand_dec(rng, 0.5, 30, 1)
    real_cm = measure * scale
    big = scale >= 25000
    d.given(escala=scale, medida=measure)
    d.const(cm_por_unidade=100000 if big else 100)
    cm = d.calc("real_cm", f"{lit(measure)} * {scale}", real_cm)
    if big:
        km = d.calc("real_km", f"{lit(measure)} * {scale} / 100000", real_cm / 100000)
        question = rng.choice([f"Num mapa na escala 1:{N(scale)}, duas cidades estão a {N(measure)} cm uma da outra. Qual a distância real?",
                               f"Medi {N(measure)} cm num mapa de escala 1:{N(scale)}. Quantos km são na realidade?"])
        steps = [f"{N(measure)} cm × {N(scale)} = {N(cm)} cm", f"{N(cm)} cm ÷ 100.000 = {N(km, 3)} km"]
        display = f"{N(km, 3)} km"
        d.result("real", km, display)
    else:
        m = d.calc("real_m", f"{lit(measure)} * {scale} / 100", real_cm / 100)
        question = rng.choice([f"Na planta da casa (escala 1:{N(scale)}), a sala mede {N(measure)} cm. Quanto mede de verdade?",
                               f"Numa planta na escala 1:{N(scale)}, um corredor tem {N(measure)} cm. Qual o tamanho real?"])
        steps = [f"{N(measure)} cm × {N(scale)} = {N(cm)} cm", f"{N(cm)} cm ÷ 100 = {N(m, 3)} m"]
        display = f"{N(m, 3)} m"
        d.result("real", m, display)
    return Out(question, steps, f"A medida real é {display}.", display)


# --------------------------------------------------------------- equações

def term(coef, var):
    if coef == 1:
        return var
    if coef == -1:
        return "-" + var
    return f"{N(coef)}{var}"


def signed(value):
    return f" + {N(value)}" if value >= 0 else f" - {N(-value)}"


@kind("equacao_1grau", "equacoes", 1.4, school=True)
def k_equacao_1grau(rng, d):
    var = rng.choice(["x", "x", "x", "y", "n", "t"])
    form = rng.randint(1, 5)
    x = Fraction(rng.randint(-15, 60)) if rng.random() < 0.88 else Fraction(rng.randint(-20, 60) * 2 + 1, 2)
    d.param(forma=form, variavel=var)
    vv = {"x": x}
    if form == 1:  # a x + b = c
        a = rng.choice([v for v in range(-6, 13) if v not in (0, 1)])
        b = Fraction(rng.choice([v for v in range(-60, 61) if v]))
        c = a * x + b
        eq = f"{term(a, var)}{signed(b)} = {N(c)}"
        d.given_coef(a=a)
        d.given(b=b, c=c)
        rhs = d.calc("isola_termo", f"{lit(c)} - {lit(b)}", c - b)
        sol = d.calc("divide", f"{lit(rhs)} / {a}", x)
        d.test("substituicao", f"abs({a} * x + {lit(b)} - {lit(c)}) < 1e-9", True, vv)
        steps = [f"passando o {N(abs(b))} para o outro lado: {term(a, var)} = {N(c)} {'-' if b > 0 else '+'} {N(abs(b))} = {N(rhs)}",
                 f"dividindo por {N(a)}: {var} = {N(rhs)} ÷ {'(' + N(a) + ')' if a < 0 else N(a)} = {N(sol)}",
                 f"conferindo: {N(a)} × {'(' + N(x) + ')' if x < 0 else N(x)}{signed(b)} = {N(c)}"]
    elif form == 2:  # a (x + b) = c
        a = rng.randint(2, 9)
        b = Fraction(rng.choice([v for v in range(-20, 21) if v]))
        c = a * (x + b)
        eq = f"{a}({var}{signed(b)}) = {N(c)}"
        d.given(a=a, b=b, c=c)
        inner = d.calc("divide", f"{lit(c)} / {a}", x + b)
        sol = d.calc("isola", f"{lit(inner)} - {lit(b)}", x)
        d.test("substituicao", f"abs({a} * (x + {lit(b)}) - {lit(c)}) < 1e-9", True, vv)
        steps = [f"dividindo os dois lados por {a}: {var}{signed(b)} = {N(c)} ÷ {a} = {N(inner)}",
                 f"{var} = {N(inner)} {'-' if b > 0 else '+'} {N(abs(b))} = {N(sol)}",
                 f"conferindo: {a} × ({N(x)}{signed(b)}) = {N(c)}"]
    elif form == 3:  # a x + b = c x + e
        a, c = rng.sample([v for v in range(-5, 13) if v], 2)
        b = Fraction(rng.randint(-40, 40))
        e = (a - c) * x + b
        eq = f"{term(a, var)}{signed(b) if b else ''} = {term(c, var)}{signed(e) if e else ''}"
        d.given_coef(a=a, c=c)
        d.given(b=b, e=e)
        coef = d.calc("junta_x", f"{a} - {c}", a - c)
        rhs = d.calc("junta_numeros", f"{lit(e)} - {lit(b)}", e - b)
        sol = d.calc("divide", f"{lit(rhs)} / {lit(coef)}", x)
        d.test("substituicao", f"abs(({a} * x + {lit(b)}) - ({c} * x + {lit(e)})) < 1e-9", True, vv)
        steps = [f"juntando os termos com {var} de um lado e os números do outro: {term(a, var)} {'-' if c > 0 else '+'} {term(abs(c), var)} = {N(e)} {'-' if b >= 0 else '+'} {N(abs(b))}",
                 f"{term(coef, var)} = {N(rhs)}", f"{var} = {N(rhs)} ÷ {'(' + N(coef) + ')' if coef < 0 else N(coef)} = {N(sol)}"]
    elif form == 4:  # x/a + b = c
        a = rng.randint(2, 9)
        x = Fraction(a * rng.randint(-10, 20))
        vv = {"x": x}
        b = Fraction(rng.choice([v for v in range(-30, 31) if v]))
        c = x / a + b
        eq = f"{var}/{a}{signed(b)} = {N(c)}"
        d.given(a=a, b=b, c=c)
        rhs = d.calc("isola_fracao", f"{lit(c)} - {lit(b)}", c - b)
        sol = d.calc("multiplica", f"{lit(rhs)} * {a}", x)
        d.test("substituicao", f"abs(x / {a} + {lit(b)} - {lit(c)}) < 1e-9", True, vv)
        steps = [f"{var}/{a} = {N(c)} {'-' if b > 0 else '+'} {N(abs(b))} = {N(rhs)}", f"{var} = {N(rhs)} × {a} = {N(sol)}",
                 f"conferindo: {N(x)} ÷ {a}{signed(b)} = {N(c)}"]
    else:  # (x + a)/b = c
        b = rng.randint(2, 9)
        a = Fraction(rng.choice([v for v in range(-20, 31) if v]))
        c = Fraction(rng.randint(-10, 25))
        x = c * b - a
        vv = {"x": x}
        eq = f"({var}{signed(a)})/{b} = {N(c)}"
        d.given(a=a, b=b, c=c)
        prod = d.calc("multiplica", f"{lit(c)} * {b}", c * b)
        sol = d.calc("isola", f"{lit(prod)} - {lit(a)}", x)
        d.test("substituicao", f"abs((x + {lit(a)}) / {b} - {lit(c)}) < 1e-9", True, vv)
        steps = [f"multiplicando os dois lados por {b}: {var}{signed(a)} = {N(c)} × {b} = {N(prod)}",
                 f"{var} = {N(prod)} {'-' if a > 0 else '+'} {N(abs(a))} = {N(sol)}"]
    question = rng.choice([f"Resolva {eq}.", f"Qual o valor de {var} em {eq}?", f"Me ajuda a achar {var}: {eq}",
                           f"Como resolvo a equação {eq}?", f"Encontre {var} na equação {eq}.", f"{eq}. Quanto vale {var}?",
                           f"Tô travado nessa equação: {eq}. Pode resolver?"])
    display = f"{var} = {N(sol)}"
    final = rng.choice([f"{display}.", f"Solução: {display}.", f"Logo, {display}.", f"Portanto {display}."])
    d.result("solucao", sol, display)
    return Out(question, steps, final, display)


@kind("equacao_problema", "equacoes", 1.1, school=True)
def k_equacao_problema(rng, d):
    form = rng.randint(1, 8)
    d.param(forma=form)
    if form == 1:
        word, a = rng.choice([("O dobro", 2), ("O triplo", 3), ("O quádruplo", 4), ("O quíntuplo", 5)])
        x = rng.randint(1, 80)
        b = rng.randint(1, 60)
        plus = rng.random() < 0.6
        c = a * x + (b if plus else -b)
        d.given(b=b, c=c)
        d.const(a=a)
        d.param(soma=plus)
        rhs = d.calc("isola", f"{c} {'-' if plus else '+'} {b}", a * x)
        sol = d.calc("divide", f"{rhs} / {a}", x)
        d.test("substituicao", f"{a} * x {'+' if plus else '-'} {b} == {c}", True, {"x": x})
        question = f"{word} de um número {'mais' if plus else 'menos'} {b} é igual a {c}. Que número é esse?"
        steps = [f"chamando o número de x: {a}x {'+' if plus else '-'} {b} = {c}", f"{a}x = {c} {'-' if plus else '+'} {b} = {N(rhs)}",
                 f"x = {N(rhs)} ÷ {a} = {N(sol)}"]
        final = f"O número é {N(sol)}."
    elif form == 2:
        a, x, b = rng.randint(2, 12), rng.randint(1, 99), rng.randint(1, 99)
        plus = rng.random() < 0.5
        c = a * x + (b if plus else -b)
        d.given(a=a, b=b, c=c)
        d.param(soma=plus)
        rhs = d.calc("desfaz_soma", f"{c} {'-' if plus else '+'} {b}", a * x)
        sol = d.calc("desfaz_produto", f"{rhs} / {a}", x)
        d.test("substituicao", f"x * {a} {'+' if plus else '-'} {b} == {c}", True, {"x": x})
        question = f"Pensei num número, multipliquei por {a} e {'somei' if plus else 'subtraí'} {b}. Deu {c}. Qual foi o número?"
        steps = [f"desfazendo as operações de trás para frente: {c} {'-' if plus else '+'} {b} = {N(rhs)}", f"{N(rhs)} ÷ {a} = {N(sol)}"]
        final = f"Você pensou no número {N(sol)}."
    elif form == 3:
        for _ in range(40):
            k, j = rng.randint(2, 20), rng.randint(1, 15)
            m = rng.choice([2, 3])
            # x + k = m (x - j)  ->  x = (k + m j) / (m - 1)
            x = Fraction(k + m * j, m - 1)
            if x.denominator == 1 and x - j > 0:
                break
        else:
            raise Reject("sem-exemplo")
        who, gender = rng.choice(NAMES)
        word = "o dobro" if m == 2 else "o triplo"
        d.given(k=k, j=j)
        d.const(m=m)
        num = d.calc("numerador", f"{k} + {m} * {j}", k + m * j)
        sol = d.calc("idade", f"({k} + {m} * {j}) / ({m} - 1)", x)
        d.test("substituicao", f"x + {k} == {m} * (x - {j})", True, {"x": x})
        question = f"Daqui a {k} anos, {who} terá {word} da idade que tinha há {j} anos. Quantos anos {'ela' if gender == 'f' else 'ele'} tem hoje?"
        steps = [f"com x = idade atual: x + {k} = {m}(x - {j})", f"x + {k} = {m}x - {m * j}",
                 f"{k} + {m * j} = {m}x - x, ou seja, {N(num)} = {m - 1}x" if m > 2 else f"{k} + {m * j} = x",
                 f"x = {N(sol)}"]
        if m == 2:
            steps = steps[:2] + [f"x = {k} + {m * j} = {N(sol)}"]
        final = f"{who} tem {N(sol)} anos."
    elif form == 4:
        n, unit_price = rng.randint(2, 12), price(rng, 3, 40)
        extra = price(rng, 1, 15)
        total = n * unit_price + extra
        d.given(total=total, cadernos=n, caneta=extra)
        rest = d.calc("sem_caneta", f"{lit(total)} - {lit(extra)}", n * unit_price)
        sol = d.calc("cada", f"({lit(total)} - {lit(extra)}) / {n}", unit_price)
        question = f"Paguei {qm(rng, total)} em {n} cadernos iguais e uma caneta de {qm(rng, extra)}. Quanto custou cada caderno?"
        steps = [f"{n}x + {M(extra)} = {M(total)}", f"{n}x = {M(total)} - {M(extra)} = {M(rest)}", f"x = {M(rest)} ÷ {n} = {M(sol)}"]
        final = f"Cada caderno custou {M(sol)}."
        d.result("solucao", sol, M(sol))
        return Out(question, steps, final, M(sol))
    elif form == 5:
        k = rng.randint(2, 6)
        w = rng.randint(2, 60)
        per = 2 * (k + 1) * w
        d.given(perimetro=per, vezes=k)
        half = d.calc("semi_perimetro", f"{per} / 2", per // 2)
        wid = d.calc("largura", f"{per} / (2 * ({k} + 1))", w)
        length = d.calc("comprimento", f"{k} * {w}", k * w)
        d.calc("conferencia", f"2 * ({k * w} + {w})", per)
        question = f"Um terreno retangular tem {N(per)} m de perímetro, e o comprimento é {k} vezes a largura. Quais são as medidas?"
        steps = [f"largura = x e comprimento = {k}x", f"perímetro: 2({k}x + x) = {N(per)}, então {k + 1}x = {N(half)}",
                 f"x = {N(half)} ÷ {k + 1} = {N(wid)} m; comprimento = {k} × {N(wid)} = {N(length)} m"]
        final = f"Largura de {N(wid)} m e comprimento de {N(length)} m."
        d.result("medidas", [wid, length], [f"{N(wid)} m", f"{N(length)} m"])
        return Out(question, steps, final, f"{N(wid)} m × {N(length)} m")
    elif form == 6:
        x = rng.randint(1, 300)
        s = 3 * x + 3
        d.given(soma=s)
        rhs = d.calc("tira_3", f"{s} - 3", 3 * x)
        sol = d.calc("divide", f"({s} - 3) / 3", x)
        question = rng.choice([f"A soma de três números inteiros consecutivos é {N(s)}. Quais são eles?",
                               f"Três números consecutivos somam {N(s)}. Que números são?"])
        steps = [f"x + (x + 1) + (x + 2) = {N(s)}", f"3x + 3 = {N(s)}, então 3x = {N(rhs)}", f"x = {N(rhs)} ÷ 3 = {N(sol)}"]
        d.calc("conferencia", f"{x} + {x + 1} + {x + 2}", s)
        final = f"Os números são {N(x)}, {N(x + 1)} e {N(x + 2)}."
        d.result("numeros", [x, x + 1, x + 2], [N(x), N(x + 1), N(x + 2)])
        return Out(question, steps, final, f"{N(x)}, {N(x + 1)} e {N(x + 2)}")
    elif form == 7:
        fixed, per = price(rng, 29, 120), price(rng, 2, 20)
        k = rng.randint(1, 30)
        total = fixed + k * per
        context = rng.choice([("Um plano de celular cobra {f} fixos mais {u} por GB extra. A conta veio {t}. Quantos GB extras foram usados?", "Foram {k} GB extras."),
                              ("Uma corrida de táxi custa {f} de bandeirada mais {u} por km. Paguei {t}. Quantos km rodei?", "Você rodou {k} km."),
                              ("A academia cobra {f} de matrícula e {u} por aula avulsa. Gastei {t} no total. Quantas aulas fiz?", "Foram {k} aulas.")])
        d.given(fixo=fixed, unitario=per, total=total)
        rest = d.calc("variavel", f"{lit(total)} - {lit(fixed)}", k * per)
        sol = d.calc("quantidade", f"({lit(total)} - {lit(fixed)}) / {lit(per)}", k)
        question = context[0].format(f=qm(rng, fixed), u=qm(rng, per), t=qm(rng, total))
        steps = [f"{M(fixed)} + {M(per)} × x = {M(total)}", f"{M(per)} × x = {M(total)} - {M(fixed)} = {M(rest)}",
                 f"x = {M(rest)} ÷ {M(per)} = {N(sol)}"]
        final = context[1].format(k=N(sol))
        d.result("solucao", sol, N(sol))
        return Out(question, steps, final, N(sol))
    else:
        who, gender = rng.choice(NAMES)
        a = Fraction(rng.randint(0, 50) * 10)
        b = Fraction(rng.choice([10, 15, 20, 25, 30, 40, 50]))
        k = rng.randint(2, 40)
        c = a + k * b
        d.given(inicial=a, por_semana=b, meta=c)
        rest = d.calc("falta", f"{lit(c)} - {lit(a)}", k * b)
        sol = d.calc("semanas", f"({lit(c)} - {lit(a)}) / {lit(b)}", k)
        question = f"{who} tem {qm(rng, a)} e guarda {qm(rng, b)} por semana. Em quantas semanas terá {qm(rng, c)}?"
        steps = [f"{M(a)} + {M(b)} × x = {M(c)}", f"falta juntar {M(c)} - {M(a)} = {M(rest)}", f"x = {M(rest)} ÷ {M(b)} = {N(sol)}"]
        final = f"Em {N(sol)} semanas."
        d.result("solucao", sol, N(sol))
        return Out(question, steps, final, f"{N(sol)} semanas")
    d.result("solucao", sol, N(sol))
    return Out(question, steps, final, N(sol))


@kind("sistema_problema", "equacoes", 0.9, school=True)
def k_sistema_problema(rng, d):
    form = rng.randint(1, 5)
    d.param(forma=form)
    if form in (1, 2):
        y = rng.randint(1, 500)
        diff = rng.randint(1, 300)
        x = y + diff
        s = x + y
        d.given(soma=s, diferenca=diff)
        dbl = d.calc("soma_mais_diferenca", f"{s} + {diff}", 2 * x)
        big = d.calc("maior", f"({s} + {diff}) / 2", x)
        small = d.calc("menor", f"{s} - {x}", y)
        d.test("substituicao", f"x + y == {s} and x - y == {diff}", True, {"x": x, "y": y})
        if form == 1:
            question = rng.choice([f"Dois números somam {N(s)} e a diferença entre eles é {N(diff)}. Quais são os números?",
                                   f"A soma de dois números é {N(s)} e a diferença é {N(diff)}. Que números são esses?"])
            steps = [f"x + y = {N(s)} e x - y = {N(diff)}", f"somando as duas: 2x = {N(dbl)}, então x = {N(big)}", f"y = {N(s)} - {N(big)} = {N(small)}"]
            final = f"Os números são {N(big)} e {N(small)}."
            displays = [N(big), N(small)]
        else:
            (p1, _), (p2, _) = names(rng, 2)
            question = f"{p1} e {p2} têm juntos {qm(rng, s)}. {p1} tem {qm(rng, diff)} a mais que {p2}. Quanto tem cada um?"
            steps = [f"{p1} + {p2} = {M(s)} e {p1} - {p2} = {M(diff)}", f"2 × {p1} = {M(s)} + {M(diff)} = {M(dbl)}, então {p1} tem {M(big)}",
                     f"{p2}: {M(s)} - {M(big)} = {M(small)}"]
            final = f"{p1} tem {M(big)} e {p2} tem {M(small)}."
            displays = [M(big), M(small)]
        d.result("par", [x, y], displays)
        return Out(question, steps, final, " e ".join(displays))
    if form in (3, 4):
        motos_or_hens = rng.randint(1, 120)
        cars_or_pigs = rng.randint(1, 120)
        total = motos_or_hens + cars_or_pigs
        legs = 2 * motos_or_hens + 4 * cars_or_pigs
        d.given(total=total, rodas=legs)
        d.const(menor=2, maior=4)
        all4 = d.calc("se_todos_4", f"4 * {total}", 4 * total)
        small = d.calc("de_2", f"(4 * {total} - {legs}) / 2", motos_or_hens)
        big = d.calc("de_4", f"{total} - {small}", cars_or_pigs)
        d.test("substituicao", f"2 * m + 4 * c == {legs} and m + c == {total}", True, {"m": motos_or_hens, "c": cars_or_pigs})
        if form == 3:
            question = f"Num estacionamento há {total} veículos entre carros e motos, somando {legs} rodas. Quantos carros e quantas motos?"
            steps = [f"se todos fossem carros seriam 4 × {total} = {N(all4)} rodas", f"sobram {N(all4)} - {legs} = {N(all4 - legs)} rodas a menos; cada moto tem 2 rodas a menos que um carro",
                     f"motos: {N(all4 - legs)} ÷ 2 = {N(small)}; carros: {total} - {N(small)} = {N(big)}"]
            final = f"São {N(big)} carros e {N(small)} motos."
            displays = [f"{N(big)} carros", f"{N(small)} motos"]
        else:
            question = f"Num sítio há galinhas e porcos: são {total} cabeças e {legs} pés. Quantos animais de cada?"
            steps = [f"se todos fossem porcos seriam 4 × {total} = {N(all4)} pés", f"faltam {N(all4)} - {legs} = {N(all4 - legs)} pés; cada galinha tem 2 pés a menos",
                     f"galinhas: {N(all4 - legs)} ÷ 2 = {N(small)}; porcos: {total} - {N(small)} = {N(big)}"]
            final = f"São {N(small)} galinhas e {N(big)} porcos."
            displays = [f"{N(small)} galinhas", f"{N(big)} porcos"]
        d.calc("rodas_a_menos", f"{all4} - {legs}", all4 - legs)
        d.result("quantidades", [cars_or_pigs, motos_or_hens], displays)
        return Out(question, steps, final, " e ".join(displays))
    full_price = Fraction(rng.randint(10, 120))
    half_price = full_price / 2
    full_n, half_n = rng.randint(1, 300), rng.randint(1, 300)
    total_n = full_n + half_n
    money = full_n * full_price + half_n * half_price
    d.given(inteira=full_price, meia=half_price, ingressos=total_n, arrecadado=money)
    if_all_half = d.calc("tudo_meia", f"{total_n} * {lit(half_price)}", total_n * half_price)
    extra = d.calc("excedente", f"{lit(money)} - {total_n} * {lit(half_price)}", money - total_n * half_price)
    fulls = d.calc("inteiras", f"({lit(money)} - {total_n} * {lit(half_price)}) / ({lit(full_price)} - {lit(half_price)})", full_n)
    halfs = d.calc("meias", f"{total_n} - {full_n}", half_n)
    question = (f"Um ingresso inteiro custa {qm(rng, full_price)} e a meia-entrada, {qm(rng, half_price)}. Foram vendidos {N(total_n)} ingressos e arrecadados "
                f"{qm(rng, money)}. Quantas inteiras e quantas meias foram vendidas?")
    steps = [f"se todos fossem meia: {N(total_n)} × {M(half_price)} = {M(if_all_half)}",
             f"o que passou disso, {M(money)} - {M(if_all_half)} = {M(extra)}, vem das inteiras, que custam {M(full_price - half_price)} a mais cada",
             f"inteiras: {M(extra)} ÷ {M(full_price - half_price)} = {N(fulls)}; meias: {N(total_n)} - {N(fulls)} = {N(halfs)}"]
    d.calc("diferenca_preco", f"{lit(full_price)} - {lit(half_price)}", full_price - half_price)
    final = f"Foram {N(fulls)} inteiras e {N(halfs)} meias."
    d.result("ingressos", [full_n, half_n], [f"{N(fulls)} inteiras", f"{N(halfs)} meias"])
    return Out(question, steps, final, f"{N(fulls)} inteiras e {N(halfs)} meias")


# --------------------------------------------------------------- problemas do dia a dia

GROCERIES = {
    "mercado": [("pacote", "pacotes", "de arroz de 5 kg", 19, 35), ("pacote", "pacotes", "de feijão", 6, 12),
                ("pacote", "pacotes", "de café", 12, 28), ("caixa", "caixas", "de leite", 4, 8), ("dúzia", "dúzias", "de ovos", 9, 18),
                ("garrafa", "garrafas", "de óleo", 6, 11), ("pacote", "pacotes", "de macarrão", 3, 8),
                ("lata", "latas", "de molho de tomate", 2, 6), ("pote", "potes", "de margarina", 6, 12),
                ("pacote", "pacotes", "de papel higiênico", 15, 30), ("sabonete", "sabonetes", "", 2, 6),
                ("refrigerante de 2 litros", "refrigerantes de 2 litros", "", 7, 13)],
    "feira": [(None, None, "tomate", 4, 12), (None, None, "banana", 4, 9), (None, None, "batata", 3, 9),
              (None, None, "cebola", 3, 8), (None, None, "maçã", 6, 14), (None, None, "uva", 8, 18), (None, None, "laranja", 3, 7),
              (None, None, "mamão", 4, 9)],
    "acougue": [(None, None, "carne moída", 30, 50), (None, None, "frango", 12, 25), (None, None, "linguiça", 18, 35),
                (None, None, "costela", 25, 45)],
    "papelaria": [("caderno", "cadernos", "", 9, 35), ("caneta", "canetas", "", 2, 8), ("lápis", "lápis", "", 1, 4),
                  ("borracha", "borrachas", "", 1, 5), ("pacote", "pacotes", "de folhas sulfite", 20, 35),
                  ("cola", "colas", "", 3, 9), ("estojo", "estojos", "", 15, 40)],
}
STORE_TEXT = {"mercado": ["Fui ao mercado e peguei", "Nas compras do mês levei", "No mercado comprei"],
              "feira": ["Na feira comprei", "Passei na feira e levei", "Comprei na feira"],
              "acougue": ["No açougue pedi", "Comprei no açougue", "Para o churrasco comprei"],
              "papelaria": ["Comprei para a escola das crianças", "Na papelaria levei", "Material escolar:"]}


@kind("compras_total", "problemas/compras", 1.4)
def k_compras_total(rng, d):
    store = rng.choice(list(GROCERIES))
    pool = GROCERIES[store]
    picks = rng.sample(pool, min(len(pool), rng.randint(2, 4)))
    quantities, prices, descs, lines, subtotals = [], [], [], [], []
    for unit_s, unit_p, desc, lo, hi in picks:
        for _ in range(30):
            pr = price(rng, lo, hi)
            if unit_s is None:
                q = rng.choice([Fraction(1, 2), Fraction(1), Fraction(3, 2), Fraction(2), Fraction(5, 2), Fraction(3), Fraction(3, 4), Fraction(1, 4)])
            else:
                q = Fraction(rng.randint(1, 6))
            if exact_at(q * pr, 2):
                break
        else:
            raise Reject("centavos")
        quantities.append(q)
        prices.append(pr)
        if unit_s is None:
            descs.append(f"{N(q)} kg de {desc} a {qm(rng, pr)} o quilo" if rng.random() < 0.6 else f"{N(q)} kg de {desc} ({qm(rng, pr)}/kg)")
            label = f"{desc} ({N(q)} kg)"
        else:
            name = (unit_s if q == 1 else unit_p) + (" " + desc if desc else "")
            descs.append(f"{N(q)} {name} a {qm(rng, pr)} cada" if q > 1 else f"{N(q)} {name} por {qm(rng, pr)}")
            label = name.split(" de ")[-1] if " de " in name and unit_s in ("pacote", "caixa", "garrafa", "lata", "pote", "dúzia") else name
        sub = d.calc("subtotal", f"{lit(q)} * {lit(pr)}", q * pr)
        subtotals.append(sub)
        lines.append(f"{label}: {N(q)} × {M(pr)} = {M(sub)}")
    d.given(quantidades=quantities, precos=prices)
    total = d.calc("total", " + ".join(f"{lit(q)} * {lit(p)}" for q, p in zip(quantities, prices)), sum(subtotals, Fraction(0)))
    lines.append(f"total: {' + '.join(M(s) for s in subtotals)} = {M(total)}")
    extra = rng.choice(["nada", "troco", "dividir"])
    d.param(extra=extra, loja=store)
    question = f"{rng.choice(STORE_TEXT[store])} {listing(descs)}. "
    question += rng.choice(["Quanto deu?", "Quanto gastei no total?", "Qual o total da compra?"])
    finals = [total]
    displays = [M(total)]
    final = rng.choice([f"O total foi {M(total)}.", f"Você gastou {M(total)}."])
    if extra == "troco":
        paid = Fraction(next(n for n in [10, 20, 50, 100, 150, 200, 300, 500, 1000] if n >= total))
        if paid == total:
            paid += 50
        change = d.calc("troco", f"{lit(paid)} - ({' + '.join(f'{lit(q)} * {lit(p)}' for q, p in zip(quantities, prices))})", paid - total)
        d.given(pago=paid)
        question = question[:-len(question.split(". ")[-1])] + f"Paguei com {qm(rng, paid)}. Quanto recebo de troco?"
        lines.append(f"troco: {M(paid)} - {M(total)} = {M(change)}")
        final = f"O total foi {M(total)} e o troco é de {M(change)}."
        finals.append(paid - total)
        displays.append(M(change))
    elif extra == "dividir":
        k = rng.choice([2, 3, 4, 5])
        each = total / k
        e = d.calc("por_pessoa", f"({' + '.join(f'{lit(q)} * {lit(p)}' for q, p in zip(quantities, prices))}) / {k}", each, places=2)
        d.given(pessoas=k)
        question = question[:-len(question.split(". ")[-1])] + f"Vamos dividir entre {k} pessoas. Quanto fica para cada uma?"
        lines.append(f"por pessoa: {M(total)} ÷ {k} {eq_or_approx(e)} {M(e)}")
        final = f"O total foi {M(total)}; para cada uma das {k} pessoas, {approx(e)}{M(e)}."
        if not exact_at(e, 2):
            final += f" Como {M(total)} não divide exato por {k}, alguém acerta os centavos de diferença."
        finals.append(each)
        displays.append(M(e))
    d.result("compra", finals, displays)
    return Out(question, lines, final, " / ".join(displays))


UNIT_PRODUCTS = [
    ("café", "g", [250, 500, 1000], "kg", 1000, (8, 70)),
    ("arroz", "kg", [1, 2, 5], "kg", 1, (5, 35)),
    ("sabão em pó", "g", [800, 1600, 2400, 4000], "kg", 1000, (10, 70)),
    ("refrigerante", "mL", [350, 600, 1500, 2000], "L", 1000, (3, 14)),
    ("azeite", "mL", [250, 500, 1000], "L", 1000, (18, 90)),
    ("papel higiênico", "rolos", [4, 12, 16, 24], "rolo", 1, (6, 45)),
    ("fralda", "unidades", [24, 36, 48, 72], "unidade", 1, (40, 140)),
    ("detergente", "mL", [500, 1500, 5000], "L", 1000, (2, 25)),
]


@kind("preco_unitario", "problemas/compras", 1.0)
def k_preco_unitario(rng, d):
    product, unit, sizes, per, div, (lo, hi) = rng.choice(UNIT_PRODUCTS)
    s1, s2 = sorted(rng.sample(sizes, 2))
    p1 = price(rng, lo, max(lo + 1, (lo + hi) // 2))
    ratio = Fraction(rng.randint(80, 125), 100)
    p2 = Fraction(quant(p1 * Fraction(s2, s1) * ratio, 1)) - Fraction(rng.choice([0, 1, 10]), 100)
    if p2 <= 0:
        raise Reject("sem-exemplo")
    d.given(tamanho1=s1, preco1=p1, tamanho2=s2, preco2=p2)
    d.const(divisor=div)
    q1, q2 = Fraction(s1, div), Fraction(s2, div)
    u1, u2 = p1 / q1, p2 / q2
    v1 = d.calc("unitario_1", f"{lit(p1)} / {s1} * {div}", u1, places=2)
    v2 = d.calc("unitario_2", f"{lit(p2)} / {s2} * {div}", u2, places=2)
    cheaper = d.test("compara", f"{lit(p2)} / {s2} < {lit(p1)} / {s1}", u2 < u1)
    d.test("empate", f"{lit(p2)} * {s1} == {lit(p1)} * {s2}", u1 == u2)
    def size_txt(s):
        return f"{N(s)} {unit}" if unit not in ("kg",) else f"{N(s)} kg"
    question = rng.choice([
        f"O que compensa mais: {product} de {size_txt(s1)} por {qm(rng, p1)} ou de {size_txt(s2)} por {qm(rng, p2)}?",
        f"No mercado tem {product} de {size_txt(s1)} a {qm(rng, p1)} e de {size_txt(s2)} a {qm(rng, p2)}. Qual sai mais barato proporcionalmente?",
        f"Qual vale mais a pena, {product} {size_txt(s1)} por {qm(rng, p1)} ou {size_txt(s2)} por {qm(rng, p2)}?",
    ])
    tag = f"/{per}"
    steps = [f"{size_txt(s1)}: {M(p1)} ÷ {N(q1, 3)} {eq_or_approx(v1)} {M(v1)}{tag}",
             f"{size_txt(s2)}: {M(p2)} ÷ {N(q2, 3)} {eq_or_approx(v2)} {M(v2)}{tag}"]
    if u1 == u2:
        final = f"Saem iguais: {M(v1)}{tag} nos dois."
        winner = 0
    else:
        best, other, bs, bv, ov = (s2, s1, q2, v2, v1) if cheaper else (s1, s2, q1, v1, v2)
        winner = 2 if cheaper else 1
        final = rng.choice([f"O de {size_txt(best)} sai mais em conta: {approx(bv)}{M(bv)}{tag} contra {approx(ov)}{M(ov)}{tag}.",
                            f"Pelo preço por {per}, compensa o de {size_txt(best)} ({approx(bv)}{M(bv)}{tag} contra {approx(ov)}{M(ov)}{tag})."])
    d.param(vencedor=winner)
    d.result("precos_unitarios", [u1, u2], [M(v1), M(v2)])
    short = (f"o de {size_txt(s2 if cheaper else s1)} ({M(v1)}{tag} contra {M(v2)}{tag})" if u1 != u2 else f"iguais, {M(v1)}{tag}")
    if u1 != u2 and cheaper:
        short = f"o de {size_txt(s2)} ({M(v2)}{tag} contra {M(v1)}{tag})"
    return Out(question, steps, final, short)


@kind("dividir_conta", "problemas/compras", 0.9)
def k_dividir_conta(rng, d):
    bill = price(rng, 40, 900)
    people = rng.randint(2, 12)
    service = Fraction(rng.choice([0, 10, 10, 10, 12, 13, 15]))
    total = bill * (1 + service / 100)
    each = total / people
    d.given(conta=bill, pessoas=people, **({"servico": service} if service else {}))
    tot = d.calc("total", f"{lit(bill)} * (1 + {lit(service)} / 100)", total, places=2)
    e = d.calc("por_pessoa", f"{lit(bill)} * (1 + {lit(service)} / 100) / {people}", each, places=2)
    steps = []
    if service:
        sv = d.calc("servico", f"{lit(bill)} * {lit(service)} / 100", bill * service / 100, places=2)
        steps.append(f"serviço: {P(service)} de {M(bill)} {eq_or_approx(sv)} {M(sv)}")
        steps.append(f"total: {M(bill)} + {M(sv)} {eq_or_approx(tot)} {M(tot)}")
    steps.append(f"por pessoa: {M(tot)} ÷ {people} {eq_or_approx(e)} {M(e)}")
    b = qm(rng, bill)
    if service:
        question = rng.choice([f"A conta do restaurante deu {b}, mais {P(service)} de serviço. Somos {people}. Quanto cada um paga?",
                               f"Jantar: {b} + {P(service)} do garçom, dividido por {people} pessoas. Quanto dá pra cada?"])
    else:
        question = rng.choice([f"Rachando uma conta de {b} entre {people} amigos, quanto dá pra cada?",
                               f"O churrasco custou {b} e vamos dividir entre {people} pessoas. Quanto cada um põe?"])
    final = f"Cada pessoa paga {approx(e)}{M(e)}."
    if not exact_at(each, 2):
        each_r, total_r = Fraction(quant(each, 2)), Fraction(quant(total, 2))
        paid = d.calc("soma_arredondada", f"{lit(each_r)} * {people}", each_r * people)
        diff = d.calc("diferenca_centavos", f"{lit(total_r)} - {lit(each_r)} * {people}", total_r - each_r * people)
        if diff > 0:
            final += f" Como {people} × {M(e)} = {M(paid)}, faltam {M(diff)} para fechar {M(total_r)}; alguém completa esses centavos."
        elif diff < 0:
            final += f" Como {people} × {M(e)} = {M(paid)}, isso passa {M(-diff)} do total de {M(total_r)}."
    d.result("por_pessoa", each, M(e))
    return Out(question, steps, final, approx(e) + M(e))


@kind("viagem_consumo", "problemas/viagens", 1.1)
def k_viagem_consumo(rng, d):
    dist = Fraction(rng.randint(30, 1500))
    km_l = rand_dec(rng, 7, 18, rng.choice([0, 0, 1]))
    fuel = rand_dec(rng, 4.59, 7.29, 2)
    round_trip = rng.random() < 0.3
    split = rng.choice([0, 0, 0, 2, 3, 4, 5])
    total_km = dist * (2 if round_trip else 1)
    liters = total_km / km_l
    cost = liters * fuel
    d.given(distancia=dist, consumo=km_l, preco_litro=fuel, **({"pessoas": split} if split else {}))
    if round_trip:
        d.const(ida_e_volta=2)
    d.param(ida_e_volta=round_trip, dividir=split)
    steps = []
    if round_trip:
        tk = d.calc("km_total", f"{lit(dist)} * 2", total_km)
        steps.append(f"ida e volta: {N(dist)} × 2 = {N(tk)} km")
    lt = d.calc("litros", f"{lit(total_km)} / {lit(km_l)}", liters, places=2)
    ct = d.calc("custo", f"{lit(total_km)} / {lit(km_l)} * {lit(fuel)}", cost, places=2)
    steps.append(f"combustível: {N(total_km)} ÷ {N(km_l)} {eq_or_approx(lt)} {N(lt)} litros")
    if exact_at(liters, 2):
        steps.append(f"custo: {N(lt)} × {M(fuel)} {eq_or_approx(ct)} {M(ct)}")
    else:
        steps.append(f"custo: ({N(total_km)} ÷ {N(km_l)}) × {M(fuel)} ≈ {M(ct)}")
    finals, displays = [liters, cost], [f"{N(lt)} litros", M(ct)]
    final = f"Você vai gastar {approx(lt)}{N(lt)} litros, o que dá {approx(ct)}{M(ct)} de combustível."
    if split:
        each = cost / split
        e = d.calc("por_pessoa", f"{lit(total_km)} / {lit(km_l)} * {lit(fuel)} / {split}", each, places=2)
        steps.append(f"por pessoa: {M(ct)} ÷ {split} {eq_or_approx(e)} {M(e)}")
        final += f" Dividindo por {split}, {approx(e)}{M(e)} para cada."
        finals.append(each)
        displays.append(M(e))
    km = f"{qn(rng, dist)} km"
    if round_trip:
        question = (f"A distância até a praia é de {km}. Vou e volto no mesmo dia; o carro faz {N(km_l)} km/L e o litro custa {qm(rng, fuel)}. "
                    "Quanto gasto de combustível?")
    else:
        question = rng.choice([f"Vou viajar {km}. Meu carro faz {N(km_l)} km por litro e a gasolina está {qm(rng, fuel)}. Quanto vou gastar?",
                               f"Quantos litros gasto numa viagem de {km} se o carro faz {N(km_l)} km/L? E quanto custa, com o litro a {qm(rng, fuel)}?",
                               f"Uma viagem de {km}, carro fazendo {N(km_l)} km/L e combustível a {qm(rng, fuel)}. Qual o gasto?"])
    if split:
        question += f" Vamos em {split} pessoas e dividir a gasolina."
    d.result("consumo", finals, displays)
    return Out(question, steps, final, " / ".join(displays))


@kind("viagem_tempo", "problemas/viagens", 1.0)
def k_viagem_tempo(rng, d):
    mode = rng.choice(["tempo", "tempo", "velocidade", "distancia", "pace"])
    d.param(modo=mode)
    if mode == "tempo":
        speed = Fraction(rng.choice([40, 50, 60, 70, 80, 90, 100, 110]))
        minutes = rng.randint(4, 60) * 15
        dist = speed * minutes / 60
        if dist.denominator != 1:
            raise Reject("feio")
        d.given(distancia=dist, velocidade=speed)
        h = d.calc("horas", f"{lit(dist)} / {lit(speed)}", Fraction(minutes, 60))
        mins = d.calc("minutos", f"{lit(dist)} / {lit(speed)} * 60", minutes)
        steps = [f"tempo = distância ÷ velocidade = {N(dist)} ÷ {N(speed)} = {N(h)} h"]
        if minutes % 60:
            steps.append(f"{N(h)} h × 60 = {N(mins)} min = {duration(minutes)}")
        question = rng.choice([f"Quanto tempo levo para percorrer {N(dist)} km a uma média de {N(speed)} km/h?",
                               f"Vou rodar {N(dist)} km a {N(speed)} km/h de média. Quanto tempo de viagem?"])
        display = duration(minutes)
        final = f"Leva {display}, mantendo a média de {N(speed)} km/h."
        start = None
        if rng.random() < 0.4:
            start = rng.randint(5, 20) * 60 + rng.choice([0, 15, 30, 45])
            end = d.calc("chegada", f"({start} + {minutes}) % 1440", (start + minutes) % 1440)
            nxt = start + minutes >= 1440
            d.given(saida=[start // 60, start % 60])
            question = question[:-1] + f"? Saio às {clock(start)}; que horas chego?"
            steps.append(f"chegada: {clock(start)} + {duration(minutes)} = {clock(int(end))}" + (" do dia seguinte" if nxt else ""))
            final = f"A viagem leva {display}; você chega por volta das {clock(int(end))}" + (" do dia seguinte." if nxt else ".")
            d.result("tempo_chegada", [minutes, end], [display, clock(int(end))])
        else:
            d.result("tempo_min", minutes, display)
        return Out(question, steps, final, display)
    if mode == "velocidade":
        minutes = rng.randint(2, 40) * 15
        speed = Fraction(rng.randint(30, 120))
        dist = speed * minutes / 60
        if dist.denominator != 1:
            raise Reject("feio")
        h = Fraction(minutes, 60)
        d.given(distancia=dist, tempo=[minutes // 60, minutes % 60] if minutes % 60 else [minutes // 60])
        hh = d.calc("horas", f"{minutes} / 60", h)
        v = d.calc("velocidade", f"{lit(dist)} / ({minutes} / 60)", speed)
        question = rng.choice([f"Percorri {N(dist)} km em {duration(minutes)}. Qual foi a velocidade média?",
                               f"Fiz uma viagem de {N(dist)} km em {duration(minutes)}. A quantos km/h fui em média?"])
        steps = [f"{duration(minutes)} = {N(hh)} h", f"velocidade = {N(dist)} ÷ {N(hh)} = {N(v)} km/h"]
        d.result("velocidade", v, f"{N(v)} km/h")
        return Out(question, steps, f"Velocidade média de {N(v)} km/h.", f"{N(v)} km/h")
    if mode == "distancia":
        speed = Fraction(rng.randint(3, 120))
        minutes = rng.randint(1, 40) * 10
        dist = speed * minutes / 60
        d.given(velocidade=speed, tempo=[minutes // 60, minutes % 60] if minutes >= 60 else [minutes])
        hh = d.calc("horas", f"{minutes} / 60", Fraction(minutes, 60), places=4)
        dd = d.calc("distancia", f"{lit(speed)} * {minutes} / 60", dist, places=2)
        question = rng.choice([f"A {N(speed)} km/h, quantos km percorro em {duration(minutes)}?",
                               f"Mantendo {N(speed)} km/h por {duration(minutes)}, qual a distância percorrida?"])
        steps = [f"distância = velocidade × tempo = {N(speed)} × {minutes}/60 {eq_or_approx(dd)} {N(dd)} km"]
        d.result("distancia", dist, f"{N(dd)} km")
        return Out(question, steps, f"Você percorre {approx(dd)}{N(dd)} km.", f"{approx(dd)}{N(dd)} km")
    km = rng.choice([3, 5, 5, 10, 10, 15, 21, 42])
    secs = rng.randint(4 * 60, 8 * 60) * km
    secs -= secs % 5
    total_min, total_sec = divmod(secs, 60)
    pace = Fraction(secs, km)
    d.given(km=km, tempo=[total_min, total_sec] if total_sec else [total_min])
    tot = d.calc("segundos", f"{total_min} * 60 + {total_sec}", secs)
    p = d.calc("pace_segundos", f"({total_min} * 60 + {total_sec}) / {km}", pace, places=0)
    rounded_pace = int(quant(pace, 0))
    pm = d.calc("pace_min", f"{rounded_pace} // 60", rounded_pace // 60)
    ps = d.calc("pace_seg", f"{rounded_pace} % 60", rounded_pace % 60)
    t_txt = f"{total_min} min" + (f" {total_sec} s" if total_sec else "")
    question = rng.choice([f"Corri {km} km em {t_txt}. Qual foi meu pace (minutos por km)?",
                           f"Fiz {km} km em {t_txt}. Qual meu ritmo médio por quilômetro?"])
    pace_txt = f"{int(pm)} min {int(ps):02d} s por km"
    steps = [f"tempo em segundos: {total_min} × 60{' + ' + str(total_sec) if total_sec else ''} = {N(tot)} s",
             f"{N(tot)} ÷ {km} {eq_or_approx(p, 0)} {N(p, 0)} s por km", f"{N(p, 0)} s = {pace_txt}"]
    d.result("pace_segundos", pace, pace_txt)
    return Out(question, steps, f"Seu pace foi de {approx(pace, 0)}{pace_txt}.", pace_txt)


@kind("salario", "problemas/salarios", 1.2)
def k_salario(rng, d):
    mode = rng.choice(["valor_hora", "hora_extra", "freela", "reajuste", "orcamento"])
    d.param(modo=mode)
    if mode in ("valor_hora", "hora_extra"):
        hours = rng.choice([220, 200, 180, 176, 150])
        for _ in range(40):
            salary = Fraction(rng.randint(1500, 12000))
            if exact_at(salary / hours, 2):
                break
        else:
            raise Reject("sem-exemplo")
        d.given(salario=salary, horas_mes=hours)
        hv = d.calc("valor_hora", f"{lit(salary)} / {hours}", salary / hours)
        s = qm(rng, salary)
        if mode == "valor_hora":
            question = rng.choice([f"Ganho {s} por mês e trabalho {hours} horas. Quanto vale minha hora?",
                                   f"Com salário de {s} e jornada de {hours} horas mensais, qual o valor da hora?"])
            d.result("valor_hora", hv, M(hv))
            return Out(question, [f"{M(salary)} ÷ {hours} = {M(hv)}"], f"Sua hora vale {M(hv)}.", M(hv))
        add = Fraction(rng.choice([50, 50, 60, 70, 100]))
        extra_h = rng.randint(2, 40)
        hx = hv * (1 + add / 100)
        if not exact_at(hx, 2):
            raise Reject("centavos")
        d.given(adicional=add, horas_extras=extra_h)
        hxv = d.calc("hora_extra", f"{lit(salary)} / {hours} * (1 + {lit(add)} / 100)", hx)
        tot = d.calc("total_extras", f"{lit(salary)} / {hours} * (1 + {lit(add)} / 100) * {extra_h}", hx * extra_h)
        question = (f"Ganho {s} por mês, para {hours} horas de trabalho. Fiz {extra_h} horas extras com adicional de {P(add)}. "
                    "Quanto recebo pelas extras?")
        steps = [f"valor da hora: {M(salary)} ÷ {hours} = {M(hv)}", f"hora extra com {P(add)}: {M(hv)} × {N(1 + add / 100)} = {M(hxv)}",
                 f"{extra_h} × {M(hxv)} = {M(tot)}"]
        final = f"As {extra_h} horas extras rendem {M(tot)}, considerando o adicional de {P(add)} que você informou."
        d.result("extras", hx * extra_h, M(tot))
        return Out(question, steps, final, M(tot))
    if mode == "freela":
        rate = Fraction(rng.choice([25, 30, 35, 40, 45, 50, 60, 70, 80, 90, 100, 120, 150]))
        hrs = rng.randint(3, 160)
        hrs_day = rng.choice([None, 4, 6, 8])
        d.given(valor_hora=rate, horas=hrs)
        tot = d.calc("total", f"{lit(rate)} * {hrs}", rate * hrs)
        question = rng.choice([f"Cobro {qm(rng, rate)} por hora e o projeto vai levar {hrs} horas. Quanto devo cobrar no total?",
                               f"Trabalhei {hrs} horas como freelancer a {qm(rng, rate)} a hora. Quanto tenho a receber?"])
        steps = [f"{hrs} × {M(rate)} = {M(tot)}"]
        final = f"O total é {M(tot)}."
        if hrs_day and hrs > hrs_day:
            dd = d.calc("dias", f"-(-{hrs} // {hrs_day})", -(-hrs // hrs_day))
            d.given(horas_dia=hrs_day)
            question += f" E se eu trabalhar {hrs_day} horas por dia, em quantos dias termino?"
            steps.append(f"dias: {hrs} ÷ {hrs_day} = {N(Fraction(hrs, hrs_day), 2)}{'' if hrs % hrs_day == 0 else ', ou seja, ' + N(dd) + ' dias (o último incompleto)'}")
            d.calc("dias_fracao", f"{hrs} / {hrs_day}", Fraction(hrs, hrs_day), places=2)
            final += f" Trabalhando {hrs_day} horas por dia, você termina em {N(dd)} dias."
            d.result("total_dias", [rate * hrs, dd], [M(tot), f"{N(dd)} dias"])
        else:
            d.result("total", tot, M(tot))
        return Out(question, steps, final, M(tot))
    if mode == "reajuste":
        salary = price(rng, 1400, 15000)
        for _ in range(40):
            pct = rand_dec(rng, 2, 15, rng.choice([0, 1]))
            new = salary * (1 + pct / 100)
            if exact_at(new, 2):
                break
        else:
            raise Reject("centavos")
        d.given(salario=salary, reajuste=pct)
        inc = d.calc("aumento", f"{lit(salary)} * {lit(pct)} / 100", salary * pct / 100)
        nv = d.calc("novo_salario", f"{lit(salary)} * (1 + {lit(pct)} / 100)", new)
        question = rng.choice([f"Meu salário é {qm(rng, salary)} e vou ter um reajuste de {P(pct)}. Quanto passo a ganhar?",
                               f"Com reajuste de {P(pct)} sobre {qm(rng, salary)}, qual fica o novo salário?",
                               f"O dissídio deu {P(pct)}. Ganho {qm(rng, salary)}. Quanto vai para o meu salário?"])
        steps = [f"aumento: {P(pct)} de {M(salary)} = {M(inc)}", f"novo salário: {M(salary)} + {M(inc)} = {M(nv)}"]
        d.result("novo_salario", nv, M(nv))
        return Out(question, steps, f"O novo salário é {M(nv)}, {M(inc)} a mais por mês.", M(nv))
    salary = Fraction(rng.randint(15, 300) * 100)
    cats = rng.sample([("aluguel", 20, 40), ("mercado", 10, 25), ("transporte", 5, 15), ("lazer", 3, 10), ("contas da casa", 5, 12),
                       ("investimentos", 5, 20), ("educação", 3, 15)], rng.randint(2, 4))
    pcts = [Fraction(rng.randint(lo, hi)) for _, lo, hi in cats]
    if sum(pcts) >= 100:
        raise Reject("orcamento")
    d.given(salario=salary, percentuais=pcts)
    values, steps = [], []
    for (cat, _, _), p in zip(cats, pcts):
        v = d.calc("parcela", f"{lit(salary)} * {lit(p)} / 100", salary * p / 100)
        values.append(v)
        steps.append(f"{cat}: {P(p)} de {M(salary)} = {M(v)}")
    left = d.calc("sobra", f"{lit(salary)} * (100 - ({' + '.join(lit(p) for p in pcts)})) / 100", salary * (100 - sum(pcts)) / 100)
    steps.append(f"sobra: {M(salary)} - ({' + '.join(M(v) for v in values)}) = {M(left)}")
    desc = listing([f"{P(p)} com {cat}" for (cat, _, _), p in zip(cats, pcts)])
    question = rng.choice([f"Recebo {qm(rng, salary)} por mês. Gasto {desc}. Quanto sobra?",
                           f"Do meu salário de {qm(rng, salary)}, vão {desc}. Quanto fica livre?"])
    final = f"Sobram {M(left)} por mês, que são {P(100 - sum(pcts))} do salário."
    d.calc("sobra_percentual", f"100 - ({' + '.join(lit(p) for p in pcts)})", 100 - sum(pcts))
    d.result("sobra", left, M(left))
    return Out(question, steps, final, M(left))


@kind("economia_meta", "problemas/salarios", 0.8)
def k_economia_meta(rng, d):
    mode = rng.choice(["meses", "mensal"])
    d.param(modo=mode)
    goal = Fraction(rng.randint(5, 600) * 100)
    if mode == "meses":
        monthly = Fraction(rng.randint(5, 150) * 10)
        if monthly >= goal:
            raise Reject("trivial")
        months = math.ceil(goal / monthly)
        d.given(meta=goal, mensal=monthly)
        m = d.calc("meses", f"-(-{lit(goal)} // {lit(monthly)})", months)
        before = d.calc("antes", f"({months} - 1) * {lit(monthly)}", (months - 1) * monthly)
        at = d.calc("no_mes", f"{months} * {lit(monthly)}", months * monthly)
        question = rng.choice([f"Quero juntar {qm(rng, goal)} guardando {qm(rng, monthly)} por mês. Em quantos meses chego lá?",
                               f"Guardando {qm(rng, monthly)} por mês, quanto tempo levo para ter {qm(rng, goal)}?"])
        steps = [f"{M(goal)} ÷ {M(monthly)} = {N(goal / monthly, 2) if exact_at(goal / monthly, 2) else '≈ ' + N(goal / monthly, 2)}"]
        d.calc("razao", f"{lit(goal)} / {lit(monthly)}", goal / monthly, places=2)
        if months * monthly != goal:
            steps.append(f"em {months - 1} meses você teria {M(before)}; no {months}º mês chega a {M(at)}")
        final = f"Você atinge a meta em {N(m)} meses."
        d.result("meses", m, f"{N(m)} meses")
        return Out(question, steps, final, f"{N(m)} meses")
    months = rng.choice([3, 6, 8, 10, 12, 18, 24, 36])
    monthly = goal / months
    d.given(meta=goal, meses=months)
    v = d.calc("mensal", f"{lit(goal)} / {months}", monthly, places=2)
    question = rng.choice([f"Quanto preciso guardar por mês para juntar {qm(rng, goal)} em {months} meses?",
                           f"Quero {qm(rng, goal)} daqui a {months} meses. Quanto devo poupar por mês, sem contar rendimento?"])
    steps = [f"{M(goal)} ÷ {months} {eq_or_approx(v)} {M(v)}"]
    final = f"Guarde {approx(v)}{M(v)} por mês."
    if not exact_at(monthly, 2):
        up = Fraction(quant(monthly + Fraction(1, 200), 2)) if quant(monthly, 2) < monthly else Fraction(quant(monthly, 2))
        tot = d.calc("com_arredondamento", f"{lit(up)} * {months}", up * months)
        final = f"Guarde {M(up)} por mês (arredondando para cima); em {months} meses isso dá {M(tot)}."
        steps.append(f"arredondando para cima: {M(up)} × {months} = {M(tot)}")
        d.result("mensal", monthly, M(up))
        return Out(question, steps, final, M(up))
    d.result("mensal", monthly, M(v))
    return Out(question, steps, final, M(v))


@kind("receita_custo", "problemas/receitas", 0.8)
def k_receita_custo(rng, d):
    item, unit = rng.choice([("bolo", "fatias"), ("pudim", "fatias"), ("torta", "pedaços"), ("brigadeiro", "docinhos"),
                             ("coxinha", "unidades"), ("pão caseiro", "pães"), ("marmita", "marmitas"), ("cookie", "cookies")])
    cost = price(rng, 15, 180)
    yield_ = rng.choice([8, 10, 12, 16, 20, 24, 30, 40, 50, 60, 100])
    sell = price(rng, 1, 25)
    per = cost / yield_
    revenue = sell * yield_
    profit = revenue - cost
    d.given(custo=cost, rendimento=yield_, preco_venda=sell)
    u = d.calc("custo_unitario", f"{lit(cost)} / {yield_}", per, places=2)
    rv = d.calc("faturamento", f"{lit(sell)} * {yield_}", revenue)
    pf = d.calc("lucro", f"{lit(sell)} * {yield_} - {lit(cost)}", profit)
    question = rng.choice([
        f"Gastei {qm(rng, cost)} nos ingredientes de uma receita de {item} que rende {yield_} {unit}. Quanto custa cada? Se eu vender cada um a {qm(rng, sell)}, qual o lucro total?",
        f"Minha receita de {item} custa {qm(rng, cost)} e rende {yield_} {unit}. Vendendo a {qm(rng, sell)} cada, quanto lucro?",
    ])
    steps = [f"custo por unidade: {M(cost)} ÷ {yield_} {eq_or_approx(u)} {M(u)}", f"faturamento: {yield_} × {M(sell)} = {M(rv)}",
             f"lucro: {M(rv)} - {M(cost)} = {M(pf)}"]
    if profit >= 0:
        final = f"Cada unidade custa {approx(u)}{M(u)} e o lucro total é de {M(pf)} (sem contar gás, embalagem e seu tempo)."
    else:
        final = f"Cada unidade custa {approx(u)}{M(u)}; vendendo a {M(sell)} há prejuízo de {M(abs(pf))}."
    d.result("custo_lucro", [per, profit], [M(u), M(abs(pf))])
    return Out(question, steps, final, f"{M(u)} por unidade, resultado de {M(pf)}")


@kind("area_piso", "problemas/casa", 0.8)
def k_area_piso(rng, d):
    w = rand_dec(rng, 2, 8, 1)
    l = rand_dec(rng, 2, 10, 1)
    box = rng.choice([Fraction(2), Fraction(5, 2), Fraction(11, 5), Fraction(3), Fraction(15, 10), Fraction(18, 10)])
    waste = rng.choice([0, 0, 10, 15])
    area = w * l
    need = area * (1 + Fraction(waste, 100))
    boxes = math.ceil(need / box)
    d.given(largura=w, comprimento=l, caixa=box, **({"perda": waste} if waste else {}))
    a = d.calc("area", f"{lit(w)} * {lit(l)}", area)
    steps = [f"área: {N(w)} × {N(l)} = {N(a)} m²"]
    if waste:
        nd = d.calc("com_sobra", f"{lit(w)} * {lit(l)} * (1 + {waste} / 100)", need)
        steps.append(f"com {waste}% a mais para recortes: {N(a)} × {N(1 + Fraction(waste, 100))} = {N(nd, 4)} m²")
    else:
        nd = area
    bx = d.calc("caixas", f"-(-({lit(w)} * {lit(l)} * (1 + {waste} / 100)) // {lit(box)})", boxes)
    d.calc("razao_caixas", f"{lit(w)} * {lit(l)} * (1 + {waste} / 100) / {lit(box)}", need / box, places=2)
    steps.append(f"caixas: {N(nd, 4)} ÷ {N(box)} {eq_or_approx(need / box)} {N(need / box)}, arredondando para cima: {N(bx)}")
    question = rng.choice([f"Minha sala tem {N(w)} m por {N(l)} m. O piso vem em caixas de {N(box)} m². Quantas caixas compro?",
                           f"Vou trocar o piso de um quarto de {N(w)} m × {N(l)} m. Cada caixa cobre {N(box)} m². Quantas caixas preciso?"])
    if waste:
        question += f" Quero comprar {waste}% a mais por causa dos recortes."
    final = f"A área é de {N(a)} m² e você precisa de {N(bx)} caixas."
    d.result("area_caixas", [area, boxes], [f"{N(a)} m²", f"{N(bx)} caixas"])
    return Out(question, steps, final, f"{N(bx)} caixas ({N(a)} m²)")


# =========================================================================== montagem

def render_exchange(question: str, answer: str) -> str:
    return f"<|user|>\n{question}\n<|assistant|>\n{answer}\n"


def build_example(kind_: Kind, rng: random.Random, engine: Engine):
    draft = Draft(engine)
    out = kind_.func(rng, draft)
    if draft.final is None:
        raise Reject("sem-resultado")
    question, mode = wrap_question(rng, out.question, kind_.school)
    if mode == "terse":
        answer = terse(rng, out.short)
    elif mode == "terse-light":
        answer = out.final
    else:
        answer = compose(rng, out.steps, out.final, mode)
    displays = draft.final["display"] if isinstance(draft.final["display"], list) else [draft.final["display"]]
    if mode == "terse":
        displays = displays[:1]
    for shown in displays:
        if shown not in answer:
            raise Reject("final-ausente")
    in_question = numbers_in(question)
    flat = []
    for value in draft.inputs.values():
        flat.extend(value if isinstance(value, list) else [value])
    for value in flat:
        if Fraction(value) != 0 and abs(Fraction(value)) not in in_question:
            raise Reject("entrada-ausente")
    if DOC_SEP in question or DOC_SEP in answer:
        raise Reject("nul")
    verification = {
        "tool": "calculate",
        "engine": "bounded-python-ast/v1",
        "engine_source": "python/execution_engine.py",
        "contract": "contracts/calculate.json",
        "method": METHOD,
        "kind": kind_.name,
        "inputs": draft.inputs,
        "constants": draft.constants,
        "params": draft.params,
        "checks": draft.checks,
        "final": draft.final,
        "answer_mode": mode,
    }
    return question, answer, verification


def generate(count: int, seed: int, engine: Engine | None = None, cli_every: int = 500, log=None):
    engine = engine or Engine()
    master = random.Random(seed)
    weights = [k.weight for k in KINDS]
    examples, seen = [], set()
    rejects = Counter()
    attempts = 0
    while len(examples) < count:
        attempts += 1
        if attempts > count * 6 + 2000:
            raise RuntimeError(f"descartes demais: {dict(rejects)}")
        kind_ = master.choices(KINDS, weights)[0]
        rng = random.Random(master.getrandbits(64))
        try:
            question, answer, verification = build_example(kind_, rng, engine)
        except Reject as reason:
            rejects[f"{kind_.name}:{str(reason).split(':')[0]}"] += 1
            continue
        key = re.sub(r"\s+", " ", question.lower()).strip()
        if key in seen:
            rejects[f"{kind_.name}:duplicada"] += 1
            continue
        index = len(examples)
        if cli_every and index % cli_every == 0:
            check = verification["checks"][-1]
            try:
                via_cli = engine.calculate_cli(check["expression"], check["variables"])
            except Reject:
                rejects[f"{kind_.name}:cli"] += 1
                continue
            if via_cli != check["engine_result"]:
                rejects[f"{kind_.name}:cli-divergente"] += 1
                continue
            verification["cli_crosscheck"] = {
                "argv": ["python", "python/execution_engine.py", "--workspace", ".", "--tool", "calculate", "--arguments",
                         json.dumps({"expression": check["expression"], "variables": check["variables"]}, ensure_ascii=False)],
                "step": check["step"], "result": via_cli, "agree": True,
            }
        seen.add(key)
        verification["example_id"] = f"{NAME}-s{seed}-{index:06d}"
        examples.append({
            "messages": [{"role": "user", "content": question}, {"role": "assistant", "content": answer}],
            "domain": kind_.domain,
            "provenance": PROVENANCE,
            "verification": verification,
        })
        if log and len(examples) % 5000 == 0:
            log(f"[{NAME}] {len(examples)}/{count} exemplos")
    return examples, {"attempts": attempts, "rejects": dict(sorted(rejects.items())), "engine_calls": engine.calls,
                      "cli_calls": engine.cli_calls}


def pack_documents(examples, seed):
    """Agrupa 1 a 4 trocas do mesmo domínio por documento, em ordem determinística."""
    rng = random.Random(seed * 7919 + 17)
    by_domain = defaultdict(list)
    for example in examples:
        by_domain[example["domain"]].append(example)
    documents = []
    for domain in sorted(by_domain):
        items = by_domain[domain]
        i = 0
        while i < len(items):
            size = rng.choice([1, 1, 2, 2, 3, 4])
            chunk = items[i:i + size]
            documents.append("".join(render_exchange(e["messages"][0]["content"], e["messages"][1]["content"]) for e in chunk))
            i += size
    rng.shuffle(documents)
    return documents


def sentence_stats(examples):
    counts = Counter()
    for example in examples:
        text = example["messages"][1]["content"]
        lines = [re.sub(r"^(?:\d+[.)]|-)\s+", "", line) for line in text.split("\n")]
        pieces = [s.strip() for line in lines for s in re.split(r"(?<=[.!?])\s+", line)]
        for sentence in set(s for s in pieces if s):
            counts[sentence] += 1
    total = sum(counts.values())
    repeated = sum(c for c in counts.values() if c > 1)
    return {"distinct_sentences": len(counts), "sentence_occurrences": total,
            "occurrences_in_repeated_sentences": repeated,
            "most_repeated": [[s, c] for s, c in counts.most_common(15)]}


def write_outputs(out_dir: Path, examples, stats, seed, count):
    out_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path, txt_path = out_dir / f"{NAME}.jsonl", out_dir / f"{NAME}.txt"
    with jsonl_path.open("w", encoding="utf-8") as handle:
        for example in examples:
            handle.write(json.dumps(example, ensure_ascii=False, sort_keys=False) + "\n")
    documents = pack_documents(examples, seed)
    with txt_path.open("w", encoding="utf-8") as handle:
        for document in documents:
            handle.write(document + DOC_SEP)
    manifest = {
        "name": NAME, "provenance": PROVENANCE, "seed": seed, "count": count, "documents": len(documents),
        "generator": "pretrain/data_engine/arithmetic.py", "engine": "python/execution_engine.py (calculate)",
        "domains": dict(sorted(Counter(e["domain"] for e in examples).items())),
        "kinds": dict(sorted(Counter(e["verification"]["kind"] for e in examples).items())),
        "answer_modes": dict(sorted(Counter(e["verification"]["answer_mode"] for e in examples).items())),
        "checks": sum(len(e["verification"]["checks"]) for e in examples),
        **stats,
        "sentences": sentence_stats(examples),
        "files": {path.name: {"bytes": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                  for path in (jsonl_path, txt_path)},
    }
    (out_dir / f"{NAME}.manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--count", type=int, default=40000)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--cli-every", type=int, default=500,
                        help="reexecuta pela linha de comando do motor 1 a cada N exemplos (0 desliga)")
    args = parser.parse_args(argv)
    if args.count < 1:
        parser.error("--count precisa ser positivo")
    examples, stats = generate(args.count, args.seed, cli_every=args.cli_every, log=lambda m: print(m, file=sys.stderr))
    manifest = write_outputs(args.out, examples, stats, args.seed, args.count)
    print(json.dumps({k: manifest[k] for k in ("name", "count", "documents", "checks", "engine_calls", "cli_calls", "attempts")},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
