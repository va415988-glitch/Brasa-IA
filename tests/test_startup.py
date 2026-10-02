"""Launcher checks run extracted preflight/functions, never start or stop services."""
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / 'start.sh').read_text()
FUNCTIONS = 'listener_rows() {' + SOURCE.split('listener_rows() {', 1)[1].split('\nCHECKPOINT_PATH=', 1)[0]
PREFLIGHT = re.search(r"CHECKPOINT_CONTEXT_INFO=.*?<<'PY'\n(.*?)\nPY\n", SOURCE, re.S).group(1)
sys.path.insert(0, str(ROOT / 'python'))
from checkpoint_io import save_checkpoint
from model import build_model
from tokenizer import ByteBPETokenizer


class ListenerChecks(unittest.TestCase):
    def shell(self, code):
        script = ('set -Eeuo pipefail\nPROJECT_DIR=' + shlex.quote(str(ROOT))
                  + '\nRUNTIME=/tmp/fixture-local-ai-runtime\n' + FUNCTIONS + '\n' + code)
        return subprocess.run(['bash', '-c', script], capture_output=True, text=True, timeout=10)

    def test_ss_failure_stops_check_and_pid_lookup(self):
        for function in ('listener_rows', 'listener_pids', 'check_project_listeners', 'stop_project_listeners'):
            result = self.shell('ss() { echo "netlink indisponível" >&2; return 42; }\n' + function + ' 3000')
            self.assertNotEqual(result.returncode, 0, function)
            self.assertIn('netlink indisponível', result.stderr)
            self.assertIn('não consegui verificar', result.stderr)

    def test_empty_ss_and_missing_pid_are_distinct(self):
        empty = self.shell('ss() { return 0; }\ncheck_project_listeners 3000')
        self.assertEqual(empty.returncode, 0, empty.stderr)
        missing_pid = 'ss() { printf "LISTEN 0 128 127.0.0.1:3000 0.0.0.0:*\\n"; }\n'
        lookup = self.shell(missing_pid + 'listener_pids 3000')
        self.assertEqual(lookup.returncode, 0, lookup.stderr)
        self.assertEqual(lookup.stdout, '')
        check = self.shell(missing_pid + 'check_project_listeners 3000')
        self.assertNotEqual(check.returncode, 0)
        self.assertIn('não consegui identificar', check.stderr)

    def test_pid_lookup_preserves_grep_errors_and_deduplicates(self):
        error = self.shell('ss() { printf "pid=123\\n"; }\ngrep() { return 2; }\nlistener_pids 3000')
        self.assertEqual(error.returncode, 2)
        valid = self.shell('ss() { printf "pid=20 pid=10 pid=20\\n"; }\nlistener_pids 3000')
        self.assertEqual(valid.returncode, 0, valid.stderr)
        self.assertEqual(set(valid.stdout.split()), {'10', '20'})

    def test_unreadable_process_cannot_be_identified_as_project(self):
        result = self.shell('is_project_listener 3000 2147483647')
        self.assertNotEqual(result.returncode, 0)


class CheckpointPreflight(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.folder = Path(self.directory.name)
        self.tokenizer_path = self.folder / 'tokenizer.json'
        tokenizer = ByteBPETokenizer.train(['Olá'], vocab_size=260)
        tokenizer.save(self.tokenizer_path)
        self.config = {'vocab_size': 260, 'context_length': 32, 'hidden_size': 8,
                       'layers': 1, 'attention_heads': 2, 'feed_forward_multiplier': 2,
                       'tokenizer_path': str(self.tokenizer_path)}
        self.checkpoint = self.folder / 'candidate.safetensors'
        self.weights = build_model(self.config).state_dict()
        save_checkpoint({'config': self.config, 'state_dict': self.weights}, self.checkpoint)

    def run_preflight(self):
        return subprocess.run([str(ROOT / '.venv/bin/python'), '-', str(self.checkpoint)],
                              input=PREFLIGHT, cwd=ROOT, capture_output=True, text=True,
                              env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'}, timeout=30)

    def test_real_weights_tokenizer_and_forward_are_required(self):
        result = self.run_preflight()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('pesos, tokenizer e forward verificados', result.stdout)

    def test_missing_metadata_and_mismatched_weight_shape_abort(self):
        metadata = Path(str(self.checkpoint) + '.json')
        original = metadata.read_text()
        metadata.unlink()
        self.assertNotEqual(self.run_preflight().returncode, 0)
        altered = json.loads(original)
        altered['config']['hidden_size'] = 10
        metadata.write_text(json.dumps(altered))
        result = self.run_preflight()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('serviços existentes foram preservados', result.stderr)

    def test_tokenizer_hash_and_vocab_mismatch_abort(self):
        metadata = Path(str(self.checkpoint) + '.json')
        altered = json.loads(metadata.read_text())
        altered['tokenizer_sha256'] = '0' * 64
        metadata.write_text(json.dumps(altered))
        result = self.run_preflight()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('hash do checkpoint', result.stderr)
        altered.pop('tokenizer_sha256')
        metadata.write_text(json.dumps(altered))
        tokenizer = json.loads(self.tokenizer_path.read_text())
        tokenizer['vocab']['61'] = 1000
        self.tokenizer_path.write_text(json.dumps(tokenizer))
        result = self.run_preflight()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('vocabulário dos pesos', result.stderr)

    def test_nonfinite_weights_abort_forward(self):
        self.weights['token_embedding.weight'].fill_(float('nan'))
        save_checkpoint({'config': self.config, 'state_dict': self.weights}, self.checkpoint)
        result = self.run_preflight()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Forward de prontidão', result.stderr)


if __name__ == '__main__':
    unittest.main()
