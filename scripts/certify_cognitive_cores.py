"""Build core-specific evidence by replaying raw outputs against the oracle."""
from __future__ import annotations
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'python'))
from cognitive_cores import (CognitiveCoreRegistry,core_summary,qualifies,regrade_report,file_hash,local_path,PROOF_SOURCES)
from evaluate_cognitive_sft import checkpoint_identity,read_cases

def certify(protocol_path,reports,output):
    registry=CognitiveCoreRegistry()
    protocol=json.loads(Path(protocol_path).read_text())
    checkpoint=local_path(protocol['checkpoint'])
    identity=checkpoint_identity(checkpoint)
    if any(protocol.get(k)!=v for k,v in identity.items()):raise ValueError('Candidato fixado mudou.')
    if protocol['catalog_sha256']!=file_hash(registry.catalog_path):raise ValueError('Critérios fixados mudaram.')
    if protocol.get('used_for_training') is not False or protocol.get('used_for_selection') is not False:
        raise ValueError('A prova não pode servir ao treino/seleção.')
    manifest_path=local_path(protocol['training_manifest'])
    if file_hash(manifest_path)!=protocol['training_manifest_sha256']:raise ValueError('Manifesto de treino mudou.')
    manifest=json.loads(manifest_path.read_text())
    blocked=set()
    for name,digest in manifest['inputs'].items():
        path=local_path(name)
        if file_hash(path)!=digest:raise ValueError('Dados da rodada mudaram.')
        if path.name in ['train.jsonl','validation.jsonl','heldout.jsonl']:
            blocked.update(row['entity'] for row in read_cases(path))
    selection_path=local_path(protocol['selection_report'])
    if file_hash(selection_path)!=protocol['selection_report_sha256']:raise ValueError('Seleção anterior mudou.')
    selection=json.loads(selection_path.read_text())
    if selection['checkpoint_sha256']!=identity['checkpoint_sha256']:raise ValueError('Candidato diverge da seleção fixada.')
    suites=[];measurements={}
    for name,report_path in reports.items():
        cases_path=local_path(protocol['cases'][name])
        if file_hash(cases_path)!=protocol['cases_sha256'][name]:raise ValueError('Bancada fixada mudou.')
        if name=='reserved' and blocked & {r['entity'] for r in read_cases(cases_path)}:
            raise ValueError('Entidades reservadas vazaram da rodada.')
        rows=regrade_report(json.loads(Path(report_path).read_text()),cases_path,checkpoint)
        measurements[name]=rows
        suites.append({'name':name,'cases':str(cases_path.relative_to(ROOT)),'cases_sha256':file_hash(cases_path),
                       'report':str(Path(report_path).resolve().relative_to(ROOT)),'report_sha256':file_hash(report_path)})
    if set(measurements)!={'reserved','regression'}:raise ValueError('Informe reservado e regressão.')
    states={}
    for id,spec in registry.cores.items():
        summary={name:core_summary(spec,rows) for name,rows in measurements.items()}
        assessed=bool(spec['domains']) and all(summary.values())
        passed=assessed and all(qualifies(spec,s) for s in summary.values())
        states[id]={'status':'passed' if passed else 'failed' if assessed else 'not_evaluated',
                    'scope':spec['scope'],'measurements':summary}
    certificate={'schema':'cognitive-core-evidence/v1','checkpoint':protocol['checkpoint'],**identity,
        'catalog_sha256':file_hash(registry.catalog_path),'protocol':str(Path(protocol_path).resolve().relative_to(ROOT)),
        'protocol_sha256':file_hash(protocol_path),'source_sha256':{name:file_hash(ROOT/name) for name in PROOF_SOURCES},
        'suites':suites,'cores':states,'promoted':False,
        'limits':['The five measured components share the V4 checkpoint; these are execution/evaluation boundaries, not independently trained neural networks.','General deliverable cores need different behavioral suites; numeric tasks cannot certify them.','Passing a core does not authorize tools or prove a particular future answer.']}
    output=Path(output)
    if output.exists():raise FileExistsError('Certificados existentes são imutáveis; use uma nova pasta.')
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(certificate,ensure_ascii=False,indent=2)+'\n')
    registry.verify_certificate(output)
    return certificate

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--protocol',type=Path,required=True);p.add_argument('--reserved-report',type=Path,required=True)
    p.add_argument('--regression-report',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();c=certify(a.protocol,{'reserved':a.reserved_report,'regression':a.regression_report},a.output)
    print(json.dumps({'checkpoint':c['checkpoint'],'cores':{id:s['status'] for id,s in c['cores'].items()},'promoted':False},ensure_ascii=False))
    return 0 if all(s['status']=='passed' for s in c['cores'].values()) else 1
if __name__=='__main__':raise SystemExit(main())
