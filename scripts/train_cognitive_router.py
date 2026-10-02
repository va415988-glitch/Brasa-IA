#!/usr/bin/env python3
"""Train a bounded action head from local weights; select on validation only."""
import argparse
import hashlib
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'python'))
import torch
from safetensors.torch import save_file
from checkpoint_io import load_checkpoint
from cognitive_router import DecisionRouter, LABELS, encode_goal
from tokenizer import ByteBPETokenizer


def datasets():
    templates = {
        'direct': ['Explique o conceito de {}.', 'O que significa {}?', 'Me ensine sobre {}.', 'Dê um exemplo simples de {}.', 'Como funciona {}?', 'Qual a diferença entre {} e uma alternativa?', 'Faça um resumo sobre {}.', 'Me ajude a entender {}.'],
        'web': ['Qual é a versão mais recente de {}?', 'Consulte a documentação oficial de {}.', 'Busque informações atuais sobre {}.', 'Qual o preço de {} hoje?', 'O que mudou em {} nesta semana?', 'Verifique na internet se {} ainda é suportado.', 'Preciso das últimas notícias sobre {}.', 'Encontre fontes confiáveis sobre {}.'],
        'local': ['Qual versão está no arquivo {}?', 'Leia {} e explique seu conteúdo.', 'O que está configurado em {} neste projeto?', 'Confira {} no workspace.', 'Procure o requisito de runtime em {}.', 'Consulte o arquivo {} antes de responder.', 'Qual é o valor salvo em {}?', 'Sem pesquisar na internet, consulte {}.'],
        'clarify': ['Essa coisa funciona?', 'Qual deles devo usar?', 'Ela serve para isso?', 'Qual é o nome daquilo?', 'Esse serve?', 'O que você acha disso?', 'Qual versão ela exige?', 'Pode me explicar aquilo?'],
    }
    templates['direct'] += ['Defina {}.', 'Descreva {}.', 'Quero entender {}.', 'Me explique {} com um exemplo.',
                            'Para que serve {}?', 'Qual a ideia de {}?', 'O que é {}?', 'Ensine os fundamentos de {}.']
    templates['web'] += ['Qual foi o lançamento mais novo de {}?', 'Confira nas fontes oficiais o suporte atual de {}.',
                         'Pesquise a versão estável de {}.', 'Quanto custa {} atualmente?', 'A última versão de {} já saiu?',
                         'Verifique as mudanças recentes de {}.', 'Há novidades sobre {} hoje?', 'Busque documentação atual de {}.']
    templates['local'] += ['Veja em {} qual runtime utilizamos.', 'Me diga o conteúdo de {}.', 'Abra {} e confira os requisitos.',
                           'O que consta em {}?', 'Verifique a porta definida em {}.', 'Qual runtime está registrado em {}?',
                           'Sem usar a web, confira o arquivo {}.', 'O que diz o arquivo {}?']
    templates['clarify'] += ['E essa outra?', 'Isso atende?', 'Qual opção?', 'Aquilo está correto?',
                             'Aquela funciona?', 'Qual delas serve?', 'Qual é a melhor escolha?', 'Como uso isso?',
                             'Preciso escolher entre essas opções, qual é adequada?', 'Pode dizer se isso atende ao que preciso?',
                             'Essa opção é compatível?', 'O que faço com aquilo?']
    entities = {
        'direct': ['recursão', 'uma variável', 'uma função', 'uma fila', 'cache', 'uma lista', 'uma árvore', 'uma pilha', 'uma classe', 'uma condição', 'um laço', 'uma matriz'],
        'web': ['Python', 'TypeScript', 'Node.js', 'Rust', 'Django', 'React', 'Go', 'Vue', 'SQLite', 'FastAPI', 'Ubuntu', 'PostgreSQL'],
        'local': ['README.md', 'package.json', 'config.json', 'requirements.txt', 'Cargo.toml', 'runtime.txt', 'settings.yaml', 'pyproject.toml', 'versao.txt', 'manifest.json', 'ambiente.md', 'dados.csv'],
    }
    train = []
    for label, patterns in templates.items():
        for pattern in patterns:
            for value in entities.get(label, ['']):
                train.append({'goal': pattern.format(value), 'label': label})
    # Distinct paraphrases and entities, not a random split of duplicate templates.
    validation = [
        ('Defina encapsulamento.', 'direct'), ('Descreva como funciona uma tabela hash.', 'direct'),
        ('Me explique herança com um exemplo.', 'direct'), ('Explique o que é uma constante.', 'direct'),
        ('Qual foi a última versão publicada do Svelte?', 'web'), ('Confira nas fontes oficiais o suporte atual ao Flask.', 'web'),
        ('Pesquise o lançamento mais novo do Debian.', 'web'), ('Quanto custa o serviço Azure atualmente?', 'web'),
        ('Veja em dependencias.txt qual runtime utilizamos.', 'local'), ('Me diga o conteúdo de notas.md.', 'local'),
        ('Abra projeto.json e confira a versão.', 'local'), ('O que consta em build.toml?', 'local'),
        ('E essa outra?', 'clarify'), ('Isso está certo?', 'clarify'), ('Qual das duas opções?', 'clarify'), ('Aquela versão funciona?', 'clarify'),
    ]
    # First run's evaluation remains a regression set after development; a separate final audit is required.
    heldout = [
        ('O que é polimorfismo?', 'direct'), ('Explique o conceito de uma interface.', 'direct'),
        ('Me ensine sobre uma tupla.', 'direct'), ('Dê um exemplo simples de composição.', 'direct'),
        ('Quero entender o funcionamento de um dicionário.', 'direct'), ('Defina uma expressão booleana.', 'direct'),
        ('Qual é a versão estável mais recente do Caddy?', 'web'), ('Pesquise as novidades do Elixir nesta semana.', 'web'),
        ('Busque documentação oficial atual do Lit.', 'web'), ('Quanto custa o serviço Backblaze hoje?', 'web'),
        ('Encontre fontes sobre as mudanças recentes do Bun.', 'web'), ('A versão mais nova do Zig já saiu?', 'web'),
        ('Consulte cognicao-teste.txt e informe a versão mínima do Node.', 'local'), ('Veja a porta definida em servico.yaml.', 'local'),
        ('O que diz o arquivo compatibilidade.md?', 'local'), ('Leia app-config.json antes de responder.', 'local'),
        ('Sem usar a web, confira o arquivo release.txt.', 'local'), ('Qual runtime está registrado em ambiente-local.toml?', 'local'),
        ('Isso atende ao que preciso?', 'clarify'), ('Qual delas é a certa?', 'clarify'),
        ('Ela suporta isso?', 'clarify'), ('Essa versão serve?', 'clarify'), ('Como uso aquilo?', 'clarify'), ('Qual devo escolher?', 'clarify'),
    ]
    convert = lambda rows: [{'goal': goal, 'label': label} for goal, label in rows]
    reserved = {goal for goal, _ in validation + heldout}
    train = [row for row in train if row['goal'] not in reserved]
    return train, convert(validation), convert(heldout)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--steps', type=int, default=300)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    torch.set_num_threads(2)
    torch.manual_seed(137)
    random.seed(137)
    source = ROOT / 'model/godmode/training-neural-v1/candidate.safetensors'
    original = load_checkpoint(source)
    config = {k: original['config'][k] for k in ('hidden_size', 'vocab_size', 'layers', 'attention_heads', 'feed_forward_multiplier')}
    config['context_length'] = 128
    tokenizer_path = ROOT / original['config']['tokenizer_path']
    tokenizer = ByteBPETokenizer.load(tokenizer_path)
    model = DecisionRouter(config)
    state = {k: v for k, v in original['state_dict'].items() if k != 'lm_head.weight'}
    state['position_embedding.weight'] = state['position_embedding.weight'][:128]
    model.load_state_dict(state, strict=False)
    train, validation, heldout = datasets()
    for name, rows in [('train', train), ('validation', validation), ('heldout', heldout)]:
        (args.output / (name + '.jsonl')).write_text(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in rows))
    assert not ({r['goal'] for r in train} & {r['goal'] for r in validation + heldout})
    def batch(rows):
        sequences = [encode_goal(tokenizer, row['goal'], 128) for row in rows]
        longest = max(map(len, sequences))
        return (torch.tensor([s + [0] * (longest - len(s)) for s in sequences]),
                torch.tensor(list(map(len, sequences))), torch.tensor([LABELS.index(r['label']) for r in rows]))
    def evaluate(rows):
        model.eval()
        x, lengths, y = batch(rows)
        with torch.inference_mode():
            logits = model(x, lengths)
            return float(torch.nn.functional.cross_entropy(logits, y)), (logits.argmax(-1) == y).float().mean().item()
    optimizer = torch.optim.AdamW(model.parameters(), lr=0.0003, weight_decay=0.01)
    best = float('inf')
    for step in range(args.steps):
        model.train()
        sampled = [random.choice([r for r in train if r['label'] == label]) for label in LABELS for _ in range(4)]
        x, lengths, y = batch(sampled)
        optimizer.zero_grad()
        loss = torch.nn.functional.cross_entropy(model(x, lengths), y)
        loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
        optimizer.step()
        if (step + 1) % 25 == 0:
            val_loss, accuracy = evaluate(validation)
            print(json.dumps({'step': step + 1, 'validation_loss': val_loss, 'validation_accuracy': accuracy}), flush=True)
            if val_loss < best:
                best = val_loss
                save_file({k: v.detach().contiguous() for k, v in model.state_dict().items()}, str(args.output / 'candidate.safetensors'))
    from safetensors.torch import load_file
    model.load_state_dict(load_file(str(args.output / 'candidate.safetensors')))
    model.eval()
    x, lengths, _ = batch(heldout)
    with torch.inference_mode():
        scores = model(x, lengths).softmax(-1)
    rows = [{**row, 'predicted': LABELS[int(probs.argmax())], 'score': float(probs.max()),
             'passed': LABELS[int(probs.argmax())] == row['label']} for row, probs in zip(heldout, scores)]
    metadata = {'schema': 'cognitive-router/v1', 'labels': list(LABELS), 'config': config,
                'weights': str((args.output / 'candidate.safetensors').relative_to(ROOT)),
                'sha256': hashlib.sha256((args.output / 'candidate.safetensors').read_bytes()).hexdigest(),
                'tokenizer': str(tokenizer_path.relative_to(ROOT)),
                'tokenizer_sha256': hashlib.sha256(tokenizer_path.read_bytes()).hexdigest(),
                'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(), 'threshold': 0.8,
                'training_rows': len(train), 'validation_rows': len(validation), 'heldout_rows': len(heldout),
                'pooling': 'masked-mean-v1', 'scope': 'short Portuguese requests: direct, web, local, clarification; not a general language model'}
    (args.output / 'candidate.json').write_text(json.dumps(metadata, indent=2) + '\n')
    report = {'passed': sum(r['passed'] for r in rows), 'total': len(rows), 'cases': rows,
              'selection': 'minimum validation loss; heldout evaluated only after selection',
              'validation_loss': best, 'mode': 'learned-action-selection-only'}
    (args.output / 'evaluation.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'heldout_passed': report['passed'], 'heldout_total': report['total']}), flush=True)


if __name__ == '__main__':
    main()
