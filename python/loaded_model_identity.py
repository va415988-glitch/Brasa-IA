"""Bind a loaded model to stable files; this is integrity evidence, not competence.

Import this module before loading the runtime dependencies. Capture immediately
before and after loading the checkpoint/tokenizer, then confirm those snapshots.
Before specialist or experimental dispatch, assert the confirmed identity is
still current. File edits require a fresh process and a fresh load.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = 'loaded-model-identity/v1'
DEFAULT_SOURCE_PATHS = (
    'python/loaded_model_identity.py', 'python/cognitive_cores.py', 'python/model.py',
    'python/model_server.py', 'python/tokenizer.py', 'python/cognitive_dialogue.py',
    'python/conditioned_context.py', 'python/tool_registry.py',
    'contracts/read_file.json', 'contracts/open_page.json',
    'scripts/evaluate_cognitive_sft.py', 'scripts/certify_cognitive_cores.py',
    'scripts/prepare_core_qualification.py', 'python/checkpoint_io.py',
    'python/context_policy.py', 'python/context_strategy.py',
    'python/conversation_compaction.py', 'python/generation_utils.py',
    'python/repair_trained_positions.py',
    'python/experimental_model.py',
    'python/dialogue.py', 'python/dialogue_api.py', 'python/cognitive_actions.py',
    'python/cognitive_router.py', 'python/product_planning.py',
)
GENERATION_ENVIRONMENT = ('IA_LOCAL_NUM_PREDICT', 'IA_LOCAL_REPETITION_PENALTY')


def _stamp(stat):
    return [stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns]


def _stable_file(path, *, contents=False):
    """Hash the same file instance and reject a replacement during the read."""
    digest = hashlib.sha256()
    collected = [] if contents else None
    with path.open('rb') as handle:
        before = _stamp(os.fstat(handle.fileno()))
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
            if collected is not None:
                collected.append(chunk)
        after = _stamp(os.fstat(handle.fileno()))
    if before != after or after != _stamp(path.stat()):
        raise ValueError('Arquivo mudou durante a leitura da identidade: ' + str(path))
    return {'sha256': digest.hexdigest(), 'file_state': after}, (b''.join(collected) if contents else None)


def _local_path(name, root):
    if not isinstance(name, (str, Path)) or not str(name):
        raise ValueError('Caminho de identidade inválido.')
    path = (root / name).resolve()
    if not path.is_relative_to(root):
        raise ValueError('Arquivo da identidade fora do projeto: ' + str(name))
    return path


# Captured before the runtime loads model_server/model/tokenizer. New file bytes
# cannot certify Python code already compiled from an older version.
_IMPORTED_SOURCE_HASHES = {
    str((ROOT / name).resolve()): _stable_file(ROOT / name)[0]['sha256']
    for name in DEFAULT_SOURCE_PATHS
}


def capture_model_identity(checkpoint_path, *, root=ROOT, source_paths=None, catalog_path=None):
    """Capture weights, sidecar, the tokenizer it names, sources and catalog.

    Custom source paths support isolated tests. Production callers should retain
    the complete default source list and import this module before the runtime.
    """
    root = Path(root).resolve()
    try:
        checkpoint = _local_path(checkpoint_path, root)
        metadata = _local_path(str(checkpoint) + '.json', root)
        files = {}

        def capture(path, *, contents=False):
            identity, data = _stable_file(path, contents=contents)
            files[str(path)] = identity
            return data

        visiting = set()
        context_sources = []

        def capture_checkpoint(path):
            if path in visiting or len(visiting) >= 32:
                raise ValueError('Origens de contexto do checkpoint formam um ciclo ou cadeia excessiva.')
            visiting.add(path)
            capture(path)
            sidecar = _local_path(str(path) + '.json', root)
            config = json.loads(capture(sidecar, contents=True))
            if not isinstance(config, dict) or not isinstance(config.get('config'), dict):
                raise ValueError('Metadados de identidade inválidos.')
            tokenizer_name = config['config'].get('tokenizer_path')
            if not isinstance(tokenizer_name, str) or not tokenizer_name:
                raise ValueError('O checkpoint precisa identificar seu tokenizer.')
            tokenizer_path = _local_path(tokenizer_name, root)
            capture(tokenizer_path)
            if config.get('tokenizer_sha256') and config['tokenizer_sha256'] != files[str(tokenizer_path)]['sha256']:
                raise ValueError('Tokenizer diverge do hash declarado no checkpoint.')
            extension = config['config'].get('context_extension') or {}
            if not isinstance(extension, dict):
                raise ValueError('Extensão de contexto inválida nos metadados.')
            if extension.get('method') == 'linear-interpolation-trained-position-span-v1':
                source = _local_path(extension.get('source_checkpoint'), root)
                source_tokenizer = capture_checkpoint(source)
                if extension.get('source_sha256') and extension['source_sha256'] != files[str(source)]['sha256']:
                    raise ValueError('Origem das posições treinadas diverge do hash declarado.')
                context_sources.append({'checkpoint': str(source), 'metadata': str(source) + '.json',
                                        'tokenizer': str(source_tokenizer)})
            visiting.remove(path)
            return tokenizer_path

        tokenizer = capture_checkpoint(checkpoint)
        sources = tuple(source_paths) if source_paths is not None else DEFAULT_SOURCE_PATHS
        if not sources:
            raise ValueError('A identidade precisa vincular as fontes do runtime.')
        source_files = list(dict.fromkeys(str(_local_path(name, root)) for name in sources))
        for name in source_files:
            capture(Path(name))
            imported = _IMPORTED_SOURCE_HASHES.get(name)
            if imported is not None and imported != files[name]['sha256']:
                raise ValueError('Fonte mudou após importar o runtime; reinicie o processo: ' + name)
        catalog = _local_path(catalog_path or 'config/cognitive_cores.json', root)
        capture(catalog)
        return {
            'schema': SCHEMA, 'root': str(root), 'checkpoint': str(checkpoint),
            'metadata': str(metadata), 'tokenizer': str(tokenizer), 'catalog': str(catalog),
            'source_paths': source_files, 'files': files, 'context_sources': context_sources,
            'generation_environment': {name: os.environ.get(name) for name in GENERATION_ENVIRONMENT},
        }
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as error:
        raise ValueError('Identidade do modelo indisponível: ' + str(error)) from error


def confirm_loaded_identity(before, after):
    """Return a detached envelope only when the whole load saw stable artifacts."""
    if (not isinstance(before, dict) or before.get('schema') != SCHEMA
            or not isinstance(after, dict) or after.get('schema') != SCHEMA
            or before != after):
        raise ValueError('Arquivos ou configuração mudaram durante o carregamento do modelo.')
    return copy.deepcopy(after)


def assert_loaded_identity_current(identity, checkpoint_path=None, *, root=ROOT):
    """Fail closed when files/settings no longer describe the loaded instance."""
    root = Path(root).resolve()
    if not isinstance(identity, dict) or identity.get('schema') != SCHEMA or identity.get('root') != str(root):
        raise ValueError('Identidade carregada ausente ou incompatível.')
    try:
        expected = _local_path(checkpoint_path or identity['checkpoint'], root)
        if str(expected) != identity['checkpoint']:
            raise ValueError('O checkpoint pedido difere do modelo carregado.')
        current = capture_model_identity(expected, root=root, source_paths=identity['source_paths'],
                                         catalog_path=identity['catalog'])
        if current != identity:
            raise ValueError('Modelo carregado diverge dos arquivos/configuração atuais; recarregue o processo.')
    except (KeyError, TypeError) as error:
        raise ValueError('Identidade carregada incompleta.') from error
    return True
