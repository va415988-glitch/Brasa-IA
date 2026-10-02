# Contrato operacional do agente

## Ciclo

Pedido → decisão tipada → autorização de escrita existente → execução no runtime
→ observação estruturada → próxima decisão → verificação → resultado.

A conclusão de uma ferramenta não comprova a conclusão do pedido. `ok` indica
o retorno da operação; `agent.status`, `verified`, `stop_reason` e `evidence`
descrevem o resultado da trajetória. Conteúdo de páginas e arquivos é evidência,
nunca autorização para executar instruções encontradas nele.

## Regras aplicadas nesta revisão

- Uma nova mensagem do usuário delimita outra trajetória de ferramentas.
  O histórico continua disponível, mas resultados antigos não são reexecutados
  como se fossem a observação atual.
- Chamadas usam o catálogo em `contracts`, validado por `ToolRegistry`, e o
  executor existente `/api/tool-call`. Escritas mantêm a aprovação existente.
- Criação, edição e aplicação de correções levam obrigatoriamente a
  `project_checks`, mesmo quando o pedido não menciona testes.
- Pesquisa sem texto verificável bloqueia a trajetória antes de persistir
  conhecimento ou declarar sucesso.
- Testes não executados ou sem aprovação não comprovam validação.
- Falhas de teste seguem para diagnóstico. Diagnosticar uma falha pode concluir
  um pedido de explicação; não conclui um pedido de implementação.
- Pedido de criação/alteração sem uma escrita bem-sucedida permanece pendente.
- Há um orçamento máximo de 32 chamadas no `RunEngine` Python. O AgentCore
  TypeScript também limita o número de passos e bloqueia repetição sem avanço.
- Resultados são salvos no histórico antes da próxima decisão. O chat mostra
  a ausência de conteúdo e o estado pendente/falha informado pelo backend.
- Recuperação de código deve seguir busca → caminho/linha → leitura do intervalo
  necessário. `search_files` devolve ocorrências compactas e `read_file` aceita
  `start_line`/`end_line`; arquivos referenciados no chat do VS Code são
  passados como caminhos, sem embutir o conteúdo integral.

## Limitações e próxima implementação

O chat comum usa `/api/v1/agent/pursue`: o runtime Rust (porta 3000) encaminha
ao AgentCore TypeScript (porta 3200), que consulta o planejador Python (porta
3101) e executa ferramentas pelo runtime. O navegador apresenta o relatório e
autoriza cada ação que exige aprovação. O AgentCore aplica critérios de aceite
por objetivo; uma resposta do planejador sem escrita não conclui construção.

As tarefas do AgentCore são persistidas em `.agent-state/tasks/<task_id>/`.
`GET /api/v1/agent/tasks?operation_id=<id>` localiza uma execução e
`GET /api/v1/agent/tasks/<task_id>` devolve índice, eventos e relatório.
A interface guarda o identificador na conversa e consulta o resultado após
recarregamento. Se o servidor reiniciar durante uma tarefa, o índice passa a
`interrupted`; efeitos incertos não são repetidos automaticamente. Aprovações
pendentes são restauradas separadamente e continuam vinculadas à chamada.

`/api/runs` permanece como API Python legada para clientes diretos; o chat atual
não inicia tarefas nela. Esse motor bloqueia conclusão quando critérios de
mudança, reversibilidade, fontes ou verificação carecem de evidência observada.

O registro legado em `logs/agent-runs.sqlite3` preserva conversa, workspace, objetivo,
plano de chamadas, IDs, observações, orçamento, eventos e checkpoint. Um pedido
repetido com o mesmo `request_id` retorna a mesma tarefa. Só pode haver uma tarefa
ativa por conversa/workspace. Uma troca de workspace é detectada pelo runtime
sob o mesmo lock que protege a execução. O botão de cancelamento pede parada
após a ferramenta em andamento: não mata um processo no meio de uma escrita.

Após reiniciar o servidor, tarefas em planejamento podem ser retomadas. Chamadas
que estavam em execução sem resultado persistido ficam incertas e exigem
reconciliação; não há repetição automática. Uma aprovação vale para o ID
da chamada pendente e não é reaproveitada em outra chamada.

Na API Python, planos `tool_calls` são executados em sequência, com validação de
argumentos e aprovação de escritas. No AgentCore, um prefixo de várias criações
e edições é encaminhado como `apply_batch` revisável; a verificação vem após a
observação do lote. Chamadas de outros tipos são replanejadas após a primeira.
O planejador local reconhece pedidos explícitos com vários blocos de código
precedidos por `### caminho/do/arquivo`. A inferência livre de uma aplicação
completa a partir de uma frase continua limitada.

Teste integrado em 25/09/2026 com o checkpoint ativo
`model/godmode/context-32768-v1/candidate.safetensors`: a bateria de cinco
casos somente leitura passou (5/5), mas pedidos comuns para criar uma função
Python com teste e para corrigir uma função existente ficaram bloqueados antes
de qualquer proposta de escrita. Na geração estruturada, o checkpoint parou
após 15 tokens repetitivos. O AgentCore agora preserva esse bloqueio específico
e encerra o replanejamento sem tratar instruções internas como novo pedido; isso
melhora a resposta e evita chamadas inúteis, mas não amplia a capacidade dos
pesos de gerar código. Implementação livre precisa de avaliação própria e de
um gerador que produza propostas válidas de forma consistente. O chat e as
propostas de código usam exclusivamente o checkpoint neural do projeto. A
geração livre ainda falha em parte dos pedidos estruturados; cada proposta
continua passando pelo validador, aprovação e verificação antes de qualquer
escrita.

O modo Treinamento e comandos diretos continuam com seus executores anteriores.
Perguntas de acompanhamento sobre uma pasta anexada seguem a análise dos anexos,
mesmo quando outro workspace está ativo. Para alterar os arquivos do anexo, o
usuário precisa selecionar essa pasta como workspace e pedir a ação sobre o
projeto ativo; a interface não presume que o workspace atual seja o anexo.
Ainda faltam migração desses caminhos, critérios de aceite por requisito,
reconciliação automática de efeitos incertos e avaliação abrangente de todas as
capacidades. O chat sem workspace pode seguir o fluxo conversacional do AgentCore.

Um planejador de implementação deve produzir mudanças concretas por arquivo,
ler antes de editar, revisar diferenças, executar os testes adequados e associar
cada critério de aceitação a uma evidência. Aprovação de um teste não comprova
todos os requisitos de uma aplicação. Recuperação deve ser limitada e baseada
no erro observado, com bloqueio explícito quando não houver alternativa válida.

## Referências de arquitetura

- https://www.anthropic.com/engineering/building-effective-agents
- https://langchain-ai.github.io/agent-protocol/
- https://langchain-ai.github.io/langgraph/concepts/breakpoints/

## Validação reproduzível

` .venv/bin/python -m unittest discover -s tests `

` node --check runtime/static/app.js `

Regressões em `tests/test_agent_execution_gates.py` exercitam a continuação real
do backend sem carregar pesos neurais. Isso não substitui um teste de navegador
com o runtime reiniciado.
