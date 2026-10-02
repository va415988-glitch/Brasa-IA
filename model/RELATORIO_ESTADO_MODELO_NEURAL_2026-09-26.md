# Relatório do estado atual do modelo neural

**Data da inspeção:** 26/09/2026  
**Escopo:** estado, pesos e metadados do checkpoint ativo, mais um treino experimental isolado em contexto 2.048 e avaliação neural direta antes/depois. O checkpoint ativo não foi alterado.

## Parecer

O projeto possui um Transformer causal próprio que carrega e executa, mas as evidências disponíveis **não demonstram geração livre coerente**. O checkpoint selecionado como ativo continua experimental; ele ainda não deve ser tratado como assistente neural autônomo ou como modelo de uso geral.

Há componentes úteis ao redor dele — respostas curadas, regras de conversa, busca, API e ferramentas — que podem produzir respostas corretas em situações cobertas. Esses resultados são do sistema composto, não prova de que os pesos neurais aprenderam a compreender perguntas.

## Checkpoint selecionado

`start.sh` consulta `model/godmode/state.json` e, enquanto o estado estiver ativo e o arquivo existir, seleciona `model/godmode/context-32768-v1/candidate.safetensors`.

| Propriedade | Estado observado |
| --- | --- |
| Arquitetura | Transformer causal decoder-only |
| Parâmetros | 6.688.256 |
| Vocabulário | 8.192 tokens |
| Camadas / dimensão / cabeças | 2 / 128 / 4 |
| Janela declarada no runtime | 32.768 tokens |
| Contexto usado no treino | 512 tokens |
| Limite de geração configurado | 4.096 tokens |
| Treinamento de origem | SFT local, do zero, sem Ollama ou outro LLM |
| Pesos | Safetensors, aproximadamente 25,5 MiB |

O treino de origem registrou 404 exemplos de treino, 30 de validação e 30 reservados, em 1.500 passos. A validação caiu de loss 9,0437 na inicialização para 3,9019. A loss reservada do candidato foi 3,7211, igual ao valor de baseline registrado; portanto, a redução de loss não demonstra ganho semântico fora da amostra.

Uma contagem com o tokenizer do checkpoint sobre esses 464 exemplos mostra que a janela de treino quase não foi ocupada: a sequência completa teve mediana de 48 tokens, p95 de 82 e máximo de 121. Nenhum exemplo chegou a 512 tokens. Portanto, não basta trocar `training_context_length` de 512 para 1.024: sem exemplos maiores, o modelo continuará recebendo praticamente o mesmo sinal de treino.

## O que foi verificado

O relatório `context_verification.json` confirma que o checkpoint ativo consegue executar uma entrada de 32.764 tokens e gerar mais 4, com logits finitos. A execução levou 20,9 segundos e registrou pico de 747,4 MB de memória. Isso verifica suporte operacional à janela configurada. **Não verifica compreensão ou retenção semântica em 32 mil tokens.** O próprio artefato registra `trained_context_tokens: 512` e `semantic_quality_evaluated: false`.

O aumento da janela foi feito por interpolação linear dos embeddings posicionais treinados. O manifesto diz explicitamente que o candidato não recebeu fine-tuning no tamanho alvo. A extensão torna o comprimento executável, mas não acrescenta, por si só, aprendizado sobre textos longos.

## Geração direta dos pesos

O relatório de treino do checkpoint de origem contém 30 respostas geradas diretamente pelos pesos. Em 24 casos a saída atingiu o limite de 128 tokens; duas saídas ficaram vazias. As amostras registradas repetem fragmentos e palavras sem formar respostas compreensíveis. Esse relatório avalia a origem antes da extensão posicional para 32.768, portanto não substitui uma avaliação do arquivo ativo exato; a extensão atual não tem avaliação semântica registrada.

Também existe um candidato separado em `model/training/senior-creative-v2`, avaliado em oito pedidos reservados: **0/8 aprovados**, com saídas vazias ou incoerentes. Ele não foi promovido e não é o checkpoint ativo; serve como evidência adicional de que reduzir loss ainda não resolveu a geração.

## Experimento de treino em contexto 2.048

Foi criado um conjunto sintético com quatro tipos de tarefa — planejamento, diagnóstico de incidentes, atendimento e leitura de experimentos. As entradas completas ficaram entre **1.556 e 1.787 tokens** (mediana 1.695), acima de 1.024 e dentro do contexto de treino 2.048. O conjunto contém 96 exemplos de treino, 16 de validação, 8 casos reservados para loss e 24 casos inéditos para avaliação direta. Os exemplos foram escritos localmente; não contêm dados privados nem conteúdo gerado por outro modelo.

O ajuste continuou os pesos do checkpoint ativo por 200 passos, em CPU, sem promoção automática. A loss de validação caiu de **9,259 para 8,129** (12,2%); a loss nos oito casos reservados caiu de **9,181 para 7,601** (17,2%). São ganhos de previsão dos tokens esperados nesses conjuntos. Eles não se converteram em respostas úteis: nas oito respostas livres internas, o candidato emitiu só um token (uma quebra de linha) antes de encerrar.

| Avaliação neural direta | Checkpoint ativo | Candidato 2.048 |
| --- | ---: | ---: |
| Casos aprovados | 0/24 | 0/24 |
| Principal motivo de rejeição | repetição (24/24) | resposta curta demais (24/24) |
| Tokens gerados, mediana | 15 | 2 |

Nos 24 casos novos, ambos os checkpoints carregaram e responderam pelo caminho dos pesos locais, sem memória ou respostas curadas. O ativo caiu em repetição; o candidato passou a encerrar quase imediatamente. Portanto, **a loss melhorou e os pesos aprenderam a interromper o ciclo repetitivo, mas a bateria não detectou ganho de coerência, relevância ou completude**. O resultado de qualidade continua 0/24.

O candidato está em `model/training/neural-context-2048-v1/run-01/candidate.safetensors`; o relatório do ajuste e as duas baterias estão na pasta experimental. Ele não foi ativado. Depois do ajuste, o teste operacional preencheu os **32.768 tokens**, gerou quatro tokens e obteve logits finitos em 26,1 segundos, com pico de 720,2 MB. Isso confirma que a janela executa com os pesos novos. O treino supervisionado cobriu sequências de até 2.048 tokens; a qualidade semântica em 32.768 continua sem avaliação.

## Como interpretar os resultados “100/100”

O `verification_100.json` da origem marcou 100/100 verificações. Isso é um resultado dos gates operacionais e dos critérios definidos pelo auditor, não uma nota de compreensão neural. As 12 sondagens chamadas de neurais foram respondidas pelo backend `local-competence-dataset` em todos os casos. O código consulta primeiro esse adaptador de correspondência exata e considera suas respostas aprovadas; nessa avaliação, os pesos não produziram as respostas das sondagens.

Há ainda uma diferença de artefatos: o estado atual aponta para o checkpoint estendido de 32.768 tokens, mas o campo `report` aponta para a auditoria da origem de 16.384 posições, gerada antes da extensão. Para o checkpoint ativo exato existe a verificação mecânica da janela, não uma nova auditoria semântica de 100 casos.

O benchmark conversacional mais recente marcou 100% de nota composta, mas seus próprios contadores atribuem 47/50 respostas a `local-conversation-rules`, duas a `session-memory` e uma ao Ollama. Essa nota descreve o comportamento da aplicação com seus fallbacks; não mede os pesos neurais isolados.

## O que o modelo demonstra hoje

- **Demonstrado:** o checkpoint carrega, o runtime aceita a janela configurada e existe um pipeline local reproduzível de treinamento e serialização.
- **Demonstrado em classes estreitas:** respostas curadas por correspondência exata, regras explícitas, recuperação local e contratos de ferramentas.
- **Não demonstrado:** geração livre confiável, generalização para paráfrases e perguntas novas, conversa longa neural, qualidade de saída próxima de 4.096 tokens ou compreensão multimodal pelos próprios pesos.
- **Conclusão de uso:** mantenha o checkpoint como experimento e identifique as respostas do sistema pelo backend que as produziu. “Ativo” no estado God Mode significa que o arquivo foi selecionado operacionalmente; não equivale a aprovação de qualidade neural.

## Próximo gargalo

O conjunto longo mostrou que loss menor, sozinha, é uma estatística insuficiente. O próximo ajuste precisa ensinar o modelo a iniciar uma resposta informativa e sustentá-la antes de emitir EOS, sem voltar aos ciclos repetitivos. A comparação deve continuar chamando `tests/benchmark_neural_generation.py` diretamente e medir casos inéditos, relevância, legibilidade, repetição, término prematuro e abstenção quando faltar evidência. O candidato também precisa passar por uma verificação operacional própria de 32.768 antes de qualquer promoção.

O gargalo atual continua na geração direta dos pesos. O checkpoint já selecionado continua ativo; o candidato experimental de 2.048 não foi ativado. A próxima melhoria precisa transformar o alcance de contexto em respostas completas e pertinentes.

## Artefatos consultados

- `model/godmode/state.json`
- `model/godmode/context-32768-v1/candidate.safetensors.json`
- `model/godmode/context-32768-v1/context_verification.json`
- `model/godmode/training-neural-v1/manifest.json`
- `model/godmode/training-neural-v1/report.json`
- `model/godmode/training-neural-v1/verification_100.json`
- `scripts/godmode.py` e `python/neural_adapter.py`
- `model/training/senior-creative-v2/run-01/candidate.safetensors.evaluation.json`
- `scripts/build_neural_context_2048.py` e `model/training/neural-context-2048-v1/data/build_manifest.json`
- `model/training/neural-context-2048-v1/run-01/report.json`
- `model/training/neural-context-2048-v1/run-01/context_verification_32768.json`
- `model/training/neural-context-2048-v1/neural-baseline.json` e `neural-candidate.json`
- `logs/dialogue-evaluations/run-20260926T160728730781Z.json`
