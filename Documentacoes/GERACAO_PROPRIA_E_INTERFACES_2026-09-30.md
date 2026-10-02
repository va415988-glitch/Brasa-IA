# Geração própria de código e interfaces — 30/09/2026

Foram corrigidas falhas reais no treino e no decoder e foi treinado um candidato próprio, desde a inicialização, usando somente recursos locais. **Ele ainda não demonstrou generalização suficiente para ser ativado.** A avaliação completa aprovou 2/4 pedidos conhecidos e 0/3 pedidos reservados. O checkpoint do chat continua sendo o anterior; as correções do runtime foram carregadas na aplicação.

## Correções aplicadas

- Respostas longas podem ser treinadas com `--preserve-prompt`: cada janela conserva uma âncora do pedido original, e cada token da resposta recebe supervisão uma vez. O modo anterior podia treinar a continuação sem seu pedido.
- Nesse modo, o treinador recusa respostas acima do orçamento, em vez de cortar código e ensinar um EOS artificial. Um orçamento zero preserva a resposta inteira.
- Treino e inferência compartilham a serialização dos papéis e a geometria das janelas. O candidato não passa por uma segunda compactação que altera o formato aprendido.
- O cache KV é reconstruído no ponto usado pelo treino. Antes, a primeira reconstrução acontecia um token depois desse limite, deslocando as posições da continuação. Um teste verifica os tokens de cada reconstrução.
- Planos de implementação usam penalidade de repetição padrão 1,0. Repetir identificadores e estruturas JSON é necessário em código. As validações de forma, caminhos, conteúdo e execução continuam exigidas.
- O relatório de novos treinos desde a inicialização deixa de confundir os pesos da última etapa com a linha de base.

As mudanças estão em `python/conditioned_context.py`, `python/finetune_assistant.py` e `python/model_server.py`. A geração experimental utiliza esse mesmo decoder, sem receitas para os casos da bancada.

## Experimento próprio

O conjunto `datasets/programming_studio_v1` contém 112 exemplos autorais de treino: 96 funções pequenas e 16 interfaces. Há três pedidos de validação e três reservados, disjuntos dos pedidos de treino. O tokenizer foi ajustado somente ao treino.

O candidato possui 1.358.976 parâmetros, três camadas, dimensão 192, vocabulário 768 e contexto efetivo de 512 tokens. Recebeu 900 passos de treino em CPU, em aproximadamente 15 minutos. Nenhum modelo externo foi instalado ou utilizado.

As referências de interface exploram composição editorial, terminal, painel dividido e caderno. Todas são variações de **um contador local**, com persistência, estados de erro, teclado, foco e responsividade. Isso é material de treino; não demonstra domínio de outros produtos ou criação visual aberta. A galeria `prototipos/programming-studio-referencias/index.html` identifica explicitamente que suas páginas foram escritas para treino.

## Avaliação e resultados

O avaliador executa os testes escritos pelo candidato e testes funcionais independentes. Para o contador web, executa sete interações em um DOM simulado, incluindo restauração, incremento, decremento, reset, prevenção de valores negativos e indisponibilidade de armazenamento.

Uma tela também precisa preservar o nome e a direção visual do pedido, oferecer elementos semânticos, foco, viewport e CSS com delimitadores íntegros. A verificação de delimitadores **não é um parser CSS completo**, e o DOM simulado não confirma renderização, contraste ou acessibilidade completa.

| Bancada | Resultado | Interpretação |
| --- | --- | --- |
| Smoke anterior à correção do cache, quatro pedidos de treino | 2/4 | As duas interfaces falhavam nas interações |
| Smoke depois da correção, sem exigir o briefing visual completo | 4/4 | Interações passaram; resultado insuficiente para aprovar interfaces |
| Critério completo, mesmos quatro pedidos conhecidos | 2/4 | Funções passaram; interfaces trocaram nomes e/ou corromperam CSS |
| Validação funcional de nove snapshots | 0/3 em todos | Menor loss não virou código correto nos pedidos novos |
| Reservado, depois de escolher o snapshot pela validação | 0/3 | `even_total`, `short_words` e o briefing `Ritual criativo` reprovaram |
| API do chat, após reiniciar com as correções | 8/9 | Operações passaram; implementação inédita de `interval_union` continua falhando |

A inspeção em navegador confirmou os limites: a interface editorial gerada ficou quase sem estilo; a composição azul e laranja renderizou em 375 px sem overflow horizontal, mas trocou o título da página. Esses arquivos são saídas reais do modelo e permanecem experimentais em `model/training/programming-studio-v1/previews/step-0900`. Não foram corrigidos manualmente para alterar a nota.

A seleção prioriza testes executados na validação e usa loss somente para desempatar. O conjunto reservado é executado depois da escolha e não entra no otimizador nem escolhe o checkpoint. O snapshot 600 foi selecionado por desempate de loss entre candidatos reprovados; **selecionado não significa aprovado**. Nenhuma promoção aconteceu.

Passaram 112 testes Python nesta rodada: 10 do treinador, 7 do decoder, 9 da bancada e seleção, 5 dos filtros e 81 do fluxo do servidor. As duas regressões da interface para streaming e preservação do contexto também passaram.

## Teste inicial reproduzível

Na raiz do projeto, execute:

```bash
.venv/bin/python scripts/evaluate_programming_studio.py \
  --checkpoint model/training/programming-studio-v1/run-01/snapshots/step-0600.safetensors \
  --cases datasets/programming_studio_v1/heldout.jsonl \
  --output /tmp/programming-studio-heldout.json
```

O resultado atual esperado é **0/3 e código de saída 1**. Isso mede geração neural, contrato e comportamento sem instalar os alvos como receitas. O script `scripts/select_programming_studio.py` reproduz a seleção pelos snapshots e também termina com código 1 quando o candidato não se qualifica.

Relatórios principais:

- `model/training/programming-studio-v1/run-01/functional-selection.json`
- `model/training/programming-studio-v1/run-01/train-probe-final-step-0900.json`
- `model/training/programming-studio-v1/run-01/report-corrected.json`
- `avaliacoes/programming-studio-runtime-2026-09-30.json`

O relatório original de loss é preservado. Sua linha de base estava rotulada incorretamente no modo desde a inicialização; a versão corrigida identifica a loss inicial real e deixa a loss reservada da linha de base como indisponível. Nenhuma comparação semântica depende dessas losses.

## Limite restante

As falhas novas também aparecem ao testar os pesos diretamente, sem a API do chat. Há uma lacuna de aprendizado e generalização, além dos problemas de runtime corrigidos. O candidato aprende estruturas e comportamentos vistos, mas ainda erra símbolos, requisitos novos e código visual. Esta entrega cria um processo verificável de treino e avaliação; não transforma o modelo em mestre de programação nem comprova criatividade geral. O avanço seguinte precisa melhorar esse aprendizado e ser demonstrado em funções, reparos e produtos de interface novos, sem alimentar o treino com as respostas da bancada reservada.
