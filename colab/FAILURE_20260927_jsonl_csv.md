# Falha de coleta: conversor JSONL para CSV

Workspace temporário: `/tmp/brasa-collection-v002-jsonl-csv-70nge61x`. O pedido solicitou ler `README.md`, implementar a CLI, criar testes e executar uma verificação. Nenhum arquivo foi criado ou alterado nas duas tentativas registradas abaixo. **Essas execuções são falhas diagnósticas; não são exemplos positivos de programação.**

| Tarefa | Objetivo escolhido | Evidência observada | Resultado |
| --- | --- | --- | --- |
| `task-03400fc9-7b10-469e-9299-de91f7eb8fce` | `testing` | O agente chamou `project_checks` antes de criar o programa ou os testes. O verificador informou que não reconheceu nenhum check no workspace (`executed: false`). | Bloqueada por `testing.execution`. |
| `task-7086ff19-635e-46ea-8e8b-9779a828aef5` | `analyze` | A mensagem genérica de retomada foi classificada como análise. O agente leu `README.md`, mas o checkpoint local repetiu a geração e não produziu proposta estruturada de código. | Bloqueada por `analysis.references`; nenhum arquivo alterado. |

## Correção no roteamento

`agent-core/src/requirements.ts` agora prioriza uma solicitação explícita para implementar um programa sobre palavras como “testes” no mesmo pedido. `agent-core/src/agent.ts` recupera o pedido anterior do histórico quando a mensagem diz “Retome o objetivo original”. A checagem TypeScript (`tsc --noEmit`) passou. O AgentCore foi reiniciado e voltou a responder em `127.0.0.1:3200`.

## Limite do gerador

A correção de rota não comprova que a Brasa já consegue gerar esta CLI. O backend ativo informa `own-checkpoint` em `model/godmode/context-32768-v1/candidate.safetensors`. O manifesto do checkpoint registra **2 camadas, dimensão oculta 128, treino do zero e apenas 512 tokens de contexto usados no treino**. A verificação de 32.768 tokens conferiu execução e logits finitos, com somente 4 tokens gerados; ela declara `semantic_quality_evaluated: false`. Portanto, o número de contexto disponível em runtime não demonstra competência para produzir múltiplos arquivos de código.

Próximo avanço técnico: usar um modelo de código pré-treinado de pelo menos 30B como **professor no Colab** para produzir propostas e exemplos de treino, sem tornar a Brasa dependente da sessão do Colab. As propostas precisam ser executadas e verificadas antes de virar exemplos positivos. O runtime local continuará responsável por escolher ferramentas, controlar aprovações, aplicar mudanças e verificar o resultado; um modelo aluno compatível com o computador será escolhido por medição. A tarefa de coleta permanece como caso de avaliação de regressão, separado do treino. A arquitetura está detalhada em `TEACHER_STUDENT_30B.md`.
