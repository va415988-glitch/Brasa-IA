"""Build the programming qualification benchmark and freeze its protocol.

Writes generator-visible inputs, hidden oracle cases and reference solutions in
separate files, proves that every reference passes its own hidden tests, and
records hashes so a later certificate can detect any change to the bench.
"""
from __future__ import annotations
import argparse,hashlib,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'python'))
from programming_qualification import grade,reference_plan,request_messages
from programming_qualification_tasks import regression_tasks,reserved_tasks

def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def write_jsonl(path,rows):Path(path).write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows),encoding='utf-8')

def build(output:Path):
    output.mkdir(parents=True,exist_ok=True)
    protocol={'schema':'programming-qualification-protocol/v1','scope':'python-function-deliverables/v1','suites':{},'self_check':{}}
    for suite,tasks in (('reserved',reserved_tasks()),('regression',regression_tasks())):
        failed=[t['id'] for t in tasks if not grade(t,reference_plan(t))['passed']]
        if failed:raise SystemExit(f'Referências reprovam nos próprios testes: {failed}')
        write_jsonl(output/f'{suite}.jsonl',[{'id':t['id'],'domain':t['domain'],'suite':suite,'messages':request_messages(t['request'])} for t in tasks])
        write_jsonl(output/f'{suite}.oracle.jsonl',[{'id':t['id'],'function':t['function'],'cases':t['cases']} for t in tasks])
        write_jsonl(output/f'{suite}.references.jsonl',[{'id':t['id'],'reference':t['reference']} for t in tasks])
        counts={}
        for t in tasks:counts[t['domain']]=counts.get(t['domain'],0)+1
        protocol['suites'][suite]={'cases':len(tasks),'per_domain':counts,
            'inputs_sha256':digest(output/f'{suite}.jsonl'),'oracle_sha256':digest(output/f'{suite}.oracle.jsonl'),
            'references_sha256':digest(output/f'{suite}.references.jsonl')}
        protocol['self_check'][suite]={'references_passing':len(tasks)}
    protocol['domains']=['strings','lists','numbers','records']
    protocol['source_sha256']={name:digest(ROOT/name) for name in ['python/programming_qualification.py','python/programming_qualification_tasks.py','scripts/prepare_programming_qualification.py']}
    protocol['limits']=['Function-level Python tasks only; integration into an existing project is not measured.',
        'Hidden tests run in a resource-limited subprocess, not a network- or filesystem-isolated sandbox.',
        'Regression tasks reuse the small parametric shapes of the training data and cannot prove transfer.',
        'A pass here does not satisfy the full executed-tests-and-unseen-integration proof of the catalog.']
    (output/'protocol.json').write_text(json.dumps(protocol,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return protocol

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,default=ROOT/'datasets/programming_qualification_v1')
    protocol=build(p.parse_args().output)
    print(json.dumps({s:v['cases'] for s,v in protocol['suites'].items()}))
if __name__=='__main__':main()
