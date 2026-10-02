"""Compacta conversas longas em estado de trabalho verificável e delimitado.

A compactação é extrativa e determinística: preserva trechos importantes do
histórico, mantém as mensagens recentes literais e nunca pede ao próprio modelo
experimental que resuma seus pesos/contexto. Assim o custo de memória é
limitado pelo prompt final, não pelo tamanho acumulado da sessão.
"""

from __future__ import annotations

import re


_SIGNAL = re.compile(
    r"\b(?:objetivo|meta|decidimos|decisão|combinamos|prefiro|preciso|"
    r"requisito|restrição|limite|não pode|nao pode|deve|pendente|pendência|"
    r"próximo passo|proxima etapa|concluí|concluido|implementado|resultado|"
    r"falhou|erro|problema|importante|quero|projeto|checkpoint|contexto)\b",
    re.IGNORECASE,
)
_PATH_OR_TECH = re.compile(
    r"(?:[\w.-]+/){1,}[\w./-]+|\b(?:python|rust|typescript|javascript|api|"
    r"endpoint|teste|memória|memoria|agente|modelo|token)\b",
    re.IGNORECASE,
)
_SENTENCE_SPLIT = re.compile(r"(?<=[!?])\s+|(?<=\.)\s+(?=[A-ZÁÉÍÓÚÀ-Ý0-9])|\n+")


def _encode(text: str, tokenizer=None) -> list[int]:
    if tokenizer is not None:
        try:
            encode = getattr(tokenizer, "encode_fast", tokenizer.encode)
            return list(encode(text))
        except (AttributeError, TypeError, ValueError):
            pass
    # Sem tokenizer, bytes UTF-8 são um limite superior consistente para o BPE.
    return list(text.encode("utf-8"))


def _decode(tokens: list[int], tokenizer=None) -> str:
    if tokenizer is not None:
        try:
            return str(tokenizer.decode(tokens))
        except (AttributeError, TypeError, ValueError):
            pass
    try:
        return bytes(tokens).decode("utf-8", errors="ignore")
    except (TypeError, ValueError):
        return ""


def _token_count(text: str, tokenizer=None) -> int:
    if tokenizer is not None:
        try:
            encode = getattr(tokenizer, "encode_fast", tokenizer.encode)
            return len(encode(str(text)))
        except (AttributeError, TypeError, ValueError):
            pass
    return len(str(text).encode("utf-8"))


def _upper_token_count(text: str) -> int:
    # O BPE local parte de bytes e só os mescla; bytes são limite superior.
    return len(str(text).encode("utf-8"))


def _budget_token_count(text: str, tokenizer=None) -> int:
    """Conta no mesmo espaço de tokens do orçamento quando há tokenizer."""
    return _token_count(text, tokenizer) if tokenizer is not None else _upper_token_count(text)


def _fit_text(text: str, limit: int, tokenizer=None) -> str:
    """Mantém início e fim de um trecho, que costumam conter pedido e detalhes."""
    if limit <= 0:
        return ""
    if tokenizer is not None:
        tokens = _encode(str(text), tokenizer)
        if len(tokens) <= limit:
            return str(text)
        marker = " … [trecho omitido] … "
        marker_tokens = _encode(marker, tokenizer)
        available = max(1, limit - len(marker_tokens))
        head_count = (available + 1) // 2
        tail_count = max(0, available - head_count)
        fitted = tokens[:head_count] + marker_tokens
        if tail_count:
            fitted.extend(tokens[-tail_count:])
        return _decode(fitted, tokenizer)
    raw = str(text).encode("utf-8")
    if len(raw) <= limit:
        return str(text)
    marker = " … [trecho omitido] … "
    marker_bytes = marker.encode("utf-8")
    available = max(1, limit - len(marker_bytes))
    head_count = (available + 1) // 2
    tail_count = max(0, available - head_count)
    head = raw[:head_count].decode("utf-8", errors="ignore")
    tail = raw[-tail_count:].decode("utf-8", errors="ignore") if tail_count else ""
    return head + marker + tail


def _fit_token_prefix(text: str, limit: int, tokenizer=None) -> str:
    """Corta um prefixo em fronteira de token, mantendo seu início legível."""
    if limit <= 0:
        return ""
    tokens = _encode(str(text), tokenizer)
    if len(tokens) <= limit:
        return str(text)
    return _decode(tokens[:limit], tokenizer)


def _messages(messages) -> list[dict]:
    normalized = []
    for item in messages if isinstance(messages, list) else []:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        content = str(item.get("content") or "").strip()
        if role in {"user", "assistant"} and content:
            normalized.append({"role": role, "content": content})
        # Resultados de ferramentas têm fluxo próprio de evidências; não são
        # mensagens de conversa e não devem ser reinterpretados como fala.
    return normalized


def _summary_candidates(messages: list[dict], query: str, tokenizer=None) -> list[dict]:
    query_terms = {
        term.casefold() for term in re.findall(r"[\wÀ-ÿ]{4,}", query)
        if term.casefold() not in {"sobre", "quais", "como", "para", "mais", "isso"}
    }
    candidates = []
    seen = set()
    for index, message in enumerate(messages):
        role = message["role"]
        content = message["content"]
        if role == "tool":
            # JSON de tool result contém muita saída sem valor conversacional.
            try:
                import json
                value = json.loads(content)
                content = str(
                    value.get("summary") or value.get("text") or value.get("message")
                    or value.get("status") or content
                )
            except (ValueError, TypeError, AttributeError):
                content = content[:1200]
        sentences = [part.strip(" \t-•") for part in _SENTENCE_SPLIT.split(content) if part.strip(" \t-•")]
        if not sentences:
            sentences = [content]
        for sentence in sentences:
            sentence = re.sub(r"\s+", " ", sentence).strip()
            if len(sentence) < 12:
                continue
            sentence = sentence[:600]
            fingerprint = re.sub(r"[^\w]+", " ", sentence.casefold()).strip()
            if not fingerprint or fingerprint in seen:
                continue
            seen.add(fingerprint)
            words = {term.casefold() for term in re.findall(r"[\wÀ-ÿ]{4,}", sentence)}
            overlap = len(words & query_terms)
            role_weight = 4 if role == "user" else (2 if role == "assistant" else 1)
            signal_weight = 4 if _SIGNAL.search(sentence) else 0
            technical_weight = 2 if _PATH_OR_TECH.search(sentence) else 0
            goal_weight = 6 if re.search(r"\b(?:objetivo|meta|decidimos|requisito|restrição|limite)\b", sentence, re.I) else 0
            recency_weight = min(index, 30) / 30
            score = role_weight + signal_weight + technical_weight + goal_weight + min(overlap, 3) * 2 + recency_weight
            if score < 4.5:
                continue
            label = {"user": "Usuário", "assistant": "Agente", "tool": "Ferramenta"}[role]
            candidates.append({
                "index": index,
                "score": score,
                "line": f"- {label}: “{sentence}”",
                "tokens": _upper_token_count(sentence),
            })
    return candidates


def compact_conversation(
    messages,
    tokenizer=None,
    *,
    fixed_prefix: str = "",
    query: str = "",
    max_input_tokens: int = 2048,
    recent_message_limit: int = 8,
    source_context_multiplier: int = 2,
) -> dict:
    """Resume o histórico antigo e preserva literalmente o sufixo da conversa.

    ``max_input_tokens`` limita o prompt completo, incluindo prefixo fixo. O
    multiplicador só declara quantos tokens de histórico de origem entram no
    ciclo de compactação; ele não amplia os embeddings nem o forward do modelo.
    """
    normalized = _messages(messages)
    max_input_tokens = max(16, int(max_input_tokens))
    fixed_tokens = _token_count(fixed_prefix, tokenizer)

    # Um prefixo de sistema pode, sozinho, ser maior que a janela reservada
    # para a entrada. Nesse caso, reduza o prefixo antes de escolher mensagens:
    # a pergunta mais recente tem prioridade sobre as instruções longas.
    latest_user = next((item for item in reversed(normalized) if item["role"] == "user"), None)
    if latest_user is not None:
        protected_turn = f"<|user|>\n{latest_user['content']}\n<|assistant|>\n"
        protected_tokens = _token_count(protected_turn, tokenizer)
        if fixed_tokens + protected_tokens > max_input_tokens:
            prefix_budget = max(0, max_input_tokens - protected_tokens)
            fixed_prefix = _fit_token_prefix(fixed_prefix, prefix_budget, tokenizer)
            fixed_tokens = _token_count(fixed_prefix, tokenizer)

    working_budget = max(0, max_input_tokens - fixed_tokens - 8)

    recent_indices = [index for index, item in enumerate(normalized) if item["role"] in {"user", "assistant"}]
    recent_indices = recent_indices[-max(1, int(recent_message_limit)): ]
    boundary = recent_indices[0] if recent_indices else len(normalized)
    recent = [normalized[index] for index in recent_indices]
    older = normalized[:boundary]
    summary_fraction = 0.42 if max_input_tokens <= 2048 else 0.28
    protected_recent_cost = (
        _budget_token_count(f"<|user|>\n{latest_user['content']}\n", tokenizer)
        if latest_user is not None else 0
    )
    summary_budget = (
        min(
            max(0, int(working_budget * summary_fraction)),
            max(0, working_budget - protected_recent_cost - 8),
        )
        if older else 0
    )
    recent_budget = max(protected_recent_cost, working_budget - summary_budget)

    # Reserva a capacidade para a mensagem atual e depois, se couber, recupera
    # ciclos anteriores em ordem reversa, sempre entregando-os cronologicamente.
    kept_recent = []
    used_recent = 0
    for message in reversed(recent):
        role = message["role"]
        text = message["content"]
        rendered_cost = _budget_token_count(f"<|{role}|>\n{text}\n", tokenizer)
        remaining = recent_budget - used_recent
        if rendered_cost <= remaining:
            kept_recent.append(message)
            used_recent += rendered_cost
            continue
        if remaining > 8:
            marker_cost = _token_count(" … [trecho omitido] … ", tokenizer)
            text_budget = max(1, remaining - marker_cost - 3)
            clipped = _fit_text(text, text_budget, tokenizer)
            kept_recent.append({"role": role, "content": clipped})
            used_recent += _upper_token_count(f"<|{role}|>\n{clipped}\n")
        break
    kept_recent.reverse()

    candidates = _summary_candidates(older, query, tokenizer)
    candidates.sort(key=lambda item: (-item["score"], -item["index"]))
    selected = []
    used_summary = 0
    for candidate in candidates:
        cost = _budget_token_count(candidate["line"] + "\n", tokenizer)
        if used_summary + cost <= summary_budget:
            selected.append(candidate)
            used_summary += cost
    selected.sort(key=lambda item: item["index"])

    def render() -> str:
        pieces = [fixed_prefix]
        if selected:
            lines = "\n".join(item["line"] for item in selected)
            pieces.append(
                "<|context|>\nRESUMO DA CONVERSA (referência histórica):\n"
                + lines + "\n"
            )
        for item in kept_recent:
            pieces.append(f"<|{item['role']}|>\n{item['content']}\n")
        pieces.append("<|assistant|>\n")
        return "".join(pieces)

    prompt = render()
    # Os marcadores especiais têm custo próprio no tokenizer. Ajusta até que o
    # prompt real caiba, removendo primeiro memória de menor prioridade e só
    # depois mensagens antigas do sufixo.
    while _token_count(prompt, tokenizer) > max_input_tokens:
        if selected:
            least_important = min(range(len(selected)), key=lambda index: (selected[index]["score"], selected[index]["index"]))
            selected.pop(least_important)
        elif len(kept_recent) > 1:
            kept_recent.pop(0)
        elif kept_recent:
            current = kept_recent[-1]
            current_tokens = _encode(current["content"], tokenizer)
            if len(current_tokens) <= 1:
                kept_recent.clear()
            else:
                current["content"] = _decode(current_tokens[:max(1, len(current_tokens) // 2)], tokenizer)
        else:
            # O prefixo do sistema sozinho excede o orçamento; preserva apenas
            # o sufixo do perfil em vez de ultrapassar o limite do checkpoint.
            prefix_tokens = _encode(fixed_prefix, tokenizer)
            allowed = max(1, max_input_tokens - 8)
            fixed_prefix = _decode(prefix_tokens[-allowed:], tokenizer)
        prompt = render()

    summary_text = "\n".join(item["line"] for item in selected)
    source_tokens = sum(_upper_token_count(item["content"]) for item in normalized)
    output_tokens = _token_count(prompt, tokenizer)
    return {
        "prompt": prompt,
        "summary": summary_text,
        "recent_messages": kept_recent,
        "stats": {
            "schema": "conversation-compaction/v1",
            "source_messages": len(normalized),
            "source_token_upper_bound": source_tokens,
            "summarized_messages": len(older),
            "summary_items": len(selected),
            "summary_tokens": _token_count(summary_text, tokenizer),
            "recent_messages_kept": len(kept_recent),
            "prompt_token_upper_bound": output_tokens,
            "token_count_mode": "bpe-exact" if tokenizer else "utf8-byte-upper-bound",
            "prompt_budget_tokens": max_input_tokens,
            "source_context_multiplier": max(1, int(source_context_multiplier)),
            "effective_source_history_tokens": max_input_tokens * max(1, int(source_context_multiplier)),
            "compression_ratio_upper_bound": round(source_tokens / max(output_tokens, 1), 2),
            "bounded": output_tokens <= max_input_tokens,
        },
    }
