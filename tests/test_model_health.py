"""Readiness must be cheap, honest and robust to disconnected HTTP clients."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'python'))
import model_server
from model_server import Handler, ModelService


class NoTruthiness:
    def __bool__(self):
        raise AssertionError('Readiness checks presence rather than model/tokenizer truthiness.')


def service(*, model=True, tokenizer=True, error=''):
    result = ModelService.__new__(ModelService)
    result.local_model = NoTruthiness() if model else None
    result.local_tokenizer = NoTruthiness() if tokenizer else None
    result.local_model_error = error
    result.checkpoint_path = 'unit-fixture-only.safetensors'
    result.capabilities = Mock(side_effect=AssertionError('Health must not calculate capabilities.'))
    result.core_status = Mock(side_effect=AssertionError('Health must not regrade cognitive evidence.'))
    return result


@pytest.mark.parametrize('model,tokenizer,error,ready', [
    (True, True, '', True), (False, True, '', False), (True, False, '', False),
    (False, False, '', False), (True, True, 'Falha ao carregar o peso.', False),
])
def test_readiness_requires_model_tokenizer_and_no_load_error(model, tokenizer, error, ready):
    instance = service(model=model, tokenizer=tokenizer, error=error)
    result = instance.health_status()
    assert result['ok'] is ready
    assert result['free_generation'] is ready
    assert result['backend'] == ('local-neural' if ready else 'unavailable')
    assert result['checkpoint'] == instance.checkpoint_path
    assert result['code_generation']['loaded'] is ready
    assert result['code_generation']['general_programming_mastery'] is False
    if ready:
        assert result['error'] is None
    elif error:
        assert result['error'] == error
    else:
        assert result['error']
    instance.capabilities.assert_not_called()
    instance.core_status.assert_not_called()


@pytest.mark.parametrize('ready', [True, False])
def test_health_handler_sends_200_or_503_without_heavy_status(monkeypatch, ready):
    instance = service(model=ready)
    handler = Handler.__new__(Handler)
    handler.path = '/health'
    handler._send = Mock()
    monkeypatch.setattr(model_server, 'SERVICE', instance)
    handler.do_GET()
    status, payload = handler._send.call_args.args
    assert status == (200 if ready else 503)
    assert payload['ok'] is ready
    assert payload['free_generation'] is ready
    instance.capabilities.assert_not_called()
    instance.core_status.assert_not_called()


def test_health_handler_without_service_is_unavailable(monkeypatch):
    handler = Handler.__new__(Handler)
    handler.path = '/health'
    handler._send = Mock()
    monkeypatch.setattr(model_server, 'SERVICE', None)
    handler.do_GET()
    status, payload = handler._send.call_args.args
    assert status == 503
    assert payload['ok'] is False
    assert payload['free_generation'] is False
    assert payload['error']


def test_health_reports_only_identity_already_captured_in_memory():
    instance = service()
    name = str(ROOT / 'python/product_planning.py')
    instance.loaded_model_identity = {'source_paths': [name], 'files': {name: {'sha256': 'a' * 64}}}
    assert instance.health_status()['loaded_source_sha256'] == {name: 'a' * 64}
    instance.capabilities.assert_not_called()
    instance.core_status.assert_not_called()


def writer():
    handler = Handler.__new__(Handler)
    handler.send_response = Mock()
    handler.send_header = Mock()
    handler.end_headers = Mock()
    handler.wfile = SimpleNamespace(write=Mock())
    handler.close_connection = False
    return handler


def test_send_serializes_utf8_once_with_matching_content_length():
    handler = writer()
    payload = {'ok': True, 'text': 'Olá · modelo pronto'}
    result = handler._send(200, payload)
    assert result is True
    encoded = handler.wfile.write.call_args.args[0]
    assert json.loads(encoded.decode('utf-8')) == payload
    handler.send_response.assert_called_once_with(200)
    handler.send_header.assert_any_call('Content-Type', 'application/json; charset=utf-8')
    handler.send_header.assert_any_call('Content-Length', str(len(encoded)))
    handler.end_headers.assert_called_once()
    handler.wfile.write.assert_called_once()
    assert handler.close_connection is False


@pytest.mark.parametrize('stage', ['end_headers', 'write'])
@pytest.mark.parametrize('error_type', [BrokenPipeError, ConnectionResetError])
def test_client_disconnect_is_handled_without_second_response(stage, error_type):
    handler = writer()
    failing = handler.end_headers if stage == 'end_headers' else handler.wfile.write
    failing.side_effect = error_type('Cliente encerrou a conexão.')
    assert handler._send(200, {'ok': True}) is False
    assert handler.close_connection is True
    handler.send_response.assert_called_once_with(200)
    failing.assert_called_once()
    if stage == 'end_headers':
        handler.wfile.write.assert_not_called()
    else:
        handler.wfile.write.assert_called_once()


@pytest.mark.parametrize('stage', ['end_headers', 'write'])
@pytest.mark.parametrize('error_type', [RuntimeError, ValueError, PermissionError])
def test_unexpected_write_errors_are_not_hidden_as_client_disconnect(stage, error_type):
    handler = writer()
    failing = handler.end_headers if stage == 'end_headers' else handler.wfile.write
    failing.side_effect = error_type('Falha inesperada ao escrever.')
    with pytest.raises(error_type, match='Falha inesperada'):
        handler._send(200, {'ok': True})
    assert handler.close_connection is False
    failing.assert_called_once()


@pytest.mark.parametrize('ready', [True, False])
def test_main_prewarms_proofs_before_http_bind_only_when_model_is_ready(monkeypatch, ready):
    events = []
    instance = service(model=ready)
    instance.tools = object()
    instance.reply = Mock()
    instance.core_status = Mock(side_effect=lambda: events.append('proofs'))
    fake_server = SimpleNamespace(serve_forever=Mock(side_effect=lambda: events.append('serve')))

    def fake_model(*args, **kwargs):
        events.append('model')
        return instance

    def fake_bind(*args, **kwargs):
        events.append('bind')
        return fake_server

    monkeypatch.setattr(model_server, 'SERVICE', None)
    monkeypatch.setattr(model_server, 'RUNS', None)
    monkeypatch.setattr(model_server, 'ModelService', fake_model)
    monkeypatch.setattr(model_server, 'RunEngine', Mock(return_value=object()))
    monkeypatch.setattr(model_server, 'ThreadingHTTPServer', fake_bind)
    monkeypatch.setattr(sys, 'argv', ['model_server.py', '--checkpoint', 'unit-fixture-only', '--port', '0'])
    model_server.main()
    if ready:
        instance.core_status.assert_called_once()
        assert events.index('proofs') < events.index('bind')
    else:
        instance.core_status.assert_not_called()
    assert events[0] == 'model'
    assert events[-2:] == ['bind', 'serve']
    fake_server.serve_forever.assert_called_once()
