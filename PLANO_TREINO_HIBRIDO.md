# Plano de treinamento híbrido

## Decisão

O assistente deve funcionar localmente mesmo quando nenhum serviço externo estiver disponível. Serviços externos podem acelerar experimentos de treinamento, mas não serão necessários para conversar, pesquisar, consultar o workspace ou executar ferramentas.

## Divisão dos componentes

### Permanecem locais

- runtime Rust e servidor HTTP;
- permissões, workspace, edição e backups;
- chamadas de ferramentas e eventos em tempo real;
- memória de conversa e histórico;
- índice lexical e futura busca vetorial;
- RAG com documentos do usuário;
- avaliação, validação de respostas e quality gate.

### Podem usar máquina externa

- treinamento de uma rede maior do zero;
- fine-tuning supervisionado;
- destilação para um modelo menor;
- comparação de tokenizadores e arquiteturas;
- experimentos de quantização.

Nenhum artefato externo entra em produção automaticamente. Um checkpoint só pode ser usado depois de passar pela avaliação de geração, perguntas de ferramentas, segurança, português, latência e consumo de memória.

## Ordem de evolução

1. Aumentar o acervo local com origem, licença, idioma, categoria, hash e data.
2. Melhorar o índice e adicionar embeddings locais somente quando a avaliação mostrar que a busca lexical deixou de ser suficiente.
3. Expandir exemplos de diálogo, programação, ferramentas, recusa e correção.
4. Separar conjuntos de treino, validação e teste por origem, evitando que uma resposta quase idêntica apareça nos dois lados.
5. Treinar localmente pequenos componentes especializados.
6. Usar GPU externa apenas para experimentos que excedam o orçamento local.
7. Destilar ou quantizar um candidato aprovado para execução no computador.

## Critérios de promoção

Um candidato precisa demonstrar:

- melhora em perguntas reservadas, sem decorar as respostas;
- chamadas corretas de ferramentas e argumentos válidos;
- nenhuma fabricação de fontes ou resultados;
- preservação de contexto e continuação de conversa;
- ausência de marcadores quebrados, repetição e saída vazia;
- p95 abaixo de 30 segundos no computador alvo;
- consumo de memória documentado;
- possibilidade de reverter para o backend anterior.

Perda de treinamento menor, sozinha, não aprova um checkpoint.

## Privacidade

Documentos do workspace, histórico, credenciais e anexos do usuário não devem ser enviados a serviços externos automaticamente. Um futuro exportador de dataset deverá selecionar dados explicitamente, remover segredos, mostrar o conteúdo que será enviado e produzir um manifesto auditável.

## Estado atual

O projeto já possui runtime Rust, análise estática de anexos, memória curada, acervo lexical e quality gate. Os checkpoints compactos disponíveis foram reprovados para geração livre. A próxima rodada deve aumentar dados e qualidade da avaliação antes de consumir uma nova rodada longa de CPU ou GPU.
