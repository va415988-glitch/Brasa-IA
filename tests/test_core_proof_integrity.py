"""Runtime proof caches must bind the exact protocol files and loaded catalog."""
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'python'), str(ROOT / 'tests')]
from cognitive_cores import file_hash, PROOF_SOURCES
from loaded_model_identity import DEFAULT_SOURCE_PATHS
import test_cognitive_cores as core_fixtures


class CoreProofIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.fixture = core_fixtures.CoreArtifactTests()
        self.fixture.setUp()

    def tearDown(self):
        self.fixture.tearDown()

    def update_protocol(self, protocol):
        fixture = self.fixture
        fixture.protocol.write_text(json.dumps(protocol))
        certificate = json.loads(fixture.certificate.read_text())
        certificate['protocol_sha256'] = file_hash(fixture.protocol)
        fixture.certificate.write_text(json.dumps(certificate))
        fixture.write_index()

    def test_protocol_cannot_point_to_unhashed_other_cases(self):
        fixture = self.fixture
        self.assertTrue(fixture.registry.snapshot(fixture.weights)['cores'][0]['dispatch_enabled'])
        protocol = json.loads(fixture.protocol.read_text())
        # The suite still names/hash-checks the original reserved cases. This
        # change formerly sent the independence check to an unrelated file.
        protocol['cases']['reserved'] = fixture.training.name
        self.update_protocol(protocol)
        state = fixture.registry.snapshot(fixture.weights)
        self.assertFalse(any(core['dispatch_enabled'] for core in state['cores']))
        self.assertTrue(state['evidence_errors'])

    def test_extra_protocol_suite_is_rejected(self):
        fixture = self.fixture
        protocol = json.loads(fixture.protocol.read_text())
        protocol['cases']['unreported'] = fixture.training.name
        self.update_protocol(protocol)
        self.assertTrue(fixture.registry.snapshot(fixture.weights)['evidence_errors'])

    def test_live_registry_cannot_use_old_catalog_with_a_new_file(self):
        fixture = self.fixture
        self.assertTrue(fixture.registry.snapshot(fixture.weights)['cores'][0]['dispatch_enabled'])
        catalog = json.loads(fixture.catalog.read_text())
        catalog['cores'][0]['qualification']['overall'] = 0.0
        fixture.catalog.write_text(json.dumps(catalog))
        state = fixture.registry.snapshot(fixture.weights)
        self.assertFalse(any(core['dispatch_enabled'] for core in state['cores']))
        self.assertIn('Catálogo mudou', state['evidence_errors'][0])

    def test_generation_dependency_edits_revoke_cached_certificate(self):
        fixture = self.fixture
        self.assertTrue(fixture.registry.snapshot(fixture.weights)['cores'][0]['dispatch_enabled'])
        path = fixture.root / 'python/generation_utils.py'
        path.write_text(path.read_text() + '\n# changed decoding\n')
        state = fixture.registry.snapshot(fixture.weights)
        self.assertFalse(any(core['dispatch_enabled'] for core in state['cores']))
        self.assertTrue(state['evidence_errors'])

    def test_non_object_registry_fails_closed(self):
        fixture = self.fixture
        for payload in [None, [], 'invalid']:
            with self.subTest(payload=payload):
                fixture.index.write_text(json.dumps(payload))
                state = fixture.registry.snapshot(fixture.weights)
                self.assertFalse(any(core['dispatch_enabled'] for core in state['cores']))
                self.assertTrue(state['evidence_errors'])

    def test_proof_sources_are_also_bound_to_the_loaded_runtime(self):
        self.assertFalse(set(PROOF_SOURCES) - set(DEFAULT_SOURCE_PATHS))


if __name__ == '__main__':
    unittest.main()
