# Experimento de reparos com trajetórias locais

**Data:** 29/09/2026. **Decisão:** nenhum checkpoint promovido.

## Dados

O [preparador](../scripts/prepare_verified_repair_sft.py) examinou os 30 shards locais de `UltraData-Code-Agent` (69.895 trajetórias). Ele aceitou somente um patch de atualização com um trecho, confirmação de aplicação e saída posterior de teste com pelo menos um caso aprovado e sem sinal de falha. Também exigiu que o plano `edit_file` passasse pelo parser da Brasa e coubesse em 2.048 tokens junto do pedido.

O filtro encontrou 2.058 candidatos. Depois de limitar cada grupo inferido a 20 exemplos, o [conjunto experimental](../datasets/implementation_repair_sft_v2/manifest.json) ficou com 753 casos de treino, 72 de validação e 85 reservados. Os grupos das três partições são disjuntos. O [controle positivo](../scripts/evaluate_repair_sft.py) aceitou os 85 patches de referência reservados.

Esses exemplos são de **reparo**, não de criação de projeto. O resultado do teste foi extraído da trajetória original, sem reproduzir o repositório. O grupo vem do caminho do arquivo e pode não identificar perfeitamente o repositório. Por isso, igualdade com o patch de referência é um diagnóstico restrito, não prova de correção funcional geral.

## Treino e seleção

Um primeiro ensaio com 98 exemplos do primeiro shard reduziu a perda reservada de 8,76 para 7,53, mas ambos os modelos fizeram 0/12 contratos válidos. [Treino curto](../model/training/implementation-repair-sft-v1/run-01/report.json) e [geração](../model/training/implementation-repair-sft-v1/run-01/heldout_evaluation.json).

O segundo ensaio partiu de um checkpoint experimental que já tinha visto propostas JSON; não partiu do checkpoint principal de produção. Treinou por 120 passos no conjunto ampliado, com snapshots nos passos 40, 80 e 120. A perda de validação caiu de 7,62 para 5,64; a perda reservada caiu de 7,61 para 5,63. [Relatório de treino](../model/training/implementation-repair-sft-v2/run-02/report.json).

| Snapshot | Perda de validação | Contratos válidos na validação | Patch exato na validação |
| --- | ---: | ---: | ---: |
| Passo 40 | 6,12 | 0/72 | 0/72 |
| Passo 80 | 5,77 | 0/72 | 0/72 |
| Passo 120 | 5,64 | 0/72 | 0/72 |

A [seleção por comportamento](../model/training/implementation-repair-sft-v2/run-02/quality_selection.json) escolheu o passo 120 somente pelo desempate de perda. Na [avaliação final reservada](../model/training/implementation-repair-sft-v2/run-02/heldout_evaluation.json), o checkpoint de partida e o escolhido ficaram em **0/85 JSON válido, 0/85 contrato válido e 0/85 patch exato**. O candidato frequentemente terminou com uma resposta curta; uma saída inspecionada começou como `{"tool":"` e parou por ciclo de baixa diversidade. O ganho de perda não se converteu em implementação.

## Decisão técnica

Não continuar a aumentar passos de SFT nessa mesma arquitetura e formato apenas porque a perda cai. O próximo experimento precisa atacar a capacidade do gerador e a representação de propostas longas, mantendo a partição reservada e a medição por execução. Para a meta de criar projetos do zero, também é necessária uma fonte de exemplos completos de projeto com verificação executada; este corpus não fornece isso de forma direta.
