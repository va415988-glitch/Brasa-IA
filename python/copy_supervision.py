"""Training-only alignment from authored spans; inference has no gold spans."""
from __future__ import annotations

import torch
import torch.nn.functional as F

from conditioned_context import conditioned_prompt


def token_boundaries(tokenizer, text):
    ids = tokenizer.encode_fast(text)
    raw_by_id = {value: bytes.fromhex(key) for key, value in tokenizer.vocab.items()}
    offsets = [0]
    for token in ids:
        offsets.append(offsets[-1] + len(raw_by_id[token]))
    if offsets[-1] != len(text.encode('utf-8')):
        raise ValueError('Token offsets do not reconstruct the encoded input.')
    return ids, offsets


def span_token_range(offsets, span):
    start, end = span
    if start < 0 or end <= start or start not in offsets or end not in offsets:
        raise ValueError('A supervised span must end at exact token boundaries.')
    return offsets.index(start), offsets.index(end)


def encode_alignment_rows(rows, tokenizer, context):
    encoded = []
    for row in rows:
        prompt = conditioned_prompt(row['messages'][:-1])
        answer = row['messages'][-1]['content'] + '\n'
        prompt_ids, prompt_offsets = token_boundaries(tokenizer, prompt)
        answer_ids, answer_offsets = token_boundaries(tokenizer, answer)
        answer_ids.append(tokenizer.special_tokens['<eos>'])
        ids = prompt_ids + answer_ids
        if len(ids) > context:
            raise ValueError('Do not truncate a copy source or a supervised answer.')
        targets = [-100] * len(prompt_ids) + answer_ids
        pointers = [-100] * len(ids)
        for span in row.get('copy_spans', []):
            a, b = span_token_range(prompt_offsets, span['source_bytes'])
            c, d = span_token_range(answer_offsets, span['answer_bytes'])
            if prompt_ids[a:b] != answer_ids[c:d]:
                raise ValueError('Source and target spans must have identical tokenization.')
            if b > len(prompt_ids) - len(tokenizer.encode_fast('<|assistant|>\n')):
                raise ValueError('Supervision must point before the answer boundary.')
            for index, source in enumerate(range(a, b), start=len(prompt_ids) + c):
                if pointers[index] != -100:
                    raise ValueError('Overlapping copy annotations.')
                pointers[index] = source
        x, y, aligned = ids[:-1], targets[1:], pointers[1:]
        continuation = [-100] * len(x)
        for index in range(len(x)):
            if y[index] != -100:
                continuation[index] = int(index > 0 and aligned[index] >= 0
                    and aligned[index - 1] >= 0 and aligned[index] == aligned[index - 1] + 1)
        padding = context - len(x)
        encoded.append((x + [tokenizer.special_tokens['<pad>']] * padding,
                        y + [-100] * padding, aligned + [-100] * padding,
                        continuation + [-100] * padding))
    if not encoded:
        raise ValueError('Empty aligned training set.')
    return tuple(torch.tensor([sample[i] for sample in encoded]) for i in range(4))


def trim_ignored_suffix(batch):
    """Causal prefixes have unchanged logits/gradients after removing ignored tails."""
    labels = batch[1]
    active = (labels != -100).any(0).nonzero()
    if not len(active):
        raise ValueError('Every batch must contain supervised response tokens.')
    end = int(active[-1]) + 1
    return tuple(part[:, :end] for part in batch)


def aligned_loss(logits, state, labels, pointers, continuation_targets, alignment_weight=.5):
    generation = F.cross_entropy(logits.flatten(0, 1), labels.flatten(), ignore_index=-100)
    supervised = labels != -100
    has_pointer = pointers >= 0
    gate = F.binary_cross_entropy(state['mixture'][supervised].clamp(1e-6, 1 - 1e-6),
                                 has_pointer[supervised].float())
    if has_pointer.any():
        probabilities = state['attention'].gather(-1, pointers.clamp_min(0).unsqueeze(-1)).squeeze(-1)
        alignment = -probabilities[has_pointer].clamp_min(1e-9).log().mean()
    else:
        alignment = logits.sum() * 0
    if state['continuation'] is not None:
        transition = F.binary_cross_entropy(state['continuation'][supervised].clamp(1e-6, 1 - 1e-6),
                                            continuation_targets[supervised].float())
    else:
        transition = logits.sum() * 0
    total = generation + alignment_weight * (alignment + gate + transition)
    return total, {'generation': generation.detach(), 'alignment': alignment.detach(),
                   'gate': gate.detach(), 'transition': transition.detach()}
