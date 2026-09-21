"""Continuação supervisionada local com splits por pergunta e loss da resposta.

Não altera o checkpoint ativo. O conjunto heldout é avaliado só após a seleção
por validação. Checkpoints curtos continuam experimentais.
"""
import argparse
import hashlib
import json
import math
import random
import time
from collections import Counter
from pathlib import Path

import torch
import torch.nn.functional as F

from assistant_profile import COMPACT_PROFILE
from checkpoint_io import load_checkpoint, save_checkpoint
from dialogue import normalize
from model import build_model
from tokenizer import ByteBPETokenizer


def question_key(row):
    return ' '.join(normalize(row['messages'][0]['content']).split()).rstrip('.?! ')


def read_rows(path):
    return [json.loads(line) for line in Path(path).read_text(encoding='utf-8').splitlines() if line.strip()]


def clean_rows(paths, excluded):
    unique = {}
    rejected = Counter()
    for path in paths:
        for row in read_rows(path):
            messages = row.get('messages', [])
            if (len(messages) != 2 or [m.get('role') for m in messages] != ['user', 'assistant']
                    or any('tool_call' in m or not isinstance(m.get('content'), str) or not m['content'].strip() for m in messages)):
                rejected['not_plain_pair'] += 1
                continue
            key = question_key(row)
            if key in excluded:
                rejected['evaluation_question'] += 1
                continue
            if key in unique:
                rejected['duplicate_question'] += 1
            unique[key] = {**row, 'source': str(path)}
    return list(unique.values()), dict(rejected)


def split_rows(rows):
    train, validation = [], []
    for row in rows:
        bucket = int(hashlib.sha256(question_key(row).encode()).hexdigest()[:8], 16) % 10
        (validation if bucket == 0 else train).append(row)
    if not train or not validation:
        raise ValueError('Treino e validação precisam conter exemplos distintos.')
    return train, validation


def encode_rows(rows, tokenizer, context, with_profile=False):
    samples = []
    for row in rows:
        question, answer = [m['content'] for m in row['messages']]
        prefix = f'<|user|>\n{question}\n<|assistant|>\n'
        variants = [prefix]
        if with_profile:
            variants.append(f'<|system|>\n{COMPACT_PROFILE}\n' + prefix)
        for prompt in variants:
            prompt_ids = tokenizer.encode(prompt)
            answer_ids = tokenizer.encode(answer + '\n', add_eos=True)
            ids = prompt_ids + answer_ids
            labels = [-100] * len(prompt_ids) + answer_ids
            # Janelas da mesma conversa permanecem no mesmo split. Nenhum
            # token de prompt ou padding contribui para o objetivo.
            for start in range(0, len(ids) - 1, context):
                x = ids[start:start + context]
                y = labels[start + 1:start + context + 1]
                x = x[:len(y)]
                if not any(value != -100 for value in y):
                    continue
                pad = context - len(x)
                samples.append((x + [tokenizer.special_tokens['<pad>']] * pad, y + [-100] * pad))
    if not samples:
        raise ValueError('Nenhum token de resposta disponível.')
    return torch.tensor([x for x, _ in samples]), torch.tensor([y for _, y in samples])


def evaluate_loss(model, samples, batch_size):
    model.eval()
    total, count = 0.0, 0
    with torch.inference_mode():
        for start in range(0, len(samples[0]), batch_size):
            x, y = (part[start:start + batch_size] for part in samples)
            logits = model(x)
            total += F.cross_entropy(logits.reshape(-1, logits.shape[-1]), y.reshape(-1), ignore_index=-100, reduction='sum').item()
            count += (y != -100).sum().item()
    return total / count


def sample_answers(model, tokenizer, config, rows, limit=128):
    model.eval()
    answers = []
    with torch.inference_mode():
        for row in rows:
            prompt = row['messages'][0]['content']
            ids = tokenizer.encode(f'<|user|>\n{prompt}\n<|assistant|>\n')
            output = []
            for _ in range(limit):
                logits = model(torch.tensor([ids[-config['context_length']:]]))[0, -1]
                logits[tokenizer.special_tokens['<pad>']] = -torch.inf
                logits[tokenizer.special_tokens['<bos>']] = -torch.inf
                token = int(logits.argmax())
                if token == tokenizer.special_tokens['<eos>']:
                    break
                ids.append(token)
                output.append(token)
            answers.append({'prompt': prompt, 'answer': tokenizer.decode(output), 'tokens': len(output), 'hit_limit': len(output) == limit})
    return answers


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--checkpoint', default='model/checkpoints/compact-08-gate-focus.pt')
    parser.add_argument('--output-dir', default='model/training/senior-creative-v1/run-01')
    parser.add_argument('--heldout', default='model/training/senior-creative-v1/heldout.jsonl')
    parser.add_argument('--steps', type=int, default=600)
    parser.add_argument('--eval-every', type=int, default=50)
    parser.add_argument('--patience', type=int, default=5)
    parser.add_argument('--batch-size', type=int, default=8)
    parser.add_argument('--threads', type=int, default=4)
    parser.add_argument('--learning-rate', type=float, default=8e-5)
    parser.add_argument('--from-scratch', action='store_true', help='usa somente a arquitetura da origem, com inicialização escalada; não continua seus pesos')
    args = parser.parse_args()
    if min(args.steps, args.eval_every, args.patience, args.batch_size, args.threads) < 1:
        parser.error('Os limites de execução precisam ser positivos.')
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=False)
    torch.set_num_threads(args.threads)
    torch.manual_seed(42)
    random.seed(42)
    tokenizer_path = Path('model/tokenizer.json')
    tokenizer = ByteBPETokenizer.load(tokenizer_path)
    original = load_checkpoint(args.checkpoint)
    config = dict(original['config'])
    if args.from_scratch:
        config['initialization'] = 'scaled-normal-v1'
    if max(tokenizer.vocab.values()) >= config['vocab_size']:
        raise ValueError('Tokenizer incompatível com os pesos.')
    paths = [Path('python/data') / name for name in (
        'combined.jsonl', 'behavior_expanded.jsonl', 'behavior_phase1.jsonl',
        'curriculum_apex_v1.jsonl', 'senior_creative_v1.jsonl')]
    heldout = read_rows(args.heldout)
    legacy = read_rows('model/eval_generation.jsonl')
    excluded = {question_key(row) for row in heldout}
    excluded.update(' '.join(normalize(row['prompt']).split()).rstrip('.?! ') for row in legacy)
    rows, rejected = clean_rows(paths, excluded)
    train, validation = split_rows(rows)
    assert not ({question_key(row) for row in train} & {question_key(row) for row in validation})
    for name, records in [('train', train), ('validation', validation)]:
        (output / f'{name}.jsonl').write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in records), encoding='utf-8')
    manifest = {'seed': 42, 'arguments': vars(args), 'config': config,
                'source_sha256': hashlib.sha256(Path(args.checkpoint).read_bytes()).hexdigest(),
                'tokenizer_sha256': hashlib.sha256(tokenizer_path.read_bytes()).hexdigest(),
                'inputs': {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths + [Path(args.heldout)]},
                'records': {'train': len(train), 'validation': len(validation), 'heldout': len(heldout)},
                'rejected': rejected, 'domains': dict(Counter(row.get('domain', 'replay') for row in train)),
                'limits': ['Validação do replay pode ter sido vista no treino original.',
                           'Heldout autoral novo não entra no otimizador nem na seleção do checkpoint.',
                           'Loss não comprova competência sênior; geração exige revisão.',
                           'Checkpoint mantém janela original e não é promovido automaticamente.']}
    (output / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    train_samples = encode_rows(train, tokenizer, config['context_length'], with_profile=True)
    val_samples = encode_rows(validation, tokenizer, config['context_length'])
    model = build_model(config)
    if not args.from_scratch:
        model.load_state_dict(original['state_dict'])
    print(json.dumps({'records': manifest['records'], 'training_windows': len(train_samples[0]), 'parameters': sum(p.numel() for p in model.parameters())}), flush=True)
    baseline_loss = evaluate_loss(model, val_samples, args.batch_size)
    best_loss, best_step, stale = baseline_loss, 0, 0
    best_state = {key: value.clone() for key, value in model.state_dict().items()}
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.learning_rate, weight_decay=0.01)
    started = time.monotonic()
    history = [{'step': 0, 'validation_loss': baseline_loss}]
    print(json.dumps(history[0]), flush=True)
    for step in range(1, args.steps + 1):
        model.train()
        warmup = min(20, args.steps)
        factor = min(1.0, step / warmup) * (0.2 + 0.8 * 0.5 * (1 + math.cos(math.pi * step / args.steps)))
        for group in optimizer.param_groups:
            group['lr'] = args.learning_rate * factor
        index = torch.randint(len(train_samples[0]), (args.batch_size,))
        x, y = (part[index] for part in train_samples)
        logits = model(x)
        loss = F.cross_entropy(logits.reshape(-1, config['vocab_size']), y.reshape(-1), ignore_index=-100)
        if not torch.isfinite(loss):
            raise RuntimeError('Loss não finita; treino interrompido sem promoção.')
        optimizer.zero_grad(set_to_none=True)
        loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
        optimizer.step()
        if step % args.eval_every == 0 or step == args.steps:
            value = evaluate_loss(model, val_samples, args.batch_size)
            if value < best_loss - 1e-4:
                best_loss, best_step, stale = value, step, 0
                best_state = {key: value.clone() for key, value in model.state_dict().items()}
                save_checkpoint({'config': config, 'state_dict': best_state, 'steps': step,
                                 'source': args.checkpoint, 'best_val_loss': best_loss, 'status': 'experimental',
                                 'training_method': 'assistant-only-from-scratch' if args.from_scratch else 'assistant-only-sft',
                                 'tokenizer_sha256': manifest['tokenizer_sha256']}, output / 'candidate.safetensors')
            else:
                stale += 1
            row = {'step': step, 'train_loss': loss.item(), 'validation_loss': value, 'best_step': best_step, 'elapsed_seconds': round(time.monotonic() - started, 2)}
            history.append(row)
            (output / 'history.json').write_text(json.dumps(history, indent=2), encoding='utf-8')
            print(json.dumps(row), flush=True)
            if stale >= args.patience:
                break
    # Teste final após seleção pela validação; resultados não influenciam o treino.
    test_samples = encode_rows(heldout, tokenizer, config['context_length'])
    model.load_state_dict(original['state_dict'])
    reference_validation_loss = evaluate_loss(model, val_samples, args.batch_size)
    baseline_test = evaluate_loss(model, test_samples, args.batch_size)
    baseline_answers = sample_answers(model, tokenizer, config, heldout)
    model.load_state_dict(best_state)
    candidate_test = evaluate_loss(model, test_samples, args.batch_size)
    candidate_answers = sample_answers(model, tokenizer, config, heldout)
    report = {'baseline_validation_loss': reference_validation_loss, 'initial_validation_loss': baseline_loss,
              'initialization_mode': 'from-scratch' if args.from_scratch else 'continue-weights',
              'candidate_validation_loss': best_loss,
              'baseline_heldout_loss': baseline_test, 'candidate_heldout_loss': candidate_test,
              'best_step': best_step, 'completed_steps': step,
              'training_seconds': round(time.monotonic() - started, 2),
              'baseline_answers': baseline_answers, 'candidate_answers': candidate_answers,
              'promoted': False, 'limits': manifest['limits']}
    (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({key: value for key, value in report.items() if not key.endswith('answers')}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
