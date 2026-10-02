"""Small supervised action selector; no generated JSON and no tool execution."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path

import torch
from torch import nn
from tokenizer import ByteBPETokenizer

ROOT = Path(__file__).resolve().parents[1]
LABELS = ('direct', 'web', 'local', 'clarify')
CONFIG_PATH = ROOT / 'model/cognitive-router/active.json'


class DecisionRouter(nn.Module):
    def __init__(self, config):
        super().__init__()
        hidden = config['hidden_size']
        self.token_embedding = nn.Embedding(config['vocab_size'], hidden)
        self.position_embedding = nn.Embedding(config['context_length'], hidden)
        self.blocks = nn.TransformerEncoder(nn.TransformerEncoderLayer(
            hidden, config['attention_heads'], hidden * config.get('feed_forward_multiplier', 4),
            dropout=0.0, activation='gelu', batch_first=True, norm_first=True), config['layers'])
        self.norm = nn.LayerNorm(hidden)
        self.classifier = nn.Linear(hidden, len(LABELS))

    def forward(self, tokens, lengths):
        positions = torch.arange(tokens.shape[1], device=tokens.device)[None, :]
        hidden = self.token_embedding(tokens) + self.position_embedding(positions)
        mask = torch.triu(torch.ones(tokens.shape[1], tokens.shape[1], device=tokens.device, dtype=torch.bool), diagonal=1)
        hidden = self.blocks(hidden, mask=mask)
        valid = (positions < lengths[:, None]).unsqueeze(-1)
        pooled = (hidden * valid).sum(1) / lengths[:, None]
        return self.classifier(self.norm(pooled))


def encode_goal(tokenizer, goal, context):
    ids = tokenizer.encode_fast(goal.strip()) + [tokenizer.special_tokens['<eos>']]
    if len(ids) > context:
        raise ValueError('Pedido excede o contexto treinado do seletor de ações.')
    return ids


def load_router(manifest_path=CONFIG_PATH):
    from safetensors.torch import load_file
    path = Path(manifest_path)
    metadata = json.loads(path.read_text())
    if metadata.get('schema') != 'cognitive-router/v1' or metadata.get('labels') != list(LABELS):
        raise ValueError('Metadados do seletor incompatíveis.')
    weights = (ROOT / metadata['weights']).resolve()
    if metadata.get('architecture') == 'char-ngram-router/v1':
        if not weights.is_relative_to(ROOT) or hashlib.sha256(weights.read_bytes()).hexdigest() != metadata['sha256']:
            raise ValueError('Integridade do seletor não confirmada.')
        model = CharacterRouter(metadata['buckets'])
        model.load_state_dict(load_file(str(weights)))
        model.eval()
        return model, None, metadata
    tokenizer_path = (ROOT / metadata['tokenizer']).resolve()
    for target, expected in [(weights, metadata['sha256']), (tokenizer_path, metadata['tokenizer_sha256'])]:
        if not target.is_relative_to(ROOT) or hashlib.sha256(target.read_bytes()).hexdigest() != expected:
            raise ValueError('Integridade do seletor não confirmada.')
    model = DecisionRouter(metadata['config'])
    model.load_state_dict(load_file(str(weights)))
    model.eval()
    return model, ByteBPETokenizer.load(tokenizer_path), metadata


def predict_router(bundle, goal):
    model, tokenizer, metadata = bundle
    with torch.inference_mode():
        if metadata.get('architecture') == 'char-ngram-router/v1':
            probabilities = model(character_features(goal, metadata['buckets'])[None, :]).softmax(-1)[0]
        else:
            ids = encode_goal(tokenizer, goal, metadata['config']['context_length'])
            probabilities = model(torch.tensor([ids]), torch.tensor([len(ids)])).softmax(-1)[0]
    best = int(probabilities.argmax())
    confidence = float(probabilities[best])
    return {'label': LABELS[best], 'score': confidence,
            'accepted': confidence >= metadata.get('threshold', 0.8),
            'scores': dict(zip(LABELS, probabilities.tolist()))}


def character_features(goal, buckets=8192):
    import math
    import unicodedata
    text = ' ' + ''.join(c for c in unicodedata.normalize('NFKD', goal.lower()) if not unicodedata.combining(c)) + ' '
    if len(text) > 2002:
        raise ValueError('Pedido excede o limite do seletor de ações.')
    values = torch.zeros(buckets)
    for size in (2, 3, 4, 5):
        for start in range(len(text) - size + 1):
            digest = hashlib.blake2s(text[start:start + size].encode(), digest_size=4).digest()
            index = int.from_bytes(digest, 'little') % buckets
            values[index] += 1
    values = torch.log1p(values)
    return values / values.norm().clamp(min=1e-8)


class CharacterRouter(nn.Module):
    def __init__(self, buckets=8192):
        super().__init__()
        self.classifier = nn.Linear(buckets, len(LABELS))

    def forward(self, features):
        return self.classifier(features)
