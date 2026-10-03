"""Converte o MBPP (problemas Python escritos por pessoas, CC-BY 4.0) em conversas de SFT
no contrato de plano JSON que o produto e a bancada de programação exigem.

Cada exemplo vira: pedido no mesmo invólucro da bancada -> resposta
``{"assumptions": [], "operations": [create_file app.py, create_file test_app.py]}``.
Só entram exemplos que passam no parser de contrato do produto e cujos testes
rodam verdes no mesmo corretor isolado da bancada. Funções com o nome de uma das
tarefas da bancada são excluídas, para o treino continuar disjunto dela.
"""

import argparse
import ast
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "python"))
from programming_qualification import request_messages, run_unittest  # noqa: E402
from proactive_implementation import parse_implementation_plan  # noqa: E402

QUALIFICATION = ROOT / "datasets/programming_qualification_v1"


def qualification_functions():
    names = set()
    for suite in ("reserved", "regression"):
        for line in (QUALIFICATION / f"{suite}.oracle.jsonl").read_text(encoding="utf-8").splitlines():
            if line:
                names.add(json.loads(line)["function"])
    return names


def tested_function(tests, defined):
    """Primeira função do próprio código chamada no primeiro assert."""
    for node in ast.walk(ast.parse(tests[0])):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in defined:
            return node.func.id
    return None


def convert(row):
    """Uma linha do MBPP -> (função, pedido, plano JSON) ou None se não der para converter."""
    try:
        module = ast.parse(row["code"].replace("\r\n", "\n"))
        defined = {node.name for node in module.body if isinstance(node, ast.FunctionDef)}
        function = tested_function(row["test_list"], defined)
    except SyntaxError:
        return None
    definition = next((node for node in module.body
                       if isinstance(node, ast.FunctionDef) and node.name == function), None)
    if definition is None:
        return None
    code = ast.unparse(module) + "\n"
    signature = f"{function}({ast.unparse(definition.args)})"
    request = (f"Crie uma função Python {signature}. Tarefa: {row['text'].strip()} "
               "A função fica em app.py e os testes em test_app.py.")
    setup = row.get("test_setup_code") or ""
    body = "\n".join(f"        {line}" for line in (setup.splitlines() + row["test_list"]) if line.strip())
    tests = (f"import unittest\n\nfrom app import *\n\n\nclass AppTests(unittest.TestCase):\n"
             f"    def test_exemplos(self):\n{body}\n\n\nif __name__ == \"__main__\":\n    unittest.main()\n")
    plan = json.dumps({"assumptions": [], "operations": [
        {"tool": "create_file", "arguments": {"path": "app.py", "content": code}},
        {"tool": "create_file", "arguments": {"path": "test_app.py", "content": tests}}]}, ensure_ascii=False)
    return function, request, plan


def passes(plan):
    try:
        parsed = parse_implementation_plan(plan, existing_paths=set(), existing_directories=set())
    except (ValueError, TypeError):
        return False
    with tempfile.TemporaryDirectory(prefix="mbpp-sft-") as scratch:
        for operation in parsed["operations"]:
            target = Path(scratch) / operation["arguments"]["path"]
            target.write_text(operation["arguments"]["content"], encoding="utf-8")
        return run_unittest(Path(scratch), "test*.py")["passed"]


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", default="pretrain_data/sft_code_plans.jsonl")
    args = parser.parse_args()
    from datasets import load_dataset

    excluded = qualification_functions()
    counts = {"convertidos": 0, "fora_do_formato": 0, "nome_da_bancada": 0, "testes_falharam": 0}
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as handle:
        for split in ("train", "validation", "test", "prompt"):
            for row in load_dataset("google-research-datasets/mbpp", "full", split=split):
                converted = convert(row)
                if converted is None:
                    counts["fora_do_formato"] += 1
                    continue
                function, request, plan = converted
                if function in excluded:
                    counts["nome_da_bancada"] += 1
                    continue
                if not passes(plan):
                    counts["testes_falharam"] += 1
                    continue
                messages = request_messages(request) + [{"role": "assistant", "content": plan}]
                handle.write(json.dumps({"source": f"mbpp-{row['task_id']}", "license": "CC-BY-4.0",
                                         "messages": messages}, ensure_ascii=False) + "\n")
                counts["convertidos"] += 1
    print(f"[mbpp] {counts} -> {out}")


if __name__ == "__main__":
    main()
