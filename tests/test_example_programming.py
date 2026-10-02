import json
import os
import py_compile
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'python'))
from example_programming import implementation_from_examples, matches, observe, one_edit_repair, parse_example_program
from model_server import ModelService
from programming_checks import verify as project_checks


PROMPT = '''Crie converter.py com esta função:

def celsius_para_fahrenheit(c):
    return c * 9 / 5 + 30

Crie tests/test_converter.py com três testes separados:
- 0 °C deve resultar em 32 °F.
- 100 °C deve resultar em 212 °F.
- -40 °C deve resultar em -40 °F.

Execute os testes antes de alterar a função. Depois investigue a falha, corrija o código e execute os testes novamente.
Entregue os arquivos e mostre as evidências das duas execuções.
'''


class ExampleProgrammingTests(unittest.TestCase):
    def test_real_suite_fails_then_passes_and_test_oracle_stays_unchanged(self):
        program = parse_example_program(PROMPT)
        self.assertIsNotNone(program)
        plan = implementation_from_examples(program, set())
        self.assertEqual(plan['operations'][0]['arguments']['content'], program.source)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for operation in plan['operations']:
                args = operation['arguments']; path = root / args['path']
                path.parent.mkdir(parents=True, exist_ok=True); path.write_text(args['content'])
            test_before = (root / program.test_path).read_bytes()
            original_stat = (root / program.path).stat()
            py_compile.compile(str(root / program.path), doraise=True)
            before = project_checks(root, 'unittest')
            self.assertTrue(before['executed'])
            self.assertFalse(before['passed'])
            self.assertEqual(before['tests_executed'], 3)
            self.assertIn('failures=3', before['stderr'])
            repair = one_edit_repair(program, program.source)
            self.assertEqual(repair['operations'][0]['arguments']['new_text'], program.source.replace('+ 30', '+ 32'))
            (root / program.path).write_text(repair['operations'][0]['arguments']['new_text'])
            os.utime(root / program.path, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
            after = project_checks(root, 'unittest')
            self.assertTrue(after['passed'])
            self.assertEqual(after['tests_executed'], 3)
            self.assertEqual(test_before, (root / program.test_path).read_bytes())

    def test_other_paths_names_numeric_constants_and_keyword_arguments(self):
        prompt = '''Crie src/price.py com esta função:
def subtotal(values, bonus=0):
    return sum(values) * 2 + bonus

Crie tests/test_price.py:
- subtotal([2, 7], bonus=1) deve retornar 28
- subtotal([5, -1], bonus=4) deve retornar 16
- subtotal([0, 4], bonus=-3) deve retornar 9
Corrija após executar os testes.
'''
        program = parse_example_program(prompt)
        self.assertIsNotNone(program)
        repaired = one_edit_repair(program, program.source)['operations'][0]['arguments']['new_text']
        self.assertIn('* 3', repaired)
        for case in program.cases:
            self.assertTrue(matches(observe(repaired, program.function, case), case['expected']))

    def test_comparison_operator_repair_at_boundary(self):
        prompt = '''Crie boundary.py com esta função:
def accepted(x):
    return x < 0

Crie test_boundary.py:
- -0.1 => true
- 0 => true
- 0.1 => false
'''
        program = parse_example_program(prompt)
        self.assertIsNotNone(program)
        self.assertIn('x <= 0', one_edit_repair(program, program.source)['operations'][0]['arguments']['new_text'])

    def test_refuses_ambiguous_examples_instead_of_guessing(self):
        prompt = '''Crie ambiguous.py:
def answer(x):
    return x * 2 + x * 2

Crie test_ambiguous.py:
- 1 => 5
- 2 => 10
- 3 => 15
'''
        program = parse_example_program(prompt)
        self.assertIsNone(one_edit_repair(program, program.source))

    def test_correct_code_needs_no_repair_and_existing_paths_are_preserved(self):
        program = parse_example_program(PROMPT.replace('+ 30', '+ 32'))
        self.assertIsNone(one_edit_repair(program, program.source))
        self.assertIsNone(implementation_from_examples(program, {program.path}))
        self.assertIsNone(implementation_from_examples(program, {program.test_path}))

    def test_incomplete_search_does_not_claim_a_unique_repair(self):
        program = parse_example_program('''Crie answer.py:
def answer(x):
    return 1

Crie test_answer.py:
- 1 => 0
- 2 => 0
''')
        self.assertIsNotNone(one_edit_repair(program, program.source))
        with patch('example_programming.MAX_REPAIR_CANDIDATES', 2):
            self.assertIsNone(one_edit_repair(program, program.source))

    def test_rejects_unsafe_source_paths_incomplete_and_contradictory_examples(self):
        for prompt in [PROMPT.replace('converter.py', '../converter.py'),
                       PROMPT.replace('converter.py', '/tmp/converter.py'),
                       PROMPT.replace('return c * 9 / 5 + 30', "return __import__('os').getcwd()"),
                       PROMPT.replace('celsius_para_fahrenheit(c)', "celsius_para_fahrenheit(c: __import__('os'))"),
                       PROMPT.replace('- -40 °C deve resultar em -40 °F.', '- casos negativos também'),
                       PROMPT.replace('- -40 °C deve resultar em -40 °F.', '- 0 °C deve resultar em 99 °F.')]:
            with self.subTest(prompt=prompt):
                self.assertIsNone(parse_example_program(prompt))

    def test_fenced_source_and_non_ascii_byte_offsets(self):
        program = parse_example_program(PROMPT.replace('def celsius', '```python\ndef celsius').replace('\nCrie tests', '\n```\nCrie tests'))
        self.assertIsNotNone(program)
        source = program.source.replace('    return', '    # implementação térmica\n    return')
        repair = one_edit_repair(program, source)
        self.assertIn('# implementação térmica', repair['operations'][0]['arguments']['new_text'])
        self.assertIn('+ 32', repair['operations'][0]['arguments']['new_text'])

    def test_agent_prepares_proposal_without_neural_json_and_recovers_newly_written_file(self):
        service = ModelService('benchmark-only', trace_path=None)
        service.local_reply = lambda *a, **k: self.fail('Explicit examples must not require neural JSON')
        inspection = {'workspace': '/tmp/fixture', 'files': [], 'directories': []}
        proposal = service.proactive_implementation_proposal(PROMPT, [], inspection)
        self.assertEqual(proposal['tool_call']['tool'], 'apply_batch')
        self.assertEqual(proposal['agent']['planner_source'], 'symbolic-example-programming/v1')
        self.assertTrue(proposal['tool_call']['requires_approval'])
        results = [
            {'tool': 'apply_batch', 'ok': True, 'data': {'operations': [
                {'tool': 'create_file', 'result': {'path': 'converter.py'}},
                {'tool': 'create_file', 'result': {'path': 'tests/test_converter.py'}}]}},
            {'tool': 'read_file', 'ok': True, 'data': {'path': 'converter.py',
                'content': parse_example_program(PROMPT).source}},
        ]
        repair = service.proactive_implementation_proposal(PROMPT, results, inspection,
            repair_context={'path': 'converter.py', 'diagnosis': {'check': 'unittest', 'category': 'assertion'}})
        self.assertEqual(repair['tool_call']['tool'], 'propose_repair')
        self.assertEqual(repair['tool_call']['arguments']['path'], 'converter.py')
        self.assertIn('+ 32', repair['tool_call']['arguments']['new_text'])

    def test_diagnosis_reads_the_confirmed_source_not_the_failing_test(self):
        service = ModelService('benchmark-only', trace_path=None)
        outputs = [
            {'tool': 'inspect_project', 'ok': True, 'data': {'files': [], 'test_files': []}},
            {'tool': 'apply_batch', 'ok': True, 'data': {'operations': [
                {'tool': 'create_file', 'result': {'path': 'converter.py'}}]}},
            {'tool': 'project_checks', 'ok': True, 'data': {'executed': True, 'passed': False}},
            {'tool': 'diagnose_project', 'ok': True, 'data': {'category': 'assertion', 'summary': 'Three failing assertions'}},
        ]
        messages = [{'role': 'user', 'content': PROMPT}] + [
            {'role': 'tool', 'content': json.dumps(row)} for row in outputs]
        response = service.continue_after_tool(messages, PROMPT, objective='build')
        self.assertEqual(response['tool_call']['tool'], 'read_file')
        self.assertEqual(response['tool_call']['arguments']['path'], 'converter.py')


if __name__ == '__main__':
    unittest.main()
