import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pretrain"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))
from sft import EOS, IGNORE, encode_conversation  # noqa: E402


def fake_encode(text):
    return [ord(ch) % 50 + 10 for ch in text]


class EncodeConversationTest(unittest.TestCase):
    def test_only_assistant_tokens_and_eos_are_trained(self):
        turns = [("user", "oi"), ("assistant", "ola"), ("user", "tchau"), ("assistant", "fui")]
        tokens, labels = encode_conversation(turns, fake_encode, 256)
        self.assertEqual(len(tokens), len(labels))
        trained = [t for t, l in zip(tokens, labels) if l != IGNORE]
        self.assertEqual(trained, fake_encode("ola") + [EOS] + fake_encode("fui") + [EOS])
        prompt = fake_encode("<|user|>\noi\n<|assistant|>\n")
        self.assertEqual(labels[:len(prompt)], [IGNORE] * len(prompt))

    def test_drops_examples_longer_than_context(self):
        self.assertIsNone(encode_conversation([("user", "x" * 100), ("assistant", "y")], fake_encode, 50))

    def test_long_conversation_keeps_the_turns_that_fit(self):
        turns = [("user", "oi"), ("assistant", "ola"), ("user", "x" * 100), ("assistant", "y")]
        tokens, labels = encode_conversation(turns, fake_encode, 60)
        expected, _ = encode_conversation(turns[:2], fake_encode, 60)
        self.assertEqual(tokens, expected)
        self.assertEqual(labels[-1], EOS)

    def test_requires_an_assistant_answer(self):
        self.assertIsNone(encode_conversation([("user", "oi")], fake_encode, 256))


if __name__ == "__main__":
    unittest.main()
