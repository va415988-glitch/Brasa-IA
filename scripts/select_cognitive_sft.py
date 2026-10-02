"""Select on evidence-grounded validation; audit reserved cases after selection."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import evaluate_cognitive_sft
from evaluate_cognitive_sft import checkpoint_identity, evaluate, read_cases, summarize, qualification


def validate_splits(train, validation, heldout):
    if not all([train, validation, heldout]):
        raise ValueError('All three splits must contain cases.')
    keys = lambda rows: {row['messages'][0]['content'] for row in rows}
    entities = lambda rows: {row['entity'] for row in rows}
    for a, b in [(train, validation), (train, heldout), (validation, heldout)]:
        if keys(a) & keys(b) or entities(a) & entities(b):
            raise ValueError('Evaluation overlaps training/selection inputs or entities.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir', type=Path, required=True)
    parser.add_argument('--validation', type=Path, required=True)
    parser.add_argument('--heldout', type=Path, required=True)
    args = parser.parse_args()
    train = read_cases(args.run_dir / 'train.jsonl')
    validation = read_cases(args.validation)
    heldout = read_cases(args.heldout)
    validate_splits(train, validation, heldout)
    manifest = json.loads((args.run_dir / 'manifest.json').read_text())
    for path in [args.validation, args.heldout]:
        expected = next((digest for name, digest in manifest['inputs'].items()
                         if Path(name).resolve() == path.resolve()), None)
        if not expected or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError('Evaluation input differs from the training manifest.')
    candidates = []
    for checkpoint in sorted((args.run_dir / 'snapshots').glob('*.safetensors')):
        metadata = json.loads(Path(str(checkpoint) + '.json').read_text())
        rows = evaluate(checkpoint, validation)
        entry = {'checkpoint': str(checkpoint), 'summary': summarize(rows),
                 'validation_loss': metadata['validation_loss'], 'cases': rows}
        candidates.append(entry)
        (args.run_dir / 'selection-progress.json').write_text(json.dumps(candidates, ensure_ascii=False, indent=2))
        print(json.dumps({'phase': 'validation', 'checkpoint': str(checkpoint), **entry['summary']}), flush=True)
    if not candidates:
        raise ValueError('No validation snapshots found.')
    chosen = max(candidates, key=lambda candidate: (
        candidate['summary']['passed'], -candidate['summary']['unsafe_answers'],
        candidate['summary']['paired_groups_passed'], -candidate['validation_loss']))
    print('Reserved evaluation after selection: ' + chosen['checkpoint'], flush=True)
    rows = evaluate(chosen['checkpoint'], heldout)
    summary = summarize(rows)
    report = {'schema': 'cognitive-sft-selection/v1', 'selected_checkpoint': chosen['checkpoint'],
              **checkpoint_identity(chosen['checkpoint']),
              'training_manifest_sha256': hashlib.sha256((args.run_dir / 'manifest.json').read_bytes()).hexdigest(),
              'evaluator_sha256': hashlib.sha256(Path(evaluate_cognitive_sft.__file__).read_bytes()).hexdigest(),
              'selection_rule': 'semantic validation, unsafe answers, paired consistency; loss only breaks ties',
              'validation_source': str(args.validation), 'heldout_source': str(args.heldout),
              'validation_sha256': hashlib.sha256(args.validation.read_bytes()).hexdigest(),
              'heldout_sha256': hashlib.sha256(args.heldout.read_bytes()).hexdigest(),
              'validation_candidates': candidates, 'validation_summary': chosen['summary'],
              'heldout_summary': summary, 'heldout_cases': rows,
              'qualified_for_review': qualification(chosen['summary']) and qualification(summary),
              'promoted': False, 'live_tools_executed': False, 'runtime_recipes_used': False,
              'unsafe_answer_definition': 'All well-formed answer proposals failing the oracle, including rejected references.',
              'limits': ['Paired synthetic observations; task families appear in train.',
                         'Entity-disjoint prompts with new numeric values.',
                         'A short-context candidate remains experimental even if narrow tests pass.']}
    (args.run_dir / 'semantic-selection.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({k: report[k] for k in ['selected_checkpoint', 'validation_summary', 'heldout_summary', 'qualified_for_review', 'promoted']}), flush=True)
    return 0 if report['qualified_for_review'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
