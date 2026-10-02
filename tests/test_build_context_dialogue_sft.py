import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'python'))
from build_context_dialogue_sft import local_pairs, make_rows
from finetune_assistant import question_key


def test_context_builder_excludes_reserved_questions_and_keeps_provenance():
    root = Path(__file__).resolve().parents[1]
    heldout = root / 'model' / 'training' / 'local-dialogue-v1' / 'heldout.jsonl'
    reserved = {question_key(json.loads(line)) for line in heldout.read_text(encoding='utf-8').splitlines() if line}
    pairs = local_pairs(heldout)
    rows = make_rows(pairs)
    assert len(pairs) >= 200
    assert len(rows) == 3 * len(pairs)
    assert all(question_key(row) not in reserved for row in rows)
    assert all(row['provenance'] == 'synthetic-from-local-curated-qa-v1' for row in rows)
    assert all([message['role'] for message in row['messages']] ==
               ['user', 'assistant', 'user', 'assistant'] for row in rows)
