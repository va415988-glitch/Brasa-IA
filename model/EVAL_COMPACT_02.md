# Avaliação do compact-02-mixed

## Resultado

O checkpoint foi treinado do zero com `model/train_tokens.bin`, contendo 91
registros misturados e 1.148.864 tokens. Em 1.000 passos, a perda final foi
3,9286.

Ele não foi promovido. Em quatro gerações livres, apresentou marcadores
quebrados, repetições e respostas sem relação com a pergunta. A perda menor não
foi suficiente para demonstrar qualidade de resposta.

O runtime continua usando o comportamento seguro do `compact-01` combinado com
memória curada, recuperação local e quality gate.

## Próxima tentativa

- separar treino e validação;
- mascarar tokens de controle durante a geração;
- aumentar exemplos de diálogo e respostas completas;
- reduzir repetição artificial do corpus;
- medir perplexidade de validação e um conjunto de perguntas reservado;
- só promover um checkpoint se ele superar o baseline em qualidade e não
  produzir marcadores ou texto corrompido.
