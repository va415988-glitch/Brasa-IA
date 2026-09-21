import unittest
from pathlib import Path

from chunk_memory import chunk_text, chunk_text_cached, select_chunks
from tokenizer import ByteBPETokenizer


class ChunkMemoryTests(unittest.TestCase):
    def test_fragmenta_e_preserva_ordem_e_hash(self):
        tokenizer = ByteBPETokenizer.load(Path(__file__).parents[1] / "model" / "tokenizer.json")
        chunks = chunk_text("Rust ownership " * 300, tokenizer, 128)
        self.assertGreater(len(chunks), 1)
        self.assertEqual(chunks[0]["ordinal"], 0)
        self.assertTrue(all(chunk["sha256"] for chunk in chunks))

    def test_selecao_e_verificavel(self):
        tokenizer = ByteBPETokenizer.load(Path(__file__).parents[1] / "model" / "tokenizer.json")
        chunks = chunk_text("alpha beta " * 200 + "Rust ownership " * 200, tokenizer, 128)
        result = select_chunks(chunks, "Rust ownership", 2)
        self.assertEqual(result["schema"], "agent-chunk-memory/v1")
        self.assertTrue(result["verified"])
        self.assertLessEqual(len(result["selected"]), 2)

    def test_cache_reutiliza_a_mesma_lista(self):
        tokenizer = ByteBPETokenizer.load(Path(__file__).parents[1] / "model" / "tokenizer.json")
        cache = {}
        first = chunk_text_cached("conteudo repetido " * 100, tokenizer, 128, cache)
        second = chunk_text_cached("conteudo repetido " * 100, tokenizer, 128, cache)
        self.assertIs(first, second)
        self.assertEqual(len(cache), 1)


if __name__ == "__main__":
    unittest.main()
