"""Regressões do ciclo real de continuação, sem carregar pesos neurais."""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'python'))
from model_server import ModelService
from tool_registry import ToolRegistry


class ExecutionGatesTests(unittest.TestCase):
    def setUp(self):
        self.service = ModelService.__new__(ModelService)
        self.service.tools = ToolRegistry()

    def continue_with(self, tool, data, **extra):
        result = {'tool': tool, 'ok': True, 'data': data, **extra}
        return self.service.continue_after_tool([
            {'role': 'user', 'content': 'Crie o projeto'},
            {'role': 'tool', 'content': json.dumps(result)},
        ], 'Crie o projeto')

    def test_empty_research_cannot_complete_or_persist(self):
        response = self.continue_with('research_web', {'pages': [], 'category': 'proactive-learning'})
        self.assertEqual(response['agent']['stop_reason'], 'empty_research')
        self.assertEqual(response['agent']['status'], 'blocked')

    def test_checks_not_executed_cannot_pass(self):
        response = self.continue_with('project_checks', {'executed': False, 'passed': True})
        self.assertFalse(response['agent']['verified'])
        self.assertEqual(response['agent']['status'], 'blocked')

    def test_write_always_requests_verification(self):
        response = self.continue_with('create_file', {'path': 'app.py'}, verification_done=True)
        self.assertEqual(response['tool_call']['tool'], 'project_checks')

    def test_new_user_request_does_not_replay_old_tool(self):
        messages = [{'role': 'tool', 'content': '{"tool":"research_web"}'},
                    {'role': 'user', 'content': 'Prossiga com a criação'}]
        self.assertEqual(ModelService._tool_results(messages), [])

    def test_invalid_result_stops(self):
        response = self.continue_with('read_file', ['unexpected'])
        self.assertEqual(response['agent']['status'], 'failed')

    def test_budget_stops_even_successful_tools(self):
        messages = [{'role': 'tool', 'content': json.dumps({'tool': 'read_file', 'ok': True, 'data': {}})}] * 32
        response = self.service.continue_after_tool(messages, 'Leia o projeto')
        self.assertEqual(response['agent']['stop_reason'], 'step_budget')
