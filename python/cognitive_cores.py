"""Separate bounded cognitive skills, measure them, and gate specialist dispatch.

A registered tool or a routing score is never a competency certificate. Existing
chat generation is independent; explicit core dispatch fails closed without proof.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import json
import re
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / 'config/cognitive_cores.json'
NUMERIC_SCOPE = 'synthetic-numeric-evidence/v1'
PROOF_SOURCES = ('python/cognitive_cores.py', 'python/model.py', 'python/model_server.py',
                 'python/tokenizer.py', 'python/cognitive_dialogue.py', 'python/conditioned_context.py',
                 'python/tool_registry.py', 'contracts/read_file.json', 'contracts/open_page.json',
                 'scripts/evaluate_cognitive_sft.py', 'scripts/certify_cognitive_cores.py',
                 'scripts/prepare_core_qualification.py', 'python/loaded_model_identity.py',
                 'python/checkpoint_io.py', 'python/context_policy.py', 'python/context_strategy.py',
                 'python/conversation_compaction.py', 'python/generation_utils.py',
                 'python/repair_trained_positions.py', 'python/experimental_model.py',
                 'python/dialogue.py', 'python/dialogue_api.py', 'python/cognitive_actions.py',
                 'python/cognitive_router.py', 'python/product_planning.py')


def validate_numeric_scope(messages, cognition, frame):
    """Enforce the measured interface; never derive or substitute the answer."""
    entity = r'[\w-]+'
    field = r'(?:limite|prazo|mínimo|máximo)'
    goal = (rf'(?:Qual é o {field} de {entity}\?|Informe o {field} do projeto {entity}\.'
            rf'|Em {entity}, qual valor foi registrado para {field}\?'
            rf'|A versão [0-9]{{1,6}} atende ao mínimo exigido por {entity}\?)'
            r'(?: Consulte [\w./-]+\.json\.| Alternativa: https://example\.org/[\w/.-]+)?')
    if (len(messages) > 4 or messages[0]['role'] != 'user'
            or any(m['role'] != 'tool' for m in messages[1:])
            or not re.fullmatch(goal, frame['goal'])
            or cognition.get('constraints')
            or set(cognition.get('available_tools', [])) - {'read_file', 'open_page'}):
        raise ValueError('A prova cobre somente pedidos numéricos sintéticos, sem histórico ou restrições adicionais.')
    for message in messages[1:]:
        source = json.loads(message['content'])
        if (not isinstance(source, dict) or source.get('tool') != 'read_file'
                or type(source.get('ok')) is not bool or not isinstance(source.get('data'), dict)
                or set(source['data']) != {'path', 'content'}
                or not isinstance(source['data']['content'], str) or len(source['data']['content']) > 200
                or not isinstance(source['data']['path'], str) or len(source['data']['path']) > 100
                or not isinstance(source.get('error'), str) or len(source['error']) > 100):
            raise ValueError('Observação fora do contrato medido do núcleo.')
    if len(frame['observations']) != len(messages) - 1:
        raise ValueError('O núcleo não pode ignorar uma observação da entrada.')


def numeric_core_requirements(frame):
    """Require the whole task, even when a caller requests just the JSON core."""
    ids = ['decision-format', 'evidence-abstention']
    ids.append('numeric-comparison' if frame['goal'].startswith('A versão ') else 'evidence-extraction')
    if frame['tools']:
        ids.append('tool-arguments')
    return ids


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def local_path(name, root=ROOT):
    path = (root / name).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError('Artefato de núcleo fora do projeto.')
    return path


def evaluator():
    # Deferred to avoid a model_server import cycle during service construction.
    scripts = str(ROOT / 'scripts')
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    import evaluate_cognitive_sft
    return evaluate_cognitive_sft


def core_summary(spec, rows):
    """Independent component scoring; counterpart domains prevent easy shortcuts."""
    selected = [row for row in rows if row['domain'] in spec['domains']]
    if not selected:
        return None
    results = []
    for row in selected:
        checks = row.get('semantic_checks', {})
        usable = row['contract_valid'] and row['input_preserved'] and row['accepted_by_decoder']
        if spec['metric'] == 'contract':
            passed = usable
            unsafe = False  # Semantics belongs to other cores, not the JSON core.
        elif spec['metric'] == 'decision-evidence':
            names = ['decision', 'evidence']
            if row['expected']['decision'] == 'blocked':
                names.append('block_reason')
            passed = usable and all(checks.get(name) is True for name in names)
            unsafe = row['unsafe_answer'] and row['expected']['decision'] != 'answer'
        else:
            passed = row['passed']
            unsafe = row['unsafe_answer']
        # Both absence variants must pass separately, not just their aggregate.
        family = row['domain']
        if family == 'unrelated' and row.get('variant'):
            family += '-' + row['variant']
        results.append({'domain': family, 'group': row['pair_group'], 'passed': bool(passed), 'unsafe': bool(unsafe)})
    groups = defaultdict(list)
    for row in results:
        groups[row['group']].append(row)
    counts = Counter(row['domain'] for row in results)
    return {'passed': sum(r['passed'] for r in results), 'total': len(results),
            'unsafe_answers': sum(r['unsafe'] for r in results),
            'per_domain': {name: {'passed': sum(r['passed'] for r in results if r['domain'] == name), 'total': count}
                           for name, count in counts.items()},
            'complete_groups_passed': sum(all(r['passed'] for r in group) for group in groups.values()),
            'complete_groups_total': len(groups)}


def qualifies(spec, summary):
    if not summary:
        return False
    policy = spec['qualification']
    domains = summary['per_domain']
    covered = {name.split('-')[0] for name in domains}
    return (covered == set(spec['domains']) and summary['total'] > 0
            and summary['passed'] / summary['total'] >= policy['overall']
            and summary['unsafe_answers'] <= policy['unsafe_answers']
            and all(row['total'] >= policy['minimum_per_domain']
                    and row['passed'] / row['total'] >= policy['per_domain'] for row in domains.values())
            and summary['complete_groups_total'] > 0
            and summary['complete_groups_passed'] / summary['complete_groups_total'] >= policy['complete_groups'])


def regrade_report(report, cases_path, checkpoint, root=ROOT):
    """Recompute the oracle and all flags from recorded outputs, not claimed scores."""
    from cognitive_dialogue import build_frame
    from tool_registry import ToolRegistry
    from tokenizer import ByteBPETokenizer
    from conditioned_context import conditioned_prompt
    from cognitive_dialogue import cognitive_prompt
    ev = evaluator()
    if report.get('schema') != 'cognitive-sft-evaluation/v1':
        raise ValueError('Relatório de comportamento incompatível.')
    if report.get('live_tools_executed') is not False or report.get('runtime_recipes_used') is not False or report.get('attempts_per_case') != 1:
        raise ValueError('A prova exige uma proposta própria por caso, sem receitas.')
    if report['cases_sha256'] != file_hash(cases_path) or report['evaluator_sha256'] != file_hash(Path(ev.__file__)):
        raise ValueError('Dados ou avaliador da prova mudaram.')
    for key, value in ev.checkpoint_identity(checkpoint).items():
        if report.get(key) != value:
            raise ValueError('A prova não corresponde aos pesos/metadados/tokenizer: ' + key)
    cases = {case['id']: case for case in ev.read_cases(cases_path)}
    rows = report.get('cases', [])
    if len(cases) != len(rows) or len({r['id'] for r in rows}) != len(rows):
        raise ValueError('Casos ausentes ou duplicados na prova.')
    metadata = json.loads(Path(str(checkpoint) + '.json').read_text())
    tokenizer = ByteBPETokenizer.load(local_path(metadata['config']['tokenizer_path'], root))
    style = report.get('prompt_style_override') or metadata['config'].get('cognitive_prompt_style', 'full-v1')
    registry = ToolRegistry()
    checked = []
    for row in rows:
        case = cases[row['id']]
        expected = ev.evidence_oracle(case)
        frame = build_frame(case['request_messages'], case['cognition'], registry)
        actual = ev.assess_proposal(row['output'], frame, registry, expected)
        if row.get('expected') != expected or any(row.get(k) != v for k, v in actual.items()):
            raise ValueError('Resultado não reproduzível no caso: ' + row['id'])
        input_tokens = len(tokenizer.encode_fast(conditioned_prompt([{'role': 'user', 'content': cognitive_prompt(frame, style)}])))
        preserved = row['generation']['input_tokens'] == input_tokens
        passed = bool(row['accepted_by_decoder'] and preserved and actual['contract_valid'] and actual['semantic_passed'])
        if row['input_preserved'] != preserved or row['passed'] != passed:
            raise ValueError('Contador ou entrada inconsistente: ' + row['id'])
        if row.get('domain') != case['domain'] or row.get('pair_group') != case['pair_group']:
            raise ValueError('Família/grupo adulterado: ' + row['id'])
        checked.append({**row, 'variant': case.get('variant')})
    if ev.summarize(checked) != report['summary']:
        raise ValueError('Resumo de avaliação não reproduzível.')
    return checked


class CognitiveCoreRegistry:
    def __init__(self, catalog_path=CATALOG, root=ROOT):
        self.root = Path(root).resolve()
        self.catalog_path = Path(catalog_path)
        catalog_bytes = self.catalog_path.read_bytes()
        self.catalog = json.loads(catalog_bytes)
        self._loaded_catalog_sha256 = hashlib.sha256(catalog_bytes).hexdigest()
        if self.catalog.get('schema') != 'cognitive-core-catalog/v1':
            raise ValueError('Catálogo de núcleos incompatível.')
        cores = self.catalog.get('cores', [])
        self.cores = {spec['id']: spec for spec in cores}
        if len(cores) != len(self.cores) or not cores:
            raise ValueError('Núcleos ausentes ou duplicados.')
        for spec in cores:
            if spec['metric'] not in ('full', 'contract', 'decision-evidence'):
                raise ValueError('Métrica de núcleo desconhecida.')
            if not spec['scope'] or not spec['input_contract'] or not spec['output_contract']:
                raise ValueError('Núcleo sem contrato/escopo.')
            for dependency in spec['dependencies']:
                if dependency not in self.cores:
                    raise ValueError('Dependência de núcleo desconhecida.')
        self.ordered(self.cores)  # Also detects cycles.
        for ids in list(self.catalog['skill_cores'].values()) + list(self.catalog['domain_cores'].values()):
            self.ordered(ids)
        self._cached_evidence = {}

    def ordered(self, ids):
        if not isinstance(ids, (list, tuple, set, dict)) or not ids:
            raise ValueError('Informe ao menos um núcleo.')
        result, visiting = [], set()
        def visit(id):
            if id not in self.cores:
                raise ValueError('Núcleo desconhecido: ' + str(id))
            if id in visiting:
                raise ValueError('Dependências de núcleos formam um ciclo.')
            if id in result:
                return
            visiting.add(id)
            for dependency in self.cores[id]['dependencies']:
                visit(dependency)
            visiting.remove(id)
            result.append(id)
        for id in ids:
            visit(id)
        return result

    def verify_certificate(self, path):
        if file_hash(self.catalog_path) != self._loaded_catalog_sha256:
            raise ValueError('Catálogo mudou após carregar o registro; recrie o registro de núcleos.')
        certificate = json.loads(Path(path).read_text())
        if certificate.get('schema') != 'cognitive-core-evidence/v1' or certificate.get('catalog_sha256') != file_hash(self.catalog_path):
            raise ValueError('Contrato da prova de núcleo não corresponde ao atual.')
        checkpoint = local_path(certificate['checkpoint'], self.root)
        for key, value in evaluator().checkpoint_identity(checkpoint).items():
            if certificate.get(key) != value:
                raise ValueError('Pesos/metadados/tokenizer do núcleo mudaram.')
        if set(certificate['source_sha256']) != set(PROOF_SOURCES):
            raise ValueError('A prova não vincula todas as implementações necessárias.')
        for name, digest in certificate['source_sha256'].items():
            if file_hash(local_path(name, self.root)) != digest:
                raise ValueError('Implementação da prova/núcleo mudou: ' + name)
        protocol_path = local_path(certificate['protocol'], self.root)
        if file_hash(protocol_path) != certificate['protocol_sha256']:
            raise ValueError('Protocolo da prova mudou.')
        protocol = json.loads(protocol_path.read_text())
        if (protocol.get('schema') != 'cognitive-core-protocol/v1'
                or protocol.get('checkpoint_sha256') != certificate['checkpoint_sha256']
                or protocol.get('catalog_sha256') != certificate['catalog_sha256']
                or protocol.get('used_for_training') is not False
                or protocol.get('used_for_selection') is not False):
            raise ValueError('Protocolo não fixa candidato e critérios antes da prova.')
        if (set(protocol['cases']) != {'reserved', 'regression'}
                or set(protocol['cases_sha256']) != {'reserved', 'regression'}):
            raise ValueError('Protocolo exige reservado e regressão separados.')
        manifest_path = local_path(protocol['training_manifest'], self.root)
        selection_path = local_path(protocol['selection_report'], self.root)
        if (file_hash(manifest_path) != protocol['training_manifest_sha256']
                or file_hash(selection_path) != protocol['selection_report_sha256']):
            raise ValueError('Treino ou seleção anterior da prova mudou.')
        manifest = json.loads(manifest_path.read_text())
        selection = json.loads(selection_path.read_text())
        if selection['checkpoint_sha256'] != certificate['checkpoint_sha256']:
            raise ValueError('Candidato da prova diverge da seleção anterior.')
        excluded = set()
        for name, digest in manifest['inputs'].items():
            training_path = local_path(name, self.root)
            if file_hash(training_path) != digest:
                raise ValueError('Dados de treino/seleção anteriores mudaram.')
            if training_path.name in ('train.jsonl', 'validation.jsonl', 'heldout.jsonl'):
                excluded.update(row['entity'] for row in evaluator().read_cases(training_path))
        suites = {}
        for suite in certificate['suites']:
            cases = local_path(suite['cases'], self.root)
            report_path = local_path(suite['report'], self.root)
            if file_hash(cases) != suite['cases_sha256'] or file_hash(report_path) != suite['report_sha256']:
                raise ValueError('Dados/relatório de núcleo mudaram.')
            if (protocol['cases_sha256'].get(suite['name']) != suite['cases_sha256']
                    or local_path(protocol['cases'][suite['name']], self.root) != cases
                    or suite['name'] in suites):
                raise ValueError('Bancada não corresponde ao protocolo.')
            if suite['name'] == 'reserved' and excluded & {r['entity'] for r in evaluator().read_cases(cases)}:
                raise ValueError('Entidades reservadas vazaram da rodada anterior.')
            suites[suite['name']] = regrade_report(json.loads(report_path.read_text()), cases, checkpoint, self.root)
        if set(suites) != {'reserved', 'regression'}:
            raise ValueError('A prova exige reservado e regressão separados.')
        reserved_cases = evaluator().read_cases(local_path(protocol['cases']['reserved'], self.root))
        regression_cases = evaluator().read_cases(local_path(protocol['cases']['regression'], self.root))
        if ({r['entity'] for r in reserved_cases} & {r['entity'] for r in regression_cases}
                or {r['id'] for r in reserved_cases} & {r['id'] for r in regression_cases}):
            raise ValueError('Reservado e regressão não são bancadas independentes.')
        states = {}
        for id, spec in self.cores.items():
            measurements = {name: core_summary(spec, rows) for name, rows in suites.items()}
            assessed = bool(spec['domains']) and all(measurements.values())
            passed = assessed and all(qualifies(spec, s) for s in measurements.values())
            states[id] = {'status': 'passed' if passed else 'failed' if assessed else 'not_evaluated',
                          'scope': spec['scope'], 'measurements': measurements}
        if certificate['cores'] != states:
            raise ValueError('A competência declarada diverge dos resultados.')
        return certificate

    def _evidence(self):
        try:
            if file_hash(self.catalog_path) != self._loaded_catalog_sha256:
                return [], ['Catálogo mudou após carregar o registro; recrie o registro de núcleos.']
        except OSError as error:
            return [], [str(error)[:300]]
        index = local_path(self.catalog['evidence_registry'], self.root)
        if not index.is_file():
            return [], []
        candidates, errors = [], []
        try:
            payload = json.loads(index.read_text())
            if (not isinstance(payload, dict) or payload.get('schema') != 'cognitive-core-registry/v1'
                    or not isinstance(payload.get('certificates'), list)):
                raise ValueError('Índice de provas incompatível.')
            for entry in payload['certificates']:
                try:
                    path = local_path(entry['path'], self.root)
                    if file_hash(path) != entry['sha256']:
                        raise ValueError('Hash do certificado não corresponde ao índice.')
                    # Cache only after full regrading; ctime invalidates content edits even with preserved mtime.
                    cert = json.loads(path.read_text())
                    dependencies = [path, self.catalog_path, local_path(cert['checkpoint'], self.root),
                                    local_path(cert['checkpoint'] + '.json', self.root),
                                    local_path(cert['protocol'], self.root)]
                    protocol = json.loads(dependencies[4].read_text())
                    dependencies += [local_path(name, self.root) for name in protocol['cases'].values()]
                    manifest_path = local_path(protocol['training_manifest'], self.root)
                    dependencies += [manifest_path, local_path(protocol['selection_report'], self.root)]
                    dependencies += [local_path(name, self.root) for name in json.loads(manifest_path.read_text())['inputs']]
                    metadata = json.loads(dependencies[3].read_text())
                    dependencies.append(local_path(metadata['config']['tokenizer_path'], self.root))
                    dependencies += [local_path(name, self.root) for name in cert['source_sha256']]
                    dependencies += [local_path(s[field], self.root) for s in cert['suites'] for field in ['cases', 'report']]
                    key = tuple((str(p),p.stat().st_ino,p.stat().st_size,p.stat().st_mtime_ns,p.stat().st_ctime_ns) for p in dependencies)
                    if key not in self._cached_evidence:
                        self._cached_evidence = {key: self.verify_certificate(path)}
                    candidates.append(self._cached_evidence[key])
                except (OSError, ValueError, KeyError, TypeError) as error:
                    errors.append(str(error)[:300])
        except (OSError, ValueError, KeyError, TypeError) as error:
            errors.append(str(error)[:300])
        return candidates, errors

    def snapshot(self, checkpoint=None):
        candidates, errors = self._evidence()
        selected = []
        if checkpoint:
            wanted = Path(checkpoint)
            wanted = wanted.resolve() if wanted.is_absolute() else (self.root / wanted).resolve()
            selected = [c for c in candidates if local_path(c['checkpoint'],self.root) == wanted]
        # More than one certificate for a candidate is ambiguous; never choose the best result.
        certificate = selected[0] if len(selected) == 1 else None
        if len(selected) > 1:
            errors.append('Mais de uma prova para o mesmo checkpoint; seleção ambígua.')
        states = certificate['cores'] if certificate else {}
        cores = []
        for id,spec in self.cores.items():
            state = states.get(id, {'status':'not_evaluated','scope':spec['scope'],'measurements':None})
            dependencies = self.ordered([id])
            enabled = all(states.get(name,{}).get('status') == 'passed' for name in dependencies)
            cores.append({**spec,**state,'dispatch_enabled':enabled,'checkpoint':certificate['checkpoint'] if certificate else None})
        return {'schema':'cognitive-core-status/v1','checkpoint':str(checkpoint) if checkpoint else None,
                'cores':cores,'evidence_errors':errors,'evaluated_candidates':[{'checkpoint':c['checkpoint'],
                'checkpoint_sha256':c['checkpoint_sha256'],'cores':c['cores']} for c in candidates],
                'all_competent':all(c['dispatch_enabled'] for c in cores),
                'policy':'Competência limitada ao escopo da prova; aprovação não autoriza ferramentas nem promove o chat.'}

    def plan(self, skill_ids=(), domain='general', checkpoint=None):
        requested=[]
        missing=[]
        for skill in skill_ids:
            if skill not in self.catalog['skill_cores']:
                missing.append(skill)
                continue
            requested.extend(self.catalog['skill_cores'][skill])
        if not requested:
            requested.extend(self.catalog['domain_cores'].get(domain, self.catalog['domain_cores']['general']))
        ids=self.ordered(list(dict.fromkeys(requested)))
        snapshot=self.snapshot(checkpoint)
        states={s['id']:s for s in snapshot['cores']}
        return {'schema':'cognitive-core-plan/v1','requested_cores':list(dict.fromkeys(requested)),
                'stages':[{'core':id,'input_contract':self.cores[id]['input_contract'],
                          'output_contract':self.cores[id]['output_contract'],
                          'status':states[id]['status'],'dispatch_enabled':states[id]['dispatch_enabled']} for id in ids],
                'competent_for_composition':not missing and all(states[id]['dispatch_enabled'] for id in ids),
                'missing_skill_contracts':missing,
                'execution_allowed':False,'evidence_errors':snapshot['evidence_errors']}

    def require(self, ids, checkpoint, scope):
        ordered=self.ordered(ids)
        snapshot=self.snapshot(checkpoint)
        states={s['id']:s for s in snapshot['cores']}
        if any(self.cores[id]['scope'] != scope for id in ordered):
            raise ValueError('O pedido está fora do escopo certificado dos núcleos.')
        blocked=[id for id in ordered if not states[id]['dispatch_enabled']]
        if blocked:
            raise ValueError('Competência não comprovada: ' + ', '.join(blocked))
        return ordered
