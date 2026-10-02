import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from contextlib import redirect_stdout
from io import StringIO

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'python'))
sys.path.insert(0,str(ROOT/'scripts'))
from conditioned_context import generation_window, training_windows, window_geometry
from interface_quality import evaluate_interface, balanced_css
from prepare_programming_studio import interface_row, function_row, FUNCTIONS, VALIDATION, HELDOUT
from select_programming_studio import main as select_main


class ProgrammingStudioTests(unittest.TestCase):
    def test_selection_uses_behavior_and_keeps_heldout_out_of_checkpoint_choice(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);run=root/'run';snapshots=run/'snapshots';snapshots.mkdir(parents=True)
            for name,loss in [('a',.01),('b',1.0)]:
                (snapshots/f'{name}.safetensors').write_bytes(b'fixture weights')
                (snapshots/f'{name}.safetensors.json').write_text(json.dumps({'validation_loss':loss}))
            def cases(path,prefix):
                path.write_text(''.join(json.dumps({'id':prefix+str(i),'domain':kind,
                    'messages':[{'content':prefix+str(i)}]})+'\n' for i,kind in enumerate(['function','function','interface'])))
            cases(run/'train.jsonl','train');cases(root/'validation.jsonl','val');cases(root/'heldout.jsonl','held')
            calls=[]
            def generated(checkpoint,records):
                calls.append((Path(checkpoint).stem,records[0]['id']))
                passed=Path(checkpoint).stem=='b' and records[0]['id'].startswith('val')
                return [dict(record,passed=passed) for record in records]
            argv=['select','--run-dir',str(run),'--validation',str(root/'validation.jsonl'),
                  '--heldout',str(root/'heldout.jsonl')]
            with patch('sys.argv',argv),patch('select_programming_studio.evaluate',generated),redirect_stdout(StringIO()):
                exit_code=select_main()
            report=json.loads((run/'functional-selection.json').read_text())
            self.assertEqual(calls,[('a','val0'),('b','val0'),('b','held0')])
            self.assertTrue(report['selected_checkpoint'].endswith('b.safetensors'))
            self.assertEqual(exit_code,1)
            self.assertFalse(report['qualified_for_review'])
            self.assertFalse(report['promoted'])

    def test_generation_rebuild_keeps_request_and_bounded_completion_history(self):
        prompt=list(range(80))
        answer=list(range(100,500))
        context=generation_window(prompt,answer,128)
        self.assertLessEqual(len(context),128)
        self.assertEqual(context[:28],prompt[:28])
        self.assertEqual(context[-1],answer[-1])
        windows=list(training_windows(prompt,answer,128,0))
        self.assertEqual([token for _,labels in windows for token in labels if token!=-100],answer)
        for inputs,_ in windows: self.assertEqual(inputs[:28],prompt[:28])

    def test_decoder_positions_match_training_chunks_at_every_next_token(self):
        prompt=list(range(80)); answer=list(range(100,500)); context=128
        anchor,stride,history=window_geometry(prompt,context)
        for completed in range(len(answer)):
            offset=completed//stride*stride
            expected=anchor+answer[max(0,offset-history):completed]
            self.assertEqual(generation_window(prompt,answer[:completed],context),expected)
            self.assertLess(len(expected),context)

    def test_interface_reference_executes_interactions_but_does_not_claim_visual_quality(self):
        record=interface_row('editorial','Leitura',0)
        result=evaluate_interface(json.loads(record['messages'][-1]['content']),record['request'])
        self.assertTrue(result['passed'],result)
        self.assertEqual(len(result['behavior']['tests']),7)
        self.assertFalse(result['rendered_in_browser'])
        self.assertFalse(result['visual_quality_confirmed'])

    def test_inert_pretty_interface_fails_behavior_gate(self):
        record=interface_row('split','Leitura',0)
        plan=json.loads(record['messages'][-1]['content'])
        html=plan['operations'][0]['arguments']['content']
        start,end=html.index('<script>'),html.index('</script>')
        plan['operations'][0]['arguments']['content']=html[:start]+'<script>const unused=1;'+html[end:]
        result=evaluate_interface(plan)
        self.assertFalse(result['passed'])
        self.assertTrue(result['checks']['responsive_css'])

    def test_working_ui_cannot_ignore_title_or_visual_brief(self):
        editorial=interface_row('editorial','Leitura',0)
        wrong=interface_row('terminal','Outro nome',0)
        result=evaluate_interface(json.loads(wrong['messages'][-1]['content']),editorial['request'])
        self.assertFalse(result['passed'])
        self.assertFalse(result['checks']['requested_title'])
        self.assertFalse(result['checks']['requested_direction'])

    def test_broken_css_is_rejected_even_with_working_buttons(self):
        row=interface_row('split','Leitura',0);plan=json.loads(row['messages'][-1]['content'])
        plan['operations'][0]['arguments']['content']=plan['operations'][0]['arguments']['content'].replace(
            '<style>','<style>stray:4px}')
        result=evaluate_interface(plan,row['request'])
        self.assertFalse(result['checks']['css_delimiters'])
        self.assertFalse(result['passed'])
        self.assertTrue(balanced_css('/* } */ a{content:"}";color:var(--color)}'))
        self.assertFalse(balanced_css('a{color:var(--color}'))

    def test_visual_directions_have_structural_differences(self):
        results={name:evaluate_interface(json.loads(interface_row(name,'Leitura',0)['messages'][-1]['content']))
                 for name in ['editorial','terminal','split','paper']}
        fingerprints={name:r['css_fingerprint'] for name,r in results.items()}
        self.assertEqual(len({json.dumps(value,sort_keys=True) for value in fingerprints.values()}),4)
        self.assertTrue(fingerprints['split']['grid'])
        self.assertTrue(fingerprints['terminal']['monospace'])
        self.assertTrue(fingerprints['paper']['lined_paper'])

    def test_reserved_function_names_are_disjoint_from_training(self):
        names={case[0] for case in FUNCTIONS}
        self.assertFalse(names & {case[0] for case in VALIDATION+HELDOUT})
        record=function_row(FUNCTIONS[13])
        plan=json.loads(record['messages'][-1]['content'])
        compile(plan['operations'][1]['arguments']['content'],'test_app.py','exec')


if __name__=='__main__':unittest.main()
