import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'python'))
import skill_lab
from curriculum import build, gaps


class SkillLabTests(unittest.TestCase):
    def test_curriculum_has_foundation_practice_and_transfer(self):
        curriculum = build('Rust', [{'text': 'ownership borrowing cargo'}])
        self.assertEqual([level['id'] for level in curriculum['levels']], ['foundation', 'practice', 'transfer'])
        self.assertTrue(gaps(curriculum))
        self.assertTrue(curriculum['completion']['requires_unseen_task'])

    def test_curriculum_extracts_source_concepts_as_unproven_objectives(self):
        curriculum = build('NovaFlux', [{'text': 'This framework uses components, modules and async error handling.'}])
        ids = {item['id'] for level in curriculum['levels'] for item in level['concepts']}
        self.assertTrue({'components', 'modules', 'async', 'error-handling'} <= ids)
        self.assertIn('components', gaps(curriculum))
    @unittest.skipUnless(skill_lab.shutil.which('node'), 'node não instalado')
    def test_practice_budget_limits_the_initial_task_queue(self):
        result = skill_lab.run("TypeScript", max_tasks=2)
        self.assertLessEqual(result["total"], 2)
        self.assertLessEqual(len(result["tasks"]), 2)

    def test_unsupported_domain_is_explicitly_not_mastered(self):
        result = skill_lab.run('NovaFlux')
        self.assertEqual(result['status'], 'practice_unavailable')
        self.assertEqual(result['tasks'], [])

    def test_unsupported_domain_exposes_a_reviewable_practice_plan(self):
        result = skill_lab.run('planejamento de projetos')
        self.assertEqual(result['status'], 'practice_unavailable')
        self.assertEqual(result['tasks'], [])
        self.assertTrue(result['practice_plan'])
        self.assertTrue(result['learning_contract']['practice']['requires_unseen_task'])

    @unittest.skipUnless(skill_lab.shutil.which('bash'), 'bash não instalado')
    def test_bash_task_is_executed_in_isolated_lab(self):
        result = skill_lab.run('Bash')
        self.assertEqual(result['status'], 'verified', result)
        self.assertGreaterEqual(result['total'], 12)
        self.assertEqual(result['passed'], result['total'])
        self.assertIn('shellcheck', {item['name'] for item in result['tasks']})

    @unittest.skipUnless(skill_lab.shutil.which('go'), 'go não instalado')
    def test_go_task_is_executed_in_isolated_lab(self):
        result = skill_lab.run('Go')
        self.assertEqual(result['status'], 'verified', result)
        self.assertGreaterEqual(result['total'], 12)
        self.assertEqual(result['passed'], result['total'])

    @unittest.skipUnless(skill_lab.shutil.which('node'), 'node não instalado')
    def test_typescript_task_is_executed_in_isolated_lab(self):
        result = skill_lab.run('TypeScript')
        self.assertEqual(result['status'], 'verified', result)
        self.assertEqual(result['passed'], result['total'])
        self.assertGreaterEqual(result['total'], 12)

    @unittest.skipUnless(skill_lab.shutil.which('node'), 'node não instalado')
    def test_javascript_task_is_executed_in_isolated_lab(self):
        result = skill_lab.run('JavaScript')
        self.assertEqual(result['status'], 'verified', result)
        self.assertGreaterEqual(result['total'], 5)
        self.assertEqual(result['passed'], result['total'])

    def test_practice_budget_also_covers_recovery_attempts(self):
        task = {"name": "bounded-check", "level": "practice", "command": ["synthetic"]}
        with patch.object(skill_lab, "tasks_for", return_value=[task]), \
             patch.object(skill_lab, "_run", return_value={"passed": False, "returncode": 1, "stderr": "AssertionError"}):
            result = skill_lab.run("Synthetic", max_tasks=1)
        self.assertEqual(result["total"], 1)
        self.assertFalse(result["tasks"][0].get("recovery_scheduled"))

    def test_command_failure_is_recorded(self):
        with patch.object(skill_lab, '_run', return_value={'passed': False, 'returncode': 1}):
            result = skill_lab.run('JavaScript')
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['passed'], 0)
        self.assertEqual(result['total'], 24)
        self.assertTrue(result['tasks'][0]['diagnosis'])
        self.assertTrue(result['tasks'][0]['recovery_scheduled'])
        self.assertTrue(result['tasks'][12]['recovery_attempt'])

    def test_failed_task_is_retried_once_and_can_recover(self):
        task = {'name': 'synthetic-check', 'level': 'practice', 'command': ['synthetic']}
        with patch.object(skill_lab, 'tasks_for', return_value=[task]), \
             patch.object(skill_lab, '_run', side_effect=[
                 {'passed': False, 'returncode': 1, 'stderr': 'AssertionError'},
                 {'passed': True, 'returncode': 0},
             ]):
            result = skill_lab.run('Synthetic')
        self.assertEqual(result['status'], 'recovered')
        self.assertEqual(result['total'], 2)
        self.assertEqual(result['passed'], 1)
        self.assertEqual(result['tasks'][1]['recovery_attempt'], True)

    def test_recovery_failure_keeps_gap_open(self):
        task = {'name': 'synthetic-check', 'level': 'practice', 'command': ['synthetic']}
        with patch.object(skill_lab, 'tasks_for', return_value=[task]), \
             patch.object(skill_lab, '_run', side_effect=[
                 {'passed': False, 'returncode': 1, 'stderr': 'AssertionError'},
                 {'passed': False, 'returncode': 1, 'stderr': 'AssertionError'},
             ]):
            result = skill_lab.run('Synthetic')
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['passed'], 0)
        self.assertTrue(result['tasks'][1]['recovery_attempt'])


if __name__ == '__main__':
    unittest.main()
