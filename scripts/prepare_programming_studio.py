"""Author a small executable curriculum; never install its targets as recipes."""
from __future__ import annotations
import argparse
import ast
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'python'))
from proactive_implementation import compact_implementation_prompt, parse_implementation_plan
from tokenizer import ByteBPETokenizer


FUNCTIONS = [
    ('add', 'left, right', 'some os dois valores', 'return left + right', '(3, 4), 7'),
    ('subtract', 'left, right', 'subtraia o segundo valor do primeiro', 'return left - right', '(9, 2), 7'),
    ('multiply', 'left, right', 'multiplique os dois valores', 'return left * right', '(-3, 4), -12'),
    ('maximum', 'left, right', 'retorne o maior valor', 'return max(left, right)', '(9, 2), 9'),
    ('minimum', 'left, right', 'retorne o menor valor', 'return min(left, right)', '(9, 2), 2'),
    ('distance', 'left, right', 'retorne a diferença absoluta', 'return abs(left - right)', '(2, 9), 7'),
    ('double', 'value', 'retorne o dobro do valor', 'return value * 2', '(7,), 14'),
    ('square', 'value', 'retorne o quadrado do valor', 'return value * value', '(-3,), 9'),
    ('cube', 'value', 'retorne o cubo do valor', 'return value ** 3', '(3,), 27'),
    ('positive', 'value', 'indique se o valor é positivo', 'return value > 0', '(-3,), False'),
    ('even', 'value', 'indique se o valor é par', 'return value % 2 == 0', '(8,), True'),
    ('length', 'items', 'conte os itens da lista', 'return len(items)', '([1, 2, 3],), 3'),
    ('total', 'items', 'some os valores da lista', 'return sum(items)', '([-2, 8, 3],), 9'),
    ('reverse', 'items', 'inverta a ordem dos itens', 'return items[::-1]', '([1, 2, 3],), [3, 2, 1]'),
    ('sort', 'items', 'ordene os itens sem alterar a entrada', 'return sorted(items)', '([3, 1, 2],), [1, 2, 3]'),
    ('positive_items', 'items', 'selecione apenas os valores positivos', 'return [x for x in items if x > 0]', '([-2, 3, 0, 5],), [3, 5]'),
    ('even_items', 'items', 'selecione apenas os valores pares', 'return [x for x in items if x % 2 == 0]', '([1, 2, 3, 4],), [2, 4]'),
    ('positive_total', 'items', 'some apenas os valores positivos', 'return sum(x for x in items if x > 0)', '([-2, 3, 0, 5],), 8'),
    ('lower', 'text', 'converta o texto para minúsculas', 'return text.lower()', "('AbC',), 'abc'"),
    ('upper', 'text', 'converta o texto para maiúsculas', 'return text.upper()', "('AbC',), 'ABC'"),
    ('words', 'text', 'separe as palavras por espaços', 'return text.split()', "('um  dois',), ['um', 'dois']"),
    ('word_count', 'text', 'conte as palavras por espaços', 'return len(text.split())', "('um  dois',), 2"),
    ('strip', 'text', 'remova espaços nas pontas do texto', 'return text.strip()', "('  abc  ',), 'abc'"),
    ('join', 'items', 'una as palavras com um espaço', "return ' '.join(items)", "(['a', 'b'],), 'a b'"),
]
VALIDATION = [
    ('absolute', 'value', 'retorne o valor absoluto', 'return abs(value)', '(-8,), 8'),
    ('first', 'items', 'retorne o primeiro item ou None para lista vazia', 'return items[0] if items else None', '([7, 3],), 7'),
]
HELDOUT = [
    ('even_total', 'items', 'some os valores pares de uma lista', 'return sum(x for x in items if x % 2 == 0)', '([1, 2, 3, 4],), 6'),
    ('short_words', 'text', 'retorne palavras com menos de quatro letras', 'return [word for word in text.split() if len(word) < 4]', "('um dois sol lua',), ['um', 'sol', 'lua']"),
]


def function_row(case, alias='', variant=0):
    name, parameters, purpose, body, assertion = case
    name += alias
    wording = ['Implemente', 'Crie', 'Desenvolva', 'Escreva'][variant % 4]
    request = f'{wording} em Python uma função {name}({parameters}) que {purpose}. Use a biblioteca padrão e inclua testes unittest.'
    source = f'def {name}({parameters}):\n    {body}\n'
    args, expected = ast.literal_eval(assertion)
    tests = f'import unittest\nfrom app import {name}\n\nclass Behavior(unittest.TestCase):\n    def test_result(self):\n        self.assertEqual({name}(*{args!r}), {expected!r})\n'
    plan = {'assumptions': [], 'operations': [
        {'tool': 'create_file', 'arguments': {'path': 'app.py', 'content': source}},
        {'tool': 'create_file', 'arguments': {'path': 'test_app.py', 'content': tests}}]}
    return row(name, 'function', request, plan)


STYLES = {
    'editorial': ('serifa, preto e creme, composição editorial, sem cartões',
        'body{background:#f6f1e7;color:#191a19;font-family:Georgia,serif}main{max-width:980px;border-top:8px solid currentColor}h1{font-size:clamp(3rem,9vw,7rem);font-weight:400;line-height:.95}output{display:block;font-size:clamp(4rem,14vw,10rem);border-bottom:1px solid currentColor}button{background:none;color:inherit;border:1px solid currentColor;border-radius:0}'),
    'terminal': ('tipografia monoespaçada, fundo escuro, verde, composição de terminal',
        'body{background:#101916;color:#abecb0;font-family:monospace}main{max-width:760px;border-left:3px solid #abecb0;padding-left:32px}h1{font-size:clamp(2rem,7vw,4rem)}output{display:block;font-size:clamp(5rem,18vw,10rem)}button{background:#abecb0;color:#101916;border:0;border-radius:0}'),
    'split': ('tipografia sem serifa, azul e laranja, painel dividido, números grandes',
        'body{background:#152956;color:#f3f0e8;font-family:Arial,sans-serif}main{max-width:1100px;display:grid;grid-template-columns:1fr 1fr;gap:32px}h1{font-size:clamp(3rem,7vw,6rem)}output{display:block;grid-column:2;font-size:clamp(5rem,14vw,11rem);color:#ffb076}section{grid-column:1/-1}button{background:#ffb076;color:#152956;border:0;border-radius:6px}@media(max-width:640px){main{display:block}}'),
    'paper': ('aparência de caderno, linhas horizontais, tipografia sem serifa, amarelo e preto',
        'body{background:repeating-linear-gradient(#ffe891 0 39px,#d8be64 40px);color:#25221b;font-family:Arial,sans-serif}main{max-width:800px;border-left:2px solid #cf5750;padding-left:30px}h1{font-size:clamp(2.5rem,8vw,5rem)}output{display:block;font-size:clamp(5rem,18vw,10rem);font-weight:700}button{background:#25221b;color:#ffe891;border:0;border-radius:0}'),
}


def interface_row(style, title, index):
    brief, css = STYLES[style]
    request = f'Crie uma interface web local chamada {title}: {brief}. Deve contar itens com botões adicionar, remover e zerar, impedir valores negativos e salvar no navegador. Use um index.html autossuficiente, acessível e responsivo, sem dependências externas.'
    source = f'''<!doctype html>
<html lang="pt-BR"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title><style>*{{box-sizing:border-box}}body{{margin:0;padding:clamp(24px,6vw,72px)}}main{{margin:auto}}button{{font:inherit;padding:14px 20px;min-height:44px;cursor:pointer}}button:focus-visible{{outline:3px solid currentColor;outline-offset:4px}}section{{display:flex;gap:12px;flex-wrap:wrap}}@media(prefers-reduced-motion:reduce){{*{{animation:none;transition:none}}}}{css}</style>
<main><header><p>SEU RITMO, SEU ESPAÇO</p><h1>{title}</h1><p>Registre um item por vez. O progresso fica neste navegador.</p></header>
<output id="value" aria-live="polite" aria-label="Total de itens">0</output><section aria-label="Ações">
<button id="add" type="button">Adicionar</button><button id="remove" type="button">Remover</button><button id="reset" type="button">Zerar</button></section><p id="status" role="status"></p></main>
<script>
let count=0;
try{{const saved=Number(localStorage.getItem('counter'));if(Number.isSafeInteger(saved)&&saved>=0)count=saved;}}catch{{}}
function render(){{document.getElementById('value').textContent=String(count);document.getElementById('remove').disabled=count===0;}}
function update(delta){{count=Math.max(0,count+delta);render();try{{localStorage.setItem('counter',String(count));document.getElementById('status').textContent='Salvo neste navegador';}}catch{{document.getElementById('status').textContent='Armazenamento indisponível; progresso apenas nesta sessão';}}}}
document.getElementById('add').addEventListener('click',()=>update(1));
document.getElementById('remove').addEventListener('click',()=>update(-1));
document.getElementById('reset').addEventListener('click',()=>update(-count));render();
</script></html>
'''
    plan = {'assumptions': [f'Direção visual: {brief}.'], 'operations': [
        {'tool': 'create_file', 'arguments': {'path': 'index.html', 'content': source}}]}
    return row(f'ui-{style}-{index}', 'interface', request, plan)


def row(case_id, kind, request, plan):
    answer = json.dumps(plan, ensure_ascii=False, separators=(',', ':'))
    parse_implementation_plan(answer)
    return {'id': case_id, 'domain': kind, 'request': request,
            'messages': [{'role': 'user', 'content': compact_implementation_prompt(request)},
                         {'role': 'assistant', 'content': answer}]}


def verify_reference(record):
    plan = json.loads(record['messages'][-1]['content'])
    if record['domain'] == 'function':
        with tempfile.TemporaryDirectory() as directory:
            for operation in plan['operations']:
                (Path(directory) / operation['arguments']['path']).write_text(operation['arguments']['content'])
            result = subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', '.', '-q'], cwd=directory,
                                    capture_output=True, text=True, timeout=5)
            if result.returncode or 'Ran 0 tests' in result.stderr: raise ValueError(record['id'] + ': ' + result.stderr)
    else:
        # Executable interactions, separate from visual review of the authored examples.
        from interface_quality import evaluate_interface
        result = evaluate_interface(plan, record['request'])
        if not result['passed']: raise ValueError(record['id'] + ': ' + json.dumps(result))


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output-dir', default='datasets/programming_studio_v1')
    args=parser.parse_args()
    destination=ROOT / args.output_dir
    destination.mkdir(parents=True,exist_ok=False)
    train=[function_row(case, '' if variant==0 else f'_{variant}', variant) for case in FUNCTIONS for variant in range(4)]
    train += [interface_row(style, title, index) for style in STYLES for index,title in enumerate(['Meu progresso','Leituras','Ideias','Pequenas vitórias'])]
    validation=[function_row(case) for case in VALIDATION] + [interface_row('editorial','Páginas do dia',100)]
    heldout=[function_row(case) for case in HELDOUT] + [interface_row('split','Ritual criativo',101)]
    for records in (train,validation,heldout):
        for record in records: verify_reference(record)
    for split,records in [('train',train),('validation',validation),('heldout',heldout)]:
        (destination / f'{split}.jsonl').write_text(''.join(json.dumps(row,ensure_ascii=False)+'\n' for row in records))
    # Fit tokenizer only on training text, never validation or heldout.
    tokenizer=ByteBPETokenizer.train([m['content'] for row in train for m in row['messages']],vocab_size=768)
    tokenizer.save(destination / 'tokenizer.json')
    lengths=[len(tokenizer.encode_fast(row['messages'][-1]['content'])) for row in train]
    manifest={'schema':'programming-studio-curriculum/v1','train':len(train),'validation':len(validation),'heldout':len(heldout),
        'maximum_answer_tokens':max(lengths),'tokenizer_fitted_on':'train-only','runtime_recipes_installed':False,
        'limits':['Autoral e sintético; não estabelece programação geral.','Interfaces reservadas variam briefing e conteúdo dentro de famílias visuais vistas.','Funções reservadas não são utilizadas no otimizador.'],
        'sha256':{path.name:hashlib.sha256(path.read_bytes()).hexdigest() for path in destination.glob('*.jsonl')}}
    (destination/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
    print(json.dumps(manifest,ensure_ascii=False,indent=2),flush=True)


if __name__=='__main__':main()
