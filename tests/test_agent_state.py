import json
import tempfile
import unittest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'python'))
from agent_state import AgentState


class AgentStateTests(unittest.TestCase):
    def test_variants_are_canonicalized_in_persistent_view(self):
        with tempfile.TemporaryDirectory() as directory:
            state = AgentState(Path(directory) / 'skills.json')
            state.learning_started('Rust profundamente')
            state.learning_started('Rust')
            self.assertEqual(list(state.all()), ['Rust'])

    def test_delete_removes_all_topic_variants(self):
        with tempfile.TemporaryDirectory() as directory:
            state = AgentState(Path(directory) / 'skills.json')
            state.learning_started('Rust profundamente')
            state.learning_started('Rust')
            self.assertEqual(state.delete('Rust'), 1)
            self.assertEqual(state.all(), {})

    def test_clear_removes_all_skills(self):
        with tempfile.TemporaryDirectory() as directory:
            state = AgentState(Path(directory) / 'skills.json')
            state.learning_started('Rust')
            state.learning_started('Python')
            self.assertEqual(state.clear(), 2)
            self.assertEqual(state.all(), {})
    def test_research_creates_partial_competence_not_mastery(self):
        with tempfile.TemporaryDirectory() as directory:
            state = AgentState(Path(directory) / 'skills.json')
            state.learning_started('Rust', version='1.86')
            skill = state.record_research(
                'Rust', sources=[{'url': 'https://rust-lang.org'}],
                independent_hosts=1, documents=1, status='provisional',
                gaps=['ownership', 'prática executável'])
            self.assertEqual(skill['status'], 'partially_known')
            self.assertLess(skill['confidence'], 0.6)
            self.assertEqual(skill['concepts']['gaps'], ['ownership', 'prática executável'])

    def test_research_preserves_repository_origin_and_technologies(self):
        with tempfile.TemporaryDirectory() as directory:
            state = AgentState(Path(directory) / 'skills.json')
            skill = state.record_research(
                'JavaScript', sources=[{'url': 'https://github.com/example/repo'}],
                independent_hosts=1, documents=1, status='provisional',
                source_repository='https://github.com/example/repo', technologies=['JavaScript'])
            self.assertEqual(skill['source_repository'], 'https://github.com/example/repo')
            self.assertEqual(skill['technologies'], ['JavaScript'])

    def test_practice_approval_closes_matching_curriculum_gap(self):
        with tempfile.TemporaryDirectory() as directory:
            state = AgentState(Path(directory) / 'skills.json')
            state.learning_started('Python')
            state.record_research('Python', sources=[], independent_hosts=0, documents=0,
                                  status='provisional', gaps=['dados-e-controle'],
                                  curriculum={'levels': [{'concepts': [{'id': 'dados-e-controle', 'status': 'pending'}]}]})
            skill = state.record_practice('Python', task='foundation-control-flow', passed=True)
            self.assertIn('dados-e-controle', skill['concepts']['covered'])
            self.assertNotIn('dados-e-controle', skill['concepts']['gaps'])

    def test_confidence_recalculates_after_verified_practice(self):
        with tempfile.TemporaryDirectory() as directory:
            state = AgentState(Path(directory) / 'skills.json')
            state.record_research('JavaScript', sources=[{'url': 'https://example.org/docs'}] * 12,
                                  independent_hosts=1, documents=12, status='provisional')
            before = state.get('JavaScript')['confidence']
            for index in range(5):
                state.record_practice('JavaScript', task=f'task-{index}', passed=True)
            after = state.get('JavaScript')['confidence']
            self.assertGreater(after, before)

    def test_source_urls_are_not_counted_as_validated_documents(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'skills.json'
            state = AgentState(path)
            state.record_research('Python', sources=[{'url': 'https://example.org/docs'}] * 4,
                                  independent_hosts=1, documents=0, status='provisional')
            stored = json.loads(path.read_text(encoding='utf-8'))['skills']['Python']
            self.assertEqual(stored['evidence']['documents'], 0)
            self.assertEqual(state.all()['Python']['evidence']['documents'], 0)
            stored = json.loads(path.read_text(encoding='utf-8'))['skills']['Python']
            self.assertEqual(stored['evidence']['documents'], 0)

    def test_mastery_requires_repeated_verified_practice_and_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            state = AgentState(Path(directory) / 'skills.json')
            state.learning_started('Rust')
            curriculum = {
                'levels': [
                    {'id': 'foundation', 'concepts': [{'id': 'foundation', 'status': 'covered'}]},
                    {'id': 'practice', 'concepts': [{'id': 'practice', 'status': 'covered'}]},
                    {'id': 'transfer', 'concepts': [{'id': 'transfer', 'status': 'covered'}]},
                ],
                'completion': {
                    'required_levels': 3, 'required_pass_rate': 0.9,
                    'required_independent_hosts': 2, 'minimum_document_count': 2,
                    'minimum_practice_tasks': 5, 'minimum_transfer_tasks': 2,
                    'required_covered_ratio': 0.9, 'requires_integration_task': True,
                },
            }
            state.record_research(
                'Rust', sources=[{'url': 'https://rust-lang.org'}, {'url': 'https://doc.rust-lang.org'}],
                independent_hosts=2, documents=2, status='corroborated', curriculum=curriculum)
            for number in range(5):
                skill = state.record_practice('Rust', task=f'task-{number}', passed=True,
                                              evidence='cargo test', level='transfer' if number >= 3 else 'practice')
            skill = state.record_practice('Rust', task='integration-project', passed=True,
                                          evidence='cargo test', level='integration')
            self.assertEqual(skill['status'], 'mastered')
            self.assertGreaterEqual(skill['practice']['passed'], 5)

    def test_failed_practice_prevents_mastery(self):
        with tempfile.TemporaryDirectory() as directory:
            state = AgentState(Path(directory) / 'skills.json')
            state.record_research('Zig', sources=[{'url': 'https://ziglang.org'}],
                                  independent_hosts=2, documents=3, status='corroborated')
            for number in range(5):
                skill = state.record_practice('Zig', task=f'task-{number}', passed=number < 3)
            self.assertNotEqual(skill['status'], 'mastered')


if __name__ == '__main__':
    unittest.main()
