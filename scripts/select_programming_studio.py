"""Select by executed validation, then audit heldout once; never auto-deploy."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
from evaluate_programming_studio import evaluate


def read_cases(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--run-dir',required=True)
    parser.add_argument('--validation',required=True)
    parser.add_argument('--heldout',required=True)
    args=parser.parse_args()
    run=Path(args.run_dir)
    validation=read_cases(args.validation);heldout=read_cases(args.heldout)
    train=read_cases(run/'train.jsonl')
    keys=lambda rows:{row['messages'][0]['content'] for row in rows}
    if keys(train)&keys(validation) or keys(train)&keys(heldout) or keys(validation)&keys(heldout):
        raise ValueError('Treino, validação e heldout precisam ter pedidos disjuntos.')
    candidates=[]
    for checkpoint in sorted((run/'snapshots').glob('*.safetensors')):
        print(f'Validation: {checkpoint.name}',flush=True)
        metadata=json.loads(Path(str(checkpoint)+'.json').read_text())
        cases=evaluate(checkpoint,validation)
        record={'checkpoint':str(checkpoint),'passed':sum(case['passed'] for case in cases),
                'total':len(cases),'validation_loss':metadata['validation_loss'],'cases':cases}
        candidates.append(record)
        (run/'functional-selection-progress.json').write_text(json.dumps(candidates,ensure_ascii=False,indent=2))
    if not candidates:raise ValueError('Nenhum snapshot encontrado.')
    chosen=max(candidates,key=lambda item:(item['passed'],-item['validation_loss']))
    print(f'Heldout (after selection): {chosen["checkpoint"]}',flush=True)
    reserved=evaluate(chosen['checkpoint'],heldout)
    reserved_passed=sum(case['passed'] for case in reserved)
    qualified=(chosen['passed']==len(validation) and reserved_passed==len(heldout)
               and {case['domain'] for case in validation+heldout}>={'function','interface'})
    report={'schema':'own-programming-selection/v1','selected_checkpoint':chosen['checkpoint'],
        'selection_rule':'executed-validation-first; loss-breaks-ties; heldout-only-after-selection',
        'checkpoint_sha256':hashlib.sha256(Path(chosen['checkpoint']).read_bytes()).hexdigest(),
        'validation_source':args.validation,'heldout_source':args.heldout,
        'validation_candidates':candidates,'validation_passed':chosen['passed'],'validation_total':len(validation),
        'heldout_cases':reserved,'heldout_passed':reserved_passed,'heldout_total':len(heldout),
        'qualified_for_review':qualified,'promoted':False,'runtime_recipes_used':False,
        'general_programming_mastery':False,'visual_quality_confirmed':False,
        'limits':['A bancada reserva nomes e pedidos; cobre funções pequenas e uma família de contador web.',
                  'Duas funções inéditas não representam programação geral.',
                  'DOM simulado não comprova layout, beleza nem acessibilidade completa.',
                  'Checkpoint não é ativado sem validação funcional e revisão da interface.']}
    (run/'functional-selection.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps({key:report[key] for key in ['selected_checkpoint','validation_passed','validation_total',
        'heldout_passed','heldout_total','qualified_for_review','promoted']},ensure_ascii=False),flush=True)
    return 0 if qualified else 1


if __name__=='__main__':raise SystemExit(main())
