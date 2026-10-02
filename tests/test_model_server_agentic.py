import json
import sys
import unittest
from unittest.mock import patch
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'python'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from benchmark_neural_generation import checkpoint_reply
from dialogue import is_project_feedback_request, is_project_understanding_request, explicit_workspace_change_request
from implementation_recipes import TODO_CLI_SOURCE
from model_server import ModelService, bounded_workspace_read_arguments, document_read_tool, explicit_file_read_answer, explicit_file_substitution_answer, next_explicit_file_window, project_analysis_read_paths, validate_agent_plan_request
from tool_registry import make_tool_call


class ModelServerAgenticTests(unittest.TestCase):
    def setUp(self):
        self.service = ModelService('benchmark-only', trace_path=None)

    def test_debug_does_not_complete_from_search_success(self):
        question = 'A interface mostra Unexpected token <.'
        messages = [{'role': 'user', 'content': question},
                    {'role': 'tool', 'content': json.dumps({
                        'tool': 'search_files', 'ok': True,
                        'data': {'matches': []},
                    })}]
        response = self.service.continue_after_tool(messages, question, objective='debug')
        self.assertNotEqual(response['agent']['status'], 'completed')
        self.assertFalse(response['agent']['verified'])
        self.assertNotIn('Concluí a etapa', response['text'])

    def test_project_analysis_uses_agent_core_canonical_scope(self):
        inspection = {
            'files': [
                {'path': 'CMakeLists.txt'}, {'path': 'src/main.cpp'},
                {'path': 'src/core/Application.cpp'}, {'path': 'src/ui/EditorLayer.cpp'},
            ],
            'manifests': ['CMakeLists.txt'],
            'entrypoints': ['src/main.cpp'],
            'analysis_candidates': [
                'CMakeLists.txt', 'src/main.cpp', 'src/core/Application.cpp',
            ],
        }
        self.assertEqual(project_analysis_read_paths(inspection), inspection['analysis_candidates'])

    def test_project_analysis_prefers_launch_instructions_over_arbitrary_nested_readme(self):
        inspection = {
            'files': [
                {'path': 'model/README.md', 'bytes': 4000},
                {'path': 'Documentacoes/README.md', 'bytes': 8000},
                {'path': 'python/README.md', 'bytes': 7000},
                {'path': 'start.sh', 'bytes': 9000},
            ],
            'manifests': [],
            'entrypoints': [],
        }
        selected = project_analysis_read_paths(inspection)
        self.assertEqual(selected[0], 'Documentacoes/README.md')
        self.assertIn('start.sh', selected)
        self.assertIn('Documentacoes/README.md', selected)
        self.assertIn('python/README.md', selected)
        self.assertNotIn('model/README.md', selected)

    def test_large_analysis_files_use_byte_paging_instead_of_line_windows(self):
        inspection = {'files': [{'path': 'runtime/src/main.rs', 'bytes': 131073}]}
        self.assertEqual(
            bounded_workspace_read_arguments(inspection, 'runtime/src/main.rs'),
            {'path': 'runtime/src/main.rs', 'offset': 0, 'max_bytes': 8192},
        )

    def test_exact_project_improvement_phrase_is_feedback(self):
        self.assertTrue(is_project_feedback_request('avalie pontos de melhora no projeto atual'))

    def test_agent_context_v2_has_provenance_and_omits_full_personality_prompt(self):
        packet = self.service.build_context(
            [{'role': 'user', 'content': 'Quero organizar entregas da equipe.'}],
            'Quero organizar entregas da equipe.', contract='agent-context-request/v2',
        )
        self.assertEqual(packet['schema'], 'agent-context/v2')
        self.assertEqual(packet['personality_ref'], 'local-personality/v1')
        self.assertNotIn('assistant_profile', packet)
        self.assertEqual(packet['provenance']['evidence'], 'local-knowledge-index')
        self.assertTrue(all('source' in row and 'confidence' in row for row in packet['session_memory']))

    def test_agent_plan_v1_requires_typed_cognition_and_bounded_messages(self):
        body = {
            'schema': 'agent-plan-request/v1', 'request_id': 'plan-1', 'objective': 'build',
            'messages': [{'role': 'user', 'content': 'Crie uma interface.'}],
            'cognition': {'schema': 'agent-cognition/v1', 'task_id': 'task-plan-1',
                          'interpretation': 'Interface', 'personality': {'version': 'local-personality/v1'}},
            'context': {'schema': 'agent-context/v2', 'status': 'ready',
                        'personality_ref': 'local-personality/v1', 'evidence': {'items': []}},
        }
        messages, objective, guidance, context, cognition = validate_agent_plan_request(body)
        self.assertEqual(objective, 'build')
        self.assertEqual(messages[0]['role'], 'user')
        self.assertEqual(guidance, [])
        self.assertIs(context, body['context'])
        self.assertIs(cognition, body['cognition'])
        with self.assertRaisesRegex(ValueError, 'preparação cognitiva'):
            validate_agent_plan_request({**body, 'cognition': {'schema': 'wrong'}})

    def test_model_failure_does_not_answer_request_complaint_with_generic_chat(self):
        failed_dialogue = {
            'ok': False,
            'generation': {
                'quality_gate_result': 'rejected',
                'attempts': [{'provider': 'project-neural-checkpoint', 'status': 'unavailable'}],
            },
        }
        with patch.object(self.service, 'dialogue_turn', return_value=failed_dialogue):
            result = self.service._reply(
                [{'role': 'user', 'content': 'Essa porra simplesmente não funciona. Não entende pedidos.'}],
                objective='conversation',
            )
        self.assertEqual(result['backend'], 'diagnostic-failure-report')
        self.assertIn('resposta não passou pelo controle de qualidade', result['text'])
        self.assertIn('ignorou sua reclamação', result['text'])

    def test_unknown_conversation_never_returns_false_followup(self):
        with patch.object(self.service, 'dialogue_turn', return_value={'ok': False}), \
             patch.object(self.service, 'conversational_reply', return_value=None), \
             patch.object(self.service, 'diagnostic_reasoning_fallback', return_value=None), \
             patch('model_server.deterministic_reasoning_answer', return_value=None):
            result = self.service._reply(
                [{'role': 'user', 'content': 'Pedido desconhecido xqv-mnp-731'}],
                objective='conversation',
            )
        self.assertEqual(result['backend'], 'quality-gate')
        self.assertEqual(result['agent']['status'], 'blocked')
        self.assertIn('tarefa permanece pendente', result['text'])
        self.assertNotIn('Estou acompanhando', result['text'])

    def test_negated_edit_does_not_turn_project_analysis_into_implementation(self):
        prompt = (
            'Analise o projeto Budget Pocket, explique como funciona e que verificação existe. '
            'Não altere nenhum arquivo.'
        )
        self.assertFalse(explicit_workspace_change_request(prompt))
        self.assertTrue(is_project_understanding_request(prompt))

    def test_explicit_change_after_negated_clause_remains_an_implementation_request(self):
        prompt = 'Não altere os arquivos existentes; crie um README com instruções de execução.'
        self.assertTrue(explicit_workspace_change_request(prompt))

    def test_project_analysis_synthesizes_read_evidence_instead_of_building(self):
        prompt = (
            'Analise o projeto Budget Pocket. Leia os arquivos relevantes e me explique o que ele faz, '
            'quais são seus arquivos principais, como posso executar as operações e que verificação existe. '
            'Não altere nenhum arquivo.'
        )
        files = {
            'README.md': (
                '# Budget Pocket\n\nA tiny local command-line helper for recording expenses in expenses.json and reporting the total. '
                'Requires Python 3 and has no third-party dependencies.\n\n'
                'Start with `./start.sh`; corpus maintenance uses `cargo run --bin corpus_ingest`. '
                'Run `python3 expense_app.py add "Lunch" 12.50` to record an expense and '
                '`python3 expense_app.py total` to print the total. '
                'Run checks with `python3 -m unittest discover -s tests`.\n'
            ),
            'expense_app.py': 'def add_expense(description, amount):\n    pass\n\ndef total_expenses():\n    pass\n',
            'tests/test_expense_app.py': 'class ExpenseAppTests(unittest.TestCase):\n    pass\n',
        }

        def tool(name, data):
            return {'role': 'tool', 'tool': name, 'content': json.dumps({
                'tool': name, 'ok': True, 'data': data,
            })}

        messages = [
            {'role': 'user', 'content': prompt},
            tool('set_workspace', {'workspace': '/tmp/BudgetPocket', 'selected': True}),
            tool('inspect_project', {
                'workspace': '/tmp/BudgetPocket',
                'files': list(files), 'directories': ['tests'],
                'manifests': [], 'test_files': ['tests/test_expense_app.py'],
                'entrypoints': [], 'checks': ['python3 -m unittest discover -s tests'],
                'analysis_candidates': list(files),
            }),
        ]
        for path, content in files.items():
            messages.append(tool('read_file', {
                'path': path, 'content': content, 'start_line': 1,
                'end_line': len(content.splitlines()), 'truncated': False,
            }))

        response = self.service.reply(messages, objective='analyze')

        self.assertEqual(response['backend'], 'workspace-evidence-analysis')
        self.assertEqual(response['agent']['status'], 'completed')
        self.assertIn('A tiny local command-line helper', response['text'])
        self.assertIn('expense_app.py', response['text'])
        self.assertIn('python3 expense_app.py add', response['text'])
        self.assertIn('python3 -m unittest discover -s tests', response['text'])
        self.assertIn('Inicialização observada', response['text'])
        self.assertIn('Comandos de uso da aplicação documentados', response['text'])
        self.assertIn('Verificações documentadas', response['text'])
        self.assertNotIn('corpus_ingest', response['text'])
        self.assertNotIn('proposta estruturada', response['text'])

    def test_project_analysis_explains_observed_storage_key_and_state_change(self):
        prompt = ('Leia este aplicativo e explique onde os livros são guardados '
                  'e como o estado de leitura é persistido. Não altere arquivos.')
        source = (ROOT / 'planning/baseline-v1/fixtures/change-project/app.js').read_text(encoding='utf-8')
        readme = (ROOT / 'planning/baseline-v1/fixtures/change-project/README.md').read_text(encoding='utf-8')
        messages = [
            {'role': 'user', 'content': prompt},
            {'role': 'tool', 'content': json.dumps({'tool': 'inspect_project', 'ok': True,
                'data': {'workspace': '/tmp/reading-shelf', 'files': ['README.md', 'app.js'],
                         'manifests': [], 'test_files': [], 'entrypoints': [],
                         'analysis_candidates': ['README.md', 'app.js']}})},
            {'role': 'tool', 'content': json.dumps({'tool': 'read_file', 'ok': True,
                'data': {'path': 'README.md', 'content': readme, 'start_line': 1}})},
            {'role': 'tool', 'content': json.dumps({'tool': 'read_file', 'ok': True,
                'data': {'path': 'app.js', 'content': source, 'start_line': 1}})},
        ]
        response = self.service.reply(messages, objective='analyze')
        self.assertEqual(response['backend'], 'workspace-evidence-analysis')
        self.assertIn('reading-shelf-books', response['text'])
        self.assertIn('app.js:6: let books = JSON.parse(localStorage.getItem(STORAGE_KEY)', response['text'])
        self.assertIn('app.js:9: localStorage.setItem(STORAGE_KEY, JSON.stringify(books))', response['text'])
        self.assertIn('book.read = read.checked', response['text'])

    def test_explicit_file_plan_survives_initial_workspace_inspection(self):
        question = '''Crie estes arquivos:
### app.py
```python
answer = 42
```
### test_app.py
```python
from app import answer
assert answer == 42
```'''
        messages = [{'role': 'user', 'content': question}, {'role': 'tool', 'content': json.dumps({
            'tool': 'inspect_project', 'ok': True,
            'data': {'workspace': '/tmp/projeto', 'files': [], 'directories': []},
        })}]
        response = self.service.reply(messages, objective='build')
        self.assertEqual([call['tool'] for call in response.get('tool_calls', [])],
                         ['create_file', 'create_file'])

    def test_single_explicit_file_plan_survives_initial_workspace_inspection(self):
        question = 'Crie o arquivo notes.txt:\nconteúdo fornecido pelo usuário'
        messages = [{'role': 'user', 'content': question}, {'role': 'tool', 'content': json.dumps({
            'tool': 'inspect_project', 'ok': True,
            'data': {'workspace': '/tmp/projeto', 'files': [], 'directories': []},
        })}]
        response = self.service.reply(messages, objective='build')
        self.assertEqual(response['tool_call']['tool'], 'create_file')
        self.assertEqual(response['tool_call']['arguments'], {
            'path': 'notes.txt', 'content': 'conteúdo fornecido pelo usuário',
        })

    def test_agentcore_build_falls_back_to_validated_proposal_after_inspection_only(self):
        question = 'Crie um projeto pequeno para registrar tarefas.'
        inspection = {'workspace': '/tmp/projeto', 'files': [], 'directories': [],
                      'manifests': [], 'entrypoints': [], 'test_files': []}
        messages = [
            {'role': 'user', 'content': question},
            {'role': 'tool', 'tool': 'inspect_project', 'content': json.dumps({
                'tool': 'inspect_project', 'ok': True, 'data': inspection,
            })},
        ]
        proposal = {'text': 'Proposta pronta para aprovação.', 'backend': 'agent-loop',
                    'tool_call': {'id': 'proposal-1', 'tool': 'apply_batch',
                                  'arguments': {'operations': []}, 'reason': 'Criar o projeto.'}}
        with patch.object(self.service, '_reply', return_value={
            'text': 'Concluí a etapa inspect_project.', 'backend': 'test',
        }), patch.object(self.service, 'proactive_implementation_proposal',
                         return_value=proposal) as proposer:
            response = self.service.reply(messages, objective='build')

        self.assertIs(response['tool_call'], proposal['tool_call'])
        proposer.assert_called_once_with(question, self.service._tool_results(messages), inspection)

    def test_agentcore_debug_report_uses_validated_repair_proposer(self):
        question = ('A interface está mostrando a mensagem: Unexpected token '
                    "'<', \"<!DOCTYPE \"... is not valid JSON\nArquivo ativo: index.html")
        inspection = {'workspace': '/tmp/projeto', 'files': [{'path': 'index.html'}, {'path': 'app.py'}],
                      'directories': [], 'manifests': [], 'entrypoints': ['app.py'], 'test_files': []}
        messages = [
            {'role': 'user', 'content': question},
            {'role': 'tool', 'tool': 'inspect_project', 'content': json.dumps({
                'tool': 'inspect_project', 'ok': True, 'data': inspection,
            })},
            {'role': 'tool', 'tool': 'read_file', 'content': json.dumps({
                'tool': 'read_file', 'ok': True,
                'data': {'path': 'index.html', 'content': "fetch('/api/tasks').then(r => r.json())"},
            })},
        ]
        proposal = {'text': 'Reparo pronto para revisão.', 'backend': 'agent-loop',
                    'tool_call': {'id': 'repair-1', 'tool': 'apply_batch',
                                  'arguments': {'operations': []}, 'reason': 'Corrigir o erro observado.'}}
        with patch.object(self.service, '_reply', return_value={
            'text': 'Estou acompanhando. Pode me contar um pouco mais?', 'backend': 'local-conversation',
        }), patch.object(self.service, 'proactive_implementation_proposal',
                         return_value=proposal) as proposer:
            response = self.service.reply(messages, objective='debug')
        self.assertIs(response['tool_call'], proposal['tool_call'])
        proposer.assert_called_once_with(question, self.service._tool_results(messages), inspection)

    def test_brave_failure_does_not_block_local_implementation(self):
        question = 'A página retornou Unexpected token <, not valid JSON. Corrija o projeto.'
        messages = [{'role': 'user', 'content': question}, {'role': 'tool', 'tool': 'inspect_project',
            'content': json.dumps({'tool': 'inspect_project', 'ok': True, 'data': {
                'workspace': '/tmp/projeto', 'files': [{'path': 'index.html'}],
                'directories': [], 'manifests': [], 'entrypoints': ['index.html'], 'test_files': []}})},
            {'role': 'tool', 'tool': 'research_web', 'content': json.dumps({
                'tool': 'research_web', 'ok': False, 'error': 'API Brave Search está indisponível'})}]
        proposal = {'text': 'Correção local pronta.', 'backend': 'agent-loop',
                    'tool_call': {'id': 'repair', 'tool': 'apply_batch', 'arguments': {'operations': []}}}
        with patch.object(self.service, 'proactive_implementation_proposal', return_value=proposal) as proposer:
            response = self.service.continue_after_tool(messages, question, objective='debug')
        self.assertEqual(response['tool_call']['tool'], 'apply_batch')
        self.assertIn('research_warning', response)
        proposer.assert_called_once()

    def test_concrete_debug_report_skips_preliminary_brave_research(self):
        question = 'A página retornou Unexpected token <, not valid JSON. Corrija o projeto.'
        inspected = {'workspace': '/tmp/projeto', 'files': [{'path': 'index.html'}],
                     'directories': [], 'manifests': [], 'entrypoints': [], 'test_files': []}
        messages = [{'role': 'user', 'content': question}, {'role': 'tool', 'tool': 'inspect_project',
                    'content': json.dumps({'tool': 'inspect_project', 'ok': True, 'data': inspected})}]
        proposal = {'text': 'Correção local pronta.', 'backend': 'agent-loop',
                    'tool_call': {'id': 'repair', 'tool': 'apply_batch', 'arguments': {'operations': []}}}
        with patch.object(self.service, 'proactive_implementation_proposal', return_value=proposal) as proposer:
            response = self.service.continue_after_tool(messages, question, objective='debug')
        self.assertEqual(response['tool_call']['tool'], 'apply_batch')
        proposer.assert_called_once()

    def test_failed_check_after_write_refreshes_source_and_reenters_proposer(self):
        inspection = {'workspace': '/tmp/projeto', 'files': [{'path': 'app.py'}]}
        results = [
            {'tool': 'inspect_project', 'ok': True, 'data': inspection},
            {'tool': 'read_file', 'ok': True, 'data': {'path': 'app.py', 'content': 'old version'}},
            {'tool': 'edit_file', 'ok': True, 'data': {'path': 'app.py', 'updated': True}},
            {'tool': 'project_checks', 'ok': True, 'data': {'executed': True, 'passed': False,
                'stderr': 'app.py:1: NameError: missing'}},
        ]
        def messages():
            return [{'role': 'user', 'content': 'Corrija o erro do projeto.'}] + [
                {'role': 'tool', 'content': json.dumps(item)} for item in results]
        stalled = {'text': 'Sem proposta', 'backend': 'test'}
        proposal = {'text': 'Correção localizada', 'tool_call': {'id': 'fix', 'tool': 'edit_file',
                    'arguments': {'path': 'app.py', 'old_text': 'current version', 'new_text': 'fixed'}}}
        with patch.object(self.service, '_reply', return_value=stalled), patch.object(
                self.service, 'proactive_implementation_proposal', return_value=proposal) as proposer:
            response = self.service.reply(messages(), objective='debug')
            self.assertEqual(response['tool_call']['tool'], 'read_file')
            self.assertEqual(response['tool_call']['arguments']['path'], 'app.py')
            proposer.assert_not_called()
            results.append({'tool': 'read_file', 'ok': True,
                            'data': {'path': 'app.py', 'content': 'current version'}})
            response = self.service.reply(messages(), objective='debug')
            self.assertEqual(response['tool_call']['tool'], 'edit_file')
            evidence = proposer.call_args.args[1]
            self.assertNotIn('old version', json.dumps(evidence))
            self.assertIn('NameError', json.dumps(evidence))
            proposer.reset_mock()
            results.append({'tool': 'project_checks', 'ok': True,
                            'data': {'executed': True, 'passed': True}})
            self.service.reply(messages(), objective='debug')
            proposer.assert_not_called()

    def test_failed_debug_generator_reports_observed_evidence_instead_of_generic_chat(self):
        question = ("A página web retornou: Unexpected token '<', "
                    "\"<!DOCTYPE\" is not valid JSON. Arquivo ativo: index.html")
        inspection = {'workspace': '/tmp/projeto', 'files': [{'path': 'index.html'}],
                      'directories': [], 'manifests': [], 'entrypoints': [], 'test_files': []}
        results = [
            {'tool': 'inspect_project', 'ok': True, 'data': inspection},
            {'tool': 'read_file', 'ok': True, 'data': {'path': 'index.html',
                'content': "fetch('/api/tasks').then(response => response.json())"}},
            {'tool': 'project_checks', 'ok': True,
             'data': {'check': 'unittest', 'executed': True, 'passed': True}},
        ]
        messages = [{'role': 'user', 'content': question}] + [
            {'role': 'tool', 'tool': item['tool'], 'content': json.dumps(item)} for item in results
        ]
        stalled = {'text': 'O gerador local não produziu uma proposta estruturada.',
                   'backend': 'agent-loop',
                   'agent': {'stop_reason': 'implementation_proposal_unavailable'}}
        with patch.object(self.service, '_reply', return_value=stalled), patch.object(
                self.service, 'proactive_implementation_proposal', return_value=stalled):
            response = self.service.reply(messages, objective='debug')
        self.assertEqual(response['backend'], 'debug-evidence-fallback')
        self.assertIn('`index.html`', response['text'])
        self.assertIn('O check inicial passou', response['text'])
        self.assertIn('recebeu conteúdo iniciado por HTML', response['text'])
        self.assertIn('Nenhum arquivo foi alterado', response['text'])
        self.assertNotIn('Pode me contar um pouco mais', response['text'])

    def test_agentcore_test_creation_uses_validated_file_proposer(self):
        question = 'Crie testes para app.py'
        inspection = {'workspace': '/tmp/projeto', 'files': [{'path': 'app.py'}],
                      'directories': [], 'manifests': [], 'entrypoints': ['app.py'], 'test_files': []}
        messages = [
            {'role': 'user', 'content': question},
            {'role': 'tool', 'tool': 'inspect_project', 'content': json.dumps({
                'tool': 'inspect_project', 'ok': True, 'data': inspection,
            })},
            {'role': 'tool', 'tool': 'read_file', 'content': json.dumps({
                'tool': 'read_file', 'ok': True,
                'data': {'path': 'app.py', 'content': 'def soma(a, b): return a + b'},
            })},
        ]
        proposal = {'text': 'Teste pronto para revisão.', 'backend': 'agent-loop',
                    'tool_call': {'id': 'tests-1', 'tool': 'apply_batch',
                                  'arguments': {'operations': []}, 'reason': 'Criar testes.'}}
        with patch.object(self.service, '_reply', return_value={
            'text': 'Concluí a inspeção.', 'backend': 'test',
        }), patch.object(self.service, 'proactive_implementation_proposal',
                         return_value=proposal) as proposer:
            response = self.service.reply(messages, objective='testing')
        self.assertIs(response['tool_call'], proposal['tool_call'])
        proposer.assert_called_once_with(question, self.service._tool_results(messages), inspection)

    def test_empty_workspace_afazeres_request_uses_recipe_before_failed_checkpoint(self):
        prompt = 'Vamos criar uma lista de afazeres'
        inspection = {
            'workspace': '/tmp/projeto', 'files': [], 'directories': [],
            'manifests': [], 'entrypoints': [], 'test_files': [],
        }
        messages = [
            {'role': 'user', 'content': prompt},
            {'role': 'tool', 'tool': 'inspect_project', 'content': json.dumps({
                'tool': 'inspect_project', 'ok': True, 'data': inspection,
            })},
        ]
        with patch.object(self.service, 'local_reply', side_effect=AssertionError(
                'a receita compatível deve evitar o checkpoint')):
            proposal = self.service.reply(messages, objective='build')
        self.assertEqual(proposal['tool_call']['tool'], 'apply_batch')
        self.assertTrue(proposal['tool_call']['requires_approval'])
        self.assertEqual(
            [item['arguments']['path'] for item in proposal['tool_call']['arguments']['operations']],
            ['index.html', 'app.js', 'task-core.js', 'package.json',
             'tests/task-core.test.cjs', 'README.md'],
        )
        self.assertEqual(proposal['agent']['planner_source'], 'deterministic-recipe:todo-web')

    def test_afazeres_inspection_continues_to_proposal_instead_of_web_research(self):
        prompt = 'Vamos criar uma lista de afazeres'
        inspection = {
            'workspace': '/tmp/projeto', 'files': [], 'directories': [],
            'manifests': [], 'entrypoints': [], 'test_files': [],
        }
        messages = [
            {'role': 'user', 'content': prompt},
            {'role': 'tool', 'tool': 'inspect_project', 'content': json.dumps({
                'tool': 'inspect_project', 'ok': True, 'data': inspection,
            })},
        ]
        with patch.object(self.service, 'local_reply', side_effect=AssertionError(
                'a receita compatível deve evitar o checkpoint')):
            result = self.service.continue_after_tool(messages, prompt, objective='build')
        self.assertEqual(result['tool_call']['tool'], 'apply_batch')
        self.assertEqual(result['agent']['planner_source'], 'deterministic-recipe:todo-web')

    def test_directory_description_reads_sources_before_completion(self):
        documents = {
            'CMakeLists.txt': 'project(GameEngineStudio)\nfind_package(OpenGL REQUIRED)',
            'src/main.cpp': '#include "Application.h"\nint main() { Application app; app.Run(); }',
            'src/core/Application.cpp': 'void Application::Run() { window.OnUpdate(); }',
            'src/ui/Window.h': '#include <GLFW/glfw3.h>\nclass Window {};',
        }
        for question in ('descreva o diretório aberto na sessão atual',
                         'Explique a pasta aberta', 'Resuma o diretório atual',
                         'Analise o diretório\nArquivo ativo: src/ui/Window.h · cpp · 26 linhas · cursor na linha 26.',
                         'Analise o diretório\nArquivo ativo: todo_cli.py · python · 120 linhas · cursor na linha 1.'):
            with self.subTest(question=question):
                messages = [{'role': 'user', 'content': question}]
                first = self.service.reply(messages)
                self.assertEqual(first.get('tool_call', {}).get('tool'), 'inspect_project')
                messages.append({'role': 'tool', 'content': json.dumps({
                    'tool': 'inspect_project', 'ok': True, 'data': {
                        'workspace': '/tmp/GameEngineStudio',
                        'files': [{'path': path} for path in documents],
                        'directories': ['src', 'src/core', 'src/ui'],
                        'manifests': ['CMakeLists.txt'], 'entrypoints': ['src/main.cpp'],
                        'checks': ['cmake'], 'test_files': [],
                        'summary': 'Estrutura inspecionada. README ausente.',
                    },
                })})
                read_paths = []
                for _ in range(6):
                    response = self.service.reply(messages)
                    call = response.get('tool_call')
                    if not call:
                        break
                    self.assertEqual(call['tool'], 'read_file')
                    path = call['arguments']['path']
                    self.assertNotIn(path, read_paths)
                    read_paths.append(path)
                    messages.append({'role': 'tool', 'content': json.dumps({
                        'tool': 'read_file', 'ok': True,
                        'data': {'path': path, 'content': documents[path], 'start_line': 1},
                    })})
                self.assertGreaterEqual(len(read_paths), 2)
                self.assertFalse(response.get('tool_call'))
                self.assertEqual(response['agent']['status'], 'completed')
                for expected in ('GameEngineStudio', 'src/core', 'src/ui', 'CMake', 'C/C++', 'Evidências observadas'):
                    self.assertIn(expected, response['text'])
                self.assertNotIn('Concluí a etapa', response['text'])

    def test_noncritical_implementation_requirements_use_defaults_instead_of_questions(self):
        question = 'Quero criar um aplicativo para organizar minhas tarefas.'
        self.assertIsNone(self.service.requirements_gate_reply(question, [{'role': 'user', 'content': question}]))

    def test_reply_forwards_persistent_agent_run_context(self):
        messages = [{'role': 'user', 'content': 'Continue o objetivo original.'}]
        context = {
            'schema': 'agent-run-context/v1', 'objective': 'Objetivo persistente',
            'status': 'recovering', 'acceptance_criteria': [], 'steps_completed': 2,
            'step_budget': 20, 'evidence': [], 'blockers': [], 'recovery_attempt': 1,
            'instruction': 'Preserve o objetivo e reavalie a próxima ação.',
        }
        with patch.object(self.service, '_reply', return_value={
            'text': 'Continuando a tarefa.', 'backend': 'local-conversation',
        }) as generate:
            response = self.service.reply(messages, agent_run_context=context)
        self.assertEqual(response['text'], 'Continuando a tarefa.')
        self.assertEqual(generate.call_args.kwargs['agent_run_context'], context)

    def test_proposal_discussion_uses_dialogue_api_before_fixed_reply(self):
        prompt = (
            'Ao invés de focar somente em datasets, podemos desenvolver uma API para isso, '
            'adaptando-a da melhor maneira à nossa IA?'
        )
        result = {
            'schema': 'agent-dialogue-response/v1', 'ok': True,
            'text': 'Faz sentido; a API pode organizar contexto e provedores, desde que o modelo continue sendo avaliado.',
            'backend': 'dialogue-test', 'intent': 'conversation',
            'dialogue': {'provider': 'fake', 'model': 'test'},
            'generation': {'quality_gate_result': 'accepted'},
        }
        with patch.object(self.service.dialogue_api, 'turn', return_value=result) as turn:
            response = self.service.reply([{
                'role': 'user', 'content': prompt, 'attachments': [],
            }], request_id='dialogue-test-1')

        self.assertEqual(response['backend'], 'dialogue-test')
        self.assertEqual(response['text'], result['text'])
        body = turn.call_args.args[0]
        self.assertEqual(body['schema'], 'agent-dialogue-request/v1')
        self.assertEqual(body['request_id'], 'dialogue-test-1')
        self.assertEqual(body['messages'][-1]['content'], prompt)
        self.assertNotIn('attachments', body['messages'][-1])

    def test_agent_conversation_objective_keeps_technical_advice_on_dialogue_provider(self):
        prompt = (
            'Sem consultar arquivos, ferramentas ou internet: um erro só aparece depois de várias execuções, '
            'mas ainda não tenho logs. O que posso concluir, o que seria apenas hipótese e qual experimento '
            'simples ajudaria a distinguir as causas?'
        )
        result = {
            'schema': 'agent-dialogue-response/v1', 'ok': True,
            'text': 'Só dá para concluir que o erro depende de algo que muda entre execuções. Sem logs, causas como estado residual, concorrência ou recurso não liberado são hipóteses. Eu repetiria o caso com um registro simples por execução e mudaria uma condição de cada vez.',
            'backend': 'dialogue-test', 'intent': 'conversation',
            'generation': {'quality_gate_result': 'accepted'},
        }
        with patch.object(self.service.dialogue_api, 'turn', return_value=result) as turn, \
             patch.object(self.service, 'local_reply', side_effect=AssertionError('conversation objective must not use raw checkpoint')):
            response = self.service.reply([{'role': 'user', 'content': prompt}], objective='conversation')

        self.assertEqual(response['backend'], 'dialogue-test')
        self.assertEqual(response['text'], result['text'])
        self.assertEqual(turn.call_args.args[0]['messages'][-1]['content'], prompt)
        self.assertIn('exatamente quatro itens curtos', turn.call_args.kwargs['system_context'])
        self.assertIn('estado residual', turn.call_args.kwargs['system_context'])

    def test_diagnostic_fallback_is_scenario_specific_when_dialogue_is_unusable(self):
        window_prompt = (
            'Estou depurando um app que inicia normalmente, mas às vezes a janela não aparece e a porta continua ocupada. '
            'Não altere arquivos. Qual hipótese você investigaria primeiro, que informação ainda falta e qual checagem segura faria?'
        )
        repeated_prompt = (
            'Sem consultar arquivos, ferramentas ou internet: um erro só aparece depois de várias execuções, '
            'mas ainda não tenho logs. O que posso concluir, o que seria apenas hipótese e qual experimento simples '
            'ajudaria a distinguir as causas?'
        )
        window_answer = self.service.diagnostic_reasoning_fallback(window_prompt)
        repeated_answer = self.service.diagnostic_reasoning_fallback(repeated_prompt)
        self.assertIn('porta', window_answer)
        self.assertIn('PID', window_answer)
        self.assertIn('Não encerre processo', window_answer)
        self.assertIn('reinício limpo', repeated_answer)
        self.assertIn('mesmo processo', repeated_answer)
        self.assertIsNone(self.service.diagnostic_reasoning_fallback('Oi, tudo bem?'))

    def test_dialogue_turn_forwards_stream_fragments_to_the_local_provider(self):
        chunks = []
        result = {
            'schema': 'agent-dialogue-response/v1', 'ok': True,
            'text': 'Uma resposta progressiva.', 'backend': 'dialogue-test',
            'generation': {'quality_gate_result': 'accepted'},
        }

        def stream_result(body, validate_candidate=None, system_context='', on_delta=None):
            if on_delta:
                on_delta('Uma resposta ')
                on_delta('progressiva.')
            return result

        with patch.object(self.service.dialogue_api, 'turn', side_effect=stream_result) as turn:
            response = self.service.dialogue_turn({
                'schema': 'agent-dialogue-request/v1',
                'messages': [{'role': 'user', 'content': 'Responda em partes.'}],
            }, on_delta=chunks.append)
        self.assertTrue(response['ok'])
        self.assertEqual(chunks, ['Uma resposta ', 'progressiva.'])
        self.assertTrue(callable(turn.call_args.kwargs['on_delta']))

    def test_diagnostic_candidate_gate_requires_conclusion_hypothesis_missing_info_and_check(self):
        question = 'O que posso concluir, qual hipótese e qual experimento seguro ajuda a distinguir?'
        answer = 'Talvez exista um problema acumulado; tente alterar um valor para ver se melhora.'
        accepted, reason = self.service._dialogue_candidate_quality(answer, question)
        self.assertFalse(accepted)
        self.assertEqual(reason, 'diagnostic-structure-incomplete')

        prompt = (
            'Sem consultar arquivos: um erro só aparece depois de várias execuções, mas ainda não tenho logs. '
            'O que posso concluir, qual hipótese e qual experimento seguro distingue as causas?'
        )
        overclaim = (
            'Conclusão: O erro pode ser causado por estado residual.\n'
            'Hipótese inicial: Estado residual no processo.\n'
            'Informação que falta: Logs.\n'
            'Checagem segura: Compare uma execução limpa com execuções consecutivas e registre o PID.'
        )
        accepted, reason = self.service._dialogue_candidate_quality(overclaim, prompt)
        self.assertFalse(accepted)
        self.assertEqual(reason, 'diagnostic-conclusion-overreach')

    def test_diagnostic_followup_uses_prior_context_and_rejects_race_overclaim(self):
        prior = (
            'Sem consultar arquivos, ferramentas ou internet: um erro só aparece depois de várias execuções, '
            'mas ainda não tenho logs. O que posso concluir, o que seria apenas hipótese e qual experimento '
            'simples ajudaria a distinguir as causas?'
        )
        followup = 'E se também falhar logo após reiniciar?'
        messages = [
            {'role': 'user', 'content': prior},
            {'role': 'assistant', 'content': 'Estado acumulado é uma hipótese, ainda sem evidência.'},
            {'role': 'user', 'content': followup},
        ]
        context = self.service.diagnostic_followup_context(followup, messages)
        self.assertEqual(context, prior)
        self.assertIn('não prova nem torna uma condição de corrida mais provável',
                      self.service.diagnostic_dialogue_guidance(followup, messages))

        overclaim = (
            'Conclusão: Falhar após reiniciar sugere que a condição de corrida é mais provável.\n'
            'Hipótese atualizada: condição de corrida.\n'
            'Informação que falta: logs.\n'
            'Experimento seguro: repita várias execuções e veja se falha.'
        )
        accepted, reason = self.service._dialogue_candidate_quality(
            overclaim, followup, diagnostic_context=context,
        )
        self.assertFalse(accepted)
        self.assertEqual(reason, 'diagnostic-followup-race-overclaim')

        incomplete = (
            'Conclusão: Isso enfraquece a hipótese de estado acumulado e não torna corrida mais provável.\n'
            'Hipótese atualizada: alguma condição persiste.\n'
            'Informação que falta: a mensagem exata.\n'
            'Experimento seguro: reinicie o app várias vezes e registre o PID.'
        )
        accepted, reason = self.service._dialogue_candidate_quality(
            incomplete, followup, diagnostic_context=context,
        )
        self.assertFalse(accepted)
        self.assertEqual(reason, 'diagnostic-followup-incomplete')

        answer = self.service.diagnostic_reasoning_fallback(followup, messages)
        self.assertIn('enfraquece a hipótese', answer)
        self.assertIn('não torna uma condição de corrida mais provável', answer)
        self.assertIn('primeira tentativa', answer)

    def test_diagnostic_followup_requires_diagnostic_history(self):
        followup = 'E se também falhar logo após reiniciar?'
        self.assertEqual(self.service.diagnostic_followup_context(followup, [
            {'role': 'user', 'content': 'Oi, tudo bem?'},
            {'role': 'user', 'content': followup},
        ]), '')

    def test_research_objective_synthesizes_opened_sources_with_local_dialogue_provider(self):
        prompt = (
            'Pesquise a documentação oficial atual do CMake sobre FetchContent. Como posso fixar versões '
            'para tornar as dependências reproduzíveis? Traga os links consultados e separe o que a '
            'documentação confirma do que é sua recomendação. Não altere arquivos.'
        )
        page = {
            'title': 'FetchContent — CMake Documentation',
            'url': 'https://cmake.org/cmake/help/latest/module/FetchContent.html',
            'text': 'The GIT_TAG value may be a commit hash. URL_HASH verifies downloaded archive content.',
        }
        tool_result = {
            'tool': 'research_web', 'ok': True,
            'data': {'pages': [page], 'search_results': [], 'answer': 'static extraction'},
        }
        answer = (
            'A documentação confirma que GIT_TAG pode fixar um commit. Minha recomendação é usar o hash '
            'completo para builds reproduzíveis.'
        )
        result = {
            'schema': 'agent-dialogue-response/v1', 'ok': True, 'text': answer,
            'backend': 'dialogue-test', 'intent': 'conversation',
            'generation': {'quality_gate_result': 'accepted'},
        }
        with patch.object(self.service, '_relevant_research_pages', return_value=[page]), \
             patch.object(self.service.dialogue_api, 'turn', return_value=result) as turn, \
             patch.object(self.service, 'local_reply', side_effect=AssertionError('research synthesis should use dialogue provider')):
            response = self.service.reply([
                {'role': 'user', 'content': prompt},
                {'role': 'tool', 'tool': 'research_web', 'content': json.dumps(tool_result)},
            ], objective='research', request_id='research-dialogue-test')

        self.assertEqual(response['backend'], 'dialogue-test')
        self.assertIn('Fontes consultadas:', response['text'])
        self.assertIn(page['url'], response['text'])
        evidence = turn.call_args.args[0]['evidence']
        self.assertEqual(evidence[0]['source'], page['url'])
        self.assertIn('GIT_TAG', evidence[0]['text'])
        self.assertIn('separar fatos de recomendação', turn.call_args.kwargs['system_context'])

    def test_research_candidate_rejects_unsupported_cmake_option_and_fallback_uses_source_facts(self):
        prompt = (
            'Pesquise a documentação oficial do CMake sobre FetchContent. Separe o que a documentação confirma '
            'do que é sua recomendação.'
        )
        url = 'https://cmake.org/cmake/help/latest/module/FetchContent.html'
        excerpt = (
            'FetchContent_Declare() uses GIT_TAG for Git dependencies. Where contents are fetched from a remote '
            'location that you do not control, it is advisable to use a hash for GIT_TAG rather than a branch or '
            'tag name. A commit hash is more secure. An archive example uses URL_HASH.'
        )
        hallucinated = (
            'A documentação confirma que GIT_REVISION fixa a dependência. Minha recomendação é usar essa opção.'
        )
        accepted, reason = self.service._dialogue_candidate_quality(
            hallucinated, prompt, [url], [excerpt],
        )
        self.assertFalse(accepted)
        self.assertIn('GIT_REVISION', reason)

        fallback = self.service.grounded_research_fallback(prompt, [{'source': url, 'text': excerpt}])
        self.assertIn('O que a documentação confirma', fallback)
        self.assertIn('Minha recomendação', fallback)
        self.assertIn('GIT_TAG', fallback)
        self.assertIn('URL_HASH', fallback)
        self.assertNotIn('GIT_REVISION', fallback)

    def test_short_acknowledgment_keeps_the_conversation_history(self):
        messages = [
            {'role': 'user', 'content': 'Podemos estruturar uma API nossa com provedores intercambiáveis?'},
            {'role': 'assistant', 'content': 'Sim, com o modelo local primeiro e externo opcional.'},
            {'role': 'user', 'content': 'É'},
        ]
        with patch.object(self.service.dialogue_api, 'turn', return_value={
            'schema': 'agent-dialogue-response/v1', 'ok': True,
            'text': 'Certo, vou seguir com essa arquitetura.', 'backend': 'dialogue-test',
            'generation': {'quality_gate_result': 'accepted'},
        }) as turn:
            response = self.service.reply(messages)

        self.assertEqual(response['backend'], 'dialogue-test')
        self.assertEqual(turn.call_args.args[0]['messages'], messages)

    def test_project_opinion_sends_file_evidence_as_named_items(self):
        result = {
            'schema': 'agent-dialogue-response/v1', 'ok': True,
            'text': 'Concordo parcialmente: este arquivo mostra o ciclo principal.',
            'backend': 'dialogue-test', 'generation': {'quality_gate_result': 'accepted'},
        }
        context = json.dumps({
            'workspace': '/tmp/Game', 'summary': 'Aplicação GLFW',
            'files': ['src/ui/Window.cpp'], 'test_files': [], 'entrypoints': ['src/main.cpp'],
            'available_checks': ['cmake'],
            'files_read': [{
                'path': 'src/ui/Window.cpp', 'start_line': 1,
                'content': 'void Window::OnUpdate() { glfwPollEvents(); }',
            }],
        })
        with patch.object(self.service.dialogue_api, 'turn', return_value=result) as turn:
            answer = self.service.opinion_model_reply(
                [{'role': 'user', 'content': 'Você concorda com essa avaliação?'}],
                'Você concorda com essa avaliação?', knowledge=context,
                evidence_paths=['src/ui/Window.cpp'],
            )
        self.assertEqual(answer, result['text'])
        evidence = turn.call_args.args[0]['evidence']
        self.assertEqual(evidence[1]['source'], 'src/ui/Window.cpp')
        self.assertIn('glfwPollEvents', evidence[1]['text'])

    def test_behavioral_instruction_is_answered_conversationally(self):
        question = (
            'Trabalhe somente no workspace ativo. Persiga o objetivo de forma proativa: '
            'inspecione o que existe, tome decisões razoáveis, implemente, teste e corrija '
            'falhas. Não apenas sugira código. Ao final de cada etapa, mostre arquivos '
            'alterados, comandos executados, resultados e pendências verificáveis.'
        )
        response = self.service.reply([
            {'role': 'user', 'content': question},
            {'role': 'tool', 'content': json.dumps({
                'tool': 'inspect_project', 'ok': True,
                'data': {'summary': 'inspeção anterior'},
            })},
        ])
        self.assertEqual(response['backend'], 'local-conversation')
        self.assertEqual(response['intent'], 'conversation')
        self.assertIn('Entendi', response['text'])
        self.assertIn('modo de trabalho', response['text'])
        self.assertNotIn('etapa de consulta terminou', response['text'].lower())

    def test_non_programming_goal_researches_before_answering(self):
        response = self.service.reply([{'role': 'user', 'content': 'Como escrever uma redação argumentativa?'}])
        call = response.get('tool_call') or {}
        self.assertEqual(response['backend'], 'proactive-learning')
        self.assertEqual(call.get('tool'), 'research_web')
        self.assertEqual(call.get('arguments', {}).get('topic'), 'uma redação argumentativa')
        self.assertIn('style guides', call.get('arguments', {}).get('query', ''))

    def test_conversation_request_is_not_promoted_to_web_research(self):
        response = self.service.reply([{'role': 'user', 'content': 'Me ajude a escrever uma mensagem curta'}])
        self.assertNotEqual(response.get('backend'), 'proactive-learning')
        self.assertFalse(response.get('tool_call'))

    def test_tool_plan_echoes_request_terms_for_auditable_ui(self):
        response = self.service.reply([{'role': 'user', 'content': 'Liste os arquivos do workspace'}])
        self.assertEqual(response['backend'], 'tool-router')
        self.assertEqual((response.get('tool_call') or {}).get('tool'), 'list_files')
        self.assertIn('workspace', response['text'].lower())
        self.assertIn('arquivos', response['text'].lower())

    def test_workspace_inventory_uses_local_listing_and_observed_entries(self):
        for question in ('o que há no workspace atual?',
                         'Quais arquivos existem no workspace?',
                         'Mostre a estrutura da pasta atual'):
            with self.subTest(question=question):
                first = self.service.reply([{'role': 'user', 'content': question}])
                call = first.get('tool_call') or {}
                self.assertEqual(first['backend'], 'tool-router')
                self.assertEqual(call.get('tool'), 'list_files')
                self.assertEqual(call.get('arguments'),
                                 {'path': '', 'include_hidden': True, 'max_entries': 200})
                self.assertEqual((call.get('skill_routing') or {}).get('network_requested'), False)

        question = 'o que há no workspace atual?'
        messages = [
            {'role': 'user', 'content': question},
            {'role': 'tool', 'content': json.dumps({
                'tool': 'list_files', 'ok': True,
                'data': {'workspace': '/tmp/repo-de-teste', 'path': '',
                         'entries': [
                             {'name': '.ia-local-backups', 'kind': 'directory'},
                             {'name': 'tests', 'kind': 'directory'},
                             {'name': 'todo_cli.py', 'kind': 'file'},
                         ], 'total_entries': 3, 'truncated': False},
            })},
        ]
        final = self.service.reply(messages)
        self.assertEqual(final['backend'], 'agent-loop')
        self.assertEqual((final.get('agent') or {}).get('status'), 'completed')
        self.assertFalse(final.get('tool_call'))
        self.assertIn('/tmp/repo-de-teste', final['text'])
        self.assertIn('todo_cli.py', final['text'])
        self.assertIn('.ia-local-backups', final['text'])
        self.assertNotIn('research_web', final['text'])

    def test_workspace_inventory_does_not_claim_contents_after_failed_listing(self):
        question = 'o que há no workspace atual?'
        response = self.service.reply([
            {'role': 'user', 'content': question},
            {'role': 'tool', 'content': json.dumps({
                'tool': 'list_files', 'ok': False, 'error': 'acesso negado', 'data': {},
            })},
        ])
        self.assertEqual((response.get('agent') or {}).get('status'), 'blocked')
        self.assertIn('leitura local falhou', response['text'])
        self.assertNotIn('research_web', response['text'])

    def test_workspace_inventory_rejects_incomplete_tool_result(self):
        question = 'o que há no workspace atual?'
        response = self.service.reply([
            {'role': 'user', 'content': question},
            {'role': 'tool', 'content': json.dumps({
                'tool': 'list_files', 'ok': True, 'data': {'entries': []},
            })},
        ])
        self.assertEqual((response.get('agent') or {}).get('status'), 'failed')
        self.assertIn('formato válido', response['text'])
        self.assertNotIn('encontrei 0', response['text'])

    def test_conceptual_variants_use_verified_local_answers(self):
        cases = [
            ('Explique variáveis em Python.', ('variável', 'python')),
            ('Qual é a diferença entre GET e POST?', ('get', 'post', 'http')),
            ('A atenção parece focar na palavra certa, mas o modelo falha quando há padding. O que verifico?', ('padding', 'softmax')),
            ('Poucos dados e um modelo grande produzem treino excelente e validação ruim. Que alternativas comparar?', ('regularizado', 'validação')),
        ]
        for question, terms in cases:
            with self.subTest(question=question):
                response = self.service.reply([{'role': 'user', 'content': question}])
                self.assertEqual(response['backend'], 'curated-memory')
                for term in terms:
                    self.assertIn(term, response['text'].lower())

    def test_raw_checkpoint_benchmark_bypasses_curated_adapter(self):
        class CheckpointOnly:
            def __init__(self):
                self.calls = []

            def local_reply(self, messages, knowledge=None):
                self.calls.append((messages, knowledge))
                return "saída do checkpoint"

            def neural_reply(self, messages):
                raise AssertionError("the benchmark must not consult the curated adapter")

        service = CheckpointOnly()
        answer = checkpoint_reply(service, "Explique um conceito novo.")
        self.assertEqual(answer, "saída do checkpoint")
        self.assertEqual(service.calls, [([{"role": "user", "content": "Explique um conceito novo."}], None)])

    def test_closed_arithmetic_contract_wins_over_neural_generation(self):
        with patch.object(self.service, 'local_reply', side_effect=AssertionError('neural generation should be bypassed')) as generate:
            response = self.service.reply([{
                'role': 'user',
                'content': 'Qual é o resultado de 19 + 23? Responda apenas com o número.',
            }])
        self.assertEqual(response['backend'], 'curated-memory')
        self.assertEqual(response['text'], '42')
        generate.assert_not_called()

    def test_compound_file_read_continues_to_each_explicit_path(self):
        question = (
            'Leia runtime/src/main.rs e python/model_server.py, compare como o runtime '
            'chama o worker e explique o caminho. Não edite arquivos.'
        )
        messages = [{'role': 'user', 'content': question}]
        first = self.service.reply(messages)
        self.assertEqual((first.get('tool_call') or {}).get('tool'), 'list_files')
        messages.append({'role': 'tool', 'content': json.dumps({
            'tool': 'list_files', 'ok': True,
            'data': {'entries': [{'name': 'runtime', 'kind': 'directory'}, {'name': 'python', 'kind': 'directory'}]},
        })})
        second = self.service.reply(messages)
        self.assertEqual((second.get('tool_call') or {}).get('arguments', {}).get('path'), 'runtime/src/main.rs')
        messages.append({'role': 'tool', 'content': json.dumps({
            'tool': 'read_file', 'ok': True,
            'data': {'path': 'runtime/src/main.rs', 'content': 'fn main() {}', 'truncated': True, 'next_offset': 131072},
        })})
        third = self.service.reply(messages)
        self.assertEqual((third.get('tool_call') or {}).get('arguments', {}).get('path'), 'python/model_server.py')
        messages.append({'role': 'tool', 'content': json.dumps({
            'tool': 'read_file', 'ok': True,
            'data': {'path': 'python/model_server.py', 'content': 'POST /generate calls ModelService.reply', 'truncated': False},
        })})
        final = self.service.reply(messages)
        self.assertEqual(final['backend'], 'curated-memory')
        self.assertIn('127.0.0.1:3101/generate', final['text'])
        self.assertIn('runtime/src/main.rs', final['text'])
        self.assertIn('worker Python', final['text'])

    def test_programming_patterns_return_actionable_grounded_answers(self):
        cases = [
            ('Implemente uma função que remova duplicatas preservando a ordem e inclua testes.',
             ('curated-memory', 'def sem_duplicatas', 'test_sem_duplicatas')),
            ('Como testar uma rota HTTP que chama um banco de dados?',
             ('curated-memory', 'banco temporário', 'status_code')),
            ('Como decidir se uma alteração de desempenho funcionou?',
             ('curated-memory', 'baseline', 'p95')),
            ('Mostre como validar um argumento de ferramenta antes de executá-la.',
             ('curated-memory', 'campos obrigatórios', 'erro estruturado')),
        ]
        for question, (backend, first, second) in cases:
            with self.subTest(question=question):
                response = self.service.reply([{'role': 'user', 'content': question}])
                self.assertEqual(response['backend'], backend)
                self.assertIn(first, response['text'])
                self.assertIn(second, response['text'])

    def test_unseen_technical_question_starts_autonomous_research(self):
        for question in ('Qual a diferença entre um lock pessimista e uma fila FIFO?',):
            with self.subTest(question=question):
                response = self.service.reply([{'role': 'user', 'content': question}])
                self.assertEqual(response['backend'], 'proactive-learning')
                self.assertEqual((response.get('tool_call') or {}).get('tool'), 'research_web')
                self.assertEqual((response.get('agent') or {}).get('phase'), 'learn')

        curated = self.service.reply([{
            'role': 'user',
            'content': 'Quando uma saída JSON do agente deve ser rejeitada?',
        }])
        self.assertEqual(curated['backend'], 'curated-memory')

    def test_unknown_goal_investigates_instead_of_asking_user_to_research(self):
        response = self.service.reply([{
            'role': 'user',
            'content': 'Qual é a melhor abordagem para organizar esta tarefa?',
        }])
        self.assertEqual(response['backend'], 'proactive-learning')
        self.assertEqual((response.get('tool_call') or {}).get('tool'), 'research_web')
        self.assertIn('fontes confiáveis', (response.get('tool_call') or {}).get('arguments', {}).get('query', ''))

    def test_creation_goal_with_explicit_workspace_inspects_before_quality_gate(self):
        question = (
            'Quero criar um assistente pessoal e integrar com o sistema operacional '
            'do meu computador Workspace local: /home/victor/Documentos/Assistente Pessoal'
        )
        messages = [{'role': 'user', 'content': question}]
        first = self.service.reply(messages)
        self.assertEqual(first['backend'], 'tool-router')
        self.assertEqual((first.get('tool_call') or {}).get('tool'), 'set_workspace')
        self.assertEqual(
            (first.get('tool_call') or {}).get('arguments', {}).get('path'),
            '/home/victor/Documentos/Assistente Pessoal',
        )
        messages.append({'role': 'tool', 'content': json.dumps({
            'tool': 'set_workspace', 'ok': True,
            'data': {'workspace': '/home/victor/Documentos/Assistente Pessoal', 'selected': True},
        })})
        second = self.service.reply(messages)
        self.assertEqual(second['backend'], 'agent-loop')
        self.assertEqual((second.get('tool_call') or {}).get('tool'), 'inspect_project')
        self.assertEqual((second.get('agent') or {}).get('phase'), 'observe')
        messages.append({'role': 'tool', 'content': json.dumps({
            'tool': 'inspect_project', 'ok': True,
            'data': {
                'workspace': '/home/victor/Documentos/Assistente Pessoal',
                'summary': 'nenhum manifesto reconhecido; nenhum arquivo de teste identificado',
                'manifests': [], 'test_files': [], 'entrypoints': [], 'checks': [], 'signals': [],
            },
        })})
        third = self.service.reply(messages)
        self.assertEqual(third['backend'], 'agent-loop')
        self.assertEqual((third.get('tool_call') or {}).get('tool'), 'research_web')
        self.assertEqual((third.get('agent') or {}).get('phase'), 'learn')

    def test_workspace_research_produces_an_approval_gated_implementation(self):
        question = (
            'Quero criar um assistente pessoal e integrar com o sistema operacional '
            'do meu computador Workspace local: /home/victor/Documentos/Assistente Pessoal'
        )
        messages = [
            {'role': 'user', 'content': question},
            {'role': 'tool', 'content': json.dumps({
                'tool': 'set_workspace', 'ok': True,
                'data': {'workspace': '/home/victor/Documentos/Assistente Pessoal', 'selected': True},
            })},
            {'role': 'tool', 'content': json.dumps({
                'tool': 'inspect_project', 'ok': True,
                'data': {'workspace': '/home/victor/Documentos/Assistente Pessoal', 'manifests': [], 'test_files': []},
            })},
            {'role': 'tool', 'content': json.dumps({
                'tool': 'research_web', 'ok': True,
                'data': {
                    'category': 'proactive-learning',
                    'query': 'arquitetura TypeScript para assistente local',
                    'pages': [{'title': 'Documentação oficial', 'url': 'https://example.test/docs', 'text': 'Conteúdo técnico verificável.'}],
                },
            })},
        ]
        plan = {
            'assumptions': ['Usar TypeScript e manter o núcleo sem acesso direto ao sistema operacional.'],
            'operations': [{
                'tool': 'create_file',
                'arguments': {'path': 'src/core.ts', 'content': 'export function main() { return "local"; }\n'},
            }],
        }
        with patch.object(self.service, 'local_reply', return_value=json.dumps(plan)), \
             patch('model_server.learning.persist_pages', return_value=0), \
             patch('model_server.learning.register_research_result', return_value=None):
            response = self.service.reply(messages)
        call = response.get('tool_call') or {}
        self.assertEqual(call.get('tool'), 'apply_batch')
        self.assertTrue(call.get('requires_approval'))
        self.assertEqual(call.get('arguments', {}).get('operations'), plan['operations'])
        self.assertIn('Premissas adotadas', response['text'])
        self.assertEqual((response.get('agent') or {}).get('planner'), 'proactive-implementation')

    def test_proactive_creation_proposes_complete_files_as_one_approved_batch(self):
        question = (
            'Quero criar um assistente pessoal e integrar com o sistema operacional '
            'do meu computador Workspace local: /home/victor/Documentos/Assistente Pessoal'
        )
        messages = [
            {'role': 'user', 'content': question},
            {'role': 'tool', 'content': json.dumps({
                'tool': 'set_workspace', 'ok': True,
                'data': {'workspace': '/home/victor/Documentos/Assistente Pessoal', 'selected': True},
            })},
            {'role': 'tool', 'content': json.dumps({
                'tool': 'inspect_project', 'ok': True,
                'data': {'workspace': '/home/victor/Documentos/Assistente Pessoal', 'files': [],
                         'manifests': [], 'test_files': [], 'entrypoints': []},
            })},
            {'role': 'tool', 'content': json.dumps({
                'tool': 'research_web', 'ok': True,
                'data': {'pages': [{'title': 'Docs', 'url': 'https://example.test/docs',
                                    'text': 'Conteúdo técnico verificável.'}]},
            })},
        ]
        plan = {
            'assumptions': ['Usar TypeScript conforme a documentação consultada.'],
            'operations': [
                {'tool': 'create_file', 'arguments': {'path': 'package.json', 'content': '{"type":"module"}\n'}},
                {'tool': 'create_file', 'arguments': {'path': 'src/core.ts', 'content': 'export const core = true;\n'}},
                {'tool': 'create_file', 'arguments': {'path': 'tests/core.test.js', 'content': 'import assert from "node:assert/strict";\nassert.equal(true, true);\n'}},
            ],
        }
        with patch.object(self.service, 'local_reply', return_value=json.dumps(plan)), \
             patch('model_server.learning.persist_pages', return_value=0), \
             patch('model_server.learning.register_research_result', return_value=None):
            response = self.service.reply(messages)
        call = response.get('tool_call') or {}
        self.assertEqual(call.get('tool'), 'apply_batch')
        self.assertEqual([item['arguments']['path'] for item in call['arguments']['operations']],
                         ['package.json', 'src/core.ts', 'tests/core.test.js'])
        self.assertTrue(call.get('requires_approval'))

    def test_huggingface_dataset_curriculum_is_grounded_and_heldout_is_safe(self):
        known = self.service.reply([{
            'role': 'user',
            'content': 'Como carregar um JSONL local para treinar um modelo?',
        }])
        self.assertEqual(known['backend'], 'curated-memory')
        self.assertIn('load_dataset', known['text'])
        unseen = self.service.reply([{
            'role': 'user',
            'content': 'Tenho um JSONL enorme e pouca memória. Qual estratégia de leitura devo escolher?',
        }])
        self.assertNotEqual(unseen['backend'], 'local-knowledge')

    def test_deep_learning_book_curriculum_is_grounded_and_heldout_is_safe(self):
        known = self.service.reply([{
            'role': 'user',
            'content': 'Como diagnosticar overfitting em um modelo?',
        }])
        self.assertEqual(known['backend'], 'curated-memory')
        self.assertIn('validação', known['text'])
        unseen = self.service.reply([{
            'role': 'user',
            'content': 'A sequência fica instável e aparecem NaNs quando aumento o comprimento. Como investigaria isso?',
        }])
        self.assertNotEqual(unseen['backend'], 'local-knowledge')

    def test_unseen_deep_learning_terms_do_not_retrieve_irrelevant_documents(self):
        for question in (
            'Por que uma CNN usa compartilhamento de pesos?',
            'Como comparar BERT e GPT sem vazamento de teste?',
            'Quando aplicar gradient clipping em uma RNN?',
        ):
            with self.subTest(question=question):
                response = self.service.reply([{'role': 'user', 'content': question}])
                self.assertNotEqual(response['backend'], 'local-knowledge')

    def test_knowledge_retrieval_requires_core_terms_in_the_same_sentence(self):
        query = 'O que é uma janela de contexto?'
        terms = {'janela', 'contexto'}
        self.service.knowledge_index = {
            'idf': {term: 2.0 for term in terms},
            'postings': {term: [0] for term in terms},
            'documents': [{
                'id': 'unrelated-ui-guide', 'category': 'fixture',
                'text': 'Manual da interface\nA janela abre o editor. O contexto mostra o menu de arquivos.',
            }],
        }
        with patch.object(self.service, 'refresh_knowledge'):
            self.assertIsNone(self.service.knowledge_answer(query))
            self.assertEqual(self.service.evidence_search(query)['status'], 'no_evidence')

        self.service.knowledge_index = {
            'idf': {term: 2.0 for term in terms},
            'postings': {term: [0] for term in terms},
            'documents': [{
                'id': 'context-window-guide', 'category': 'fixture',
                'text': 'Guia de modelos\nA janela de contexto limita quantos tokens o modelo considera ao mesmo tempo.',
            }],
        }
        with patch.object(self.service, 'refresh_knowledge'):
            result = self.service.knowledge_answer(query)
            evidence = self.service.evidence_search(query)
        self.assertIn('janela de contexto', result.lower())
        self.assertEqual(evidence['status'], 'found')
        self.assertIn('janela de contexto', evidence['items'][0]['excerpt'].lower())

    def test_basic_context_window_question_uses_curated_answer_without_bad_retrieval(self):
        response = self.service.reply([{
            'role': 'user',
            'content': 'Responda em português, em uma frase: o que é uma janela de contexto?',
        }])
        self.assertEqual(response['backend'], 'curated-memory')
        self.assertIn('quantidade de tokens', response['text'])
        self.assertNotIn('EEVEE', response['text'])

    def test_explanatory_analogy_stays_in_chat_and_gets_an_answer(self):
        question = 'Me dê uma analogia curta para explicar por que dividir uma tarefa grande em etapas ajuda.'
        self.assertEqual(self.service.route_intent(question), 'knowledge')
        response = self.service.reply([{'role': 'user', 'content': question}])
        self.assertEqual(response['backend'], 'curated-memory')
        self.assertIn('montar um móvel', response['text'])
        self.assertNotIn('workspace', response['text'].lower())

    def test_attached_books_curriculum_is_grounded_and_heldout_is_safe(self):
        known = self.service.reply([{
            'role': 'user',
            'content': 'Quando RAG é melhor que colocar conhecimento no fine-tuning?',
        }])
        self.assertEqual(known['backend'], 'curated-memory')
        self.assertIn('conhecimento', known['text'])
        unseen = self.service.reply([{
            'role': 'user',
            'content': 'Minha RAG gera respostas convincentes, mas cita documentos errados. Qual etapa devo isolar?',
        }])
        self.assertNotEqual(unseen['backend'], 'local-knowledge')

    def test_workspace_continuation_proposes_a_change_after_two_reads(self):
        question = 'Continue o projeto do workspace.'
        messages = [
            {'role': 'user', 'content': question},
            {'role': 'tool', 'content': json.dumps({
                'tool': 'inspect_project', 'ok': True,
                'data': {
                    'workspace': '/tmp/project', 'summary': 'C++ com CMake.',
                    'files': [{'path': 'CMakeLists.txt'}, {'path': 'src/main.cpp'}, {'path': 'src/Application.cpp'}],
                    'manifests': ['CMakeLists.txt'], 'entrypoints': ['src/main.cpp'],
                    'test_files': [],
                },
            })},
            {'role': 'tool', 'content': json.dumps({
                'tool': 'read_file', 'ok': True,
                'data': {'path': 'CMakeLists.txt', 'content': 'project(sample)'},
            })},
            {'role': 'tool', 'content': json.dumps({
                'tool': 'read_file', 'ok': True,
                'data': {'path': 'src/main.cpp', 'content': 'int main() { return 0; }'},
            })},
        ]
        plan = {
            'assumptions': ['Preservar CMake e manter a alteração pequena.'],
            'operations': [{
                'tool': 'create_file',
                'arguments': {'path': 'src/feature.cpp', 'content': 'int feature() { return 1; }\n'},
            }],
        }
        with patch.object(self.service, 'local_reply', return_value=json.dumps(plan)):
            response = self.service.reply(messages)
        self.assertEqual(response['backend'], 'agent-loop')
        self.assertEqual((response.get('tool_call') or {}).get('tool'), 'apply_batch')
        self.assertIn('src/feature.cpp', response['text'])
        self.assertEqual((response.get('agent') or {}).get('status'), 'tool_call')

    def test_explicit_implementation_request_proposes_files_after_inspection(self):
        question = 'Implemente neste projeto uma CLI de tarefas em Python 3, sem bibliotecas externas.'
        messages = [
            {'role': 'user', 'content': question},
            {'role': 'tool', 'content': json.dumps({
                'tool': 'inspect_project', 'ok': True,
                'data': {
                    'workspace': '/tmp/todo', 'summary': 'Workspace Python 3 vazio.',
                    'files': [{'path': 'README.md'}], 'manifests': [],
                    'entrypoints': [], 'test_files': [],
                },
            })},
            {'role': 'tool', 'content': json.dumps({
                'tool': 'read_file', 'ok': True,
                'data': {'path': 'README.md', 'content': '# Projeto\nCLI Python.'},
            })},
        ]
        plan = {
            'assumptions': ['Persistir tarefas em JSON local e usar unittest.'],
            'operations': [{
                'tool': 'create_file',
                'arguments': {'path': 'todo.py', 'content': 'def main():\n    return 0\n'},
            }],
        }
        with patch.object(self.service, 'local_reply', return_value=json.dumps(plan)) as generate:
            response = self.service.reply(messages)
        self.assertEqual((response.get('tool_call') or {}).get('tool'), 'apply_batch')
        self.assertEqual(response['tool_call']['arguments']['operations'], plan['operations'])
        self.assertIn('todo.py', response['text'])
        generate.assert_called_once()
        generation_messages = generate.call_args.args[0]
        self.assertIn(question, generation_messages[0]['content'])
        self.assertIn('historical context unless the current user explicitly asks', generation_messages[0]['content'])

    def test_task_cli_recipe_proposes_approved_batch_when_local_generator_abstains(self):
        question = 'Crie um programa de lista de tarefas em Python, com adicionar, listar e concluir tarefas, e inclua testes.'
        inspected = {
            'workspace': '/tmp/todo',
            'summary': 'Workspace vazio com README.',
            'files': [{'path': 'README.md'}],
            'manifests': [],
            'entrypoints': [],
            'test_files': [],
        }
        with patch.object(self.service, 'local_reply', return_value=None):
            response = self.service.proactive_implementation_proposal(question, [], inspected)
        call = response.get('tool_call') or {}
        self.assertEqual(call.get('tool'), 'apply_batch')
        self.assertTrue(call.get('requires_approval'))
        self.assertEqual((response.get('agent') or {}).get('planner_source'), 'deterministic-recipe:todo-cli')
        self.assertEqual(
            [item['arguments']['path'] for item in call['arguments']['operations']],
            ['todo_cli.py', 'tests/test_todo_cli.py'],
        )
        self.assertIn('receita local determinística', response['text'])

    def test_node_calculator_recipe_proposes_approval_after_checkpoint_repetition(self):
        question = (
            'No workspace aprovado, crie um projeto Node.js sem dependências externas: '
            'package.json com test: node --test e check: node --check src/calculadora.js; '
            'src/calculadora.js com soma e multiplicação; tests/calculadora.test.js com casos '
            'positivos, negativos e decimais, usando node:test.'
        )
        inspected = {
            'workspace': '/tmp/calculadora', 'summary': 'Workspace vazio.',
            'files': [], 'directories': [], 'manifests': [], 'entrypoints': [], 'test_files': [],
        }
        with patch.object(self.service, 'local_reply', return_value=None):
            response = self.service.proactive_implementation_proposal(question, [], inspected)
        call = response.get('tool_call') or {}
        self.assertEqual(call.get('tool'), 'apply_batch')
        self.assertTrue(call.get('requires_approval'))
        self.assertEqual((response.get('agent') or {}).get('planner_source'),
                         'deterministic-recipe:node-calculator')
        self.assertEqual(
            [item['arguments']['path'] for item in call['arguments']['operations']],
            ['package.json', 'src/calculadora.js', 'tests/calculadora.test.js'],
        )

    def test_delivery_app_recipe_proposes_functional_prototype_when_local_generator_abstains(self):
        question = (
            'Crie o primeiro protótipo de um app para entregadores registrarem as entregas '
            'feitas num determinado período e os quilômetros rodados nesse tempo'
        )
        inspected = {
            'workspace': '/tmp/entregas', 'summary': 'Workspace vazio com README.',
            'files': [{'path': 'README.md'}], 'directories': [], 'manifests': [],
            'entrypoints': [], 'test_files': [],
        }
        with patch.object(self.service, 'local_reply', return_value=None):
            response = self.service.proactive_implementation_proposal(question, [], inspected)
        call = response.get('tool_call') or {}
        operations = call.get('arguments', {}).get('operations', [])
        self.assertEqual(call.get('tool'), 'apply_batch')
        self.assertTrue(call.get('requires_approval'))
        self.assertEqual(response['agent']['planner_source'], 'deterministic-recipe:delivery-tracker')
        self.assertEqual(
            [item['arguments']['path'] for item in operations],
            ['index.html', 'app.js', 'delivery-core.js', 'package.json', 'tests/delivery-core.test.cjs'],
        )
        self.assertIn('receita local determinística', response['text'])

    def test_build_without_valid_generation_explains_the_blocker_and_next_input(self):
        question = 'Crie app.py com soma(a, b) e testes.'
        inspected = {'workspace': '/tmp/projeto', 'files': [{'path': 'README.md'}],
                     'directories': [], 'manifests': [], 'entrypoints': [], 'test_files': []}
        self.service.local_model = object()
        self.service.last_generation = {'quality_gate_result': 'rejected',
                                        'quality_stop_reason': 'repeated-fragment'}
        with patch.object(self.service, 'local_reply', return_value=None):
            response = self.service.proactive_implementation_proposal(question, [], inspected)
        self.assertIsNone(response.get('tool_call'))
        self.assertEqual(response['agent']['stop_reason'], 'implementation_proposal_unavailable')
        self.assertIn('interrompeu a geração de código por repetição', response['text'])
        self.assertFalse(response['agent']['retryable'])
        self.assertIn('### caminho', response['text'])

    def test_todo_ui_follow_up_proposes_approved_batch_from_inspected_source(self):
        question = "Tente novamente implementar a interface da lista de tarefas."
        inspected = {
            "workspace": "/tmp/todo",
            "summary": "CLI de tarefas Python com persistência JSON local.",
            "files": [{"path": "todo_cli.py"}, {"path": "tests/test_todo_cli.py"}],
            "manifests": [], "entrypoints": [{"path": "todo_cli.py"}],
            "test_files": [{"path": "tests/test_todo_cli.py"}],
        }
        results = [{"tool": "read_file", "ok": True,
                    "data": {"path": "todo_cli.py", "content": TODO_CLI_SOURCE}}]
        with patch.object(self.service, "local_reply", return_value=None):
            response = self.service.proactive_implementation_proposal(question, results, inspected)
        call = response.get("tool_call") or {}
        self.assertEqual(call.get("tool"), "apply_batch")
        self.assertTrue(call.get("requires_approval"))
        self.assertEqual((response.get("agent") or {}).get("planner_source"),
                         "deterministic-recipe:todo-ui")
        self.assertEqual([item["arguments"]["path"] for item in call["arguments"]["operations"]],
                         ["todo_ui.py", "tests/test_todo_ui.py"])
        self.assertIn("interface web local", response["text"].lower())

    def test_project_check_completion_reports_changed_files_and_observed_evidence(self):
        question = 'Implemente uma CLI de tarefas em Python.'
        messages = [
            {'role': 'user', 'content': question},
            {'role': 'tool', 'content': json.dumps({
                'tool': 'apply_batch', 'ok': True,
                'data': {'count': 2, 'operations': [
                    {'tool': 'create_file', 'result': {'path': 'todo_cli.py'}},
                    {'tool': 'create_file', 'result': {'path': 'tests/test_todo_cli.py'}},
                ]},
            })},
            {'role': 'tool', 'content': json.dumps({
                'tool': 'project_checks', 'ok': True,
                'data': {
                    'check': 'unittest',
                    'command': 'python3 -m unittest discover -s tests -p test*.py -v',
                    'executed': True, 'passed': True, 'exit_code': 0, 'elapsed_ms': 21,
                    'stdout': '', 'stderr': 'Ran 4 tests in 0.021s\n\nOK\n',
                },
            })},
        ]
        response = self.service.reply(messages)
        self.assertEqual((response.get('agent') or {}).get('status'), 'completed')
        self.assertTrue((response.get('agent') or {}).get('verified'))
        self.assertEqual((response.get('agent') or {}).get('changed_files'),
                         ['todo_cli.py', 'tests/test_todo_cli.py'])
        self.assertIn('Verificação: passou', response['text'])
        self.assertIn('Ran 4 tests', response['text'])
        self.assertIn('Arquivos criados ou alterados:', response['text'])
        self.assertIn('tests/test_todo_cli.py', response['text'])

    def test_failed_project_check_diagnosis_keeps_failure_and_changed_files_visible(self):
        question = 'Implemente uma CLI de tarefas em Python.'
        messages = [
            {'role': 'user', 'content': question},
            {'role': 'tool', 'content': json.dumps({
                'tool': 'apply_batch', 'ok': True,
                'data': {'operations': [
                    {'tool': 'create_file', 'result': {'path': 'todo_cli.py'}},
                ]},
            })},
            {'role': 'tool', 'content': json.dumps({
                'tool': 'project_checks', 'ok': True,
                'data': {
                    'check': 'unittest', 'command': 'python3 -m unittest discover -s tests',
                    'executed': True, 'passed': False, 'exit_code': 1,
                    'stdout': '', 'stderr': 'FAILED (failures=1)\n',
                },
            })},
            {'role': 'tool', 'content': json.dumps({
                'tool': 'diagnose_project', 'ok': True,
                'data': {'summary': 'Uma asserção falhou.', 'evidence': ['assertEqual esperado 1, observado 0'],
                         'next_steps': ['Corrigir a lógica e repetir os testes.']},
            })},
        ]
        response = self.service.reply(messages)
        self.assertEqual((response.get('agent') or {}).get('status'), 'blocked')
        self.assertIn('Verificação: falhou', response['text'])
        self.assertIn('FAILED (failures=1)', response['text'])
        self.assertIn('todo_cli.py', response['text'])
        self.assertIn('Corrigir a lógica', response['text'])

    def test_technical_request_never_returns_irrelevant_learned_excerpt(self):
        question = 'Como funciona o recurso zeta_event_loop no código Python?'
        messages = [
            {'role': 'user', 'content': question},
            {'role': 'tool', 'content': json.dumps({
                'tool': 'read_file', 'ok': True,
                'data': {'path': 'src/worker.py', 'content': 'def run(): pass'},
            })},
        ]
        with patch.object(self.service, 'learned_answer', return_value='Trecho não relacionado sobre Django.'), \
             patch.object(self.service, 'knowledge_answer', return_value=None), \
             patch.object(self.service, 'local_reply', return_value=None):
            response = self.service.reply(messages)
        self.assertEqual(response['backend'], 'quality-gate')
        self.assertNotIn('Django', response['text'])



    def test_explicit_planner_file_gets_scoped_answer_from_multiple_windows(self):
        answer = explicit_file_read_answer('Explique o roteador em agent-core/src/planner.ts.', [
            {'tool': 'read_file', 'ok': True, 'data': {
                'path': 'agent-core/src/planner.ts', 'start_line': 1,
                'content': 'const toolNames = [\"inspect_project\"];',
            }},
            {'tool': 'read_file', 'ok': True, 'data': {
                'path': 'agent-core/src/planner.ts', 'start_line': 121,
                'content': 'export class LocalPlannerHttp implements PlannerPort { fetch(\"/generate\"); validateProposal(); }',
            }},
        ])
        self.assertIn('LocalPlannerHttp', answer)
        self.assertIn('/generate', answer)
        self.assertIn('não faz o ranqueamento', answer)

    def test_explicit_source_read_continues_to_next_bounded_line_window(self):
        question = 'Leia agent-core/src/planner.ts e explique como o planejador escolhe a próxima ferramenta.'
        last = {'tool': 'read_file', 'ok': True, 'data': {
            'path': 'agent-core/src/planner.ts', 'start_line': 1,
            'end_line': 120, 'total_lines': 215,
        }}
        window = next_explicit_file_window(question, last, [last])
        self.assertEqual(window, {
            'path': 'agent-core/src/planner.ts', 'start_line': 121,
            'end_line': 215, 'max_bytes': 8192,
        })

    def test_plain_configuration_files_use_read_file(self):
        self.assertEqual(document_read_tool('config/agent-planner.yaml'), 'read_file')
        self.assertEqual(document_read_tool('src/main.py'), 'read_file')
        self.assertEqual(document_read_tool('report.pdf'), 'extract_document_text')
        self.assertEqual(document_read_tool('report.rtf'), 'extract_document_text')
        self.assertEqual(document_read_tool('notebook.ipynb'), 'extract_document_text')
        self.assertEqual(self.service.plan_tool('Leia `config/agent-planner.yaml` e explique o catálogo.')['tool'], 'read_file')
        self.assertEqual(self.service.plan_tool('Leia `report.pdf` e resuma o documento.')['tool'], 'extract_document_text')

    def test_missing_explicit_file_replans_once_to_related_sibling(self):
        question = (
            'Leia `config/agent-planner.yaml` para explicar o catálogo de datasets. '
            'Se não existir, encontre na mesma pasta o arquivo existente mais relacionado e leia-o.'
        )
        messages = [
            {'role': 'user', 'content': question},
            {'role': 'tool', 'content': json.dumps({
                'tool': 'read_file', 'ok': False, 'data': None,
                'error': 'No such file or directory (os error 2)',
            })},
        ]
        listing = self.service.continue_after_tool(messages, question, objective='analyze')
        call = listing.get('tool_call') or {}
        self.assertEqual(call.get('tool'), 'list_files')
        self.assertEqual(call.get('arguments', {}).get('path'), 'config')

        messages.append({'role': 'tool', 'content': json.dumps({
            'tool': 'list_files', 'ok': True, 'data': {
                'path': 'config', 'workspace': '/workspace',
                'entries': [
                    {'name': 'huggingface_catalog.json', 'kind': 'file'},
                    {'name': 'pyproject.toml', 'kind': 'file'},
                ],
                'total_entries': 2, 'truncated': False,
            },
        })})
        retry = self.service.continue_after_tool(messages, question, objective='analyze')
        retry_call = retry.get('tool_call') or {}
        self.assertEqual(retry_call.get('tool'), 'read_file')
        self.assertEqual(retry_call.get('arguments', {}).get('path'), 'config/huggingface_catalog.json')

        messages.append({'role': 'tool', 'content': json.dumps({
            'tool': 'read_file', 'ok': True, 'data': {
                'path': 'config/huggingface_catalog.json',
                'content': json.dumps({'datasets': [{'id': 'example/code-data'}],
                                       'policy': {'status': 'quarantine', 'training_eligible': False}}),
            },
        })})
        answer = self.service.continue_after_tool(messages, question, objective='analyze')
        self.assertEqual(answer['agent']['status'], 'completed')
        self.assertIn('config/huggingface_catalog.json', answer['text'])
        self.assertIn('example/code-data', answer['text'])

    def test_missing_explicit_file_does_not_choose_unrelated_sibling(self):
        question = 'Leia `config/agent-planner.yaml` para explicar o catálogo de datasets.'
        messages = [
            {'role': 'user', 'content': question},
            {'role': 'tool', 'content': json.dumps({
                'tool': 'read_file', 'ok': False, 'data': None,
                'error': 'No such file or directory (os error 2)',
            })},
            {'role': 'tool', 'content': json.dumps({
                'tool': 'list_files', 'ok': True, 'data': {
                    'path': 'config', 'workspace': '/workspace',
                    'entries': [{'name': 'unrelated_build_flags.toml', 'kind': 'file'}],
                    'total_entries': 1, 'truncated': False,
                },
            })},
        ]
        response = self.service.continue_after_tool(messages, question, objective='analyze')
        self.assertEqual(response['agent']['status'], 'blocked')
        self.assertEqual(response['agent']['stop_reason'], 'missing_file_recovery_no_match')
        self.assertFalse(response.get('tool_call'))

    def test_missing_explicit_file_can_be_replaced_by_verified_related_catalog(self):
        answer = explicit_file_substitution_answer(
            'Leia config/agent-planner.yaml para explicar o catálogo de datasets.',
            ['config/agent-planner.yaml'],
            [{'tool': 'read_file', 'ok': True, 'data': {
                'path': 'config/huggingface_catalog.json',
                'content': json.dumps({'policy': {'status': 'quarantine', 'training_eligible': False},
                                       'datasets': [{'id': 'example/code-data'}]}),
            }}],
            [{'tool': 'read_file', 'ok': False, 'error': 'arquivo ausente'}],
        )
        self.assertIn('config/huggingface_catalog.json', answer)
        self.assertIn('example/code-data', answer)
        self.assertIn('elegível para treino: não', answer)

    def test_conversation_math_is_answered_without_workspace_tools(self):
        response = self.service.reply(
            [{'role': 'user', 'content': 'Quanto é 19 × 23? Mostre uma conta curta.'}],
            objective='conversation',
        )
        self.assertEqual(response['backend'], 'execution-engine')
        self.assertTrue(response['execution_engine']['passed'])
        self.assertIn('437', response['text'])
        self.assertFalse(response.get('tool_call'))

    def test_small_factorial_code_example_is_grounded_and_calculated(self):
        response = self.service.reply(
            [{'role': 'user', 'content': 'Escreva uma função factorial em Python para n=5, explique e informe o resultado.'}],
            objective='conversation',
        )
        self.assertEqual(response['backend'], 'deterministic-reasoning')
        self.assertIn('def factorial', response['text'])
        self.assertIn('120', response['text'])
        self.assertFalse(response.get('tool_call'))

    def test_conversation_objective_never_routes_math_or_code_to_workspace(self):
        prompts = (
            'Quanto é 19 vezes 23?',
            'Escreva uma função factorial em Python para n=5.',
        )
        for question in prompts:
            with self.subTest(question=question), \
                 patch.object(self.service, 'local_reply', return_value=None), \
                 patch.object(self.service, 'learned_answer', return_value=None), \
                 patch.object(self.service, 'knowledge_answer', return_value=None):
                response = self.service.reply(
                    [{'role': 'user', 'content': question}], objective='conversation'
                )
            expected = 'execution-engine' if question.startswith('Quanto') else 'deterministic-reasoning'
            self.assertEqual(response['backend'], expected)
            self.assertEqual(response['intent'], 'conversation')
            self.assertFalse(response.get('tool_call'))
            self.assertNotIn('inspecione o projeto', response['text'].lower())


if __name__ == '__main__':
    unittest.main()
