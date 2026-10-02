import json
import unittest

from scripts.prepare_verified_repair_sft import extract, patch_to_edit


PATCH = """*** Begin Patch
*** Update File: /app/src/example/core.py
@@
 def convert(value):
-    return value + 1
+    return value * 2
*** End Patch"""


def trajectory(*, test_output="[STDOUT]\n2 passed in 0.1s", second_patch=False):
    messages = [
        {"role": "user", "content": "<pr_description>Fix conversion.</pr_description>"},
        {"role": "assistant", "tool_calls": [{"function": {"name": "apply_patch", "arguments": {"patch": PATCH}}}]},
        {"role": "tool", "content": "Updated file: src/example/core.py"},
    ]
    if second_patch:
        messages.extend(messages[1:3])
    messages.extend([
        {"role": "assistant", "tool_calls": [{"function": {"name": "shell", "arguments": {"command": "pytest tests/test_core.py"}}}]},
        {"role": "tool", "content": test_output},
    ])
    return {"uuid": "example-1", "messages": json.dumps(messages)}


class PrepareVerifiedRepairSftTests(unittest.TestCase):
    def test_one_hunk_becomes_exact_edit(self):
        path, old_text, new_text = patch_to_edit(PATCH)
        self.assertEqual(path, "src/example/core.py")
        self.assertIn("return value + 1", old_text)
        self.assertIn("return value * 2", new_text)
        example, reason = extract(trajectory())
        self.assertEqual(reason, "accepted")
        self.assertEqual(example["provenance"]["group"], "example")

    def test_failed_test_is_rejected(self):
        _, reason = extract(trajectory(test_output="[STDOUT]\n1 passed, 1 failed"))
        self.assertEqual(reason, "no_confirmed_test")

    def test_zero_tests_are_rejected(self):
        _, reason = extract(trajectory(test_output="[STDOUT]\n0 passed in 0.1s"))
        self.assertEqual(reason, "no_confirmed_test")

    def test_multiple_patches_are_rejected(self):
        _, reason = extract(trajectory(second_patch=True))
        self.assertEqual(reason, "multiple_or_ambiguous_patches")

    def test_multiple_hunks_are_rejected(self):
        patch = PATCH.replace("*** End Patch", "@@\n-foo\n+bar\n*** End Patch")
        self.assertIsNone(patch_to_edit(patch))


if __name__ == "__main__":
    unittest.main()
