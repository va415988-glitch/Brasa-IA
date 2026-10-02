import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))
import learning
from build_knowledge_index import subject_tokens, topic_matches
from source_evidence import assess_sources, source_priority, topic_context_matches, topic_from_question


class LearningTests(unittest.TestCase):
    def test_canonical_topic_removes_depth_instruction(self):
        self.assertEqual(learning.canonical_topic('Rust profundamente'), 'Rust')
        self.assertEqual(learning.canonical_topic('  React em profundidade  '), 'React')
        self.assertEqual(learning.canonical_topic('https://github.com/freeCodeCamp/freeCodeCamp.git'), 'freeCodeCamp')
        self.assertEqual(learning.source_url_from_topic('Estude https://github.com/freeCodeCamp/freeCodeCamp.git'), 'https://github.com/freeCodeCamp/freeCodeCamp.git')

    def test_repository_technologies_are_inferred_as_separate_competencies(self):
        documents = [
            {'title': 'package.json', 'url': 'https://github.com/example/repo/package.json',
             'text': '{ "name": "example", "scripts": {"test": "node test.js"} }'},
            {'title': 'app.js', 'url': 'https://github.com/example/repo/app.js',
             'text': 'import fs from "node:fs"; const run = () => fs.readFileSync("x");'},
            {'title': 'queries.sql', 'url': 'https://github.com/example/repo/queries.sql',
             'text': 'SELECT id FROM users INNER JOIN teams ON teams.id = users.team_id;'},
        ]
        self.assertEqual(learning.infer_technologies(documents), ['JavaScript', 'Node.js', 'SQL'])

    def test_repository_mentions_do_not_create_false_competencies(self):
        documents = [{'title': 'README', 'url': 'https://github.com/example/repo/README.md',
                      'text': 'This project mentions Python, SQL, C# and Machine Learning as future goals.'}]
        self.assertEqual(learning.infer_technologies(documents), [])

    def test_readme_snippets_do_not_create_language_competencies(self):
        documents = [
            {'title': 'README', 'url': 'https://github.com/example/rustlings/README.md',
             'text': 'Examples mention JavaScript, C++ and HTML, but the project is Rust.'},
            {'title': 'exercise', 'url': 'https://raw.githubusercontent.com/example/rustlings/main/exercises/01_variables/README.md',
             'text': 'Rust variables and ownership examples. ' * 20},
        ]
        self.assertEqual(learning.infer_technologies(documents), [])

    def test_language_competency_requires_repository_artifact(self):
        documents = [
            {'title': 'README', 'url': 'https://github.com/example/repo/README.md',
             'text': 'JavaScript examples use const and require. ' * 20},
            {'title': 'app.js', 'url': 'https://raw.githubusercontent.com/example/repo/main/src/app.js',
             'text': 'const answer = 42; export default answer;'},
        ]
        self.assertEqual(learning.infer_technologies(documents), ['JavaScript', 'Node.js'])

    def test_framework_is_detected_from_its_real_rust_repository(self):
        documents = [
            {'title': 'README', 'url': 'https://github.com/tokio-rs/axum/README.md',
             'text': 'Axum is a web framework for Rust.' * 20},
            {'title': 'main.rs', 'url': 'https://raw.githubusercontent.com/tokio-rs/axum/main/examples/hello-world.rs',
             'text': 'use axum::{routing::get, Router}; fn main() {}'},
        ]
        self.assertIn('Axum', learning.infer_technologies(documents))

    def test_manifests_and_documentation_do_not_prove_node_or_sql(self):
        documents = [
            {'title': 'package.json', 'url': 'https://github.com/example/security/package.json',
             'text': '{"scripts":{"test":"node test.js"}}'},
            {'title': 'schema validation', 'url': 'https://raw.githubusercontent.com/example/security/main/schema-validation.md',
             'text': 'SQL schema validation documentation. ' * 20},
        ]
        self.assertEqual(learning.infer_technologies(documents), [])

    def test_auxiliary_automation_does_not_define_repository_language(self):
        documents = [
            {'title': 'Generate index', 'url': 'https://github.com/example/security/scripts/Generate.py',
             'text': 'Python script generates the security index. ' * 20},
            {'title': 'README', 'url': 'https://github.com/example/security/README.md',
             'text': 'Security guidance and cheat sheets. ' * 20},
        ]
        self.assertNotIn('Python', learning.infer_technologies(documents))

    def test_specialized_curriculum_is_created_for_sql(self):
        curriculum = learning.build_curriculum('SQL', [{'text': 'relational joins indexes transactions'}])
        ids = {item['id'] for level in curriculum['levels'] for item in level['concepts']}
        self.assertTrue({'relational-model', 'joins', 'indexes', 'transactions'} <= ids)
    def test_generic_subject_extraction_does_not_turn_visual_work_into_research(self):
        self.assertEqual(topic_from_question('O que é ZirconFable999?'), 'ZirconFable999')
        self.assertEqual(topic_from_question('Como usar ZirconFable999 para rotas?'), 'ZirconFable999')
        self.assertEqual(topic_from_question('Explique o framework ZirconFable999, com exemplos'), 'ZirconFable999')
        self.assertIsNone(topic_from_question('Explique como projetar uma API REST em Rust com Axum'))
        self.assertEqual(topic_from_question('Implemente uma API em ZirconFable999'), 'ZirconFable999')
        self.assertIsNone(topic_from_question('Cria uma tela de login com animações legais'))

    def test_ambiguous_language_name_requires_language_context(self):
        app = {
            'title': 'Google Go',
            'url': 'https://play.google.com/store/apps/details?id=com.google.android.apps.searchlite',
            'text': 'Google Go is an application for searching the web and reading results. ' * 20,
        }
        self.assertFalse(topic_context_matches('Go', app['title'], app['text']))
        self.assertEqual(assess_sources('Go', [app])['status'], 'unverified')
        language = {
            'title': 'Go programming language',
            'url': 'https://go.dev/doc/',
            'text': 'Go is an open source programming language with a compiler, packages and goroutines. ' * 20,
        }
        self.assertTrue(topic_context_matches('Go', language['title'], language['text']))
        self.assertEqual(assess_sources('Go', [language])['status'], 'provisional')

    def test_evidence_never_treats_one_matching_page_as_proof(self):
        page = {'title': 'ZirconFable999 docs', 'url': 'https://docs.alpha.test/zircon',
                'text': 'ZirconFable999 describes handlers and routes. ' * 12}
        self.assertEqual(assess_sources('ZirconFable999', [page])['status'], 'provisional')
        other = {**page, 'url': 'https://reference.beta.test/zircon',
                 'text': 'The ZirconFable999 API offers routing and shared state. ' * 12}
        self.assertEqual(assess_sources('ZirconFable999', [page, other])['status'], 'corroborated')
        self.assertEqual(assess_sources('ZirconFable999', [page, {**page, 'url': other['url']}])['status'], 'provisional')
        same_organization = {**page, 'url': 'https://blog.alpha.test/zircon'}
        self.assertEqual(assess_sources('ZirconFable999', [page, same_organization])['status'], 'provisional')
        fiction = {**page, 'text': 'ZirconFable999 is a fictional framework from a story. ' * 12}
        self.assertTrue(assess_sources('ZirconFable999', [fiction])['fiction_warnings'])

    def test_short_page_does_not_inflate_independent_source_count(self):
        accepted = {'title': 'Zig reference', 'url': 'https://deepwiki.com/zig/reference',
                    'text': 'Zig documents its syntax and compiler tooling. ' * 12}
        too_short = {'title': 'Zig docs', 'url': 'https://another.example/zig',
                     'text': 'Zig documentation is here. ' * 8}
        evidence = assess_sources('Zig', [accepted, too_short])
        self.assertEqual(evidence['status'], 'provisional')
        self.assertEqual(len(evidence['sources']), 1)
        self.assertEqual(evidence['independent_hosts'], 1)

    def test_project_domain_is_prioritized_without_claiming_officiality(self):
        project = {'title': 'Zig Language Reference', 'url': 'https://ziglang.org/documentation/master/'}
        wiki = {'title': 'Zig reference', 'url': 'https://deepwiki.com/ziglang/zig/reference'}
        self.assertGreater(source_priority('Zig', project), source_priority('Zig', wiki))

    def test_learning_rejects_matching_title_with_unrelated_body(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            page = {'title': 'ZirconFable999 docs', 'url': 'https://example.org/zircon',
                    'text': 'A guide to gardening and growing vegetables. ' * 20}
            with patch.object(learning, 'CORPUS', root / 'knowledge.jsonl'), \
                 patch.object(learning, 'INDEX', root / 'index.json'), \
                 patch.object(learning, 'RAW', root / 'raw.jsonl'):
                self.assertEqual(learning.persist_pages([page], 'ZirconFable999', 'learned/ZirconFable999'), 0)
                self.assertFalse((root / 'knowledge.jsonl').exists())

    def test_learning_does_not_index_page_marked_as_fiction(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            page = {'title': 'ZirconFable999 docs', 'url': 'https://example.org/zircon',
                    'text': 'ZirconFable999 is a fictional framework from a novel. ' * 20}
            with patch.object(learning, 'CORPUS', root / 'knowledge.jsonl'), \
                 patch.object(learning, 'INDEX', root / 'index.json'), \
                 patch.object(learning, 'RAW', root / 'raw.jsonl'):
                self.assertEqual(learning.persist_pages([page], 'ZirconFable999', 'learned/ZirconFable999'), 0)
                self.assertFalse((root / 'knowledge.jsonl').exists())

    def test_technical_names_are_not_lost_or_confused(self):
        for topic, title in [('C++', 'C++ reference'), ('C#', 'C# language guide'),
                             ('.NET', '.NET documentation'), ('Node.js', 'Node.js API'),
                             ('R', 'The R Project'), ('NovaFlux', 'NovaFlux framework docs')]:
            with self.subTest(topic=topic):
                self.assertTrue(subject_tokens(topic))
                self.assertTrue(topic_matches(topic, title))
        self.assertFalse(topic_matches('Java', 'JavaScript guide'))
        self.assertFalse(topic_matches('C++', 'C# guide'))

    def test_persist_pages_is_retrievable_after_index_rebuild(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            page = {"title": "Java classes", "url": "https://example.org/classes",
                    "text": "Java classes methods inheritance objects. " * 40}
            with patch.object(learning, "CORPUS", root / "knowledge.jsonl"), \
                 patch.object(learning, "INDEX", root / "index.json"), \
                 patch.object(learning, "RAW", root / "raw.jsonl"):
                self.assertEqual(learning.persist_pages([page], "Java", "proactive-learning"), 1)
                self.assertEqual(learning.persist_pages([page], "Java", "proactive-learning"), 0)
                index = json.loads((root / "index.json").read_text())
                self.assertEqual(index["documents"][0]["topic"], "Java")
                self.assertIn("inheritance", index["postings"])

    def test_chat_research_registers_general_competency_and_practice_contract(self):
        pages = [
            {'title': 'Planejamento de projetos — guia', 'url': 'https://plan.example.org/guide',
             'text': 'Planejamento de projetos define objetivo, escopo, etapas, riscos e verificação. ' * 12},
            {'title': 'Planejamento de projetos — estudo', 'url': 'https://research.example.net/study',
             'text': 'Planejamento de projetos pode ser avaliado por marcos, recursos, alternativas e critérios. ' * 12},
        ]
        with tempfile.TemporaryDirectory() as directory:
            state = learning.AgentState(Path(directory) / 'skills.json')
            with patch.object(learning, 'AGENT_STATE', state), \
                 patch.object(learning, 'run_skill_lab', return_value={'status': 'practice_unavailable', 'tasks': []}):
                result = learning.register_research_result(
                    'planejamento de projetos', pages, search_query='planejamento de projetos reliable frameworks')
            self.assertEqual(result['skill']['learning_contract']['domain'], 'planning')
            self.assertEqual(result['skill']['practice']['passed'], 0)
            self.assertEqual(result['laboratory']['status'], 'practice_unavailable')
            self.assertFalse(result['skill']['evaluation']['ready'])

    def test_topic_research_updates_index_and_deduplicates(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            page = {"title": "Java documentation", "url": "https://example.org/java",
                    "text": "Java classes and methods. " * 40}
            with patch.object(learning, "CORPUS", root / "knowledge.jsonl"), \
                 patch.object(learning, "INDEX", root / "index.json"), \
                 patch.object(learning, "RAW", root / "raw.jsonl"), \
                 patch.object(learning, "_research", return_value={"pages": [page]}):
                job = learning.start("Java")
                for _ in range(100):
                    result = learning.snapshot(job["id"])
                    if result["status"] != "running":
                        break
                    time.sleep(0.01)
                self.assertEqual(result["status"], "completed", result)
                self.assertEqual(result["documents_added"], 1)
                self.assertEqual(result['evidence']['status'], 'provisional')
                index = json.loads((root / "index.json").read_text())
                self.assertEqual(len(index["documents"]), 1)
                self.assertIn("java", index["postings"])

    def test_zig_learning_retries_for_project_site_and_reports_only_accepted_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            wiki = {'title': 'Zig reference on a wiki', 'url': 'https://deepwiki.com/ziglang/zig/reference',
                    'text': 'Zig documentation and compiler reference examples. ' * 18}
            short = {'title': 'Zig overview', 'url': 'https://short.example/zig',
                     'text': 'Zig is a language. ' * 10}
            project = {'title': 'Zig Language Reference', 'url': 'https://ziglang.org/documentation/master/',
                       'text': 'Zig is a systems programming language with tested examples. ' * 18}
            calls = []

            def research(query, topic):
                calls.append(query)
                return {'pages': [wiki, short] if len(calls) == 1 else [project],
                        'search_results': [1, 2], 'attempts': [{'url': project['url'], 'status': 'opened'}]}

            with patch.object(learning, 'CORPUS', root / 'knowledge.jsonl'), \
                 patch.object(learning, 'INDEX', root / 'index.json'), \
                 patch.object(learning, 'RAW', root / 'raw.jsonl'), \
                 patch.object(learning, '_research', side_effect=research):
                job = learning.start('Zig')
                for _ in range(100):
                    result = learning.snapshot(job['id'])
                    if result['status'] != 'running':
                        break
                    time.sleep(0.01)
                self.assertEqual(result['status'], 'completed', result)
                self.assertIn('official project site', calls[1])
                self.assertEqual(result['evidence']['status'], 'corroborated')
                self.assertEqual(len(result['sources']), 2)
                self.assertEqual(result['sources'][0]['url'], project['url'])
                self.assertEqual(result['documents_added'], 2)
                self.assertTrue(any('Fonte opened' in item['message'] for item in result['logs']))

    def test_proactive_learning_does_not_index_dictionary_result(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pages = [
                {"title": "Sinônimo de explique", "url": "https://example.org/explique",
                 "text": "Significado do verbo explicar. " * 30},
                {"title": "Axum documentation", "url": "https://docs.rs/axum",
                 "text": "Axum Router and State in Rust. " * 30},
            ]
            with patch.object(learning, "CORPUS", root / "knowledge.jsonl"), \
                 patch.object(learning, "INDEX", root / "index.json"), \
                 patch.object(learning, "RAW", root / "raw.jsonl"):
                self.assertEqual(learning.persist_pages(pages, "Explique Rust Axum", search_query="Rust Axum documentation"), 1)
                rows = [json.loads(line) for line in (root / "knowledge.jsonl").read_text().splitlines()]
                self.assertEqual(len(rows), 1)
                self.assertIn("Axum", rows[0]["text"])

    def test_manual_learning_accepts_cpp_and_rejects_other_language(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pages = [
                {"title": "C# guide", "url": "https://example.org/csharp",
                 "text": "This guide describes C# classes and interfaces. " * 20},
                {"title": "C++ reference", "url": "https://example.org/cpp",
                 "text": "C++ templates and standard library examples. " * 20},
            ]
            with patch.object(learning, "CORPUS", root / "knowledge.jsonl"), \
                 patch.object(learning, "INDEX", root / "index.json"), \
                 patch.object(learning, "RAW", root / "raw.jsonl"), \
                 patch.object(learning, "_research", return_value={"pages": pages}):
                job = learning.start("C++")
                for _ in range(100):
                    result = learning.snapshot(job["id"])
                    if result["status"] != "running":
                        break
                    time.sleep(0.01)
                self.assertEqual(result["status"], "completed", result)
                self.assertEqual(result["documents_added"], 1)
                index = json.loads((root / "index.json").read_text())
                self.assertEqual(len(index["documents"]), 1)
                self.assertIn("cplusplus", index["postings"])


if __name__ == "__main__":
    unittest.main()
