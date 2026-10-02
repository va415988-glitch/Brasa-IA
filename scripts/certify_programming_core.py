"""Regrade raw generations against the frozen protocol and write a programming-core certificate.

The certificate re-executes every proposal; a reported ``passed`` is never trusted.
Exit status is 1 unless both suites meet the catalog thresholds of the ``programming`` core.
"""
from __future__ import annotations
import argparse,hashlib,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'python'))
from programming_qualification import grade,load_suite,summarize

def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def certify(dataset,reports,output,catalog=ROOT/'config/cognitive_cores.json'):
    protocol=json.loads((dataset/'protocol.json').read_text())
    for name,expected in protocol['source_sha256'].items():
        if digest(ROOT/name)!=expected:raise ValueError(f'Código do avaliador mudou: {name}')
    spec=next(c for c in json.loads(Path(catalog).read_text())['cores'] if c['id']=='programming')
    if set(reports)!={'reserved','regression'}:raise ValueError('Informe reservado e regressão.')
    summaries={};suites=[]
    for suite,path in reports.items():
        meta=protocol['suites'][suite]
        for key,name in (('inputs_sha256',f'{suite}.jsonl'),('oracle_sha256',f'{suite}.oracle.jsonl')):
            if digest(dataset/name)!=meta[key]:raise ValueError(f'Bancada {suite} mudou.')
        report=json.loads(Path(path).read_text())
        if report['suite']!=suite or report['backend'] in ('reference','null'):
            raise ValueError('Relatório inválido para certificação (suíte errada ou backend de controle).')
        tasks=load_suite(dataset,suite);raw={r['id']:r['raw_output'] for r in report['cases']}
        if set(raw)!={t['id'] for t in tasks}:raise ValueError(f'Relatório {suite} não cobre todas as tarefas.')
        rows=[grade(t,raw[t['id']]) for t in tasks]
        summaries[suite]=summarize(spec,rows,protocol['domains'])
        suites.append({'name':suite,'report':str(Path(path).resolve().relative_to(ROOT)),'report_sha256':digest(path)})
    passed=all(s['meets_catalog_thresholds'] for s in summaries.values())
    certificate={'schema':'programming-core-evidence/v1','scope':protocol['scope'],
        'generator':{k:json.loads(Path(reports['reserved']).read_text()).get(k) for k in ('backend','checkpoint')},
        'protocol_sha256':digest(dataset/'protocol.json'),'catalog_sha256':digest(catalog),
        'suites':suites,'measurements':summaries,
        'status':'passed_function_level' if passed else 'failed',
        'registry_eligible':False,'integration_proven':False,'promoted':False,'limits':protocol['limits']}
    output=Path(output)
    if output.exists():raise FileExistsError('Certificados existentes são imutáveis; use outro caminho.')
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(certificate,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return certificate

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--dataset',type=Path,default=ROOT/'datasets/programming_qualification_v1')
    p.add_argument('--reserved-report',type=Path,required=True);p.add_argument('--regression-report',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    c=certify(a.dataset,{'reserved':a.reserved_report,'regression':a.regression_report},a.output)
    print(json.dumps({'status':c['status'],**{s:f"{m['passed']}/{m['total']}" for s,m in c['measurements'].items()}}))
    return 0 if c['status']!='failed' else 1
if __name__=='__main__':raise SystemExit(main())
