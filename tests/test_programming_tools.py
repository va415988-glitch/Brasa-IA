import json
from pathlib import Path
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'python'))
from code_intelligence import inspect, describe, lexical_observations
from programming_checks import verify, run_command


class ProgrammingToolsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='programming-tools-')
        self.root = Path(self.temporary.name)
    def tearDown(self): self.temporary.cleanup()
    def write(self, path, content):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        return target

    def test_python_file_ast_tracks_scope_returns_calls_and_async(self):
        self.write('logic.py', 'import math\nasync def load(x):\n    return math.ceil(x)\nclass Box:\n    def total(self, rows):\n        def hidden(): return 999\n        return sum(rows)\n')
        result = inspect(self.root, 'logic.py')
        symbols = {row['qualified_name']: row for row in result['symbols']}
        self.assertEqual(result['files_scanned'], 1)
        self.assertTrue(symbols['load']['async'])
        self.assertEqual(symbols['Box.total']['returns'], ['sum(rows)'])
        self.assertEqual(symbols['Box.total']['calls'], ['sum'])
        self.assertEqual(symbols['Box.total.hidden']['returns'], ['999'])
        self.assertEqual(symbols['load']['line'], 2)
        self.assertIn('logic.py:5', describe(result))
        self.assertIn('math.ceil(x)', describe(result))

    def test_inspection_never_imports_user_code(self):
        marker = self.root / 'executed'
        self.write('logic.py', f'from pathlib import Path\nPath({str(marker)!r}).write_text("unsafe")\ndef total(a,b): return a+b\n')
        self.assertFalse(inspect(self.root)['executed_workspace_code'])
        self.assertFalse(marker.exists())

    def test_typescript_ast_handles_exports_arrow_functions_and_methods(self):
        self.write('logic.ts', 'export function join(a:string,b:string){return a+b;}\nexport const twice=(n:number)=>n*2;\nexport class Box { size(){return 0;} }\n')
        result = inspect(self.root, 'logic.ts')
        symbols = {row['qualified_name']: row for row in result['symbols']}
        self.assertEqual(result['parsers']['logic.ts'], 'typescript-ast')
        self.assertEqual(symbols['twice']['returns'], ['n*2'])
        self.assertEqual(symbols['Box.size']['returns'], ['0'])
        self.assertIn('join', symbols)

    def test_malformed_code_reports_syntax_diagnostic(self):
        self.write('bad.py', 'def broken(\n')
        self.write('bad.ts', 'export function broken( {\n')
        result = inspect(self.root)
        self.assertEqual({row['path'] for row in result['diagnostics']}, {'bad.py', 'bad.ts'})
        self.assertIn('Erro de sintaxe', describe(result))

    def test_lexical_fallback_has_explicit_limit_and_rust_name(self):
        self.write('lib.rs', 'pub fn calculate(x:i32)->i32 { x+1 }\n')
        result = inspect(self.root)
        self.assertEqual(result['symbols'][0]['name'], 'calculate')
        self.assertIn('somente navegação lexical', describe(result))

    def test_inspection_rejects_traversal_and_symlink(self):
        for path in ('../outside.py', '/tmp/outside.py'):
            with self.assertRaises(ValueError): inspect(self.root, path)
        self.write('source.py', 'x=1')
        (self.root / 'link.py').symlink_to(self.root / 'source.py')
        with self.assertRaises(ValueError): inspect(self.root, 'link.py')

    def test_node_tests_without_package_are_discovered_and_executed(self):
        self.write('tests/total.test.cjs', "const {test}=require('node:test');const assert=require('node:assert/strict');test('sum',()=>assert.equal(3+4,7));")
        result = verify(self.root)
        self.assertEqual(result['check'], 'node-test')
        self.assertTrue(result['passed'], result)
        self.assertEqual(result['tests_executed'], 1)

    def test_empty_unittest_suite_does_not_claim_success(self):
        self.write('tests/test_empty.py', 'import unittest\nclass Empty(unittest.TestCase): pass\n')
        result = verify(self.root, 'unittest')
        self.assertTrue(result['executed'])
        self.assertFalse(result['passed'])
        self.assertEqual(result['tests_executed'], 0)

    def test_large_stdout_and_stderr_drain_without_deadlock(self):
        self.write('test_output.py', "import sys, unittest\nclass Stream(unittest.TestCase):\n    def test_output(self):\n        sys.stdout.write('a'*300000)\n        sys.stderr.write('b'*300000)\n        self.assertEqual(2+2,4)\n")
        result = verify(self.root, 'unittest')
        self.assertTrue(result['passed'], result['stderr'][-200:])
        self.assertTrue(result['truncated'])
        self.assertLessEqual(len(result['stdout']), 12000)
        self.assertIn('Ran 1 test', result['stderr'])

    def test_syntax_check_never_executes_code_and_explains_scope(self):
        self.write('program.py', 'raise RuntimeError("do not execute me")\n')
        result = verify(self.root)
        self.assertTrue(result['passed'])
        self.assertEqual(result['verification_kind'], 'syntax')
        self.assertIsNone(result['tests_executed'])
        self.assertIn('testes de comportamento não foram executados', result['summary'])
        self.assertFalse((self.root / '__pycache__').exists())
        self.write('program.py', 'def broken(\n')
        self.assertFalse(verify(self.root)['passed'])

    def test_no_checker_or_missing_program_are_not_success(self):
        result = verify(self.root)
        self.assertFalse(result['passed'])
        self.assertFalse(result['executed'])
        missing = run_command(['__missing_programming_verifier__'], self.root)
        self.assertFalse(missing['passed'])
        self.assertFalse(missing['executed'])
        self.assertFalse(missing['truncated'])

    def test_profile_and_paths_reject_arbitrary_commands(self):
        for profile in ('sh -c echo unsafe', 'pip-install'):
            with self.assertRaises(ValueError): verify(self.root, profile)
        with self.assertRaises(ValueError): verify(self.root, 'auto', '../outside.py')

    def test_timeout_reaps_child_process_group(self):
        started = time.monotonic()
        result = run_command([sys.executable, '-c', 'import subprocess,sys,time; subprocess.Popen([sys.executable,"-c","import time; time.sleep(20)"]); time.sleep(20)'], self.root, timeout=.2)
        self.assertTrue(result['timed_out'])
        self.assertFalse(result['passed'])
        self.assertLess(time.monotonic() - started, 3)

    def test_changed_path_selects_its_project_and_all_checks_every_project(self):
        for name in ('a', 'b'):
            self.write(f'{name}/package.json', json.dumps({'scripts': {'test': 'node --test --test-reporter=tap'}}))
            self.write(f'{name}/value.test.cjs', "const {test}=require('node:test');test('local',()=>{});")
        self.write('shell.sh', 'if broken; then\n')
        selected = verify(self.root, 'auto', 'b/value.test.cjs')
        self.assertTrue(selected['passed'])
        self.assertEqual(selected['project_path'], 'b')
        combined = verify(self.root, 'all')
        self.assertFalse(combined['passed'])
        tested = {row['project_path'] for row in combined['results'] if row['check'] == 'npm-test'}
        self.assertEqual(tested, {'a', 'b'})
        self.assertFalse(next(row for row in combined['results'] if row['check'] == 'shell-syntax')['passed'])


if __name__ == '__main__': unittest.main()
