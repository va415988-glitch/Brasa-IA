"""Memória hierárquica determinística para contexto acima da janela do forward."""

import hashlib


def chunk_text(text: str, tokenizer, chunk_tokens: int = 2048) -> list[dict]:
    if chunk_tokens < 128:
        raise ValueError("chunk_tokens deve ser pelo menos 128")
    encode = getattr(tokenizer, "encode_fast", tokenizer.encode)
    ids = encode(str(text))
    chunks = []
    for index in range(0, len(ids), chunk_tokens):
        part = ids[index:index + chunk_tokens]
        decoded = tokenizer.decode(part)
        chunks.append({
            "id": f"chunk-{len(chunks) + 1:04d}",
            "ordinal": len(chunks),
            "token_start": index,
            "token_end": index + len(part),
            "tokens": len(part),
            "sha256": hashlib.sha256(decoded.encode("utf-8")).hexdigest(),
            "text": decoded,
        })
    return chunks

def chunk_text_cached(text: str, tokenizer, chunk_tokens: int = 2048, cache: dict | None = None) -> list[dict]:
    key = (hashlib.sha256(str(text).encode('utf-8')).hexdigest(), int(chunk_tokens))
    if cache is not None and key in cache:
        return cache[key]
    chunks = chunk_text(text, tokenizer, chunk_tokens)
    if cache is not None:
        cache[key] = chunks
        while len(cache) > 32:
            cache.pop(next(iter(cache)))
    return chunks


def select_chunks(chunks: list[dict], query: str, limit: int = 4) -> dict:
    terms = {term.casefold() for term in str(query).split() if len(term) > 2}
    ranked = []
    for chunk in chunks:
        text = chunk.get("text", "").casefold()
        score = sum(text.count(term) for term in terms)
        ranked.append((score, -int(chunk.get("ordinal", 0)), chunk))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    selected = [item[2] for item in ranked[:max(1, limit)]]
    selected.sort(key=lambda item: item.get("ordinal", 0))
    return {
        "schema": "agent-chunk-memory/v1",
        "query": str(query),
        "selected": selected,
        "selected_ids": [item["id"] for item in selected],
        "total_chunks": len(chunks),
        "verified": all(item.get("sha256") for item in selected),
    }
