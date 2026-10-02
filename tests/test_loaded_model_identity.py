"""A certificate for new files must never license old weights or compiled code."""
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'python'))
import loaded_model_identity as identity


class LoadedModelIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.checkpoint = self.root / 'model.safetensors'
        self.checkpoint.write_bytes(b'fixture weights, not a model competence proof')
        self.tokenizer = self.root / 'tokenizer.json'
        self.tokenizer.write_text('{}')
        self.metadata = Path(str(self.checkpoint) + '.json')
        self.write_metadata()
        self.source = self.root / 'runtime.py'
        self.source.write_text('value = 1\n')
        self.catalog = self.root / 'config/cognitive_cores.json'
        self.catalog.parent.mkdir()
        self.catalog.write_text('{}')

    def tearDown(self):
        self.temp.cleanup()

    def write_metadata(self, tokenizer='tokenizer.json'):
        self.metadata.write_text(json.dumps({'config': {'tokenizer_path': tokenizer},
            'tokenizer_sha256': hashlib.sha256(self.tokenizer.read_bytes()).hexdigest()}))

    def capture(self):
        return identity.capture_model_identity(self.checkpoint, root=self.root, source_paths=['runtime.py'])

    def loaded(self):
        return identity.confirm_loaded_identity(self.capture(), self.capture())

    def test_stable_load_can_be_used_and_envelope_is_detached(self):
        before, after = self.capture(), self.capture()
        loaded = identity.confirm_loaded_identity(before, after)
        after['files'].clear()
        self.assertTrue(identity.assert_loaded_identity_current(loaded, self.checkpoint, root=self.root))

    def test_each_loaded_artifact_change_blocks_dispatch(self):
        for path in [self.checkpoint, self.metadata, self.tokenizer, self.source, self.catalog]:
            with self.subTest(path=path.name):
                loaded = self.loaded()
                original = path.read_bytes()
                path.write_bytes(original + b' ')
                with self.assertRaises(ValueError):
                    identity.assert_loaded_identity_current(loaded, root=self.root)
                path.write_bytes(original)

    def test_change_during_load_requires_retry_even_if_restored_later(self):
        before = self.capture()
        original = self.checkpoint.read_bytes()
        self.checkpoint.write_bytes(b'temporary other weights')
        self.checkpoint.write_bytes(original)
        with self.assertRaises(ValueError):
            identity.confirm_loaded_identity(before, self.capture())

    def test_changed_environment_cannot_borrow_decoding_proof(self):
        loaded = self.loaded()
        with patch.dict(os.environ, {'IA_LOCAL_REPETITION_PENALTY': '2.0'}):
            with self.assertRaises(ValueError):
                identity.assert_loaded_identity_current(loaded, root=self.root)

    def test_tokenizer_path_mismatch_and_declared_hash_mismatch_are_rejected(self):
        loaded = self.loaded()
        other = self.root / 'other.json'
        other.write_bytes(self.tokenizer.read_bytes())
        self.write_metadata('other.json')
        with self.assertRaises(ValueError):
            identity.assert_loaded_identity_current(loaded, root=self.root)
        self.tokenizer.write_text('{"changed":true}')
        self.write_metadata()
        metadata = json.loads(self.metadata.read_text())
        metadata['tokenizer_sha256'] = '0' * 64
        self.metadata.write_text(json.dumps(metadata))
        with self.assertRaisesRegex(ValueError, 'Tokenizer'):
            self.capture()

    def test_source_edit_after_import_requires_process_restart(self):
        imported = hashlib.sha256(self.source.read_bytes()).hexdigest()
        with patch.dict(identity._IMPORTED_SOURCE_HASHES, {str(self.source): imported}):
            self.capture()
            self.source.write_text('value = 2\n')
            with self.assertRaisesRegex(ValueError, 'reinicie'):
                self.capture()

    def test_other_checkpoint_or_root_cannot_borrow_the_loaded_identity(self):
        loaded = self.loaded()
        with self.assertRaises(ValueError):
            identity.assert_loaded_identity_current(loaded, 'other.safetensors', root=self.root)
        with self.assertRaises(ValueError):
            identity.assert_loaded_identity_current(loaded, root=self.root.parent)

    def test_missing_artifact_and_identity_fail_closed(self):
        loaded = self.loaded()
        self.source.unlink()
        with self.assertRaises(ValueError):
            identity.assert_loaded_identity_current(loaded, root=self.root)
        for invalid in [None, {}, {'schema': identity.SCHEMA, 'root': str(self.root)}]:
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                identity.assert_loaded_identity_current(invalid, root=self.root)

    def test_outside_project_and_empty_sources_are_rejected(self):
        self.write_metadata('../outside.json')
        with self.assertRaisesRegex(ValueError, 'fora do projeto'):
            self.capture()
        self.write_metadata()
        with self.assertRaises(ValueError):
            identity.capture_model_identity(self.checkpoint, root=self.root, source_paths=[])

    def test_incomplete_envelope_is_not_validated(self):
        loaded = copy.deepcopy(self.loaded())
        loaded.pop('source_paths')
        with self.assertRaises(ValueError):
            identity.assert_loaded_identity_current(loaded, root=self.root)

    def add_context_source(self):
        source = self.root / 'source.safetensors'
        source.write_bytes(b'original trained positions')
        source_metadata = Path(str(source) + '.json')
        source_metadata.write_bytes(self.metadata.read_bytes())
        metadata = json.loads(self.metadata.read_text())
        metadata['config']['context_extension'] = {
            'method': 'linear-interpolation-trained-position-span-v1',
            'source_checkpoint': source.name,
            'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
        }
        self.metadata.write_text(json.dumps(metadata))
        return source, source_metadata

    def test_context_repair_sources_and_their_metadata_are_bound(self):
        source, sidecar = self.add_context_source()
        for path in [source, sidecar]:
            with self.subTest(path=path.name):
                loaded = self.loaded()
                self.assertEqual(loaded['context_sources'][0]['checkpoint'], str(source))
                original = path.read_bytes()
                path.write_bytes(original + b' ')
                with self.assertRaises(ValueError):
                    identity.assert_loaded_identity_current(loaded, root=self.root)
                path.write_bytes(original)

    def test_context_source_hash_and_cycles_fail_closed(self):
        source, sidecar = self.add_context_source()
        source.write_bytes(b'other positions')
        with self.assertRaisesRegex(ValueError, 'posições treinadas'):
            self.capture()
        # Point the source back to the main checkpoint; this cannot recurse forever.
        main = json.loads(self.metadata.read_text())
        main['config']['context_extension']['source_sha256'] = hashlib.sha256(source.read_bytes()).hexdigest()
        self.metadata.write_text(json.dumps(main))
        source_config = json.loads(sidecar.read_text())
        source_config['config']['context_extension'] = {
            'method': 'linear-interpolation-trained-position-span-v1',
            'source_checkpoint': self.checkpoint.name,
        }
        sidecar.write_text(json.dumps(source_config))
        with self.assertRaisesRegex(ValueError, 'ciclo'):
            self.capture()


if __name__ == '__main__':
    unittest.main()
