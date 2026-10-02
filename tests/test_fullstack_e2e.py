import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))

from implementation_recipes import fallback_implementation_plan
from proactive_implementation import parse_implementation_plan
from fullstack_gate import assess_fullstack_plan


class FullstackEndToEndTests(unittest.TestCase):
    def test_delivery_system_plan_is_written_and_its_real_tests_pass(self):
        question = "Crie um app para entregadores registrarem entregas e quilômetros rodados"
        recipe = fallback_implementation_plan(question, existing_paths=set())
        self.assertIsNotNone(recipe)
        plan = parse_implementation_plan(json.dumps(recipe, ensure_ascii=False))
        # Esta receita é um produto local frontend + persistência; o gate
        # full-stack corretamente não o promove sem servidor/API.
        gate = assess_fullstack_plan(plan)
        self.assertIn("contract", gate["missing"])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for operation in plan["operations"]:
                args = operation["arguments"]
                path = root / args["path"]
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(args["content"], encoding="utf-8")
            result = subprocess.run(
                ["node", "--test", "tests/delivery-core.test.cjs"],
                cwd=root, capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
