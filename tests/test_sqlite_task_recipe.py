import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'python'))
from fullstack_gate import assess_fullstack_plan
from implementation_recipes import fallback_implementation_plan
from model_server import ModelService
from proactive_implementation import parse_implementation_plan


REQUEST = (
    'Crie neste workspace uma aplicação web local de tarefas para uma pessoa, sem login nem serviços externos. '
    'Deve permitir cadastrar tarefas, listar, concluir e excluir. Persista os dados em SQLite pelo backend, '
    'valide títulos vazios e inclua testes para as operações e para a persistência. '
    'Se já houver uma stack no workspace, use-a; se estiver vazio, escolha uma stack local simples e explique a escolha. '
    'Antes de escrever arquivos, mostre um plano curto com os principais arquivos e testes e peça minha aprovação. '
    'Depois implemente, execute os testes, corrija falhas com base nas saídas reais e rode os testes novamente. '
    'No fim, informe como iniciar a aplicação e quais verificações passaram. Não diga que executou algo se não executou.'
)


class SQLiteTaskRecipeTests(unittest.TestCase):
    @unittest.skipUnless((Path(__file__).resolve().parents[1] / 'runtime/target/debug/local_ai_runtime').exists(),
                         'build the local runtime before running this integration test')
    def test_real_runtime_writes_detects_and_verifies_proposal(self):
        runtime = Path(__file__).resolve().parents[1] / 'runtime/target/debug/local_ai_runtime'
        service = ModelService('benchmark-only', trace_path=None)
        with tempfile.TemporaryDirectory(prefix='brasa-runtime-sqlite-') as directory:
            def execute(tool, arguments):
                process = subprocess.run([str(runtime)], cwd=directory,
                    input=json.dumps({'tool': tool, 'arguments': arguments}) + '\n',
                    capture_output=True, text=True, timeout=40)
                self.assertEqual(process.returncode, 0, process.stderr)
                result = json.loads(process.stdout)
                self.assertTrue(result['ok'], result)
                return {'role': 'tool', 'content': json.dumps(result)}
            messages = [{'role': 'user', 'content': REQUEST}, execute('inspect_project', {})]
            proposal = service.reply(messages, objective='build')
            call = proposal.get('tool_call') or {}
            self.assertEqual(call.get('tool'), 'apply_batch', proposal)
            self.assertTrue(call['requires_approval'])
            self.assertEqual(list(Path(directory).iterdir()), [])
            messages.append(execute(call['tool'], call['arguments']))
            verification = service.reply(messages, objective='build')['tool_call']
            self.assertEqual(verification['tool'], 'project_checks')
            messages.append(execute(verification['tool'], verification['arguments']))
            evidence = json.loads(messages[-1]['content'])['data']
            self.assertEqual(evidence['check'], 'unittest')
            self.assertTrue(evidence['executed'])
            self.assertTrue(evidence['passed'], evidence)
            self.assertIn('Ran 4 tests', evidence['stderr'])
            final = service.reply(messages, objective='build')
            self.assertEqual(final['agent']['status'], 'completed')
            self.assertIn('python3 app.py', final['text'])
            self.assertIn('Como iniciar', final['text'])

    def test_original_request_proposes_then_runs_real_http_and_sqlite_tests(self):
        service = ModelService('benchmark-only', trace_path=None)
        with tempfile.TemporaryDirectory(prefix='brasa-sqlite-test-') as directory:
            root = Path(directory)
            messages = [{'role': 'user', 'content': REQUEST}, {'role': 'tool', 'content': json.dumps({
                'tool': 'inspect_project', 'ok': True, 'data': {
                    'workspace': directory, 'files': [], 'directories': [],
                    'manifests': [], 'entrypoints': [], 'test_files': [],
                },
            })}]
            with patch.object(service, 'local_reply', side_effect=AssertionError('compatible recipe must avoid neural generation')):
                response = service.reply(messages, objective='build')
            call = response.get('tool_call') or {}
            self.assertEqual(call.get('tool'), 'apply_batch', response.get('text'))
            self.assertTrue(call['requires_approval'])
            self.assertEqual(response['agent']['planner_source'], 'deterministic-recipe:todo-sqlite-web')
            self.assertEqual(list(root.iterdir()), [], 'proposal must not write before approval')
            plan = parse_implementation_plan(json.dumps({
                'assumptions': response['agent']['assumptions'], 'operations': call['arguments']['operations'],
            }))
            self.assertTrue(assess_fullstack_plan(plan)['passed'])
            observed = []
            # Isolated harness executes the proposed files after asserting the approval boundary.
            for operation in plan['operations']:
                self.assertEqual(operation['tool'], 'create_file')
                args = operation['arguments']
                path = root / args['path']
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(args['content'], encoding='utf-8')
                observed.append({'tool': 'create_file', 'result': {'path': args['path']}})
            messages.append({'role': 'tool', 'content': json.dumps({
                'tool': 'apply_batch', 'ok': True, 'data': {'count': len(observed), 'operations': observed},
            })})
            next_step = service.reply(messages, objective='build')
            self.assertEqual(next_step.get('tool_call', {}).get('tool'), 'project_checks', next_step)
            command = [sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-v']
            execution = subprocess.run(command, cwd=root, capture_output=True, text=True, timeout=30)
            self.assertEqual(execution.returncode, 0, execution.stdout + execution.stderr)
            self.assertIn('Ran 4 tests', execution.stderr)
            messages.append({'role': 'tool', 'content': json.dumps({
                'tool': 'project_checks', 'ok': True, 'data': {
                    'check': 'unittest', 'command': ' '.join(command), 'executed': True,
                    'passed': execution.returncode == 0, 'exit_code': execution.returncode,
                    'stdout': execution.stdout, 'stderr': execution.stderr,
                },
            })})
            final = service.reply(messages, objective='build')
            self.assertEqual(final['agent']['status'], 'completed', final)
            self.assertTrue(final['agent']['verified'])
            self.assertIn('Ran 4 tests', final['text'])
            self.assertEqual(set(final['agent']['changed_files']), {item['result']['path'] for item in observed})

    def test_preserves_existing_stack_and_rejects_unimplemented_requirements(self):
        for files in ({'package.json'}, {'app.py'}, {'src/main.rs'}, {'tests/test_app.py'}):
            with self.subTest(files=files):
                self.assertIsNone(fallback_implementation_plan(REQUEST, existing_paths=files))
        for extra in (' Use React.', ' Use Flask.', ' Inclua login.', ' Inclua categorias.', ' Deve editar tarefas.'):
            with self.subTest(extra=extra):
                self.assertIsNone(fallback_implementation_plan(REQUEST + extra))

    def test_existing_readme_is_preserved(self):
        recipe = fallback_implementation_plan(REQUEST, existing_paths={'README.md', '.gitignore'})
        plan = parse_implementation_plan(json.dumps(recipe), existing_paths={'README.md', '.gitignore'})
        paths = [item['arguments']['path'] for item in plan['operations']]
        self.assertIn('TASKS.md', paths)
        self.assertNotIn('README.md', paths)


if __name__ == '__main__':
    unittest.main()
