# Avaliação do compact-03-validated

## Resultado

Este candidato usou 82 registros de treino e 9 registros inteiros reservados
para validação. Foram 284.524 tokens de treino e 673 tokens de validação. Em
1.200 passos, a menor perda de validação observada ficou perto de 5,63.

Mesmo com a validação melhor que a tentativa anterior, a geração livre falhou
nas quatro perguntas: repetiu marcadores como `<|assistant|>`, produziu texto
corrompido e não respondeu ao conteúdo solicitado. O checkpoint não foi
promovido e o runtime continua no baseline seguro.

## Decisão

Perda de token não será usada como critério único. A próxima rodada precisa
validar também texto gerado, ausência de marcadores de controle, repetição,
resposta ao assunto e preservação das regras de abstinência.
