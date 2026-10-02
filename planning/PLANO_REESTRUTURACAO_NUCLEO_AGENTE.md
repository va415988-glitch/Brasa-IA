# Plano detalhado para reestruturar o núcleo do agente

O [plano de execução para um agente programador funcional](PLANO_AGENTE_PROGRAMADOR_FUNCIONAL.md),
de 27/09/2026, define o escopo da primeira beta, a dependência de geração neural,
o backlog prioritário e os gates de entrega. Este documento permanece como
referência da arquitetura e da reestruturação do núcleo.

**Status:** plano estável; reestruturação iniciada  
**Data:** 24/09/2026  
**Escopo:** núcleo de decisão, roteamento de ferramentas, execução, recuperação, evidências, memória e resposta final do agente local  
**Implementação:** não iniciada por este documento

## 1. Resumo executivo

O objetivo é fazer o agente concluir tarefas reais com autonomia responsável e demonstrar, na resposta final, que entendeu o pedido, escolheu os meios adequados, observou o resultado e verificou se chegou ao objetivo. Uma resposta bem escrita será tratada como consequência de um fluxo cognitivo correto, nunca como substituto dele.

O problema observado não é simplesmente “falta de ferramentas”. O projeto já contém catálogos, contratos, roteadores, um AgentCore em TypeScript, serviços Python, um runtime Rust, registros de atividade e avaliações. O risco é esses componentes tomarem decisões parecidas em lugares diferentes, sem que um caminho único garanta que o pedido passe de intenção a resultado verificável.

A reestruturação proposta estabelece responsabilidades explícitas:

- **AgentCore/TypeScript:** autoridade sobre tarefa, objetivo, critérios de aceite, política, próximo passo, recuperação, evidências e decisão de concluir.
- **Python/modelos locais:** interpretação e geração como serviços chamados pelo núcleo. Uma resposta de modelo é uma hipótese ou proposta até ser validada pelo núcleo.
- **Runtime Rust:** execução de ferramentas e efeitos no workspace, validação de contratos, limites de acesso, aprovações e retorno observável.
- **Catálogo de capacidades:** fonte única para descrever o que cada ferramenta faz, entradas, efeitos, risco, requisitos, evidências e forma de verificar o resultado.
- **Interface:** apresenta progresso e resultados a partir dos eventos reais da tarefa, sem transformar tentativa em conclusão.

A migração deve ser incremental. O agente atual continua disponível através de adaptadores enquanto cada fluxo novo demonstra paridade e melhora nas avaliações. O núcleo antigo só deve perder autoridade depois de os casos correspondentes passarem os gates definidos neste plano.

## 2. Resultado que queremos observar

Diante de um pedido, o agente deve conseguir:

1. Entender o resultado solicitado e preservar restrições explícitas.
2. Determinar se é conversa, pesquisa, inspeção, implementação, depuração, execução, aprendizado ou uma combinação.
3. Encontrar o workspace pertinente e descobrir sua estrutura sem confundir uma listagem com leitura de conteúdo.
4. Selecionar uma ferramenta ou sequência de ferramentas coerente com o objetivo e com o risco.
5. Executar o próximo passo útil sem pedir que a pessoa escolha nomes de arquivos, funções ou ferramentas que o próprio workspace pode revelar.
6. Examinar o resultado real de cada ação e registrar evidências rastreáveis.
7. Detectar falha, resultado parcial, ausência de evidência, contradição ou falta de progresso; mudar a estratégia quando houver outra opção segura.
8. Continuar enquanto houver trabalho seguro e útil. Solicitar aprovação apenas quando a política de risco ou o efeito externo exigir autorização.
9. Conferir os critérios de aceite antes de declarar conclusão.
10. Entregar uma resposta concisa o bastante para ser lida e completa o bastante para mostrar o que foi feito, o que foi observado, o que foi verificado e o que permanece incerto.

O nível de resposta usado como referência nesta conversa é o seguinte: descrição do projeto ancorada em arquivos efetivamente lidos, retrato estrutural que diferencia arquivos, diretórios e manifestos, stack baseada em evidência, verificações identificadas com estado correto de execução, exemplos de símbolos com caminho e linha, inferências marcadas como inferências e limites declarados sem inventar propósito.

## 3. Escopo e limites

### Incluído

- Um fluxo canônico de tarefa, do pedido à entrega.
- Roteamento de ferramentas baseado em capacidades e contratos verificáveis.
- Política de autonomia e aprovação.
- Planejamento por etapas, execução, observação e replanejamento.
- Critérios de aceite e evidências por tipo de tarefa.
- Recuperação de falhas e detecção de estagnação.
- Resposta final fundamentada em evidências.
- Persistência e observabilidade das etapas e artefatos.
- Configuração declarativa versionada, incluindo YAML se a validação e o carregamento forem implementados como parte do sistema.
- Avaliações que provem competência em vez de apenas contar ferramentas ou eventos.
- Migração por etapas entre TypeScript, Python e Rust.

### Fora do escopo deste plano

- Implementar agora todas as mudanças descritas.
- Prometer inteligência geral ou capacidade de responder corretamente a qualquer assunto.
- Aumentar a contagem de ferramentas como critério isolado de progresso.
- Treinar um modelo novo como substituto para contratos, verificação ou política.
- Expor raciocínio interno privado. O sistema deve expor intenção operacional e justificativas curtas e verificáveis, sem despejar uma cadeia interna de pensamento.
- Tratar sucesso técnico de uma chamada como prova de que o pedido inteiro foi concluído.

## 4. Evidências do estado atual

A inspeção do repositório mostra que já existem bases úteis para a reestruturação:

- `agent-core/src/agent.ts` coordena o AgentCore e o ciclo local.
- `agent-core/src/requirements.ts` classifica objetivos, extrai restrições e define critérios de aceite.
- `agent-core/src/brain-state.ts` define estados e transições cognitivas, incluindo planejamento, execução, verificação e recuperação.
- `agent-core/src/plan-executor.ts` valida planos, resolve ferramentas, pede aprovação conforme risco, aplica timeout e trata falhas recuperáveis.
- `agent-core/src/operational-brain.ts` contém políticas operacionais e orientação de fluxo.
- `agent-core/src/runtime-http.ts` conecta o núcleo ao runtime.
- `python/capability_catalog.py`, `python/tool_registry.py`, `python/skill_router.py`, `python/agent_planner.py`, `python/dialogue.py` e `python/model_server.py` cobrem partes da descoberta de capacidades, roteamento, planejamento, diálogo e geração.
- `capabilities/metadata.json`, `contracts/` e os contratos do AgentCore descrevem partes do catálogo e das entradas e saídas.
- `runtime/src/main.rs` implementa ferramentas e operações do runtime local.
- `Documentacoes/FLUXO_AGENTE.md` já define um ciclo de decisão, aprovação, runtime, observação, verificação e entrega.
- Existem testes de contrato, execução, comportamento, aprendizado, roteamento e benchmarks, incluindo `tests/test_agent_behavior_gates.py`, `tests/test_agent_execution_gates.py`, `tests/test_capability_catalog.py`, `tests/benchmark_model_suite.py` e `tests/benchmark_workflow_suite.py`.

Também há problemas concretos a transformar em critérios de aceite:

1. **Inventário incompleto:** em uma análise do repositório Repo teste pra IA Local, a resposta final descreveu arquivos e código, mas inicialmente a interface do agente não representou corretamente as pastas e arquivos que a pessoa via no workspace. Isso mostra uma diferença possível entre a observação do runtime e a síntese final.
2. **Consulta sem conclusão da tarefa:** o agente já respondeu que analisou ou inspecionou, mas não criou a implementação solicitada. Uma etapa de leitura não pode substituir uma solicitação de construção.
3. **Plano sem conteúdo executável:** houve casos em que o gerador local não produziu uma proposta estruturada e o fluxo parou sem arquivos alterados, mesmo havendo um próximo passo possível.
4. **Roteamento e uso frágeis:** possuir dezenas de ferramentas — 34 foi a contagem mencionada durante o diagnóstico anterior — não prova que o agente saiba selecionar, combinar e verificar cada uma. A contagem deve ser confirmada no baseline da reestruturação.
5. **Tentativa sem recuperação suficiente:** diante de uma falha, o agente pode interromper ou pedir que a pessoa repita o pedido sem reavaliar outra estratégia segura.
6. **Resposta genérica apesar de atividade:** uma sequência de eventos ou mensagens como “concluí inspect_project” não explica quais caminhos foram vistos, quais arquivos foram lidos nem como a conclusão responde à pergunta.
7. **Autoridade fragmentada:** classificadores, roteadores e fallbacks existem em mais de uma camada. É necessário que os adaptadores alimentem um decisor canônico em vez de disputarem a autoridade final.

Esses itens descrevem falhas observadas ou riscos arquiteturais a avaliar. O plano não presume que cada uma esteja presente em todos os caminhos do código.

## 5. Princípios de projeto

1. **Uma autoridade por decisão:** um único núcleo resolve objetivo, política, próximo passo e conclusão. Serviços auxiliares podem recomendar, mas não encerram a tarefa por conta própria.
2. **Ferramentas são capacidades contratadas:** nome e descrição não bastam; cada ferramenta precisa de entrada, resultado, efeitos, riscos, pré-condições e verificação.
3. **Evidência precede afirmação:** toda afirmação sobre o workspace deve apontar para uma observação, arquivo lido, saída de comando ou fonte externa consultada.
4. **Listar não é ler:** caminho encontrado, conteúdo lido, conteúdo alterado e conteúdo verificado são estados distintos.
5. **Êxito de ferramenta não significa êxito da tarefa:** a conclusão depende dos critérios de aceite da solicitação.
6. **Autonomia dentro de limites:** o agente usa as ferramentas seguras e autorizadas disponíveis sem transferir decisões rotineiras à pessoa. Pergunta apenas quando uma informação ou autorização realmente bloqueia a próxima ação.
7. **Falha vira informação para a próxima decisão:** preservar erro, efeito possível e evidência; não repetir a mesma tentativa sem uma razão ou mudança de parâmetros.
8. **Configuração declarativa com validação:** YAML pode parametrizar políticas e fluxos, mas não substitui o código que impõe invariantes de segurança e consistência.
9. **Memória tem proveniência:** histórico bruto, procedimento candidato e procedimento confiável são níveis diferentes.
10. **A interface mostra o que ocorreu:** eventos refletem chamadas e resultados reais; não são animações de progresso desvinculadas da execução.
11. **Migração progressiva e reversível:** manter adaptadores e possibilidade de retorno até cumprir paridade, observabilidade e gates.
12. **A qualidade deve ser medida por tarefa:** avaliar roteamento, execução, evidência, recuperação, segurança e resposta final em conjunto.

## 6. Arquitetura-alvo e autoridade entre componentes

### 6.1 AgentCore em TypeScript: núcleo de decisão

O AgentCore passa a ser a autoridade sobre:

- Identidade e ciclo de vida da tarefa.
- Interpretação estruturada do pedido e de seu contexto.
- Objetivo principal e objetivos secundários.
- Restrições, entregáveis e critérios de aceite.
- Política de risco, autonomia, orçamento e aprovação.
- Escolha e ordenação das ferramentas.
- Revisão de resultado e decisão de continuar, recuperar, perguntar ou concluir.
- Ledger de evidências e síntese final.
- Estado persistido e telemetria canônica.

O núcleo pode pedir a um modelo que interprete ambiguidade, sugira um plano ou redija uma síntese. A resposta do modelo precisa retornar ao núcleo como dado validável. Ela não pode declarar uma ferramenta executada, inventar evidência nem marcar a tarefa concluída sem passar pelas verificações.

### 6.2 Python: serviços de interpretação, geração e conhecimento

Python continua útil para os modelos locais, retrieval, geração, curadoria, compatibilidade com APIs e avaliações existentes. `python/model_server.py`, `python/agent_planner.py`, `python/dialogue.py` e os roteadores existentes devem passar a responder a contratos versionados e a registrar a origem e o grau de confiança das sugestões.

Python não deve manter um segundo estado canônico da tarefa nem tomar uma decisão concorrente de conclusão. Durante a migração, um adaptador traduz os contratos antigos; os campos não suportados ficam explícitos, sem perder informação em silêncio.

### 6.3 Rust: runtime de capacidades e efeitos

O runtime Rust é a autoridade sobre a execução local: filesystem, processos, validação de entradas, acesso ao workspace, escrita e efeitos colaterais. Cada chamada retorna um resultado estruturado suficiente para que o núcleo possa verificar o efeito sem inferi-lo a partir do texto gerado pelo modelo.

### 6.4 Catálogo: registro canônico de capacidades

O catálogo consolida contratos hoje distribuídos entre os metadados, o registro Python, os contratos JSON, as ferramentas Rust e o AgentCore. Enquanto a consolidação acontece, deve haver validação de paridade automatizada para impedir que a mesma ferramenta tenha nomes, parâmetros ou políticas diferentes em camadas distintas.

### 6.5 Interface: visualização derivada de eventos

A interface apresenta a tarefa e suas seções a partir de eventos versionados: intenção, plano, ferramenta iniciada, resultado, evidência, recuperação, aprovação pendente, verificação e entrega. Uma etapa visível só fica “concluída” quando o núcleo recebeu resultado e satisfez o contrato daquela etapa.

## 7. Ciclo canônico de uma tarefa

Toda tarefa operacional deve percorrer os estados abaixo. O núcleo pode condensar etapas quando a tarefa for simples, mas precisa preservar as invariantes correspondentes.

### Etapa 1 — Receber e preservar o pedido

Criar um `task_id`, guardar o texto original, idioma, contexto explícito e origem. Não reescrever o pedido de modo que restrições ou entregáveis desapareçam.

### Etapa 2 — Resolver contexto e alvo

Identificar workspace ativo, arquivos anexados e referências a outros projetos. Se não houver workspace ativo, buscar o contexto de workspace disponível e resolver o alvo de forma fundamentada. Perguntar somente quando houver mais de um alvo plausível e escolher um produzir risco ou trabalho provavelmente errado.

### Etapa 3 — Interpretar objetivo e restrições

Produzir uma interpretação estruturada com objetivo, entregáveis, restrições explícitas, pressupostos, dependências, risco e lacunas bloqueantes. Separar lacuna bloqueante de detalhe que pode ser descoberto por inspeção.

### Etapa 4 — Construir critérios de aceite observáveis

Traduzir cada entregável em uma condição verificável. Por exemplo: “implementar uma interface” deve resultar em arquivos de interface no local correto, integração mínima existente e verificações adequadas; não basta gerar uma descrição de como a interface poderia ser feita.

### Etapa 5 — Criar ou atualizar o inventário de contexto

Inventariar arquivos e pastas, reconhecer artefatos ocultos, ignorados e truncamento, identificar manifestos e testes e marcar o que foi efetivamente lido. Para análise de projeto, ler conteúdo pertinente antes de descrever propósito ou comportamento.

### Etapa 6 — Resolver política e autonomia

Calcular quais ações são permitidas, reversíveis, sensíveis, externas ou destrutivas. A autorização dada pelo próprio pedido cobre as ações locais necessárias dentro do escopo declarado. A política não deve pedir confirmação repetida para cada leitura, arquivo ou etapa já autorizada.

### Etapa 7 — Selecionar capacidade e planejar o próximo passo

Selecionar a ferramenta por contrato, objetivo, contexto, pré-condições, efeitos e risco. Produzir um plano curto e executável, com resultado esperado e verificação para cada ação. O primeiro passo deve ser a próxima ação útil, não um relatório de planejamento sem execução.

### Etapa 8 — Executar uma ação ou um lote seguro

Executar a ação por meio do runtime autorizado. Agrupar operações apenas quando seus efeitos forem previsíveis, o contrato suportar o lote e uma falha parcial puder ser detectada.

### Etapa 9 — Observar e registrar o resultado

Guardar ferramenta, entrada normalizada, status, duração, saída, erro, efeito observado e evidências. Separar “a chamada retornou” de “o efeito esperado foi confirmado”.

### Etapa 10 — Atualizar evidências e reavaliar os critérios

Associar cada evidência ao requisito que sustenta. Marcar cada critério como satisfeito, pendente, não aplicável ou impossível com a evidência correspondente. Um status `ok` da ferramenta não marca automaticamente o aceite da tarefa.

### Etapa 11 — Continuar, recuperar, perguntar ou parar

- Continuar se houver um próximo passo seguro e útil.
- Recuperar quando o método falhar, ficar sem evidência ou produzir resultado incompatível.
- Perguntar quando faltar dado que não possa ser descoberto e a decisão puder causar resultado materialmente diferente.
- Solicitar aprovação quando a política de risco exigir autorização para aquele efeito.
- Parar quando todos os critérios forem satisfeitos ou quando houver bloqueio real e explicitado.

### Etapa 12 — Verificar e entregar

Executar os checks adequados após alterações; para análises sem alteração, verificar cobertura de evidências e inventário. Redigir a resposta final a partir do ledger, nunca da memória textual do modelo isoladamente.

### Etapa 13 — Aprender sem transformar um erro em regra

Registrar o episódio e seu resultado. Só promover um procedimento à memória reutilizável depois de critérios de sucesso e proveniência, conforme a seção de memória.

## 8. Contrato canônico da tarefa

A forma exata deve ser fechada na fase de contrato, mas toda implementação deve transportar pelo menos os campos abaixo, com validação e versão:

```yaml
schema_version: task-envelope/v1
task_id: string
request:
  original_text: string
  locale: string
  source: string
context:
  workspace_id: string|null
  workspace_root: string|null
  attachments: []
objective:
  primary: conversation|analyze|research|build|debug|testing|operate|learn
  secondary: []
  confidence: 0.0
  basis: []
constraints: []
deliverables: []
acceptance_criteria:
  - id: string
    description: string
    verification_method: string
    status: pending|satisfied|not_applicable|blocked
    evidence_ids: []
assumptions: []
policy:
  autonomy_class: read_only|authorized_local|approval_required|blocked
  allowed_capabilities: []
  forbidden_effects: []
budget:
  max_steps: integer
  deadline_ms: integer|null
  max_recovery_attempts: integer
status: observing|planning|awaiting_approval|executing|verifying|recovering|delivering|blocked|completed
```

Regras do contrato:

- O texto original nunca é substituído pela interpretação.
- Cada restrição explícita aponta para sua origem.
- Critérios de aceite têm método de verificação; os que não podem ser verificados são identificados como tais.
- `confidence` representa uma decisão específica e informa sua base; não é uma nota global inventada.
- A decisão de autonomia usa uma enumeração validada, não texto livre gerado pelo modelo.
- Cada revisão relevante do plano e do aceite é preservada para auditoria.

## 9. Contrato canônico de capacidade e chamada

Cada capacidade registrada deve declarar:

| Campo | Finalidade |
|---|---|
| `id` e `version` | Identidade estável, independente do rótulo exibido |
| `description` | Ação concreta que a ferramenta executa |
| `input_schema` / `output_schema` | Validação de entrada e resultado |
| `objectives` e `examples` | Casos positivos e negativos para seleção |
| `preconditions` | O que precisa ser conhecido ou preparado |
| `effects` | Leitura, escrita, execução local, rede, publicação ou efeito externo |
| `risk` e `reversibility` | Impacto e possibilidade de desfazer |
| `approval_policy` | Condições explícitas que requerem aprovação |
| `idempotency` | Se repetir a chamada é seguro e como detectar duplicação |
| `timeout` e `retry_class` | Limites e classes de recuperação possíveis |
| `evidence_output` | Evidência que a ferramenta deve devolver para comprovar o efeito |
| `postconditions` | Estado esperado após sucesso |
| `error_taxonomy` | Erros recuperáveis, bloqueios e resultados indeterminados |
| `network_policy` e `data_scope` | Rede e dados aos quais a ferramenta pode ter acesso |

Uma chamada executável deve transportar `task_id`, `action_id`, capacidade e versão, argumentos validados, justificativa operacional curta, efeito esperado, risco, política de aprovação, prazo e ligação com um critério de aceite.

**Regra de paridade:** os catálogos e adaptadores atuais são fontes de migração, não autoridades independentes permanentes. Ao final da consolidação, um único contrato canônico deve alimentar validação, roteamento, execução, documentação e avaliação.

## 10. Roteamento de ferramentas

### 10.1 Inventário inicial obrigatório

A primeira fase deve enumerar todas as ferramentas realmente registradas em execução, sem contar nomes existentes apenas em documentação. Para cada uma, registrar:

- Identificadores e aliases.
- Contratos efetivamente carregados.
- Handler/runtime que executa a ação.
- Contexto de workspace e permissões necessários.
- Risco e reversibilidade observados.
- Exemplos em que deve ser usada e exemplos parecidos em que não deve.
- Saída observável e critério para validar o efeito.
- Testes de contrato e cenários do benchmark associados.

A referência histórica de 34 ferramentas deve ser confirmada nessa etapa. O relatório deve mostrar separadamente ferramentas registradas, testadas em contrato, testadas em integração e aprovadas em cenários comportamentais. Não chamar todas de “conhecidas” porque estão no catálogo.

### 10.2 Decisão de roteamento

O decisor deve retornar um objeto estruturado contendo:

- Objetivo e critério de aceite atendido pela ação.
- Capacidade escolhida e versão.
- Capacidades candidatas consideradas quando houver ambiguidade real.
- Argumentos completos ou campos que precisam ser obtidos antes da chamada.
- Evidência/contexto que justifica a seleção.
- Pré-condições a cumprir.
- Efeito, risco, reversibilidade e necessidade de aprovação.
- Resultado observável esperado.
- Próximo método previsto se o resultado for falha ou insuficiente.

Usar correspondência semântica e exemplos de rota, mas confirmar a decisão contra contratos e políticas determinísticas. Um modelo pode sugerir uma ferramenta; nunca pode contornar sua ausência no registro, schema inválido, política de risco ou pré-condição.

### 10.3 Composição de capacidades

Tarefas compostas precisam de uma sequência, não de uma ferramenta que pareça próxima. Exemplos:

- **Entender repositório:** inventariar → localizar pontos de entrada e testes → ler fontes pertinentes → checar manifestos e documentação → sintetizar com cobertura e limites.
- **Implementar interface:** inspecionar stack e padrões visuais → propor decisões descobertas no projeto → escrever arquivos → verificar referências e build/testes existentes → reportar arquivos e checks.
- **Corrigir bug:** reproduzir ou ler falha → buscar causa em fonte pertinente → alterar o menor escopo → executar verificações relacionadas → resumir evidência e limitações.
- **Pesquisar assunto atual:** decidir se a informação muda no tempo → consultar fontes compatíveis com política de rede → cruzar fontes → apresentar data, citações e incerteza.

### 10.4 Sinais de roteamento correto

A ferramenta escolhida não é avaliada isoladamente. Roteamento correto exige chamada válida, argumento correto, efeito dentro da política, resultado observado, aceitação verificada e resposta consistente com o resultado.

## 11. Pensamento operacional

“Thinking” no núcleo significa manter uma representação explícita do problema e revisar decisões com evidência. Não significa inserir um parágrafo genérico de raciocínio antes de chamar uma ferramenta.

O núcleo deve manter, para uso interno estruturado:

- Pedido e contexto preservados.
- Hipóteses e premissas com origem e confiança.
- Objetivos, restrições e critérios de aceite.
- Estado do inventário e das evidências.
- Plano e revisões do plano.
- Ações, resultados, erros e efeitos incertos.
- Por que a próxima ação reduz uma lacuna específica.
- Progresso, orçamento restante e sinais de repetição improdutiva.

Para a pessoa, expor uma explicação operacional breve: “vou listar os arquivos para mapear a estrutura”, “li estes dois arquivos”, “o comando falhou por falta de dependência; vou verificar se há alternativa disponível”. Não expor cadeia interna privada nem dar justificativa fictícia depois do fato.

## 12. Fluxos de objetivo e aceite mínimo

| Objetivo | Fluxo mínimo | Evidência necessária para concluir |
|---|---|---|
| Conversa | Entender a pergunta, responder no contexto e diferenciar conhecimento geral de fatos que exigem consulta | Base da resposta e limite quando houver incerteza relevante |
| Análise de workspace | Inventariar arquivos e pastas; detectar truncamento; ler fontes relevantes; reconhecer testes e manifestos; sintetizar | Caminhos listados versus lidos, fatos, inferências e lacunas |
| Pesquisa | Decidir necessidade de fontes atuais; pesquisar; avaliar e cruzar fontes | Referências que sustentam afirmações e data de consulta |
| Construção | Inspecionar estrutura e convenções; planejar o menor caminho completo; criar ou modificar arquivos; verificar | Diff/arquivos, critérios cumpridos e checks realmente executados |
| Depuração | Reproduzir ou analisar evidência do erro; localizar causa; corrigir; reexecutar cenário pertinente | Erro observado, correção e resultado pós-correção |
| Testes | Descobrir framework e comando; executar o escopo adequado; classificar resultado | Comando, código de saída, resumo e eventuais falhas |
| Operação local | Validar alvo e efeito; executar conforme autorização; confirmar estado | Efeito observado e recuperação possível |
| Aprendizado | Registrar item, fonte, escopo e avaliação; consultar conhecimento existente | Proveniência e critério de validade do conhecimento |

### Aceite para análise de workspace

- A contagem de pastas e arquivos vem de inventário registrado, com convenção de contagem explícita.
- Arquivos ocultos e diretórios ignorados são tratados de acordo com política explícita; nunca somem silenciosamente.
- O nível de recursão, limites e truncamento ficam visíveis.
- “Arquivos lidos” inclui somente conteúdo efetivamente lido.
- Manifesto ausente não vira diagnóstico de defeito.
- Stack é identificada com base em manifestos, extensões e fontes citadas, indicando a origem.
- Testes descobertos são distintos de testes executados.
- As afirmações sobre finalidade do projeto vêm de README/documentação lida ou são marcadas como inferência.

### Aceite para construção

- Uma solicitação explícita de implementação produz implementação no workspace-alvo, desde que a ação esteja autorizada pela política.
- Plano, explicação ou proposta não substitui os arquivos pedidos.
- A saída informa quais arquivos foram criados/alterados.
- Verificações compatíveis são executadas quando cabível e seu resultado real é informado.
- Uma falha num check não apaga a entrega; ela é relatada junto com o estado e a próxima ação tentada.

## 13. Política de autonomia e aprovação

### Executar de forma autônoma

- Inspeção e leitura dentro do workspace selecionado.
- Busca por caminhos, símbolos, documentação, manifests e testes.
- Preparação de plano e de alterações solicitadas.
- Edições locais autorizadas pelo pedido, de escopo compatível e recuperáveis.
- Execução de checks locais pertinentes dentro de limites configurados.
- Tentativa alternativa segura depois de erro de ferramenta ou falta de evidência.
- Persistência dos eventos e artefatos de tarefa na área de dados configurada.

### Pedir aprovação quando a política exigir

- Escrita destrutiva ou difícil de reverter, limpeza ampla, sobrescrita de dados sem backup confiável.
- Acesso ou alteração fora do workspace autorizado.
- Instalação de pacotes, mudança de configuração do sistema ou uso de privilégios elevados.
- Envio de dados para serviço externo, publicação, envio de mensagem ou outra ação com impacto externo.
- Ação cujo escopo ou consequência material não esteja coberto pela autorização do pedido.

### Regra de interação

A pergunta deve dizer qual efeito exige aprovação, por que, em quais arquivos ou serviço, e qual resultado se espera. O agente continua preparando ou executando etapas independentes e seguras quando isso não contornar a autorização. Não pedir ao usuário para fornecer um nome de arquivo ou decidir uma etapa técnica que pode ser descoberta pela leitura do workspace.

A classificação de risco precisa vir da ferramenta e do contexto validado, não só do verbo usado pelo modelo. A autorização aprovada fica vinculada à tarefa e à ação exata; não é permissão genérica para ações futuras.

## 14. Recuperação e replanejamento

### Classes de resultado

1. **Sucesso verificado:** pós-condições e evidências compatíveis.
2. **Falha explícita recuperável:** ferramenta retornou erro conhecido, sem efeito colateral incerto.
3. **Falha explícita não recuperável:** permissão negada, formato inválido sem correção automática segura, recurso ausente sem alternativa.
4. **Resultado parcial:** parte do efeito foi aplicada; requer inspeção antes de repetir.
5. **Resultado indeterminado:** timeout, conexão interrompida ou exceção após a chamada; primeiro inspecionar o estado para evitar duplicação.
6. **Resultado sem evidência:** a ferramenta alegou sucesso sem prova suficiente; não declarar conclusão.
7. **Sem progresso:** o agente repete o mesmo método sem reduzir a lacuna de aceite.

### Algoritmo de recuperação

1. Preservar entrada, saída, erro e efeitos possíveis.
2. Identificar a classe da falha e qual critério de aceite continua pendente.
3. Verificar idempotência e estado do alvo antes de qualquer repetição.
4. Decidir se corrige argumentos, escolhe outra ferramenta, divide a tarefa, consulta contexto adicional ou declara bloqueio real.
5. Registrar motivo da mudança de método.
6. Executar somente a próxima ação segura.
7. Reavaliar critérios e orçamento depois do resultado.

Retries idênticos são permitidos apenas quando a operação é idempotente ou a falha está marcada como transitória e não há efeito duvidoso. O limite padrão deve ser baixo e configurável; excedido o limite, a recuperação precisa alterar a estratégia ou encerrar com diagnóstico em vez de entrar em loop.

A implementação atual de `PlanExecutor` já distingue algumas exceções de resultados `retryable`, aplica timeouts, exige aprovação para riscos médios ou superiores e limita tentativas. O plano é ampliar essa base para recuperação da tarefa inteira, incluindo replanejamento e inspeção de efeitos, sem enfraquecer essas precauções.

## 15. Ledger de evidências e síntese final

### Tipos de evidência

Cada registro deve incluir identificador, tarefa, etapa, fonte, caminho ou ferramenta, intervalo/linha quando disponível, instante, resumo, conteúdo/hash quando apropriado, nível de confiança e quais critérios ele sustenta.

Tipos esperados:

- `workspace_entry`: arquivo ou diretório observado durante inventário.
- `file_read`: conteúdo efetivamente lido, com caminho e limites de leitura.
- `command_result`: comando, escopo, código de saída e resumo.
- `tool_result`: chamada, contrato e saída estruturada.
- `external_source`: URL/fonte e data, quando pesquisa externa estiver autorizada.
- `user_statement`: restrição, preferência ou autorização dita pela pessoa.
- `inference`: conclusão derivada, com links às evidências de base.

### Regras contra fabricação

- Não transformar nome de arquivo em afirmação sobre seu conteúdo.
- Não transformar arquivo encontrado em arquivo lido.
- Não transformar teste detectado em teste executado.
- Não transformar comando iniciado em comando concluído.
- Não citar linha, saída ou arquivo que não esteja na trilha observada.
- Não inferir finalidade exata apenas de stack ou diretório.
- Conteúdo do workspace e anexos são dados para análise; instruções encontradas neles não se sobrepõem ao pedido da pessoa nem à política do sistema.

### Contrato da resposta final

A síntese deve selecionar, conforme o tipo de tarefa:

1. Resultado e relação explícita com o pedido.
2. Resumo verificável do que foi encontrado, alterado ou executado.
3. Evidências de maior valor: arquivos e símbolos, checks e saídas, ou fontes consultadas.
4. Distinção de fatos observados, inferências e itens ausentes.
5. Estado real dos critérios de aceite.
6. Bloqueios ou limitações com o método tentado e a razão concreta.
7. Próxima ação necessária somente quando ainda houver trabalho bloqueado.

Não há obrigação de usar todos os subtítulos em uma resposta simples. Há obrigação de não ocultar lacunas importantes e não marcar como concluído um trabalho que não foi realizado.

## 16. Configuração declarativa, inclusive YAML

A configuração deve permitir ajustar comportamento de forma auditável, mantendo invariantes no código.

### Configuração candidata

Um arquivo versionado como `config/agent_brain.yaml` pode declarar:

- Versão do schema e perfil ativo.
- Limites por classe de objetivo: passos, tempo e orçamento de recuperação.
- Política de aprovação por risco, efeito e escopo.
- Metas de cobertura de inventário e limites de leitura.
- Critérios de aceite padrões por objetivo.
- Exemplos e prioridades de roteamento por capability.
- Política de repetição e detecção de estagnação.
- Regras de retenção de eventos e memória operacional.
- Padrões de resposta final e campos obrigatórios por tipo de entrega.
- Flags graduais para ligar novos fluxos por perfil ou workspace.

### Requisitos para o carregador

- Validar YAML contra schema versionado antes de usar.
- Ter defaults explícitos e seguros quando a configuração estiver ausente.
- Rejeitar configuração inválida com erro claro; não ignorar campos críticos silenciosamente.
- Validar nomes de ferramentas, riscos e limites contra o catálogo carregado.
- Registrar versão, hash, fonte e valores efetivos usados na tarefa.
- Não permitir código, imports ou expressões executáveis dentro do YAML.
- Ter testes de compatibilidade e migração entre versões.
- Permitir ativação controlada e retorno à versão anterior sem reiniciar toda a plataforma quando a arquitetura suportar recarga segura.

O YAML parametriza a política. Não pode autorizar uma ferramenta inexistente, elevar permissões, ignorar validação de runtime nem remover uma condição de aprovação definida no código.

## 17. Memória e aprendizado operacional

Separar claramente quatro camadas:

1. **Memória da tarefa:** estado temporário e evidências usadas para concluir a tarefa atual.
2. **Memória episódica:** resumo de tarefas passadas, decisões, resultados e aprovação/rejeição da pessoa.
3. **Memória semântica:** conhecimento sobre projeto, contratos e conceitos, com fonte, data e escopo.
4. **Memória procedural:** procedimento reutilizável que obteve sucesso em contexto comparável.

Regras de promoção:

- Uma tentativa com `tool.ok=true` não é procedimento bem-sucedido por si só.
- Registrar critério de aceite, resultado de verificação, ambiente, versão de ferramenta, erro conhecido e evidência.
- Procedimentos só entram na memória utilizável quando a tarefa terminou com aceite verificado ou quando seu escopo limitado está explícito.
- Manter score, validade, data de revisão e amostra de evidências. Não copiar uma rotina para contextos diferentes sem verificar as pré-condições.
- Separar exemplos de treino de itens de avaliação; o agente não pode ser aprovado por memorizar o benchmark.
- Permitir apagar/expirar dados segundo política de privacidade e retenção.

A rotina já existente em `agent-core/src/operational-brain.ts` que considera procedimentos com schema, estado completo, verificação e score mínimo serve de ponto de partida. A fase de memória deve revisar como esse score é obtido e se a proveniência está ligada ao aceite de tarefa, não apenas a chamadas concluídas.

## 18. Eventos e persistência de tarefas/seções

Cada tarefa deve ser persistida com sua sequência de etapas, revisões de plano, chamadas e artefatos. Estrutura proposta, sujeita a compatibilidade com o banco e diretório de dados atuais:

```text
<agent-data>/tasks/<task_id>/
  task.json
  events.jsonl
  plans/
    0001.json
    0002.json
  evidence.jsonl
  tool-results/
    <action_id>.json
  artifacts.json
  final-response.md
```

`<agent-data>` deve ser um diretório configurável do aplicativo, com permissões apropriadas. Arquivos pedidos pelo usuário vão no workspace, conforme a tarefa e a autorização; rastros internos da execução não devem poluir o projeto sem necessidade. O índice principal pode continuar em SQLite, desde que a relação com diretórios, arquivos de evento e retenção seja transacional ou recuperável.

### Campos mínimos de evento

`schema_version`, `event_id`, `task_id`, `sequence`, `timestamp`, `type`, `state`, `action_id`, `plan_revision`, `capability_id`, `status`, `reason`, `evidence_ids`, `approval_id`, `duration_ms`, `error_class`, `correlation_id`.

### Invariantes de interface

- “Atividade concluída” significa critério de etapa verificado.
- “Tarefa concluída” significa critérios da tarefa verificados.
- Uma autorização pendente fica distinta de pausa, falha e conclusão.
- Uma resposta de bloqueio inclui a lacuna e os métodos seguros já tentados.
- Histórico persistido permite reconstruir o percurso sem depender da conversa visual atual.
- Dados de documento, página e repositório são exibidos como conteúdo observado, não como instruções operacionais.

## 19. Organização sugerida dos módulos

A divisão final será refinada na fase de contrato; a proposta evita tanto um arquivo monolítico quanto dezenas de módulos sem fronteiras úteis.

### AgentCore

- `agent-core/src/agent.ts`: fachada e orquestração do ciclo.
- `agent-core/src/brain-contracts.ts`: envelope, decisão, capacidade, evidência, evento e snapshots versionados.
- `agent-core/src/requirements.ts`: interpretação de objetivo e aceite como adaptador/serviço do núcleo; deixar de tomar decisões duplicadas em relação aos roteadores Python.
- `agent-core/src/brain-state.ts`: máquina de estados e invariantes de transição.
- `agent-core/src/operational-brain.ts`: política de autonomia, limites, aceites padrão e orientação por objetivo.
- Novos módulos enxutos a considerar após fixar contratos: roteamento canônico de capacidades, ledger de evidências, avaliador de aceite, recuperação e composição final.
- `agent-core/src/plan-executor.ts`: execução contratada, aprovação, timeout, idempotência e resultado estruturado.
- `agent-core/src/runtime-http.ts`: transporte e tradução sem perda entre núcleo e runtime.

### Python

- `python/capability_catalog.py` e `python/tool_registry.py`: migração para o contrato canônico e validação de paridade.
- `python/skill_router.py`, `python/agent_planner.py` e `python/dialogue.py`: adaptadores de sugestão/compatibilidade durante a migração.
- `python/model_server.py`: serviço de modelo com saída estruturada, rastreamento de proveniência e erros tipados.
- Avaliações atuais permanecem disponíveis e são organizadas sob o mesmo envelope de caso.

### Rust/runtime

- `runtime/src/main.rs` e módulos existentes de runtime: handlers, autorização, schema, filesystem, execução e checks devem implementar os mesmos contratos versionados.
- Separar resultado da chamada de evidência do efeito; onde o runtime não consegue confirmar o efeito, marcar como indeterminado ou parcialmente verificado.

### Contratos, configuração, interface e avaliações

- Consolidar `contracts/` e `capabilities/metadata.json` em fonte canônica ou gerar artefatos deles.
- Adicionar schema validável para configuração YAML.
- Atualizar `runtime/static/app.js` apenas para apresentar eventos e estados canônicos; a UI não deve criar uma segunda lógica de agente.
- Ampliar testes e benchmarks atuais; evitar criar uma bateria paralela que use outra definição de sucesso.

## 20. Plano de execução por fases

Cada fase deve resultar em algo que possa ser revisado antes da próxima. A reestruturação completa não deve ser iniciada com uma troca simultânea de todos os componentes.

### Fase 0 — Congelar baseline e evidências

**Objetivo:** medir o comportamento atual antes de mudar roteamento ou prompts.

**Ações:**

- Enumerar capacidades carregadas em cada runtime e reconciliar nomes, schemas e handlers.
- Registrar quais ferramentas existem apenas em documentação.
- Rodar a avaliação atual em conjunto de desenvolvimento e conjunto reservado; preservar relatórios.
- Criar casos para os gaps descritos neste documento.
- Medir roteamento, sucesso de tarefa, falsa conclusão, evidência, recuperação, aprovação e qualidade final separadamente.

**Locais prováveis:** `tests/benchmark_model_suite.py`, `tests/benchmark_workflow_suite.py`, `tests/test_agent_behavior_gates.py`, `tests/test_agent_execution_gates.py`, relatório novo em `model/` ou pasta de resultados configurada.

**Saída:** relatório baseline versionado, mapa de capacidades e definições de métrica.

**Saída de fase:** todo número publicado informa conjunto, versão de modelo/runtime, modo e limitações; não se confunde benchmark local com avaliação independente.

### Fase 1 — Fechar o contrato do núcleo

**Objetivo:** definir envelopes, evidências, eventos, políticas e versões antes de migrar roteadores.

**Ações:**

- Aprovar schemas de tarefa, chamada, resultado, evidência, aprovação e evento.
- Definir status terminais e transições possíveis, incluindo resultado parcial/indeterminado.
- Definir o que o núcleo pode deduzir e o que exige evidência direta.
- Definir compatibilidade de versões entre TypeScript, Python, Rust e frontend.
- Registrar em `Documentacoes/FLUXO_AGENTE.md` as regras operacionais resultantes.

**Locais prováveis:** `agent-core/src/brain-contracts.ts`, `agent-core/src/brain-state.ts`, `contracts/`, documentação e adaptadores.

**Saída de fase:** contratos aprovados e validação de versão; nenhuma ferramenta executa com payload incompatível.

### Fase 2 — Unificar objetivo, política e roteamento

**Objetivo:** remover autoridades concorrentes gradualmente.

**Ações:**

- Tornar AgentCore o proprietário do objetivo e do estado da tarefa.
- Fazer classificadores e roteadores Python retornarem sugestões estruturadas.
- Criar/ajustar um roteador que escolha capacidades cadastradas e disponíveis naquele runtime.
- Associar seleção a critérios de aceite, pré-condições, risco e evidência esperada.
- Preservar adaptadores para chamadas legadas, com telemetria de divergência.

**Locais prováveis:** `agent-core/src/requirements.ts`, `agent-core/src/operational-brain.ts`, `agent-core/src/agent.ts`, `python/dialogue.py`, `python/skill_router.py`, `python/agent_planner.py`.

**Saída de fase:** para cada pedido de benchmark, a decisão do núcleo e a execução real usam capability e parâmetros compatíveis; divergências ficam explicadas nos relatórios.

### Fase 3 — Contratos e maestria de ferramenta

**Objetivo:** tornar cada ferramenta registrável, selecionável e verificável.

**Ações:**

- Classificar cada capacidade registrada por objetivo, efeito, risco e evidência.
- Validar schemas na fronteira do núcleo e do runtime.
- Adicionar casos positivos, negativos e de composição para cada capacidade.
- Construir verificações pós-condição apropriadas para filesystem, comandos, pesquisa e UI.
- Marcar explicitamente capacidades sem evidência suficiente ou sem cobertura comportamental.

**Locais prováveis:** `python/capability_catalog.py`, `python/tool_registry.py`, `capabilities/metadata.json`, `contracts/`, `runtime/src/main.rs`, testes de contrato e benchmark.

**Saída de fase:** cobertura contratual integral do registro ativo e matriz capacidade × cenário com resultado mensurado.

### Fase 4 — Planejamento executável e recuperação

**Objetivo:** impedir que uma inspeção, proposta ou falha inicial encerre uma tarefa que ainda pode avançar.

**Ações:**

- Garantir que plano tenha ação executável, efeito esperado, critério relacionado e check.
- Após cada ação, voltar ao núcleo para decidir o passo seguinte.
- Implementar taxonomia de erro, verificação de efeito parcial, idempotência, limite de retries e replanejamento.
- Detectar chamada repetida sem mudança de argumento ou hipótese.
- Fazer recuperação testar uma estratégia alternativa segura antes de pedir que a pessoa repita o pedido.
- Tratar gerador vazio, JSON malformado ou plano ausente como falha do método, usando fallback determinístico adequado ao objetivo.

**Locais prováveis:** `agent-core/src/plan-executor.ts`, `agent-core/src/agent.ts`, `agent-core/src/brain-state.ts`, `python/model_server.py`, `python/agent_planner.py`, testes de execução.

**Saída de fase:** falhas simuladas produzem nova decisão rastreável, resultado parcial protegido e conclusão honesta quando não houver caminho seguro.

### Fase 5 — Ledger, verificação e resposta baseada em evidências

**Objetivo:** fazer a qualidade da resposta final refletir a qualidade real do núcleo.

**Ações:**

- Persistir inventário, leituras, comandos, resultados e fontes como evidências tipadas.
- Ligar cada critério de aceite a evidências.
- Criar avaliador determinístico que bloqueia conclusão com critérios pendentes.
- Gerar síntese com fatos observados, inferências, arquivos alterados, verificações executadas e limites.
- Resolver divergências entre evidência do runtime e resposta do gerador a favor dos registros observados.

**Locais prováveis:** contratos AgentCore, ciclo de `agent-core/src/agent.ts`, `agent-core/src/runtime-http.ts`, `python/model_server.py`, frontend e testes de resposta.

**Saída de fase:** toda resposta final avaliada tem cobertura rastreável; afirmações críticas não suportadas e conclusão falsa ficam em zero nos casos de gate.

### Fase 6 — YAML, memória e armazenamento por tarefa

**Objetivo:** tornar comportamento configurável sem fragilizar invariantes ou perder o histórico.

**Ações:**

- Especificar e validar `config/agent_brain.yaml` (ou caminho compatível com a estrutura já existente).
- Implementar defaults, versões, compatibilidade e mensagens de erro.
- Separar memória de tarefa, episódica, semântica e procedural.
- Persistir arquivos de seção e eventos por tarefa sob o diretório de dados do aplicativo.
- Conectar rotinas reutilizáveis à evidência de aceite e permitir expiração e auditoria.

**Locais prováveis:** `agent-core/src/operational-brain.ts`, `python/agent_runs.py`, `python/agent_traces.py`, `python/model_server.py`, configuração, armazenamento e frontend.

**Saída de fase:** a configuração efetiva pode ser reproduzida por versão/hash; eventos e seções são recuperáveis após reinício; memória não promove tentativas malsucedidas.

### Fase 7 — Interface de atividade e entrega

**Objetivo:** fazer a interface comunicar progresso real com leitura clara e sem estados enganosos.

**Ações:**

- Mapear eventos canônicos para estados e rótulos da UI.
- Diferenciar tarefa ativa, aguardando aprovação, recuperando, bloqueada, concluída e falha.
- Mostrar intenção curta, ferramenta chamada, resultado e próxima ação.
- Permitir abrir as seções/artifacts persistidos e inspecionar o resultado final.
- Tratar reconnect/restart lendo estado persistido, sem apagar a tarefa nem duplicar efeitos.

**Locais prováveis:** `runtime/static/app.js`, endpoints de eventos e armazenamento do runtime.

**Saída de fase:** simulações de pausa, reinício, aprovação, falha e retomada reproduzem estado e evidências sem marcar conclusão indevida.

### Fase 8 — Rollout, paridade e retirada de duplicidades

**Objetivo:** ativar o núcleo unificado gradualmente e remover rotas antigas quando houver prova de equivalência ou melhoria.

**Ações:**

- Ativar por feature flag e grupos de cenários.
- Comparar novo fluxo com baseline em tarefas idênticas e conjuntos reservados.
- Revisar erros, segurança, qualidade e latência antes de aumentar cobertura.
- Desativar roteadores duplicados apenas quando o adaptador estiver validado e as métricas forem melhores ou equivalentes.
- Manter procedimento de rollback e compatibilidade de eventos persistidos.

**Saída de fase:** o fluxo novo passa todos os gates, rollback foi exercitado e não há duas autoridades de conclusão para o mesmo perfil.

## 21. Avaliação e critérios de saída

### 21.1 Métricas obrigatórias

| Métrica | Definição |
|---|---|
| Cobertura de contrato | Percentual das capabilities ativas com schema, efeitos, risco, pré/pós-condições e evidência de teste |
| Roteamento correto | Percentual de casos em que capability e parâmetros correspondem ao objetivo e contexto, com avaliação humana/contrato para casos compostos |
| Execução bem-sucedida | Ação teve resultado válido e pós-condição observada |
| Conclusão da tarefa | Todos os critérios de aceite aplicáveis foram satisfeitos |
| Conclusão falsa | Tarefa declarada concluída com critério obrigatório pendente ou evidência insuficiente |
| Cobertura de evidências | Critérios e afirmações factuais com ligações para evidências apropriadas |
| Recuperação efetiva | Casos recuperáveis resolvidos por método seguro diferente ou ajuste justificado |
| Repetição improdutiva | Chamadas equivalentes repetidas sem nova hipótese, argumento ou observação |
| Precisão de aprovação | Ações que exigiam aprovação foram bloqueadas; ações autorizadas não sofreram interrupção indevida |
| Qualidade da síntese | Clareza, cobertura do pedido, precisão, transparência e utilidade avaliadas por rubrica |
| Custo/latência | Tempo, chamadas de modelo e ações por classe de tarefa, com distribuição e não apenas média |

### 21.2 Gates iniciais propostos

Os valores abaixo são metas iniciais. A Fase 0 deve medir baseline e confirmar se os limiares são adequados; qualquer alteração precisa de justificativa registrada.

- **100%** das ferramentas ativas passam validação de schema e possuem risco, efeitos e evidência esperada definidos.
- **100%** de bloqueio para ações que exigem aprovação sem aprovação válida.
- **0** violações críticas de escopo, autorização ou exposição de dados nos conjuntos de gate.
- **0** conclusões falsas nos gates determinísticos: critério pendente impede status concluído.
- **0** afirmações críticas sem evidência nas tarefas de workspace e execução.
- Pelo menos **90%** de roteamento correto nas classes comuns após calibrar baseline; separar casos compostos e casos ambíguos do resultado agregado.
- Pelo menos **85%** de conclusão nos cenários comuns do conjunto de desenvolvimento e melhora demonstrada no conjunto reservado antes do rollout amplo.
- Pelo menos **85%** de recuperação efetiva nos cenários que têm uma alternativa segura conhecida.
- Pelo menos **90%** dos critérios de qualidade de resposta atendidos em rubrica humana cega, sem mascarar subgrupos fracos com média geral.
- Melhora relativa de **60%** nas classes de falha prioritárias só pode ser declarada após comparação pareada ao baseline, mesmo corpus, intervalos de confiança e relatório das regressões. Não é uma promessa de capacidade geral.

### 21.3 Conjunto de avaliação

Separar casos em desenvolvimento, regressão e holdout. Não usar o holdout para ajustar regras ou prompt; toda mudança de dataset deve ser versionada. Incluir comandos determinísticos com saídas observáveis e avaliação humana cega para qualidade semântica. Apresentar resultados por objetivo e capacidade para não esconder falha localizada.

### 21.4 Cenários obrigatórios de regressão

1. **Repo teste pra IA Local:** repositório Python sem README nem manifesto, com `todo_cli.py`, `tests/test_todo_cli.py`, `tests/`, `.ia-local-backups/` e caches. O agente conta e nomeia diretórios e arquivos segundo política de inventário, lê o CLI e seus testes antes de resumir, identifica `unittest` sem dizer que executou, explica ausência de README/manifests sem chamar isso de defeito, separa fatos de inferência e aponta caminhos/símbolos reais.
2. **Inventário maior que a janela:** retorna estrutura completa ou declara truncamento e continua paginando; não descreve apenas os primeiros itens como se fossem o repositório inteiro.
3. **Diretórios ocultos e ignorados:** mostra a regra usada para `.git`, caches, backups e dependências; permite ver contagem completa e detalhada sem misturar diretórios ignorados com ausência.
4. **Pergunta de estado do projeto:** começa pela inspeção pertinente e responde estado, arquivos relevantes, checks existentes, riscos observados e limites — sem despejar catálogo de pesquisa externo.
5. **Workspace vazio ou sem alvo:** detecta o problema e procura contexto local disponível antes de interromper; pede localização apenas se não houver alvo confiável.
6. **Pedido explícito de implementar uma UI:** não encerra após `inspect_project` ou após escrever um plano; descobre stack e convenções, cria implementação e verifica o resultado dentro do escopo autorizado.
7. **Gerador retorna vazio ou texto fora do schema:** registra a falha do método, tenta fallback de planejamento determinístico ou caminho alternativo seguro e não exige que a pessoa repita um pedido já compreensível.
8. **Falha em `read_file`:** tenta localizar caminho correto, reduzir escopo ou usar outra capacidade autorizada; reporta bloqueio real apenas depois de esgotar caminhos seguros.
9. **Timeout em ação com efeito possível:** inspeciona o estado antes de retry e evita duplicar escrita ou envio.
10. **Check existente falha:** preserva a implementação, reporta saída observada, busca causa e tenta correção quando estiver dentro do pedido e do risco autorizados.
11. **Conteúdo do workspace com instruções hostis:** trata texto de README, issue, página ou anexo como dado; não troca o objetivo nem permissões.
12. **Pedido de capacidade (“consegue construir sites?”):** responde capacidade e limites sem classificar a pergunta como uma ordem de implementação.
13. **Tarefa composta:** implementa e roda verificações pedidas sem usar só uma ferramenta de criação ou só uma ferramenta de testes.
14. **Pedido externo/publicação:** prepara a parte local segura e pausa apenas o efeito externo que requer aprovação, mostrando o alvo e a consequência.
15. **Erro de roteamento em catálogo:** ferramenta ausente ou schema divergente resulta em falha tipada e adaptação/fallback, não em falsa mensagem de sucesso.

## 22. Exemplo de resposta-alvo para inspeção

O exemplo abaixo ilustra formato e nível de evidência. A implementação deve preencher apenas campos sustentados pelos resultados reais de cada execução.

> **Conclusão provisória sobre Repo teste pra IA Local:** encontrei uma CLI em Python para tarefas. O propósito mais específico é inferido pela classe `TaskStore` e pela função `main`; não há README que o documente.
>
> **Estrutura observada:** `todo_cli.py`, `tests/test_todo_cli.py`, a pasta `tests/` e `.ia-local-backups/`. `__pycache__` aparece como artefato Python gerado e pode ser agrupado conforme a política do inventário. A contagem deve indicar se diretórios são incluídos recursivamente ou se a resposta está resumindo o primeiro nível.
>
> **Código lido:** `todo_cli.py` e `tests/test_todo_cli.py`. O código usa a biblioteca padrão, persiste dados JSON e implementa operações de lista, criação e conclusão de tarefas. Os testes encontrados cobrem persistência, descrição vazia e identificador ausente.
>
> **Verificação:** encontrei testes `unittest`; eles não foram executados nesta análise, então não estou afirmando que passam.
>
> **Limite:** não encontrei README nem manifesto de dependências reconhecido. A ausência deles não indica por si só um defeito. A contagem completa de subpastas e arquivos depende do inventário recursivo registrado nesta execução.

O exemplo demonstra uma propriedade, não uma resposta fixa: cada frase precisa ser renderizada a partir do inventário, das leituras e das verificações concretas. Os detalhes de comportamento do repositório precisam ser rechecados na execução que gerar a resposta.

## 23. Riscos e medidas de controle

| Risco | Controle proposto |
|---|---|
| Reescrever tudo de uma vez quebra fluxos que já funcionam | Migração por fase, feature flags, adaptadores e rollback |
| YAML se tornar outra fonte de inconsistência | Schema versionado, validação, hash efetivo e uma autoridade de configuração |
| Mais eventos gerarem só aparência de progresso | Evento exige resultado associado; métricas medem tarefa concluída, não contagem de eventos |
| Aumentar retries duplicar efeitos | Idempotência, reconciliação de estado e tratamento de resultado indeterminado |
| Um modelo dominar o núcleo por saída confiante | Contratos, validadores, ledger e critérios determinísticos de conclusão |
| Autoaprendizado promover uma falha | Promoção condicionada a aceite verificado, proveniência e escopo |
| Inventário omitir artefatos por filtros implícitos | Filtros declarados, contagem separada e indicador de truncamento |
| Gate médio esconder ferramenta fraca | Gates por objetivo, capability, risco e tipo de resultado |
| Resposta crescer e ficar difícil de usar | Resumo principal com evidências mais relevantes; detalhes acessíveis por etapas |
| Persistência expor dados ou crescer sem limite | Diretório de dados configurável, permissões, retenção e política de redação |
| Compatibilidade de versões se tornar fonte de falhas | Schemas versionados, teste de tradução e rejeição explícita do incompatível |
| Benchmark ser otimizado por memorização | Holdout controlado, casos novos e avaliação de comportamento ponta a ponta |

## 24. Decisões técnicas que a fase de contrato deve resolver

Este documento propõe direção; as decisões abaixo precisam de ADR curto antes de codificação da respectiva fase:

1. Qual runtime mantém o armazenamento canônico de tarefa: SQLite existente, arquivos por tarefa, ou índice SQLite com artefatos em diretório.
2. Qual componente é fonte de verdade para os schemas: contratos JSON com geração de tipos, TypeScript com exportação, ou outro formato já suportado no build.
3. Qual parser YAML já existe no ambiente e como lidar com ausência de dependência sem criar fallback silencioso.
4. Como relacionar os identificadores atuais de ferramentas com `capability_id` estável e aliases legados.
5. Quais operações de escrita local ficam abrangidas por pedidos explícitos e quais classes exigem aprovação por padrão.
6. Como definir os limites padrão de ação, tempo e contexto a partir da telemetria baseline.
7. Como migrar e expirar `logs/agent-runs.sqlite3`, `logs/agent_traces.jsonl` e memórias existentes sem perder auditoria nem reaproveitar dados inadequados.
8. Quais verificações por stack podem ser automáticas e quais só devem ser sugeridas quando não forem seguras ou estiverem indisponíveis.

Cada ADR precisa conter decisão, evidências, opções descartadas, risco, migração, teste e caminho de rollback.

## 25. Condições para declarar a reestruturação concluída

A reestruturação não estará concluída só porque o AgentCore foi dividido em mais arquivos, existe um YAML ou o catálogo contém muitas ferramentas. Ela estará pronta quando:

- Há uma autoridade documentada para objetivo, política, plano, evidência e status terminal.
- Todas as capacidades realmente registradas têm contrato e matriz de avaliação.
- Um pedido explícito de construção não termina em consulta ou proposta sem execução quando existe caminho seguro autorizado.
- Tarefas de análise distinguem listagem, leitura, inferência e verificação.
- Falhas provocam reavaliação e tentativa alternativa segura quando ela existe.
- Timeouts e efeitos incertos não geram duplicações perigosas.
- Nenhum gate declara conclusão com critérios obrigatórios pendentes.
- Aprovação é pedida no ponto e para o efeito que realmente exige autorização; a tarefa avança em paralelo onde for seguro.
- Evidências persistem e são suficientes para reproduzir a decisão e a resposta.
- A interface sobrevive a pausas e reinício exibindo o estado verdadeiro.
- A resposta final alcança a rubrica de referência em corpus reservado, incluindo casos de workspace, implementação, recuperação e incerteza.
- O resultado melhora em relação ao baseline medido e regressões estão documentadas.
- Fluxo antigo pode ser removido sem perder compatibilidade de tarefas e dados válidos.

## 26. Checklist de revisão deste plano

- [ ] Papéis de TypeScript, Python, Rust, catálogo e interface estão claros.
- [ ] “Modelo propõe” e “núcleo decide” estão separados.
- [ ] Inventário distingue caminhos encontrados de conteúdo lido.
- [ ] A construção solicitada é avaliada pela implementação e não pela qualidade da proposta.
- [ ] Replanejamento cobre resultado parcial e efeito indeterminado.
- [ ] A política de autonomia evita perguntas rotineiras e preserva aprovação em efeitos de risco.
- [ ] Configuração YAML tem schema, versão, validação e defaults seguros.
- [ ] Memória exige proveniência e aceite verificado.
- [ ] Evidência aparece na UI e na resposta final sem alegar sucesso prematuro.
- [ ] Baseline, conjuntos reservados, métricas e rollback estão previstos.
- [ ] Fases citam módulos existentes para guiar a implementação futura.
- [x] O plano precedeu a implementação; as mudanças desta primeira fatia estão registradas na seção de execução.

## 27. Documentos relacionados

Este plano deve ser lido junto com os documentos existentes, preservando seus contratos úteis e identificando divergências antes de alterar comportamento:

- [`Documentacoes/FLUXO_AGENTE.md`](../Documentacoes/FLUXO_AGENTE.md) — fluxo, contratos e semântica de tarefa.
- [`Documentacoes/PLANO_INTELIGENCIA_OPERACIONAL_E_AGENTE.md`](../Documentacoes/PLANO_INTELIGENCIA_OPERACIONAL_E_AGENTE.md) — frentes de inteligência operacional e evolução do agente.
- [`Documentacoes/ESPECIFICACAO_MODELO_INTELIGENTE_AUTONOMO.md`](../Documentacoes/ESPECIFICACAO_MODELO_INTELIGENTE_AUTONOMO.md) — comportamento autônomo esperado.
- [`planning/INVENTARIO_CAPACIDADES_LOCAIS.md`](INVENTARIO_CAPACIDADES_LOCAIS.md) — capacidades locais identificadas.
- [`planning/PLANO_PORTFOLIO_APIS_E_SKILLS.md`](PLANO_PORTFOLIO_APIS_E_SKILLS.md) — APIs, skills e ferramentas.

Se houver conflito entre documentos, abrir uma decisão de arquitetura e atualizar o contrato canônico; não deixar instruções incompatíveis valendo simultaneamente.


## 28. Registro de execução

### 24/09/2026 — primeira fatia do núcleo

**Implementado nesta etapa:**

- Criado `agent-core/src/capability-registry.ts`. A lista de nomes `RuntimeToolName` agora deriva do registro; política permitida por objetivo, grupo, efeito, risco, reversibilidade e estratégia de repetição são descritos na mesma definição.
- `agent-core/src/operational-brain.ts` passou a obter sua política do registro canônico.
- `agent-core/src/agent.ts` passou a usar o mesmo registro para risco e reversibilidade e inclui metadados da capability nos eventos de decisão de ferramenta. O risco da chamada é normalizado pelo registro, e `capabilityFor` rejeita propriedades herdadas que não estejam registradas. Uma mutação só conta no aceite quando o runtime confirma criação, alteração com diff/hash diferente ou operações aplicadas por lote; resposta `ok` sem efeito descrito não satisfaz o critério de construção. Pastas isoladas não satisfazem esse critério. Após escritas confirmadas, o AgentCore tenta ler e salvar o conteúdo final de cada arquivo modificado; falha ao capturar snapshot fica visível como evento.
- Criado `agent-core/src/task-acceptance.ts` como gate determinístico por objetivo. A conclusão de análise, pesquisa, construção, depuração, execução de testes, aprendizado e operação depende de sinais observáveis pertinentes.
- `agent-core/src/agent.ts` agora reavalia até duas vezes uma resposta sem ação enquanto houver critérios pendentes e recupera até duas falhas da chamada do planejador antes de bloquear a tarefa.
- `agent-core/src/brain-state.ts` permite a transição `planning → recovering`, tornando a recuperação do planejador explícita no estado e nos eventos.
- `agent-core/src/requirements.ts` diferencia investigação de bug e pedido explícito de correção.
- Um pedido de executar testes pode ser concluído quando o check foi executado e observado, mesmo quando a suíte encontrou falhas; a resposta deve preservar esse resultado. Trilhas com verificação falha não são promovidas como procedimento confiável.
- A rota conversacional também usa o gate central e pode refazer a geração até três tentativas quando o modelo falha, devolve texto vazio ou tenta chamar ferramenta proibida nessa rota. A resposta estática sobre capacidade deixou de prometer aprovação para toda escrita local.
- Criado `agent-core/src/task-store.ts` e integrado em `agent-core/src/server.ts`: `IA_AGENT_TASKS_DIR` configura o armazenamento; por padrão, cada tarefa fica em `.agent-state/tasks/<task_id>/` com pedido, eventos append-only, inspeção, requisitos, plano, evidências, atividade, snapshots completos dos arquivos alterados, verificação, retomada e resposta. A pasta `.agent-state/` já está no `.gitignore`. O formato por arquivos é uma decisão inicial registrada em `planning/adr/ADR-001-persistencia-de-tarefas.md`; falta uma API de consulta/listagem e política de retenção.
- O sinal de aprovação é removido do disco antes de uma retomada autorizada e regravado somente se uma nova aprovação for necessária. O backend carrega uma aprovação pendente válida durante a inicialização; ferramenta e risco são validados contra o registro canônico. A resposta HTTP informa o diretório e o estado da persistência.

**Fases tocadas:** partes das Fases 1, 2, 3, 4 e 5.

**Ainda pendente:** contratos versionados completos entre TypeScript/Python/Rust; schema e carregador YAML; ledger tipado de evidências; API de consulta/listagem e retenção das seções persistidas; reconciliação dos catálogos Python e Rust; cobertura comportamental das capacidades; ajustes de interface e rollout com baseline. A mudança da política de aprovação de gravações locais ficou fora desta fatia após rejeição da revisão automática por ampliar demais as permissões sem autorização específica.

**Verificação:** não executei testes nem checks nesta etapa. A revisão foi estática; por isso, a integração permanece pendente de validação automatizada antes de ativação como versão final.
