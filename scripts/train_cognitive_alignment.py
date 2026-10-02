"""Train a local aligned-copy candidate; reserved cases never enter the optimizer."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import time
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'python'))
import torch
from checkpoint_io import load_checkpoint, save_checkpoint
from copy_supervision import aligned_loss, encode_alignment_rows, trim_ignored_suffix
from model import build_model
from tokenizer import ByteBPETokenizer
from evaluate_cognitive_sft import read_cases
from select_cognitive_sft import validate_splits


def evaluate_loss(model, samples, batch_size, alignment_weight):
    model.eval()
    totals = {key: 0. for key in ['generation', 'alignment', 'gate', 'transition']}
    counts = {key: 0 for key in totals}
    with torch.inference_mode():
        for start in range(0, len(samples[0]), batch_size):
            x, y, pointers, continuation = trim_ignored_suffix(tuple(p[start:start + batch_size] for p in samples))
            logits, state = model(x, return_copy_state=True)
            _, details = aligned_loss(logits, state, y, pointers, continuation, alignment_weight)
            for key, value in details.items():
                count = int((pointers >= 0).sum()) if key == 'alignment' else int((y != -100).sum())
                totals[key] += float(value) * count
                counts[key] += count
    return {key: total / max(1, counts[key]) for key, total in totals.items()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, default=ROOT / 'datasets/cognitive_alignment_v4')
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--steps', type=int, default=1600)
    parser.add_argument('--eval-every', type=int, default=400)
    parser.add_argument('--batch-size', type=int, default=16)
    parser.add_argument('--threads', type=int, default=4)
    parser.add_argument('--learning-rate', type=float, default=.0015)
    parser.add_argument('--alignment-weight', type=float, default=.5)
    parser.add_argument('--checkpoint', type=Path, help='Continue saved weights with a fresh optimizer; steps are additional.')
    parser.add_argument('--schedule-steps', type=int, default=0, help='Optional cosine horizon for a deliberately shortened stage.')
    args = parser.parse_args()
    if min(args.steps, args.eval_every, args.batch_size, args.threads) < 1 or args.alignment_weight <= 0:
        parser.error('Training limits and alignment weight must be positive.')
    schedule_steps = args.schedule_steps or args.steps
    if schedule_steps < args.steps:
        parser.error('Schedule horizon cannot be shorter than executed steps.')
    torch.set_num_threads(args.threads)
    torch.manual_seed(42)
    config_path = args.data_dir / 'config.json'
    tokenizer_path = args.data_dir / 'tokenizer.json'
    config = json.loads(config_path.read_text())
    config['tokenizer_path'] = str(tokenizer_path.resolve())
    tokenizer = ByteBPETokenizer.load(tokenizer_path)
    train_path, val_path, heldout_path = [args.data_dir / (split + '.jsonl') for split in ['train', 'validation', 'heldout']]
    train, validation = read_cases(train_path), read_cases(val_path)
    # Read only split identities for overlap checks; reserved labels never encode here.
    validate_splits(train, validation, read_cases(heldout_path))
    train_samples = encode_alignment_rows(train, tokenizer, config['context_length'])
    val_samples = encode_alignment_rows(validation, tokenizer, config['context_length'])
    output = args.output_dir
    output.mkdir(parents=True, exist_ok=False)
    for split, rows in [('train', train), ('validation', validation)]:
        (output / (split + '.jsonl')).write_text(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in rows))
    model = build_model(config)
    source_steps = 0
    if args.checkpoint:
        source = load_checkpoint(args.checkpoint)
        for key in ['vocab_size', 'context_length', 'layers', 'hidden_size', 'attention_heads',
                    'copy_attention_size', 'copy_boundary_ids', 'copy_transition']:
            if source['config'].get(key) != config.get(key):
                raise ValueError('Resume architecture differs: ' + key)
        if source.get('tokenizer_sha256') != hashlib.sha256(tokenizer_path.read_bytes()).hexdigest():
            raise ValueError('Resume tokenizer differs from training.')
        model.load_state_dict(source['state_dict'])
        source_steps = int(source.get('steps') or 0)
    manifest = {'schema': 'cognitive-alignment-training/v1', 'seed': 42, 'config': config,
                'arguments': {key: str(value) if isinstance(value, Path) else value for key, value in vars(args).items()},
                'records': {'train': len(train), 'validation': len(validation)},
                'inputs': {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in [train_path, val_path, heldout_path, config_path]},
                'tokenizer_sha256': hashlib.sha256(tokenizer_path.read_bytes()).hexdigest(),
                'parameters': sum(p.numel() for p in model.parameters()),
                'resume': None if not args.checkpoint else {'checkpoint': str(args.checkpoint),
                    'checkpoint_sha256': hashlib.sha256(args.checkpoint.read_bytes()).hexdigest(),
                    'metadata_sha256': hashlib.sha256(Path(str(args.checkpoint)+'.json').read_bytes()).hexdigest(),
                    'source_steps': source_steps, 'optimizer_restarted': True},
                'source_sha256': {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in
                                  [Path(__file__), ROOT / 'python/copy_supervision.py', ROOT / 'python/model.py']},
                'limits': ['From scratch on authored synthetic tasks; no external generator.',
                           'Gold source positions are training-only; inference has prompt tokens only.',
                           'Reserved data does not choose training steps or checkpoints.',
                           'Trailing ignored padding is removed; full evidence and responses remain intact.']}
    (output / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.learning_rate, weight_decay=.01)
    started = time.monotonic()
    initial = evaluate_loss(model, val_samples, args.batch_size, args.alignment_weight)
    history = [{'step': source_steps, 'validation': initial, 'elapsed_seconds': round(time.monotonic() - started, 2)}]
    print(json.dumps({'parameters': manifest['parameters'], 'train': len(train), 'validation': len(validation), **history[0]}), flush=True)
    for step in range(1, args.steps + 1):
        model.train()
        factor = min(1., step / min(20, args.steps)) * (.2 + .8 * .5 * (1 + math.cos(math.pi * step / schedule_steps)))
        for group in optimizer.param_groups:
            group['lr'] = args.learning_rate * factor
        indices = torch.randint(len(train), (args.batch_size,))
        x, y, pointers, continuation = trim_ignored_suffix(tuple(p[indices] for p in train_samples))
        logits, state = model(x, return_copy_state=True)
        loss, details = aligned_loss(logits, state, y, pointers, continuation, args.alignment_weight)
        if not torch.isfinite(loss):
            raise RuntimeError('Non-finite training loss; no checkpoint promotion.')
        optimizer.zero_grad(set_to_none=True)
        loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), 1.)
        optimizer.step()
        if step % args.eval_every == 0 or step == args.steps:
            val = evaluate_loss(model, val_samples, args.batch_size, args.alignment_weight)
            snapshots = output / 'snapshots'
            snapshots.mkdir(exist_ok=True)
            save_checkpoint({'config': config, 'state_dict': model.state_dict(), 'steps': source_steps + step,
                             'validation_loss': val['generation'], 'validation_alignment': val,
                             'status': 'experimental-validation-snapshot',
                             'tokenizer_sha256': manifest['tokenizer_sha256']}, snapshots / f'step-{source_steps + step:04d}.safetensors')
            row = {'step': source_steps + step, 'train': {k: float(v) for k, v in details.items()}, 'validation': val,
                   'elapsed_seconds': round(time.monotonic() - started, 2)}
            history.append(row)
            (output / 'history.json').write_text(json.dumps(history, indent=2) + '\n')
            print(json.dumps(row), flush=True)
    report = {'completed_steps': args.steps, 'source_steps': source_steps,
              'total_steps': source_steps + args.steps, 'optimizer_restarted': bool(args.checkpoint),
              'training_seconds': round(time.monotonic() - started, 2),
              'promoted': False, 'semantic_evaluation_required': True, 'history': history}
    (output / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({k: v for k, v in report.items() if k != 'history'}), flush=True)


if __name__ == '__main__':
    main()
