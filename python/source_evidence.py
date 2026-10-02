"""Conservative, explainable assessment of web evidence.

Search hits are leads, not facts. In particular, a matching URL does not
establish that a technology exists or that the page is its official manual.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from build_knowledge_index import subject_tokens, topic_matches


FICTION_MARKERS = re.compile(
    r"\b(?:fictional|imaginary|made.up|fan.?fiction|fictional universe|"
    r"fict[ií]ci[oa]|imagin[aá]ri[oa]|universo fict[ií]cio)\b", re.I,
)


def topic_from_question(question: str) -> str | None:
    """Extract a user-named subject without maintaining a language allowlist."""
    text = re.sub(r"\s+", " ", question).strip()
    if re.match(r"^(?:crie|cria|fa[cç]a|gere|desenhe|produza)\b", text, re.I) and not re.search(
        r"\b(?:api|c[oó]digo|programa|aplicativo|framework|biblioteca|sdk|site|backend)\b", text, re.I
    ):
        return None
    patterns = (
        r"^(?:pesquise|verifique|confirme)\s+se\s+(.+?)\s+(?:existe|[eé] real)\b",
        r"^(.+?)\s+(?:existe|[eé] real)\b",
        r"^[eé]\s+(.+?)\s+(?:real|fict[ií]ci[oa])\b",
        r"^(?:o que [eé]|quem [eé]|como funciona|o que significa|fale sobre)\s+(.+)",
        r"^(?:explique|me explique|me explica)\s+(?!como\b)(.+)",
        r"^(?:como (?:usar|utilizar|aprender|instalar)|me ensine)\s+(.+)",
        r"\b(?:usando|utilizando|em|com|sobre)\s+([\w.+#-]+(?:\s+[\w.+#-]+){0,2})",
    )
    for index, pattern in enumerate(patterns):
        if index == len(patterns) - 1 and not re.match(
            r"^(?:implemente|implementar|crie|cria|desenvolva|construa|configure|integre|corrija)\b", text, re.I
        ):
            continue
        match = re.search(pattern, text, re.I)
        if not match:
            continue
        candidate = re.split(r"[,;]|\s+(?:para|em|com|usando|utilizando|e (?:crie|fa[cç]a|implemente))\b",
                             match.group(1), maxsplit=1, flags=re.I)[0].strip(" .?!:;\"'“”")
        candidate = re.sub(r"^(?:(?:a|o|as|os|um|uma|sobre|linguagem|framework)\s+)+", "", candidate, flags=re.I)
        if candidate and len(candidate) <= 80 and subject_tokens(candidate):
            return candidate
    return None


def _host(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in {"https", "http"} or not parsed.hostname:
        return ""
    return parsed.hostname.lower().removeprefix("www.")


def _organization(host: str) -> str:
    """Avoid counting two subdomains of the same site as independent sources."""
    parts = host.split('.')
    if len(parts) < 2:
        return host
    if len(parts) >= 3 and parts[-2:] in (["com", "br"], ["org", "br"], ["co", "uk"]):
        return '.'.join(parts[-3:])
    return '.'.join(parts[-2:])


_AMBIGUOUS_TOPIC_CONTEXT: dict[str, tuple[str, ...]] = {
    "go": (r"\b(?:programming language|language|compiler|goroutine|goroutines|golang|go\.mod|package main)\b",),
    "r": (r"\b(?:programming language|statistics|statistical|data frame|ggplot|cran|rstudio)\b",),
    "c": (r"\b(?:programming language|compiler|pointer|memory|gcc|clang|stdlib)\b",),
    "java": (r"\b(?:programming language|jvm|bytecode|javac|jdk|spring|classes?)\b",),
    "dart": (r"\b(?:programming language|flutter|dart sdk|pubspec|isolate)\b",),
    "swift": (r"\b(?:programming language|xcode|ios|macos|swiftui|swift package)\b",),
}


def topic_context_matches(topic: str, title: str, body: str = "") -> bool:
    """Rejects lexical collisions for short language names (Go, C, R...)."""
    key = "".join(subject_tokens(topic))
    patterns = _AMBIGUOUS_TOPIC_CONTEXT.get(key)
    if not patterns:
        return True
    material = f"{title} {body}"
    return any(re.search(pattern, material, re.I) for pattern in patterns)


def source_priority(topic: str, page: dict) -> int:
    """Rank likely project publishers before secondary summaries.

    A domain resembling the topic is a discovery signal, never proof that the
    publisher is official. The caller must keep the URL visible to the user.
    """
    url = str(page.get('url') or '')
    parsed = urlparse(url)
    host = _host(url)
    if not host:
        return 0
    topic_parts = subject_tokens(topic)
    compact_host = re.sub(r'[^a-z0-9]', '', host)
    publisher_match = bool(topic_parts) and all(part in compact_host for part in topic_parts)
    title = str(page.get('title') or '').lower()
    path = parsed.path.lower()
    score = 20 if publisher_match else 0
    if re.search(r'\b(?:documentation|reference|manual|guide|docs)\b', title):
        score += 4
    if re.search(r'/(?:docs?|documentation|learn|reference|guide)(?:/|$)', path):
        score += 4
    if parsed.scheme == 'https':
        score += 1
    return score


def assess_sources(topic: str, pages: list[dict]) -> dict:
    """Report corroboration, never a categorical real/fictional verdict.

Two distinct hosts with topical body text provide corroboration. A lone page
is provisional even when it says "official". Fiction markers are warnings,
not proof that the subject itself is fictional.
    """
    relevant = []
    hosts = set()
    independent_texts = set()
    fiction_warnings = []
    seen_urls = set()
    for page in pages:
        if not isinstance(page, dict):
            continue
        title = str(page.get("title") or "")
        url = str(page.get("url") or "")
        body = str(page.get("text") or "")
        host = _host(url)
        if (not host or url in seen_urls or len(body.strip()) < 300
                or not topic_matches(topic, title, url)
                or not topic_context_matches(topic, title, body)):
            continue
        focus = set(subject_tokens(topic))
        # Arquivos internos de um repositório podem não repetir o nome do
        # projeto no corpo; a URL do repositório já fornece a identidade da
        # fonte, enquanto o conteúdo continua exigindo tamanho mínimo.
        if not focus.issubset(set(subject_tokens(body[:5000]))) and not topic_matches(topic, title, url):
            continue
        seen_urls.add(url)
        record = {"title": title, "url": url, "host": host, "priority": source_priority(topic, page)}
        relevant.append(record)
        hosts.add(_organization(host))
        independent_texts.add(re.sub(r"\s+", " ", body.lower()).strip()[:2000])
        if FICTION_MARKERS.search(f"{title} {body[:1500]}"):
            fiction_warnings.append(url)
    relevant.sort(key=lambda item: item['priority'], reverse=True)
    status = "corroborated" if len(hosts) >= 2 and len(independent_texts) >= 2 and not fiction_warnings else "provisional" if relevant else "unverified"
    return {"status": status, "sources": relevant, "independent_hosts": len(hosts),
            "fiction_warnings": fiction_warnings}
