# Comparativo contínuo de agentes

Este arquivo registra a referência usada para evoluir a IA Local do Zero. A comparação com GPT-6 Astra e Claude Fable 5 é arquitetural e baseada na documentação pública; não representa um benchmark de qualidade dos modelos remotos.

## Referência pública

- GPT-6 Astra: [model guidance](https://developers.openai.com/api/docs/guides/latest-model) e [function calling](https://developers.openai.com/api/docs/guides/function-calling).
- Claude Fable 5: [tool use](https://docs.anthropic.com/en/docs/agents-and-tools/tool-use/implement-tool-use) e [prompting best practices](https://docs.anthropic.com/en/docs/build-with-claude/prompt-engineering/prompt-templates-and-variables).

## Estado atual

| Dimensão | IA Local do Zero | Referência Astra/Fable |
|---|---|---|
| Catálogo | Contratos JSON carregados pelo `ToolRegistry` | Schemas de ferramentas fornecidos ao modelo |
| Seleção | Ranqueamento local por aliases, descrição e argumentos | Decisão generativa com `tool_choice` automático |
| Execução | Runtime Rust, permissões e logs | Aplicação executora ou ferramentas hospedadas |
| Continuação | `role=tool`, até seis etapas, fases `plan/act/verify/diagnose/recover` | Loop contínuo conforme a tarefa |
| Paralelismo | Próxima etapa sequencial | Chamadas independentes podem ser paralelas |
| Estado | Histórico local e resultados estruturados | Estado persistente, compaction ou contexto multi janela |
| Verificação | `project_checks` após mudanças ou quando solicitado; falha dispara `diagnose_project` | Padrões de teste e verificação orientados pelo agente |
| Recuperação | Fonte web alternativa uma vez; diagnóstico sem alteração automática | Replanejamento e recuperação adaptativos, conforme contexto |

## Critério permanente

Cada nova ferramenta deve entrar pelo contrato, aparecer no catálogo, possuir casos de avaliação e funcionar no loop web e ACP. Uma melhoria só será considerada concluída quando o comportamento for comparado com os quatro pontos de referência: seleção de ferramenta, encadeamento, feedback de execução e encerramento fundamentado.

Execute a matriz local com:

```bash
./.venv/bin/python tests/run_agent_benchmark.py
```

Baseline atual: 12 cenários, seleção correta em 12/12, média de planejamento de aproximadamente 3 ms com trace ativo, cadeia composta validada em três etapas e `trace_continuity=true`. O benchmark também verifica duas recuperações: falha de verificação → diagnóstico e fonte web indisponível → próxima fonte. Esse número mede o planejador local; não é uma medição direta de qualidade generativa do Astra ou do Fable.

## Pacotes avaliados para comportamento agêntico

Não adicionamos um framework pesado ao runtime. O catálogo e o loop já são próprios,
com contratos JSON, executor Rust e clientes web/ACP; colocar outro orquestrador por
cima duplicaria estado e aumentaria as superfícies de falha.

- **Pydantic AI**: referência útil para ferramentas tipadas, saídas estruturadas,
  validação e retries. A arquitetura local absorve esses princípios nos contratos
  JSON e no `ToolRegistry`. Veja a [visão geral do Pydantic AI](https://pydantic.dev/docs/ai/overview/)
  e [saídas estruturadas](https://pydantic.dev/docs/ai/core-concepts/output/).
- **LangGraph**: referência útil para estado explícito, nós de verificação,
  retomada e intervenção humana. A primeira versão local usa uma máquina de estados
  menor e limitada a seis etapas. Veja a [referência do LangGraph](https://langchain-ai.github.io/langgraph/reference/).
- **DSPy**: candidato para uma fase posterior, quando houver um modelo generativo
  aprovado e um conjunto maior de traces rotulados. Ele otimiza programas de prompts
  e módulos com métricas; não substitui nosso executor nem transforma respostas
  prontas em raciocínio neural. Veja os [otimizadores do DSPy](https://github.com/stanfordnlp/dspy/blob/main/docs/docs/learn/optimization/optimizers.md).

O ganho imediato veio de implementar os padrões diretamente: entender e planejar
antes da chamada, executar com feedback, verificar alterações, diagnosticar uma
falha com evidências e próximos passos, e recuperar uma fonte web indisponível uma
vez. Isso melhora a confiabilidade do agente, mas ainda não equivale à flexibilidade
generativa dos modelos de referência.

## Traces e treinamento do planejador

Cada rodada agora grava em `logs/agent_traces.jsonl` o pedido, os candidatos,
a chamada escolhida, argumentos, resultado, verificação, próxima etapa e estado
final. O registro é truncado e redige campos sensíveis; falha de telemetria não
interrompe a resposta.

Para auditar e gerar exemplos estruturados:

```bash
./.venv/bin/python python/agent_traces.py report
./.venv/bin/python python/agent_traces.py export-workflow
```

O exportador produz um candidato por decisão em
`corpus/raw/workflow_planner_candidates.jsonl`, com ações e observações
anteriores da mesma trajetória. Todo registro começa como `pending` e fora do
treino. Revise uma decisão com contexto usando `python/review_workflow_candidates.py`:

```bash
./.venv/bin/python python/review_workflow_candidates.py list --limit 20
./.venv/bin/python python/review_workflow_candidates.py show TRACE_ID#1
./.venv/bin/python python/review_workflow_candidates.py approve TRACE_ID#1 --reviewer "Victor" --rationale "A ação corresponde ao objetivo e às observações anteriores."
```

Cada aprovação ou rejeição recebe revisor, justificativa e horário em um ledger
append-only. O dataset aprovado é uma cópia separada; o pipeline lê apenas essa
cópia com `review_status: approved`, e continua ignorando pendentes e rejeitados.
Traces brutos bem-sucedidos também não entram sem revisão explícita. A validação
agrupa decisões pelo trace para não dividir uma mesma trajetória entre treino e
holdout. Assim o modelo pode aprender a escolher a próxima ação conforme o que
observou, em vez de receber apenas a pergunta inicial. O reranker local continua
especializado em seleção de ferramenta; esse dataset contextual é base para
avaliar o futuro planejador por trajetória.

O AgentCore TypeScript também expõe `propose_repair` e `apply_repair` como
ferramentas tipadas. Uma proposta de correção validada pode avançar para
aprovação, mas a aprovação mostra o trecho antigo e o novo antes de executar;
depois da alteração, o workflow solicita verificação do projeto. A etapa de
reformular a correção após uma falha ainda depende de um planejador contextual
capaz de usar o diagnóstico, portanto falhas sem uma nova proposta permanecem
bloqueadas e explícitas.

O reranker especializado pode ser reconstruído com:

```bash
./.venv/bin/python python/train_planner.py
```

O artefato fica em `model/planner/planner_index.json` e é carregado pelo
`AgentPlanner` como uma pontuação pequena de desempate. Contratos, validação de
argumentos e quality gate continuam tendo prioridade. A primeira rodada reuniu
7 exemplos de 3 ferramentas e preservou 100% da seleção no benchmark; isso é
um começo de aprendizado especializado, ainda pequeno demais para ser tratado
como treinamento geral.

### Leitura honesta da comparação

O ciclo local já reproduz o fundamento comum de Astra e Fable: catálogo
estruturado, execução externa, resultado tipado e continuação. Ainda estamos
atrás em decisão generativa ampla, chamadas paralelas independentes,
planejamento adaptativo e recuperação de falhas. Os traces são a ponte para
medir e melhorar essas lacunas; não são evidência de equivalência de qualidade.
