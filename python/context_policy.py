"""Política de promoção da janela de contexto do agente."""

MIN_CONTEXT_TOKENS = 8192
TARGET_CONTEXT_TOKENS = 16384
PROMOTION_STAGES = (8192, 16384, 32768, 65536)
PROFESSIONAL_GENERATION_TARGET = 4096


def inspect_context(config):
    effective = int((config or {}).get("context_length") or 0)
    if effective >= 65536:
        stage = 65536
    elif effective >= 32768:
        stage = 32768
    elif effective >= 16384:
        stage = 16384
    elif effective >= 8192:
        stage = 8192
    else:
        stage = 0
    generation_limit = int((config or {}).get("generation_length") or 0)
    return {
        "effective_tokens": effective,
        "minimum_tokens": MIN_CONTEXT_TOKENS,
        "target_tokens": TARGET_CONTEXT_TOKENS,
        "promotion_stage": stage,
        "production_eligible": effective >= MIN_CONTEXT_TOKENS,
        "status": "eligible" if effective >= MIN_CONTEXT_TOKENS else "experimental",
        "reason": None if effective >= MIN_CONTEXT_TOKENS else "checkpoint abaixo do mínimo de 8192 tokens",
        "generation_policy": {
            "professional_target_tokens": PROFESSIONAL_GENERATION_TARGET,
            "configured_tokens": generation_limit,
            "production_eligible": generation_limit >= PROFESSIONAL_GENERATION_TARGET,
            "immutable_rule": "não promover sem geração longa contínua, sem truncamento e com avaliação multiarquivo",
        },
    }
