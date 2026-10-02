import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))
from active_checkpoint import selected_checkpoint


class ActiveCheckpointTests(unittest.TestCase):
    def test_active_checkpoint_matches_launcher_selection_and_override_wins(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "model/godmode").mkdir(parents=True)
            (root / "model/candidate.safetensors").touch()
            (root / "model/godmode/state.json").write_text(json.dumps({
                "status": "active", "checkpoint": "model/candidate.safetensors"
            }))
            self.assertEqual(selected_checkpoint(root, {}), root / "model/candidate.safetensors")
            self.assertEqual(selected_checkpoint(root, {"IA_LOCAL_CHECKPOINT": "custom.pt"}), root / "custom.pt")

    def test_inactive_or_missing_candidate_uses_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "model/godmode").mkdir(parents=True)
            state = root / "model/godmode/state.json"
            state.write_text(json.dumps({"status": "inactive", "checkpoint": "model/missing.pt"}))
            self.assertEqual(selected_checkpoint(root, {}), root / "model/checkpoints/compact-08-gate-focus.pt")
            state.write_text(json.dumps({"status": "active", "checkpoint": "model/missing.pt"}))
            self.assertEqual(selected_checkpoint(root, {}), root / "model/checkpoints/compact-08-gate-focus.pt")


if __name__ == "__main__":
    unittest.main()
