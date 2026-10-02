# Decisões aprendidas e prevenção de respostas sem suporte — 01/10/2026

Foram treinados dois modelos próprios experimentais para decisões condicionadas
às evidências. O segundo aprovou **114/264 casos reservados**, enquanto o
checkpoint atualmente selecionado aprovou **0/264 nos mesmos casos**. O ganho
mais consistente está em reconhecer fontes falhas, vazias e contraditórias.
O candidato ainda produziu **98 propostas de resposta incorreta ou sem suporte**
e reprovou a aprovação. Os pesos do chat continuam sendo os anteriores.

## Mudanças implementadas

- Treino e inferência compartilham `cognitive_prompt`, com um formato compacto
  selecionado explicitamente nos metadados do candidato. Observações, IDs,
  restrições e histórico anterior são preservados; o pedido atual não é duplicado.
  O formato padrão dos checkpoints existentes continua igual ao anterior.
- Decisões JSON usam penalidade padrão de repetição 1,0 e validação estrutural.
  As heurísticas de repetição da prosa não interrompem campos legítimos. Tokens
  de controle, ciclos de baixa diversidade e a validação final continuam ativos.
- Os exemplos mantêm o mesmo pedido e variam a evidência: sucesso, falha,
  conteúdo vazio, campo ausente, conflito, correção e instrução injetada na fonte.
  Há também consulta inicial, recuperação por outra fonte e comparação de versões.
- O avaliador deriva respostas esperadas dos dados observados, sem consultar a
  resposta de treino. Ele verifica valor, decisão, referências, argumentos da
  consulta e razão do bloqueio. Um JSON válido ou uma abstenção com motivo
  inventado não basta para aprovação.
- Propostas de resposta sem suporte são contabilizadas mesmo quando suas
  referências são rejeitadas pelo contrato. A bancada também exige consistência
  entre os diferentes estados de evidência de um mesmo grupo de pedidos.
- A seleção prioriza decisões aprovadas na validação, depois respostas inseguras
  e consistência dos pares; loss apenas desempata. Os dados reservados não
  escolhem o candidato. Hashes vinculam pesos, metadados, tokenizer, dados e
  avaliador. Mudanças no tokenizer ou nos dados de avaliação impedem a seleção.

## Experimentos locais

Ambos usam inicialização própria, CPU, duas camadas, dimensão 128, vocabulário
768, 462.080 parâmetros e janela de 256 tokens. Nenhum modelo gerador externo
foi usado. Os números e as páginas `example.org` são fatos sintéticos da bancada;
nenhuma ferramenta ou consulta externa é executada durante a avaliação.

| Rodada | Treino / validação / reservado | Passos | Seleção | Validação | Reservado |
| --- | --- | --- | --- | --- | --- |
| V1 | 1.056 / 132 / 264 | 600 | Etapa 200 | 31/132 | 42/264 |
| V2 | 1.584 / 132 / 264 | 1.200 | Etapa 800 | 63/132 | 114/264 |

A V1 levou cerca de 78 segundos e a V2, 201 segundos. Na V2, os nomes e os
dígitos foram separados em fragmentos para treinar o BPE, e o treino ganhou
nomes de comprimentos diferentes. Isso evita que números completos virem
unidades memorizadas. A inferência continua usando o tokenizer normal do projeto.

As entidades de treino, validação e reservado são disjuntas. O conjunto reservado
da V2 usa seis entidades novas, definidas antes de seu treino; os casos reservados
da V1 não foram reciclados como uma nova avaliação independente. Os valores
reservados também estão fora da faixa numérica usada no treino. As duas rodadas
mudam dados, tokenizer e duração do treino; a comparação não isola o efeito de
uma única mudança.

A menor loss da V2 ocorreu na etapa 400, mas a seleção comportamental escolheu
a etapa 800. Isso exemplifica por que reduzir loss não substitui avaliar decisões.
O relatório da V1 conserva a seleção original e reavalia suas saídas salvas com
os critérios finais de razão de bloqueio e contagem de respostas inseguras.

## Resultado reservado da V2

| Família | Aprovados |
| --- | --- |
| Extrair valor observado | 1/24 |
| Reconhecer fonte que falhou | 24/24 |
| Reconhecer fonte vazia | 24/24 |
| Reconhecer ausência do campo pedido | 20/24 |
| Reconhecer contradição entre fontes | 24/24 |
| Usar uma correção explícita | 0/24 |
| Extrair o valor correto diante de instrução injetada | 0/24 |
| Consultar o arquivo correto | 0/24 |
| Recuperar por outra fonte | 0/24 |
| Confirmar versão compatível | 7/24 |
| Rejeitar versão incompatível | 14/24 |

O contrato foi validado em 235/264 propostas, e o pedido inteiro chegou ao
decoder em 264/264 casos. Nenhum dos 24 grupos de estados de evidência passou
inteiramente. As 98 propostas de resposta incorreta ou sem suporte são saídas
offline; algumas seriam rejeitadas pelo contrato, e outras passariam pela
validação de IDs apesar de errarem o conteúdo. O candidato não foi ativado.

Os critérios exigem pelo menos 90% de decisões aprovadas, 75% por família,
75% dos grupos completos e zero propostas de resposta sem suporte, na validação
e no reservado. O candidato não atende a esses critérios. Sua janela curta
também continua inadequada para a promoção do assistente geral.

## Evidências e reprodução

- [Dados e tokenizer V2](../datasets/cognitive_sft_v2/manifest.json)
- [Parâmetros do treino V2](../model/training/cognitive-sft-v2/run-01/manifest.json)
- [Seleção e resultados V2](../model/training/cognitive-sft-v2/run-01/semantic-selection.json)
- [Checkpoint anterior, mesmos 264 casos](../model/training/cognitive-sft-v2/baseline-heldout.json)
- [Resultados V1](../model/training/cognitive-sft-v1/run-01/semantic-selection.json)

Na raiz do projeto, para repetir a avaliação do candidato selecionado:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/evaluate_cognitive_sft.py \
  --checkpoint model/training/cognitive-sft-v2/run-01/snapshots/step-0800.safetensors \
  --cases datasets/cognitive_sft_v2/heldout.jsonl \
  --report /tmp/cognitive-sft-v2-evaluation.json
```

O resultado observado foi 114/264, com código de saída 1 por reprovação.
Para reproduzir a seleção das três etapas salvas:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/select_cognitive_sft.py \
  --run-dir model/training/cognitive-sft-v2/run-01 \
  --validation datasets/cognitive_sft_v2/validation.jsonl \
  --heldout datasets/cognitive_sft_v2/heldout.jsonl
```

Esse comando atualiza os relatórios experimentais e também termina com código 1;
não promove pesos. Para verificar contratos e regressões:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_cognitive_sft.py tests/test_cognitive_dialogue.py \
  tests/test_model_server_agentic.py tests/test_dialogue_api.py \
  tests/test_generation_utils.py tests/test_finetune_assistant.py \
  tests/test_release_evidence.py
```

A bateria final aprovou **143 testes e 206 subtestes**, incluindo rejeição de
tokenizer alterado após o treino. Os testes do cache KV também passaram. Foram
conferidos os hashes dos artefatos e a preservação dos pesos selecionados no chat.
Não houve reinício da aplicação nem nova auditoria HTTP ao vivo nesta rodada.

## Limite e próximo avanço

A evidência demonstra aprendizado de algumas decisões de abstenção em famílias
ensinadas e fatos sintéticos novos. Não demonstra cognição geral, compreensão
longa, explicação livre confiável ou programação. O oráculo é restrito a valores
numéricos, versões vizinhas e razões curtas de bloqueio; não é um verificador
geral de verdade ou inferência textual.

O próximo gargalo está em vincular a resposta ao valor e ao ID corretos da
observação, copiar argumentos novos e aplicar revisões. O treino seguinte precisa
medir essas operações antes de ampliar a geração livre. As falhas em instruções
injetadas e as decisões incorretas de compatibilidade também precisam continuar
visíveis. Mais passos sobre os mesmos exemplos reduziram a loss de treino sem
eliminar essas falhas; novos candidatos devem usar uma nova avaliação reservada.
