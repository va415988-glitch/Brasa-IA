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
- Há um orçamento de 12 resultados por trajetória, preservando o último
  resultado no histórico. O frontend interrompe repetição literal da mesma
  ferramenta e argumentos.
- Resultados são salvos no histórico antes da próxima decisão. O chat mostra
  a ausência de conteúdo e o estado pendente/falha informado pelo backend.

## Limitações e próxima implementação

O planejador continua baseado principalmente em regras e seleção de contratos.
Estas mudanças corrigem decisões de controle, mas não o tornam capaz de gerar
qualquer aplicação. Com um workspace selecionado, o chat agora usa `/api/runs`:
o ciclo executa no servidor e o navegador apenas acompanha e autoriza chamadas.

O registro em `logs/agent-runs.sqlite3` preserva conversa, workspace, objetivo,
plano de chamadas, IDs, observações, orçamento, eventos e checkpoint. Um pedido
repetido com o mesmo `request_id` retorna a mesma tarefa. Só pode haver uma tarefa
ativa por conversa/workspace. Uma troca de workspace é detectada pelo runtime
sob o mesmo lock que protege a execução. O botão de cancelamento pede parada
após a ferramenta em andamento: não mata um processo no meio de uma escrita.

Após reiniciar o servidor, tarefas em planejamento podem ser retomadas. Chamadas
que estavam em execução sem resultado persistido ficam incertas e exigem
reconciliação; não há repetição automática. Uma aprovação vale para o ID
da chamada pendente e não é reaproveitada em outra chamada.

Planos `tool_calls` são executados em sequência, com validação de argumentos e
aprovação de escritas. Verificação ocorre depois do lote. Não se trata de uma
transação: arquivos já criados permanecem se uma etapa posterior falhar.
O planejador local reconhece pedidos explícitos com vários blocos de código
precedidos por `### caminho/do/arquivo`. A inferência livre de uma aplicação
completa a partir de uma frase continua limitada.

O modo Treinamento e comandos diretos continuam com seus executores anteriores.
Ainda falta migrá-los, definir critérios de aceitação por requisito e automatizar
a reconciliação de chamadas incertas. O chat sem workspace mantém o fluxo anterior.

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
