"""Política de promoção da janela de contexto do agente."""

MIN_CONTEXT_TOKENS = 8192
TARGET_CONTEXT_TOKENS = 32768
PROMOTION_STAGES = (8192, 16384, 32768, 65536)
PROFESSIONAL_GENERATION_TARGET = 4096
DEFAULT_GENERATION_TOKENS = 2048


def runtime_context_window(config):
    """Calcula a janela ativa, inclusive extensões explicitamente configuradas."""
    config = config or {}
    native = max(0, int(config.get("context_length") or 0))
    trained = max(0, int(config.get("training_context_length") or native))
    configured_runtime = max(0, int(config.get("runtime_context_tokens") or 0))
    verified_runtime = max(0, int(config.get("runtime_context_verified_tokens") or 0))
    if configured_runtime:
        runtime = configured_runtime
    else:
        runtime = min(native, trained * 2) if trained and native else native
    return {
        "native_context_tokens": native,
        "trained_context_tokens": trained,
        "runtime_context_tokens": runtime,
        "runtime_context_verified_tokens": verified_runtime,
        "runtime_supported": verified_runtime >= runtime > 0,
        "runtime_extension": runtime > trained > 0,
        "runtime_policy": (
            "configured-runtime-verified-extension" if configured_runtime and verified_runtime >= runtime > trained > 0
            else "configured-experimental-window" if configured_runtime and runtime > trained > 0
            else "experimental-2x-training-window" if runtime > trained > 0
            else "checkpoint-native-window"
        ),
    }


def resolve_generation_budget(configured_generation=None, requested_tokens=0, override=None):
    """Resolve an output budget independently from the attention context size."""
    configured = int(configured_generation or DEFAULT_GENERATION_TOKENS)
    cap = min(4096, max(96, configured))
    if override not in (None, ""):
        return min(cap, max(16, int(override)))
    return min(cap, max(DEFAULT_GENERATION_TOKENS, int(requested_tokens or 0)))


def inspect_context(config):
    window = runtime_context_window(config)
    effective = window["runtime_context_tokens"]
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
    generation_limit = int((config or {}).get("generation_length") or DEFAULT_GENERATION_TOKENS)
    extended = window["runtime_extension"]
    production_eligible = effective >= MIN_CONTEXT_TOKENS and not extended
    runtime_ready = effective >= MIN_CONTEXT_TOKENS and window["runtime_supported"]
    reason = None
    if extended:
        if runtime_ready:
            reason = (
                f"janela de {effective} tokens verificada para execução; o checkpoint foi treinado com "
                f"{window['trained_context_tokens']} tokens, então a qualidade semântica no contexto estendido "
                "continua experimental"
            )
        else:
            reason = (
                f"janela de {effective} tokens não verificada para execução; o checkpoint foi treinado com "
                f"{window['trained_context_tokens']} tokens"
            )
    elif effective < MIN_CONTEXT_TOKENS:
        reason = f"janela de inferência experimental de {effective} tokens; mínimo de produção: {MIN_CONTEXT_TOKENS}"
    return {
        "effective_tokens": effective,
        **window,
        "minimum_tokens": MIN_CONTEXT_TOKENS,
        "target_tokens": TARGET_CONTEXT_TOKENS,
        "promotion_stage": stage,
        "production_eligible": production_eligible,
        "runtime_ready": runtime_ready,
        "quality_validated_at_runtime_context": not extended,
        "status": "eligible" if production_eligible else ("runtime-ready" if runtime_ready else "experimental"),
        "reason": reason,
        "generation_policy": {
            "professional_target_tokens": PROFESSIONAL_GENERATION_TARGET,
            "configured_tokens": generation_limit,
            "production_eligible": generation_limit >= PROFESSIONAL_GENERATION_TARGET,
            "immutable_rule": "não promover sem geração longa contínua, sem truncamento e com avaliação multiarquivo",
        },
    }
