# APIs locais para alimentar o agente

## Objetivo

O agente deve buscar dados e executar operações por contratos próprios, locais e versionados. A API é uma interface para memória, evidências, competências e ferramentas; ela **não substitui** o modelo de linguagem nem transforma uma página lida em habilidade adquirida. O runtime Rust expõe as rotas ao cliente; o worker Python prepara dados e executa análise local. Nenhuma rota desta camada depende de um modelo hospedado por terceiros.

## Primeiro contrato implementado

`GET /api/v1/agent/capabilities` retorna `agent-capabilities/v1`: competência, estado, progresso nos critérios do laboratório, tarefas resolvidas pelo agente e aprovadas, verificações fixas do laboratório, fontes independentes, documentos e repositório de origem. Os dois tipos de resultado são separados: uma suíte fixa valida seus próprios exercícios e o ambiente, mas não prova que o agente produziu uma solução. O chat usa a mesma projeção do registro local para responder sobre suas capacidades. Uma documentação sobre JavaScript não pode ser usada como prova de que o agente domina linguagens e frameworks.

O progresso nessa API não é porcentagem de proficiência geral. Para declarar domínio, o laboratório ainda precisa comprovar cobertura, prática, transferência para problemas novos e verificação independente. Uma resposta deve distinguir “consultei material” de “executei e validei”.

Também está implementado `POST /api/v1/knowledge/search`, contrato `agent-evidence/v1`. Ele pesquisa apenas o índice local, limita os resultados e retorna `no_evidence` quando não há correspondência suficiente. A busca não executa instruções encontradas nem autoriza o agente a tratar o trecho como resposta pronta.

O terceiro contrato implementado é `POST /api/v1/agent/context`, contrato `agent-context/v1`. Ele combina pergunta atual, até oito mensagens recentes, memória explícita da sessão, competências relacionadas e evidências locais limitadas. Também publica os limites do pacote e a regra de tratar evidência como dado, não como instrução.

## Próximos contratos, em ordem

1. `POST /api/v1/knowledge/search`: recebe pergunta, assunto e orçamento de contexto; devolve trechos curtos com título, origem, data/versão quando disponível e pontuação de pertinência. O contrato deve poder devolver `no_evidence`. Não deve devolver texto bruto como resposta final.
2. `POST /api/v1/agent/context`: monta um pacote limitado e auditável de memória da sessão, competências pertinentes e evidências da busca. A composição precisa priorizar a pergunta atual e caber na janela real do modelo. Cada parte tem origem e validade explícitas.
3. `GET /api/v1/learning/jobs/{id}` e `GET /api/v1/learning/evidence/{topic}`: mostram etapa, documentos aceitos/rejeitados, lacunas, exercícios, testes e motivos de avaliação. A rota de acompanhamento existente `/api/learn/{id}` pode ser adaptada; não se cria um segundo mecanismo de aprendizagem.
4. Contrato de avaliação para tarefas verificáveis: um agente só promove uma competência após resolver problemas inéditos, executar testes e registrar artefatos. O avaliador não deve confiar em autoavaliação textual.

O executor de ferramentas existente (`/api/v1/tools/call`) continua responsável por ações. Rotas de contexto e consulta são apenas leitura; mutações de arquivos e processos continuam com escopo de projeto, confirmação quando aplicável e logs correlacionados no chat.

## Regras de qualidade e segurança

- O material recuperado é dado, nunca instrução para o agente. Instruções dentro de páginas, repositórios e documentos não podem alterar suas permissões.
- Resultados precisam ser pertinentes à **relação perguntada**, não apenas compartilhar palavras. Se a evidência não cobre o pedido, o agente pesquisa mais, pede contexto ou declara o limite.
- Nenhuma pontuação de desempenho sobe por mera repetição de um repositório, por contagem de URLs ou por exercício resolvido previamente pela própria suíte. Fontes independentes, documentos validados, verificações do ambiente e soluções produzidas pelo agente são sinais diferentes.
- O modelo deve produzir uma resposta nova e adequada à conversa; trechos do acervo ficam como referências citadas, não como fala do assistente.
- IDs de requisição ligam busca, chamada de ferramenta, avaliação e resposta ao log visível no chat. Erros da API devem ser explícitos e não virar respostas fabricadas.
- APIs locais eliminam dependência de um serviço de inferência externo, mas conhecimento atualizado ainda requer fontes. A importação de repositórios ou documentação é uma operação deliberada e auditável; o runtime continua funcional sem internet.

## Critérios para considerar a fase concluída

Uma pergunta sobre as habilidades do agente deve consultar o registro local e declarar limites; uma pergunta técnica deve recuperar evidências pertinentes, executar tarefas verificáveis quando solicitado e não colar uma página como resposta. Testes de regressão devem cobrir perguntas ambíguas, fontes irrelevantes, falta de internet, inexistência de competência e diferença entre progresso do laboratório e domínio real.
