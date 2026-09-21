"""Gera dados de supervisão usando um modelo Ollama já instalado.

Este script não faz fine-tuning dos pesos do Ollama. Ele usa o modelo escolhido
como professor, gera decisões estruturadas e treina o reranker local do projeto.
Assim o processo é barato, auditável e pode ser repetido com outro professor.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = ROOT / "corpus" / "training" / "teacher_plans.jsonl"
DEFAULT_TASKS = ROOT / "corpus" / "training" / "teaching_tasks.txt"
OLLAMA_URL = "http://127.0.0.1:11434/api/chat"


def installed_models() -> list[str]:
    try:
        output = subprocess.check_output(["ollama", "list"], text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return []
    models = []
    for line in output.splitlines()[1:]:
        value = line.split()[0] if line.split() else ""
        if value:
            models.append(value)
    return models


def choose_model(requested: str | None) -> str:
    models = installed_models()
    if requested:
        if models and requested not in models:
            raise SystemExit(f"Modelo não encontrado no Ollama: {requested}\nDisponíveis: {', '.join(models)}")
        return requested
    preferred = ["qwen3:30b", "qwen3:14b", "qwen3-coder:30b"]
    choices = [name for name in preferred if name in models] or models
    if not choices:
        raise SystemExit("Nenhum modelo foi encontrado. Execute: ollama list")
    if len(choices) == 1 or not sys.stdin.isatty():
        return choices[0]
    print("Modelos disponíveis:")
    for index, name in enumerate(choices, 1):
        print(f"  {index}. {name}")
    answer = input(f"Escolha [1-{len(choices)}] (padrão 1): ").strip() or "1"
    try:
        return choices[int(answer) - 1]
    except (ValueError, IndexError):
        raise SystemExit("Escolha inválida")


def load_tasks(path: Path, limit: int) -> list[str]:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "\n".join([
                "Analise meu projeto e explique a arquitetura antes de modificar qualquer arquivo.",
                "Liste os arquivos do workspace e destaque os mais relevantes para começar.",
                "Leia o arquivo index.html e proponha uma melhoria visual sem substituir o original.",
                "Busque no código onde a tela de login é montada.",
                "Crie uma página de login moderna em um projeto vazio e mostre a prévia.",
                "Transforme a tela de login existente em uma pequena vitrine de produtos.",
                "Revise a alteração e execute os testes disponíveis no projeto.",
                "Explique por que uma alteração não funcionou e indique a próxima investigação.",
            ]) + "\n", encoding="utf-8")
    tasks = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip() and not line.lstrip().startswith("#")]
    return tasks[:limit]


def catalog() -> list[dict]:
    result = []
    for path in sorted((ROOT / "contracts").glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        result.append({
            "name": data.get("name"),
            "description": data.get("description", ""),
            "arguments": data.get("arguments", {}),
            "requires_approval": bool(data.get("requires_approval")),
        })
    return result


def ask(model: str, task: str, context_size: int, timeout: float) -> tuple[dict | None, str | None]:
    system = (
        "Você é um professor de agentes de programação. Gere uma decisão de planejamento "
        "para uma IA local em português do Brasil. Escolha no máximo uma próxima ferramenta "
        "do catálogo. Para alterar um arquivo existente, leia ou pesquise antes. Não alegue "
        "que algo foi executado. Retorne somente JSON válido no formato "
        '{"text":"plano curto","tool_call":{"name":"ferramenta","arguments":{}},'
        '"quality":"good"} ou tool_call null. Nunca invente ferramenta.\nCATÁLOGO:\n'
        + json.dumps(catalog(), ensure_ascii=False)
    )
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": task},
        ],
        "stream": False,
        "format": "json",
        "options": {"temperature": 0.1, "num_ctx": context_size},
    }
    request = urllib.request.Request(
        OLLAMA_URL,
        data=json.dumps(payload, ensure_ascii=False).encode(),
        headers={"content-type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            content = json.loads(response.read().decode("utf-8"))
        raw = ((content.get("message") or {}).get("content") or "").strip()
        # Alguns modelos colocam o JSON em markdown ou precedem a resposta
        # com um bloco de raciocínio. Mantemos somente o objeto final.
        if "```" in raw:
            blocks = [block.strip() for block in raw.split("```") if block.strip()]
            raw = next((block[4:].strip() if block.startswith("json") else block for block in blocks if "{" in block), raw)
        start, end = raw.find("{"), raw.rfind("}")
        if start >= 0 and end > start:
            raw = raw[start:end + 1]
        result = json.loads(raw)
        call = result.get("tool_call")
        if call is not None and (not isinstance(call, dict) or not call.get("name") or not isinstance(call.get("arguments", {}), dict)):
            return None, "schema tool_call inválido"
        result["teacher_model"] = model
        result["task"] = task
        return result, None
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")[:500]
        return None, f"HTTP {error.code}: {detail}"
    except (OSError, urllib.error.URLError) as error:
        return None, f"conexão Ollama: {error}"
    except (ValueError, TypeError, json.JSONDecodeError) as error:
        return None, f"JSON inválido: {error}; resposta: {locals().get('raw', '')[:300]}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Gera exemplos de ensino para o agente local usando Ollama")
    parser.add_argument("--model", help="Modelo Ollama; sem isso o script oferece os modelos instalados")
    parser.add_argument("--tasks", default=str(DEFAULT_TASKS), help="Arquivo txt com uma tarefa por linha")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="JSONL de exemplos gerados")
    parser.add_argument("--max-examples", type=int, default=8)
    parser.add_argument("--num-ctx", type=int, default=8192)
    parser.add_argument("--timeout", type=float, default=180)
    args = parser.parse_args()
    model = choose_model(args.model)
    tasks = load_tasks(Path(args.tasks), max(1, args.max_examples))
    destination = Path(args.output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    generated = 0
    failed = 0
    with destination.open("w", encoding="utf-8") as stream:
        for index, task in enumerate(tasks, 1):
            print(f"[{index}/{len(tasks)}] {task[:100]}", flush=True)
            result, error = ask(model, task, args.num_ctx, args.timeout)
            if not result:
                failed += 1
                print(f"  falhou: {error or 'resposta vazia'}", flush=True)
                continue
            call = result.get("tool_call")
            row = {
                "messages": [
                    {"role": "system", "content": "Selecione uma ferramenta do catálogo e devolva uma chamada JSON válida."},
                    {"role": "user", "content": task},
                    {"role": "assistant", "tool_call": call} if call else {"role": "assistant", "content": result.get("text", "")},
                ],
                "metadata": {"source": "ollama-teacher", "teacher_model": model, "quality": result.get("quality", "unreviewed")},
            }
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
            generated += 1
    print(json.dumps({"model": model, "generated": generated, "failed": failed, "output": str(destination)}, ensure_ascii=False, indent=2))
    print("Próximo passo: .venv/bin/python python/train_planner.py --input " + str(destination))
    return 0 if generated else 1


if __name__ == "__main__":
    raise SystemExit(main())
