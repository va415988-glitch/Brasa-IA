# Cópia neural de evidências — 01/10/2026

O candidato próprio com cópia neural aprovou **122/264 casos reservados**, contra
**98/264 no controle sem cópia** e **67/264 na V2**, todos na mesma bancada V3.
Foram aprovadas 13/72 extrações de valores, incluindo correções e fontes com
instrução injetada; os comparadores não aprovaram nenhuma dessas extrações.
O candidato ainda produziu **79 propostas de resposta incorreta ou sem suporte**,
reprovou os critérios e permanece experimental. Os pesos ativos do chat foram
preservados. O resultado não demonstra cognição geral.

## Implementação

`python/model.py` ganhou uma cabeça opcional de atenção de cópia. Projeções
treináveis escolhem tokens da origem, e uma porta treinável combina sua
distribuição com a saída normal do modelo. Não há regras sobre entidades,
números, revisões, caminhos ou URLs dentro dessa cabeça. O treino supervisionado
continua usando a loss dos tokens da resposta.

A origem disponível termina antes do delimitador da resposta atual. Cada
posição só reconhece delimitadores já completos, mantendo a causalidade durante
o treino. Os testes verificam que modificar tokens ou delimitadores futuros não
altera os logits anteriores e que a distribuição de cópia vem dos tokens da
origem. Também verificam gradientes finitos quando não existe delimitador.

Os checkpoints existentes mantêm sua arquitetura e o cache KV. O candidato com
cópia usa forward completo: o cache atual não armazena a memória exigida pela
cabeça, e os caminhos de cache rejeitam sua ativação. Essa limitação aumenta o
custo de geração e precisa ser resolvida antes de considerar uso amplo.

## Bancada V3

O conjunto contém 2.112 exemplos de treino, 132 de validação e 264 reservados,
em onze famílias. As entidades dos três conjuntos são disjuntas. Os seis nomes
reservados são novos em relação ao experimento V2. As famílias de tarefa estão
presentes no treino; a avaliação mede transferência para novos parâmetros e
estados de evidência dentro dessas famílias.

- Números de um a cinco dígitos no treino; faixas numéricas novas na validação e
  no reservado. O BPE é treinado apenas com o conjunto de treino, separando
  nomes, dígitos e delimitadores de papel em fragmentos.
- Fontes de outro projeto com o mesmo campo, em posições alternadas, exigem
  associar o valor ao projeto pedido. A correção explícita também alterna de
  posição, exigindo escolher o valor e a referência corretos.
- Caminhos incluem subpastas e sufixos; URLs incluem o caminho completo. A
  comparação de versões inclui igualdade e distâncias maiores que um.
- Falhas de fonte, conteúdo vazio, ausência do campo, contradições e instruções
  injetadas continuam sendo cobrados. As fontes e os endereços são sintéticos;
  nenhuma consulta externa é executada.

O avaliador foi atualizado para conferir a entidade real do pedido, ignorar
fatos de outro projeto e verificar argumentos completos de consulta. Seu oráculo
deriva o alvo dos dados de entrada, sem ler a resposta de treino. É um avaliador
restrito a essas famílias numéricas, e não um verificador geral de verdade.

## Protocolo

Dois candidatos usam os mesmos dados, tokenizer, semente 42, duas camadas,
dimensão 128, janela 384, batch 12, taxa inicial 0,0015 e orçamento de 1.200
passos. O modelo com cópia tem 494.977 parâmetros, com dimensão de atenção de
cópia 64; o controle sem cópia tem 478.464 parâmetros.

As sequências reais completas chegam a 95 tokens no treino, 92 na validação e
97 no reservado. A alocação de 384 posições não demonstra aprendizado de
contextos desse comprimento; o padding posterior não recebe loss.

Esse controle ajuda a comparar os mecanismos com o mesmo currículo e orçamento.
Há apenas uma semente, e a construção dos módulos adicionais altera o consumo
de números aleatórios na inicialização. Portanto, o resultado não isola todos os
efeitos de inicialização. A comparação com a V2 também muda dados, tokenizer e
arquitetura, e deve ser interpretada como comparação entre candidatos completos.

Snapshots em 400, 800 e 1.200 passos são selecionados exclusivamente pelos
resultados de validação: acertos semânticos, menos propostas inseguras,
consistência dos grupos e, por último, loss. Só então o checkpoint escolhido é
avaliado no reservado. Cada caso recebe uma geração, com orçamento de 192
tokens, sem receitas de resposta e sem ferramentas executadas.

Os critérios de aprovação continuam fixos: 90% no total, 75% em cada família,
75% dos grupos inteiros e zero propostas de resposta incorreta ou sem suporte,
na validação e no reservado. Propostas com referências rejeitadas também contam
como inseguras. Uma janela curta e tarefas sintéticas continuam insuficientes
para qualificar o assistente geral, mesmo se essa bancada restrita passar.

## Resultados e artefatos

| Candidato | Snapshot escolhido | Validação | Reservado V3 | Propostas inseguras no reservado |
| --- | --- | --- | --- | --- |
| V2, comparação anterior | 800 | 63/132 na bancada V2 | 67/264 | 123 |
| V3, controle sem cópia | 800 | 60/132 | 98/264 | 118 |
| V3, com cópia | 1.200 | 73/132 | 122/264 | 79 |

Os treinos duraram 794 segundos com cópia e 652 sem cópia, executados
concorrentemente em CPU. No modelo com cópia, a menor loss de validação ocorreu
na etapa 400 (0,492), mas a seleção de decisões escolheu a etapa 1.200 (loss
0,511). Seus acertos de validação foram 60, 59 e 73 nas três etapas.

| Família reservada | V2 | V3 sem cópia | V3 com cópia |
| --- | --- | --- | --- |
| Extrair valor observado | 0/24 | 0/24 | 1/24 |
| Reconhecer fonte que falhou | 22/24 | 21/24 | 24/24 |
| Reconhecer fonte vazia | 16/24 | 24/24 | 24/24 |
| Ignorar campo de outro projeto | 0/24 | 24/24 | 23/24 |
| Reconhecer contradição | 9/24 | 13/24 | 15/24 |
| Usar uma correção explícita | 0/24 | 0/24 | 5/24 |
| Extrair diante de instrução injetada | 0/24 | 0/24 | 7/24 |
| Consultar o arquivo correto | 0/24 | 0/24 | 1/24 |
| Recuperar por outra fonte | 0/24 | 0/24 | 0/24 |
| Confirmar versão compatível | 2/24 | 16/24 | 16/24 |
| Rejeitar versão incompatível | 18/24 | 0/24 | 6/24 |

O candidato com cópia tem 46,2% de acertos, contra 37,1% no controle e 25,4%
na V2. Em comparação por pedido com o controle, passa em 34 casos que o controle
reprova, mas reprova em 10 que o controle passa: ganho líquido de 24 casos.
Os ganhos não são uniformes; a comparação de versões ainda fica em 22/48.

O pedido inteiro chegou ao decoder em 264/264 casos. O contrato foi validado em
244/264 propostas, mas nenhum dos 24 grupos completos passou. A validação teve
35 propostas inseguras e o reservado, 79. Saídas malformadas também reprovam,
mas não entram nessa contagem de propostas de resposta bem formadas sem suporte.

As falhas continuam concretas: para um valor observado de `80001`, o candidato
respondeu `8000`; numa revisão de `80004`, respondeu `804`. Um caminho pedido
como `ipirangaq.json` virou `ipanqr.json`. Em nove casos com fontes contraditórias,
a decisão correta de bloqueio não foi aprovada. Copiar tokens da fonte não
garante preservar sua ordem, seu comprimento ou sua relação com o pedido.

Um diagnóstico da etapa 800 aprovou 16/22 exemplos já usados no treino, frente a
59/132 na validação daquela etapa. Esse diagnóstico não entrou na seleção e não
é evidência independente de competência. Ele reforça a distinção entre ajustar
exemplos vistos e generalizar para parâmetros novos.

- [Comparação por pedido e hashes dos relatórios](../model/training/cognitive-copy-v3/comparison.json)
- [Auditoria dos artefatos e saídas](../model/training/cognitive-copy-v3/audit.json)
- [Protocolo e hashes das fontes](../model/training/cognitive-copy-v3/protocol.json)
- [Dados e tokenizer V3](../datasets/cognitive_copy_v3/manifest.json)
- [Treino com cópia](../model/training/cognitive-copy-v3/run-01/manifest.json)
- [Treino de controle](../model/training/cognitive-copy-v3/run-no-copy-01/manifest.json)
- [Seleção e resultados com cópia](../model/training/cognitive-copy-v3/run-01/semantic-selection.json)
- [Seleção e resultados do controle](../model/training/cognitive-copy-v3/run-no-copy-01/semantic-selection.json)
- [Candidato V2 na bancada V3](../model/training/cognitive-copy-v3/baseline-v2-heldout.json)
- [Regressão da V2 na bancada original](../model/training/cognitive-copy-v3/regression-v2-original-heldout.json)
- [Candidato com cópia na bancada antiga V2](../model/training/cognitive-copy-v3/regression-copy-on-v2-heldout.json)

## Verificação

A bateria de contratos, decoder, treino, arquitetura, cache e preparação de
dados aprovou **161 testes e 338 subtestes**. A V2 manteve **114/264 acertos e
98 propostas inseguras** em sua bancada original com o avaliador atualizado,
confirmando a preservação dessa avaliação. Na bancada V3, obteve **67/264
acertos e 123 propostas inseguras**.

O novo candidato com cópia também foi avaliado na bancada antiga da V2, após sua
seleção: obteve **115/264 e 58 propostas inseguras**. Apesar do total parecido,
houve regressões: a aprovação em fontes com outro campo do mesmo projeto caiu
de 20/24 para 1/24, e em conflitos de 24/24 para 16/24. O novo treino ensinou
principalmente fontes de outro projeto como caso de ausência; esses dois tipos
precisam coexistir no currículo. A cópia em revisões e instruções injetadas passou
de 0/24 para 14/24 em cada família antiga. Ganhos agregados não garantem a
preservação das capacidades anteriores.

Não houve reinício do serviço nem auditoria HTTP ao vivo nesta rodada.

## Gargalo seguinte

O experimento melhora parte das decisões e algumas cópias, mas o principal
gargalo permanece na seleção e continuação do trecho correto. Um próximo treino
deve supervisionar o alinhamento às posições da origem e medir a cópia de trechos
completos, além de ampliar os contraexemplos de conflito e comparação numérica.
O currículo precisa recuperar também a ausência do campo no próprio projeto,
evitando trocar uma forma de abstenção por outra.
Isso deve continuar separado da promoção e usar novos casos reservados para
avaliar mudanças escolhidas a partir destas falhas. Mais ajuste aos mesmos
modelos ou queda de loss não constitui prova de cognição confiável.

## Reprodução

Na raiz do projeto, gere os dados em um diretório novo. O gerador recusa
sobrescrever dados existentes:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/prepare_cognitive_copy.py \
  --output-dir /tmp/cognitive-copy-v3-reproduction
```

Para repetir o treino em um diretório de saída novo:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python python/finetune_assistant.py \
  --checkpoint model/godmode/context-32768-v1/candidate.safetensors \
  --config-path datasets/cognitive_copy_v3/config.json \
  --tokenizer-path datasets/cognitive_copy_v3/tokenizer.json \
  --output-dir /tmp/cognitive-copy-v3-run \
  --only-jsonl datasets/cognitive_copy_v3/train.jsonl \
  --validation-jsonl datasets/cognitive_copy_v3/validation.jsonl \
  --heldout datasets/cognitive_copy_v3/heldout.jsonl \
  --steps 1200 --batch-size 12 --threads 4 --learning-rate 0.0015 \
  --max-answer-tokens 192 --sample-tokens 0 --snapshot-every-eval \
  --eval-every 400 --patience 4 --without-profile --from-scratch
```

O controle usa os mesmos argumentos, trocando a configuração por
`model/training/cognitive-copy-v3/no-copy-config.json` e o diretório de saída.
Para selecionar e avaliar os snapshots do treino salvo:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/select_cognitive_sft.py \
  --run-dir model/training/cognitive-copy-v3/run-01 \
  --validation datasets/cognitive_copy_v3/validation.jsonl \
  --heldout datasets/cognitive_copy_v3/heldout.jsonl
```

Esse comando atualiza relatórios, termina com código 1 quando os critérios
reprovam e nunca ativa pesos no chat. Para conferir as regressões:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_cognitive_copy_data.py tests/test_neural_copy.py \
  tests/test_cognitive_sft.py tests/test_cognitive_dialogue.py \
  tests/test_model_server_agentic.py tests/test_dialogue_api.py \
  tests/test_generation_utils.py tests/test_finetune_assistant.py \
  tests/test_release_evidence.py tests/test_context_kv_cache.py \
  tests/test_model_initialization.py
```
