"""Restore the trained positional prefix after extending a local checkpoint.

The original context extension interpolated the 512 trained positions across
the entire 32k table. Short prompts then used only an almost constant slice of
that interpolation. This repair keeps the extended tail but restores the exact
trained prefix. It writes a separate candidate and never changes the active
checkpoint.
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

import torch

from checkpoint_io import load_checkpoint, save_checkpoint


def repair_trained_prefix(extended: dict, source: dict) -> dict:
    extended_config = dict(extended['config'])
    source_config = source['config']
    trained = int(extended_config.get('training_context_length') or 0)
    if trained < 1 or trained > int(source_config['context_length']):
        raise ValueError('Faixa treinada ausente ou incompatível com a origem.')
    restored = dict(extended['state_dict'])
    original = source['state_dict']
    target_positions = restored['position_embedding.weight']
    source_positions = original['position_embedding.weight']
    if (target_positions.ndim != 2 or source_positions.ndim != 2
            or target_positions.shape[1] != source_positions.shape[1]
            or target_positions.shape[0] < trained or source_positions.shape[0] < trained):
        raise ValueError('Tabelas de posição incompatíveis.')
    for name, weights in restored.items():
        if name != 'position_embedding.weight':
            if name not in original or original[name].shape != weights.shape:
                raise ValueError(f'Peso de origem incompatível: {name}')
            if not torch.equal(weights, original[name]):
                raise ValueError(f'O candidato não deriva da origem declarada: {name}')
    repaired_positions = target_positions.clone()
    repaired_positions[:trained] = source_positions[:trained]
    restored['position_embedding.weight'] = repaired_positions
    extension = dict(extended_config.get('context_extension') or {})
    extension.update({
        'method': 'trained-prefix-preserved-v1',
        'source_trained_context_tokens': trained,
        'prefix_exactly_restored': True,
        'fine_tuned_at_target': False,
        'claim': 'prefixo treinado preservado; contexto longo ainda não tem validação semântica',
    })
    extended_config['context_extension'] = extension
    return {
        'config': extended_config,
        'state_dict': restored,
        'source': 'local-position-repair',
        'status': 'experimental',
    }


def native_training_checkpoint(extended: dict, source: dict) -> dict:
    """Keep only the positions with trained semantics for a new local SFT run."""
    repaired = repair_trained_prefix(extended, source)
    trained = int(repaired['config']['training_context_length'])
    repaired['state_dict']['position_embedding.weight'] = (
        repaired['state_dict']['position_embedding.weight'][:trained].clone()
    )
    repaired['config']['context_length'] = trained
    repaired['config']['runtime_context_tokens'] = trained
    repaired['config'].pop('context_extension', None)
    repaired['source'] = 'local-native-context-training'
    return repaired


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--extended', type=Path, default=Path('model/godmode/context-32768-v1/candidate.safetensors'))
    parser.add_argument('--source', type=Path, default=Path('model/godmode/training-neural-v1/candidate.safetensors'))
    parser.add_argument('--output', type=Path, default=Path('model/training/position-repair-v1/candidate.safetensors'))
    parser.add_argument('--native', action='store_true', help='salva somente o prefixo treinado para ajuste eficiente')
    args = parser.parse_args()
    if args.output.exists():
        parser.error(f'Candidato já existe: {args.output}')
    operation = native_training_checkpoint if args.native else repair_trained_prefix
    candidate = operation(load_checkpoint(args.extended), load_checkpoint(args.source))
    candidate['extended_sha256'] = hashlib.sha256(args.extended.read_bytes()).hexdigest()
    candidate['source_sha256'] = hashlib.sha256(args.source.read_bytes()).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    save_checkpoint(candidate, args.output)
    print(args.output)


if __name__ == '__main__':
    main()
