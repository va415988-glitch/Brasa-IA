import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pretrain"))
sys.path.insert(0, str(ROOT / "python"))
from build_agent_sft import (Builder, decision, lead, parse_authored, prose_paragraph,  # noqa: E402
                             sentences, tool_message)

RUNTIME = ROOT / "runtime/target/release/local_ai_runtime"


class TextTest(unittest.TestCase):
    def test_readme_paragraph_skips_titles_badges_and_setext(self):
        readme = ("# Nome\n\n[![build](x.svg)](ci)\n\nTítulo\n------\n"
                  "Uma biblioteca para enviar requisições HTTP de forma simples e segura.\n")
        self.assertEqual(prose_paragraph(readme), "Uma biblioteca para enviar requisições HTTP de forma simples e segura.")

    def test_wikipedia_lead_restores_subject_and_drops_empty_parentheses(self):
        self.assertEqual(lead({"title": "Banco de dados", "text": "são conjuntos de arquivos.\nmais"}),
                         "Banco de dados são conjuntos de arquivos.")
        self.assertEqual(lead({"title": "X", "text": "Austrália (, , ), oficialmente Comunidade, é um país."}),
                         "Austrália, oficialmente Comunidade, é um país.")

    def test_sentences_respects_limit(self):
        self.assertEqual(sentences("Primeira frase. Segunda frase. Terceira.", 2), "Primeira frase. Segunda frase.")


class AuthoredTest(unittest.TestCase):
    def test_parses_multiline_turns_and_rejects_malformed(self):
        with tempfile.TemporaryDirectory() as scratch:
            path = Path(scratch) / "a.txt"
            path.write_text("# comentário\nU: oi\nA: linha 1\nlinha 2\n---\nU: só pergunta\n", encoding="utf-8")
            with self.assertRaises(SystemExit):
                parse_authored(path)
            path.write_text("U: oi\nA: linha 1\nlinha 2\n---\nU: a\nA: b\n", encoding="utf-8")
            conversations = parse_authored(path)
        self.assertEqual(len(conversations), 2)
        self.assertEqual(conversations[0][1]["content"], "linha 1\nlinha 2")

    def test_project_file_is_well_formed(self):
        self.assertGreater(len(parse_authored(ROOT / "pretrain/agent_sft_authored.txt")), 50)


class BuilderTest(unittest.TestCase):
    def test_targets_pass_the_product_validator(self):
        builder = Builder()
        messages = [{"role": "user", "content": "O que tem no README?"}]
        tools = ["read_file", "list_files"]
        self.assertTrue(builder.add(messages, tools, decision("consult", "Vou ler.", "Falta o conteúdo.",
                                                              tool="read_file", arguments={"path": "README.md"}), "k", "p", "g"))
        observed = messages + [tool_message({"tool": "read_file", "ok": True,
                                             "data": {"path": "README.md", "content": "Projeto X.", "bytes": 10,
                                                      "offset": 0, "truncated": False}})]
        self.assertTrue(builder.add(observed, tools, decision("answer", "É o Projeto X.", evidence=["obs-1"]), "k", "p", "g"))
        # sem evidência após consultar, ou citando uma observação inexistente: rejeitado
        self.assertFalse(builder.add(observed, tools, decision("answer", "É o Projeto X."), "k", "p", "g"))
        self.assertFalse(builder.add(observed, tools, decision("answer", "É o Projeto X.", evidence=["obs-9"]), "k", "p", "g"))
        # ferramenta fora do catálogo: rejeitado
        self.assertFalse(builder.add(messages, ["list_files"], decision("consult", "Vou ler.", "Falta.", tool="read_file",
                                                                       arguments={"path": "README.md"}), "k", "p", "g"))
        self.assertEqual(len(builder.rows), 2)
        prompt = builder.rows[1]["messages"][0]["content"]
        self.assertTrue(prompt.startswith("Decida answer, consult ou blocked."))
        self.assertEqual(json.loads(builder.rows[1]["messages"][1]["content"])["evidence_ids"], ["obs-1"])

    @unittest.skipUnless(RUNTIME.exists(), "runtime não compilado")
    def test_real_runtime_observation(self):
        from build_agent_sft import Workspace
        with tempfile.TemporaryDirectory() as scratch:
            (Path(scratch) / "README.md").write_text("# Demo\n\nUm projeto de demonstração.\n", encoding="utf-8")
            (Path(scratch) / ".env").write_text("SEGREDO=1\n", encoding="utf-8")
            ws = Workspace(RUNTIME, scratch)
            try:
                result = ws.call("read_file", {"path": "README.md"})
                with self.assertRaises(ValueError):
                    ws.call("read_file", {"path": ".env"})
                files = ws.files()
            finally:
                ws.close()
        self.assertTrue(result["ok"])
        self.assertIn("demonstração", result["data"]["content"])
        self.assertEqual(files, ["README.md"])


if __name__ == "__main__":
    unittest.main()
