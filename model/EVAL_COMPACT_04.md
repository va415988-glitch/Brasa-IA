# Avaliação do compact-04-mixed-tokenizer

## Resultado

Este candidato usou o tokenizer treinado no corpus misturado, 82 registros de
treino e 9 de validação. O lote teve 48.958 tokens de treino e 667 de
validação. A menor perda de validação observada foi aproximadamente 5,55.

A geração livre falhou nas quatro perguntas: houve repetições, palavras
corrompidas e respostas sem relação suficiente com o assunto. O checkpoint não
foi promovido.

## Conclusão

O gargalo deixou de ser apenas a ingestão ou o tokenizer. A rede pequena,
treinada como modelo causal em um corpus ainda curto e heterogêneo, não está
aprendendo uma política conversacional confiável. O runtime continuará usando
recuperação local, memória curada e quality gate.
