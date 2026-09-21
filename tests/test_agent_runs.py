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
            return {'ok': True, 'data': {'path': str(path)}}
        return {'ok': True, 'data': {'executed': True, 'passed': True}}

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
