"""Estimativa de RAM para atenção causal em blocos no CPU."""


def choose_strategy(
    available_mb: int | None,
    context_tokens: int,
    batch_size: int = 1,
    *,
    vocab_size: int = 8192,
    hidden_size: int = 128,
    attention_heads: int = 4,
    attention_chunk: int = 512,
) -> dict:
    context_tokens = max(1, int(context_tokens))
    batch_size = max(1, int(batch_size))
    mib = 1024 * 1024
    if context_tokens <= attention_chunk:
        estimate = batch_size * attention_heads * context_tokens * context_tokens * 4 / mib
        safe = available_mb is None or estimate < available_mb * 0.45
        return {
            "mode": "direct-attention",
            "effective_context": context_tokens,
            "forward_chunk": context_tokens,
            "attention_chunk": min(attention_chunk, context_tokens),
            "estimated_peak_mb": round(estimate, 1),
            "safe": safe,
        }

    # A atenção opera em blocos de consultas; o limite de memória passa a ser
    # linear no contexto. A estimativa inclui logits completos, scores do bloco,
    # ativações e uma reserva conservadora para alocador/temporários do PyTorch.
    logits_mb = batch_size * context_tokens * vocab_size * 4 / mib
    attention_mb = batch_size * attention_heads * min(attention_chunk, context_tokens) * context_tokens * 4 * 2 / mib
    activations_mb = batch_size * context_tokens * hidden_size * 4 * 8 / mib
    runtime_reserve_mb = 2048
    estimate = logits_mb + attention_mb + activations_mb + runtime_reserve_mb
    safe = available_mb is None or estimate < available_mb * 0.45
    return {
        "mode": "chunked-attention",
        "effective_context": context_tokens,
        "forward_chunk": context_tokens,
        "attention_chunk": min(attention_chunk, context_tokens),
        "estimated_peak_mb": round(estimate, 1),
        "safe": safe,
        "mechanism": "causal-query-blocks",
    }
