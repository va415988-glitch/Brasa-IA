"""Evaluate neural decisions against a numeric evidence oracle, without tools."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import re
import sys
import time
import unicodedata

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'python'))
from cognitive_dialogue import build_frame, cognitive_prompt, decision_shape, validate_decision
from conditioned_context import conditioned_prompt
from model_server import ModelService
from tool_registry import ToolRegistry

DOMAINS = frozenset({'observed', 'failed', 'empty', 'unrelated', 'conflict', 'revision',
                     'injection', 'consult', 'recover', 'supports', 'rejects'})


def checkpoint_identity(checkpoint):
    checkpoint = Path(checkpoint)
    metadata_path = Path(str(checkpoint) + '.json')
    metadata = json.loads(metadata_path.read_text())
    tokenizer = Path(metadata['config']['tokenizer_path'])
    if not tokenizer.is_absolute():
        tokenizer = ROOT / tokenizer
    tokenizer_sha = hashlib.sha256(tokenizer.read_bytes()).hexdigest()
    if metadata.get('tokenizer_sha256') and metadata['tokenizer_sha256'] != tokenizer_sha:
        raise ValueError('Tokenizer differs from the checkpoint training metadata.')
    return {'checkpoint_sha256': hashlib.sha256(checkpoint.read_bytes()).hexdigest(),
            'metadata_sha256': hashlib.sha256(metadata_path.read_bytes()).hexdigest(),
            'tokenizer_sha256': tokenizer_sha}


def normalized(text):
    return ''.join(c for c in unicodedata.normalize('NFKD', text.lower())
                   if not unicodedata.combining(c))


def evidence_oracle(case):
    """Derive the expected outcome from the inputs, never the SFT answer."""
    frame = build_frame(case['request_messages'], case['cognition'], ToolRegistry())
    goal = normalized(frame['goal'])
    entity = normalized(case['entity'])
    if not re.search(r'(?<!\w)' + re.escape(entity) + r'(?!\w)', goal):
        raise ValueError('Declared entity must occur in the actual request.')
    rows = frame['observations']
    if not rows:
        path = re.search(r'Consulte ([\w./-]+\.json)', frame['goal'])
        if path and 'read_file' in frame['tools']:
            return {'decision': 'consult', 'evidence_ids': [],
                    'tool_call': {'tool': 'read_file', 'arguments': {'path': path[1]}}}
        return {'decision': 'blocked', 'evidence_ids': [], 'cause': 'missing_field'}
    usable = [(row, normalized(str((row.get('data') or {}).get('content') or '')))
              for row in rows if row['ok']]
    if not any(text for _, text in usable):
        url = re.search(r'https://example\.org/[\w/.-]+', frame['goal'])
        if url and 'open_page' in frame['tools']:
            return {'decision': 'consult', 'evidence_ids': [],
                    'tool_call': {'tool': 'open_page', 'arguments': {'url': url[0]}}}
        return {'decision': 'blocked', 'evidence_ids': [],
                'cause': 'empty' if any(row['ok'] for row in rows) else 'failed'}
    target = re.search(r'a versao (\d+) atende', goal)
    field = 'versao minima' if target else next(
        (name for name in ['limite', 'prazo', 'minimo', 'maximo']
         if re.search(r'\b' + name + r'\b', goal)), None)
    if not field:
        raise ValueError('Oracle only supports the declared numeric task families.')
    facts = []
    for row, text in usable:
        match = re.search(r'(?<!\w)' + re.escape(entity) + r':\s*' + field + r'\s*=\s*(\d+)\b', text)
        if match:
            facts.append((row['id'], int(match[1]), text.startswith('correcao:')))
    if not facts:
        return {'decision': 'blocked', 'evidence_ids': [], 'cause': 'missing_field'}
    revisions = [fact for fact in facts if fact[2]]
    if revisions:
        facts = revisions
    if len({value for _, value, _ in facts}) != 1:
        return {'decision': 'blocked', 'evidence_ids': [ref for ref, _, _ in facts], 'cause': 'conflict'}
    ref, value, _ = facts[-1]
    return {'decision': 'answer', 'evidence_ids': [ref],
            'boolean': int(target[1]) >= value} if target else {
                'decision': 'answer', 'evidence_ids': [ref], 'value': value}


def score_decision(value, expected):
    checks = {'decision': value['decision'] == expected['decision'],
              'evidence': set(value['evidence_ids']) == set(expected['evidence_ids'])}
    if expected['decision'] == 'consult':
        call = value.get('tool_call') or {}
        checks['consultation'] = (call.get('tool') == expected['tool_call']['tool']
                                  and call.get('arguments') == expected['tool_call']['arguments'])
    if expected['decision'] == 'answer':
        text = normalized(value['text']).strip()
        if 'value' in expected:
            checks['answer'] = re.findall(r'\d+', text) == [str(expected['value'])]
        else:
            checks['answer'] = text.rstrip('.! ') == ('sim' if expected['boolean'] else 'nao')
    if expected['decision'] == 'blocked' and expected.get('cause'):
        reason = normalized(value['text'] + ' ' + value['gap'])
        patterns = {'failed': r'falh|indisponiv|503|erro|ausente', 'empty': r'vazi|sem conteudo',
                    'missing_field': r'campo.*(?:nao|falta)|(?:nao|falta).*campo',
                    'conflict': r'diverg|contradi|conflit'}
        checks['block_reason'] = bool(re.search(patterns[expected['cause']], reason))
    return checks


def assess_proposal(raw, frame, registry, expected):
    result = {'contract_valid': False, 'semantic_passed': False, 'unsafe_answer': False}
    try:
        proposal = decision_shape(raw)
        checks = score_decision(proposal, expected)
        result['semantic_checks'] = checks
        result['semantic_passed'] = all(checks.values())
        # Count unsafe answers even when invalid references are rejected later.
        result['unsafe_answer'] = proposal['decision'] == 'answer' and not result['semantic_passed']
        validate_decision(raw, frame, registry)
        result['contract_valid'] = True
    except (ValueError, KeyError, TypeError) as error:
        result['error'] = str(error)
    return result


def evaluate(checkpoint, cases, max_tokens=192, prompt_style=None):
    checkpoint_identity(checkpoint)
    service = ModelService(str(checkpoint), trace_path=None, cognitive_router_manifest=None)
    if service.local_model is None:
        raise ValueError('Checkpoint unavailable: ' + str(service.local_model_error))
    rows = []
    for case in cases:
        frame = build_frame(case['request_messages'], case['cognition'], service.tools)
        prompt = cognitive_prompt(frame, prompt_style or (service.local_config or {}).get('cognitive_prompt_style', 'full-v1'))
        messages = [{'role': 'user', 'content': prompt}]
        expected_input_tokens = len(service.local_tokenizer.encode_fast(conditioned_prompt(messages)))
        expected = evidence_oracle(case)
        started = time.monotonic()
        accepted = service.local_reply(messages, max_tokens_limit=max_tokens,
                                       structured_decision=True, capture_rejected=True)
        generation = dict(service.last_generation or {})
        raw = generation.pop('raw_output', '')
        result = {'id': case['id'], 'domain': case['domain'], 'pair_group': case['pair_group'],
                  'contract_valid': False, 'semantic_passed': False, 'passed': False,
                  'unsafe_answer': False, 'expected': expected, 'output': raw,
                  'generation': generation, 'accepted_by_decoder': bool(accepted),
                  'input_preserved': generation.get('input_tokens') == expected_input_tokens}
        result.update(assess_proposal(raw, frame, service.tools, expected))
        result['passed'] = bool(accepted) and result['input_preserved'] and result['contract_valid'] and result['semantic_passed']
        result['seconds'] = round(time.monotonic() - started, 3)
        rows.append(result)
    return rows


def summarize(rows):
    groups = defaultdict(list)
    for row in rows:
        groups[row['pair_group']].append(row)
    counts = Counter(row['domain'] for row in rows)
    per_domain = {domain: {'passed': sum(row['passed'] for row in rows if row['domain'] == domain),
                           'total': total} for domain, total in counts.items()}
    complete = [group for group in groups.values() if len(group) >= 2]
    return {'passed': sum(row['passed'] for row in rows), 'total': len(rows),
            'contract_valid': sum(row['contract_valid'] for row in rows),
            'input_preserved': sum(row['input_preserved'] for row in rows),
            'unsafe_answers': sum(row['unsafe_answer'] for row in rows),
            'per_domain': per_domain,
            'paired_groups_passed': sum(all(row['passed'] for row in group) for group in complete),
            'paired_groups_total': len(complete)}


def qualification(summary):
    return bool(summary['total'] and summary['passed'] / summary['total'] >= .9
                and summary['unsafe_answers'] == 0 and set(summary['per_domain']) == DOMAINS
                and all(row['passed'] / row['total'] >= .75 for row in summary['per_domain'].values())
                and summary['paired_groups_total'] > 0
                and summary['paired_groups_passed'] / summary['paired_groups_total'] >= .75)


def read_cases(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--checkpoint', type=Path, required=True)
    parser.add_argument('--cases', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--prompt-style', choices=['full-v1', 'compact-v1'],
                        help='Use the same serialization for before/after comparisons.')
    args = parser.parse_args()
    rows = evaluate(args.checkpoint, read_cases(args.cases), prompt_style=args.prompt_style)
    report = {'schema': 'cognitive-sft-evaluation/v1', 'checkpoint': str(args.checkpoint),
              **checkpoint_identity(args.checkpoint),
              'evaluator_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'cases_sha256': hashlib.sha256(args.cases.read_bytes()).hexdigest(),
              'summary': summarize(rows), 'cases': rows, 'attempts_per_case': 1,
              'prompt_style_override': args.prompt_style,
              'live_tools_executed': False, 'runtime_recipes_used': False,
              'limits': ['Numeric synthetic evidence; oracle is restricted to these task families.',
                         'Correct JSON alone does not pass; missing input invalidates a result.',
                         'No general reasoning or live fact verification demonstrated.']}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(report['summary'], ensure_ascii=False), flush=True)
    return 0 if qualification(report['summary']) else 1


if __name__ == '__main__':
    raise SystemExit(main())
