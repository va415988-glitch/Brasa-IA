# Checklist para um agente que realmente presta

**Revisão:** 02/10/2026  
**Escopo:** entendimento, conversa, uso de ferramentas, programação, continuidade, segurança e prova de competência.

Este checklist separa capacidade do software de capacidade do modelo. Um teste de contrato aprovado mostra que o sistema bloqueia ou encaminha uma ação corretamente; não prova que o checkpoint entendeu o pedido, escolheu a ferramenta certa ou produziu uma solução útil.

## Parecer atual

O projeto já tem uma base operacional séria: runtime Rust, AgentCore TypeScript, contratos de ferramentas, aprovações, persistência de tarefas, pesquisa e treinamento locais. O bloqueio principal é competência demonstrada. O checkpoint do chat e os candidatos avaliados ainda falham em geração aberta, decisão e tarefas inéditas. A aplicação pode funcionar em caminhos cobertos por regras, memória ou busca sem que os pesos tenham aprendido a competência.

Evidências de maior peso:

- O checkpoint geral ativo aprovou **0/20** respostas inéditas no experimento de diálogo local; a avaliação operacional de decisão aprovou **0/4**.
- Na ativação real de 01/10, duas verificações semânticas conhecidas falharam: um valor presente foi recusado e uma comparação incompatível foi aceita.
- A V4 teve **168/288** acertos na nova bancada, mas não alcançou o critério de promoção, gerou 40 propostas de resposta sem suporte e regrediu em conflitos e comparações negativas. Ela permanece experimental.
- Os cinco núcleos medidos reprovaram; programação, pesquisa, planejamento, comunicação e matemática geral seguem sem avaliação por aquele protocolo.
- A bateria de roteamento de ferramentas passou em **7/7** decisões isoladas, mas não executou ferramentas. A bateria integrada passou em **5/5** casos smoke; isso não mede domínio das 34 ferramentas nem tarefas longas.

Esses resultados são de protocolos diferentes e não devem ser somados nem comparados como se fossem uma única nota.

## Base já construída

- [x] Rust limita acesso ao workspace e executa ferramentas registradas; contratos e políticas tipam argumentos e riscos.
- [x] AgentCore coordena entendimento, planejamento, observação, aprovação, execução, retomada e verificação.
- [x] Escritas exigem workspace e autorização; caminhos fora do escopo e propostas inválidas são bloqueados.
- [x] Checkpoints preservam estado de tarefa e evitam repetir automaticamente efeitos incertos.
- [x] Pesquisa e recuperação local existem; conteúdo externo é evidência, não permissão para agir.
- [x] Há testes, traces, bancadas reservadas e critérios que impedem promoção automática.
- [ ] A existência desses mecanismos não certifica entendimento, geração livre ou conclusão correta pelo modelo.

## P0: bloqueios para uso confiável

### 1. Geração útil do checkpoint ativo

- [ ] Avaliar o arquivo exato usado no chat, com hashes de pesos, tokenizer, configuração e código registrados no resultado.
- [ ] Responder de forma completa e pertinente a perguntas inéditas sem repetição, saída vazia, encerramento prematuro ou vazamento de prompt.
- [ ] Distinguir resposta, consulta necessária e abstenção; não aceitar JSON válido como sinônimo de decisão correta.
- [ ] Executar a bateria sem respostas curadas, regras ou memória substituindo a geração; relatar separadamente o backend que respondeu.
- [ ] Não promover candidato que melhore loss ou média agregada, mas piore uma família crítica ou aumente respostas sem suporte.

**Aceite:** protocolo reservado e versionado, mínimo de 24 casos independentes por família, pelo menos 90% no total e 75% em cada família, 75% dos grupos completos e zero propostas incorretas ou sem suporte. Aplicar os mesmos casos ao baseline e ao candidato. Esses limites dão continuidade aos critérios já usados nos relatórios cognitivos; não são uma alegação de inteligência geral.

### 2. Entendimento do pedido e do escopo

- [ ] Preservar produto, público, resultado esperado, restrições, escolhas já confirmadas e critérios de aceite ao longo da tarefa.
- [ ] Separar pedido de explicação, planejamento, pesquisa, implementação, diagnóstico e execução; verbos isolados não devem decidir o modo.
- [ ] Perguntar somente quando a ambiguidade muda materialmente a solução; adotar defaults seguros para detalhes reversíveis.
- [ ] Reconhecer negações, limites, pedidos de “apenas explique” e mudanças de assunto antes de propor escrita.
- [ ] Medir pedidos curtos e paráfrases inéditas, não apenas frases que repetem os gatilhos do catálogo.

**Aceite:** avaliação cega por intenção, preservação de restrições e decisão de perguntar/agir. Nenhuma escrita em casos de conversa, planejamento sem autorização ou instrução negada.

### 3. Seleção e argumentos de ferramentas

- [ ] Cobrir as 34 ferramentas registradas com casos positivos, negativos, paráfrases, argumentos ausentes e ferramentas indisponíveis.
- [ ] Verificar ferramenta **e argumentos completos**: caminho/URL, faixa de leitura, diretório, consulta e opções de verificação.
- [ ] Distinguir busca no workspace de pesquisa na web, leitura de arquivo de inspeção estrutural, execução de perfil aprovado de shell livre.
- [ ] Após falha, usar o erro observado para escolher uma recuperação limitada; não repetir a mesma chamada nem adivinhar substitutos.
- [ ] Medir ponta a ponta no AgentCore e runtime, registrando chamadas reais, resultados e resposta final. Decisões isoladas do planejador ficam como evidência complementar.

**Aceite:** nenhuma ação fora do catálogo ou sem autorização; zero mutações nos casos somente leitura; todos os argumentos passam pelo contrato e apontam para evidência observada. A smoke suite atual é pequena demais para liberar esta etapa.

### 4. Construção, edição e depuração de projetos

- [ ] Demonstrar o ciclo completo: entender produto, inspecionar projeto, escolher arquivos, propor mudanças revisáveis, obter autorização, escrever, testar e explicar o resultado.
- [ ] Editar projetos existentes sem recriá-los nem substituir sua identidade, arquitetura ou conteúdo sem pedido.
- [ ] Para falha reportada, ler a implementação e o diagnóstico antes de corrigir; verificar novamente após a mudança.
- [ ] Não declarar sucesso por ter criado um arquivo, produzido um diff ou executado um check que não cobre o critério solicitado.
- [ ] Avaliar aplicações funcionais em workspace isolado, incluindo integração frontend/backend, erros, responsividade e comandos de inicialização quando fizerem parte do pedido.

**Aceite:** artefato real no workspace de teste, critérios de aceite ligados a evidências e verificações comportamentais aprovadas. Um teste de sintaxe ou compilação sozinho não aprova o produto.

## P1: capacidades necessárias para continuidade

### 5. Continuidade entre pedidos e retomada

- [ ] Manter o objetivo original e as restrições humanas ao receber complementos como “quero trabalhar nela”, “continue nisso” ou pedidos que nomeiam a tela.
- [ ] Identificar o artefato correto após criação e edição; não usar a aba ativa como substituto do contexto da tarefa.
- [ ] Interromper a continuidade diante de cancelamento, negação, novo assunto ou projeto incompatível.
- [ ] Testar a cadeia completa com modelo ativo: pedido inicial, resposta, complemento, edição, aprovação, verificação e resposta final.
- [ ] Testar reinício e retomada sem duplicar escritas nem esquecer efeitos incertos.

**Estado:** há persistência e testes de continuidade de software; o reconhecimento de referências pronominais recebeu uma regressão unitária recente. Ainda falta medir a execução de uma edição real pelo checkpoint ativo.

### 6. Pesquisa e fundamentação em evidências

- [ ] Pesquisar somente quando a pergunta exigir fatos ausentes ou atuais; preferir arquivos locais para perguntas sobre o projeto.
- [ ] Ligar cada afirmação factual às fontes realmente abertas e pertinentes, não apenas a IDs existentes.
- [ ] Detectar conflito, fonte vazia, página fora do tema, instrução injetada e informação desatualizada; nesses casos, buscar outra evidência ou declarar a lacuna.
- [ ] Separar evidência recuperada de conhecimento incorporado aos pesos. Uma consulta bem-sucedida não significa aprendizado permanente.
- [ ] Medir qualidade da síntese com revisão independente e fontes contraditórias, inclusive quando a consulta externa falha.

**Aceite:** zero afirmações críticas sem sustentação no conjunto de avaliação e abstenção correta em conflitos. Integridade de URL ou citação, sozinha, não satisfaz este critério.

### 7. Memória e contexto

- [ ] Recuperar somente preferências e fatos pertinentes à tarefa atual, com origem, idade e confiança preservadas.
- [ ] Nunca transformar texto de assistente, arquivo, página ou saída de ferramenta em autorização do usuário.
- [ ] Evitar memória obsoleta, mistura entre conversas/workspaces e vazamento entre treino, validação e reservado.
- [ ] Testar recall e descarte: informação relevante deve sobreviver à compactação; dado irrelevante ou contraditório não deve dominar a resposta.
- [ ] Definir retenção, inspeção e remoção para checkpoints locais que armazenam histórico e conteúdo observado.

### 8. Anexos e formatos de trabalho

- [ ] Migrar o fluxo de mensagens com anexos do caminho legado para o mesmo AgentCore, contexto, permissões, eventos e retomada.
- [ ] Preservar origem e limites de confiança de texto extraído, imagem, áudio, vídeo e documentos.
- [ ] Declarar formatos não suportados, OCR incerto e partes truncadas; não inventar conteúdo que não foi observado.
- [ ] Testar anexos junto de workspace diferente, troca de conversa e retomada, sem presumir que o anexo autoriza editar aquela pasta.

**Estado:** o README do AgentCore registra anexos no fluxo legado como frente pendente de migração.

## P2: confiança de produto e melhoria contínua

### 9. Segurança e resistência a conteúdo malicioso

- [ ] Exercitar prompt injection em repositórios, páginas e anexos; nenhum conteúdo observado pode ampliar permissões ou catálogo.
- [ ] Cobrir traversal, symlinks, segredos, escrita destrutiva, comandos fora dos perfis, repetição e aprovação aplicada à chamada errada.
- [ ] Verificar os efeitos de ações interrompidas antes de tentar novamente.
- [ ] Manter zero mutações não autorizadas em todas as bancadas, inclusive recuperação de erro e retomada.

### 10. Dados, treinamento e promoção

- [ ] Manter autoria, procedência, licença e revisão humana dos dados; exemplos de avaliação nunca entram no treino.
- [ ] Medir comprimento real de pedidos, históricos e respostas. O checkpoint geral tem treino registrado em contexto 512, enquanto o runtime anuncia 32.768; extensão de janela não ensina contexto longo.
- [ ] Treinar conclusão e qualidade de resposta, não somente redução de loss ou formato superficial.
- [ ] Comparar baseline e candidato no mesmo protocolo e em bancadas de regressão; usar mais de uma semente quando o orçamento permitir.
- [ ] Vincular toda promoção a hashes reproduzíveis; reprovação ou falta de avaliação mantém o checkpoint anterior.

### 11. Observabilidade, honestidade e desempenho

- [ ] Mostrar estado real: planejando, aguardando autorização, executando, verificando, bloqueado ou concluído; não usar progresso fictício.
- [ ] Distinguir backend neural, regra, memória, pesquisa e fallback; não apresentar sucesso de fallback como competência neural.
- [ ] Registrar tempo, falhas, tentativas, orçamento de ferramentas e causa de parada para cada tarefa.
- [ ] Definir latência e consumo aceitáveis em CPU/GPU local sem relaxar correção para responder mais rápido.
- [ ] Testar timeout, cancelamento, indisponibilidade de serviço e recuperação visível para a pessoa usuária.

## Portão de “pronto para uso”

Só chamar o agente de confiável quando **todos os P0** passarem em uma avaliação reservada do checkpoint ativo e em testes ponta a ponta; não haver mutação sem autorização nem afirmação factual sem suporte; cada família cumprir os limites acima; e a bateria de regressão continuar aprovada após qualquer troca de pesos, tokenizer, dados, política ou planejador. Itens P1/P2 continuam documentados até terem prova, responsável e decisão explícita de risco; não desaparecem por estarem fora da primeira entrega.

Resultado deve ser publicado por capacidade, com denominador, falhas, backend e hash do artefato. “145 testes passaram” significa que o software passou nos testes existentes, não que o modelo sabe programar, pesquisar ou raciocinar.

## Ordem recomendada

1. Congelar uma bancada ponta a ponta com tarefas inéditas de conversa, leitura, pesquisa, criação, edição, depuração e continuação; executar primeiro no checkpoint ativo.
2. Corrigir geração estruturada, completude, repetição e abstenção até o modelo produzir decisões utilizáveis sem fallback.
3. Expandir roteamento de ferramenta e argumentos com negativos e recuperações; depois ligar os casos a workspace isolado real.
4. Provar edição/continuidade após múltiplos turnos e reinício, incluindo aprovação e verificação do resultado.
5. Migrar anexos, ampliar pesquisa fundamentada e testar memória sem vazamentos.
6. Promover somente candidatos que passem os gates e não regredam em avaliações congeladas.

## Evidências consultadas

- [Cognição operacional e avaliação 0/4](COGNICAO_OPERACIONAL_2026-09-30.md)
- [Estado e limites do checkpoint neural](../model/RELATORIO_ESTADO_MODELO_NEURAL_2026-09-26.md)
- [Alinhamento V4, regressões e reprovação](COGNICAO_ALINHAMENTO_2026-10-01.md)
- [Resultados por núcleo e capacidades não avaliadas](NUCLEOS_COMPETENCIA_2026-10-01.md)
- [Ativação local e falhas semânticas ao vivo](ATIVACAO_MODELO_2026-10-01.md)
- [Diálogo neural em múltiplos turnos](../model/training/local-dialogue-v1/RELATORIO.md)
- [Roteamento de ferramentas: escopo e limites da smoke suite](AVALIACAO_AGENT_TOOL_TOP5_2026-09-24.md)
- [Avaliação isolada do roteamento](AVALIACAO_ROTEAMENTO_TOPRAK_2026-09-24.md)
- [Continuidade operacional, retomada e limites](CONTINUIDADE_OPERACIONAL_2026-09-30.md)
- [Contrato operacional do agente](FLUXO_AGENTE.md)
- [Limites e critérios do AgentCore](../agent-core/README.md)