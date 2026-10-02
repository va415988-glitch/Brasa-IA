# Continuidade de tarefas e pedidos curtos

O AgentCore agora persiste o estado de execução antes de cada ferramenta e o resultado confirmado logo depois. A retomada usa a mesma identidade de tarefa e recupera objetivo, restrições, hipóteses, critérios pendentes, evidências, alterações e verificações. Isso complementa a janela de contexto; não aumenta a capacidade aprendida pelos pesos.

## Comportamento implementado

- Pedidos como “Crie um controle de gastos simples” permitem iniciar a inspeção sem exigir uma stack no prompt. A escolha deve aproveitar o projeto existente ou explicitar padrões para uma primeira entrega local. Pedidos sem finalidade, como “Crie um aplicativo”, continuam pedindo esclarecimento.
- Um resumo estruturado do objetivo e das pendências recebe prioridade no contexto do planejador. Corpos extensos de ferramentas são reduzidos preservando JSON válido, caminhos e indicação de truncamento. O histórico completo continua no checkpoint.
- Na interface, “Continue de onde parou” retoma a tarefa interrompida ou bloqueada mais recente da mesma conversa e do mesmo workspace. Uma tarefa posterior substitui a anterior; outra conversa não empresta seu estado.
- Escritas já confirmadas ficam no registro de ações e não são repetidas com os mesmos argumentos. Após alterações, a retomada exige uma nova verificação.
- Uma leitura interrompida pode ser repetida. Se uma escrita ou processo foi interrompido antes de confirmar seu resultado, a retomada informa a ação e exige conferir seus efeitos; não presume que ela falhou nem a repete automaticamente.
- A persistência do checkpoint é aguardada antes da execução. Uma falha de gravação impede a próxima ação.
- O pedido original e os anexos são armazenados separadamente para evitar duplicação na retomada. As operações original e de continuação apontam para a mesma tarefa no acompanhamento.
- O runtime espera o serviço do modelo responder a `/health` antes de iniciar o agente. Isso corrige as falhas de conexão encontradas ao retomar imediatamente após um reinício.

Os checkpoints ficam em `.agent-state/tasks/<taskId>/sections/state/checkpoint.json`. Eles incluem o histórico e os conteúdos observados; devem receber o mesmo cuidado dado aos arquivos locais da conversa.

## API de retomada

Além do pedido curto na conversa, a API aceita:

```http
POST /api/v1/agent/tasks/<taskId>/resume
Content-Type: application/json

{"schema":"agent-resume/v1","request_id":"resume-exemplo-1","kind":"continue"}
```

Esse caminho aceita apenas tarefas bloqueadas ou interrompidas com checkpoint. Aprovações e esclarecimentos usam seus contratos próprios. Repetir o mesmo `request_id` no endpoint de retomada recupera a resposta persistida em vez de executar novamente.

## Teste inicial reproduzível

Com a aplicação iniciada por `bash start.sh`:

```bash
.venv/bin/python scripts/smoke_task_continuity.py --output /tmp/ia-continuity-report.json
```

O teste hospeda uma página local, envia uma consulta com anexo, espera um checkpoint antes da leitura, reinicia a aplicação pelo iniciador do projeto e envia apenas “Continue de onde parou”, sem histórico do cliente. A entrega precisa recuperar o marcador real da página e citar sua URL. O teste reinicia a aplicação somente depois de identificar sua própria tarefa aceita.

Resultado observado: **5/5 verificações**, tarefa concluída com a mesma identidade. Relatório: `avaliacoes/task-continuity-smoke-2026-09-30.json`.

O pedido curto “Crie um controle de gastos simples” também foi enviado à API de entendimento com uma pasta vazia: retornou `ready`, objetivo `build`, nenhuma pergunta e uma hipótese explícita de inspeção antes da escolha da stack. Esse teste verifica o início do trabalho, sem afirmar que o produto foi implementado. Relatório: `avaliacoes/short-request-understanding-2026-09-30.json`.

Os cinco testes de continuidade do TypeScript cobrem compactação, persistência, isolamento entre conversas/workspaces, escrita confirmada sem repetição e falha de armazenamento sem efeito. A checagem de tipos, os 17 arquivos de testes TypeScript e os 45 testes Rust passaram. Os seis cenários de pesquisa, habilidades, leitura, testes e streaming também passaram após a integração: `avaliacoes/operational-continuity-regression-2026-09-30.json`.

## Limite desta entrega

O teste real confirma recuperação de estado e consulta de fonte após reinício. Os testes com planejador controlado confirmam os contratos de execução; não certificam que o modelo consiga elaborar e implementar um produto livremente. O checkpoint generativo não foi treinado nem substituído, e sua avaliação isolada anterior foi 0/4 decisões válidas. Ainda faltam pesos que passem avaliações inéditas de planejamento, implementação e recuperação. A janela declarada continua em 32768 tokens de execução e 512 de treino.
