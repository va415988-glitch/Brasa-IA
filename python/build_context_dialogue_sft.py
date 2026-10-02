"""Create contextual SFT variants from the project's local reviewed Q&A.

The variants teach the role/history format. They do not add new facts and must
not be treated as independent evidence of subject-matter competence.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from dialogue import normalize
from finetune_assistant import question_key


ROOT = Path(__file__).resolve().parents[1]
SOURCES = (
    'combined.jsonl', 'behavior_expanded.jsonl', 'behavior_phase1.jsonl',
    'curriculum_apex_v1.jsonl', 'senior_creative_v1.jsonl',
    'agentic_curriculum_v1.jsonl', 'open_programming_curriculum_v1.jsonl',
    'agent_harness_curriculum_v1.jsonl', 'hf_datasets_curriculum_v1.jsonl',
    'deep_learning_book_curriculum_v1.jsonl', 'databricks_genai_curriculum_v1.jsonl',
    'little_book_deep_learning_curriculum_v1.jsonl',
    'godmode_knowledge_v1.jsonl', 'neural_professional_v1.jsonl',
    'godmode_procedures_v1.jsonl',
)


def local_pairs(heldout: Path) -> list[tuple[str, str]]:
    excluded = {question_key(json.loads(line)) for line in heldout.read_text(encoding='utf-8').splitlines() if line}
    excluded.update(normalize(json.loads(line)['prompt']).strip().rstrip('.?! ')
                    for line in (ROOT / 'model' / 'eval_generation.jsonl').read_text(encoding='utf-8').splitlines() if line)
    seen = set()
    pairs = []
    for name in SOURCES:
        path = ROOT / 'python' / 'data' / name
        for line in path.read_text(encoding='utf-8').splitlines():
            row = json.loads(line)
            messages = row.get('messages') or []
            if (len(messages) != 2 or messages[0].get('role') != 'user'
                    or messages[1].get('role') != 'assistant'
                    or messages[1].get('tool_call')):
                continue
            question, answer = str(messages[0].get('content') or '').strip(), str(messages[1].get('content') or '').strip()
            key = question_key(row)
            if (key in seen or key in excluded or len(question) < 15 or len(question) > 220
                    or len(answer) < 40 or len(answer) > 1200 or '```' in answer):
                continue
            seen.add(key)
            pairs.append((question, answer))
    return pairs


def make_rows(pairs: list[tuple[str, str]]) -> list[dict]:
    contexts = (
        ('Estou estudando e quero entender uma dúvida.',
         'Certo. Vou responder ao assunto específico da próxima pergunta.'),
        ('Prefiro uma explicação em português que vá direto ao ponto.',
         'Vou começar pela ideia principal e indicar limites quando existirem.'),
        ('Quero aplicar o que aprender a um caso concreto.',
         'Vou conectar a resposta à pergunta sem presumir detalhes do seu projeto.'),
    )
    rows = []
    for index, (question, answer) in enumerate(pairs):
        for variant, (previous_user, previous_assistant) in enumerate(contexts):
            rows.append({
                'id': f'local-context-qa-{index:04d}-{variant}',
                'domain': 'contextual-local-qa',
                'provenance': 'synthetic-from-local-curated-qa-v1',
                'messages': [
                    {'role': 'user', 'content': previous_user},
                    {'role': 'assistant', 'content': previous_assistant},
                    {'role': 'user', 'content': question},
                    {'role': 'assistant', 'content': answer},
                ],
            })
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--heldout', type=Path, default=ROOT / 'model' / 'training' / 'local-dialogue-v1' / 'heldout.jsonl')
    parser.add_argument('--output', type=Path, default=ROOT / 'python' / 'data' / 'contextual_local_qa_v1.jsonl')
    args = parser.parse_args()
    pairs = local_pairs(args.heldout)
    rows = make_rows(pairs)
    if args.output.exists():
        parser.error(f'Saída já existe: {args.output}')
    args.output.write_text(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in rows), encoding='utf-8')
    print(json.dumps({'source_pairs': len(pairs), 'contextual_rows': len(rows), 'output': str(args.output)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
