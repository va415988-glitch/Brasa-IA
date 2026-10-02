"""Bounded web research for unfamiliar implementation requests."""

import re
import unicodedata


_PRODUCT = re.compile(r'\b(?:aplicativo|aplicacao|app|sistema|plataforma|site|servico|ferramenta|'
                      r'api|dashboard|assistente|interface|frontend|backend|jogo|biblioteca|'
                      r'framework|microservico)\b', re.I)
_NO_WEB = re.compile(r'\b(?:sem|nao)\s+(?:(?:usar|acessar|consultar|acesso\s+a)\s+)?(?:internet|rede|pesquisa\s+web|'
                     r'consultar\s+a\s+web)\b', re.I)
_PATH = re.compile(r'(?<!\w)(?:/[\w. +~-]+){2,}|\b[A-Za-z]:\\[^\s]+')
_SECRET = re.compile(r'\b[A-Za-z0-9_-]{32,}\b')


def should_research_build(question, results, recipe=None):
    """One investigation per build when no compatible local recipe exists."""
    folded = ''.join(char for char in unicodedata.normalize('NFKD', question.casefold())
                     if not unicodedata.combining(char))
    if recipe is not None or _NO_WEB.search(folded):
        return False
    if any(item.get('tool') == 'research_web' for item in results):
        return False
    return bool(_PRODUCT.search(folded))


def build_research_query(question, inspected):
    """Send the task topic, without local paths or long pasted code, to Brave."""
    prompt = re.sub(r'```[\s\S]*?```', ' ', question)
    prompt = _PATH.sub(' ', prompt)
    prompt = _SECRET.sub(' ', prompt)
    prompt = re.sub(r'\s+', ' ', prompt).strip()
    words = prompt.split()[:55]
    summary = ' '.join(words)[:390].strip(' ,.;:')
    stack = [str(item.get('path') if isinstance(item, dict) else item)
             for item in (inspected.get('manifests') or [])[:3]]
    stack = [path.rsplit('/', 1)[-1] for path in stack if path]
    suffix = ' '.join(stack)[:80]
    query = f'{summary} {suffix} official documentation implementation examples'.strip()
    return ' '.join(query.split()[:72])[:590]


def build_research_queries(question, inspected):
    """Cover the product request, chosen technology and verification in three bounded searches."""
    primary = build_research_query(question, inspected)
    cleaned = _PATH.sub(' ', _SECRET.sub(' ', re.sub(r'```[\s\S]*?```', ' ', question)))
    named = re.findall(r'\b(?:em|com|usando|stack|framework|linguagem)\s+'
                       r'([A-Z][\w.+#-]+(?:\s+[A-Z][\w.+#-]+)?)', cleaned)
    manifests = [str(item.get('path') if isinstance(item, dict) else item).rsplit('/', 1)[-1]
                 for item in (inspected.get('manifests') or [])[:3]]
    technologies = ' '.join(dict.fromkeys(named + manifests))[:100]
    if not technologies:
        return [primary]
    return [primary,
            f'{technologies} official documentation setup architecture API examples',
            f'{technologies} official testing persistence error handling documentation']


def research_evidence(results, max_pages=3):
    """Only opened pages with source URLs can ground a code proposal."""
    pages = []
    seen = set()
    for item in results:
        if item.get('tool') != 'research_web' or item.get('ok') is False:
            continue
        data = item.get('data') or {}
        for page in data.get('pages') or []:
            if not isinstance(page, dict):
                continue
            url = str(page.get('url') or '')
            content = str(page.get('text') or '').strip()
            if not url.startswith(('https://', 'http://')) or not content or url in seen:
                continue
            seen.add(url)
            pages.append({'title': str(page.get('title') or 'Documentação')[:160],
                          'url': url[:1000], 'excerpt': content[:1800],
                          'provider': (data.get('providers_used') or ['unreported'])[0]})
            if len(pages) >= max_pages:
                return pages
    return pages
