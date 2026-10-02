import unittest

from scripts.prepare_ultradata_training import conversation_pairs, has_tool_interaction, normalize_question, retain_lowest_hash


class UltraDataPreparationTests(unittest.TestCase):
    def test_extracts_visible_text_turn_without_tool_interaction(self):
        row = {
            "uuid": "conversation-1",
            "domain": "code",
            "messages": [
                {"role": "system", "content": "system secret instruction"},
                {"role": "user", "content": "Write a small parser."},
                {"role": "assistant", "content": "The parser splits the input into tokens and validates each token.",
                 "reasoning_content": "hidden thought"},
            ],
        }
        pairs = conversation_pairs(row, "Code-Agent")
        self.assertEqual(len(pairs), 1)
        self.assertEqual(pairs[0]["messages"][1]["content"], row["messages"][-1]["content"])
        prompt = pairs[0]["messages"][0]["content"]
        self.assertIn("USER: Write a small parser.", prompt)
        self.assertNotIn("system secret", prompt)
        self.assertNotIn("hidden thought", prompt)

    def test_excludes_whole_conversation_when_tool_evidence_would_be_lost(self):
        rows = [
            {"messages": [
                {"role": "user", "content": "Run the tests."},
                {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "run_shell"}}]},
                {"role": "tool", "content": "tests failed"},
                {"role": "assistant", "content": "The tests completed successfully and the report confirms every assertion passed."},
            ]},
            {"messages": [
                {"role": "user", "content": "Run the tests."},
                {"role": "assistant", "content": "The tests completed successfully and the report confirms every assertion passed."},
            ]},
        ]
        for row in (rows[0], {"messages": [
            {"role": "user", "content": "Run the tests."},
            {"role": "function", "content": "tests failed"},
            {"role": "assistant", "content": "The tests completed successfully and the report confirms every assertion passed."},
        ]}):
            self.assertTrue(has_tool_interaction(row))
            self.assertEqual(conversation_pairs(row, "Tool-Use"), [])
        # A declared tool schema alone means availability, not a call.
        available_only = {"tools": [{"type": "function", "function": {"name": "run_shell"}}], "messages": rows[1]["messages"]}
        self.assertFalse(has_tool_interaction(available_only))
        self.assertEqual(len(conversation_pairs(available_only, "General-Agent")), 1)

    def test_excludes_evaluation_questions(self):
        row = {"messages": [
            {"role": "user", "content": "O que é uma variável em Python?"},
            {"role": "assistant", "content": "Uma variável guarda um valor nomeado em um programa Python."},
        ]}
        excluded = {normalize_question("O que é uma variável em Python?")}
        self.assertEqual(conversation_pairs(row, "General-Agent", excluded), [])

    def test_hash_ranked_selection_is_bounded_and_deterministic(self):
        first, second = [], []
        values = [(i, f"row-{i}") for i in range(20)]
        for priority, row in values:
            retain_lowest_hash(first, 4, priority, row)
        for priority, row in reversed(values):
            retain_lowest_hash(second, 4, priority, row)
        self.assertEqual(sorted(x[1] for x in first), sorted(x[1] for x in second))
        self.assertEqual(len(first), 4)


if __name__ == "__main__":
    unittest.main()
