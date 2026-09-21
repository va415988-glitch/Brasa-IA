"""Evidências mínimas para promoção; metadados de capacidade não são provas."""
import hashlib
import json
import math
from pathlib import Path

REQUIRED_SUITES = ('retention', 'programming', 'tools', 'verification',
                   'regression', 'creative', 'long_generation')


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def config_hash(config):
    return hashlib.sha256(json.dumps(config, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def positive_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0


def evidence_gates(source, checkpoint, tokenizer, evaluation=None):
    path = Path(evaluation) if evaluation else Path(str(source) + '.evaluation.json')
    gates = {'behavioral_report_readable': False, 'behavioral_schema': False,
             'evaluated_checkpoint_matches': False, 'evaluated_config_matches': False,
             'evaluated_tokenizer_matches': False, 'reserved_evaluation': False,
             **{f'behavioral_{name}': False for name in REQUIRED_SUITES},
             'long_generation_observed': False, 'retention_observed': False,
             'latency_budget': False, 'memory_measured': False}
    try:
        report = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(report, dict):
            return gates, str(path)
        gates['behavioral_report_readable'] = True
        gates['behavioral_schema'] = report.get('schema') == 'checkpoint-release-evidence/v1'
        gates['evaluated_checkpoint_matches'] = report.get('checkpoint_sha256') == file_hash(source)
        gates['evaluated_config_matches'] = report.get('config_sha256') == config_hash(checkpoint.get('config', {}))
        fingerprint = file_hash(tokenizer)
        gates['evaluated_tokenizer_matches'] = (
            report.get('tokenizer_sha256') == fingerprint == checkpoint.get('tokenizer_sha256'))
        provenance = report.get('evaluation_provenance', {})
        gates['reserved_evaluation'] = (isinstance(provenance, dict)
                                        and provenance.get('used_for_training') is False
                                        and provenance.get('used_for_selection') is False
                                        and isinstance(provenance.get('suite_sha256'), str)
                                        and len(provenance['suite_sha256']) == 64)
        suites = report.get('suites', {})
        if not isinstance(suites, dict):
            suites = {}
        for name in REQUIRED_SUITES:
            cases = suites.get(name)
            gates[f'behavioral_{name}'] = (
                isinstance(cases, list) and len(cases) > 0
                and all(isinstance(case, dict) and case.get('passed') is True
                        and isinstance(case.get('id'), str) and bool(case['id'].strip())
                        and isinstance(case.get('evidence'), dict) and bool(case['evidence'])
                        for case in cases))
        long_cases = suites.get('long_generation')
        if gates['behavioral_long_generation']:
            gates['long_generation_observed'] = all(
                positive_number(case.get('output_tokens')) and case['output_tokens'] >= 4096
                and case.get('complete') is True and case.get('truncated') is False
                for case in long_cases)
        retention_cases = suites.get('retention')
        if gates['behavioral_retention']:
            gates['retention_observed'] = all(
                positive_number(case.get('input_tokens')) and case['input_tokens'] >= 8192
                for case in retention_cases)
        resources = report.get('resources', {})
        if isinstance(resources, dict):
            latency = resources.get('p95_seconds')
            gates['latency_budget'] = positive_number(latency) and latency <= 30
            gates['memory_measured'] = positive_number(resources.get('peak_rss_mb'))
    except (OSError, ValueError, TypeError):
        # Um relatório incompleto ou malformado jamais vira uma aprovação.
        pass
    return gates, str(path)
