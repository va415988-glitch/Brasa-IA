import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'python'))
import engineering_insights as insights


class EngineeringInsightsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='brasa-insights-')
        self.root = Path(self.temp.name)
        files = {
            'app/billing.py': 'def total(items):\n    return sum(items)\n\n\nclass Invoice:\n    pass\n',
            'app/api.py': 'from app.billing import total\n\n\ndef handler(request):\n    return total(request)\n',
            'app/report.py': 'import app.billing\n# total(items) aparece só em comentário\n',
            'tests/test_billing.py': 'import unittest\nfrom app.billing import total\n\n'
                                     'class T(unittest.TestCase):\n    def test_total(self):\n        self.assertEqual(total([1]), 1)\n',
            'web/util.js': 'export function slug(value) { return value.toLowerCase(); }\n',
            'web/page.js': "import {slug} from './util.js';\nel.innerHTML = slug(name);\n",
            'web/util.test.js': "import test from 'node:test';\ntest('slug', () => {});\n",
            'config.py': 'API_KEY = "abcd1234efgh5678ijkl"\nEXAMPLE_TOKEN = "your-token-here"\nDEBUG = True\n',
            'loader.py': 'import pickle, subprocess\n\n\ndef load(path):\n    subprocess.run(path, shell=True)\n'
                         '    return pickle.load(open(path, "rb"))\n',
            'package.json': json.dumps({'dependencies': {'express': '^4.19.0', 'leftpad': '*', 'lib': 'github:me/lib'},
                                        'devDependencies': {'jest': '29.7.0'}}),
            'requirements.txt': 'requests==2.32.3\nnumpy\n-e git+https://example.test/pkg.git#egg=pkg\n',
            'Cargo.toml': '[package]\nname = "x"\nversion = "0.1.0"\n[dependencies]\nserde = "1"\nlocal = { path = "../local" }\n',
            'Cargo.lock': '',
        }
        for relative, content in files.items():
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding='utf-8')

    def tearDown(self):
        self.temp.cleanup()

    def test_references_classify_definition_import_call_and_tests(self):
        result = insights.find_references(str(self.root), 'total')
        self.assertEqual(result['counts']['definition'], 1)
        self.assertEqual(result['definitions'][0]['path'], 'app/billing.py')
        kinds = {(row['path'], row['kind']) for row in result['references']}
        self.assertIn(('app/api.py', 'import'), kinds)
        self.assertIn(('app/api.py', 'call'), kinds)
        self.assertEqual(result['test_files'], ['tests/test_billing.py'])
        # Comentários não contam como uso.
        self.assertNotIn('app/report.py', result['files'])
        self.assertFalse(result['executed_workspace_code'])
        with self.assertRaises(ValueError):
            insights.find_references(str(self.root), 'rm -rf /')

    def test_change_impact_finds_importers_tests_and_risk(self):
        result = insights.change_impact(str(self.root), path='app/billing.py')
        self.assertEqual(result['production_dependents'], ['app/api.py', 'app/report.py'])
        self.assertIn('tests/test_billing.py', result['affected_tests'])
        self.assertEqual(result['risk'], 'medium')
        self.assertEqual(result['exported_symbols'], ['total', 'Invoice'])
        js = insights.change_impact(str(self.root), path='web/util.js')
        self.assertEqual(js['production_dependents'], ['web/page.js'])
        self.assertIn('web/util.test.js', js['affected_tests'])
        with self.assertRaises(ValueError):
            insights.change_impact(str(self.root))
        with self.assertRaises(ValueError):
            insights.change_impact(str(self.root), path='../fora.py')

    def test_discover_tests_reports_frameworks_and_gaps(self):
        result = insights.discover_tests(str(self.root))
        self.assertIn('unittest', result['frameworks'])
        self.assertIn('jest', result['frameworks'])
        self.assertIn('node:test', result['frameworks'])
        self.assertEqual(result['test_file_count'], 2)
        self.assertGreaterEqual(result['estimated_test_cases'], 2)
        self.assertIn('loader.py', result['untested_candidates'])
        self.assertNotIn('app/billing.py', result['untested_candidates'])

    def test_security_scan_masks_secrets_and_skips_placeholders(self):
        result = insights.security_scan(str(self.root))
        rules = {(row['rule_id'], row['path']) for row in result['findings']}
        self.assertIn(('secret-hardcoded-credential', 'config.py'), rules)
        self.assertIn(('py-shell-true', 'loader.py'), rules)
        self.assertIn(('py-unsafe-deserialization', 'loader.py'), rules)
        self.assertIn(('debug-mode-enabled', 'config.py'), rules)
        self.assertIn(('xss-inner-html', 'web/page.js'), rules)
        secret = next(row for row in result['findings'] if row['rule_id'] == 'secret-hardcoded-credential')
        self.assertNotIn('abcd1234efgh5678ijkl', secret['excerpt'])
        self.assertEqual(sum(row['path'] == 'config.py' and row['line'] == 2 for row in result['findings']), 0)
        high_only = insights.security_scan(str(self.root), min_severity='high')
        self.assertTrue(all(row['severity'] in {'critical', 'high'} for row in high_only['findings']))
        with self.assertRaises(ValueError):
            insights.security_scan(str(self.root), min_severity='urgente')

    def test_dependency_audit_flags_unpinned_sources_and_lockfiles(self):
        result = insights.dependency_audit(str(self.root))
        kinds = {(row['kind'], row.get('dependency'), row.get('path')) for row in result['findings']}
        self.assertIn(('unpinned', 'leftpad', 'package.json'), kinds)
        self.assertIn(('non-registry', 'lib', 'package.json'), kinds)
        self.assertIn(('unpinned', 'numpy', 'requirements.txt'), kinds)
        self.assertIn(('non-registry', 'local', 'Cargo.toml'), kinds)
        self.assertIn(('missing-lockfile', None, 'package.json'), kinds)
        self.assertNotIn(('missing-lockfile', None, 'Cargo.toml'), kinds)
        self.assertFalse(result['network_used'])
        jest = next(dep for manifest in result['manifests'] for dep in manifest['dependencies'] if dep['name'] == 'jest')
        self.assertEqual(jest['status'], 'pinned')

    def test_cli_contract_rejects_unknown_operations_and_arguments(self):
        script = ROOT / 'python' / 'engineering_insights.py'
        ok = subprocess.run([sys.executable, str(script), '--workspace', str(self.root), '--operation', 'discover_tests'],
                            capture_output=True, text=True, timeout=30)
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertEqual(json.loads(ok.stdout)['schema'], 'engineering-tests-discovery/v1')
        for operation, arguments in [('apagar_tudo', '{}'), ('security_scan', '{"executar": true}'), ('code_references', '[]')]:
            failed = subprocess.run([sys.executable, str(script), '--workspace', str(self.root), '--operation', operation,
                                     '--arguments', arguments], capture_output=True, text=True, timeout=30)
            self.assertEqual(failed.returncode, 1, operation)


class PlannerTriggerTests(unittest.TestCase):
    def test_engineering_requests_select_static_analysis_tools(self):
        from agent_planner import AgentPlanner
        cases = {
            'Faça uma revisão de segurança do projeto': 'security_scan',
            'Há vulnerabilidades no código do projeto?': 'security_scan',
            'Audite as dependências do projeto': 'dependency_audit',
            'Quais testes existem no projeto?': 'discover_tests',
            'Onde a função `parse_config` é usada?': 'code_references',
            'Qual o impacto de mudar app/billing.py?': 'change_impact',
        }
        for question, tool in cases.items():
            with self.subTest(question=question):
                self.assertEqual((AgentPlanner.workspace_read_request(question) or ('',))[0], tool)
        # Perguntas conceituais ou sobre falhas não disparam análise do workspace.
        for question in ('O que é um lockfile?', 'Quais vulnerabilidades o Log4Shell explora?', 'Quais testes falharam?',
                         'Crie testes para o módulo de cobrança'):
            with self.subTest(question=question):
                self.assertIsNone(AgentPlanner.workspace_read_request(question))


if __name__ == '__main__':
    unittest.main()
