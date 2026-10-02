"""Freeze fresh, entity-disjoint core tests and criteria before evaluation."""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'python'))
from cognitive_cores import CognitiveCoreRegistry, file_hash, local_path
from evaluate_cognitive_sft import checkpoint_identity, evidence_oracle, read_cases
import prepare_cognitive_alignment as author

ENTITIES=['AraticP','MangabaD','BuritiE','CopaibaL','PitangaS','JenipapZ']

def prepare(destination,checkpoint,training_manifest,selection_report,regression):
    destination=Path(destination)
    if destination.exists(): raise FileExistsError('Use uma pasta nova; bancadas existentes são imutáveis.')
    checkpoint=Path(checkpoint).resolve()
    manifest=json.loads(Path(training_manifest).read_text())
    selection=json.loads(Path(selection_report).read_text())
    identity=checkpoint_identity(checkpoint)
    if selection['checkpoint_sha256'] != identity['checkpoint_sha256']:
        raise ValueError('O candidato deve ser fixado antes da prova por núcleos.')
    inputs={Path(name).name:local_path(name) for name in manifest['inputs']}
    for name,digest in manifest['inputs'].items():
        if file_hash(local_path(name)) != digest: raise ValueError('Dados de treino/seleção mudaram.')
    excluded={row['entity'] for name in ['train.jsonl','validation.jsonl','heldout.jsonl'] for row in read_cases(inputs[name])}
    if excluded & set(ENTITIES): raise ValueError('Entidade da nova prova já usada na rodada.')
    original=author.ENTITIES_V4['heldout']
    try:
        author.ENTITIES_V4['heldout']=ENTITIES
        rows=author.cases_for('heldout')
    finally:
        author.ENTITIES_V4['heldout']=original
    # Shift all numeric facts and comparison operands to a fresh six-digit range.
    import re
    for row in rows:
        for message in row['request_messages']:
            message['content']=re.sub(r'\b\d{6}\b',lambda m:str(int(m[0])+90002),message['content'])
        row['id']=row['id'].replace('aligned-heldout-','core-reserved-')
        row['pair_group']=row['pair_group'].replace('aligned-heldout-','core-reserved-')
        # Reference decisions, model answers and gold copy spans are absent from the inference suite.
        for key in ['messages','reference_decision','copy_spans']:row.pop(key,None)
        evidence_oracle(row)
    regression_rows=read_cases(regression)
    if {r['entity'] for r in regression_rows} & {r['entity'] for r in read_cases(inputs['train.jsonl'])}:
        raise ValueError('A bancada de regressão contém entidades de treino.')
    destination.mkdir(parents=True)
    reserved=destination/'reserved.jsonl'
    reserved.write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows))
    registry=CognitiveCoreRegistry()
    protocol={'schema':'cognitive-core-protocol/v1','checkpoint':str(checkpoint.relative_to(ROOT)),
        **identity,'catalog_sha256':file_hash(registry.catalog_path),
        'training_manifest':str(Path(training_manifest).resolve().relative_to(ROOT)),
        'training_manifest_sha256':file_hash(training_manifest),
        'selection_report':str(Path(selection_report).resolve().relative_to(ROOT)),
        'selection_report_sha256':file_hash(selection_report),
        'cases':{'reserved':str(reserved.resolve().relative_to(ROOT)),'regression':str(Path(regression).resolve().relative_to(ROOT))},
        'cases_sha256':{'reserved':file_hash(reserved),'regression':file_hash(regression)},
        'used_for_training':False,'used_for_selection':False,'selected_before_qualification':True,
        'attempts_per_case':1,'source_sha256':{'scripts/prepare_core_qualification.py':file_hash(Path(__file__))},
        'reserved_entities':ENTITIES,'reserved_rows':len(rows),
        'policy':'Each core must pass reserved and regression suites, per-family thresholds and counterpart states. Missing proof is not evaluated.',
        'limits':['Synthetic task templates are familiar; entities and numeric range are new.','Regression cases are historical and cannot independently prove unseen transfer.','No retraining, nucleus-specific weights or general competency are claimed.']}
    (destination/'protocol.json').write_text(json.dumps(protocol,ensure_ascii=False,indent=2)+'\n')
    return protocol

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output-dir',type=Path,required=True);p.add_argument('--checkpoint',type=Path,required=True)
    p.add_argument('--training-manifest',type=Path,required=True);p.add_argument('--selection-report',type=Path,required=True)
    p.add_argument('--regression',type=Path,required=True)
    a=p.parse_args();print(json.dumps(prepare(a.output_dir,a.checkpoint,a.training_manifest,a.selection_report,a.regression),ensure_ascii=False,indent=2))
if __name__=='__main__':main()
