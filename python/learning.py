"""Aprendizado sob demanda: coleta fontes, registra evidências e atualiza o índice local.

Não altera pesos do modelo. As fontes são tratadas como dados, nunca como instruções.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from build_knowledge_index import tokens, usable_row, topic_matches, subject_tokens
from source_evidence import assess_sources, source_priority
from agent_state import AgentState, canonical_topic as state_canonical_topic
from skill_lab import run as run_skill_lab
from curriculum import build as build_curriculum, gaps as curriculum_gaps


ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "corpus" / "clean" / "knowledge.jsonl"
INDEX = ROOT / "corpus" / "index" / "knowledge.json"
RAW = ROOT / "corpus" / "raw" / "learned_topics.jsonl"
TRAINING_SOURCES = ROOT / "config" / "training_sources.json"
JOBS: dict[str, dict] = {}
LOCK = threading.Lock()
AGENT_STATE = AgentState()


def curated_repository_urls(topic: str) -> list[str]:
    """Retorna poucos repositórios curados para complementar a documentação.

    O catálogo é apenas uma lista de pontos de partida. O conteúdo continua
    sujeito à abertura, validação de origem e avaliação do laboratório.
    """
    try:
        catalog = json.loads(TRAINING_SOURCES.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    entries = catalog.get("sources", {}).get(state_canonical_topic(topic), [])
    return [str(item.get("url", "")).strip() for item in entries if str(item.get("url", "")).startswith("https://github.com/")]


def canonical_topic(topic: str) -> str:
    """Remove instruções de profundidade do nome técnico pesquisável."""
    value = re.sub(r"\s+", " ", str(topic)).strip().strip('"\'“”')
    value = re.sub(r"\b(?:profundamente|a fundo|em profundidade|com profundidade|de forma profunda|detalhadamente)\b", "", value, flags=re.I)
    value = re.sub(r"\s+", " ", value).strip(" .,:;-")
    repo = re.search(r"github\.com/([^/]+)/([^/#?]+)", value, re.I)
    if repo:
        owner, slug = repo.groups()
        slug = slug.removesuffix('.git')
        # Nomes genéricos perdem a identidade do material quando viram
        # apenas "book", "website" ou "tour". Mantemos o namespace nesses
        # casos para evitar colisões entre repositórios diferentes.
        value = f"{owner}/{slug}" if slug.lower() in {"book", "website", "tour", "docs", "examples"} else slug
    # URLs de documentação devem atualizar a competência da tecnologia,
    # não criar um cartão novo para cada página da mesma trilha.
    return state_canonical_topic(value or str(topic).strip())


def source_url_from_topic(topic: str) -> str | None:
    match = re.search(r"https?://[^\s<>\"']+", str(topic))
    return match.group(0).rstrip('.,);') if match else None


TECHNOLOGY_RULES = {
    "JavaScript": {
        "explicit": r"\bjavascript\b",
        "paths": r"\.(?:js|mjs|cjs)$",
        "strong": r"\b(?:require\s*\(|import\s+.+\s+from|const\s+\w+\s*=|function\s+\w+)\b",
    },
    "TypeScript": {
        "explicit": r"\btypescript\b",
        "paths": r"\.(?:ts|tsx)$|(?:^|/)tsconfig(?:\.json)?$",
        "strong": r"\b(?:interface\s+\w+|type\s+\w+\s*=|as\s+(?:string|number|boolean))\b",
    },
    "Node.js": {
        "explicit": r"\bnode(?:\.js)?\b",
        "paths": r"\.(?:js|mjs|cjs)$",
        "strong": r"\b(?:node:|process\.env|require\s*\(|module\.exports|npm\s+(?:run|test))",
    },
    "Python": {
        "explicit": r"\bpython(?:3)?\b",
        "paths": r"\.py$",
        "strong": r"\b(?:def\s+\w+\s*\(|from\s+\w+\s+import|pytest|asyncio)\b",
    },
    "Rust": {
        "explicit": r"\brust\b",
        "paths": r"\.rs$|(?:^|/)cargo\.toml$",
        "strong": r"\b(?:fn\s+main\s*\(|impl\s+\w+|cargo\s+(?:test|build))\b",
    },
    "React": {
        "explicit": r"\breact(?:\.js)?\b",
        "paths": r"\.(?:jsx|tsx)$",
        "strong": r"\b(?:useState|useEffect|createElement)\b|from\s+['\"]react['\"]",
    },
    "SQL": {
        "explicit": r"\bsql\b",
        "paths": r"\.sql$|(?:^|/)(?:migrations?|schema)(?:/|$)",
        "strong": r"\b(?:select\s+.+\s+from|create\s+table|insert\s+into|inner\s+join)\b",
    },
    "C#": {
        "explicit": r"(?<!\w)c#(?!\w)|\bcsharp\b",
        "paths": r"\.(?:cs|csproj|sln)$",
        "strong": r"\b(?:using\s+System|namespace\s+\w+|public\s+class\s+\w+)\b",
    },
    "Java": {
        "explicit": r"\bjava\b",
        "paths": r"\.java$",
        "strong": r"\b(?:public\s+class\s+\w+|System\.out\.println|@Override)\b",
    },
    "C++": {
        "explicit": r"\bc\+\+\b|\bcplusplus\b",
        "paths": r"\.(?:cpp|cc|cxx|hpp|h)$",
        "strong": r"#include\s*<[^>]+>|\b(?:std::|template\s*<)\b",
    },
    "HTML/CSS": {
        "explicit": r"\b(?:html|css)\b",
        "paths": r"\.(?:html?|css|scss)$",
        "strong": r"<!(?:doctype)|</?[a-z][^>]*>|\.[a-z][\w-]*\s*\{",
    },
    "Machine Learning": {
        "explicit": r"\b(?:machine learning|deep learning)\b",
        "paths": r"(?:^|/)(?:models?|datasets?|notebooks?)(?:/|$)",
        "strong": r"\b(?:scikit-learn|sklearn|tensorflow|pytorch|keras|fit\s*\(|train(?:ing)?\s+model)\b",
    },
    "Axum": {
        "explicit": r"\baxum\b",
        "paths": r"\.rs$|(?:^|/)cargo\.toml$",
        "strong": r"\b(?:Router|State|Json|Path|Query|IntoResponse|middleware)\b",
    },
}

# Para linguagens e frameworks, texto de README e snippets são contexto, não
# evidência de que o projeto realmente ensina aquela tecnologia. A origem de
# uma competência derivada precisa conter um artefato estrutural rastreável.
ARTIFACT_REQUIRED = {
    "JavaScript", "TypeScript", "Node.js", "Python", "Rust", "React",
    "SQL", "C#", "Java", "C++", "HTML/CSS", "Machine Learning", "Axum",
}


def technology_evidence(documents: list[dict]) -> dict[str, dict]:
    """Pontua sinais por arquivo, evitando transformar menções em domínio.

    Um nome citado uma vez em README é apenas vocabulário do projeto. Para
    criar uma competência derivada de repositório exigimos um artefato forte
    (extensão, manifesto ou sintaxe) ou duas fontes textuais independentes.
    """
    evidence = {}
    for name, rules in TECHNOLOGY_RULES.items():
        score = 0
        explicit_documents = 0
        strong_documents = 0
        path_documents = 0
        supporting_urls = []
        for page in documents:
            url = str(page.get("url") or page.get("source") or "")
            path = urlparse(url).path.lower()
            title = str(page.get("title") or "")
            text = str(page.get("text") or "")
            identity = f"{title} {url}"
            page_score = 0
            if re.search(rules["explicit"], identity + " " + text, re.I):
                page_score += 1
                explicit_documents += 1
            auxiliary_path = re.search(r"/(?:scripts?|tools?|build|ci)(?:/|$)", path, re.I)
            if re.search(rules["paths"], path, re.I) and not auxiliary_path:
                page_score += 4
                strong_documents += 1
                path_documents += 1
            elif re.search(rules["strong"], text, re.I):
                page_score += 3
                strong_documents += 1
            if page_score:
                score += min(page_score, 5)
                if url:
                    supporting_urls.append(url)
        # Menções repetidas em README, comentários ou strings continuam sendo
        # vocabulário. Competência derivada de repositório exige pelo menos um
        # artefato estrutural/sintático específico.
        # Tags HTML em README/Markdown não comprovam que o repositório ensina
        # HTML/CSS. Para essa categoria exigimos um arquivo estrutural real.
        eligible = score >= 3 and strong_documents >= 1
        if name in ARTIFACT_REQUIRED:
            eligible = eligible and path_documents >= 1
        if eligible:
            evidence[name] = {
                "score": score,
                "explicit_documents": explicit_documents,
                "strong_documents": strong_documents,
                "sources": list(dict.fromkeys(supporting_urls))[:12],
            }
    return evidence


def infer_technologies(documents: list[dict]) -> list[str]:
    """Retorna somente tecnologias com sinais estruturais suficientes."""
    return list(technology_evidence(documents))


def snapshot(job_id: str) -> dict | None:
    with LOCK:
        job = JOBS.get(job_id)
        return json.loads(json.dumps(job, ensure_ascii=False)) if job else None


def update(job_id: str, progress: int, message: str, **fields) -> None:
    with LOCK:
        job = JOBS[job_id]
        job.update(fields)
        job["progress"] = progress
        job["logs"].append({"at": round(time.time()), "message": message})


def start(topic: str) -> dict:
    source_url = source_url_from_topic(topic)
    topic = canonical_topic(topic)
    if not 1 <= len(topic) <= 100 or any(ch in topic for ch in "\r\n\0"):
        raise ValueError("Informe um tema entre 1 e 100 caracteres.")
    job_id = uuid.uuid4().hex
    with LOCK:
        JOBS[job_id] = {"id": job_id, "topic": topic, "status": "running", "progress": 0,
                        "logs": [{"at": round(time.time()), "message": "Preparando pesquisa."}],
                        "source_url": source_url,
                        "sources": [], "documents_added": 0}
    AGENT_STATE.learning_started(topic)
    threading.Thread(target=_run, args=(job_id, topic, source_url), daemon=True).start()
    return snapshot(job_id)


def _research(query: str, topic: str, source_url: str | None = None) -> dict:
    body = json.dumps({"query": query, "topic": topic, "source_url": source_url,
                       "max_results": 3, "save_to_corpus": False}).encode()
    request = Request("http://127.0.0.1:3000/api/v1/research", data=body,
                      headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(request, timeout=45) as response:
        result = json.load(response)
    if not result.get("ok"):
        raise RuntimeError(result.get("error") or "pesquisa indisponível")
    return result.get("data") or {}


def _rebuild_index(rows: list[dict]) -> None:
    from collections import Counter, defaultdict
    import math

    rows = [row for row in rows if usable_row(row)]
    postings = defaultdict(list)
    frequencies = Counter()
    for index, row in enumerate(rows):
        for term in set(tokens(row["text"])):
            postings[term].append(index)
            frequencies[term] += 1
    total = max(1, len(rows))
    index = {"documents": rows, "postings": postings,
             "idf": {term: math.log((1 + total) / (1 + frequency)) + 1
                     for term, frequency in frequencies.items()}}
    INDEX.parent.mkdir(parents=True, exist_ok=True)
    temporary = INDEX.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
    temporary.replace(INDEX)


def persist_pages(pages: list[dict], topic: str, category: str = "proactive-learning", search_query: str | None = None) -> int:
    """Persiste páginas já coletadas e reconstrói o índice de forma atômica."""
    query = search_query or topic
    evidence = assess_sources(topic, pages) if category.startswith('learned/') else None
    allowed = {item['url'] for item in evidence['sources']} if evidence else None
    warned = set(evidence['fiction_warnings']) if evidence else set()
    with LOCK:
        rows = [json.loads(line) for line in CORPUS.read_text(encoding="utf-8").splitlines() if line.strip()] if CORPUS.exists() else []
        known_hashes = {row.get("sha256") for row in rows}
        known_urls = {row.get("url") for row in rows}
        fresh = []
        for page in sorted(pages, key=lambda item: source_priority(topic, item), reverse=True):
            content = re.sub(r"\s+", " ", str(page.get("text") or "")).strip()[:12000]
            url = str(page.get("url") or "")
            if len(content) < 300 or not url.startswith(("https://", "http://")) or url in known_urls:
                continue
            if allowed is not None and (url not in allowed or url in warned):
                continue
            title = str(page.get("title") or topic)[:200]
            if not topic_matches(query, title, url, require_all=False):
                continue
            if not (set(subject_tokens(query)) & set(subject_tokens(content))):
                continue
            text = f"{title}\n\n{content}"
            digest = hashlib.sha256(text.encode()).hexdigest()
            if digest in known_hashes:
                continue
            row = {"id": f"doc-{digest[:16]}", "text": text, "source": url, "url": url,
                   "license": "unknown-review-required", "language": "und",
                   "category": category, "topic": topic, "search_query": query, "sha256": digest}
            if evidence:
                row['evidence_status'] = evidence['status']
                row['evidence_policy'] = 2
            fresh.append(row)
            known_hashes.add(digest)
            known_urls.add(url)
        if not fresh:
            return 0
        CORPUS.parent.mkdir(parents=True, exist_ok=True)
        RAW.parent.mkdir(parents=True, exist_ok=True)
        with CORPUS.open("a", encoding="utf-8") as output, RAW.open("a", encoding="utf-8") as raw:
            for row in fresh:
                line = json.dumps(row, ensure_ascii=False) + "\n"
                output.write(line)
                raw.write(line)
        rows.extend(fresh)
        _rebuild_index(rows)
        return len(fresh)


def _run(job_id: str, topic: str, source_url: str | None = None) -> None:
    try:
        pages = []
        errors = []
        search_plan = (f"{topic} official documentation", None, f"{topic} official project site documentation",
                       f"{topic} reference guide {topic.lower().replace(' ', '-')}.org")
        total_searches = len(search_plan)
        catalog_repositories = [] if source_url else curated_repository_urls(topic)
        for number, query in enumerate(search_plan, 1):
            progress = min(70, 10 + round(number * 60 / total_searches))
            relevant_urls = {item['url'] for item in assess_sources(topic, pages)['sources']}
            has_project_domain = any(page.get('url') in relevant_urls and source_priority(topic, page) >= 20
                                     for page in pages)
            if number == 2:
                query = (f"{topic} documentation examples reference" if has_project_domain
                         else f"{topic} official project site documentation")
            update(job_id, progress, f"Pesquisando fontes para: {query}")
            try:
                # A primeira rodada abre um repositório curado para trazer
                # código, testes e exemplos; as demais continuam pesquisando
                # documentação e fontes independentes.
                repository = catalog_repositories[0] if number == 1 and catalog_repositories else None
                selected_source = source_url or repository
                result = _research(query, topic, selected_source) if selected_source else _research(query, topic)
                pages.extend(page for page in result.get("pages", []) if page.get("text"))
                update(job_id, progress, f"Pesquisa {number}/{total_searches}: {len(result.get('pages', []))} página(s) abertas; {len(result.get('search_results', []))} resultado(s) encontrados.")
                for attempt in (result.get('attempts') or [])[:8]:
                    target = str(attempt.get('url') or attempt.get('query') or '')[:150]
                    outcome = str(attempt.get('status') or 'desconhecido')
                    detail = str(attempt.get('error') or '')[:120]
                    update(job_id, progress, f"Fonte {outcome}: {target}" + (f" · {detail}" if detail else ''))
            except Exception as error:
                errors.append(str(error))
                update(job_id, progress, f"Pesquisa {number}/{total_searches} falhou: {error}")
        update(job_id, 75, "Validando conteúdo, origem e removendo duplicatas.")
        preliminary = assess_sources(topic, pages)
        allowed = {item['url'] for item in preliminary['sources']}
        warned = set(preliminary['fiction_warnings'])
        reviewed = []
        seen_urls = set()
        for page in sorted(pages, key=lambda item: source_priority(topic, item), reverse=True):
            url = str(page.get('url') or '')
            if url in allowed and url not in warned and url not in seen_urls:
                reviewed.append(page)
                seen_urls.add(url)
        reviewed = reviewed[:48] if source_url else reviewed[:6]
        evidence = assess_sources(topic, reviewed)
        update(job_id, 82, f"{len(reviewed)} fonte(s) passaram na validação; {evidence['independent_hosts']} origem(ns) distinta(s).")
        with LOCK:
            rows = [json.loads(line) for line in CORPUS.read_text(encoding="utf-8").splitlines() if line.strip()] if CORPUS.exists() else []
            known = {row.get("sha256") for row in rows}
            known_urls = {row.get("url") for row in rows}
            fresh = []
            accepted = []
            for page in reviewed:
                content = re.sub(r"\s+", " ", str(page.get("text") or "")).strip()[:12000]
                url = str(page.get("url") or "")
                if len(content) < 300 or not url.startswith(("https://", "http://")):
                    continue
                title = str(page.get("title") or topic)[:200]
                if not topic_matches(topic, title, url):
                    continue
                accepted.append(page)
                text = f"{title}\n\n{content}"
                digest = hashlib.sha256(text.encode()).hexdigest()
                if digest in known or url in known_urls:
                    continue
                known.add(digest)
                known_urls.add(url)
                fresh.append({"id": f"doc-{digest[:16]}", "text": text, "source": url,
                              "url": url, "license": "unknown-review-required", "language": "und",
                              "category": f"learned/{topic}", "topic": topic, "sha256": digest,
                              "evidence_status": evidence['status'], "evidence_policy": 2})
            if fresh:
                CORPUS.parent.mkdir(parents=True, exist_ok=True)
                RAW.parent.mkdir(parents=True, exist_ok=True)
                with CORPUS.open("a", encoding="utf-8") as output, RAW.open("a", encoding="utf-8") as raw:
                    for row in fresh:
                        line = json.dumps(row, ensure_ascii=False) + "\n"
                        output.write(line)
                        raw.write(line)
                rows.extend(fresh)
                _rebuild_index(rows)
        if not pages:
            update(job_id, 100, f"Nenhuma fonte legível foi obtida após {len(search_plan)} consultas e recuperação direta. O acervo não foi alterado.",
                   status="failed", error="; ".join(errors) or "Nenhuma fonte legível.")
            return
        if not accepted:
            update(job_id, 100, "As páginas não sustentam o tema com conteúdo verificável; nada foi aprendido.",
                   status="failed", error="Nenhuma página relevante e legível foi encontrada.", evidence=evidence)
            return
        sources = [{"title": page.get("title"), "url": page.get("url")} for page in accepted]
        confidence = 'fontes independentes' if evidence['status'] == 'corroborated' else 'evidência provisória; confirme a origem antes de usar'
        detected_evidence = technology_evidence(reviewed) if source_url else {}
        detected = list(detected_evidence)
        skill = AGENT_STATE.record_research(
            topic, sources=sources, independent_hosts=evidence['independent_hosts'],
            documents=len(accepted), status=evidence['status'],
            gaps=curriculum_gaps(curriculum := build_curriculum(topic, reviewed)),
            curriculum=curriculum, source_repository=source_url,
            technologies=detected,
            technology_evidence=detected_evidence,
        )
        technology_pages = {technology: list(reviewed) for technology in detected}
        technology_curricula = {}
        if source_url and detected:
            update(job_id, 85, f"Ampliando a pesquisa com documentação oficial para {len(detected)} tecnologia(s) detectada(s).")
            for technology in detected:
                try:
                    official = _research(f"{technology} official documentation", technology).get('pages', [])
                    official = [page for page in official if page.get('text')]
                    if official:
                        technology_pages[technology].extend(official)
                        persist_pages(official, technology, category=f"learned/{technology}",
                                      search_query=f"{technology} official documentation")
                        update(job_id, 86, f"Documentação oficial adicionada para {technology}: {len(official)} página(s).")
                    else:
                        update(job_id, 86, f"Nenhuma documentação oficial adicional legível para {technology}; mantendo a evidência do repositório.")
                except Exception as error:
                    update(job_id, 86, f"Pesquisa oficial de {technology} falhou; competência permanece parcial: {error}")
        for technology in detected:
            tech_documents = technology_pages.get(technology, reviewed)
            tech_curriculum = build_curriculum(technology, tech_documents)
            technology_curricula[technology] = tech_curriculum
            tech_evidence = assess_sources(technology, tech_documents)
            tech_sources = [{"title": page.get("title"), "url": page.get("url")}
                            for page in tech_documents if page.get("url")]
            tech_sources = list({item["url"]: item for item in tech_sources}.values())
            if not tech_sources:
                tech_sources = sources
            AGENT_STATE.record_research(
                technology, sources=tech_sources,
                independent_hosts=max(evidence['independent_hosts'], tech_evidence['independent_hosts']),
                documents=len(tech_sources),
                status=tech_evidence['status'] if tech_evidence['status'] != 'unverified' else evidence['status'],
                gaps=curriculum_gaps(tech_curriculum), curriculum=tech_curriculum,
                source_repository=source_url, technologies=[technology],
                technology_evidence={technology: detected_evidence[technology]},
            )
        update(job_id, 90, "Iniciando laboratório prático isolado; nenhum código baixado será executado.")
        practice_topics = detected or [topic]
        laboratories = {}
        for practice_topic in practice_topics:
            practice_curriculum = curriculum if practice_topic == topic else technology_curricula.get(
                practice_topic, build_curriculum(practice_topic, technology_pages.get(practice_topic, reviewed)))
            current_lab = run_skill_lab(practice_topic, curriculum=practice_curriculum)
            laboratories[practice_topic] = current_lab
            for task in current_lab.get('tasks', []):
                if task.get('recovery_scheduled'):
                    update(job_id, 94, f"{practice_topic}: falha classificada em {task['name']}; recuperação agendada.")
                elif task.get('recovery_attempt'):
                    update(job_id, 97, f"{practice_topic}: executando recuperação de {task['name']}.")
                updated_skill = AGENT_STATE.record_practice(
                    practice_topic, task=task['name'], passed=bool(task.get('passed')),
                    evidence=f"{' '.join(task.get('command', []))}; {task.get('diagnosis', '')}",
                    level=task.get('level'))
                if practice_topic == topic:
                    skill = updated_skill
        statuses = [item['status'] for item in laboratories.values()]
        summary_status = ('failed' if 'failed' in statuses else 'recovered' if 'recovered' in statuses
                          else 'partial' if 'verified' in statuses and 'practice_unavailable' in statuses
                          else 'verified' if statuses and all(status == 'verified' for status in statuses)
                          else 'practice_unavailable')
        lab = {'status': summary_status, 'topic': topic, 'skills': list(laboratories),
               'laboratories': laboratories, 'total': sum(item.get('total', 0) for item in laboratories.values()),
               'passed': sum(item.get('passed', 0) for item in laboratories.values())}
        gaps = skill['concepts']['gaps']
        failed_gaps = [f"{name}/{task.get('level', 'practice')}: {task.get('diagnosis')}"
                       for name, result in laboratories.items() for task in result.get('tasks', []) if not task.get('passed')]
        gaps = list(dict.fromkeys(gaps + failed_gaps))
        if lab['status'] == 'practice_unavailable':
            gaps = list(dict.fromkeys(gaps + ['executor local seguro para prática']))
        update(job_id, 100, f"Pesquisa e avaliação concluídas; competência registrada como {skill['status']} (não dominada).",
               status="completed", sources=sources, documents_added=len(fresh), evidence=evidence,
               skill=skill, laboratory=lab, gaps=gaps)
    except Exception as error:
        update(job_id, 100, f"Falha ao atualizar o acervo: {error}", status="failed", error=str(error))
