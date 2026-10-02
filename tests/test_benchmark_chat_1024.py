"""Check the large chat gate and its negative oracles."""

from collections import Counter

from benchmark_chat_1024 import build_cases, evaluate, known_examples
from model_server import ModelService, normalize


def test_large_gate_has_distinct_unseen_wordings_and_declared_categories():
    cases = build_cases()
    questions = [normalize(case['messages'][-1]['content']) for case in cases]
    originals = {normalize(question) for question, _ in known_examples()}
    assert len(cases) == len(set(questions)) == 1024
    assert not originals.intersection(questions)
    assert Counter(case['category'] for case in cases) == {
        'knowledge': 768, 'ambiguity': 64, 'creative': 64,
        'arithmetic': 64, 'unknown': 64,
    }


def test_large_gate_rejects_wrong_answer_and_fake_uncertainty():
    cases = build_cases()
    arithmetic = next(case for case in cases if case['category'] == 'arithmetic')
    unknown = next(case for case in cases if case['category'] == 'unknown')
    ambiguous = next(case for case in cases if case['category'] == 'ambiguity')
    assert not evaluate(arithmetic, {'text': '13 + 5 = 17.', 'backend': 'deterministic-reasoning'})[0]
    assert not evaluate(unknown, {'text': 'É um framework de rotas.', 'backend': 'local-neural'})[0]
    assert not evaluate(ambiguous, {'text': 'Concluí o aplicativo.', 'backend': 'local-task'})[0]


def test_curated_retrieval_does_not_ignore_new_subject_terms():
    service = ModelService('unused', trace_path=None)
    assert service.curated_concept_answer('Você pode explicar como testar Rust com cargo test?')
    assert service.curated_concept_answer('Você pode explicar como testar Rust com cargo desconhecido999?') is None
