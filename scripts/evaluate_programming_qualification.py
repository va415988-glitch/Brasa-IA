"""Generate one proposal per qualification task and grade it with hidden tests.

Backends: ``checkpoint`` (the project's own model), ``endpoint`` (any
OpenAI-compatible server, for comparing reference models), ``reference`` (the
oracle solutions, must score 100%) and ``null`` (an empty answer, must score 0%).
"""
from __future__ import annotations
import argparse,json,sys,time
from pathlib import Path
from urllib.request import Request,urlopen
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'python'))
from programming_qualification import grade,load_suite,reference_plan

def checkpoint_generator(path,max_tokens):
    from model_server import ModelService
    service=ModelService(str(path),trace_path=None,cognitive_router_manifest=None)
    if service.local_model_error:raise ValueError(service.local_model_error)
    def generate(task):
        service.local_reply(task['messages'],max_tokens_limit=max_tokens,capture_rejected=True)
        return (service.last_generation or {}).get('raw_output','')
    return generate

def endpoint_generator(endpoint,model,max_tokens):
    def generate(task):
        body=json.dumps({'model':model,'messages':task['messages'],'temperature':0,'max_tokens':max_tokens}).encode()
        request=Request(endpoint.rstrip('/')+'/v1/chat/completions',data=body,headers={'Content-Type':'application/json'})
        with urlopen(request,timeout=300) as response:return json.load(response)['choices'][0]['message']['content']
    return generate

def evaluate(tasks,generate):
    rows=[]
    for task in tasks:
        started=time.monotonic()
        try:raw=generate(task)
        except Exception as error:raw='';failure=f'geração: {str(error)[:200]}'
        else:failure=None
        record=grade(task,raw)
        if failure:record['error']=failure
        record['raw_output']=raw;record['seconds']=round(time.monotonic()-started,2)
        rows.append(record)
        print(json.dumps({k:record[k] for k in ('id','contract_valid','own_tests_passed','hidden_tests_passed','passed')},ensure_ascii=False),flush=True)
    return rows

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--suite',choices=['reserved','regression'],required=True)
    p.add_argument('--backend',choices=['checkpoint','endpoint','reference','null'],required=True)
    p.add_argument('--checkpoint');p.add_argument('--endpoint');p.add_argument('--model')
    p.add_argument('--dataset',type=Path,default=ROOT/'datasets/programming_qualification_v1')
    p.add_argument('--max-tokens',type=int,default=2048);p.add_argument('--limit',type=int)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    tasks=load_suite(a.dataset,a.suite)
    if a.limit:tasks=tasks[:a.limit]
    if a.backend=='checkpoint':
        if not a.checkpoint:p.error('--checkpoint é obrigatório');
        generate=checkpoint_generator(a.checkpoint,a.max_tokens)
    elif a.backend=='endpoint':
        if not (a.endpoint and a.model):p.error('--endpoint e --model são obrigatórios')
        generate=endpoint_generator(a.endpoint,a.model,a.max_tokens)
    elif a.backend=='reference':
        refs={json.loads(l)['id']:json.loads(l)['reference'] for l in (a.dataset/f'{a.suite}.references.jsonl').read_text().splitlines() if l}
        generate=lambda task:reference_plan({**task,'reference':refs[task['id']]})
    else:generate=lambda task:''
    rows=evaluate(tasks,generate)
    report={'schema':'programming-qualification-report/v1','suite':a.suite,'backend':a.backend,
        'checkpoint':a.checkpoint,'endpoint':a.endpoint,'model':a.model,'cases':rows,
        'passed':sum(r['passed'] for r in rows),'total':len(rows)}
    a.output.parent.mkdir(parents=True,exist_ok=True)
    if a.output.exists():raise FileExistsError('Relatórios existentes são imutáveis; use outro caminho.')
    a.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(f"{report['passed']}/{report['total']}")
    return 0 if rows and report['passed']==report['total'] else 1
if __name__=='__main__':raise SystemExit(main())
