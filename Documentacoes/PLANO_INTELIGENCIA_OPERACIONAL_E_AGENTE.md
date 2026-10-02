# Plano de evolução: inteligência operacional, contexto e experiência de agente

**Projeto:** IA Local do Zero  
**Escopo:** Chat, Workspace e Treinamento  
**Objetivo:** transformar a aplicação em um agente local observável, verificável e capaz de executar trabalho real com qualidade profissional.

## 1. Resumo executivo

O projeto já possui os elementos fundamentais: runtime Rust, worker local, pesquisa com fontes, acervo indexado, contratos de ferramentas, traces, workspace autorizado e um laboratório de competências. O problema atual não é a ausência de funcionalidades isoladas. É a falta de uma espinha dorsal única que faça essas funcionalidades trabalharem como um agente coerente.

Hoje, a interface ainda separa artificialmente conversa, workspace, atividades e treinamento. O usuário vê partes do processo, mas não possui uma visão contínua de:

```text
pedido -> entendimento -> contexto -> plano -> decisão -> ferramenta
      -> evidência -> artefato -> verificação -> aprendizado -> resposta
```

Este plano propõe uma evolução em sete frentes:

1. unificar Chat e Workspace em uma única superfície de trabalho;
2. fazer todos os modos emitirem o mesmo fluxo de eventos em tempo real;
3. colocar logs operacionais dentro da conversa, com detalhe recolhível;
4. transformar chamadas de ferramenta em operações tipadas, auditáveis e canceláveis;
5. tratar código como artefato revisável, com overlay, diff, diagnósticos e testes;
6. transformar Treinamento em um ciclo de competência comprovada, não em simples coleta de páginas;
7. criar memória operacional, avaliação contínua e métricas que permitam melhorar o agente sem fingir capacidade.

O alvo não é apenas gerar textos mais longos. É construir um agente que entenda o objetivo, escolha ações adequadas, execute com segurança, saiba quando está incerto, aprenda com evidência e demonstre o resultado.

## 2. Visão de produto

### 2.1 O que significa competir com modelos maiores

“Ficar frente a frente” com Claude, GPT ou Gemini não deve significar copiar a aparência ou prometer a mesma escala de conhecimento bruto. O diferencial do projeto é ser melhor no trabalho local e verificável:

- entender o projeto e o contexto autorizado;
- agir sobre arquivos, testes e ferramentas sem perder rastreabilidade;
- mostrar o que está fazendo sem expor raciocínio interno privado;
- pesquisar e citar fontes reais;
- distinguir fato, hipótese, instrução encontrada e conteúdo fictício;
- converter conhecimento em competência prática;
- preservar privacidade e operar localmente;
- recuperar uma tarefa interrompida sem perder seu estado;
- admitir que ainda não sabe e iniciar uma busca adequada.

Fluência textual é apenas um componente. A métrica principal será a taxa de tarefas concluídas corretamente, com evidência e dentro de um tempo aceitável.

### 2.2 Princípios inegociáveis

- **Local-first:** o chat e as ferramentas principais funcionam sem Ollama, sem modelo externo e sem depender de um serviço remoto.
- **Evidência antes de confiança:** uma fonte localizada não equivale a conhecimento aprendido; competência precisa de prática, transferência e verificação.
- **Observabilidade sem teatro:** o sistema mostra eventos reais do agente, não animações ou progresso inventado.
- **Dados não são instruções:** conteúdo de repositórios, páginas, PDFs e arquivos pode conter texto malicioso. Deve ser tratado como evidência, nunca como autoridade operacional.
- **Ações reversíveis por padrão:** toda escrita produz diff, backup ou operação reversível quando possível.
- **Permissões explícitas:** ler, pesquisar, editar, executar, excluir e acessar rede são capacidades diferentes.
- **Memória versionada:** fatos, habilidades, preferências, políticas e traces devem ter origem, data, versão e possibilidade de rollback.
- **Incerteza honesta:** o agente não deve completar uma resposta só para parecer rápido.
- **Separação de responsabilidades:** Rust controla transporte, limites, permissões, workspace e ciclo operacional; Python controla pesquisa, recuperação, planejamento, aprendizagem e geração local.

## 3. Diagnóstico do estado atual

### 3.1 O que já existe

O repositório já fornece uma base aproveitável:

- `runtime/src/main.rs` expõe HTTP local, workspace autorizado, chamadas de ferramentas, pesquisa e chat;
- `runtime/static/app.js` consulta `/api/activity`, atualiza ações e gerencia Chat, Workspace e Treinamento;
- `runtime/static/index.html` já possui painéis distintos para conversa, workspace e treinamento;
- `python/tool_registry.py` lê contratos JSON e valida argumentos básicos;
- `python/agent_traces.py` registra seleção de ferramentas, resultados e conclusão;
- `python/task_graph.py` explicita dependências simples de tarefas;
- `python/learning.py`, `curriculum.py`, `skill_lab.py` e `agent_state.py` sustentam pesquisa e competências;
- `corpus/` separa conhecimento factual, índices, fontes e material de treinamento;
- `logs/agent_traces.jsonl` já funciona como ponto inicial de auditoria;
- o projeto rejeita checkpoints de geração que produzem texto corrompido, repetição ou saída vazia.

O exportador de workflow deve gerar um exemplo por decisão, incluindo o
objetivo, ações anteriores e observações reais. Esses candidatos ficam em
`corpus/raw/workflow_planner_candidates.jsonl`, marcados para revisão humana.
A curadoria registra cada decisão em `corpus/review/workflow_planner_decisions.jsonl`
e materializa somente as aprovações em `corpus/review/workflow_planner_approved.jsonl`.
O arquivo bruto não é alterado; traces brutos de execução não entram
automaticamente no treino, mesmo quando as ferramentas terminaram sem erro.

Na aba **Treinamento**, a fila de revisão apresenta o objetivo, a ação
selecionada, o estado anterior e o ciclo completo ligado ao trace: ações,
resultados observados e resposta final. A continuação posterior é evidência
para a pessoa revisora e fica fora do contexto/rotulagem do planejador, evitando
vazamento do futuro no exemplo. Aprovar ou rejeitar exige identificador do
revisor e justificativa; aprovações atualizam o snapshot curado, sem iniciar
treino nem promover o índice automaticamente.

A validação mantém as decisões de cada trace no mesmo grupo. Se houver menos
de dois grupos independentes, a rodada não inventa um holdout e a promoção
permanece bloqueada por falta de validação.

### 3.2 Limitações que impedem uma experiência profissional

1. **Eventos pobres:** o evento atual contém `id`, `operation`, `phase`, `message`, `done`, tempo e progresso. Isso não identifica pai, tarefa, ferramenta, artefato, permissão, fonte ou resultado estruturado.
2. **Polling como transporte principal:** a consulta periódica de `/api/activity` funciona para um protótipo, mas não oferece reconexão semântica, backpressure, agrupamento, streaming de saída ou garantia de entrega.
3. **Logs afastados da resposta:** a atividade aparece como estado técnico separado, em vez de acompanhar a mensagem que a originou.
4. **Modos isolados:** Chat e Workspace alternam a tela. O treinamento possui outra superfície e outro ciclo de atualização. O agente não tem uma sessão unificada.
5. **Ferramentas com contrato incompleto:** há validação de tipo e campos obrigatórios, mas ainda faltam versão, capacidades, escopo, idempotência, pré-condições, risco, limites e efeitos esperados.
6. **Código sem artefato de primeira classe:** blocos de código precisam de origem, caminho, linhas, diff, diagnóstico, ação de aplicar e resultado de teste.
7. **Progresso impreciso:** porcentagens fixas ou derivadas de tempo podem sugerir avanço que não corresponde ao trabalho real.
8. **Treinamento medido por cobertura, não por domínio:** documentos e fontes são contabilizados, mas devem ser separados de conceitos expostos, praticados, transferidos e dominados.
9. **Memória operacional fragmentada:** traces, estado do agente, acervo, habilidades e histórico ainda não formam uma memória consultável e versionada.
10. **Pouca avaliação de comportamento:** é necessário testar planejamento, escolha de ferramentas, segurança, recuperação após falha, pesquisa, aprendizagem e execução de código — não apenas a resposta final.

## 4. Arquitetura-alvo: um agente, três superfícies

### 4.1 Unificar Chat e Workspace

Chat e Workspace devem deixar de ser dois modos mentais separados. Devem ser duas visualizações do mesmo **Modo Agente**:

- **Conversa:** explica, pergunta, planeja e resume;
- **Projeto:** mostra arquivos, diffs, prévias, testes e artefatos;
- **Inspector:** mostra fontes, permissões, ferramentas, contexto e timeline detalhada.

O cabeçalho pode continuar oferecendo atalhos “Chat” e “Workspace” por familiaridade, mas a sessão, o histórico, os eventos e a tarefa devem ser os mesmos. Trocar a visualização não pode reiniciar o contexto nem esconder o trabalho em andamento.

### 4.2 Treinamento como especialização do mesmo agente

Treinamento não deve ser um painel desconectado. Ele deve abrir uma tarefa do agente com tipo `training_run` e usar o mesmo:

- identificador de sessão;
- fluxo de eventos;
- catálogo de ferramentas;
- sistema de permissões;
- memória e fontes;
- registro de artefatos;
- mecanismo de cancelamento e retomada;
- relatório final dentro do chat.

O painel de Treinamento continua útil para comparar competências, lacunas e trilhas, mas a execução deve aparecer na conversa como uma operação acompanhável.

### 4.3 Estado unificado da sessão

Cada sessão deve ter um snapshot como este:

```json
{
  "session_id": "sess-...",
  "conversation_id": "conv-...",
  "project_id": "project-...",
  "workspace_root": "/caminho/autorizado",
  "surface": "agent",
  "view": "chat|workspace|training|inspector",
  "active_task_id": "task-...",
  "context": {
    "selected_files": [],
    "open_artifacts": [],
    "recent_sources": [],
    "user_preferences": {}
  },
  "last_event_seq": 0,
  "memory_snapshot": "memory-..."
}
```

O estado da interface é uma projeção desse snapshot. A interface não deve ser a única fonte de verdade.

## 5. Protocolo operacional em tempo real

### 5.1 Envelope único de evento

Todos os componentes devem publicar eventos no mesmo formato:

```json
{
  "schema": "agent-event/v2",
  "event_id": "evt-uuid",
  "seq": 142,
  "timestamp": "2026-09-19T22:00:00Z",
  "session_id": "sess-uuid",
  "task_id": "task-uuid",
  "trace_id": "trace-uuid",
  "parent_id": "evt-141",
  "kind": "tool.progress",
  "phase": "execution",
  "status": "running",
  "title": "Lendo manifestos do projeto",
  "detail": "Cargo.toml e package.json analisados",
  "progress": {"value": 42, "known": true},
  "elapsed_ms": 834,
  "actor": "runtime-rust|worker-python|agent|user",
  "tool_call_id": "call-uuid",
  "artifact_ids": [],
  "source_ids": [],
  "visibility": "user|technical|audit",
  "cancellable": true,
  "retryable": true,
  "payload": {}
}
```

Campos obrigatórios: `schema`, `event_id`, `seq`, `timestamp`, `session_id`, `task_id`, `kind`, `status` e `actor`. Os campos opcionais devem ser omitidos quando não se aplicarem, não preenchidos com texto genérico.

### 5.2 Eventos mínimos

```text
message.received
context.started / context.item / context.completed
intent.classified
plan.created / plan.revised
decision.made
approval.required / approval.granted / approval.denied
tool.requested / tool.queued / tool.started
tool.progress / tool.output / tool.completed / tool.failed / tool.cancelled
source.search_started / source.opened / source.rejected / source.assessed
evidence.added / evidence.conflict
artifact.created / artifact.updated / artifact.diff_ready
verification.started / verification.passed / verification.failed
training.objective / training.practice / training.transfer / training.promoted
response.started / response.delta / response.completed
task.paused / task.resumed / task.cancelled
error.recoverable / error.fatal
```

### 5.3 Transporte e persistência

Implementar em duas camadas:

1. **SSE ou WebSocket no Rust:** entrega imediata para a interface, heartbeat, reconexão e cancelamento.
2. **log append-only local:** persistência em JSONL ou SQLite leve, com snapshots periódicos para replay e auditoria.

Endpoints previstos:

```text
GET  /api/events?session_id=...&after_seq=...
GET  /api/stream?session_id=...&after_seq=...
POST /api/tasks
GET  /api/tasks/{task_id}
POST /api/tasks/{task_id}/cancel
POST /api/tasks/{task_id}/resume
```

Regras:

- `after_seq` permite retomar sem perder eventos;
- eventos repetidos são ignorados por `event_id`;
- a interface mostra um aviso quando há lacuna de sequência;
- o servidor limita o tamanho de payload e envia artefatos por referência;
- logs antigos são compactados em snapshots sem remover a auditoria;
- quando a rede local cai, a tarefa continua ou é marcada como interrompida de forma explícita;
- o progresso pode ser `known: false`; não inventar percentual para operações indeterminadas.

## 6. Comunicação visual: logs dentro do chat

### 6.1 Princípio de apresentação

Toda mensagem do usuário deve produzir uma unidade visual única contendo:

1. mensagem recebida;
2. plano resumido, quando houver trabalho composto;
3. timeline operacional em tempo real;
4. artefatos, fontes e chamadas de ferramenta;
5. resposta final e validação.

O painel externo pode existir como inspector avançado, mas nunca deve ser o único lugar onde o usuário consegue acompanhar a tarefa.

### 6.2 Cartão de operação

O cartão deve mostrar, sem abrir nada:

- nome humano da operação;
- estado atual: preparando, executando, aguardando confirmação, concluída ou falhou;
- tempo decorrido;
- progresso apenas quando mensurável;
- última ocorrência real;
- botão de cancelar quando permitido;
- quantidade de ferramentas, fontes, artefatos e verificações.

Ao expandir “Etapas”, mostrar a árvore de eventos agrupada por fase. Não despejar cada linha de stdout no fluxo principal. Saídas extensas ficam em blocos recolhíveis com pesquisa, cópia e download local.

### 6.3 Níveis de detalhe

- **Essencial:** plano, ação atual, resultado, fonte, teste e erro.
- **Detalhado:** argumentos sanitizados, duração, dependências, arquivos afetados e decisões.
- **Auditoria:** trace completo, hashes, versões, permissões, retries e payloads estruturados.

O usuário escolhe o nível por sessão. A tarefa nunca perde dados porque a interface está no modo essencial.

### 6.4 Resposta progressiva

A resposta deve começar somente quando houver conteúdo útil, não com um texto vazio. Enquanto a geração ocorre, usar `response.delta` e mostrar:

- estado “redigindo”;
- tokens/bytes recebidos, quando disponível;
- fontes já associadas;
- avisos de incerteza;
- possibilidade de cancelar.

Não exibir raciocínio interno privado. Exibir apenas decisões operacionais, evidências, ações e resultados verificáveis.

## 7. Overlays de código e artefatos profissionais

### 7.1 Código como artefato

Todo código produzido ou lido deve poder ser representado como `CodeArtifact`:

```json
{
  "artifact_id": "artifact-uuid",
  "kind": "code",
  "path": "src/main.rs",
  "language": "rust",
  "content": "...",
  "base_hash": "sha256:...",
  "source": {"task_id": "task-...", "source_ids": []},
  "anchors": [{"start_line": 12, "end_line": 28}],
  "diagnostics": [],
  "tests": [],
  "status": "proposed|applied|rejected|verified"
}
```

### 7.2 Overlay mínimo

Cada bloco de código deve oferecer:

- caminho e linguagem;
- linhas e âncoras no arquivo;
- copiar;
- abrir no Workspace;
- explicar trecho;
- buscar referências;
- executar verificação permitida;
- propor aplicação;
- abrir diff;
- rejeitar ou reverter;
- mostrar fonte ou decisão que originou o trecho.

### 7.3 Aplicação segura

O agente nunca deve substituir o arquivo diretamente a partir de um bloco de texto. O fluxo correto é:

```text
proposta -> patch estruturado -> validação de base_hash
-> diff visual -> confirmação -> backup -> aplicação
-> testes/diagnósticos -> resultado -> possibilidade de rollback
```

Se o arquivo mudou desde a leitura, o patch deve ser recalculado ou rejeitado por conflito. Linhas afetadas, comandos e testes devem aparecer no cartão da tarefa.

### 7.4 Evolução da interface

Primeiro, usar um visualizador de diff e editor já compatível com o frontend atual. Depois, adicionar:

- navegação por símbolos;
- diagnósticos por linha;
- seleção com pergunta contextual;
- comparação entre versões do artefato;
- preview para HTML/CSS;
- resultado de testes ancorado no trecho;
- integração com VS Code usando o mesmo `artifact_id`.

## 8. Function calls em nível profissional

### 8.1 Contrato de ferramenta

Cada contrato em `contracts/` deve evoluir para incluir:

```json
{
  "name": "edit_file",
  "version": "1.2.0",
  "description": "Aplica um patch dentro do workspace autorizado",
  "arguments": {"type": "object", "additionalProperties": false},
  "capabilities": ["workspace.write"],
  "risk": "write",
  "requires_approval": true,
  "idempotent": false,
  "timeout_ms": 30000,
  "limits": {"max_input_bytes": 262144, "max_output_bytes": 1048576},
  "preconditions": ["workspace_selected", "base_hash_matches"],
  "effects": ["file_modified", "backup_created"],
  "rollback": "restore_backup",
  "retry_policy": "never_after_partial_write"
}
```

O `ToolRegistry` deve validar o schema completo, rejeitar propriedades desconhecidas, normalizar argumentos, gerar um `tool_call_id` único e registrar a versão do contrato usada.

### 8.2 Ciclo de vida da chamada

```text
requested
  -> validated
  -> approval_required (se aplicável)
  -> queued
  -> started
  -> progress / output
  -> completed | failed | cancelled
  -> verified | rollback_required
```

O resultado deve separar `data`, `stdout`, `stderr`, `warnings`, `artifacts`, `sources`, `verification` e `error`. Nunca transformar uma falha de ferramenta em uma frase genérica que pareça sucesso.

### 8.3 Política de autorização

- leitura local e consulta ao índice: aprovação automática;
- pesquisa externa: automática quando a política da sessão permitir, com registro de domínio e fontes;
- criação de arquivo ou diretório: confirmação inline;
- edição: diff e confirmação inline;
- execução de teste ou build: automática dentro do workspace e dos limites;
- comando com risco elevado, acesso fora do workspace, rede externa ou exclusão: confirmação explícita;
- exclusão: sempre confirmação com alvo, impacto e possibilidade de recuperação;
- credenciais, `.env`, chaves e segredos: nunca expostos ao modelo sem política dedicada.

O cartão de aprovação deve explicar: **o que será feito, em qual alvo, por quê, quais efeitos pode causar e como desfazer**.

### 8.4 Execução concorrente

- ferramentas somente de leitura podem ser paralelizadas;
- escritas no mesmo workspace devem ser serializadas;
- cada chamada recebe `idempotency_key`;
- retries só ocorrem quando o contrato declarar segurança;
- toda chamada pode ser cancelada antes do commit;
- falhas parciais produzem estado explícito e instrução de recuperação;
- ações destrutivas precisam de operação compensatória ou bloqueio.

## 9. Inteligência operacional do agente

### 9.1 Pipeline de decisão

O caminho padrão para qualquer pedido deve ser:

```text
classificar intenção
-> identificar objetivo e critérios de sucesso
-> carregar contexto mínimo suficiente
-> consultar memória e acervo
-> avaliar evidência e conflitos
-> escolher plano
-> selecionar ferramentas tipadas
-> executar com permissões
-> verificar resultado
-> atualizar memória operacional
-> responder com fontes, limitações e próximo passo
```

O agente deve poder parar entre as fases e pedir esclarecimento. “Responder logo” não é um critério de sucesso quando faltam dados para agir corretamente.

### 9.2 Memórias diferentes para problemas diferentes

Manter separadas:

- **memória episódica:** o que ocorreu em tarefas anteriores;
- **memória semântica:** fatos e conceitos com fontes;
- **memória procedural:** como executar tarefas e ferramentas;
- **memória de projeto:** estrutura, decisões, convenções e testes do workspace;
- **memória de usuário:** preferências explicitamente autorizadas;
- **memória de competência:** conceitos expostos, praticados, transferidos e dominados;
- **memória de falhas:** erros reproduzíveis, tentativas e correções.

Cada registro precisa de `source`, `created_at`, `updated_at`, `confidence`, `scope`, `version` e `expires_at` quando for informação mutável.

### 9.3 Distinção entre real, fictício e desconhecido

O classificador deve separar:

- entidade confirmada por fonte adequada;
- entidade existente, mas com evidência insuficiente;
- hipótese do agente;
- ficção ou exemplo inventado;
- termo não encontrado.

Para nomes inventados, como um identificador sem resultados, o agente não deve criar uma biografia. Deve dizer que não encontrou evidência, registrar as consultas realizadas e perguntar se o usuário quer tratar o termo como conceito fictício, projeto local ou nova entidade a ser definida.

### 9.4 Recuperação de conhecimento

A pesquisa deve evoluir de busca simples para recuperação em camadas:

1. normalização do pedido e extração de entidades;
2. consulta lexical local;
3. consulta por sinônimos e aliases técnicos;
4. busca por documentação oficial;
5. busca por exemplos e testes;
6. abertura e extração de conteúdo;
7. deduplicação e avaliação de qualidade;
8. comparação de fontes e detecção de conflito;
9. seleção de trechos com âncoras;
10. síntese com citações e grau de confiança.

Para programação, os sinais devem incluir manifestos, extensões reais, exemplos compiláveis, testes, APIs chamadas, versões e dependências. Uma menção em README não pode elevar uma linguagem para “dominada”.

## 10. Treinamento e aquisição de competência

### 10.1 O treinamento correto

“Aprender Rust” ou “estudar um repositório” deve iniciar uma missão de competência, não uma pesquisa curta. O agente precisa:

1. definir o escopo e a versão do assunto;
2. mapear conceitos e pré-requisitos;
3. selecionar fontes primárias e exemplos;
4. coletar, filtrar, deduplicar e registrar proveniência;
5. construir uma matriz de cobertura;
6. gerar exercícios e casos de falha;
7. executar laboratórios isolados;
8. testar transferência para problemas não vistos;
9. revisar erros e preencher lacunas;
10. promover somente o que passou pelos critérios.

### 10.2 Estados de competência

Usar estados distintos, sem confundir quantidade de páginas com domínio:

```text
UNKNOWN
EXPOSED
PARTIALLY_KNOWN
PRACTICED
TRANSFER_VERIFIED
MASTERED
STALE
BLOCKED
```

O card deve mostrar, no mínimo:

- domínio estimado e confiança calibrada;
- conceitos cobertos e ainda faltantes;
- fontes independentes e repositórios de origem;
- práticas tentadas, aprovadas e reprovadas;
- testes de transferência;
- falhas e contradições;
- data e versão da evidência;
- comportamento que mudou no agente por causa da competência.

### 10.3 Critério de promoção

Uma competência só pode ser promovida para `MASTERED` quando cumprir uma política configurável, por exemplo:

- documentação primária e pelo menos uma fonte independente;
- cobertura completa do currículo definido;
- exercícios fundamentais e avançados aprovados;
- exemplos compilados/testados quando aplicável;
- resolução de tarefa nova sem copiar o exemplo;
- integração com o workspace;
- ausência de contradições críticas;
- regressão executada contra competências anteriores;
- evidência rastreável e removível.

“100%” significa 100% do currículo e da avaliação definidos, não conhecimento absoluto de toda a linguagem ou framework.

Uma suíte fixa que cria a própria solução e executa testes valida apenas seus
exercícios de referência e o ambiente local. Ela deve aparecer separada de uma
tarefa que o agente resolveu. Para contar como desempenho, o agente precisa
produzir o artefato, submetê-lo a testes independentes e preservar a evidência;
transferência e integração continuam sendo critérios distintos.

### 10.4 Efeito real no comportamento

Aprender deve alterar políticas operacionais, não apenas adicionar texto ao índice. Após a promoção, a competência pode:

- melhorar a seleção de ferramentas;
- mudar os arquivos que o agente prioriza;
- ativar verificações específicas;
- acrescentar regras de estilo ou segurança;
- alterar o formato dos exemplos;
- reduzir confiança quando a versão estiver desatualizada;
- solicitar testes adicionais em pontos de risco.

Essas mudanças devem ser versionadas como políticas e poder ser revertidas. Não alterar pesos do modelo automaticamente com base em uma única consulta.

### 10.5 Avaliação integral de problemas inéditos

Uma avaliação de capacidade deve começar por um cenário que o agente não viu
durante o treino e medir a trajetória completa, não apenas a resposta ou a
aprovação de um teste fixo:

1. interpretar o objetivo, restrições e critérios de sucesso; pedir contexto
   quando uma decisão bloqueante não puder ser inferida;
2. inspecionar o workspace e mapear entradas, dependências, arquitetura e
   verificações disponíveis;
3. pesquisar fontes primárias quando a tarefa depender de informação externa,
   recente ou ainda ausente no projeto;
4. propor um plano curto, com hipóteses, riscos, artefatos esperados e uma
   forma observável de validar cada etapa;
5. executar ações por ferramentas locais com escopo, aprovação e registro de
   resultados;
6. comparar os resultados com os critérios, investigar falhas e revisar o
   plano dentro de um orçamento limitado;
7. entregar mudanças e evidências, distinguir o que passou do que segue
   pendente e registrar a trajetória para revisão humana.

O AgentCore possui ciclo de decisões locais, execução e verificação. Os
contratos TypeScript aceitam `propose_repair` (somente validação do diff) e
`apply_repair` (alteração com autorização). A autorização apresenta o diff
exato ao usuário; depois da aplicação o ciclo executa o verificador e registra
o resultado. A próxima lacuna é devolver diagnóstico e novas observações ao
planejador para propor a correção seguinte. Até existir uma nova proposta
contextual válida, a tarefa fica bloqueada em vez de alegar conclusão.

O benchmark deve pontuar cada etapa e o resultado final. Cenários de
transferência devem variar os projetos, requisitos e falhas; testes ocultos
precisam ficar fora do treino. A interface pode mostrar fase, ferramenta,
progresso, evidência e próximo passo em tempo real, com uma justificativa
operacional breve, sem expor raciocínio privado. Traces só entram em datasets
curados depois de revisão; uma execução bem-sucedida isolada não atualiza
automaticamente os pesos nem promove domínio geral.

## 11. Repositórios como material didático

Quando o usuário enviar um repositório, a missão deve produzir:

- URL, commit ou tag e data de coleta;
- licença e política de uso;
- mapa de diretórios relevantes;
- linguagens, frameworks e versões identificados;
- conceitos ensinados por arquivo ou módulo;
- testes, exemplos e comandos de validação;
- dependências e pré-requisitos;
- pontos fracos, lacunas e conteúdo não didático;
- evidência que sustenta cada competência;
- tarefas práticas derivadas do repositório;
- relatório de aproveitamento: o que agregou, o que foi descartado e por quê.

O coletor não deve absorver o repositório inteiro indiscriminadamente. Deve priorizar documentação, exemplos, testes, módulos centrais e histórico recente relevante. Arquivos gerados, dependências vendorizadas, binários e código auxiliar devem ser filtrados.

## 12. Desempenho sem sacrificar qualidade

### 12.1 Divisão Rust/Python

**Rust:**

- servidor local e streaming de eventos;
- multiplexação de sessões;
- limites de tamanho e tempo;
- permissões e sandbox do workspace;
- execução de ferramentas e cancelamento;
- diffs, hashes, backups e artefatos;
- cache, filas e controle de concorrência;
- métricas de transporte.

**Python:**

- classificação, recuperação e ranking;
- planejamento e grafo de tarefas;
- pesquisa e avaliação de fontes;
- currículo, laboratório e competência;
- geração local e quality gate;
- análise dos traces e avaliação do agente.

O objetivo não é reescrever tudo em Rust. É mover para Rust os caminhos de infraestrutura onde serialização, polling, limites e controle de processos causam custo, preservando em Python a lógica que muda com maior frequência.

### 12.2 Contexto adaptativo

O agente deve calcular o contexto necessário por tarefa:

```text
contexto = instruções estáveis
         + pedido atual
         + resumo da conversa relevante
         + arquivos selecionados
         + evidências recuperadas
         + resultados de ferramentas
         + políticas da competência
```

Registrar `context_requested`, `context_used`, `estimated_tokens`, `truncated_items` e `selection_reason`. Incluir mais tokens não corrige contexto irrelevante.

### 12.3 Paralelismo e cache

- pesquisar fontes independentes em paralelo;
- ler manifestos e listar diretórios em paralelo;
- indexar incrementalmente por hash;
- reutilizar fontes e artefatos imutáveis;
- não executar escritas concorrentes no mesmo arquivo;
- cancelar pesquisa quando já houver evidência suficiente;
- medir tempo por etapa, não apenas tempo total.

### 12.4 Métricas de tempo

Medir sempre:

- tempo até o primeiro evento;
- tempo até o primeiro conteúdo útil;
- tempo até a primeira ferramenta;
- tempo por ferramenta;
- tempo de pesquisa;
- tempo de geração;
- tempo total;
- p50, p95 e p99;
- tokens/bytes de entrada e saída;
- CPU, RAM e tamanho do contexto;
- taxa de cancelamento e repetição.

Uma resposta instantânea e errada é uma regressão, não uma vitória.

## 13. Segurança, confiabilidade e recuperação

- manter todas as operações dentro do workspace autorizado;
- redigir segredos dos eventos, traces e respostas;
- impor limites de caminho, tamanho, tempo e processo;
- bloquear symlinks e traversal;
- registrar origem de cada instrução usada pelo plano;
- tratar prompt injection de documentos como conteúdo suspeito;
- validar saída de ferramentas antes de entregá-la ao planejador;
- criar backup antes de escrita;
- permitir cancelar e retomar tarefas;
- preservar estado após reinício do runtime;
- registrar falhas parciais e ações de recuperação;
- exigir confirmação antes de excluir competências, fontes, arquivos ou histórico;
- oferecer `dry_run` para operações que tenham efeitos.

## 14. Avaliação de inteligência operacional

Criar uma suíte reservada, separada do corpus de treinamento, com cenários:

1. pergunta factual com fonte;
2. entidade fictícia ou inexistente;
3. projeto desconhecido que precisa ser inspecionado;
4. bug que exige ler, editar e testar;
5. tarefa composta com dependências;
6. ferramenta com argumento inválido;
7. falha parcial e retry seguro;
8. conflito entre duas fontes;
9. repositório para estudo;
10. treinamento com exercício e transferência;
11. pedido destrutivo que exige confirmação;
12. tentativa de instrução maliciosa dentro de um documento;
13. reconexão da interface durante tarefa longa;
14. cancelamento no meio de uma chamada;
15. pergunta de continuação que depende do contexto anterior.

Métricas:

- sucesso operacional da tarefa;
- correção factual e qualidade das citações;
- seleção e argumentos de ferramenta;
- percentual de ações verificadas;
- abstinência correta;
- taxa de falsos sucessos;
- preservação de contexto;
- tempo p50/p95;
- taxa de intervenção do usuário;
- qualidade do aprendizado transferido;
- clareza e completude dos eventos na interface.

Comparações com outros modelos devem usar o mesmo conjunto de tarefas, contexto e critérios. O objetivo é comparar resultados e confiabilidade, não imitar internamente a arquitetura de nenhum fornecedor.

## 15. Roadmap de implementação

### Fase 0 — contratos e telemetria

**Entregas:** `agent-event/v2`, `task_id`, `trace_id`, sequência persistente, redaction, métricas básicas e compatibilidade temporária com `/api/activity`.

**Aceitação:** toda mensagem e toda ferramenta produzem eventos correlacionáveis; nenhum segredo aparece nos logs; uma sessão pode ser reconstruída após reinício.

### Fase 1 — superfície unificada

**Entregas:** Chat e Workspace como views da mesma sessão, contexto persistente, tarefa ativa no cabeçalho e inspector lateral opcional.

**Aceitação:** trocar entre conversa e projeto não perde mensagens, eventos, artefatos ou tarefa em andamento.

### Fase 2 — eventos em tempo real

**Entregas:** SSE/WebSocket no Rust, reconexão por `after_seq`, heartbeat, cancelamento, agrupamento de eventos e logs dentro da mensagem.

**Aceitação:** uma tarefa longa continua visível em tempo real, mesmo após rolar a conversa ou recarregar a página; eventos não são duplicados.

### Fase 3 — function calls profissionais

**Entregas:** contratos versionados, schema estrito, risco, permissões, idempotência, pré-condições, outputs estruturados, retries e aprovações inline.

**Aceitação:** chamada inválida é rejeitada antes da execução; toda escrita exige diff ou confirmação; falhas e cancelamentos aparecem claramente.

### Fase 4 — código e artefatos

**Entregas:** `CodeArtifact`, overlays, diff, âncoras, diagnósticos, aplicar/reverter, testes associados e abertura no Workspace.

**Aceitação:** o usuário consegue entender a origem de um trecho, revisar a mudança, aplicar com segurança e conferir a verificação sem sair do chat.

### Fase 5 — Treinamento integrado

**Entregas:** training run no mesmo event bus, currículo versionado, fontes, práticas, transferência, promoção, regressão e exclusão confirmada.

**Aceitação:** o painel mostra exatamente por que uma competência está em cada estado; uma pesquisa não pode se declarar domínio sem prática e verificação.

### Fase 6 — memória e políticas do agente

**Entregas:** memória episódica/procedural/semântica, política derivada de competência, conflito, expiração, rollback e recuperação contextual.

**Aceitação:** aprender uma tecnologia altera uma decisão observável do agente em uma tarefa nova; a mudança pode ser auditada e revertida.

### Fase 7 — desempenho e benchmark

**Entregas:** cache, paralelismo seguro, contexto adaptativo, métricas p95, suíte reservada e relatório de regressão.

**Aceitação:** qualidade operacional melhora sem ultrapassar o orçamento de hardware definido; qualquer otimização que aumente falsos sucessos é rejeitada.

## 16. Mapa de implementação no repositório

| Área | Responsabilidade futura |
|---|---|
| `runtime/src/main.rs` | event bus, SSE/WebSocket, tarefas, cancelamento, permissões, artefatos, diffs e execução segura |
| `runtime/static/index.html` | shell unificado, timeline inline, inspector, overlay de código e estados de aprovação |
| `runtime/static/app.js` | projeção de eventos, reconexão, agrupamento, cards, atualização de Chat/Workspace/Treinamento |
| `runtime/static/chat-core.js` | modelo de mensagens, renderização de artefatos, resposta progressiva e ações inline |
| `python/model_server.py` | intenção, contexto, planejamento, pesquisa, resposta e emissão de eventos do worker |
| `python/tool_registry.py` | schemas, versão, capacidades, riscos, limites, idempotência e validação |
| `python/agent_traces.py` | trace correlacionado, auditoria, métricas e exportação de candidatos de workflow para revisão |
| `python/task_graph.py` | planos com dependências, paralelismo seguro e retomada |
| `python/learning.py` | aquisição, fontes, currículo, exercícios e atualização do índice |
| `python/agent_state.py` | estado versionado de tarefas e competências |
| `python/curriculum.py` | objetivos, lacunas, cobertura e critérios de promoção |
| `python/skill_lab.py` | práticas, execução isolada, testes e transferência |
| `contracts/` | contratos versionados de ferramentas |
| `corpus/` | evidência factual, fontes, índices, licenças e datasets separados |
| `logs/` | traces e eventos locais com redaction |
| `tests/` | cenários de agente, segurança, pesquisa, ferramentas e treinamento |

## 17. Primeiras dez tarefas executáveis

1. Definir `agent-event/v2`, estados, fases e regras de progresso.
2. Criar um `EventBus` no Rust com sequência por sessão e persistência local.
3. Adicionar `task_id`, `trace_id` e `session_id` a chat, pesquisa, aprendizado e ferramentas.
4. Implementar `/api/stream` com reconexão por sequência e manter `/api/activity` como adaptador temporário.
5. Renderizar a timeline dentro da bolha/unidade da mensagem, com níveis Essencial, Detalhado e Auditoria.
6. Unificar a sessão Chat/Workspace e manter o treinamento conectado ao mesmo histórico de eventos.
7. Evoluir os contratos de `contracts/` e o `ToolRegistry` para risco, permissões, timeout, idempotência e outputs estruturados.
8. Criar `CodeArtifact`, diff e fluxo de aprovação para qualquer edição.
9. Migrar `learning.py` para emitir fases de treinamento, evidência, prática e promoção no event bus.
10. Adicionar uma suíte reservada de tarefas e bloquear qualquer promoção que não passe por verificação.

## 18. Critérios de pronto para a virada de chave

Consideraremos essa fase concluída quando:

- o usuário puder fazer uma solicitação complexa e acompanhar tudo no chat;
- Chat, Workspace e Treinamento compartilharem sessão, contexto e histórico;
- cada ação tiver evento, estado, duração e resultado verificável;
- function calls forem tipadas, autorizadas, canceláveis e auditáveis;
- código puder ser aberto, explicado, comparado, aplicado e testado como artefato;
- fontes, decisões, arquivos e testes estiverem ligados à resposta final;
- o agente souber diferenciar evidência, hipótese, ficção e desconhecido;
- aprendizado puder modificar políticas observáveis sem retreino automático inseguro;
- competências tiverem prova de prática e transferência;
- uma falha ou reinício não transformar uma tarefa incompleta em sucesso aparente;
- os benchmarks mostrarem melhora de qualidade, não somente menor latência.

## 19. Resultado esperado para o usuário

Ao enviar uma solicitação, o usuário verá uma única operação viva na conversa:

```text
Recebido
  -> Entendendo o objetivo
  -> Consultando contexto do projeto
  -> Plano criado
  -> Aguardando confirmação (se necessário)
  -> Executando ferramentas
  -> Fontes e evidências anexadas
  -> Código/diff/testes disponíveis
  -> Verificando
  -> Conhecimento ou competência atualizados
  -> Resposta final com limitações e próximos passos
```

A resposta final não será apenas um texto. Será o resumo de um trabalho que pode ser inspecionado, reproduzido, corrigido e retomado.

## 20. Decisões reservadas para implementação

Estas decisões podem ser tomadas depois da aprovação da arquitetura:

- SSE ou WebSocket como transporte primário;
- JSONL estruturado ou SQLite para persistência dos eventos;
- editor embutido inicial e biblioteca de diff;
- política padrão de aprovação para rede e execução;
- limites de concorrência por projeto;
- formato final de snapshots e migrações;
- critérios específicos de promoção por linguagem/framework;
- eventual índice vetorial local, somente se os benchmarks mostrarem necessidade.

Nenhuma dessas escolhas deve quebrar o princípio central: um agente local, observável, verificável e capaz de usar o conhecimento para agir melhor.

## Documentos relacionados

- [README.md](README.md)
- [PLANO_INTERFACE_ASSISTENTE.md](PLANO_INTERFACE_ASSISTENTE.md)
- [PLANO_INTEGRACAO_EDITORES.md](PLANO_INTEGRACAO_EDITORES.md)
- [PLANO_APRENDIZADO.md](PLANO_APRENDIZADO.md)
- [PLANO_TREINO_HIBRIDO.md](PLANO_TREINO_HIBRIDO.md)
- [corpus/README.md](corpus/README.md)
