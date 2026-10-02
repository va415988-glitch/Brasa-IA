#!/usr/bin/env python3
"""Evaluate reserved action labels; activate only if fixed acceptance gates pass."""
import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'python'))
import torch
from cognitive_router import CONFIG_PATH, LABELS, load_router, predict_router


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--candidate', type=Path, required=True)
    parser.add_argument('--eval', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--activate', action='store_true')
    args = parser.parse_args()
    torch.set_num_threads(2)
    bundle = load_router(args.candidate)
    cases = json.loads(args.eval.read_text())
    training = [json.loads(line) for line in (args.candidate.parent / 'train.jsonl').read_text().splitlines()]
    assert not ({row['goal'] for row in cases} & {row['goal'] for row in training}), 'Evaluation overlaps training'
    rows = []
    for case in cases:
        result = predict_router(bundle, case['goal'])
        rows.append({**case, **result, 'expected': case['label'], 'passed': result['label'] == case['label']})
    count = len(rows)
    passed = sum(row['passed'] for row in rows)
    accepted = [row for row in rows if row['accepted']]
    unsafe = sum(not row['passed'] for row in accepted)
    per_label = {label: sum(row['passed'] for row in rows if row['expected'] == label)
                 / max(1, sum(row['expected'] == label for row in rows)) for label in LABELS}
    gates = {'accuracy_at_least_90pct': passed / count >= .9,
             'every_label_at_least_75pct': min(per_label.values()) >= .75,
             'accepted_coverage_at_least_75pct': len(accepted) / count >= .75,
             'no_confident_errors': unsafe == 0}
    report = {'mode': 'supervised-action-selection', 'passed': passed, 'total': count,
              'accepted': len(accepted), 'confident_errors': unsafe, 'per_label': per_label,
              'gates': gates, 'eligible': all(gates.values()), 'cases': rows,
              'candidate_sha256': bundle[2]['sha256'],
              'evaluation_sha256': hashlib.sha256(args.eval.read_bytes()).hexdigest(),
              'limitations': ['Does not evaluate free-form reasoning or answer generation',
                             'Portuguese, short requests; synthetic evaluation authored locally']}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({key: report[key] for key in ('passed', 'total', 'accepted', 'confident_errors', 'eligible', 'gates')}), flush=True)
    if args.activate:
        if not report['eligible']:
            raise SystemExit('Candidato reprovado; nenhum seletor ativo foi alterado.')
        metadata = {**bundle[2], 'evaluation': str(args.report.relative_to(ROOT)),
                    'evaluation_sha256': hashlib.sha256(args.report.read_bytes()).hexdigest()}
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        temporary = CONFIG_PATH.with_suffix('.tmp')
        temporary.write_text(json.dumps(metadata, indent=2) + '\n')
        temporary.replace(CONFIG_PATH)
        print('Activated:', CONFIG_PATH)


if __name__ == '__main__':
    main()
