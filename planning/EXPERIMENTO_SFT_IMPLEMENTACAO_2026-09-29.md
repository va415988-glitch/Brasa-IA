# Experimento inicial de treino para propostas de implementação

**Data:** 29/09/2026  
**Fonte:** `model/godmode/context-32768-v1/candidate.safetensors`  
**Candidato:** `model/training/implementation-sft-v1/memorization-01/candidate.safetensors`  
**Estado:** experimental; não promovido.

## Contrato e dados

O gerador de [dados](../scripts/prepare_implementation_sft_v1.py) usa o
`implementation_prompt` real e produz JSON com `assumptions` e duas operações
`create_file` (`app.py` e `test_app.py`). Cada plano passou pelo parser do
runtime. O código de referência passou em `unittest` num workspace temporário.
Os splits ficam em [implementation_sft_v1](../datasets/implementation_sft_v1/README.md):
quatro pedidos de treino, um de validação e dois inéditos para avaliação final.
Nenhum pedido reservado entrou no otimizador.

Os prompts têm 848–863 tokens e as respostas, 128–181 tokens com o tokenizer
ativo. O treino anterior usava apenas 512 tokens de contexto. Este ensaio
reservou 2048 para que o pedido e a resposta caibam na mesma janela.

## Execução e resultado

O [treinador](../python/finetune_assistant.py) recebeu apenas esses quatro
exemplos, continuou os pesos ativos e registrou validação fixa. O ensaio de
30 passos baixou a perda reservada de 8,93 para 7,78, mas falhou em 2/2
pedidos inéditos e 4/4 exemplos de treino. O ensaio de 240 passos chegou a:

| Medida | Fonte | Candidato |
| --- | ---: | ---: |
| Perda de validação | 8,91 | 5,37 |
| Perda nos dois pedidos inéditos | 8,93 | 4,66 |
| JSON válido nos quatro pedidos de treino | 0/4 | 0/4 |
| JSON válido nos dois pedidos inéditos | 0/2 | 0/2 |
| Propostas que passaram nos testes de referência inéditos | 0/2 | 0/2 |

Os resultados de geração estão em
[train_probe.json](../model/training/implementation-sft-v1/memorization-01/train_probe.json)
e [heldout_evaluation.json](../model/training/implementation-sft-v1/memorization-01/heldout_evaluation.json).
A avaliação limitou a saída a 384 tokens; as respostas de referência exigem no
máximo 181. Um trecho bruto de um pedido visto no treino começa com
`{"tool":"create_file","arguments":{"path":"create_file","arguments":...`
e repete esse padrão até o limite. Também misturou conteúdo de outra função.
Isso explica por que a queda da perda não se converteu em um plano executável.

## Defeitos de infraestrutura corrigidos durante o ensaio

1. Um candidato ajustado herdava o metadado de extensão que exige pesos iguais
   aos de origem. Após treino, o runtime o recusava. O treinador agora remove
   esse vínculo, não herda a verificação antiga e limita o contexto anunciado
   ao contexto efetivamente treinado.
2. A compactação da inferência reservava toda a saída de 3072 tokens dentro de
   uma janela treinada de 2048, sobrando apenas 16 tokens para o pedido. A
   reserva agora usa no máximo um quarto da janela, pois o decoder usa janela
   deslizante. O prompt completo de 849 tokens chegou ao modelo no diagnóstico.

## Conclusão e gate

O pipeline agora prepara exemplos válidos, treina um candidato isolado e mede
validade estrutural e comportamento executado em tarefas inéditas. O candidato
**não passou nem no ensaio de memorização**, portanto não deve substituir o
checkpoint ativo. A próxima rodada precisa de mais exemplos revisados e maior
diversidade de operações e projetos. Antes de ampliar o corpus, é útil fazer
um ensaio em que o modelo reproduza integralmente alguns planos vistos; só
depois faz sentido atribuir ganho de generalização a pedidos inéditos.

O conjunto atual é pequeno e composto de funções Python em dois arquivos. Ele
testa o protocolo mínimo, não a competência de construir aplicativos completos.

Os testes focados do treinador e da avaliação do serviço passaram: 11 testes e
28 subcasos. O teste que ainda esperava o antigo orçamento padrão de 1024
tokens foi atualizado para 2048, conforme a configuração atual.

A [segunda rodada](EXPERIMENTO_SFT_IMPLEMENTACAO_RODADA2_2026-09-29.md)
preservou o prompt literal na inferência e testou um prompt compacto e um
conjunto de treino maior. Houve ganho nos exemplos vistos, mas os pedidos
inéditos ainda não passaram.
