# Treino local de diálogo em contexto — 2026-09-26

## Objetivo

Melhorar a continuidade do chat usando exclusivamente os pesos, os dados e o treinamento locais do projeto. Nenhum serviço generativo externo ou Ollama foi usado.

## Mudanças no pipeline

- O fine-tuning aceita conversas alternadas com vários turnos e calcula loss apenas sobre a resposta final do assistente.
- O split continua agrupando pela pergunta mais recente, para manter perguntas iguais no mesmo lado da validação.
- O heldout termina na pergunta do usuário e guarda a resposta de referência separadamente. Ele é medido depois da seleção do checkpoint e nunca entra no otimizador.
- `--multi-turn-repeat` permite aumentar o peso de históricos completos sem duplicar validação. O teste unitário verifica esse comportamento.
- A bateria neural aceita mensagens de contexto e chama diretamente o checkpoint; memória, ferramentas e respostas de regras não substituem a geração avaliada.
- Foram escritos 37 exemplos locais de diálogo e 20 casos inéditos reservados em áreas como preferências, revisão, planejamento, ideias, incerteza e continuidade.

## Resultados

| Medida | Checkpoint ativo | Candidato 1 | Candidato 2, melhor etapa 600 |
|---|---:|---:|---:|
| Loss de validação | 9,303 | 8,176 | 8,136 |
| Loss nos 20 casos reservados | 9,335 | 7,889 | 7,747 |
| Respostas inéditas aprovadas | 0/20 | 0/20 | 0/20 |

A loss melhora nos dois candidatos, mas as respostas geradas continuam curtas ou repetitivas. A métrica de aprovação é estrutural e automática; não comprova qualidade semântica. Nenhum candidato foi promovido, e o estado ativo não foi alterado.

## Artefatos

- Dados adicionais: `python/data/local_dialogue_context_v1.jsonl`
- Casos reservados: `model/training/local-dialogue-v1/heldout.jsonl`
- Comparação e parâmetros do primeiro treino: `model/training/local-dialogue-v1/run-01/`
- Comparação e parâmetros do treino com oito repetições de contexto: `model/training/local-dialogue-v1/run-02/`
- Benchmark do checkpoint ativo: `model/training/local-dialogue-v1/baseline.json`
- Benchmarks dos candidatos: `model/training/local-dialogue-v1/candidate-neural-eval.json` e `model/training/local-dialogue-v1/candidate-multiturn-neural-eval.json`

## Verificação

`tests/test_finetune_assistant.py`: 8 testes aprovados e 28 subtestes aprovados. Os dois benchmarks neurais completaram 20 casos cada; ambos reprovaram os candidatos, por isso os pesos ativos foram preservados.
