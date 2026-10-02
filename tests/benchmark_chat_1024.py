#!/usr/bin/env python3
"""Gate de 1.024 perguntas inéditas para o chat local completo.

As 768 perguntas de conhecimento reformulam 256 assuntos presentes no acervo.
Isto mede robustez a novas formulações, não conhecimento de tópicos inéditos.
Os outros 256 casos são cenários compostos gerados com oráculos próprios.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
import time
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for candidate in (str(ROOT), str(ROOT / 'python')):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

from model_server import ModelService, normalize

DATASETS = (
    'combined', 'behavior_expanded', 'behavior_phase1', 'curriculum_apex_v1',
    'senior_creative_v1', 'agentic_curriculum_v1', 'open_programming_curriculum_v1',
    'agent_harness_curriculum_v1', 'neural_gate_focus_v1',
    'hf_datasets_curriculum_v1', 'deep_learning_book_curriculum_v1',
    'databricks_genai_curriculum_v1', 'little_book_deep_learning_curriculum_v1',
)


def known_examples() -> list[tuple[str, str]]:
    seen = set()
    examples = []
    for dataset in DATASETS:
        for line in (ROOT / 'python' / 'data' / f'{dataset}.jsonl').read_text(encoding='utf-8').splitlines():
            messages = json.loads(line)['messages']
            if len(messages) < 2 or messages[0]['role'] != 'user' or messages[1]['role'] != 'assistant':
                continue
            if messages[1].get('tool_call'):
                continue
            question, answer = messages[0]['content'].strip(), messages[1]['content'].strip()
            key = normalize(question)
            if (key not in seen and len(question) >= 16 and len(answer) >= 40
                    and re.match(r'^(?:como|o que|por que|qual|explique|quando|defina)\b', key)):
                seen.add(key)
                examples.append((question, answer))
    return examples


def reformulations(question: str) -> list[str]:
    base = question.strip().rstrip(' .?!')
    patterns = [
        (r'^o que [eé] (.+)$', lambda stem: [
            f'Defina {stem} de modo claro.',
            f'Como você explicaria {stem} a alguém iniciante?',
            f'Em termos práticos, o que significa {stem}?',
        ]),
        (r'^o que (.+)$', lambda stem: [
            f'Explique o que {stem}.',
            f'Em termos práticos, o que {stem}?',
            f'Ajude-me a entender o que {stem}.',
        ]),
        (r'^como (.+)$', lambda stem: [
            f'Você pode explicar como {stem}?',
            f'Descreva, com foco no essencial, como {stem}.',
            f'Na prática, como {stem}?',
        ]),
        (r'^por que (.+)$', lambda stem: [
            f'Qual é a razão de {stem}?',
            f'Explique por que {stem}.',
            f'Qual o motivo pelo qual {stem}?',
        ]),
        (r'^explique (.+)$', lambda stem: [
            f'O que preciso saber sobre {stem}?',
            f'Ajude-me a compreender {stem}.',
            f'Descreva {stem} em termos práticos.',
        ]),
        (r'^quando (.+)$', lambda stem: [
            f'Em que situação {stem}?',
            f'Explique quando {stem}.',
            f'Qual é o momento em que {stem}?',
        ]),
        (r'^qual (.+)$', lambda stem: [
            f'Explique qual {stem}.',
            f'Na prática, qual {stem}?',
            f'Responda de forma direta: qual {stem}?',
        ]),
        (r'^defina (.+)$', lambda stem: [
            f'O que significa {stem}?',
            f'Explique o conceito de {stem}.',
            f'Descreva {stem} para um iniciante.',
        ]),
    ]
    for pattern, rewrite in patterns:
        match = re.match(pattern, base, re.I)
        if match:
            return rewrite(match.group(1))
    raise ValueError(f'Pergunta sem padrão: {question}')


def build_cases() -> list[dict]:
    examples = known_examples()
    dependent = {
        'O que mudou nesta resposta?', 'Explique para quem está começando.',
        'Explique para alguém experiente.', 'Qual é o próximo passo recomendado?',
        'Explique isso de forma mais simples.', 'O que você precisa para continuar?',
        'Explique uma pergunta simples.', 'O que você consegue fazer?',
    }
    # Tópicos sem referente e duas respostas sobre o mesmo assunto não formam
    # oráculos independentes para uma pergunta de turno único.
    examples = [(question, answer) for question, answer in examples if question not in dependent]
    topic_signatures = set()
    distinct = []
    for question, answer in examples:
        signature = frozenset(ModelService._retrieval_content_terms(question))
        if signature not in topic_signatures:
            topic_signatures.add(signature)
            distinct.append((question, answer))
    examples = distinct
    random.Random(20260927).shuffle(examples)
    selected = examples[:256]
    if len(selected) < 256:
        raise RuntimeError(f'Acervo insuficiente: {len(selected)} assuntos elegíveis')
    cases = []
    for topic_id, (source, answer) in enumerate(selected):
        for variant, question in enumerate(reformulations(source)):
            cases.append({'id': f'knowledge-{topic_id:03d}-{variant}', 'category': 'knowledge',
                          'messages': [{'role': 'user', 'content': question}],
                          'reference': answer, 'source_question': source})

    app_goals = [
        'organizar tarefas', 'compartilhar receitas', 'registrar livros', 'planejar viagens',
        'acompanhar estudos', 'agendar consultas', 'cuidar de plantas', 'gerenciar voluntários',
        'mostrar eventos locais', 'organizar coleções', 'controlar despesas', 'trocar objetos',
        'gerir uma biblioteca', 'acompanhar exercícios', 'marcar reuniões', 'catalogar filmes',
    ]
    for index in range(64):
        goal = app_goals[index % len(app_goals)]
        prefix = ('Quero criar', 'Gostaria de criar', 'Preciso construir', 'Vamos desenvolver')[index // 16]
        cases.append({'id': f'ambiguous-{index:03d}', 'category': 'ambiguity',
                      'messages': [{'role': 'user', 'content': f'{prefix} um aplicativo para {goal}.'}]})

    products = [
        'café artesanal', 'livraria de bairro', 'oficina de bicicletas', 'padaria local',
        'curso de fotografia', 'marca de chás', 'feira de produtores', 'estúdio de cerâmica',
        'brechó infantil', 'escola de música', 'jardim comunitário', 'serviço de entregas',
        'loja de plantas', 'clube de leitura', 'museu regional', 'marca de cadernos',
    ]
    for index in range(64):
        product = products[index % len(products)]
        prefix = ('Crie', 'Proponha', 'Sugira', 'Crie')[index // 16]
        quantity = '3' if index >= 48 else 'três'
        cases.append({'id': f'creative-{index:03d}', 'category': 'creative',
                      'messages': [{'role': 'user', 'content':
                                   f'{prefix} {quantity} conceitos de campanha bem diferentes para {product}.'}],
                      'subject': product})

    for index in range(64):
        left = 13 + index * 7
        right = 5 + index * 3
        cases.append({'id': f'arithmetic-{index:03d}', 'category': 'arithmetic',
                      'messages': [{'role': 'user', 'content': f'Quanto é {left} + {right}?'}],
                      'expected': str(left + right)})

    names = [f'ZirconFable{1000 + index}' for index in range(64)]
    for index, name in enumerate(names):
        cases.append({'id': f'unknown-{index:03d}', 'category': 'unknown',
                      'messages': [{'role': 'user', 'content': f'Como funciona o framework {name}?'}],
                      'subject': name})

    original = {normalize(question) for question, _ in known_examples()}
    prompts = [normalize(case['messages'][-1]['content']) for case in cases]
    assert len(cases) == 1024 and len(set(prompts)) == 1024
    assert not original.intersection(prompts), 'Uma pergunta de avaliação coincide com o acervo'
    return cases


def evaluate(case: dict, response: dict) -> tuple[bool, dict]:
    text = str(response.get('text') or '').strip()
    backend = str(response.get('backend') or '')
    category = case['category']
    if category == 'knowledge':
        reference = case['reference']
        ratio = SequenceMatcher(None, normalize(text), normalize(reference), autojunk=False).ratio()
        words = set(normalize(text).split())
        expected = set(normalize(reference).split())
        overlap = len(words & expected) / max(1, len(expected))
        return ratio >= 0.78 and overlap >= 0.7, {'similarity': round(ratio, 3), 'term_recall': round(overlap, 3)}
    if category == 'ambiguity':
        ok = ('?' in text and bool(re.search(r'\b(?:qual|quais|quem|plataforma|público|publico)\b', text, re.I))
              and not re.search(r'\b(?:conclu[ií]|implementei|finalizei)\b', text, re.I))
        return bool(ok), {}
    if category == 'creative':
        options = re.findall(r'(?m)^\s*[123][.)]\s+', text)
        subject = normalize(case['subject'])
        return (len(options) >= 3 and subject in normalize(text) and backend != 'quality-gate'), {'options': len(options)}
    if category == 'arithmetic':
        prompt = case['messages'][-1]['content']
        operands = re.search(r'(\d+)\s*\+\s*(\d+)', prompt)
        expression = bool(operands and re.fullmatch(
            rf'{operands.group(1)}\s*\+\s*{operands.group(2)}\s*=\s*{case["expected"]}[.]?', text
        ))
        return text.strip(' .') == case['expected'] or expression, {}
    if category == 'unknown':
        call = response.get('tool_call') or {}
        return (call.get('tool') == 'research_web' and
                case['subject'] in str(call.get('arguments') or {}) and
                bool(re.search(r'(?:não tenho evidência|investigar|pesquisar)', text, re.I))), {}
    raise AssertionError(category)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--checkpoint', type=Path, default=ROOT / 'model' / 'godmode' / 'context-32768-v1' / 'candidate.safetensors')
    parser.add_argument('--report', type=Path, default=ROOT / 'model' / 'chat_1024_benchmark_report.json')
    parser.add_argument('--limit', type=int)
    args = parser.parse_args()
    cases = build_cases()
    if args.limit is not None:
        cases = cases[:args.limit]
    service = ModelService(args.checkpoint, trace_path=None)
    rows = []
    for index, case in enumerate(cases, 1):
        started = time.perf_counter()
        response = service.reply(case['messages'])
        passed, checks = evaluate(case, response)
        rows.append({'id': case['id'], 'category': case['category'], 'question': case['messages'][-1]['content'],
                     'backend': response.get('backend'), 'passed': passed, 'checks': checks,
                     'response': str(response.get('text') or '')[:1200],
                     'elapsed_ms': round((time.perf_counter() - started) * 1000, 2)})
        if index % 128 == 0:
            print(f'{index}/{len(cases)} avaliadas; {sum(row["passed"] for row in rows)} aprovadas', flush=True)
    by_category = {category: {'passed': sum(row['passed'] for row in rows if row['category'] == category),
                              'total': sum(row['category'] == category for row in rows)}
                   for category in sorted({row['category'] for row in rows})}
    report = {'version': 'chat-1024/v1', 'mode': 'full-local-chat',
              'scope': '768 reformulações de 256 assuntos conhecidos; 256 cenários compostos novos',
              'checkpoint': str(args.checkpoint), 'local_model_loaded': service.local_model is not None,
              'passed': sum(row['passed'] for row in rows), 'total': len(rows),
              'by_category': by_category, 'backends': dict(Counter(row['backend'] for row in rows)),
              'cases': rows}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({key: value for key, value in report.items() if key != 'cases'}, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report['passed'] == report['total'] else 1)


if __name__ == '__main__':
    main()
