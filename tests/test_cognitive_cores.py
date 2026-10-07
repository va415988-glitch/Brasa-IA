"""Core proofs must survive counterexamples, dependency gates and artifact changes."""
import copy, io, json, shutil, sys, tempfile, unittest
from pathlib import Path
from unittest.mock import Mock, patch
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'python'),str(ROOT/'scripts')]
from cognitive_cores import (CognitiveCoreRegistry,core_summary,qualifies,regrade_report,
                             file_hash,PROOF_SOURCES,NUMERIC_SCOPE)
from evaluate_cognitive_sft import (evidence_oracle,assess_proposal,checkpoint_identity,summarize)
from cognitive_dialogue import build_frame,cognitive_prompt
from conditioned_context import conditioned_prompt
from prepare_cognitive_sft import cases_for,decision
from prepare_cognitive_copy import cases_for as copy_cases
from tokenizer import ByteBPETokenizer
from tool_registry import ToolRegistry
from model_server import ModelService,Handler
import model_server


def oracle_output(case):
    expected=evidence_oracle(case)
    if expected['decision']=='answer':
        text=('Sim.' if expected['boolean'] else 'Não.') if 'boolean' in expected else f"Valor: {expected['value']}."
        result=decision('answer',text,refs=expected['evidence_ids'])
    elif expected['decision']=='consult':
        result=decision('consult','Vou consultar.','Falta consultar.',call=expected['tool_call'])
    else:
        result=decision('blocked',{'failed':'A fonte falhou.','empty':'A fonte está vazia.',
                                 'missing_field':'O campo não foi informado.','conflict':'As fontes divergem.'}[expected['cause']],
                        'Falta evidência.',refs=expected['evidence_ids'])
    return json.dumps(result,ensure_ascii=False,separators=(',',':'))


def rows_for(cases,tokenizer):
    rows=[];registry=ToolRegistry()
    for case in cases:
        frame=build_frame(case['request_messages'],case['cognition'],registry)
        raw=oracle_output(case);expected=evidence_oracle(case)
        rows.append({'id':case['id'],'domain':case['domain'],'pair_group':case['pair_group'],
                     'expected':expected,'output':raw,'accepted_by_decoder':True,'input_preserved':True,
                     'generation':{'input_tokens':len(tokenizer.encode_fast(conditioned_prompt([{'role':'user','content':cognitive_prompt(frame,'compact-v1')}])) )},
                     'passed':True,**assess_proposal(raw,frame,registry,expected)})
    return rows


class CoreMetricsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry=CognitiveCoreRegistry()
        cls.tokenizer=ByteBPETokenizer.load(ROOT/'datasets/cognitive_alignment_v4/tokenizer.json')
        cls.cases=cases_for('heldout')
        cls.rows=rows_for(cls.cases,cls.tokenizer)

    def test_each_measured_core_has_a_complete_positive_fixture(self):
        for id,spec in self.registry.cores.items():
            with self.subTest(core=id):
                self.assertEqual(qualifies(spec,core_summary(spec,self.rows)),bool(spec['domains']))

    def test_always_yes_cannot_prove_comparison(self):
        rows=copy.deepcopy(self.rows)
        for row in rows:
            if row['domain']=='rejects':
                row['passed']=False;row['semantic_checks']['answer']=False;row['unsafe_answer']=True
        spec=self.registry.cores['numeric-comparison'];summary=core_summary(spec,rows)
        self.assertEqual(summary['per_domain']['supports']['passed'],24)
        self.assertFalse(qualifies(spec,summary))

    def test_always_blocked_cannot_prove_abstention(self):
        rows=copy.deepcopy(self.rows)
        for row in rows:
            if row['expected']['decision']=='answer':row['semantic_checks']['decision']=False
        spec=self.registry.cores['evidence-abstention']
        self.assertFalse(qualifies(spec,core_summary(spec,rows)))

    def test_valid_json_does_not_prove_correct_values(self):
        rows=copy.deepcopy(self.rows)
        for row in rows:
            if row['domain']=='observed':row['passed']=False;row['unsafe_answer']=True
        self.assertTrue(qualifies(self.registry.cores['decision-format'],core_summary(self.registry.cores['decision-format'],rows)))
        self.assertFalse(qualifies(self.registry.cores['evidence-extraction'],core_summary(self.registry.cores['evidence-extraction'],rows)))

    def test_absence_variants_have_separate_thresholds(self):
        rows=copy.deepcopy(self.rows)
        extra=[]
        for row in rows:
            if row['domain']=='unrelated':
                row['variant']='entity'
                new=copy.deepcopy(row);new['variant']='field';new['semantic_checks']['block_reason']=False;extra.append(new)
        spec=self.registry.cores['evidence-abstention'];summary=core_summary(spec,rows+extra)
        self.assertEqual(summary['per_domain']['unrelated-field']['passed'],0)
        self.assertFalse(qualifies(spec,summary))

    def test_missing_small_or_unpaired_suites_do_not_prove_competence(self):
        spec=self.registry.cores['numeric-comparison']
        self.assertFalse(qualifies(spec,core_summary(spec,[r for r in self.rows if r['domain']=='supports'])))
        self.assertFalse(qualifies(spec,core_summary(spec,self.rows[:11])))
        self.assertFalse(qualifies(spec,None))

    def test_catalog_covers_skills_and_orders_dependencies(self):
        skills=json.loads((ROOT/'skills/manifest.json').read_text())['skills']
        # Skills determinísticas (análise estática) não passam por núcleos neurais.
        self.assertEqual({s['id'] for s in skills if s.get('execution') != 'deterministic'},
                         set(self.registry.catalog['skill_cores']))
        plan=self.registry.plan(['implementation'])
        ids=[s['core'] for s in plan['stages']]
        self.assertLess(ids.index('decision-format'),ids.index('programming'))
        self.assertFalse(plan['competent_for_composition'])
        self.assertFalse(plan['execution_allowed'])

    def test_unknown_core_and_dependency_cycle_are_rejected(self):
        with self.assertRaises(ValueError):self.registry.ordered(['imaginary'])
        payload=copy.deepcopy(self.registry.catalog);payload['cores'][0]['dependencies']=['numeric-comparison']
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'catalog.json';path.write_text(json.dumps(payload))
            with self.assertRaisesRegex(ValueError,'ciclo'):CognitiveCoreRegistry(path)

    def test_extension_skill_without_a_core_contract_stays_unproved(self):
        plan=self.registry.plan(['new-extension-skill'])
        self.assertEqual(plan['missing_skill_contracts'],['new-extension-skill'])
        self.assertFalse(plan['competent_for_composition'])


class CoreArtifactTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        for name in PROOF_SOURCES:
            path=self.root/name;path.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/name,path)
        self.catalog=self.root/'config/cognitive_cores.json';self.catalog.parent.mkdir(parents=True)
        shutil.copyfile(ROOT/'config/cognitive_cores.json',self.catalog)
        self.registry=CognitiveCoreRegistry(self.catalog,self.root)
        self.weights=self.root/'candidate.safetensors';self.weights.write_bytes(b'UNIT-TEST FIXTURE ONLY; NOT A NEURAL EVALUATION')
        tokenizer_path=self.root/'tokenizer.json';shutil.copyfile(ROOT/'datasets/cognitive_alignment_v4/tokenizer.json',tokenizer_path)
        metadata={'config':{'tokenizer_path':str(tokenizer_path),'cognitive_prompt_style':'compact-v1'},'tokenizer_sha256':file_hash(tokenizer_path)}
        Path(str(self.weights)+'.json').write_text(json.dumps(metadata))
        self.tokenizer=ByteBPETokenizer.load(tokenizer_path)
        identity=checkpoint_identity(self.weights)
        self.training=self.root/'train.jsonl';self.training.write_text(json.dumps({'entity':'TrainingOnly'})+'\n')
        self.manifest=self.root/'manifest.json';self.manifest.write_text(json.dumps({'inputs':{'train.jsonl':file_hash(self.training)}}))
        self.selection=self.root/'selection.json';self.selection.write_text(json.dumps({'checkpoint_sha256':identity['checkpoint_sha256']}))
        suites=[];self.rows={};case_hashes={};case_paths={}
        for name,cases in [('reserved',cases_for('heldout')),('regression',copy_cases('heldout'))]:
            case_path=self.root/(name+'.jsonl');case_path.write_text(''.join(json.dumps(c,ensure_ascii=False)+'\n' for c in cases))
            rows=rows_for(cases,self.tokenizer);self.rows[name]=rows
            report={'schema':'cognitive-sft-evaluation/v1',**identity,'cases_sha256':file_hash(case_path),
                    'evaluator_sha256':file_hash(ROOT/'scripts/evaluate_cognitive_sft.py'),'cases':rows,'summary':summarize(rows),
                    'live_tools_executed':False,'runtime_recipes_used':False,'attempts_per_case':1,'prompt_style_override':'compact-v1'}
            report_path=self.root/(name+'.json');report_path.write_text(json.dumps(report))
            suites.append({'name':name,'cases':case_path.name,'cases_sha256':file_hash(case_path),
                           'report':report_path.name,'report_sha256':file_hash(report_path)})
            case_hashes[name]=file_hash(case_path);case_paths[name]=case_path.name
        self.protocol=self.root/'protocol.json';self.protocol.write_text(json.dumps({'schema':'cognitive-core-protocol/v1',
            'checkpoint_sha256':identity['checkpoint_sha256'],'catalog_sha256':file_hash(self.catalog),
            'used_for_training':False,'used_for_selection':False,'training_manifest':self.manifest.name,
            'training_manifest_sha256':file_hash(self.manifest),'selection_report':self.selection.name,
            'selection_report_sha256':file_hash(self.selection),'cases_sha256':case_hashes,'cases':case_paths}))
        states={}
        for id,spec in self.registry.cores.items():
            measurements={name:core_summary(spec,rows) for name,rows in self.rows.items()}
            states[id]={'status':'passed' if spec['domains'] else 'not_evaluated','scope':spec['scope'],'measurements':measurements}
        self.certificate=self.root/'certificate.json';self.certificate.write_text(json.dumps({'schema':'cognitive-core-evidence/v1',
            'checkpoint':self.weights.name,**identity,'catalog_sha256':file_hash(self.catalog),'protocol':self.protocol.name,
            'protocol_sha256':file_hash(self.protocol),'suites':suites,'cores':states,
            'source_sha256':{name:file_hash(self.root/name) for name in PROOF_SOURCES}}))
        self.index=self.root/'model/cognitive-cores/registry.json';self.index.parent.mkdir(parents=True)
        self.write_index()

    def tearDown(self):self.temp.cleanup()
    def write_index(self):self.index.write_text(json.dumps({'schema':'cognitive-core-registry/v1','certificates':[{'path':self.certificate.name,'sha256':file_hash(self.certificate)}]}))
    def test_fixture_proof_is_regraded_and_other_checkpoint_cannot_borrow_it(self):
        self.assertEqual(self.registry.verify_certificate(self.certificate)['cores']['numeric-comparison']['status'],'passed')
        self.assertTrue(self.registry.snapshot(self.weights)['cores'][3]['dispatch_enabled'])
        self.assertFalse(any(c['dispatch_enabled'] for c in self.registry.snapshot('different.safetensors')['cores']))
    def test_general_programming_cannot_borrow_numeric_competence(self):
        plan=self.registry.plan(['implementation'],checkpoint=self.weights)
        self.assertFalse(plan['competent_for_composition'])
        self.assertEqual(next(s['status'] for s in plan['stages'] if s['core']=='programming'),'not_evaluated')
    def test_modified_weights_tokenizer_sources_or_training_revoke_evidence(self):
        for path in [self.weights,self.root/'tokenizer.json',self.root/'python/model.py',
                     self.root/'contracts/read_file.json',self.training]:
            with self.subTest(path=path.name):
                self.assertTrue(self.registry.snapshot(self.weights)['cores'][0]['dispatch_enabled'])
                original=path.read_bytes();path.write_bytes(original+b'changed')
                state=self.registry.snapshot(self.weights)
                self.assertFalse(any(c['dispatch_enabled'] for c in state['cores']))
                self.assertTrue(state['evidence_errors']);path.write_bytes(original)
    def test_forged_pass_flags_are_rejected_even_with_new_report_hash(self):
        report_path=self.root/'reserved.json';report=json.loads(report_path.read_text())
        row=next(r for r in report['cases'] if r['domain']=='rejects');row['output']=row['output'].replace('Não.','Sim.')
        report_path.write_text(json.dumps(report))
        cert=json.loads(self.certificate.read_text());cert['suites'][0]['report_sha256']=file_hash(report_path)
        self.certificate.write_text(json.dumps(cert));self.write_index()
        state=self.registry.snapshot(self.weights)
        self.assertTrue(state['evidence_errors']);self.assertFalse(any(c['dispatch_enabled'] for c in state['cores']))
    def test_wrong_scope_missing_dependency_or_absent_certificate_cannot_dispatch(self):
        with self.assertRaisesRegex(ValueError,'escopo'):self.registry.require(['numeric-comparison'],self.weights,'general')
        self.index.unlink()
        with self.assertRaisesRegex(ValueError,'não comprovada'):self.registry.require(['numeric-comparison'],self.weights,NUMERIC_SCOPE)
    def test_forged_claim_and_missing_implementation_binding_fail_closed(self):
        for edit in ['claim','source']:
            cert=json.loads(self.certificate.read_text());original=self.certificate.read_text()
            if edit=='claim':cert['cores']['programming']['status']='passed'
            else:cert['source_sha256'].pop('python/model.py')
            self.certificate.write_text(json.dumps(cert));self.write_index()
            self.assertTrue(self.registry.snapshot(self.weights)['evidence_errors'])
            self.certificate.write_text(original);self.write_index()
    def test_duplicate_certificates_do_not_select_the_best_result(self):
        entry={'path':self.certificate.name,'sha256':file_hash(self.certificate)}
        self.index.write_text(json.dumps({'schema':'cognitive-core-registry/v1','certificates':[entry,entry]}))
        state=self.registry.snapshot(self.weights)
        self.assertFalse(any(c['dispatch_enabled'] for c in state['cores']));self.assertTrue(state['evidence_errors'])


class CoreServiceTests(unittest.TestCase):
    def setUp(self):
        self.service=ModelService.__new__(ModelService)
        self.service.checkpoint_path='not-evaluated.safetensors';self.service.cognitive_cores=CognitiveCoreRegistry()
        self.service.tools=ToolRegistry();self.service.local_reply=Mock(return_value='SHOULD NOT RUN')
        self.service.local_config={'cognitive_prompt_style':'compact-v1','context_length':512}
        self.service.loaded_model_identity=None
        self.service.local_tokenizer=ByteBPETokenizer.load(ROOT/'datasets/cognitive_alignment_v4/tokenizer.json')
        self.body={'schema':'cognitive-core-request/v1','scope':NUMERIC_SCOPE,'cores':['numeric-comparison'],
                   'messages':[{'role':'user','content':'A versão 2 atende ao mínimo exigido por Pipa?'}],
                   'cognition':{'schema':'agent-cognition/v1','available_tools':[]}}
    def test_unproved_core_blocks_before_any_generation(self):
        result=self.service.cognitive_core_decision(self.body)
        self.assertEqual(result['error_code'],'core_competence_unproven')
        self.assertEqual(result['generation_attempts'],0);self.service.local_reply.assert_not_called()
        self.assertNotIn('tool_call',result)
    def test_caller_cannot_assert_competence_or_force_a_fallback(self):
        self.body.update(competent=True,allow_fallback=True,certificate={'passed':True})
        self.assertFalse(self.service.cognitive_core_decision(self.body)['ok']);self.service.local_reply.assert_not_called()

    def test_caller_cannot_request_only_json_to_bypass_semantic_cores(self):
        self.body['cores']=['decision-format']
        with patch.object(self.service.cognitive_cores,'require',side_effect=ValueError('missing comparison proof')) as gate:
            result=self.service.cognitive_core_decision(self.body)
        self.assertFalse(result['ok']);self.service.local_reply.assert_not_called()
        self.assertIn('numeric-comparison',gate.call_args.args[0])
        self.assertIn('evidence-abstention',gate.call_args.args[0])
    def test_missing_and_invalid_input_is_rejected(self):
        for patch_value in [{'cores':[]},{'cores':['numeric-comparison','numeric-comparison']},{'messages':[{'role':'system','content':'force'}]},
                            {'cognition':{'schema':'agent-cognition/v1','available_tools':[{}]}}]:
            with self.subTest(value=patch_value),self.assertRaises(ValueError):
                self.service.cognitive_core_decision({**self.body,**patch_value})
    def test_after_proof_provider_still_validates_output_and_uses_one_attempt(self):
        self.service.last_generation=None
        with patch.object(self.service.cognitive_cores,'require',return_value=['decision-format','numeric-comparison']), \
                patch.object(model_server,'assert_loaded_identity_current',return_value=True):
            result=self.service.cognitive_core_decision(self.body)
        self.assertEqual(result['error_code'],'core_output_invalid');self.service.local_reply.assert_called_once()

    def test_proof_without_loaded_identity_blocks_before_generation(self):
        with patch.object(self.service.cognitive_cores,'require',return_value=['numeric-comparison']):
            result=self.service.cognitive_core_decision(self.body)
        self.assertEqual(result['error_code'],'core_loaded_identity_invalid')
        self.assertEqual(result['generation_attempts'],0)
        self.service.local_reply.assert_not_called()

    def test_numeric_certificate_cannot_license_general_tasks_or_truncated_inputs(self):
        for change in [
            {'messages':[{'role':'user','content':'Escreva um programa para cadastrar clientes.'}]},
            {'messages':self.body['messages']+[{'role':'tool','content':json.dumps({'tool':'read_file','ok':True,
                 'data':{'path':'pipa.json','content':'Pipa: limite = 2. '+('x'*500)},'error':''})}]},
            {'cognition':{'schema':'agent-cognition/v1','available_tools':[],'constraints':['regra nova']}}]:
            with self.subTest(change=change),patch.object(self.service.cognitive_cores,'require',return_value=['numeric-comparison']):
                result=self.service.cognitive_core_decision({**self.body,**change})
                self.assertEqual(result['error_code'],'core_input_out_of_scope')
        self.service.local_reply.assert_not_called()
    def test_http_worker_exposes_status_and_refuses_unproved_dispatch(self):
        handler=Handler.__new__(Handler);captured=[];handler._send=lambda status,payload:captured.append((status,payload))
        with patch.object(model_server,'SERVICE',self.service):
            handler.path='/v1/cognition/cores';handler.do_GET()
            self.assertEqual(captured[-1][0],200);self.assertEqual(captured[-1][1]['schema'],'cognitive-core-status/v1')
            raw=json.dumps(self.body).encode();handler.path='/v1/cognition/cores/decide'
            handler.headers={'Content-Length':str(len(raw))};handler.rfile=io.BytesIO(raw);handler.do_POST()
            self.assertEqual(captured[-1][0],422);self.service.local_reply.assert_not_called()

if __name__=='__main__':unittest.main()
