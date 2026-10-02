"""Configuração e seleção verificável para geração criativa local."""

from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata


def _words(text: str) -> list[str]:
    plain = "".join(char for char in unicodedata.normalize("NFKD", str(text).casefold())
                    if not unicodedata.combining(char))
    return re.findall(r"[a-z0-9]+", plain)


@dataclass(frozen=True)
class CreativeConfig:
    """Parâmetros aprovados para uma família de pedidos criativos."""

    name: str = "balanced"
    attempts: int = 2
    temperature: float = 0.8
    top_p: float = 0.9
    max_tokens: int = 512
    diversity_weight: float = 4.0
    specificity_weight: float = 1.0
    constraint_weight: float = 5.0
    repetition_weight: float = 3.0

    def __post_init__(self) -> None:
        if self.attempts < 1 or not 0 < self.temperature <= 2 or not 0 < self.top_p <= 1:
            raise ValueError("parâmetros de amostragem criativa inválidos")
        if self.max_tokens < 32:
            raise ValueError("max_tokens criativo deve ser pelo menos 32")


CREATIVE_PROFILES = {
    "focused": CreativeConfig("focused", 1, 0.65, 0.85, 512, 3.0, 1.4, 7.0, 4.0),
    "balanced": CreativeConfig(),
    "divergent": CreativeConfig("divergent", 3, 1.0, 0.95, 512, 5.0, 1.0, 5.0, 2.0),
}


def profile_for(question: str) -> CreativeConfig:
    words = set(_words(question))
    if words & {"brainstorm", "divergencia", "divergentes", "disruptivas", "radicais"}:
        return CREATIVE_PROFILES["divergent"]
    if words & {"reescreva", "revise", "ajuste", "edite", "preserve"}:
        return CREATIVE_PROFILES["focused"]
    return CREATIVE_PROFILES["balanced"]


def is_interface_request(question: str) -> bool:
    return bool(re.search(
        r"\b(?:interface|ui|ux|dashboard|painel|pagina|p[aá]gina|site|landing|formul[aá]rio|tela|frontend|front-end|webapp)\b",
        str(question), re.I,
    ))


def interface_design_guidance(question: str) -> str:
    """Rubrica de produto e engenharia para interfaces geradas pelo agente."""
    if not is_interface_request(question):
        return ""
    return (
        "TRILHA DE INTERFACE SÊNIOR: antes de escrever código, escolha uma direção visual "
        "com motivo de produto. Faça a proposta ter hierarquia, foco e uma ação principal; "
        "defina layout, navegação, estados vazio/carregando/erro/sucesso, responsividade, "
        "teclado, foco, contraste, semântica e componentes reutilizáveis. Evite o template "
        "genérico de dashboard: varie composição, densidade, ritmo, tipografia e relação "
        "entre conteúdo e ação conforme o domínio. Explique no campo assumptions por que a "
        "direção escolhida serve ao usuário. Não use placeholders nem invente funcionalidades."
    )


def is_fullstack_request(question: str) -> bool:
    text = str(question)
    has_layers = bool(re.search(r"\b(?:frontend|front-end|interface|ui|pagina|site|tela)\b", text, re.I)) and bool(
        re.search(r"\b(?:backend|back-end|api|servidor|banco|persist[eê]ncia|autentic[aã]cao|full.?stack)\b", text, re.I)
    )
    return has_layers or bool(re.search(r"\bfull.?stack\b", text, re.I))


def fullstack_guidance(question: str) -> str:
    """Contrato de trabalho para construir um produto full-stack coerente."""
    if not is_fullstack_request(question):
        return ""
    return (
        "TRILHA FULL-STACK SÊNIOR: trate a interface, o domínio e a infraestrutura "
        "como um produto único. Defina primeiro entidades, invariantes e fluxos; depois "
        "explicite o contrato de cada API (método, rota, entrada, saída e erro) e conecte "
        "a UI a estados reais de carregamento, vazio, sucesso e falha. Separe domínio, "
        "transporte, persistência e apresentação. Valide entradas no servidor, trate "
        "concorrência e autenticação quando existirem, e escreva testes de unidade e de "
        "integração para os fluxos críticos. Não crie uma tela falsa com dados hardcoded "
        "nem um backend desconectado da experiência do usuário."
    )


def wants_variations(question: str) -> bool:
    words = set(_words(question))
    return bool(words & {"ideias", "alternativas", "opcoes", "conceitos", "brainstorm", "variacoes"})


def creative_guidance(question: str, config: CreativeConfig | None = None) -> str:
    config = config or profile_for(question)
    if wants_variations(question):
        mode = f"Gere {config.attempts} alternativas internamente e apresente as melhores."
        distinction = "Use mecanismos realmente diferentes, com públicos ou experiências distintas; não troque apenas adjetivos."
    else:
        mode = "Entregue uma peça coesa e pronta para ser usada."
        distinction = "Use detalhes concretos, imagem ou aplicação verificável."
    return (
        f"Pedido criativo ({config.name}): {mode} {distinction} "
        "Respeite público, tom, formato, quantidade e exclusões expressos pela pessoa. "
        "Evite introduções genéricas e não alegue pesquisa ou validação que não ocorreu."
    )


def extract_constraints(question: str) -> dict[str, list[str]]:
    """Extrai restrições simples e auditáveis do briefing."""
    forbidden = [_words(item)[0] for item in re.findall(r"\bsem\s+([\wÀ-ÿ-]+)", question, re.I)
                 if _words(item)]
    required = [_words(item)[0] for item in re.findall(r"\b(?:com|inclua|incluindo)\s+([\wÀ-ÿ-]+)", question, re.I)
                if _words(item)]
    return {"forbidden": forbidden, "required": required}


def score_candidate(question: str, answer: str, config: CreativeConfig | None = None) -> dict:
    config = config or profile_for(question)
    words = _words(answer)
    constraints = extract_constraints(question)
    unique_ratio = len(set(words)) / len(words) if words else 0.0
    trigrams = list(zip(words, words[1:], words[2:]))
    repetition = 1 - (len(set(trigrams)) / len(trigrams)) if trigrams else 0.0
    forbidden_hits = sum(word in words for word in constraints["forbidden"])
    required_hits = sum(word in words for word in constraints["required"])
    score = (config.diversity_weight * unique_ratio
             + config.specificity_weight * min(len(words), 120) / 120
             - config.repetition_weight * repetition
             - config.constraint_weight * forbidden_hits
             + min(required_hits, 3) * config.constraint_weight)
    if len(words) < 12:
        score -= 100
    return {"score": round(score, 4), "word_count": len(words),
            "unique_ratio": round(unique_ratio, 4), "repetition": round(repetition, 4),
            "forbidden_hits": forbidden_hits, "required_hits": required_hits,
            "constraints": constraints, "profile": config.name}


def candidate_score(question: str, answer: str) -> float:
    return float(score_candidate(question, answer)["score"])


def select_candidate(question: str, candidates: list[str], config: CreativeConfig | None = None) -> tuple[str, int]:
    if not candidates:
        return "", -1
    config = config or profile_for(question)
    index = max(range(len(candidates)), key=lambda i: score_candidate(question, candidates[i], config)["score"])
    return candidates[index], index
