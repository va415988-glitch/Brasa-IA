# API de acompanhamento do workflow

`GET /tasks/{taskId}/workflow` consulta o AgentCore (porta padrão 3200).
A rota é somente leitura e usa a mesma persistência de `GET /tasks/{taskId}`.

Resposta: `{ "ok": true, "workflow": { ... } }`.

Campos de `agent-workflow/v1`:

- `status`: estado persistido da tarefa, independente do status de etapas individuais.
- `currentStage`, `lastObservation`: última fase e observação registradas.
- `verification`: `unknown`, `stale`, `passed` ou `failed`, derivado dos eventos explícitos de verificação. `unknown` não significa aprovação nem ausência de testes.
- `pendingCriteria`: critérios reprovados na última avaliação de aceite disponível.
- `recoveryAttempts`: número de eventos de recuperação registrados, incluindo planejamento e verificação.
- `next`: recomendação que muda conforme interrupção, aprovação pendente, bloqueio ou verificação. `advisory: true` e `authorized: false`: não executa ferramentas nem concede autorização.
- `eventCount`, `updatedAt`: informações para acompanhamento; não representam porcentagem de progresso.

Tarefa inexistente retorna 404. A rota não cria tarefas; use o fluxo existente de `/pursue`.
O consumidor deve tratar a resposta como uma observação momentânea: eventos e índice podem mudar durante uma execução.
O resultado de verificação é geral; não comprova cobertura de todos os componentes do projeto.
