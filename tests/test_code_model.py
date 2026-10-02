import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'python'))

from model_server import ModelService


class LocalCodePlanTests(unittest.TestCase):
    def setUp(self):
        self.service = ModelService('benchmark-only', trace_path=None)
        self.empty_workspace = {'workspace': '/tmp/teste', 'files': [], 'directories': []}

    def test_own_checkpoint_proposes_files_through_approval_gate(self):
        plan = {'assumptions': ['Python sem dependências externas.'], 'operations': [
            {'tool': 'create_file', 'arguments': {'path': 'app.py', 'content': 'def dobro(n):\n    return n * 2\n'}},
            {'tool': 'create_file', 'arguments': {'path': 'test_app.py', 'content': 'from app import dobro\nassert dobro(3) == 6\n'}},
        ]}
        with patch.object(self.service, 'local_reply', return_value=json.dumps(plan)) as generate:
            result = self.service.proactive_implementation_proposal(
                'Crie uma função dobro com teste.', [], self.empty_workspace,
            )
        self.assertEqual(result['tool_call']['tool'], 'apply_batch')
        self.assertTrue(result['tool_call']['requires_approval'])
        self.assertEqual(result['agent']['planner_source'], 'project-neural-checkpoint')
        self.assertEqual(result['tool_call']['arguments']['operations'], plan['operations'])
        generate.assert_called_once()

    def test_own_checkpoint_cannot_propose_path_outside_workspace(self):
        plan = {'assumptions': [], 'operations': [
            {'tool': 'create_file', 'arguments': {'path': '../outside.py', 'content': 'pass\n'}},
        ]}
        with patch.object(self.service, 'local_reply', return_value=json.dumps(plan)) as generate:
            result = self.service.proactive_implementation_proposal(
                'Crie um programa para calcular primos.', [], self.empty_workspace,
            )
        self.assertEqual(result['agent']['status'], 'blocked')
        self.assertFalse(result.get('tool_call'))
        self.assertEqual(generate.call_count, 2)

    def test_repair_requires_exact_observed_source(self):
        plan = {'assumptions': [], 'operations': [
            {'tool': 'edit_file', 'arguments': {
                'path': 'app.py', 'old_text': 'return left - right',
                'new_text': 'return left + right',
            }},
        ]}
        results = [{'tool': 'read_file', 'ok': True, 'data': {
            'path': 'app.py', 'content': 'def add(left, right):\n    return left - right\n',
        }}]
        repair = {'path': 'app.py', 'diagnosis': {'category': 'test_failure', 'check': 'pytest'}}
        with patch.object(self.service, 'local_reply', return_value=json.dumps(plan)):
            result = self.service.proactive_implementation_proposal(
                'Conserte a soma.', results,
                {'workspace': '/tmp/teste', 'files': [{'path': 'app.py'}]}, repair_context=repair,
            )
        self.assertEqual(result['tool_call']['tool'], 'propose_repair')
        self.assertEqual(result['tool_call']['arguments']['old_text'], 'return left - right')
        self.assertEqual(result['agent']['planner_source'], 'project-neural-checkpoint')

    def test_unavailable_checkpoint_blocks_unverified_plan(self):
        with patch.object(self.service, 'local_reply', return_value=None) as own:
            result = self.service.proactive_implementation_proposal(
                'Crie um programa Python que mostre o número 42.', [], self.empty_workspace,
            )
        self.assertEqual(result['agent']['status'], 'blocked')
        self.assertFalse(result.get('tool_call'))
        self.assertEqual(own.call_count, 2)

    def test_python_plan_must_include_discoverable_tests(self):
        plan = {'assumptions': [], 'operations': [
            {'tool': 'create_directory', 'arguments': {'path': 'src'}},
            {'tool': 'create_file', 'arguments': {'path': 'src/app.py', 'content': 'def add(a, b):\n    return a + b\n'}},
            {'tool': 'create_file', 'arguments': {'path': 'src/test_app.py', 'content': 'assert True\n'}},
        ]}
        with patch.object(self.service, 'local_reply', return_value=json.dumps(plan)) as generate:
            result = self.service.proactive_implementation_proposal(
                'Crie uma função Python de soma com testes.', [], self.empty_workspace,
            )
        self.assertEqual(result['agent']['status'], 'blocked')
        self.assertFalse(result.get('tool_call'))
        self.assertEqual(generate.call_count, 2)


if __name__ == '__main__':
    unittest.main()
