# Terceira rodada: formato de arquivos e seleção por qualidade

**Data:** 29/09/2026. **Estado:** nenhum candidato promovido.

## Formato de dois arquivos

O [conjunto experimental](../datasets/implementation_markdown_probe_v1/manifest.json)
transformou os mesmos 20 exemplos de treino em `### app.py` e
`### test_app.py` com blocos de código. Isso retirou o escape de conteúdo
dentro de JSON, mantendo o parser de operações e testes de referência como
critério final. A avaliação permitiu observar a saída antes do filtro de
relevância de prosa; ainda exigiu blocos completos, caminhos corretos e código
que passasse nos testes.

O candidato selecionado pelo melhor valor de perda (passo 300) produziu 0/20
planos válidos no treino e 0/2 nos pedidos inéditos. As saídas repetiram
trechos de `unittest` e misturaram funções. O formato mais simples não resolveu
a geração. [Treino](../model/training/implementation-sft-v1/markdown-01/train_probe.json)
e [pedidos inéditos](../model/training/implementation-sft-v1/markdown-01/heldout_evaluation.json).

## Seleção por comportamento

Uma nova [partição fixa](../datasets/implementation_sft_compact_extended_v2/manifest.json)
separou 17 casos para treino, quatro para seleção e manteve os mesmos dois
pedidos inéditos. O treinador guardou snapshots nos passos 80, 160, 240, 320 e
400. O [seletor](../scripts/select_implementation_checkpoint.py) pontuou cada
snapshot primeiro pelos testes de referência, depois pela validade do contrato
e do JSON, usando perda só para desempate. Não consultou os pedidos inéditos
para escolher.

O [resultado da seleção](../model/training/implementation-sft-v1/quality-selection-01/quality_selection.json)
escolheu o passo 400: 0/4 testes, 1/4 contratos válidos, 1/4 JSON válidos.
O [resultado final reservado](../model/training/implementation-sft-v1/quality-selection-01/heldout_evaluation.json)
permaneceu em 0/2 JSON, 0/2 contratos e 0/2 verificações executadas. A perda
reservada caiu para 3,99, mas isso não representa melhora funcional.

## Decisão

O ganho da segunda rodada continua restrito aos exemplos vistos: 2/4 JSON e
1/4 código aprovado. Nenhuma das novas variantes aumentou a taxa inédita.
Continuar ajustando o mesmo conjunto pequeno ou trocar somente o formato de
saída não justifica promoção. O próximo experimento deve mudar a qualidade e a
escala da fonte de treino ou a capacidade do gerador, mantendo a seleção por
comportamento e o conjunto inédito fechado.

O corpus local Code-Agent tem aproximadamente 69.895 trajetórias, mas inclui
conversas longas com chamadas de ferramentas e saídas de testes. Ele não é um
conjunto de propostas de arquivos prontas: antes de treinar, seria necessário
extrair, revisar e verificar exemplos sob o contrato de Brasa.

Uma leitura inicial do primeiro shard encontrou 2.330 trajetórias; 2.327 têm
ao menos uma chamada `apply_patch` e um comando de teste. Isso indica uma fonte
potencial de edições reais, mas **não** comprova que cada teste passou nem que
o patch pode ser convertido com segurança em `old_text` e `new_text`. O próximo
preparador deve associar patch, trecho-fonte observado e resultado posterior,
descartar casos ambíguos e passar pelo parser do runtime antes de marcar uma
amostra como apta ao treino.

Nesse shard, 2.321 trajetórias editam arquivo existente e 191 contêm alguma
criação de arquivo; há sobreposição. A primeira criação inspecionada foi um
`/app/reproduce.py` vazio usado para reproduzir uma falha, não uma entrega.
Portanto, contar chamadas `Add File` não basta para alimentar o treino.
