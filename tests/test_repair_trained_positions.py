import sys
from pathlib import Path

import pytest
import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'python'))
from repair_trained_positions import native_training_checkpoint, repair_trained_prefix


def _checkpoints():
    source = {'config': {'context_length': 4}, 'state_dict': {
        'position_embedding.weight': torch.arange(8, dtype=torch.float32).reshape(4, 2),
        'other.weight': torch.ones(2, 2),
    }}
    extended = {'config': {'context_length': 8, 'training_context_length': 3,
                           'context_extension': {'method': 'interpolation'}}, 'state_dict': {
        'position_embedding.weight': torch.full((8, 2), -1.0),
        'other.weight': torch.ones(2, 2),
    }}
    return extended, source


def test_repair_restores_prefix_without_changing_source_or_tail():
    extended, source = _checkpoints()
    result = repair_trained_prefix(extended, source)
    weights = result['state_dict']['position_embedding.weight']
    assert torch.equal(weights[:3], source['state_dict']['position_embedding.weight'][:3])
    assert torch.equal(weights[3:], extended['state_dict']['position_embedding.weight'][3:])
    assert torch.all(extended['state_dict']['position_embedding.weight'] == -1)
    assert result['config']['context_extension']['prefix_exactly_restored'] is True


def test_repair_refuses_unrelated_weights():
    extended, source = _checkpoints()
    extended['state_dict']['other.weight'][0, 0] = 2
    with pytest.raises(ValueError, match='não deriva da origem'):
        repair_trained_prefix(extended, source)


def test_native_training_checkpoint_uses_only_trained_positions():
    extended, source = _checkpoints()
    native = native_training_checkpoint(extended, source)
    assert native['config']['context_length'] == 3
    assert native['config']['runtime_context_tokens'] == 3
    assert 'context_extension' not in native['config']
    assert torch.equal(native['state_dict']['position_embedding.weight'],
                       source['state_dict']['position_embedding.weight'][:3])
