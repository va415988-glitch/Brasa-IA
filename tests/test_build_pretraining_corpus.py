import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))
from build_pretraining_corpus import curate_records, workspace_records


class PretrainingCorpusTests(unittest.TestCase):
    def test_includes_only_exact_repository_matches_on_allowlist(self):
        registry = [
            {"id": "axum", "title": "Axum", "url": "https://github.com/tokio-rs/axum", "license": "MIT", "language": "en"},
            {"id": "owasp", "title": "OWASP", "url": "https://github.com/OWASP/CheatSheetSeries", "license": "CC-BY-SA-4.0", "language": "en"},
        ]
        documents = [
            {"url": "https://raw.githubusercontent.com/tokio-rs/axum/main/README.md", "text": "Axum documentation text."},
            {"url": "https://raw.githubusercontent.com/OWASP/CheatSheetSeries/main/README.md", "text": "OWASP text."},
            {"url": "https://example.org/unknown", "text": "Unmatched text."},
        ]
        records, rejected = curate_records(
            documents, registry, {"MIT"},
            documents_path="corpus/raw/learned_topics.jsonl",
            registry_path="corpus/programming_sources.jsonl",
        )
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["license"], "MIT")
        self.assertEqual(records[0]["license_reference"], "https://github.com/tokio-rs/axum")
        self.assertEqual(records[0]["source_url"], documents[0]["url"])
        self.assertEqual(rejected["unmatched_or_disallowed_source"], 2)

    def test_deduplicates_text_and_keeps_license_review_status_explicit(self):
        registry = [{"id": "python", "title": "Python", "url": "https://github.com/python/cpython", "license": "PSF License", "language": "en"}]
        document = {"url": "https://raw.githubusercontent.com/python/cpython/main/README.rst", "text": "Python source text."}
        records, rejected = curate_records(
            [document, document], registry, {"PSF License"},
            documents_path="raw.jsonl", registry_path="registry.jsonl",
        )
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["license_status"], "declared-in-local-registry-not-legally-reviewed")
        self.assertEqual(rejected["duplicate_text"], 1)

    def test_workspace_ingest_is_scoped_and_keeps_local_provenance(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "Documentacoes").mkdir()
            (root / "python" / "data").mkdir(parents=True)
            (root / "runtime" / "src").mkdir(parents=True)
            (root / "Documentacoes" / "guide.md").write_text("Uma explicação em português.\n", encoding="utf-8")
            (root / "python" / "example.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
            (root / "python" / "data" / "excluded.py").write_text("third-party payload\n", encoding="utf-8")
            (root / "runtime" / "target").mkdir()
            (root / "runtime" / "target" / "generated.rs").write_text("generated output\n", encoding="utf-8")

            records = workspace_records(root)

        self.assertEqual(len(records), 2)
        self.assertEqual({record["path"] for record in records}, {
            "Documentacoes/guide.md", "python/example.py",
        })
        self.assertTrue(all(record["license"] == "user-provided-local" for record in records))
        self.assertTrue(all(record["license_status"] == "local-workspace-user-authorized-not-legal-review" for record in records))


if __name__ == "__main__":
    unittest.main()