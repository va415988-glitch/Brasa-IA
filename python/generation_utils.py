"""Shared rules for filtering reserved tokens during autoregressive decoding."""


def generation_control_token_ids(tokenizer, vocab_size):
    """Block reserved protocol tokens, preserving programming operators and EOS."""
    special = getattr(tokenizer, 'special_tokens', {}) or {}
    blocked = set(special.values())
    for token_id in range(int(vocab_size)):
        try: decoded = tokenizer.decode([token_id])
        except (KeyError, IndexError):
            blocked.add(token_id)
            continue
        if '<|' in decoded or '|>' in decoded or decoded in special:
            blocked.add(token_id)
    eos_id = special.get("<eos>")
    if eos_id is not None:
        blocked.discard(int(eos_id))
    return blocked


def sample_creative_token(logits, temperature=0.8, top_p=0.9):
    """Sample a valid token from a bounded nucleus of model probabilities."""
    import torch

    values = logits.float().flatten()
    if not torch.isfinite(values).any():
        raise ValueError("no finite token logits")
    probabilities = torch.softmax(values / temperature, dim=-1)
    ordered, indices = torch.sort(probabilities, descending=True)
    cumulative = torch.cumsum(ordered, dim=0)
    keep = cumulative - ordered < top_p
    keep[0] = True
    nucleus = ordered * keep
    nucleus = nucleus / nucleus.sum()
    return int(indices[torch.multinomial(nucleus, 1).item()].item())


def apply_repetition_penalty(logits, generated_ids, penalty=1.08, window=96):
    """Diminui ciclos do decoder sem bloquear palavras necessárias."""
    import torch

    if penalty <= 1 or not generated_ids:
        return logits
    adjusted = logits.clone()
    for token_id in set(generated_ids[-window:]):
        value = adjusted[..., int(token_id)]
        adjusted[..., int(token_id)] = torch.where(value < 0, value * penalty, value / penalty)
    return adjusted
