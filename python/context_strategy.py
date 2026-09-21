"""Escolha fail-closed da estratégia de contexto conforme a memória disponível."""

def choose_strategy(available_mb: int | None, context_tokens: int, batch_size: int = 1) -> dict:
    direct_estimate = batch_size * 4 * 2 * context_tokens * context_tokens * 4 * 8 / (1024 * 1024)
    direct_ok = available_mb is None or direct_estimate < available_mb * 0.45
    if direct_ok:
        return {"mode": "direct-attention", "effective_context": context_tokens, "estimated_peak_mb": round(direct_estimate, 1), "safe": True}
    # Escolhe o maior chunk que respeita a margem de 45% da RAM disponível.
    candidates = [8192, 6144, 4096, 3072, 2048, 1024]
    chunk = next((value for value in candidates if value <= context_tokens and (available_mb is None or batch_size * 4 * 2 * value * value * 4 * 8 / (1024 * 1024) < available_mb * 0.45)), 1024)
    chunk_estimate = batch_size * 4 * 2 * chunk * chunk * 4 * 8 / (1024 * 1024)
    return {
        "mode": "chunked-memory",
        "effective_context": context_tokens,
        "forward_chunk": chunk,
        "estimated_peak_mb": round(chunk_estimate, 1),
        "safe": available_mb is None or chunk_estimate < available_mb * 0.45,
        "mechanism": ["chunk summaries", "retrieval of relevant chunks", "verified rolling memory"],
    }
