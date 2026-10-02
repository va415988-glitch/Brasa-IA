"""Measure generated files with independent function tests and counter UI probes."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'python'))
from model_server import ModelService
from proactive_implementation import parse_implementation_plan
from interface_quality import evaluate_interface


def evaluate(checkpoint, cases, max_tokens=2048):
    service=ModelService(str(checkpoint),trace_path=None,cognitive_router_manifest=None)
    if service.local_model_error: raise ValueError(service.local_model_error)
    results=[]
    for case in cases:
        started=time.monotonic()
        accepted=service.local_reply(case['messages'][:-1],max_tokens_limit=max_tokens,capture_rejected=True)
        generation=dict(service.last_generation or {})
        raw=generation.pop('raw_output','')
        record={'id':case['id'],'domain':case['domain'],'accepted_by_decoder':bool(accepted),
            'contract_valid':False,'behavior_passed':False,'generated_tests_passed':False,
            'generation':generation,'answer_preview':raw[:600]}
        try:
            plan=parse_implementation_plan(raw)
            record['contract_valid']=True
            if case['domain']=='interface':
                result=evaluate_interface(plan,case['request'])
                record['interface']=result
                record['behavior_passed']=result['passed']
                if result['passed']:
                    preview=ROOT/'model/training/programming-studio-v1/previews'/Path(checkpoint).stem/case['id']
                    preview.mkdir(parents=True,exist_ok=True)
                    for operation in plan['operations']:
                        if operation['tool']=='create_file':
                            target=preview/operation['arguments']['path']
                            target.parent.mkdir(parents=True,exist_ok=True)
                            target.write_text(operation['arguments']['content'])
                    record['preview_directory']=str(preview)
            else:
                reference=json.loads(case['messages'][-1]['content'])
                reference_test=next(op['arguments']['content'] for op in reference['operations'] if op['arguments']['path']=='test_app.py')
                with tempfile.TemporaryDirectory(prefix='own-code-eval-') as temporary:
                    directory=Path(temporary)
                    for operation in plan['operations']:
                        arguments=operation['arguments'];target=directory/arguments['path']
                        if operation['tool']=='create_directory': target.mkdir(parents=True,exist_ok=True)
                        elif operation['tool']=='create_file':
                            target.parent.mkdir(parents=True,exist_ok=True);target.write_text(arguments['content'])
                        else: raise ValueError('Esse benchmark usa projeto vazio e apenas criação de arquivos.')
                    generated=subprocess.run([sys.executable,'-m','unittest','discover','-s','.','-p','test*.py','-q'],
                                             cwd=directory,text=True,capture_output=True,timeout=5)
                    record['generated_tests_passed']=generated.returncode==0 and 'Ran 0 tests' not in generated.stderr
                    (directory/'test_independent.py').write_text(reference_test)
                    independent=subprocess.run([sys.executable,'-m','unittest','discover','-s','.','-p','test_independent.py','-q'],
                                               cwd=directory,text=True,capture_output=True,timeout=5)
                    record['behavior_passed']=independent.returncode==0 and 'Ran 0 tests' not in independent.stderr
                    record['verification_excerpt']=independent.stderr[-1000:]
        except (ValueError,KeyError,StopIteration,subprocess.TimeoutExpired,OSError) as error:
            record['error']=str(error)[:600]
        record['passed']=record['accepted_by_decoder'] and record['contract_valid'] and record['behavior_passed'] and (
            record['domain']=='interface' or record['generated_tests_passed'])
        record['seconds']=round(time.monotonic()-started,2)
        results.append(record)
        print(json.dumps({k:record[k] for k in ['id','passed','contract_valid','behavior_passed','seconds']},ensure_ascii=False),flush=True)
    return results


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--checkpoint',required=True)
    parser.add_argument('--cases',required=True)
    parser.add_argument('--output',required=True)
    args=parser.parse_args()
    cases=[json.loads(line) for line in Path(args.cases).read_text().splitlines() if line]
    rows=evaluate(args.checkpoint,cases)
    report={'schema':'own-programming-studio-eval/v1','checkpoint':args.checkpoint,'cases_source':args.cases,
        'passed':sum(row['passed'] for row in rows),'total':len(rows),'cases':rows,'runtime_recipes_used':False,
        'general_programming_mastery':False,'browser_visual_quality_confirmed':False}
    Path(args.output).parent.mkdir(parents=True,exist_ok=True)
    Path(args.output).write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(f"{report['passed']}/{report['total']}",flush=True)
    return 0 if rows and report['passed']==report['total'] else 1


if __name__=='__main__':raise SystemExit(main())
