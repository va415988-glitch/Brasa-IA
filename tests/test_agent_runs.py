import json
import sys
import tempfile
import threading
import subprocess
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'python'))
from agent_runs import RunEngine, TERMINAL
from tool_registry import ToolRegistry
from agent_planner import AgentPlanner


class RunTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.calls = []

    def engine(self, planner, executor=None):
        engine = RunEngine(self.root / 'runs.sqlite', planner, ToolRegistry(), executor or self.execute)
        self.addCleanup(engine.db.close)
        return engine

    def execute(self, call, workspace):
        self.calls.append(call['tool'])
        if call['tool'] == 'create_file':
            path = Path(workspace) / call['arguments']['path']
            path.write_text(call['arguments']['content'])
            return {'ok': True, 'data': {'path': str(path), 'created': True,
                    'bytes': path.stat().st_size, 'diff': {}, 'artifact': {}}}
        if call['tool'] == 'set_workspace':
            return {'ok': True, 'data': {'workspace': workspace, 'selected': True}}
        if call['tool'] == 'list_files':
            return {'ok': True, 'data': {'workspace': workspace, 'path': '',
                    'entries': [], 'total_entries': 0, 'truncated': False}}
        return {'ok': True, 'data': {'executed': True, 'passed': True, 'elapsed_ms': 1}}

    def start(self, engine, request='request-1'):
        return engine.start('conversation-1', str(self.root), [{'role':'user','content':'Crie dois arquivos e verifique'}], request)['id']

    def wait_for(self, engine, run_id, statuses):
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            run = engine.snapshot(run_id)
            if run['status'] in statuses:
                return run
            time.sleep(.01)
        self.fail(str(engine.snapshot(run_id)))

    def test_multiple_files_approval_and_checks_survive_reopen(self):
        def planner(messages):
            if messages[-1]['role'] == 'tool':
                return {'text':'Arquivos verificados.', 'agent':{'status':'completed'}}
            return {'tool_calls':[{'tool':'create_file','arguments':{'path':name,'content':'hello'}} for name in ['a.txt','b.txt']]}
        engine = self.engine(planner)
        run_id = self.start(engine)
        for expected in ['a.txt', 'b.txt']:
            run = self.wait_for(engine, run_id, {'awaiting_approval'})
            self.assertEqual(run['pending']['arguments']['path'], expected)
            self.assertFalse((self.root / expected).exists())
            with self.assertRaises(ValueError):
                engine.control(run_id, 'approve', 'wrong-id')
            engine.control(run_id, 'approve', run['pending']['id'])
        run = self.wait_for(engine, run_id, TERMINAL)
        self.assertEqual(run['status'], 'completed')
        self.assertEqual(self.calls, ['create_file','create_file','project_checks'])
        self.assertEqual((self.root/'b.txt').read_text(), 'hello')
        reopened = self.engine(planner)
        self.assertEqual(reopened.snapshot(run_id)['events'], run['events'])
        self.assertEqual(self.start(reopened), run_id)
        with self.assertRaises(ValueError):
            reopened.start('conversation-1',str(self.root),[{'role':'user','content':'Outro pedido'}],'request-1')

    def test_empty_research_stops_queue(self):
        engine = self.engine(lambda m: {'tool_calls':[{'tool':'research_web','arguments':{'query':'x'}},{'tool':'list_files','arguments':{}}]},
                             lambda c,w: {'ok':True,'data':{'pages':[]}})
        run = self.wait_for(engine, self.start(engine), TERMINAL)
        self.assertEqual(run['status'], 'blocked')
        self.assertEqual(run['steps'], 1)

    def test_same_workspace_selection_does_not_wait_for_approval(self):
        def planner(messages):
            if messages[-1]['role'] == 'tool':
                return {'text': 'Workspace confirmado.', 'agent': {'status': 'completed'}}
            return {'tool_call': {'tool': 'set_workspace', 'arguments': {'path': str(self.root)}}}
        engine = self.engine(planner)
        run_id = engine.start('same-workspace', str(self.root), [
            {'role': 'user', 'content': 'Confirme o workspace atual.'},
        ], 'same-workspace-1')['id']
        run = self.wait_for(engine, run_id, TERMINAL)
        self.assertEqual(run['status'], 'completed')
        self.assertEqual(self.calls, ['set_workspace'])

    def test_task_contract_and_evidence_ledger_are_persisted(self):
        def planner(messages):
            if messages[-1]['role'] == 'tool':
                return {'text': 'A inspeção terminou com evidência registrada.', 'agent': {'status': 'completed'}}
            return {'tool_call': {'tool': 'list_files', 'arguments': {}}}

        engine = self.engine(planner, lambda call, workspace: {
            'ok': True, 'data': {'workspace': workspace, 'path': '', 'entries': [],
                                  'total_entries': 0, 'truncated': False, 'summary': 'workspace vazio'},
        })
        run_id = engine.start('contract', str(self.root), [{
            'role': 'user', 'content': 'Leia o workspace sem editar arquivos.'
        }], 'contract-1')['id']
        run = self.wait_for(engine, run_id, TERMINAL)
        self.assertEqual(run['contract']['schema'], 'task-contract/v1')
        self.assertEqual(run['contract']['side_effects'], 'read-only')
        self.assertEqual(len(run['evidence']), 1)
        self.assertEqual(run['evidence'][0]['tool'], 'list_files')
        self.assertEqual(run['delivery']['status'], 'completed')
        self.assertTrue(run['delivery']['verified'])

    def test_follow_up_turn_preserves_previous_user_objective(self):
        engine = self.engine(lambda messages, agent_run_context=None: {
            'text': 'A solicitação precisa de evidência adicional.',
            'backend': 'quality-gate', 'agent': {'status': 'blocked', 'stop_reason': 'insufficient_evidence'},
        })
        run_id = engine.start('follow-up', str(self.root), [
            {'role': 'user', 'content': 'Implemente uma interface para organizar minhas tarefas.'},
            {'role': 'assistant', 'content': 'A primeira tentativa não gerou uma proposta.'},
            {'role': 'user', 'content': 'Tente novamente implementar a interface.'},
        ], 'follow-up-1')['id']
        run = self.wait_for(engine, run_id, TERMINAL)
        self.assertIn('Implemente uma interface para organizar minhas tarefas.', run['objective'])
        self.assertIn('Continuação solicitada pelo usuário: Tente novamente implementar a interface.', run['objective'])
        self.assertEqual(run['contract']['intent'], 'change')

    def test_blocked_planner_gets_one_goal_context_recovery(self):
        planning_calls = []

        def planner(messages, agent_run_context=None):
            planning_calls.append(agent_run_context)
            if len(planning_calls) == 1:
                return {
                    'text': 'Ainda não encontrei uma proposta estruturada.',
                    'backend': 'quality-gate', 'agent': {'status': 'blocked', 'stop_reason': 'no_tool_selected'},
                }
            if len(planning_calls) == 2:
                self.assertEqual(agent_run_context['schema'], 'agent-run-context/v1')
                self.assertEqual(agent_run_context['status'], 'recovering')
                self.assertEqual(agent_run_context['recovery_attempt'], 1)
                self.assertIn('Ainda não encontrei', agent_run_context['blockers'][0]['summary'])
                self.assertIn('Implemente uma solução', agent_run_context['objective'])
                return {'tool_call': {'tool': 'list_files', 'arguments': {}}}
            return {'text': 'Inspecionei o workspace e concluí a tarefa.', 'agent': {'status': 'completed'}}

        engine = self.engine(planner)
        run_id = engine.start('goal-recovery', str(self.root), [
            {'role': 'user', 'content': 'Implemente uma solução no workspace e verifique o que existe.'},
        ], 'goal-recovery-1')['id']
        run = self.wait_for(engine, run_id, TERMINAL)

        self.assertEqual(run['status'], 'blocked')
        self.assertEqual(run['goal_state']['schema'], 'agent-goal-state/v1')
        self.assertEqual(run['goal_state']['status'], 'blocked')
        self.assertEqual(run['goal_state']['steps_completed'], 1)
        self.assertEqual(run['goal_state']['evidence_count'], 1)
        self.assertEqual(run['recovery_attempts'], 1)
        self.assertEqual([event['status'] for event in run['events']].count('recovering'), 1)
        self.assertEqual(self.calls, ['list_files'])
        self.assertIn('change-scoped', run['delivery']['pending_criteria'])

    def test_repeated_planner_abstention_stops_after_one_recovery(self):
        planning_calls = []

        def planner(messages, agent_run_context=None):
            planning_calls.append(agent_run_context)
            return {
                'text': 'Não encontrei uma ação segura.', 'backend': 'quality-gate',
                'agent': {'status': 'blocked', 'stop_reason': 'no_tool_selected'},
            }

        engine = self.engine(planner)
        run_id = engine.start('bounded-recovery', str(self.root), [
            {'role': 'user', 'content': 'Crie uma solução para este workspace.'},
        ], 'bounded-recovery-1')['id']
        run = self.wait_for(engine, run_id, TERMINAL)

        self.assertEqual(run['status'], 'blocked')
        self.assertEqual(len(planning_calls), 2)
        self.assertEqual(run['recovery_attempts'], 1)
        self.assertEqual(run['goal_state']['recovery_attempts'], 1)
        self.assertEqual([event['status'] for event in run['events']].count('recovering'), 1)
        self.assertEqual(run['goal_state']['next_action'], 'report_blocker')
        self.assertEqual(self.calls, [])

    def test_planner_cannot_complete_a_change_without_a_write(self):
        engine = self.engine(lambda messages: {
            'text': 'Arquivo criado.', 'agent': {'status': 'completed'},
        })
        run_id = engine.start('false-completion', str(self.root), [
            {'role': 'user', 'content': 'Crie um arquivo app.py no workspace'},
        ], 'false-completion-1')['id']
        run = self.wait_for(engine, run_id, TERMINAL)
        self.assertEqual(run['status'], 'blocked')
        self.assertEqual(run['steps'], 0)
        self.assertFalse(run['delivery']['verified'])
        self.assertIn('change-scoped', run['delivery']['pending_criteria'])
        self.assertEqual(run['contract']['verification']['status'], 'pending')
        self.assertFalse((self.root / 'app.py').exists())

    def test_failed_checks_are_recovered_before_final_block(self):
        calls = []
        def planner(messages):
            calls.append(messages[-1].get('content', ''))
            if len(calls) == 1:
                return {'tool_call': {'tool': 'project_checks', 'arguments': {'check': 'pytest'}}}
            if len(calls) == 2:
                return {'tool_call': {'tool': 'diagnose_project', 'arguments': {}}}
            return {'text': 'A falha foi diagnosticada e aguarda correção.', 'agent': {'status': 'blocked'}}
        def executor(call, workspace):
            if call['tool'] == 'project_checks':
                return {'ok': True, 'data': {'executed': True, 'passed': False, 'elapsed_ms': 1,
                                             'stderr': 'AssertionError: expected 5, got 4'}}
            return {'ok': True, 'data': {'check': 'pytest', 'passed': False, 'category': 'test-failure',
                                         'summary': 'assertion failed', 'evidence': ['AssertionError'],
                                         'next_steps': ['corrigir a implementação']}}
        engine = self.engine(planner, executor)
        run_id = engine.start('recovery', str(self.root), [{'role': 'user', 'content': 'Rode os testes e corrija se falhar'}], 'recovery-1')['id']
        run = self.wait_for(engine, run_id, TERMINAL)
        self.assertEqual(run['status'], 'blocked')
        self.assertEqual([e['result']['tool'] for e in run['events'] if e.get('result')], ['project_checks', 'diagnose_project', 'project_checks'])
        self.assertTrue(any(e['status'] == 'recovering' for e in run['events']))

    def test_cancel_preserves_inflight_observation(self):
        entered, release = threading.Event(), threading.Event()
        def executor(call, workspace):
            entered.set()
            release.wait(3)
            return {'ok':True,'data':{'entries':[]}}
        engine = self.engine(lambda m:{'tool_call':{'tool':'list_files','arguments':{}}}, executor)
        run_id = self.start(engine)
        self.assertTrue(entered.wait(2))
        engine.control(run_id,'cancel')
        with self.assertRaises(ValueError):
            self.start(engine, 'another-request')
        release.set()
        run = self.wait_for(engine,run_id,TERMINAL)
        self.assertEqual(run['status'],'cancelled')
        self.assertEqual(run['steps'],1)
        self.assertTrue(any(e.get('result') for e in run['events']))

    def test_repeat_and_invalid_calls_do_not_execute(self):
        engine = self.engine(lambda m:{'tool_call':{'tool':'list_files','arguments':{}}})
        run = self.wait_for(engine,self.start(engine),TERMINAL)
        self.assertEqual(run['status'],'blocked')
        self.assertEqual(run['steps'],1)
        other = self.engine(lambda m:{'tool_call':{'tool':'read_file','arguments':{'path':3}}})
        run = self.wait_for(other,self.start(other,'invalid'),TERMINAL)
        self.assertEqual(run['status'],'failed')
        self.assertEqual(run['steps'],0)

    def test_restart_does_not_repeat_uncertain_write(self):
        engine = self.engine(lambda m: {})
        run = {'id':'run-interrupted','status':'executing','events':[],'pending':{'tool':'create_file'}}
        with engine.lock:
            engine._save(run)
        reopened = self.engine(lambda m: {})
        self.assertEqual(reopened.snapshot(run['id'])['status'],'interrupted')
        with self.assertRaises(ValueError):
            reopened.control(run['id'],'resume')

    def test_terminal_project_check_requires_approval_and_is_verification_evidence(self):
        def planner(messages):
            if messages[-1]['role'] == 'tool':
                return {'text': 'O perfil de teste passou.', 'agent': {'status': 'completed'}}
            return {'tool_call': {'tool': 'terminal_run', 'arguments': {
                'operation': 'project_check', 'check': 'pytest'
            }}}

        engine = self.engine(planner, lambda call, workspace: {
            'ok': True, 'data': {
                'operation': 'project_check', 'check': 'pytest', 'command': 'python -m pytest -q',
                'executed': True, 'passed': True, 'exit_code': 0, 'stdout': '2 passed', 'stderr': '',
            },
        })
        run_id = engine.start('terminal-check', str(self.root), [
            {'role': 'user', 'content': 'Execute no terminal `pytest`'}
        ], 'terminal-check-1')['id']
        run = self.wait_for(engine, run_id, {'awaiting_approval'} | TERMINAL)
        self.assertEqual(run['status'], 'awaiting_approval')
        self.assertEqual(run['pending']['tool'], 'terminal_run')
        engine.control(run_id, 'approve', run['pending']['id'])
        run = self.wait_for(engine, run_id, TERMINAL)
        self.assertEqual(run['status'], 'completed')
        self.assertTrue(run['delivery']['verified'])
        self.assertEqual(run['contract']['verification']['checks'][0]['tool'], 'terminal_run')

    def test_unexecuted_checks_block(self):
        engine = self.engine(lambda m:{'tool_call':{'tool':'project_checks','arguments':{'check':'auto'}}},
                             lambda c,w:{'ok':True,'data':{'executed':False,'passed':True}})
        run = self.wait_for(engine,self.start(engine),TERMINAL)
        self.assertEqual(run['status'],'blocked')

    def test_explicit_file_plan_is_executable_without_external_model(self):
        planner = AgentPlanner(ToolRegistry())
        request = 'Crie estes arquivos:\n### a.py\n```python\nanswer = 42\n```\n### test_a.py\n```python\nassert True\n```'
        calls = planner.file_plan(request)
        self.assertEqual([c['arguments']['path'] for c in calls], ['a.py','test_a.py'])
        self.assertEqual(calls[0]['arguments']['content'], 'answer = 42')
        self.assertEqual(planner.file_plan(request.replace('a.py','../a.py')), [])

    def test_real_rust_executor_creates_multiple_files_and_runs_tests(self):
        binary = Path(__file__).resolve().parents[1] / 'runtime/target/debug/local_ai_runtime'
        if not binary.exists():
            self.skipTest('Compile o runtime antes do teste de integração')
        from model_server import ModelService
        service = ModelService('checkpoint-inexistente.pt')
        request = '''Crie estes arquivos:
### app.py
```python
def add(a, b):
    return a + b
```
### tests/test_app.py
```python
import unittest
from app import add
class AppTest(unittest.TestCase):
    def test_add(self):
        self.assertEqual(add(2, 3), 5)
```'''
        def executor(call, workspace):
            output = subprocess.run([str(binary)], input=json.dumps(call)+'\n', text=True,
                                    capture_output=True, cwd=workspace, timeout=15, check=True)
            return json.loads(output.stdout)
        engine = self.engine(service.reply, executor)
        run_id = engine.start('integration',str(self.root),[{'role':'user','content':request}],'real')['id']
        for _ in range(2):
            run = self.wait_for(engine,run_id,{'awaiting_approval'} | TERMINAL)
            self.assertEqual(run['status'],'awaiting_approval',run)
            engine.control(run_id,'approve',run['pending']['id'])
        run = self.wait_for(engine,run_id,TERMINAL)
        self.assertEqual(run['status'],'completed',run)
        results = [e['result'] for e in run['events'] if e.get('result')]
        self.assertEqual([r['tool'] for r in results],['create_file','create_file','project_checks'])
        self.assertTrue(results[-1]['data']['executed'])
        self.assertTrue(results[-1]['data']['passed'])
        self.assertIn('Ran 1 test',results[-1]['data']['stderr'])

    def test_autonomous_change_of_existing_files_requires_verification(self):
        (self.root / 'app.py').write_text('def add(a, b):\n    return a - b\n')
        (self.root / 'tests').mkdir()
        (self.root / 'tests' / 'test_app.py').write_text('import unittest\nfrom app import add\n\nclass AppTest(unittest.TestCase):\n    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n')
        planner = lambda messages: (
            {'text': 'Corrigi a implementação e vou verificar a regressão.', 'agent': {'status': 'completed'}}
            if messages[-1]['role'] == 'tool' and any('project_checks' in str(m.get('content')) for m in messages[-2:])
            else {'tool_calls': [
                {'tool': 'edit_file', 'arguments': {'path': 'app.py', 'old_text': 'return a - b', 'new_text': 'return a + b'}},
                {'tool': 'project_checks', 'arguments': {'check': 'pytest'}}
            ]}
        )
        def executor(call, workspace):
            if call['tool'] == 'edit_file':
                path = Path(workspace) / call['arguments']['path']
                text = path.read_text().replace(call['arguments']['old_text'], call['arguments']['new_text'])
                path.write_text(text)
                return {'ok': True, 'data': {'path': str(path), 'updated': True, 'bytes': path.stat().st_size,
                        'backup': 'backup-app.py', 'backup_hash': 'before', 'diff': {},
                        'artifact': {'kind': 'code', 'status': 'applied'}}}
            completed = subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests'], cwd=workspace, capture_output=True, text=True)
            return {'ok': True, 'data': {'executed': True, 'passed': completed.returncode == 0,
                    'elapsed_ms': 1, 'stdout': completed.stdout, 'stderr': completed.stderr}}
        engine = self.engine(planner, executor)
        run_id = engine.start('existing-files', str(self.root), [{'role': 'user', 'content': 'Corrija app.py e confirme que os testes passam'}], 'existing-files-1')['id']
        run = self.wait_for(engine, run_id, {'awaiting_approval'} | TERMINAL)
        self.assertEqual(run['status'], 'awaiting_approval', run)
        engine.control(run_id, 'approve', run['pending']['id'])
        run = self.wait_for(engine, run_id, TERMINAL)
        self.assertEqual(run['status'], 'completed', run)
        self.assertEqual((self.root / 'app.py').read_text(), 'def add(a, b):\n    return a + b\n')
        self.assertTrue(any(e.get('result', {}).get('tool') == 'project_checks' and e['result']['data']['passed'] for e in run['events']))
