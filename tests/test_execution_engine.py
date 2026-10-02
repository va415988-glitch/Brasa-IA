import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'python'))
from execution_engine import run_engine
from execution_routing import engine_request
from model_server import ModelService


class ExecutionEngineTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory();self.root=Path(self.temporary.name)
    def tearDown(self):self.temporary.cleanup()
    def function(self,source,name,args=None,kwargs=None):
        (self.root/'logic.py').write_text(source)
        return run_engine('evaluate_function',{'path':'logic.py','function':name,'args':args or [],'kwargs':kwargs or {}},self.root)
    def test_new_expressions_and_json_variables_are_calculated(self):
        result=run_engine('calculate',{'expression':'(income-cost)/len(items)','variables':{'income':1937,'cost':221,'items':[1,2,3,4]}})
        self.assertTrue(result['passed'],result);self.assertEqual(result['result'],429)
    def test_generators_preserve_short_circuit_and_comprehension_scope(self):
        result=run_engine('calculate',{'expression':'any(x == 1 or 1/0 for x in [1, 0])'})
        self.assertTrue(result['passed'],result);self.assertIs(result['result'],True)
        result=run_engine('calculate',{'expression':'sum(x*x for x in range(11))'})
        self.assertEqual(result['result'],385)
    def test_large_integer_is_encoded_exactly_for_javascript_clients(self):
        result=run_engine('calculate',{'expression':'2**200 + 17'})
        self.assertEqual(result['result'],{'integer_decimal':str(2**200+17)})
    def test_pure_function_behaviors_match_cpython_on_new_inputs(self):
        source='''def merge_segments(rows):
    merged=[]
    for start,end in sorted(rows):
        if merged and start <= merged[-1][1]:
            merged[-1][1]=max(merged[-1][1],end)
        else:
            merged.append([start,end])
    return merged
def fact(n):
    if n<=1:return 1
    return n*fact(n-1)
def selected(values,minimum=3):
    return [x*2 for x in values if x>=minimum]
def receiver_order(items):
    items.pop(0).append(items.pop())
    return items
def alias_update(items):
    alias=items
    items += [3]
    return alias
def closure_value():
    value=2
    chosen=lambda: value
    value=9
    return chosen()
def generator_order(items):
    generated=(x for x in items.pop(0))
    items.append([9])
    return [list(generated),items]
'''
        # This is a trusted test fixture, not code submitted to the engine.
        namespace={};exec(source,namespace)
        cases=[('merge_segments',[[[9,12],[-2,3],[2,7],[7,10]]],{}),
               ('fact',[7],{}),('selected',[[1,5,7]],{'minimum':5}),
               ('receiver_order',[[[1],[2],3]],{}),('alias_update',[[1,2]],{}),
               ('closure_value',[],{}),('generator_order',[[[1,2]]],{})]
        for name,args,kwargs in cases:
            with self.subTest(name=name):
                expected=namespace[name](*json.loads(json.dumps(args)),**kwargs)
                observed=self.function(source,name,args,kwargs)
                self.assertTrue(observed['passed'],observed)
                self.assertEqual(observed['result'],expected)
                self.assertEqual(observed['request']['args'],args)
                self.assertFalse(observed['top_level_executed'])
                self.assertEqual(len(observed['source_sha256']),64)
    def test_module_imports_and_top_level_effects_are_never_executed(self):
        marker=self.root/'marker'
        source=f'from pathlib import Path\nPath({str(marker)!r}).write_text("unsafe")\ndef square(x):return x*x\n'
        result=self.function(source,'square',[19])
        self.assertEqual(result['result'],361);self.assertFalse(marker.exists())
    def test_host_access_and_unsupported_python_fail_explicitly(self):
        for expression in ["__import__('os').getcwd()","open('secret')","(1).__class__",'lambda x: x']:
            with self.subTest(expression=expression):
                result=run_engine('calculate',{'expression':expression})
                self.assertFalse(result['passed']);self.assertIsNone(result['result'])
        result=self.function('def f():\n    import os\n    return os.getcwd()\n','f')
        self.assertFalse(result['passed']);self.assertIn('Import',result['error']['message'])
    def test_repetition_recursion_and_expansion_are_bounded(self):
        result=self.function('def spin():\n    while True:pass\n','spin')
        self.assertEqual(result['error']['type'],'LimitExceeded')
        result=self.function('def recur():return recur()\n','recur')
        self.assertEqual(result['error']['type'],'LimitExceeded')
        for expression in ["'a'*1000000000",'2**1000000','list(range(1000000000))',
                           "'a'.replace('', 'b'*8192)"]:
            with self.subTest(expression=expression):
                result=run_engine('calculate',{'expression':expression})
                self.assertEqual(result['error']['type'],'LimitExceeded')
        result=run_engine('calculate',{'expression':"'%1000000000s' % 'x'"})
        self.assertEqual(result['error']['type'],'UnsupportedSyntax')
        result=self.function('def expand():\n    value=[0]*1000\n    for i in range(8):value=[value]*1000\n    return value\n','expand')
        self.assertEqual(result['error']['type'],'LimitExceeded')
    def test_traversal_symlinks_and_missing_symbols_are_rejected(self):
        (self.root/'outside.py').write_text('def f():return 7')
        (self.root/'linked.py').symlink_to(self.root/'outside.py')
        for path in ('../outside.py',str(self.root/'outside.py'),'linked.py'):
            result=run_engine('evaluate_function',{'path':path,'function':'f'},self.root)
            self.assertFalse(result['passed'],result)
    def test_routing_handles_composition_and_literal_function_calls(self):
        self.assertEqual(engine_request('Calcule (1275 * 3 + 18) / 7')[0],'calculate')
        self.assertEqual(engine_request('Calcule 15% de 320')[1]['expression'],'(15 / 100) * 320')
        self.assertEqual(engine_request('Execute selected([1, 4, 9], minimum=5) em logic.py')[1],
                         {'path':'logic.py','function':'selected','args':[[1,4,9]],'kwargs':{'minimum':5}})
        self.assertIsNone(engine_request('Execute selected(__import__("os")) em logic.py'))
        self.assertIsNone(engine_request('Quanto é caro morar aqui?'))
        self.assertEqual(engine_request("Calcule len('a?b')?")[1]['expression'],"len('a?b')")
    def test_chat_computes_without_neural_generation_or_fixed_answers(self):
        service=ModelService('missing-checkpoint',trace_path=None)
        with patch.object(service,'local_reply',side_effect=AssertionError('must use execution engine')):
            response=service.reply([{'role':'user','content':'Calcule (1275 * 3 + 18) / 7'}],objective='conversation')
        self.assertEqual(response['backend'],'execution-engine')
        self.assertIn('549',response['text']);self.assertTrue(response['execution_engine']['passed'])
        self.assertNotIn('stop_reason',response['agent'])
    def test_function_tool_result_is_reused_instead_of_reexecuted(self):
        service=ModelService('missing-checkpoint',trace_path=None)
        question='Execute selected([2, 5, 9]) em logic.py'
        first=service.reply([{'role':'user','content':question}],objective='conversation')
        self.assertEqual(first['tool_call']['tool'],'evaluate_function')
        result=self.function('def selected(values):return [x for x in values if x>4]\n','selected',[[2,5,9]])
        messages=[{'role':'user','content':question},{'role':'tool','content':json.dumps({'tool':'evaluate_function','ok':True,'data':result})}]
        response=service.reply(messages,objective='conversation')
        self.assertFalse(response.get('tool_call'));self.assertIn('[5, 9]',response['text'])
    def test_invalid_tool_evidence_and_read_errors_do_not_claim_success(self):
        service=ModelService('missing-checkpoint',trace_path=None)
        question='Execute selected([2, 5, 9]) em logic.py'
        for observed in ({'tool':'evaluate_function','ok':False,'error':'arquivo ausente'},
                         {'tool':'evaluate_function','ok':True,'data':{
                             'passed':True,'request':engine_request(question)[1],'result':[5,9]}}):
            response=service.reply([{'role':'user','content':question},
                {'role':'tool','content':json.dumps(observed)}],objective='conversation')
            self.assertFalse(response.get('tool_call'));self.assertEqual(response['agent']['status'],'blocked')
        failed=service.reply([{'role':'user','content':'Calcule 8 / 0'}],objective='conversation')
        self.assertEqual(failed['agent']['status'],'blocked')
        self.assertEqual(failed['agent']['stop_reason'],'execution_engine_evaluation_failed')
        self.assertIs(failed['agent']['retryable'],False)
        missing=service.reply([{'role':'user','content':'Calcule unknown_total'}],objective='conversation')
        self.assertEqual(missing['agent']['status'],'blocked')
        self.assertIn('unknown_total',missing['text'])


if __name__=='__main__':unittest.main()
