# Diagnóstico dos pesos neurais isolados — 2026-09-27

## Resultado

**O bloqueio permanece: 0/20 respostas aprovadas.** A bateria `tests/benchmark_neural_generation.py` chama o checkpoint diretamente, sem memória curada, ferramentas ou respostas de regras. Os 1.024 acertos do fluxo completo de chat não representam capacidade generativa dos pesos.

## Defeito encontrado e corrigido

O checkpoint ativo foi expandido de 512 para 32.768 posições por uma interpolação que espalhou as 512 posições treinadas ao longo de toda a tabela. Assim, os prompts curtos passaram a usar um pequeno trecho quase constante dessa interpolação. Os demais pesos do checkpoint expandido são idênticos aos do checkpoint de origem.

`extend_position_embeddings()` agora preserva exatamente as posições treinadas e interpola somente a cauda experimental. O servidor identifica o checkpoint expandido antigo, verifica o hash do arquivo de origem e restaura o prefixo em memória ao carregar o modelo. O artefato ativo não foi reescrito. A janela longa continua sem validação semântica; a correção do prefixo, sozinha, também obteve **0/20**.

## Treinos locais e medição

| Experimento | Mudança medida | Casos reservados aprovados |
|---|---|---:|
| Checkpoint ativo com posições restauradas | Corrige as primeiras 512 posições durante a carga | 0/20 |
| Ajuste do modelo nativo de 8.192 tokens | Loss de validação 9,503 → 8,139 em 100 etapas | 0/20 |
| Ajuste contextual do modelo de 8.192 tokens | Loss de validação 8,213 → 8,130 em 100 etapas; depois piorou | 0/20 |
| Modelo compacto de 1.024 tokens | Loss de validação 4,086 → 3,742 em 500 etapas; loss nos 20 casos 4,004 → 3,818 | 0/20 |
| Continuação do modelo compacto com taxa maior | Loss de validação 3,742 → 3,770 em 200 etapas; tentativa interrompida sem novo candidato | Não avaliado; não superou o anterior |

Foram geradas 1.245 conversas contextualizadas a partir de 415 pares de pergunta e resposta já existentes no acervo local, excluindo as perguntas reservadas. Essas variantes treinam o formato de histórico, mas **não acrescentam conhecimento novo**. O acervo disponível contém poucas centenas de respostas distintas. As saídas dos candidatos ainda são vazias, curtas, repetitivas ou irrelevantes. Reduzir a loss não satisfez o critério de geração.

Nenhum checkpoint experimental foi promovido. O estado ativo do modelo permanece no checkpoint anterior, com a correção posicional aplicada apenas na carga do servidor. Não foram usados Ollama nem serviços externos para gerar respostas ou treinar esses candidatos.

Também avaliei cinco checkpoints compactos anteriores nos mesmos 20 diálogos reservados: todos marcaram **0/20**. O antigo `compact-08-gate-focus.pt` marcou 11/12 na bateria antiga `model/eval_generation.jsonl`, mas 0/20 nesta bateria de diálogo. Esse contraste mostra que o desempenho anterior não se transfere automaticamente para conversas novas.

## Artefatos e verificação

- Dados contextualizados: `python/data/contextual_local_qa_v1.jsonl`; gerador: `python/build_context_dialogue_sft.py`.
- Reparação de posições: `python/repair_trained_positions.py` e `python/model_server.py`.
- Avaliação do ativo reparado e dos checkpoints antigos: relatórios `active-runtime-repair-neural-eval.json` e `heldout-*-neural-eval.json` em `model/training/position-repair-v1/`.
- Histórico e avaliação do candidato compacto: `model/training/neural-dialogue-compact-v1/run-01/history.json` e `neural-eval-final-128.json` na mesma pasta.
- Suíte Python após as mudanças: **342 testes aprovados, 144 subtestes aprovados**. Chat completo: **1.024/1.024**. Requisitos do chat: **7/7**. Geração neural isolada: **0/20**.

Para repetir a medição do checkpoint ativo nos 20 diálogos, indique explicitamente o arquivo reservado; a bateria antiga tem somente 12 casos:

```bash
.venv/bin/python tests/benchmark_neural_generation.py \
  --checkpoint model/godmode/context-32768-v1/candidate.safetensors \
  --eval model/training/local-dialogue-v1/heldout.jsonl \
  --max-tokens 128 \
  --report /tmp/ia-local-neural-dialogue.json
```

## Próximo requisito técnico

O treino atual parte de modelos pequenos e de um conjunto de respostas distintas pequeno demais para sustentar geração aberta. Os datasets maiores já baixados estão em `corpus/quarantine/` e marcados como inelegíveis para treino até a revisão de licença, duplicatas e contaminação. Para sair do zero de forma legítima, é necessário ampliar substancialmente o corpus autorizado e diversificado, pré treinar ou treinar um modelo próprio com capacidade e computação compatíveis, e manter os 20 casos reservados fora do treino. A promoção deve exigir geração útil nessa bateria e revisão humana de respostas abertas; loss isolada e acertos obtidos por memória ou regras não bastam.
