# Avaliação do compact-06-augmented-v2

- dados: corpus misturado com comportamento aumentado;
- passos: 1.200;
- perda final de treino: 3,8085;
- melhor perda de validação observada: 3,9507;
- avaliação de geração: 0/4;
- decisão: reprovado para geração livre.

O checkpoint reduziu a perda de validação, mas ainda produz saída vazia,
marcadores de controle e repetição. O baseline e o quality gate continuam
ativos. O checkpoint fica preservado para análise, sem ser usado pelo worker.
