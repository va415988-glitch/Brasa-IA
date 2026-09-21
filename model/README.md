# Primeiro modelo próprio

Esta pasta descreve o modelo experimental treinado pelo projeto. Nenhum modelo do Ollama participa deste caminho.

## Primeira configuração

- arquitetura: Transformer decoder-only;
- objetivo: prever o próximo token;
- vocabulário inicial: até 1.024 tokens;
- contexto: 512 tokens;
- camadas: 6;
- dimensão interna: 384;
- cabeças de atenção: 6;
- precisão de inferência planejada: quantizada para CPU;
- prioridade: português, código e uso de ferramentas.

Essa primeira versão será pequena de propósito. O objetivo inicial é validar o ciclo de treinamento, uso de ferramentas e avaliação no computador local. A qualidade será ampliada por dados melhores e destilação somente depois que o ciclo estiver mensurável.

O vocabulário será ampliado junto com o corpus. O limite de 1.024 tokens é adequado a esta primeira experiência e evita reservar capacidade que ainda não está sendo usada.

## Dados de ferramenta

Os exemplos devem ensinar quatro comportamentos:

1. responder diretamente quando não é necessário usar uma ferramenta;
2. escolher a ferramenta correta;
3. produzir argumentos válidos;
4. interpretar o resultado e citar as fontes.

Os exemplos iniciais estão em `python/data/tool_traces.jsonl`.

## Componentes auxiliares aprovados

O treino do planejador usa `python/train_planner.py` e grava um reranker leve
em `model/planner/planner_index.json`. Ele melhora desempates entre ferramentas
sem substituir contratos ou validação.

Checkpoints próprios também podem ser convertidos para Safetensors:

```bash
./.venv/bin/python python/convert_checkpoint.py \
  model/checkpoints/compact-06-augmented-v2.pt \
  model/checkpoints/compact-06-augmented-v2.safetensors
```

O formato evita carregar pesos por pickle e mantém metadados JSON separados.
O checkpoint em Safetensors foi validado com o mesmo gerador local; isso muda o
formato de armazenamento, não transforma um checkpoint reprovado em um modelo
aprovado.

## Direção do modelo próprio

## Janela mínima de produção

O projeto não promove checkpoints com menos de 8.192 tokens de contexto. A
configuração de primeiro experimento está em `config-context-8192.json`; ela
deve ser treinada, avaliada e comparada antes de substituir qualquer
checkpoint atual. O treinador bloqueia configurações experimentais menores
que esse mínimo, exceto o caminho histórico `config.json`, mantido apenas para
reproduzir os experimentos antigos.

O caminho do projeto não depende de baixar um modelo externo. A flexibilidade
será construída no próprio sistema com decomposição de tarefas, planner local,
contratos de ferramentas, memória explícita, corpus, traces avaliados,
verificação e recuperação. Os checkpoints próprios continuam separados do
runtime até passarem pela avaliação de coerência.
