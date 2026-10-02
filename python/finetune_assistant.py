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
from conditioned_context import training_windows, generation_window, conditioned_prompt


def question_key(row):
    messages = row.get('messages') or []
    latest_user = next((message.get('content', '') for message in reversed(messages)
                        if message.get('role') == 'user'), '')
    return ' '.join(normalize(latest_user).split()).rstrip('.?! ')


def compute_excluded_prompts(legacy_rows, only_jsonl=None):
    """A focused fine-tune should not filter out the benchmark prompts it is trying to teach."""
    if only_jsonl:
        return set()
    return {(' '.join(normalize(row['prompt']).split())).rstrip('.?! ') for row in legacy_rows if isinstance(row, dict) and 'prompt' in row}


def sample_key(row):
    """Identify the full prompt history so context variants remain distinct."""
    return '\n'.join(
        f"{message.get('role')}:{' '.join(normalize(message.get('content', '')).split()).rstrip('.?! ')}"
        for message in row.get('messages', [])[:-1]
    )


def read_rows(path):
    return [json.loads(line) for line in Path(path).read_text(encoding='utf-8').splitlines() if line.strip()]


def clean_rows(paths, excluded):
    unique = {}
    rejected = Counter()
    for path in paths:
        for row in read_rows(path):
            messages = row.get('messages', [])
            roles = [m.get('role') for m in messages if isinstance(m, dict)]
            valid_dialogue = (
                len(messages) >= 2 and len(roles) == len(messages)
                and roles[0] == 'user' and roles[-1] == 'assistant'
                and all(role in {'user', 'assistant'} for role in roles)
                and all(left != right for left, right in zip(roles, roles[1:]))
            )
            if (not valid_dialogue
                    or any(any(m.get(key) for key in ('tool_call', 'tool_calls', 'function_call'))
                           or not isinstance(m.get('content'), str) or not m['content'].strip() for m in messages)):
                rejected['not_plain_pair'] += 1
                continue
            key = question_key(row)
            if key in excluded:
                rejected['evaluation_question'] += 1
                continue
            prompt_key = sample_key(row)
            if prompt_key in unique:
                rejected['duplicate_question'] += 1
            unique[prompt_key] = {**row, 'source': str(path)}
    return list(unique.values()), dict(rejected)


def split_rows(rows):
    train, validation = [], []
    for row in rows:
        bucket = int(hashlib.sha256(question_key(row).encode()).hexdigest()[:8], 16) % 10
        (validation if bucket == 0 else train).append(row)
    if not train or not validation:
        raise ValueError('Treino e validação precisam conter exemplos distintos.')
    return train, validation


def training_views(rows, multi_turn_repeat=1):
    """Oversample complete dialogue histories without duplicating validation."""
    repeated = max(1, int(multi_turn_repeat))
    return [
        row
        for row in rows
        for _ in range(repeated if len(row.get('messages', [])) > 2 else 1)
    ]


def encode_rows(rows, tokenizer, context, with_profile=False, progress=False,
                max_answer_tokens=None, preserve_prompt=False):
    """Encode a context-sized prompt and optionally multi-window answer targets."""
    samples = []
    prompt_char_limit = context * 8
    answer_token_limit = max(0, int(max_answer_tokens if max_answer_tokens is not None else context - 1))
    answer_char_limit = max(prompt_char_limit, answer_token_limit * 8)
    total = len(rows)
    encode_text = getattr(tokenizer, "encode_fast", tokenizer.encode)
    for row_index, row in enumerate(rows, start=1):
        messages = row["messages"]
        if messages[-1].get('role') == 'assistant':
            context_messages = messages[:-1]
            answer = messages[-1]['content']
        elif row.get('reference_answer'):
            # Blind heldout prompts end in a user turn; their separately authored
            # reference answer is the target used only for post-selection loss.
            context_messages = messages
            answer = row['reference_answer']
        else:
            raise ValueError('A amostra precisa terminar em assistant ou ter reference_answer.')
        prompt = conditioned_prompt(context_messages) if preserve_prompt else ''.join(
            f'<|{message["role"]}|>\n{message["content"] if preserve_prompt else message["content"][-prompt_char_limit:]}\n'
            for message in context_messages
        ) + '<|assistant|>\n'
        if not preserve_prompt: prompt = prompt[-prompt_char_limit:]
        if preserve_prompt:
            answer_ids = encode_text(answer + "\n")
            if max_answer_tokens and len(answer_ids) > max_answer_tokens:
                raise ValueError('Resposta excede o orçamento; não treinar código incompleto com EOS artificial.')
            answer_ids += [tokenizer.special_tokens["<eos>"]]
        else:
            answer_ids = encode_text(answer[:answer_char_limit] + "\n")
            answer_ids = answer_ids[:answer_token_limit] + [tokenizer.special_tokens["<eos>"]]
        variants = [prompt]
        if with_profile:
            variants.append(f"<|system|>\n{COMPACT_PROFILE}\n" + prompt)
        for prompt in variants:
            # The architecture has a short fixed context. Keeping its recent tail
            # avoids repeatedly applying every BPE merge to irrelevant old text.
            prompt_ids = encode_text(prompt if preserve_prompt else prompt[-prompt_char_limit:])
            if preserve_prompt:
                samples.extend(training_windows(prompt_ids, answer_ids, context, tokenizer.special_tokens['<pad>']))
                continue
            prompt_ids = prompt_ids[-context:]
            ids = prompt_ids + answer_ids
            labels = [-100] * len(prompt_ids) + answer_ids
            for start in range(0, len(ids) - 1, context):
                x = ids[start:start + context]
                y = labels[start + 1:start + context + 1]
                x = x[:len(y)]
                if not any(value != -100 for value in y):
                    continue
                pad = context - len(x)
                samples.append((x + [tokenizer.special_tokens["<pad>"]] * pad, y + [-100] * pad))
        if progress and (row_index % 500 == 0 or row_index == total):
            print(json.dumps({"encoding_rows": row_index, "total_rows": total}, ensure_ascii=False), flush=True)
    if not samples:
        raise ValueError("Nenhum token de resposta disponível.")
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
            context_messages = row['messages']
            if context_messages[-1].get('role') == 'assistant':
                context_messages = context_messages[:-1]
            prompt = ''.join(
                f'<|{message["role"]}|>\n{message["content"]}\n'
                for message in context_messages
            ) + '<|assistant|>\n'
            ids = tokenizer.encode(prompt)
            output = []
            for _ in range(limit):
                context = (generation_window(tokenizer.encode(prompt), output, config['context_length'])
                           if config.get('training_window_mode') == 'prompt-anchor-v1' else ids[-config['context_length']:])
                logits = model(torch.tensor([context]))[0, -1]
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
    parser.add_argument('--config-path', default='', help='configuração alternativa para um candidato de arquitetura')
    parser.add_argument('--tokenizer-path', default='model/tokenizer.json')
    parser.add_argument('--output-dir', default='model/training/senior-creative-v1/run-01')
    parser.add_argument('--heldout', default='model/training/senior-creative-v1/heldout.jsonl')
    parser.add_argument('--steps', type=int, default=600)
    parser.add_argument('--max-answer-tokens', type=int, default=0,
                        help='alvo máximo por resposta; 0 usa contexto-1, valores maiores treinam continuação em janelas sucessivas')
    parser.add_argument('--multi-turn-repeat', type=int, default=1,
                        help='vezes que cada diálogo com histórico completo aparece no treino; validação não é duplicada')
    parser.add_argument('--additional-jsonl', action='append', default=[],
                        help='dataset JSONL adicional com conversas alternadas em messages; pode repetir')
    parser.add_argument('--only-jsonl', action='append', default=[],
                        help='usa somente estes arquivos de treino; isola um experimento do corpus geral')
    parser.add_argument('--validation-jsonl', default='',
                        help='validação fixa e disjunta, sem repartição por hash')
    parser.add_argument('--sample-tokens', type=int, default=128,
                        help='tokens por amostra final; 0 omite geração amostral')
    parser.add_argument('--snapshot-every-eval', action='store_true',
                        help='guarda cada checkpoint de validação para seleção posterior por qualidade de geração')
    parser.add_argument('--eval-every', type=int, default=50)
    parser.add_argument('--patience', type=int, default=5)
    parser.add_argument('--batch-size', type=int, default=8)
    parser.add_argument('--threads', type=int, default=4)
    parser.add_argument('--learning-rate', type=float, default=8e-5)
    parser.add_argument('--without-profile', action='store_true',
                        help='não injeta <|system|> nos exemplos de treino; útil para checkpoints sem esse formato')
    parser.add_argument('--preserve-prompt', action='store_true',
                        help='mantém o pedido em cada janela e recusa respostas cortadas com EOS artificial')
    parser.add_argument('--from-scratch', action='store_true', help='usa somente a arquitetura da origem, com inicialização escalada; não continua seus pesos')
    args = parser.parse_args()
    if min(args.steps, args.eval_every, args.patience, args.batch_size, args.threads, args.multi_turn_repeat) < 1:
        parser.error('Os limites de execução precisam ser positivos.')
    if args.max_answer_tokens < 0:
        parser.error('--max-answer-tokens não pode ser negativo.')
    if args.sample_tokens < 0:
        parser.error('--sample-tokens não pode ser negativo.')
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=False)
    torch.set_num_threads(args.threads)
    torch.manual_seed(42)
    random.seed(42)
    tokenizer_path = Path(args.tokenizer_path)
    tokenizer = ByteBPETokenizer.load(tokenizer_path)
    original = load_checkpoint(args.checkpoint)
    config = dict(original['config'])
    if args.config_path:
        config.update(json.loads(Path(args.config_path).read_text(encoding='utf-8')))
    # Fine-tuned weights no longer equal the position-extension source. The
    # source-integrity repair contract cannot be copied to a new candidate.
    config.pop('context_extension', None)
    config.pop('runtime_context_verified_tokens', None)
    config['tokenizer_path'] = str(tokenizer_path)
    if args.preserve_prompt:
        config['training_window_mode'] = 'prompt-anchor-v1'
        config['prompt_anchor_tokens'] = int(config.get('training_context_length') or config['context_length']) // 3
    if args.from_scratch:
        config['initialization'] = 'scaled-normal-v1'
    if max(tokenizer.vocab.values()) >= config['vocab_size']:
        raise ValueError('Tokenizer incompatível com os pesos.')
    paths = [Path('python/data') / name for name in (
        'combined.jsonl', 'behavior_expanded.jsonl', 'behavior_phase1.jsonl',
        'curriculum_apex_v1.jsonl', 'senior_creative_v1.jsonl',
        'agentic_curriculum_v1.jsonl', 'open_programming_curriculum_v1.jsonl',
        'agent_harness_curriculum_v1.jsonl', 'hf_datasets_curriculum_v1.jsonl',
        'deep_learning_book_curriculum_v1.jsonl', 'databricks_genai_curriculum_v1.jsonl',
        'little_book_deep_learning_curriculum_v1.jsonl', 'godmode_knowledge_v1.jsonl',
        'neural_professional_v1.jsonl',
        'godmode_procedures_v1.jsonl')] if not args.only_jsonl else [Path(path) for path in args.only_jsonl]
    paths.extend(Path(path) for path in args.additional_jsonl)
    missing_paths = [str(path) for path in paths if not path.is_file()]
    if missing_paths:
        raise FileNotFoundError('arquivos de treino ausentes: ' + ', '.join(missing_paths))
    heldout = read_rows(args.heldout)
    legacy = read_rows('model/eval_generation.jsonl')
    excluded = {question_key(row) for row in heldout}
    excluded.update(compute_excluded_prompts(legacy, args.only_jsonl))
    rows, rejected = clean_rows(paths, excluded)
    if args.validation_jsonl:
        train = rows
        validation, validation_rejected = clean_rows([Path(args.validation_jsonl)], excluded)
        rejected.update({f'validation_{key}': value for key, value in validation_rejected.items()})
        if not validation or ({question_key(row) for row in train} & {question_key(row) for row in validation}):
            raise ValueError('Validação fixa precisa conter exemplos disjuntos do treino.')
    else:
        train, validation = split_rows(rows)
    training_rows = training_views(train, args.multi_turn_repeat)
    assert not ({question_key(row) for row in train} & {question_key(row) for row in validation})
    for name, records in [('train', train), ('validation', validation)]:
        (output / f'{name}.jsonl').write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in records), encoding='utf-8')
    manifest = {'seed': 42, 'arguments': vars(args), 'config': config,
                'source_sha256': hashlib.sha256(Path(args.checkpoint).read_bytes()).hexdigest(),
                'tokenizer_sha256': hashlib.sha256(tokenizer_path.read_bytes()).hexdigest(),
                'inputs': {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths + [Path(args.heldout)] + ([Path(args.validation_jsonl)] if args.validation_jsonl else [])},
                'records': {'train': len(train), 'training_views': len(training_rows),
                            'validation': len(validation), 'heldout': len(heldout)},
                'rejected': rejected, 'domains': dict(Counter(row.get('domain', 'replay') for row in train)),
                'limits': ['Validação do replay pode ter sido vista no treino original.',
                           'Heldout autoral novo não entra no otimizador nem na seleção do checkpoint.',
                           'Loss não comprova competência sênior; geração exige revisão.',
                           'Checkpoint mantém janela original e não é promovido automaticamente.']}
    training_context = min(
        int(config['context_length']),
        int(config.get('training_context_length') or config['context_length']),
    )
    if training_context < 64:
        raise ValueError('training_context_length deve ser pelo menos 64 tokens.')
    config['runtime_context_tokens'] = training_context
    (output / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    answer_token_limit = args.max_answer_tokens or (None if args.preserve_prompt else training_context - 1)
    train_samples = encode_rows(
        training_rows, tokenizer, training_context,
        with_profile=not args.without_profile,
        progress=True,
        max_answer_tokens=answer_token_limit,
        preserve_prompt=args.preserve_prompt,
    )
    val_samples = encode_rows(validation, tokenizer, training_context, progress=True,
                              max_answer_tokens=answer_token_limit, preserve_prompt=args.preserve_prompt)
    model = build_model(config)
    if not args.from_scratch:
        if tuple(model.token_embedding.weight.shape) != tuple(original['state_dict']['token_embedding.weight'].shape):
            raise ValueError('Arquitetura do candidato incompatível com o checkpoint; use --from-scratch.')
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
            if args.snapshot_every_eval:
                snapshot_dir = output / 'snapshots'
                snapshot_dir.mkdir(exist_ok=True)
                save_checkpoint({'config': config, 'state_dict': model.state_dict(), 'steps': step,
                                 'source': args.checkpoint, 'validation_loss': value,
                                 'status': 'experimental-validation-snapshot',
                                 'tokenizer_sha256': manifest['tokenizer_sha256']},
                                snapshot_dir / f'step-{step:04d}.safetensors')
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
    test_samples = encode_rows(heldout, tokenizer, training_context,
                               max_answer_tokens=answer_token_limit, preserve_prompt=args.preserve_prompt)
    if not args.from_scratch:
        model.load_state_dict(original['state_dict'])
        reference_validation_loss = evaluate_loss(model, val_samples, args.batch_size)
        baseline_test = evaluate_loss(model, test_samples, args.batch_size)
        baseline_answers = sample_answers(model, tokenizer, config, heldout, limit=args.sample_tokens) if args.sample_tokens else []
    else:
        # The initialized weights were not retained; final training weights
        # cannot be reported as a baseline for a from-scratch experiment.
        reference_validation_loss, baseline_test, baseline_answers = baseline_loss, None, []
    model.load_state_dict(best_state)
    candidate_test = evaluate_loss(model, test_samples, args.batch_size)
    candidate_answers = sample_answers(model, tokenizer, config, heldout, limit=args.sample_tokens) if args.sample_tokens else []
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
