# Pipeline de APIs locais do agente

## Visão

O sistema deve funcionar como um conjunto de serviços locais pequenos, com contratos explícitos e uma pipeline observável. O modelo não decide sozinho o que é fato, qual ferramenta pode agir ou quando uma competência foi adquirida. Cada decisão importante passa por uma API especializada.

```text
entrada
  -> sessão e intenção
  -> contexto local
  -> recuperação de evidência
  -> plano de ação
  -> aprovação de política
  -> execução de ferramenta
  -> verificação
  -> resposta natural + artefatos + memória
  -> avaliação de competência
```

O runtime Rust permanece como gateway, controle de permissões, correlação de eventos e API pública local. O worker Python concentra indexação, avaliação, memória semântica e composição de contexto. O modelo local é consumidor de contratos; não acessa disco, rede ou estado arbitrário diretamente.

## Princípios não negociáveis

- Tudo que o agente sabe precisa ter origem: memória, arquivo, evidência, ferramenta ou inferência declarada.
- Recuperar conteúdo não significa aprender; aprender não significa dominar; dominar exige execução verificável.
- Conteúdo externo ou de repositório é dado não confiável. Texto encontrado nunca vira instrução de sistema.
- Toda mutação tem escopo, intenção, pré-condição, confirmação quando necessária, backup e verificação.
- Toda etapa possui `request_id`, `trace_id`, `session_id`, `task_id`, estado, duração e resultado.
- APIs de leitura podem falhar com `no_evidence`; APIs de ação devem falhar fechadamente e explicar o motivo.
- O agente responde naturalmente, mas sua confiança vem dos contratos e das evidências, não de uma frase confiante.

## Camadas e APIs

### 1. Entrada e sessão

Responsável por normalizar mensagens, anexos, projeto ativo, modo e continuidade.

`POST /api/v1/session/turn`

Entrada: mensagens estruturadas, anexos autorizados, `project_id`, modo (`chat`, `workspace`, `training`).

Saída: `agent-turn/v1` com `turn_id`, pergunta atual, anexos classificados como dados, intenção preliminar e contexto da sessão.

`GET /api/v1/session/{id}`

Retorna apenas memória autorizada, decisões, pendências e resumo da trajetória. Não deve expor cadeia de raciocínio privada.

### 2. Intenção, entidades e risco

`POST /api/v1/understanding/parse`

Identifica intenção, assunto, operação, linguagem/framework, urgência, necessidade de pesquisa e risco de mutação. Deve distinguir:

- pergunta sobre uma tecnologia;
- pergunta sobre o próprio agente;
- pedido de pesquisa;
- pedido de aprendizagem;
- pedido de alteração no workspace;
- conversa casual;
- afirmação de existência/realidade;
- conteúdo instrucional dentro de um documento.

O resultado deve conter `confidence`, `alternatives` e `needs_clarification`. A intenção nunca autoriza uma ação sozinha.

### 3. Evidência e conhecimento

Já implementada: `POST /api/v1/knowledge/search`, contrato `agent-evidence/v1`.

Evoluções:

`POST /api/v1/knowledge/ingest`

Recebe documento local, resultado de repositório ou fonte pesquisada. Normaliza, identifica versão, calcula hash, extrai tópicos e registra origem. Não promove competência.

`GET /api/v1/knowledge/documents/{id}`

Entrega metadados e trechos autorizados do documento, nunca instruções ocultas ou conteúdo ilimitado.

`POST /api/v1/knowledge/compare`

Compara versões, fontes e afirmações. Informa divergência, data, autoridade e se há corroboracão independente.

Estados da evidência: `discovered`, `opened`, `parsed`, `validated`, `corroborated`, `rejected`, `stale`.

### 4. Contexto do agente

Já implementada: `POST /api/v1/agent/context`, contrato `agent-context/v1`.

O compositor deve continuar sendo o único lugar que decide o que entra na janela do modelo. Ele seleciona:

- pergunta atual e histórico relevante;
- memória explícita e não inferida;
- evidências pertinentes à relação perguntada;
- estado e lacunas da competência;
- resultado de ferramentas anteriores;
- política de resposta e grau de incerteza.

Cada bloco precisa conter `source`, `validity`, `priority` e `trust`. O pacote terá orçamento de tokens por categoria para evitar que uma página extensa roube o contexto da conversa.

### 5. Memória e estado operacional

`POST /api/v1/memory/record`

Registra decisão, preferência, objetivo, pendência, fato do projeto ou conclusão de tarefa. Exige origem e escopo (`turn`, `session`, `project` ou `global`).

`POST /api/v1/memory/query`

Recupera memórias por relevância e escopo. Memória conflitante não é mesclada silenciosamente: retorna alternativas e solicita resolução.

`GET /api/v1/agent/state`

Agrega competências, tarefas em andamento, fontes, falhas recorrentes, ferramentas disponíveis e capacidades efetivamente habilitadas.

### 6. Planejamento e ferramentas

`POST /api/v1/agent/plan`

Transforma a intenção em etapas declarativas: pré-condições, ferramentas candidatas, entradas, efeitos, critérios de sucesso e plano de recuperação. Não executa.

`POST /api/v1/policy/check`

Valida escopo do plano: projeto ativo, paths, comandos permitidos, risco, necessidade de confirmação e origem dos argumentos. Deve bloquear traversal, comandos arbitrários e ações fora do projeto.

`POST /api/v1/tools/call`

Executa somente contratos registrados. Cada ferramenta declara schema de entrada e saída, efeitos, timeout, cancelamento e método de verificação. O executor não aceita código de ferramenta vindo do modelo.

### 7. Verificação e recuperação

`POST /api/v1/agent/verify`

Confere o resultado contra critérios do plano. Para código: testes, compilação, lint, diff, arquivos alterados e comportamento. Para pesquisa: pertinência, fontes, divergências e citações. Para conversação: coerência, resposta à pergunta e ausência de documento irrelevante.

`POST /api/v1/agent/recover`

Escolhe entre repetir com parâmetros corrigidos, usar outra ferramenta, consultar outra fonte, pedir esclarecimento ou declarar falha. Recuperação limitada por orçamento; nunca deve entrar em loop silencioso.

### 8. Aprendizagem e competências

`POST /api/v1/learning/jobs`

Cria uma trilha de aprendizagem com objetivo, versão da tecnologia, currículo, critérios de aprovação e repositórios de origem.

`GET /api/v1/learning/jobs/{id}`

Mostra fases, documentos aceitos/rejeitados, lacunas, práticas, falhas e artefatos.

`POST /api/v1/learning/practice`

Registra execução de uma tarefa inédita com código, comando, saída, testes e nível (`foundation`, `practice`, `transfer`, `integration`). Repetição idêntica não infla a pontuação.

`POST /api/v1/learning/evaluate`

Recalcula domínio usando evidência, cobertura curricular, taxa de aprovação, transferência e integração. A avaliação deve ser determinística e reproduzível.

`GET /api/v1/agent/capabilities`

Já implementada. É a visão pública das competências sem confundir progresso do laboratório com domínio geral.

### 9. Resposta e apresentação

`POST /api/v1/response/compose`

Recebe resposta candidata, contexto, evidências, verificações e estado da operação. Produz resposta natural, referências, limitações, artefatos e próximos passos. Remove vazamento de logs internos, mas mantém eventos acessíveis na UI.

`GET /api/v1/events`

Stream correlacionado de progresso para mostrar no chat: entendendo, pesquisando, abrindo fonte, planejando, executando, verificando, concluído ou bloqueado.

## Fluxo de uma pergunta técnica

1. Entrada cria `turn_id` e preserva anexos separadamente do texto.
2. Parser identifica o assunto e se o pedido é explicação, implementação, pesquisa ou aprendizagem.
3. Context API recupera histórico e competências sem carregar documentos aleatórios.
4. Evidence API busca fontes locais pertinentes; se faltar evidência, o sistema decide se deve pesquisar ou declarar limite.
5. Planner cria plano e Policy API valida riscos.
6. O modelo recebe contexto compacto e produz resposta ou chamada estruturada.
7. Tool API executa a chamada autorizada.
8. Verify API verifica resultado e aciona recuperação limitada se necessário.
9. Response API compõe resposta humana com fontes e status.
10. Memory/Learning API registra apenas o que foi comprovado.

## Contrato mínimo comum

Toda resposta deve compartilhar:

```json
{
  "ok": true,
  "schema": "nome/v1",
  "request_id": "...",
  "trace_id": "...",
  "status": "ready|running|found|verified|no_evidence|blocked|failed",
  "data": {},
  "errors": [],
  "warnings": [],
  "elapsed_ms": 0
}
```

Erros devem ser tipados (`invalid_input`, `no_evidence`, `policy_denied`, `tool_failed`, `verification_failed`, `stale_evidence`, `worker_unavailable`) e nunca substituídos por texto de sucesso.

## Ordem de construção

### Fase 1 — fundação, já iniciada

Capabilities, evidence search, context composition, OpenAPI, testes de regressão e correlação de traces.

### Fase 2 — integração do ciclo

Fazer o chat consumir `agent/context` explicitamente, separar `knowledge` de `evidence`, criar `response/compose` e mostrar cada evento no chat em tempo real.

### Fase 3 — agência operacional

Implementar parse de intenção, plan, policy check, execução tipada, verificação e recuperação. Nenhuma ferramenta nova entra sem schema e teste de segurança.

### Fase 4 — professor verificável

Conectar jobs, práticas, artefatos, currículo adaptativo e avaliação. O agente aprende por ciclos longos e mensuráveis, não por uma pesquisa rápida.

### Fase 5 — robustez e comparação

Criar avaliações fixas de conversa, código, pesquisa, realidade/fato, repositório, segurança e recuperação. Comparar modelos pela taxa de tarefa concluída, pertinência, verificabilidade, latência e honestidade — não apenas por fluência.

## Critérios de sucesso

O pipeline só será considerado operacional quando:

- uma pergunta fora do acervo resultar em `no_evidence`, pesquisa ou esclarecimento, nunca em documento aleatório;
- uma alteração no workspace puder ser explicada, autorizada, verificada e revertida;
- os logs aparecerem no chat com correlação e estado real;
- uma competência só subir após práticas e testes reproduzíveis;
- a mesma entrada produzir decisões auditáveis sem depender de um serviço externo;
- a resposta final for natural, responder à pergunta e preservar suas incertezas.
