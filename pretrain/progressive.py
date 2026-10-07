"""Pré-treino com crescimento progressivo de pesos, pensado para CPU.

Treina uma semente pequena (passos baratos), aumenta os pesos com
``pretrain/grow.py`` preservando a função e continua o treino no modelo maior.
Cada etapa grava em ``<out>/<etapa>/`` e é retomável: rodar de novo pula etapas
concluídas e continua a etapa em andamento a partir do ``last.pt``.

O plano padrão (``--plan cpu-3stage``) vai de ~4M para ~30M parâmetros de
blocos + embeddings de vocabulário 8k. As etapas intermediárias usam taxa de
aprendizado constante (sem decaimento) e só a última decai, como na agenda WSD.

Exemplo:
    .venv/bin/python pretrain/progressive.py --data pretrain_data --out pretrain_out/progressivo
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

PLANS = {
    "cpu-3stage": [
        {"name": "etapa1-semente", "preset": "cpu-seed", "tokens": 24e6, "decay_frac": 0.0},
        {"name": "etapa2-12x384", "grow": {"layers": 12, "hidden_size": 384}, "tokens": 40e6, "decay_frac": 0.0},
        {"name": "etapa3-12x512", "grow": {"layers": 12, "hidden_size": 512}, "tokens": 64e6, "decay_frac": 0.25},
    ],
    # Plano de fumaça: valida o encadeamento em minutos.
    "smoke": [
        {"name": "s1", "preset": "tiny", "tokens": 2e5, "decay_frac": 0.0},
        {"name": "s2", "grow": {"layers": 3, "hidden_size": 192}, "tokens": 2e5, "decay_frac": 0.5},
    ],
}


def run(command):
    print("$ " + " ".join(str(part) for part in command), flush=True)
    subprocess.run([str(part) for part in command], check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data", default="pretrain_data")
    parser.add_argument("--out", default="pretrain_out/progressivo")
    parser.add_argument("--plan", choices=sorted(PLANS), default="cpu-3stage")
    parser.add_argument("--plan-json", help="arquivo JSON com a lista de etapas (substitui --plan)")
    parser.add_argument("--token-scale", type=float, default=1.0, help="multiplica o orçamento de tokens de todas as etapas")
    parser.add_argument("--batch-tokens", type=int, default=32768)
    parser.add_argument("--micro-batch", type=int, default=16)
    parser.add_argument("--optimizer", choices=("muon", "adamw"), default="muon")
    parser.add_argument("--lr", type=float, default=0)
    parser.add_argument("--max-hours", type=float, default=0, help="limite por chamada do treino de cada etapa")
    parser.add_argument("--no-compile", action="store_true")
    parser.add_argument("--no-cpu-bf16", action="store_true")
    parser.add_argument("--device", default=None)
    args = parser.parse_args()

    stages = json.loads(Path(args.plan_json).read_text()) if args.plan_json else PLANS[args.plan]
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    previous = None
    for index, stage in enumerate(stages):
        stage_dir = out / stage["name"]
        stage_dir.mkdir(parents=True, exist_ok=True)
        final = stage_dir / "final.safetensors"
        done_marker = stage_dir / "COMPLETE.json"
        if done_marker.exists() and final.exists():
            print(f"[progressivo] {stage['name']}: concluída, pulando", flush=True)
            previous = final
            continue
        command = [sys.executable, HERE / "train.py", "--data", stage.get("data", args.data), "--out", stage_dir,
                   "--tokens", f"{stage['tokens'] * args.token_scale:.0f}",
                   "--batch-tokens", args.batch_tokens, "--micro-batch", args.micro_batch,
                   "--optimizer", args.optimizer, "--decay-frac", stage.get("decay_frac", 0.2),
                   "--warmup-steps", stage.get("warmup_steps", 100 if index == 0 else 30),
                   "--eval-every", stage.get("eval_every", 100), "--eval-batches", 10, "--save-every", 50]
        if args.lr:
            command += ["--lr", args.lr]
        if args.max_hours:
            command += ["--max-hours", args.max_hours]
        if not args.no_compile:
            command.append("--compile")
        if not args.no_cpu_bf16:
            command.append("--cpu-bf16")
        if args.device:
            command += ["--device", args.device]
        if "grow" in stage:
            if previous is None:
                raise SystemExit(f"{stage['name']} cresce a partir da etapa anterior, que não terminou")
            grown = stage_dir / "init.safetensors"
            if not grown.exists():
                grow_args = []
                for key, value in stage["grow"].items():
                    grow_args += [f"--{key.replace('_', '-')}", value]
                run([sys.executable, HERE / "grow.py", previous, grown, *grow_args])
            command += ["--init", grown]
        else:
            command += ["--preset", stage["preset"]]
        run(command)
        if not done_marker.exists():
            print(f"[progressivo] {stage['name']}: parou antes do fim (limite de tempo); rode de novo para continuar")
            return
        previous = final
    print(f"[progressivo] concluído; pesos finais em {previous}")


if __name__ == "__main__":
    main()
