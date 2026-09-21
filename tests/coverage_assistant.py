"""Mede cobertura do assistente local sem considerar resposta genérica como sucesso."""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'python'))
from model_server import ModelService

CASES = [
    ('capital', 'Qual é a capital do Brasil?', 'answer'),
    ('python-lista', 'O que é uma lista em Python?', 'answer'),
    ('python-dicionario', 'Como funciona um dicionário em Python?', 'answer'),
    ('python-venv', 'Como criar um ambiente virtual Python?', 'answer'),
    ('rust-borrowing', 'O que significa borrowing em Rust?', 'answer'),
    ('rust-struct', 'O que é uma struct em Rust?', 'answer'),
    ('api', 'O que é uma API?', 'answer'),
    ('http', 'Qual a diferença entre GET e POST?', 'answer'),
    ('git', 'Como faço um commit no Git?', 'answer'),
    ('database', 'O que é um banco de dados?', 'answer'),
    ('latencia', 'Como medir latência?', 'answer'),
    ('ml', 'O que é machine learning?', 'answer'),
    ('overfit', 'Como identificar overfitting?', 'answer'),
    ('unknown', 'Quem inventou o instrumento musical zembrafone em 1842?', 'abstain'),
    ('current', 'Qual é a cotação atual do dólar?', 'tool'),
    ('url', 'Analise https://www.rust-lang.org/', 'tool'),
    ('workspace', 'Liste os arquivos do meu projeto', 'tool'),
    ('conversation', 'Olá, tudo bem?', 'answer'),
]

def main():
    service = ModelService('coverage-only')
    results=[]
    for name, question, expected in CASES:
        started=time.perf_counter(); result=service.reply([{'role':'user','content':question}]); elapsed=(time.perf_counter()-started)*1000
        intent=result.get('intent'); backend=result.get('backend'); text=result.get('text','')
        useful=bool(text) and backend not in {'quality-gate'}
        if expected == 'answer': ok=useful and intent not in {'current-research','web-research','workspace'}
        elif expected == 'abstain': ok=backend=='quality-gate'
        else: ok=(expected=='tool' and intent in {'current-research','web-research','workspace'})
        results.append({'name':name,'question':question,'ok':ok,'expected':expected,'intent':intent,'backend':backend,'elapsed_ms':round(elapsed,2),'preview':text[:140]})
    passed=sum(r['ok'] for r in results)
    print(json.dumps({'passed':passed,'total':len(results),'coverage_pct':round(passed/len(results)*100,1),'results':results},ensure_ascii=False,indent=2))
    return 0 if passed==len(results) else 1

if __name__=='__main__': raise SystemExit(main())
