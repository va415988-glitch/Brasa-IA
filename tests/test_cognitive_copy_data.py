from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'python'), str(ROOT / 'scripts')]
from cognitive_dialogue import build_frame, validate_decision
from conditioned_context import conditioned_prompt
from evaluate_cognitive_sft import evidence_oracle, score_decision
from prepare_cognitive_copy import cases_for, copy_tokenizer, ENTITIES_V3
from prepare_cognitive_sft import cases_for as previous_cases
from select_cognitive_sft import validate_splits
from tool_registry import ToolRegistry


class CognitiveCopyDataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows = cases_for('validation')

    def test_all_targets_match_independent_input_oracle(self):
        registry = ToolRegistry()
        for case in self.rows:
            with self.subTest(case=case['id']):
                frame = build_frame(case['request_messages'], case['cognition'], registry)
                value = validate_decision(case['messages'][-1]['content'], frame, registry)
                self.assertTrue(all(score_decision(value, evidence_oracle(case)).values()))

    def test_same_field_from_another_entity_does_not_ground_answer(self):
        case = next(row for row in self.rows if row['domain'] == 'unrelated')
        self.assertEqual(evidence_oracle(case)['decision'], 'blocked')
        self.assertEqual(evidence_oracle(case)['cause'], 'missing_field')

    def test_reordered_revision_requires_correct_source_id(self):
        rows = [row for row in self.rows if row['domain'] == 'revision']
        refs = {tuple(evidence_oracle(row)['evidence_ids']) for row in rows}
        self.assertEqual(refs, {('obs-1',), ('obs-2',)})

    def test_complete_nested_path_and_url_are_copied_by_oracle(self):
        case = next(row for row in self.rows if row['domain'] == 'consult'
                    and 'cfg/' in row['request_messages'][0]['content'])
        self.assertTrue(evidence_oracle(case)['tool_call']['arguments']['path'].startswith('cfg/'))
        case = next(row for row in self.rows if row['domain'] == 'recover')
        self.assertTrue(evidence_oracle(case)['tool_call']['arguments']['url'].endswith('/config'))

    def test_new_reserved_entities_do_not_overlap_previous_experiment(self):
        previous = {row['entity'] for row in previous_cases('heldout', version=2)}
        self.assertFalse(previous & set(ENTITIES_V3['heldout']))
        validate_splits(cases_for('train'), self.rows, cases_for('heldout'))

    def test_role_marker_survives_whole_prompt_encoding(self):
        rows = self.rows[:20]
        texts = [conditioned_prompt(row['messages'][:-1]) + row['messages'][-1]['content'] for row in rows]
        tokenizer = copy_tokenizer(texts, {row['entity'] for row in rows})
        marker = tokenizer.encode_fast('<|assistant|>\n')
        for row in rows:
            ids = tokenizer.encode_fast(conditioned_prompt(row['messages'][:-1]))
            self.assertEqual(ids[-len(marker):], marker)


if __name__ == '__main__':
    unittest.main()
