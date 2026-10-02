# Segunda rodada: prompt compacto e expansão de exemplos

**Data:** 29/09/2026. **Checkpoint ativo:** inalterado. Esta rodada compara
candidatos isolados com o mesmo contrato de operações e com dois pedidos
reservados (`minutes` e `initials`). Nenhum pedido reservado entrou no treino.

## Alinhamento entre treino e inferência

O diagnóstico mostrou que `conversation_compaction._messages` trocava todas as
quebras de linha de uma mensagem por espaços. O treino via o prompt
multilinha, mas a inferência via uma linha única. A compactação agora preserva
literalmente o pedido recente; o teste focado cobre código multilinha. O
`prefill_with_cache` e o cálculo direto do próximo token concordaram no
mesmo prompt (diferença máxima de logit de aproximadamente 3,8 × 10⁻⁶).

## Rodadas

O [preparador](../scripts/prepare_implementation_sft_v1.py) passou a gerar,
separadamente, um prompt experimental de 181–200 tokens. Ele mantém o contrato
JSON, caminhos relativos, aprovação e a distinção entre pedido e dados do
workspace. A rota de produção continua usando o prompt completo. Os arquivos
de treino compacto estão em
[implementation_sft_compact_v1](../datasets/implementation_sft_compact_v1/manifest.json).

| Experimento | Treino | Melhor passo | JSON no treino | Contrato no treino | Testes de referência no treino | JSON inédito | Testes inéditos |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Fonte anterior | 4 | — | 0/4 | 0/4 | 0/4 | 0/2 | 0/2 |
| [Prompt compacto](../model/training/implementation-sft-v1/compact-01/report.json) | 4 | 200 | **2/4** | **2/4** | **1/4** | 0/2 | 0/2 |
| [Prompt compacto ampliado](../model/training/implementation-sft-v1/compact-extended-01/report.json) | 20 | 80 | 1/20 | 0/20 | 0/20 | 0/2 | 0/2 |

Os relatórios de geração da rodada compacta estão em
[treino](../model/training/implementation-sft-v1/compact-01/train_probe.json) e
[reservado](../model/training/implementation-sft-v1/compact-01/heldout_evaluation.json).
Os da rodada ampliada estão em
[treino ampliado](../model/training/implementation-sft-v1/compact-extended-01/train_probe.json) e
[reservado ampliado](../model/training/implementation-sft-v1/compact-extended-01/heldout_evaluation.json).

Na rodada ampliada, a perda nos pedidos reservados caiu de 4,38 para 3,87,
mas o desempenho de geração piorou. Uma saída bruta começa com
`{"tool":"create_file","arguments":{"path":"create_file"...` e repete
operações e caminhos, em vez de criar código pertinente. A perda menor não
representa melhora funcional.

## Decisão

Há melhora observável **somente nos exemplos vistos** com o prompt compacto:
50% de JSON válido e 25% de execução correta contra 0% antes. A taxa inédita
permanece 0%. O conjunto ampliado não melhorou a geração; nenhum candidato
deve ser promovido. O prompt compacto também não deve substituir a rota de
produção com base nesses resultados.

O próximo investimento útil é uma fonte muito maior e diversa de propostas
revisadas, com comparação contra um gerador de referência sob o mesmo parser e
as mesmas verificações. É preciso cobrir arquivos completos, edições, projetos
existentes e correções após falha. Repetir estes sete pedidos para elevar
porcentagens de treino não mede a capacidade que a pessoa espera da Brasa.

Checagem de regressão executada: 26 testes focados passaram, além de oito
subcasos, incluindo compactação, KV cache e parser de implementação.
