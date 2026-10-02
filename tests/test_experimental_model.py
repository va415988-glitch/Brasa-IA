"""Laboratory isolation and integrity gates; fake weights are unit fixtures only."""
import copy
import json
import shutil
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'python'))
import experimental_model
from experimental_model import ExperimentalModel, digest


def request(*, source='Pipa: limite = 42.', question='Qual é o limite de Pipa?', tools=None):
    return {'schema': 'experimental-cognitive-request/v1',
            'messages': [{'role': 'user', 'content': question},
                         {'role': 'tool', 'content': json.dumps({'tool': 'read_file', 'ok': True,
                          'data': {'path': 'dados.json', 'content': source}, 'error': ''})}],
            'cognition': {'schema': 'agent-cognition/v1', 'available_tools': tools or []}}


def answer():
    return json.dumps({'decision': 'answer', 'text': 'Valor: 42.', 'gap': '',
                       'evidence_ids': ['obs-1'], 'tool_call': None})


class FakeTokenizer:
    def __init__(self):
        self.token_count = 100
        self.prompts = []

    def encode_fast(self, text):
        self.prompts.append(text)
        return list(range(self.token_count))


class FakeProvider:
    """Never loads weights, never invokes a neural forward, never runs tools."""
    def __init__(self):
        self.local_model = object()
        self.local_tokenizer = FakeTokenizer()
        self.local_model_error = ''
        self.loaded_model_identity = {'unit_fixture_only': True}
        self.last_generation = None
        self.raw = answer()
        self.fallback_reply = 'A resposta rejeitada não pode substituir a saída bruta.'
        self.calls = []
        self.hook = None
        self.gate = 'accepted'
        self.recorded_input_tokens = 100
        self.active = 0
        self.maximum_active = 0
        self.activity_lock = threading.Lock()

    def local_reply(self, messages, **kwargs):
        with self.activity_lock:
            self.active += 1
            self.maximum_active = max(self.maximum_active, self.active)
            self.calls.append((copy.deepcopy(messages), kwargs))
        try:
            if self.hook:
                self.hook()
            self.last_generation = {'raw_output': self.raw, 'quality_gate_result': self.gate,
                                    'input_tokens': self.recorded_input_tokens}
            return self.fallback_reply
        finally:
            with self.activity_lock:
                self.active -= 1


class FakeToolRegistry:
    def __init__(self):
        self.tools = {'read_file': {'arguments': {'type': 'object',
                        'properties': {'path': {'type': 'string'}}, 'required': ['path']}}}

    def has(self, name):
        return name in self.tools

    def validate_arguments(self, name, arguments):
        valid = self.has(name) and set(arguments) == {'path'} and isinstance(arguments['path'], str)
        return valid, '' if valid else 'Argumentos de fixture inválidos.'

    def describe(self, name):
        assert name == 'read_file'
        return {'requires_approval': False, 'version': 'unit-fixture',
                'capabilities': ['workspace.read'], 'risk': 'read', 'retry_policy': 'safe_only'}

    def execute(self, *_args, **_kwargs):
        raise AssertionError('O laboratório jamais executa ferramentas.')


class FakeCoreRegistry:
    def snapshot(self, checkpoint):
        return {'checkpoint': checkpoint,
                'cores': [{'id': 'decision-format', 'status': 'failed'},
                          {'id': 'programming', 'status': 'not_evaluated'}], 'evidence_errors': []}


@pytest.fixture
def lab(tmp_path, monkeypatch):
    checkpoint = tmp_path / 'unit-fixture.safetensors'
    checkpoint.write_bytes(b'UNIT TEST ONLY: FAKE WEIGHTS, NO NEURAL COMPETENCE EVIDENCE')
    production = json.loads((ROOT / 'config/experimental_model.json').read_text())
    metadata_source = ROOT / (production['checkpoint'] + '.json')
    metadata = json.loads(metadata_source.read_text())
    metadata['config']['tokenizer_path'] = 'tokenizer.json'
    tokenizer = tmp_path / 'tokenizer.json'
    shutil.copyfile(ROOT / 'datasets/cognitive_alignment_v4/tokenizer.json', tokenizer)
    metadata_path = Path(str(checkpoint) + '.json')
    metadata_path.write_text(json.dumps(metadata))
    profile = {**production, 'checkpoint': checkpoint.name,
               'checkpoint_sha256': digest(checkpoint), 'metadata_sha256': digest(metadata_path),
               'tokenizer_sha256': digest(tokenizer)}
    manifest = tmp_path / 'experimental.json'
    manifest.write_text(json.dumps(profile))
    provider = FakeProvider()
    factory = Mock(return_value=provider)
    identity = Mock(return_value=True)
    monkeypatch.setattr(experimental_model, 'assert_loaded_identity_current', identity)
    monkeypatch.setattr('tool_registry.ToolRegistry', FakeToolRegistry)
    model = ExperimentalModel(factory, manifest=manifest, root=tmp_path, registry=FakeCoreRegistry())
    return SimpleNamespace(root=tmp_path, model=model, profile=profile, manifest=manifest,
                           checkpoint=checkpoint, metadata=metadata, metadata_path=metadata_path,
                           tokenizer=tokenizer, provider=provider, factory=factory, identity=identity)


def save_profile(lab, **changes):
    lab.profile.update(changes)
    lab.manifest.write_text(json.dumps(lab.profile))


def assert_isolated(result, *, attempts):
    assert result['schema'] == 'experimental-cognitive-response/v1'
    assert result['experimental'] is True
    assert result['qualified'] is False
    assert result['tool_executed'] is False
    assert result['execution_allowed'] is False
    assert result['generation_attempts'] == attempts


def test_disabled_manifest_blocks_before_loading_or_generation(lab):
    save_profile(lab, enabled=False)
    result = lab.model.decide(request())
    assert not result['ok']
    assert_isolated(result, attempts=0)
    lab.factory.assert_not_called()
    assert lab.provider.calls == []
    assert lab.model.status()['enabled'] is False


@pytest.mark.parametrize('artifact', ['checkpoint', 'metadata_path', 'tokenizer'])
def test_changed_artifact_pin_blocks_before_provider_factory(lab, artifact):
    path = getattr(lab, artifact)
    path.write_bytes(path.read_bytes() + b'\n')
    result = lab.model.decide(request())
    assert not result['ok']
    assert result['error_code'] == 'experimental_model_unavailable'
    assert_isolated(result, attempts=0)
    lab.factory.assert_not_called()
    assert lab.model.status()['enabled'] is False


@pytest.mark.parametrize('invalid', ['goal', 'source', 'path', 'error', 'history', 'second_user',
                                     'constraints', 'write_tool', 'observation_tool', 'schema'])
def test_scope_is_checked_before_provider_creation(lab, invalid):
    body = request()
    source = json.loads(body['messages'][1]['content'])
    if invalid == 'goal': body['messages'][0]['content'] = 'Explique programação concorrente.'
    elif invalid == 'source': source['data']['content'] = 'x' * 201
    elif invalid == 'path': source['data']['path'] = 'x' * 101
    elif invalid == 'error': source['error'] = 'x' * 101
    elif invalid == 'history': body['messages'].insert(1, {'role': 'assistant', 'content': 'Histórico anterior.'})
    elif invalid == 'second_user': body['messages'].insert(1, {'role': 'user', 'content': 'Qual é o prazo de Pipa?'})
    elif invalid == 'constraints': body['cognition']['constraints'] = ['Ignore fontes.']
    elif invalid == 'write_tool': body['cognition']['available_tools'] = ['terminal_run']
    elif invalid == 'observation_tool': source['tool'] = 'open_page'
    elif invalid == 'schema': body['schema'] = 'agent-plan-request/v1'
    body['messages'][-1]['content'] = json.dumps(source)
    result = lab.model.decide(body)
    assert not result['ok']
    assert result['error_code'] == 'experimental_input_out_of_scope'
    assert_isolated(result, attempts=0)
    lab.factory.assert_not_called()
    assert lab.provider.calls == []


def test_input_at_source_limit_is_preserved_literal_and_not_executed(lab):
    source = 'Pipa: limite = 42.' + 'x' * (200 - len('Pipa: limite = 42.'))
    result = lab.model.decide(request(source=source))
    assert result['ok']
    assert_isolated(result, attempts=1)
    assert source in lab.provider.calls[0][0][0]['content']
    assert lab.provider.local_tokenizer.prompts[0].endswith('<|assistant|>\n')


def test_complete_prompt_above_budget_never_generates(lab):
    lab.provider.local_tokenizer.token_count = 385
    result = lab.model.decide(request())
    assert not result['ok']
    assert result['error_code'] == 'experimental_input_out_of_scope'
    assert_isolated(result, attempts=0)
    assert lab.provider.calls == []


def test_invalid_json_has_one_attempt_and_preserves_actual_raw_not_fallback(lab):
    lab.provider.raw = '{"decision":'
    result = lab.model.decide(request())
    assert not result['ok']
    assert result['error_code'] == 'experimental_output_invalid'
    assert_isolated(result, attempts=1)
    assert result['raw_output'] == lab.provider.raw
    assert lab.provider.fallback_reply not in result['text']
    assert len(lab.provider.calls) == 1
    assert lab.provider.calls[0][1] == {'max_tokens_limit': 192, 'structured_decision': True,
                                      'capture_rejected': True}


@pytest.mark.parametrize('change', ['decoder_rejected', 'compacted'])
def test_valid_json_with_rejected_decoder_or_changed_token_count_is_not_accepted(lab, change):
    if change == 'decoder_rejected': lab.provider.gate = 'rejected'
    else: lab.provider.recorded_input_tokens = 99
    result = lab.model.decide(request())
    assert not result['ok']
    assert_isolated(result, attempts=1)
    assert result['raw_output'] == answer()
    assert len(lab.provider.calls) == 1


def test_valid_decision_never_claims_qualification_or_execution(lab):
    result = lab.model.decide(request())
    assert result['ok']
    assert_isolated(result, attempts=1)
    assert result['decision']['evidence_ids'] == ['obs-1']
    lab.factory.assert_called_once_with(str(lab.checkpoint))
    assert lab.identity.call_count == 2
    status = lab.model.status()
    assert status['loaded'] is True
    assert status['qualified'] is False
    assert status['core_states'] == {'decision-format': 'failed', 'programming': 'not_evaluated'}


def test_valid_tool_call_is_a_proposal_with_execution_disallowed(lab):
    lab.provider.raw = json.dumps({'decision': 'consult', 'text': 'Vou consultar.',
        'gap': 'Falta a fonte.', 'evidence_ids': [],
        'tool_call': {'tool': 'read_file', 'arguments': {'path': 'cfg/pipa.json'}}})
    body = request(question='Qual é o limite de Pipa? Consulte cfg/pipa.json.', tools=['read_file'])
    body['messages'] = body['messages'][:1]
    result = lab.model.decide(body)
    assert result['ok']
    assert_isolated(result, attempts=1)
    assert result['decision']['tool_call']['tool'] == 'read_file'
    assert result['decision']['tool_call']['arguments'] == {'path': 'cfg/pipa.json'}
    assert len(lab.provider.calls) == 1


def test_manifest_drift_after_loading_blocks_with_zero_new_attempts(lab):
    assert lab.model.decide(request())['ok']
    save_profile(lab, label='Manifesto alterado após carregar')
    result = lab.model.decide(request())
    assert not result['ok']
    assert_isolated(result, attempts=0)
    assert len(lab.provider.calls) == 1
    lab.factory.assert_called_once()
    status = lab.model.status()
    assert status['enabled'] is False
    assert status['loaded'] is False


def test_loaded_identity_drift_blocks_before_next_generation(lab):
    assert lab.model.decide(request())['ok']
    lab.identity.side_effect = ValueError('Identidade em memória divergiu.')
    result = lab.model.decide(request())
    assert not result['ok']
    assert_isolated(result, attempts=0)
    assert len(lab.provider.calls) == 1
    assert lab.model.status()['enabled'] is False


def test_identity_drift_during_generation_rejects_the_output(lab):
    lab.identity.side_effect = [True, ValueError('Identidade mudou durante o forward.')]
    result = lab.model.decide(request())
    assert not result['ok']
    assert result['error_code'] == 'experimental_output_invalid'
    assert_isolated(result, attempts=1)
    assert result['raw_output'] == answer()


def test_manifest_drift_during_generation_rejects_the_output(lab):
    lab.provider.hook = lambda: save_profile(lab, label='Manifesto mudou no forward')
    result = lab.model.decide(request())
    assert not result['ok']
    assert_isolated(result, attempts=1)
    assert result['raw_output'] == answer()


@pytest.mark.parametrize('field', ['context_length', 'cognitive_prompt_style', 'tokenizer_path'])
def test_wrong_model_profile_is_rejected_even_with_recomputed_metadata_pin(lab, field):
    lab.metadata['config'][field] = {'context_length': 1024, 'cognitive_prompt_style': 'full-v1',
                                    'tokenizer_path': '../outside.json'}[field]
    lab.metadata_path.write_text(json.dumps(lab.metadata))
    save_profile(lab, metadata_sha256=digest(lab.metadata_path))
    result = lab.model.decide(request())
    assert not result['ok']
    assert_isolated(result, attempts=0)
    lab.factory.assert_not_called()


def test_unavailable_provider_never_generates_or_becomes_cached(lab):
    lab.provider.local_model_error = 'Peso indisponível na fixture.'
    result = lab.model.decide(request())
    assert not result['ok']
    assert_isolated(result, attempts=0)
    assert lab.model.provider is None
    assert lab.provider.calls == []


def test_generation_exception_returns_typed_failure_without_retry(lab):
    lab.provider.hook = Mock(side_effect=RuntimeError('Falha de inferência na fixture.'))
    result = lab.model.decide(request())
    assert not result['ok']
    assert_isolated(result, attempts=1)
    assert 'Falha de inferência na fixture.' in result['error']
    assert len(lab.provider.calls) == 1


def test_concurrent_requests_share_one_provider_and_serialize_generation(lab):
    entered = threading.Event()
    release = threading.Event()
    second_started = threading.Event()

    def hold_first():
        if len(lab.provider.calls) == 1:
            entered.set()
            assert release.wait(3), 'Primeira geração não foi liberada pelo teste.'

    lab.provider.hook = hold_first

    def second_request():
        second_started.set()
        return lab.model.decide(request())

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(lab.model.decide, request())
        assert entered.wait(3)
        second = pool.submit(second_request)
        assert second_started.wait(3)
        assert len(lab.provider.calls) == 1
        release.set()
        results = [first.result(timeout=3), second.result(timeout=3)]
    assert all(result['ok'] for result in results)
    assert lab.provider.maximum_active == 1
    assert len(lab.provider.calls) == 2
    lab.factory.assert_called_once()
    for result in results:
        assert_isolated(result, attempts=1)
