"""Executa as 100 tarefas e grava cobertura por categoria."""
import json
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'python'))
from model_server import ModelService

def main():
    service=ModelService('reference-evaluation')
    rows=[json.loads(line) for line in Path('corpus/eval/reference_tasks.jsonl').read_text().splitlines() if line.strip()]
    results=[]
    for row in rows:
        started=time.perf_counter(); result=service.reply([{'role':'user','content':row['question']}]); elapsed=(time.perf_counter()-started)*1000
        text=result.get('text','').lower()
        missing=[check for check in row['checks'] if check.lower() not in text]
        useful=result.get('backend') != 'quality-gate' and bool(text)
        results.append({**row,'ok':useful and not missing,'backend':result.get('backend'),'intent':result.get('intent'),'missing':missing,'elapsed_ms':round(elapsed,2)})
    by_category={}
    for category in sorted({row['category'] for row in rows}):
        group=[r for r in results if r['category']==category]; by_category[category]={'passed':sum(r['ok'] for r in group),'total':len(group)}
    passed=sum(r['ok'] for r in results)
    report={'passed':passed,'total':len(results),'coverage_pct':round(passed/len(results)*100,1),'by_category':by_category,'backend':Counter(r['backend'] for r in results),'failed':[r for r in results if not r['ok']]}
    report['backend']=dict(report['backend'])
    Path('corpus/eval/reference_report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k != 'failed'},ensure_ascii=False,indent=2))
    print(f"falhas detalhadas: {len(report['failed'])} em corpus/eval/reference_report.json")
    return 0

if __name__=='__main__': raise SystemExit(main())
