# Avaliação adaptada Top 5 do AgentCore — 24/09/2026

## Escopo

Apliquei os cinco perfis de datasets indicados a uma bateria de cinco tarefas do AgentCore. Os casos são adaptações autorais para este workspace; não são exemplos brutos dos datasets e foram usados apenas para avaliação. Não houve ingestão nem fine-tuning.

O perfil `diogoneno/desktop-agent-trajectories-sample` não pôde ser consultado sem autenticação (HTTP 401). Os arquivos de `ToolGym/short-horizon-traj` são públicos, mas o endpoint de amostras não converte seu schema heterogêneo de forma consistente. Os perfis Toprak, GRPO e Code Reasoning tinham amostras públicas acessíveis. Por isso, a bateria mede comportamentos adaptados, não pontuação oficial dessas fontes.

## Resultado live

| Perfil de referência | Cenário avaliado | Resultado | Evidência |
|---|---|---:|---|
| Desktop Agent Trajectories | Inspecionar e descrever pastas e arquivos | PASS | Inventário parcial: pelo menos 400 arquivos e 84 pastas; leu quatro arquivos; nenhuma mutação |
| ToolGym | Ler e explicar arquivo-fonte citado | PASS | Leu `agent-core/src/planner.ts` em duas janelas (linhas 1–120 e 121–215); explicou que `LocalPlannerHttp` delega a decisão a `/generate` e valida o retorno; a lógica interna do endpoint não é demonstrada por esse arquivo |
| Toprak Agent Tool Use | Recuperar após caminho inexistente | PASS | Confirmou que `config/agent-planner.yaml` não existe, leu `config/huggingface_catalog.json` e reportou nove datasets em quarentena, inelegíveis para treino |
| GRPO Reasoning Tools | Resolver cálculo sem ferramenta de workspace | PASS | `19 × 23 = 437`; nenhuma leitura ou mutação |
| Code Reasoning | Escrever e explicar fatorial pequeno | PASS | Função Python para `n=5`, resultado 120; nenhuma leitura ou mutação |

**Resultado live: 5/5; mutações: 0/5.** No caso de arquivo-fonte, a resposta final ficou restrita à explicação pedida, sem anexar o inventário geral do workspace.

## Correções motivadas pela avaliação

- O AgentCore agora permite continuar uma leitura somente quando a nova janela começa imediatamente após a anterior. Releituras repetidas continuam bloqueadas.
- A normalização de chamadas preserva a faixa solicitada e limita cada leitura a 120 linhas e 8 KiB.
- O backend continua lendo arquivos citados em janelas limitadas e combina os trechos antes de sintetizar a explicação.
- Pedidos sobre um arquivo específico não recebem mais o inventário genérico de todo o workspace.

## Verificações

- `python -m py_compile python/model_server.py tests/test_model_server_agentic.py`: passou.
- `python tests/test_model_server_agentic.py`: **37 testes passaram**.
- `npm --prefix agent-core test`: **36 testes passaram**.
- `npm --prefix agent-core run check`: passou.
- `tests/benchmark_agent_tool_eval_top5.py`, contra o AgentCore e o ModelServer iniciados com o código final: **5/5 passaram**.

O runner salva os detalhes por caso em JSON via `--output`; o resultado desta rodada foi gravado durante a execução em `/tmp/ia-local-top5-eval-final9/results.json`.

## Correção da telemetria e repetição da bateria

Uma rodada posterior apareceu como **2/5**, embora os relatórios mostrassem inspeção e leituras corretas. A causa era dupla: o adaptador do AgentCore sobrescrevia `payload.tool` com `undefined` ao emitir transições `brain.state`, e o avaliador só reconhecia eventos `tool.started`/`tool.completed`. As ferramentas haviam sido executadas; o campo que as identificava era descartado antes de chegar ao relatório.

Corrigi o AgentCore para preservar os dados da ferramenta nas transições `executing` e `verifying`, e o avaliador agora lê esse envelope. A inspeção inicial conta como `inspect_project`: `RuntimeHttpPorts.workspace.inspect()` chama esse endpoint diretamente antes do loop do planejador.

No caso Toprak, a inspeção inicial já havia fornecido a lista observada de arquivos da pasta. A recuperação escolheu dali `config/huggingface_catalog.json`; não houve uma segunda chamada a `list_files`, que seria redundante. O critério foi ajustado para exigir a chamada observável `read_file`, mantendo as verificações de que o caminho ausente não foi lido e de que o substituto foi efetivamente lido.

Depois de reiniciar o serviço para carregar o código atualizado, a repetição passou **5/5**, com zero mutações. O resultado detalhado está em `/tmp/agent-tool-eval-top5-results.json`. Também passaram `npm test` (**7 arquivos de teste**), `npm run check`, `python3 -m unittest tests.test_benchmark_agent_tool_eval_top5 -v` (**3 testes**) e `py_compile` dos dois scripts Python alterados.

## Limites

Esta é uma smoke suite pequena, não comprova competência geral nem domínio confiável das 34 ferramentas. Os testes de matemática e programação cobrem um exemplo cada. O teste de desktop foi adaptado para inspeção local e não reproduz ações reais de interface. Os cenários devem crescer em cobertura e variedade antes de qualquer afirmação ampla sobre capacidade.

## Continuação — roteamento por formato e inspeção de código — 25/09/2026

Um caso real apontou que o planejador lia `python/agent_planner.py` e listava alguns símbolos, mas ignorava a ferramenta especializada `inspect_code`. A primeira ação do AgentCore agora roteia pedidos explícitos de símbolos/imports para essa ferramenta; documentos como PDF passam por `extract_document_text`, e perguntas sobre metadados de mídia usam `inspect_media`. `inspect_project` também está permitido na política de análise somente leitura.

A verificação encontrou outro bloqueio: o inventário pode estar truncado e ainda assim conter um caminho exato citado pelo usuário que a ferramenta consegue abrir. A política passou a confiar nos candidatos selecionados para a tarefa, que preservam esse caminho explícito, em vez de exigir que apareça na amostra truncada. A repetição de inspeção de símbolos no mesmo arquivo continua bloqueada; depois de `inspect_code`, uma leitura de conteúdo do mesmo arquivo segue disponível.

Validação após reiniciar o serviço com as mudanças carregadas:

- Caso live `tool-routing-inspect-code-live`: **1/1**, ferramenta observada: `inspect_code`, caminho citado preservado, zero mutações.
- Bateria integrada adaptada dos cinco perfis: **5/5**, zero mutações.
- `npm test` e `npm run check`: passaram; 8 arquivos de teste.
- `python3 -m unittest tests/test_agent_planner_routing.py tests/test_benchmark_agent_tool_eval_top5.py`: **5 testes passaram**; `py_compile` dos arquivos Python alterados passou.

Os resultados live ficaram em `/tmp/agent-tool-routing-live-result.json` e `/tmp/agent-tool-eval-top5-results.json`. A avaliação ainda é smoke coverage: o caso novo valida uma rota adicional, mas não demonstra domínio geral das 34 ferramentas.
