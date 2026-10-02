# Plano de execução: agente programador funcional

Data: 27/09/2026. Status: proposta de execução baseada no código e nos relatórios disponíveis.

Este documento define o caminho da versão atual até uma beta com escopo verificável. Complementa o [plano do núcleo](PLANO_REESTRUTURACAO_NUCLEO_AGENTE.md) e a [especificação de autonomia](../Documentacoes/ESPECIFICACAO_MODELO_INTELIGENTE_AUTONOMO.md), priorizando entregas e dependências. As metas abaixo são propostas; não são resultados já obtidos. Nenhuma nova bateria neural ou operacional foi executada para escrever este plano.

## Diretriz de produto confirmada em 27/09/2026

O usuário definiu como requisito central compreender pedidos em linguagem natural, desenvolver backend e frontend integrados, avaliar o projeto existente e criar projetos do zero. O escopo Python descrito abaixo é uma etapa de diagnóstico; não satisfaz sozinho essa definição de produto.

Critérios obrigatórios da capacidade pretendida:

- Preservar objetivo, funcionalidades e stack ao longo da conversa. “Agora adicione uma interface” acrescenta um entregável à tarefa; uma escolha de tecnologias atualiza suas restrições.
- Inspecionar manifestos, pontos de entrada, interfaces e testes para decidir o que reutilizar e o que alterar. Arquivos existentes são evidência do estado, não autorização para substituir o produto solicitado por outro.
- Em workspace vazio, propor e criar estrutura, dependências, backend, frontend e instruções de execução coerentes com o pedido.
- Integrar frontend e backend por contratos explícitos: rotas, formatos de dados, erros e estados da interface.
- Conferir os comportamentos solicitados em execução. Testes de uma lista de tarefas não aprovam um ambiente de criação de jogos, mesmo que terminem com saída zero.
- Entregar continuação útil após uma falha, com diagnóstico concreto; registrar bloqueio quando o gerador não consegue produzir uma mudança válida.

### Evidências recentes e prioridade

O ensaio `task-a9108706-26e4-40b2-94af-bdacbd23921e` ficou bloqueado por repetição antes de escrever a implementação de `event_csv`. Foi uma avaliação do checkpoint ativo com especificação e testes, sem treinamento prévio com o novo material. Não deve ser apresentado como avaliação após treino.

No caso GameEngineStudio, `task-2fd94757-13a9-4238-adf5-38e7bfd9728f` usou a receita de interface de tarefas para um pedido de ambiente de criação de jogos. Os checks executados comprovaram comportamentos de tarefas, mas não o produto solicitado. Os complementos seguintes receberam respostas genéricas; o pedido explícito de Rust/TypeScript/CSS/HTML (`task-4dd4ac28-6f39-4b18-9e59-9aa223e85b4e`) bloqueou na geração. A restrição recente da receita contém esse desvio específico; não demonstra compreensão geral.

Próximas entregas, na ordem de dependência:

1. Comparar a geração local com um gerador de referência usando as mesmas tarefas e contratos do agente, incluindo projetos novos, manutenção e backend/frontend integrados. A geração avulsa do professor no Colab ainda não é uma integração ao agente.
2. Persistir requisitos e atualizações de conversa na tarefa e associá-los a evidências do projeto. O histórico textual sozinho não garante continuidade.
3. Vincular cada requisito a um critério de aceite e à revisão dos arquivos verificada. A conclusão deve considerar finalidade e integração, além de escrita e saída dos testes.
4. Implementar o ciclo incremental de leitura, patch, execução e reparo usando o gerador que demonstrar capacidade nas tarefas de desenvolvimento. Medir depois em tarefas reservadas distintas.

Estado: requisitos e prioridades registrados; a capacidade geral descrita nesta seção ainda não foi implementada nem demonstrada.

## 1. Resultado esperado

Um programador funcional recebe uma tarefa inédita, entende o resultado desejado, encontra o código relevante, propõe e aplica uma mudança coerente, executa verificações pertinentes, corrige falhas recuperáveis e entrega arquivos que atendem ao pedido. Consegue receber uma mudança de requisito na mesma conversa e continuar o trabalho.

A primeira beta terá escopo explícito:

- Projetos locais Python: funções, ferramentas de linha de comando e aplicações pequenas com persistência em JSON ou SQLite.
- Criação de projetos pequenos e manutenção de projetos existentes; alterações coordenadas em 2 a 5 arquivos.
- Documentação fundamentada no projeto, correção de defeitos e inclusão de funcionalidades.
- Conversas de 3 a 5 turnos com continuidade de requisitos e arquivos.
- Execução local controlada, diff para revisão, evidência de verificação e recuperação de alterações.

JavaScript/TypeScript será a segunda stack; Rust e C++/CMake entram depois, com verificadores próprios. O agente poderá ler outras stacks, mas não deverá anunciar competência de implementação antes de demonstrá-la.

Primeiro projeto demonstrador: um gerenciador local de despesas, com CLI, validação de entradas, persistência e resumo por categoria. Depois, acrescentar exportação e corrigir um defeito introduzido no cálculo. Esse demonstrador será público e usado no desenvolvimento; NÃO contará como tarefa inédita na avaliação reservada. As receitas existentes de tarefas/todo também não servirão como prova de generalização.

## 2. Diagnóstico que determina a ordem do trabalho

| Evidência disponível | Implicação | Trabalho necessário |
| --- | --- | --- |
| O relatório neural de 26/09 registra 0/24 casos úteis no checkpoint ativo avaliado e no candidato de contexto 2.048 | Ainda não há evidência de um gerador capaz de sustentar programação aberta | Investigar geração e treinamento como dependência crítica |
| O mesmo relatório descreve cerca de 6,7 milhões de parâmetros e 404 exemplos no treino de origem | A amostra existente não demonstra cobertura suficiente para o objetivo amplo | Auditar dados, objetivo de treino, generalização e orçamento antes de ampliar o modelo |
| Contexto de execução de 32.768 foi verificado mecanicamente, com treino de origem em 512 | Executar uma entrada longa não comprova usar seus requisitos corretamente | Medir retenção de requisitos e edição com evidência distribuída |
| AgentCore, Python e fluxos legados ainda participam da interpretação e do roteamento | Decisões podem divergir entre entradas do mesmo produto | Estabelecer tarefa e política canônicas |
| A classificação lexical coloca padrões de testes antes de build e inclui verbos de criação combinados com “app/aplicativo/projeto” | Pedidos de criação podem ser classificados como execução de testes | Corrigir precedência e representar intenções compostas |
| A proposta de implementação pede um JSON com o conteúdo completo de várias alterações | Uma resposta truncada pode invalidar todo o lote | Planejar e gerar alterações incrementais |
| A inspeção para análise seleciona até quatro arquivos com janelas iniciais limitadas | É uma primeira leitura, insuficiente como estratégia geral para modificar repositórios | Buscar símbolos, dependências e trechos adicionais conforme a tarefa |
| A verificação de build exige sucesso, mas reconhece principalmente project_checks/terminal_run | Uma documentação criada pode ficar bloqueada por falta de suíte executável | Critérios de aceite por artefato e por requisito |
| O executor Node acrescenta --runInBand a npm test | Uma opção específica pode quebrar projetos com outro executor | Descobrir comandos reais do manifesto e executar argumentos compatíveis |
| O comando check do AgentCore verifica sintaxe por remoção de tipos | Não equivale a uma checagem completa dos tipos TypeScript | Adicionar typecheck real em tarefa própria |

Fontes locais: [relatório neural](../model/RELATORIO_ESTADO_MODELO_NEURAL_2026-09-26.md), [AgentCore](../agent-core/src/agent.ts), [classificação](../agent-core/src/requirements.ts), [aceite](../agent-core/src/task-acceptance.ts), [propostas](../python/proactive_implementation.py), [worker](../python/model_server.py), [runtime](../runtime/src/main.rs), [scripts do núcleo](../agent-core/package.json).

Os números neurais são históricos e associados aos artefatos daquele relatório. A fase inicial deverá confirmar qual checkpoint o processo em execução realmente carregou: há seleção pelo estado God Mode em start.sh e um padrão diferente no worker.

O ajuste recente para “ok, crie o documento” é uma mitigação por regras. Ele precisa de regressão e deve evoluir para referências estruturadas à proposta anterior. Concatenar a resposta anterior ao pedido não resolve ambiguidade, mudança de workspace ou qualidade de geração.

## 3. Decisão sobre o motor de geração

O caminho principal preserva a proposta do projeto: modelo próprio, execução local e promoção baseada em evidências. A arquitetura permitirá comparar geradores pela mesma interface, mas este plano não pressupõe instalar um modelo externo nem usar uma API.

Há uma decisão de produto que não pode ser escondida no cronograma:

| Caminho | Consequência |
| --- | --- |
| Modelo próprio como gerador obrigatório | A data da beta depende de pesquisa e treinamento; o gerador é uma dependência sem prazo confiável hoje |
| Um modelo local pré-treinado como referência ou gerador alternativo, se essa preferência mudar | Permite investigar mais cedo quanto da falha vem da orquestração; requer avaliar hardware, licença e qualidade e identificar o backend usado |

Enquanto o gerador próprio reprovar as tarefas básicas, o sistema será classificado como protótipo de agente. Receitas úteis podem continuar disponíveis, identificadas como tal, mas não contarão como programação geral.

## 4. Arquitetura operacional a consolidar

    Pedido e contexto estruturado do editor
        → tarefa persistente com requisitos e critérios de aceite
        → descoberta do projeto e seleção de evidências
        → plano de pequenas alterações
        → proposta de patch e validação
        → autorização conforme a política da sessão
        → aplicação transacional em ambiente controlado
        → verificação associada à revisão dos arquivos
        → diagnóstico e correção limitada, ou entrega com evidências

AgentCore será a autoridade sobre objetivo, escopo, estado, orçamento e conclusão. Python oferecerá interpretação e geração como propostas tipadas. Rust executará operações e verificações. A interface apresentará eventos e decisões existentes no núcleo. Clientes com anexos deverão passar pelo mesmo ciclo.

A tarefa precisa registrar, no mínimo: task_id, workspace_id, pedido original, requisitos atuais, restrições, critérios de aceite, referências a propostas, leituras com hashes, ações concluídas, proposta pendente, resultados dos checks e orçamento restante.

Regras centrais:

1. Uma nova mensagem altera a tarefa por uma atualização explícita: continuar, acrescentar requisito, substituir requisito ou cancelar.
2. Arquivo ativo, seleção e anexos são campos separados do texto do pedido.
3. A resposta anterior do assistente é uma proposta referenciável; só fatos observados no workspace sustentam alegações sobre o projeto.
4. O resultado do modelo não concede autorização. A política de sessão define o que já está permitido; uma revisão do conteúdo invalida a aprovação de uma proposta exata anterior.
5. Uma verificação pertence a uma versão concreta dos arquivos. Editar depois de verificar exige nova verificação pertinente.

## 5. Fase 0 — Baseline reproduzível e rastreável

**Entregas**

- Inventário do checkpoint carregado, hashes de pesos/tokenizer, configuração, versões dos serviços e perfil de hardware.
- Snapshot reproduzível do código atual, preservando as alterações existentes. Registrar fontes novas, arquivos não rastreados relevantes e dependências; separar saídas geradas do código antes de planejar commits.
- Catálogo de capacidades realmente executáveis e divergências entre schemas Python, TypeScript e Rust.
- Uma execução de referência em cópias descartáveis de projetos, com os modos: regras/receitas, pesos isolados e agente completo.
- Relatório por tarefa com pedido, revisão inicial, ferramentas, diff, revisão final, checks, resultado esperado, resultado observado e motivo de falha.

**Implementação:** ampliar os avaliadores existentes em tests/ e o checkpoint de estabilização; evitar uma nova suíte com definições incompatíveis.

**Gate G0:** outra execução consegue reproduzir ambiente e atribuição de backend; todas as tarefas terminam classificadas, inclusive timeout, bloqueio e erro de ambiente. Nenhuma falha desaparece do denominador.

**Esforço inicial estimado:** 2–4 dias de engenharia, condicionado ao estado atual do ambiente.

## 6. Fase 1 — Estabelecer capacidade mínima de geração

Esta fase começa após G0 e é o caminho crítico. A infraestrutura das fases seguintes pode avançar com propostas controladas; seus testes não serão apresentados como capacidade neural.

### 6.1 Auditar treinamento e inferência

- Conferir round-trip do tokenizer, tokens de papel, EOS, máscara de padding, deslocamento dos alvos e posições que recebem loss.
- Comparar o formato real de treino com os prompts de implementação. Hoje o afinador de diálogo simples rejeita chamadas estruturadas de ferramentas; decidir entre texto serializado compatível e um treinador de trajetórias próprio.
- Conferir truncamento do pedido e das fontes em cada camada. O worker limita evidência textual antes da compactação; medir quais requisitos e trechos chegam de fato ao modelo.
- Comparar inferência com e sem cache em entradas iguais e verificar o carregamento exato dos pesos.
- Investigar término precoce e repetição na saída bruta. Registrar o texto bruto no ambiente de avaliação, com redaction quando houver dados privados.
- Revisar os filtros de repetição para código: repetição de identificadores e palavras de sintaxe pode ser válida. Separar degeneração de estruturas normais, sem liberar código incompleto como sucesso.
- Executar um pequeno ensaio de memorização controlada para diagnosticar o pipeline. Aprender esse ensaio não contará como generalização.

### 6.2 Preparar dados que representem o trabalho

Criar um manifesto versionado com origem, licença, tamanho em tokens, domínio, família de tarefa e método de validação. Usar material próprio ou com permissão compatível; não promover traces automaticamente para exemplos corretos.

Currículo progressivo:

1. Explicar entradas/saídas e gerar funções pequenas a partir de contratos.
2. Corrigir uma função com defeito e preservar seu comportamento restante.
3. Editar um arquivo existente a partir do conteúdo observado.
4. Atualizar dois ou mais arquivos com imports e interfaces compatíveis.
5. Interpretar falhas de execução e propor uma correção.
6. Incorporar um novo requisito sem perder os anteriores.
7. Declarar uma lacuna concreta quando faltarem dados essenciais.

Separar treino, desenvolvimento e avaliação por família de problema e origem de projeto. Deduplicar também variações próximas; separar apenas pelo texto exato da pergunta não basta. Depois de usar falhas de uma avaliação para ajustar o sistema, tratá-la como desenvolvimento e manter outro conjunto reservado.

Não fixar um número mágico de exemplos como garantia. Crescer o conjunto conforme lacunas, diversidade e curvas de aprendizado; registrar tokens efetivamente usados e distribuição de comprimento.

### 6.3 Executar experimentos controlados

Comparar, alterando um fator por rodada: correções de pipeline, composição dos dados, objetivo de treino, comprimento treinado, capacidade do modelo e decodificação. Medir tempo, memória e qualidade por família. Escolher configurações pelo desenvolvimento; consultar o conjunto reservado apenas no gate de candidato.

Antes de aumentar a arquitetura, medir memória e tokens por segundo no hardware disponível e calcular o custo da rodada. Aumentar contexto, saída máxima ou parâmetros exige uma hipótese mensurável.

**Gate G1 proposto:** em 40 tarefas reservadas simples, ao menos 24 implementações passam os testes de comportamento; ao menos 16/20 propostas estruturadas adicionais são válidas; nenhuma correção de pipeline introduz regressão crítica nos contratos. Esses limiares habilitam integração, não lançamento.

**Regra de revisão:** após duas rodadas sem ganho reproduzível fora do treino, revisar hipótese, dados e orçamento antes de repetir. Se G1 falhar, manter o status experimental e a data da beta aberta.

## 7. Fase 2 — Objetivo estável e entendimento do repositório

**Entregas**

- Registro canônico da tarefa e das mudanças de requisito.
- Classificação que distingue “crie um aplicativo com testes” de “execute os testes do aplicativo”.
- Resolução de “prossiga”, “faça a primeira opção” e “crie o documento” por referência à proposta e ao workspace corretos. Se houver duas opções incompatíveis, fazer uma pergunta curta.
- Descoberta de stack, entradas, dependências, comandos e convenções a partir dos manifestos e arquivos lidos.
- Busca incremental: símbolos e imports → arquivos consumidores → testes relacionados → trechos relevantes.
- Contexto por ação com caminhos, linhas, hashes, trechos observados, lacunas e requisitos. Limites iniciais de leitura deverão crescer conforme evidência de necessidade.
- Invalidação de evidências quando o arquivo ou workspace mudar.

**Arquivos principais:** agent-core/src/agent.ts, requirements.ts, contracts.ts, task-store.ts; python/dialogue.py, agent_planner.py, local_context.py; integrações de editor.

**Gate G2 proposto:** pelo menos 27/30 cenários de intenção e continuidade corretos; nenhum deles escreve no workspace anterior após troca de projeto. Todos os casos de “criar com testes” preservam criação como objetivo principal.

**Esforço:** 5–8 dias de engenharia após contratos e casos de G0.

## 8. Fase 3 — Planejar e editar por incrementos

**Entregas**

- Plano com passos, dependências, requisitos atendidos e evidência esperada.
- Gerar primeiro a lista de mudanças necessárias; depois produzir patches limitados por etapa. Usar a capacidade de JSON estruturado existente como validador.
- Ler o trecho completo necessário antes de editar; conflito ou arquivo alterado desde a leitura exige nova inspeção.
- Aplicação de lote com snapshot, pré-condições e recuperação. Reutilizar apply_batch/undo_batch, auditando comportamento em falha parcial.
- Preservar alterações do usuário; rollback só poderá restaurar o que pertence à transação e não pode apagar uma edição posterior do usuário.
- Registrar progresso por requisitos concluídos, impedindo que o agente gere o mesmo lote repetidamente.
- Distinguir restrições técnicas do runtime de decisões rotineiras que o próprio agente deve tomar.

**Arquivos principais:** python/proactive_implementation.py, model_server.py; agent-core/src/planner.ts, plan-executor.ts; runtime/src/main.rs e contracts/.

**Gate G3:** criação e atualização de projetos de 2–5 arquivos com diffs íntegros; todas as sondagens de conflito detectam mudança concorrente; falha parcial é recuperada ou registrada como estado indeterminado, sem conclusão falsa.

**Esforço:** 4–7 dias; demonstração de qualidade depende de G1 e G2.

## 9. Fase 4 — Executar, verificar e corrigir

O critério de término será o atendimento aos requisitos, sustentado por verificações específicas.

| Artefato ou tarefa | Evidência exigida |
| --- | --- |
| Documento | Arquivo gravado e relido, conteúdo esperado presente, referências locais válidas; comandos alegados como executados têm log |
| Configuração | Parsing, schema quando disponível e check da ferramenta consumidora |
| Função/CLI Python | Sintaxe/importação, testes de comportamento e execução de um fluxo representativo |
| Aplicação com persistência | Fluxo de gravação/leitura em dados temporários, restart e recuperação do estado |
| Projeto JS/TS, na expansão | Comando real do projeto, typecheck/build quando definidos e testes pertinentes |
| Projeto C++/CMake, na expansão | Configuração/build em diretório separado e CTest quando registrado; compilação isolada não prova comportamento |
| Interface, na expansão | Fluxo de interação e resultado visível; servidor aberto ou porta respondendo não comprova função do produto |

**Trabalho**

- Substituir o único resumo de verificação por uma lista tipada: requisito, tipo de check, comando/argumentos, diretório, revisão verificada, execução, código de saída, duração e resultado.
- Descobrir perfis de execução pelo projeto; evitar flags universais como --runInBand.
- Distinguir suíte indisponível, zero testes coletados, falha de dependência, falha preexistente e regressão introduzida.
- Registrar baseline dos checks existentes antes da alteração, quando pertinente.
- Usar testes independentes do código gerado nos benchmarks. O agente pode escrever testes do produto, mas não deve alterar a avaliação reservada para fazê-la passar.
- Corrigir com base em diagnóstico: localizar causa provável → ler fonte relacionada → patch → repetir checks relevantes.
- Limite inicial de três tentativas de reparo por falha, orçamento por tarefa e parada por repetição sem nova evidência. O limite global de ações não deve interromper uma tarefa apenas porque houve várias leituras legítimas.
- Propagar cancelamento e prazos pelos serviços; timeout do cliente não pode virar repetição cega de uma escrita cujo efeito é desconhecido.

**Ambiente de execução:** projetos e scripts devem rodar em cópia descartável ou isolamento adequado, com limites de processo/tempo, ambiente sem credenciais desnecessárias e controle de rede. Validar caminhos de arquivo não confina os efeitos de um subprocesso. As permissões podem ser concedidas para o escopo de uma sessão; mudanças desse escopo exigem decisão explícita.

**Gate G4 proposto:** corrigir pelo menos 14/20 defeitos inéditos no domínio Python; zero conclusões de sucesso quando check obrigatório falhar ou não executar; sucesso anterior não vale depois de uma nova edição.

**Esforço:** 6–10 dias; pode compartilhar trabalho de infraestrutura com G3.

## 10. Fase 5 — Avaliação de produto e beta restrita

Montar 60 tarefas reservadas em projetos preparados independentemente das receitas e do treino:

| Família | Quantidade | Prova |
| --- | ---: | --- |
| Construção de projeto pequeno | 12 | Requisitos funcionais e persistência |
| Nova funcionalidade em projeto existente | 12 | Mudança útil sem regressão |
| Correção de defeito | 12 | Falha reproduzida e comportamento corrigido |
| Refatoração pequena | 8 | Contratos e comportamento preservados |
| Documentação/configuração | 8 | Conteúdo correto e artefato consumível |
| Continuidade em 3–5 turnos | 8 | Requisitos mantidos e mudanças incorporadas |

Executar três rodadas por tarefa em workspaces novos: 180 execuções. Fixar orçamento, perfil de máquina e dependências; registrar seeds quando houver amostragem. Com decodificação determinística, as repetições medem sobretudo estabilidade do sistema.

**Definição de sucesso por execução:** todos os critérios obrigatórios aprovados, verificações independentes pertinentes executadas, ausência de regressão introduzida e entrega fiel à evidência. Uma chamada de ferramenta válida ou uma mensagem “concluído” não basta.

**Gate G5 proposto para beta**

- Pelo menos 144/180 execuções bem-sucedidas; ao menos 70% em cada família.
- Pelo menos 45/60 tarefas bem-sucedidas nas três rodadas.
- Nenhuma conclusão falsa observada e nenhuma violação de escopo nas sondagens dedicadas. Publicar contagens; zero observado não é garantia universal.
- Cancelamento, retomada e recuperação de transação aprovados em pelo menos 12 cenários dedicados, fora das 60 tarefas de produto.
- Orçamento inicial de até 10 minutos por tarefa pequena, excluindo espera de aprovação; timeout conta como insucesso. Recalibrar uma vez após G0, antes de avaliar candidatos, e congelar o limite.
- Medir latência p50/p95, memória máxima, tentativas, quantidade de aprovações e proporção de código exigido do usuário. Pedir ao usuário a solução completa conta como falha de autonomia.

Separar resultados dos pesos isolados, do agente com pesos e do sistema com receitas. Publicar numeradores, denominadores e incerteza; 60 tarefas não demonstram competência em qualquer projeto.

Depois de G5, usar a beta em dez tarefas reais supervisionadas e registrar esforço humano de correção. Nenhuma expansão de stack enquanto falhas recorrentes da stack inicial estiverem abertas.

## 11. Backlog inicial executável

Cada item deve ter uma mudança revisável, casos pertinentes e evidências anexadas. Os caminhos indicam pontos de integração, não obrigação de concentrar mais código em arquivos já grandes.

| ID | Prioridade | Entrega | Dependência |
| --- | --- | --- | --- |
| B01 | P0 | Manifesto do runtime e checkpoint exatos; snapshot do código atual | Nenhuma |
| B02 | P0 | Runner de tarefas reais com workspaces descartáveis, relatório por requisito e baseline | B01 |
| B03 | P0 | Corrigir precedência criação/testes e criar representação de intenção composta | B02 |
| B04 | P0 | Auditar tokenizer, labels, EOS, serialização, cache e orçamento de contexto | B01 |
| B05 | P0 | Currículo de programação com deduplicação e splits por família | B02, B04 |
| B06 | P0 | Candidato neural e avaliação de G1 com pesos isolados | B05 |
| B07 | P1 | Registro de tarefa, referências a propostas e contexto de editor estruturado | B03 |
| B08 | P1 | Descoberta incremental de código e invalidação de evidências | B07 |
| B09 | P1 | Plano incremental e patches com pré-condições | B08; qualidade depende de B06 |
| B10 | P1 | Verificadores tipados; checks de documentos e perfis Python/Node corretos | B02 |
| B11 | P1 | Isolamento, cancelamento e reconciliação de efeitos incertos | B01 |
| B12 | P1 | Reparo dirigido por falha e revalidação após cada edição | B09, B10, B11 |
| B13 | P1 | Migrar anexos para o núcleo e typecheck real do AgentCore | B07 |
| B14 | P1 | Bateria reservada de produto, relatório e decisão da beta | G1–G4, B13 |
| B15 | P2 | JS/TS com build, testes e execução próprios | G5 Python |
| B16 | P2 | Rust e C++/CMake com avaliação por stack | G5 da stack anterior |

Ordem de início: B01 → B02; depois B03/B04/B10. Dados e geração avançam junto à infraestrutura, com resultados separados. Não iniciar uma reescrita total de Python, Rust e TypeScript.

## 12. Cadência, orçamento e decisões de avanço

Primeira semana proposta: fechar G0, classificar as falhas mais frequentes, corrigir roteamento de criação/testes e levantar defeitos do pipeline neural. Segunda semana proposta: concluir a primeira rodada de diagnóstico/candidato e a representação estruturada de tarefa; implementar verificações de documentação e Python.

As estimativas das fases representam esforço de engenharia de uma pessoa familiarizada com o projeto. Não incluem tempo indeterminado de pesquisa neural, aquisição de hardware ou preparação extensa de dados. Não há data responsável para prometer G5 antes de G1.

A cada ciclo de trabalho, registrar: hipótese, mudança, tarefas que passaram/falharam, regressões, custo e decisão. Cada rodada de treino terá limite de duração, armazenamento e memória definido após medir o hardware; pesos candidatos ficam separados até passar o gate.

Regras de avanço:

- Geração continua em 0/N: prioridade ao diagnóstico e ao treinamento, sem expansão de escopo de produto.
- Código isolado passa e agente completo falha: prioridade à seleção de contexto, planejamento e execução.
- Sucesso depende de comandos ou código fornecidos pelo usuário: classificar como assistido e corrigir autonomia.
- Cresce o número de regras, mas tarefas inéditas não melhoram: revisar a abordagem, não aumentar a nota pela cobertura de exemplos conhecidos.
- Ganho restrito ao conjunto de desenvolvimento: não promover; investigar sobreajuste e vazamento.

Ficam depois da primeira beta: aprendizado autônomo amplo, novas integrações, multimodalidade geral, extensão adicional de contexto e expansão indiscriminada do catálogo de ferramentas. O investimento inicial deve aumentar a taxa de tarefas de programação efetivamente concluídas.

## 13. Referências e uso das evidências

- [Plano do núcleo](PLANO_REESTRUTURACAO_NUCLEO_AGENTE.md): contratos, estados, migração e recuperação que este plano transforma em entregas.
- [Estado neural em 26/09](../model/RELATORIO_ESTADO_MODELO_NEURAL_2026-09-26.md): resultados históricos e limites do checkpoint avaliado.
- [Política de contexto](../Documentacoes/POLITICA_JANELA_CONTEXTO.md): considerar separadamente janela executável e qualidade semântica; qualquer revisão do gate deverá ser explícita.
- [SWE-bench: avaliação](https://www.swebench.com/SWE-bench/guides/evaluation/): referência para aplicar patches em repositórios e avaliar o comportamento por testes. A bateria local proposta não é um resultado SWE-bench.
- [SWE-bench: isolamento reproduzível](https://www.swebench.com/SWE-bench/guides/docker_setup/): referência de ambientes de avaliação. A tecnologia exata de isolamento local será escolhida após o inventário do ambiente.
- [Hugging Face: exemplo de treinamento causal](https://github.com/huggingface/transformers/blob/main/examples/pytorch/language-modeling/run_clm.py): referência para auditar separação de treino/validação e métricas de previsão. O plano preserva o modelo próprio e mede utilidade de código por execução.

Critério final de entrega do projeto: o usuário consegue pedir, construir, modificar e corrigir um programa dentro do escopo anunciado, com resultado reproduzível e esforço humano mensuravelmente menor. A declaração de beta só ocorrerá com os relatórios de G0–G5 e as limitações publicadas.
