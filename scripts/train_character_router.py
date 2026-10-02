#!/usr/bin/env python3
"""Small learned action classifier. No external model or corpus; no auto-promotion."""
import argparse
import hashlib
import json
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'python'))
import torch
from safetensors.torch import save_file
from cognitive_router import CharacterRouter, LABELS, character_features
from train_cognitive_router import datasets

parser = argparse.ArgumentParser()
parser.add_argument('--output', type=Path, required=True)
args = parser.parse_args()
args.output.mkdir(parents=True, exist_ok=False)
torch.set_num_threads(2)
torch.manual_seed(137)
train, validation, regression = datasets()
for name, rows in [('train', train), ('validation', validation), ('regression', regression)]:
    (args.output / (name + '.jsonl')).write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rows))
features = lambda rows: torch.stack([character_features(row['goal']) for row in rows])
labels = lambda rows: torch.tensor([LABELS.index(row['label']) for row in rows])
x, y, vx, vy = features(train), labels(train), features(validation), labels(validation)
model = CharacterRouter()
optimizer = torch.optim.AdamW(model.parameters(), lr=.04, weight_decay=.01)
weights = torch.tensor([len(train) / sum(row['label'] == label for row in train) for label in LABELS])
best = float('inf')
for step in range(800):
    optimizer.zero_grad()
    loss = torch.nn.functional.cross_entropy(model(x), y, weight=weights)
    loss.backward()
    optimizer.step()
    if (step + 1) % 40 == 0:
        with torch.inference_mode():
            logits = model(vx)
            val_loss = float(torch.nn.functional.cross_entropy(logits, vy))
            accuracy = float((logits.argmax(-1) == vy).float().mean())
        print(json.dumps({'step': step + 1, 'validation_loss': val_loss, 'validation_accuracy': accuracy}), flush=True)
        if val_loss < best:
            best = val_loss
            save_file(model.state_dict(), str(args.output / 'candidate.safetensors'))
metadata = {'schema': 'cognitive-router/v1', 'architecture': 'char-ngram-router/v1',
            'buckets': 8192, 'labels': list(LABELS), 'threshold': .8,
            'weights': str((args.output / 'candidate.safetensors').relative_to(ROOT)),
            'sha256': hashlib.sha256((args.output / 'candidate.safetensors').read_bytes()).hexdigest(),
            'training_rows': len(train), 'validation_rows': len(validation),
            'selection': 'minimum validation loss', 'validation_loss': best,
            'scope': 'learned short-request action selection, not semantic language generation',
            'dataset_sha256': hashlib.sha256((args.output / 'train.jsonl').read_bytes()).hexdigest()}
(args.output / 'candidate.json').write_text(json.dumps(metadata, indent=2) + '\n')
