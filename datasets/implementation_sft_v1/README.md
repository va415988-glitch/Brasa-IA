# Experimento de propostas de implementação v1

Este conjunto testa se o checkpoint próprio aprende o contrato usado pela etapa
`implementation_prompt` → `parse_implementation_plan`. Cada resposta é um objeto
JSON com `assumptions` e `operations` que cria `app.py` e `test_app.py`.

`scripts/prepare_implementation_sft_v1.py` gera os três splits. Os sete casos
foram escritos para este experimento; o gerador valida o JSON com o parser real
e executa os testes de referência em diretórios temporários. O manifesto guarda
contagens, comprimentos com o tokenizer ativo e hashes dos arquivos.

- Treino: quatro pedidos de funções Python.
- Validação: um pedido diferente, usado para escolher o passo do checkpoint.
- Avaliação reservada: dois pedidos adicionais, consultados somente depois do treino.

Este é um ensaio de protocolo e memorização, não uma amostra representativa de
projetos completos. Um modelo que passa nesses sete casos ainda precisa de
dados diversos de edição, integração, recuperação e projetos maiores. A
avaliação reservada usa `test_app.py` de referência, independentemente dos
testes que o modelo propuser. O checkpoint ativo não é substituído pelo script.

O treino precisa de pelo menos 2048 tokens de contexto: as entradas reais têm
cerca de 850 tokens, antes da resposta. A inferência mantém um orçamento de
entrada que preserva esse pedido mesmo quando o orçamento de saída é maior que
a janela treinada.
