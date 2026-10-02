"""Explicit, read-only use of an unqualified checkpoint; never a skill provider."""
from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path

from loaded_model_identity import assert_loaded_identity_current
from cognitive_cores import CognitiveCoreRegistry, validate_numeric_scope
from cognitive_dialogue import build_frame, cognitive_prompt, validate_decision
from conditioned_context import conditioned_prompt

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / 'config/experimental_model.json'
REQUEST_SCHEMA = 'experimental-cognitive-request/v1'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ExperimentalModel:
    def __init__(self, provider_factory, manifest=CONFIG_PATH, *, root=ROOT, registry=None):
        self.root = Path(root).resolve()
        self.manifest = Path(manifest)
        self.provider_factory = provider_factory
        self.registry = registry or CognitiveCoreRegistry()
        self.provider = None
        self.loaded_profile = None
        self.lock = threading.RLock()

    def profile(self):
        value = json.loads(self.manifest.read_text(encoding='utf-8'))
        if (not isinstance(value, dict) or value.get('schema') != 'experimental-model/v1'
                or type(value.get('enabled')) is not bool or value.get('qualified') is not False
                or value.get('context_tokens') != 512):
            raise ValueError('Configuração experimental incompatível.')
        checkpoint = (self.root / value['checkpoint']).resolve()
        if not checkpoint.is_relative_to(self.root):
            raise ValueError('Checkpoint experimental fora do projeto.')
        metadata_path = Path(str(checkpoint) + '.json')
        metadata = json.loads(metadata_path.read_text(encoding='utf-8'))
        config = metadata['config']
        tokenizer = (self.root / config['tokenizer_path']).resolve()
        if (not tokenizer.is_relative_to(self.root) or config.get('context_length') != 512
                or config.get('cognitive_prompt_style') != 'compact-v1'):
            raise ValueError('Modelo experimental fora do perfil numérico esperado.')
        for path, key in [(checkpoint, 'checkpoint_sha256'), (metadata_path, 'metadata_sha256'),
                          (tokenizer, 'tokenizer_sha256')]:
            if digest(path) != value.get(key):
                raise ValueError('Artefato experimental diverge da configuração: ' + key)
        return {**value, 'checkpoint': str(checkpoint), 'manifest_sha256': digest(self.manifest)}

    def status(self):
        with self.lock:
            result = {'schema': 'experimental-model-status/v1', 'enabled': False, 'loaded': False,
                      'qualified': False, 'label': 'V4 · experimental', 'context_tokens': 512,
                      'core_states': {}, 'limits': ['Decisões numéricas sintéticas; janela de 512 tokens.',
                          'Competência reprovada; respostas podem conter erros.',
                          'Nenhuma ferramenta é executada pelo laboratório.']}
            try:
                profile = self.profile()
                snapshot = self.registry.snapshot(profile['checkpoint'])
                result.update(enabled=profile['enabled'], checkpoint=profile['checkpoint'],
                              label=profile.get('label', result['label']),
                              core_states={c['id']: c['status'] for c in snapshot['cores']})
                if snapshot.get('evidence_errors'):
                    result['evidence_errors'] = snapshot['evidence_errors']
                if self.provider is not None:
                    if profile != self.loaded_profile:
                        raise ValueError('Configuração experimental mudou; reinicie o serviço.')
                    assert_loaded_identity_current(self.provider.loaded_model_identity,
                                                   profile['checkpoint'], root=self.root)
                    result['loaded'] = bool(self.provider.local_model is not None
                                            and self.provider.local_tokenizer is not None
                                            and not self.provider.local_model_error)
            except (OSError, ValueError, TypeError, KeyError) as error:
                result.update(enabled=False, error=str(error))
            return result

    @staticmethod
    def failure(code, message, *, attempts=0, raw='', generation=None):
        return {'ok': False, 'schema': 'experimental-cognitive-response/v1',
                'experimental': True, 'qualified': False, 'tool_executed': False,
                'execution_allowed': False, 'generation_attempts': attempts,
                'error_code': code, 'error': str(message), 'text': str(message),
                'raw_output': raw, 'generation': generation}

    def decide(self, body):
        with self.lock:
            try:
                if not isinstance(body, dict) or body.get('schema') != REQUEST_SCHEMA:
                    raise ValueError('Pedido experimental incompatível.')
                messages, cognition = body.get('messages'), body.get('cognition')
                if (not isinstance(messages, list) or not 0 < len(messages) <= 4
                        or any(not isinstance(m, dict) or m.get('role') not in ('user', 'tool')
                               or not isinstance(m.get('content'), str) or len(m['content']) > 2000
                               for m in messages)
                        or len(messages[0]['content']) > 200
                        or not isinstance(cognition, dict) or cognition.get('schema') != 'agent-cognition/v1'
                        or not isinstance(cognition.get('available_tools', []), list)
                        or any(not isinstance(t, str) for t in cognition.get('available_tools', []))
                        or not isinstance(cognition.get('constraints', []), list)):
                    raise ValueError('Entrada experimental inválida ou acima do limite.')
                # Only the declared numeric input contract reaches the provider.
                from tool_registry import ToolRegistry
                tools = ToolRegistry()
                frame = build_frame(messages, cognition, tools)
                validate_numeric_scope(messages, cognition, frame)
                prompt = cognitive_prompt(frame, 'compact-v1')
            except (ValueError, TypeError, KeyError) as error:
                return self.failure('experimental_input_out_of_scope', error)
            try:
                profile = self.profile()
                if not profile['enabled']:
                    raise ValueError('Laboratório desativado na configuração local.')
                if self.provider is None:
                    provider = self.provider_factory(profile['checkpoint'])
                    if provider.local_model is None or provider.local_tokenizer is None or provider.local_model_error:
                        raise ValueError(provider.local_model_error or 'Modelo experimental indisponível.')
                    self.provider, self.loaded_profile = provider, profile
                if profile != self.loaded_profile:
                    raise ValueError('Configuração experimental mudou; reinicie o serviço.')
                provider = self.provider
                assert_loaded_identity_current(provider.loaded_model_identity, profile['checkpoint'], root=self.root)
                tokens = provider.local_tokenizer.encode_fast(conditioned_prompt([{'role': 'user', 'content': prompt}]))
                if len(tokens) > 512 - 128:
                    return self.failure('experimental_input_out_of_scope', 'O pedido completo não cabe na janela experimental.')
            except (OSError, ValueError, TypeError, KeyError) as error:
                return self.failure('experimental_model_unavailable', error)
            provider.last_generation = None
            try:
                raw = provider.local_reply([{'role': 'user', 'content': prompt}], max_tokens_limit=192,
                                           structured_decision=True, capture_rejected=True)
            except Exception as error:
                generation = dict(provider.last_generation or {})
                return self.failure('experimental_generation_failed', str(error)[:500], attempts=1,
                                    raw=generation.get('raw_output', ''), generation=generation)
            generation = dict(provider.last_generation or {})
            raw = generation.get('raw_output', raw) or ''
            try:
                # Recheck after inference so changes during a forward pass cannot pass.
                assert_loaded_identity_current(provider.loaded_model_identity, profile['checkpoint'], root=self.root)
                if self.profile() != profile:
                    raise ValueError('Configuração mudou durante a geração.')
                if generation.get('quality_gate_result') != 'accepted':
                    raise ValueError('O decoder rejeitou a saída experimental.')
                if generation.get('input_tokens') != len(tokens):
                    raise ValueError('O decoder não preservou a entrada completa.')
                decision = validate_decision(raw, frame, tools)
            except (OSError, ValueError, TypeError, KeyError) as error:
                return self.failure('experimental_output_invalid', error, attempts=1, raw=raw, generation=generation)
            return {'ok': True, 'schema': 'experimental-cognitive-response/v1', 'experimental': True,
                    'qualified': False, 'tool_executed': False, 'execution_allowed': False,
                    'generation_attempts': 1, 'text': decision['text'], 'decision': decision,
                    'raw_output': raw, 'generation': generation}
