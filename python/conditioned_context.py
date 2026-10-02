"""Shared conditioning geometry for long code training and generation."""
from __future__ import annotations


def prompt_anchor(ids, limit):
    limit = max(2, int(limit))
    if len(ids) <= limit: return list(ids)
    head = limit * 2 // 3
    return list(ids[:head]) + list(ids[-(limit - head):])


def window_geometry(prompt_ids, context, anchor_tokens=None):
    anchor = prompt_anchor(prompt_ids, min(int(anchor_tokens or context // 3), context // 3))
    stride = max(1, (context - len(anchor)) // 3)
    return anchor, stride, stride * 2


def generation_window(prompt_ids, completion_ids, context, anchor_tokens=None):
    anchor, stride, history = window_geometry(prompt_ids, context, anchor_tokens)
    offset = len(completion_ids) // stride * stride
    start = max(0, offset - history)
    return anchor + list(completion_ids[start:])


def conditioned_prompt(messages, fixed_prefix=''):
    """Serialize the same role boundaries used by assistant-only training."""
    return fixed_prefix + ''.join(
        f'<|{message["role"]}|>\n{message["content"]}\n'
        for message in messages if message.get('role') in {'user', 'assistant'}
    ) + '<|assistant|>\n'


def training_windows(prompt_ids, answer_ids, context, pad_id, anchor_tokens=None):
    """Every target has its original request; each answer token has loss once."""
    anchor, stride, history = window_geometry(prompt_ids, context, anchor_tokens)
    if not anchor: raise ValueError('A resposta precisa de um pedido para condicionar o treino.')
    for offset in range(0, len(answer_ids), stride):
        previous = list(answer_ids[max(0, offset - history):offset])
        target = list(answer_ids[offset:offset + stride])
        prefix = anchor + previous
        ids = prefix + target
        labels = [-100] * len(prefix) + target
        x, y = ids[:-1], labels[1:]
        padding = context - len(x)
        if padding < 0: raise ValueError('Janela de treino maior que o contexto.')
        yield x + [pad_id] * padding, y + [-100] * padding
