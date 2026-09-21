"""Promove checkpoints somente quando todos os gates básicos estão satisfeitos.

Uso seguro: sem --promote apenas audita; com --promote copia para o destino
somente depois de validar contexto, pesos e evidência comportamental vinculada
por hash. Nunca sobrescreve o destino nem troca o formato de serialização.
"""

import argparse
import json
import shutil
from pathlib import Path

from checkpoint_io import load_checkpoint
from context_policy import MIN_CONTEXT_TOKENS, inspect_context
from release_evidence import evidence_gates


def audit(source: Path, evaluation=None, tokenizer=Path('model/tokenizer.json')) -> dict:
    checkpoint = load_checkpoint(source)
    policy = inspect_context(checkpoint.get("config"))
    gates = {
        "checkpoint_readable": True,
        "context_minimum": policy["effective_tokens"] >= MIN_CONTEXT_TOKENS,
        "generation_professional": policy["generation_policy"]["production_eligible"],
        "has_state_dict": bool(checkpoint.get("state_dict")),
        "has_training_steps": int(checkpoint.get("steps") or 0) > 0,
        "not_partial": checkpoint.get("status") != "partial",
    }
    weights = checkpoint.get('state_dict', {})
    config = checkpoint.get('config', {})
    positional = weights.get('position_embedding.weight')
    embedding = weights.get('token_embedding.weight')
    gates['context_weights_match'] = (positional is not None and embedding is not None
                                      and tuple(positional.shape) == (config.get('context_length'), config.get('hidden_size'))
                                      and tuple(embedding.shape) == (config.get('vocab_size'), config.get('hidden_size')))
    observed, evaluation_path = evidence_gates(source, checkpoint, tokenizer, evaluation)
    gates.update(observed)
    return {
        "schema": "checkpoint-promotion-audit/v1",
        "source": str(source),
        "evaluation_path": evaluation_path,
        "context_policy": policy,
        "gates": gates,
        "promotable": all(gates.values()),
        "blocked_reasons": [name for name, passed in gates.items() if not passed],
    }


def copy_bundle(source, destination, evaluation):
    """Copia pesos, metadados e evidência juntos, sem sobrescrever arquivos."""
    if source.suffix != destination.suffix:
        raise ValueError('o destino deve preservar a extensão do checkpoint')
    pairs = [(source, destination), (Path(evaluation), Path(str(destination) + '.evaluation.json'))]
    if source.suffix == '.safetensors':
        pairs.append((Path(str(source) + '.json'), Path(str(destination) + '.json')))
    if any(target.exists() for _, target in pairs):
        raise FileExistsError('destino ou arquivo auxiliar já existe')
    destination.parent.mkdir(parents=True, exist_ok=True)
    created = []
    try:
        for original, target in pairs:
            with original.open('rb') as reader, target.open('xb') as writer:
                created.append(target)
                shutil.copyfileobj(reader, writer)
    except Exception:
        for target in created:
            target.unlink(missing_ok=True)
        raise
    return created


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source")
    parser.add_argument("--destination")
    parser.add_argument('--evaluation', help='relatório comportamental vinculado por hash ao candidato')
    parser.add_argument('--tokenizer', default='model/tokenizer.json')
    parser.add_argument("--promote", action="store_true")
    args = parser.parse_args()
    source = Path(args.source)
    destination = Path(args.destination) if args.destination else Path('model/checkpoints') / ('production' + source.suffix)
    report = audit(source, args.evaluation, Path(args.tokenizer))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["promotable"]:
        raise SystemExit("promoção bloqueada: gate obrigatório falhou")
    if args.promote:
        created = copy_bundle(source, destination, report['evaluation_path'])
        try:
            copied = audit(destination, tokenizer=Path(args.tokenizer))
            if not copied['promotable']:
                raise ValueError('artefatos mudaram durante a cópia ou evidência não corresponde ao destino')
        except Exception:
            for path in created:
                path.unlink(missing_ok=True)
            raise
        print(f"checkpoint promovido com segurança: {destination}")


if __name__ == "__main__":
    main()
