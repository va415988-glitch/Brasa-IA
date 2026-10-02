import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))
from chunk_memory import chunk_text
from tokenizer import ByteBPETokenizer


class FastTokenizerPathTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1]
        cls.tokenizer = ByteBPETokenizer.load(root / "model/godmode/godmode-tokenizer-v1.json")

    def test_fast_bpe_preserves_tokens_and_unicode(self):
        samples = [
            "Memória do agente: contexto de 32.768 tokens e português.",
            "def fatorial(n):\n    return 1 if n < 2 else n * fatorial(n - 1)",
            "🧠 ação, revisão — evidência; 483917",
        ]
        for text in samples:
            with self.subTest(text=text[:24]):
                self.assertEqual(self.tokenizer.encode_fast(text), self.tokenizer.encode(text))
                self.assertEqual(self.tokenizer.decode(self.tokenizer.encode_fast(text)), text)

    def test_chunk_memory_prefere_encoder_rapido(self):
        class FastOnly:
            def encode_fast(self, text):
                return list(text.encode("utf-8"))

            def encode(self, text):
                raise AssertionError("encoder lento não deve ser chamado")

            @staticmethod
            def decode(tokens):
                return bytes(tokens).decode("utf-8")

        chunks = chunk_text("a" * 300, FastOnly(), chunk_tokens=128)
        self.assertEqual([item["tokens"] for item in chunks], [128, 128, 44])


if __name__ == "__main__":
    unittest.main()
