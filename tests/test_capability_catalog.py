import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'python'))
from agent_planner import AgentPlanner
from capability_catalog import CapabilityCatalog
from skill_router import SkillRouter
from tool_registry import ToolRegistry
from model_server import ModelService


class CapabilityCatalogTests(unittest.TestCase):
    def setUp(self):
        registry = ToolRegistry()
        self.catalog = CapabilityCatalog(registry)
        self.planner = AgentPlanner(registry)
        self.router = SkillRouter(self.catalog, self.planner)
        self.service = ModelService.__new__(ModelService)
        self.service.tools = registry
        self.service.planner = self.planner
        self.service.capability_catalog = self.catalog
        self.service.skill_router = self.router

    def test_catalog_covers_every_valid_contract_without_network_fallback(self):
        snapshot = self.catalog.snapshot()
        registered = {tool for tool in self.catalog.registry.tools if self.catalog.registry.has(tool)}
        described = {item['tool'] for item in snapshot['capabilities']}
        self.assertEqual(registered, described)
        self.assertEqual(snapshot['schema'], 'local-api-catalog/v1')
        self.assertEqual(snapshot['mode'], 'offline-first')
        self.assertGreater(snapshot['external_optional_count'], 0)
        self.assertEqual(snapshot['service_api_count'], 8)
        self.assertEqual(snapshot['count'], len(registered))
        self.assertIn('learning.topic.start', {item['id'] for item in snapshot['service_apis']})
        self.assertTrue(all(isinstance(item['request_schema'], dict) and isinstance(item['response_schema'], dict)
                            for item in snapshot['service_apis']))
        self.assertTrue(all(item['provider'] == 'runtime-rust' for item in snapshot['capabilities']))

    def test_routes_debugging_to_local_verification_and_repair_capabilities(self):
        result = self.router.route('Investigue por que os testes pytest falham e corrija o erro.')
        selected = {item['id'] for item in result['selected_skills']}
        capabilities = {item['id'] for item in result['api_candidates']}
        self.assertIn('test-debugging', selected)
        self.assertIn('project.verify', capabilities)
        self.assertFalse(result['network_requested'])
        self.assertTrue(all(item['network_policy'] != 'external-optional' for item in result['api_candidates']))
        self.assertFalse(result['execution_allowed'])

    def test_web_sources_are_suggested_only_when_task_explicitly_requests_network(self):
        result = self.router.route('Pesquise na internet fontes recentes sobre Rust.')
        selected = {item['id'] for item in result['selected_skills']}
        capabilities = {item['id'] for item in result['api_candidates']}
        self.assertTrue(result['network_requested'])
        self.assertIn('external-research', selected)
        self.assertIn('research.web.search', capabilities)
        self.assertTrue(any(item['network_policy'] == 'external-optional' for item in result['api_candidates']))
        self.assertTrue(all(item['execution'] == 'not_executed_by_router' for item in result['api_candidates']))

    def test_offline_project_research_does_not_route_to_web_provider(self):
        result = self.router.route('Pesquise no código do projeto os arquivos de rotas.')
        self.assertFalse(result['network_requested'])
        self.assertIn('offline-first-research', {item['id'] for item in result['selected_skills']})
        self.assertFalse(any(item['network_policy'] == 'external-optional' for item in result['api_candidates']))

    def test_route_smoke_matrix_covers_all_initial_skill_families(self):
        cases = {
            'Inspecione o projeto e mostre sua estrutura.': 'workspace-inspection',
            'Implemente em Python uma função de cadastro.': 'implementation',
            'Investigue por que os testes pytest falham.': 'test-debugging',
            'Crie uma interface acessível para cadastro.': 'ui-design',
            'Extraia o texto deste PDF local.': 'local-document-analysis',
            'Escreva documentação para a API.': 'technical-writing',
            'Pesquise no código do projeto os arquivos de rotas.': 'offline-first-research',
            'Pesquise na internet fontes recentes sobre Rust.': 'external-research',
            'Monte um roadmap para a migração.': 'task-planning',
        }
        for task, expected in cases.items():
            with self.subTest(task=task):
                route = self.router.route(task)
                selected = {item['id'] for item in route['selected_skills']}
                self.assertIn(expected, selected, route)
                self.assertFalse(route['execution_allowed'])

    def test_live_planner_applies_skill_api_allowlist_and_attaches_route_metadata(self):
        offline = self.service.plan_tool('Pesquise no código do projeto por rotas HTTP.')
        self.assertEqual(offline['tool'], 'search_files')
        self.assertEqual(offline['planner']['strategy'], 'skill-routed-contract-ranking')
        self.assertEqual(offline['skill_routing']['selected_api']['id'], 'workspace.code.search')
        self.assertFalse(offline['skill_routing']['network_requested'])
        self.assertNotIn('research.web.search', offline['skill_routing']['candidate_apis'])

        external = self.service.plan_tool('Pesquise na internet fontes recentes sobre Rust.')
        self.assertEqual(external['tool'], 'research_web')
        self.assertTrue(external['skill_routing']['network_requested'])
        self.assertEqual(external['skill_routing']['selected_api']['network_policy'], 'external-optional')

        interface = self.service.plan_tool('Crie uma interface acessível para cadastro.')
        self.assertEqual(interface['tool'], 'create_web_page')
        self.assertEqual(interface['skill_routing']['skills'][0]['id'], 'ui-design')

    def test_explicit_learning_routes_to_research_pipeline_and_learning_service(self):
        for task in ('Aprenda Go', 'Quero aprender Rust'):
            with self.subTest(task=task):
                result = self.router.route(task)
                selected = {item['id'] for item in result['selected_skills']}
                apis = {item['id']: item for item in result['api_candidates']}
                self.assertIn('knowledge-learning', selected)
                self.assertTrue(result['learning_requested'])
                self.assertTrue(result['network_requested'])
                self.assertIn('learning.topic.start', apis)
                self.assertIn('research.pipeline.run', apis)
                self.assertEqual(apis['learning.topic.start']['kind'], 'service-api')
                self.assertEqual(apis['learning.topic.start']['method'], 'POST')
                self.assertEqual(apis['learning.topic.start']['path'], '/api/learn')
                self.assertTrue(all(item['execution'] == 'not_executed_by_router' for item in apis.values()))

    def test_explicit_combined_web_research_and_learning_selects_composed_skills(self):
        result = self.router.route('Pesquise na internet e aprenda Go')
        selected = {item['id'] for item in result['selected_skills']}
        self.assertIn('external-research', selected)
        self.assertIn('knowledge-learning', selected)
        self.assertFalse(result['needs_clarification'])
        self.assertTrue(result['learning_requested'])
        self.assertTrue(result['network_requested'])
        self.assertIn('learning.topic.start', {item['id'] for item in result['api_candidates']})
        self.assertIn('research.web.search', {item['id'] for item in result['api_candidates']})

    def test_explicit_autonomous_cycle_routes_to_approval_gated_local_service(self):
        result = self.router.route('Execute o próximo ciclo de aprendizado autônomo')
        selected = {item['id'] for item in result['selected_skills']}
        apis = {item['id']: item for item in result['api_candidates']}
        self.assertIn('autonomous-learning-cycle', selected)
        self.assertTrue(result['autonomous_cycle_requested'])
        self.assertTrue(result['network_requested'])
        self.assertIn('learning.autonomous.tick', apis)
        self.assertTrue(apis['learning.autonomous.tick']['requires_approval'])
        self.assertEqual(apis['learning.autonomous.tick']['path'], '/api/v1/learning/autonomous/tick')

    def test_observing_autonomous_learning_does_not_suggest_external_tick(self):
        result = self.router.route('Mostre o estado do aprendizado autônomo')
        self.assertFalse(result['autonomous_cycle_requested'])
        self.assertFalse(result['network_requested'])
        self.assertFalse(any(item['id'] == 'learning.autonomous.tick' for item in result['api_candidates']))

    def test_unknown_task_stays_unrouted_and_requests_clarification(self):
        result = self.router.route('Faça uma coisa.')
        self.assertEqual(result['selected_skills'], [])
        self.assertTrue(result['needs_clarification'])

    def test_empty_or_oversized_task_is_rejected(self):
        with self.assertRaises(ValueError):
            self.router.route(' ')
        with self.assertRaises(ValueError):
            self.router.route('x' * 8001)


if __name__ == '__main__':
    unittest.main()
