# Teste basal do Brasa com a tarefa event_csv

## Objetivo

Medir se o Brasa consegue implementar a tarefa `event_csv` usando os critérios derivados do material corrigido, sem receber a implementação de referência.

## Configuração

- Checkpoint ativo: `model/godmode/context-32768-v1/candidate.safetensors`
- Backend informado pelo worker: `local-neural`; AgentCore TypeScript e runtime Rust ativos.
- Workspace isolado: `/tmp/brasa_event_csv_trial_20260927`
- Workspace continha somente a especificação `README.md` e a bateria de nove testes `tests/test_event_csv.py`; nenhum código de referência foi copiado.
- Pedido: implementar pacote e CLI, inspecionar o workspace, preservar os testes e concluir somente após execução real bem-sucedida.
- `taskId`: `task-a9108706-26e4-40b2-94af-bdacbd23921e`
- `operationId`: `agent-core-brasa-eventcsv-20260927-a`

## Resultado observado

- HTTP 200 no endpoint do AgentCore; relatório `agent-core/v1` com `status: blocked`.
- O texto final foi: “O checkpoint local interrompeu a geração de código por repetição. Não há receita determinística compatível com este pedido. Nenhum arquivo foi alterado.”
- `artifacts`: vazio; não houve chamada de escrita nem solicitação de aprovação.
- `taskStorage.saved: true`; tarefa e eventos persistidos em `.agent-state/tasks`.
- Nenhum teste do workspace foi executado, porque a implementação não chegou a ser proposta.
- Após a execução, o workspace ainda contém somente `README.md` e `tests/test_event_csv.py`.

## Conclusão

Este teste basal **não passou**: com o checkpoint ativo, o Brasa não conseguiu produzir uma implementação para os critérios do exercício, antes da etapa de escrita. O resultado confirma a limitação de geração repetitiva já conhecida; não mede a qualidade do código porque nenhum código foi produzido.

O material corrigido permanece como referência e os testes foram usados como critérios de aceitação, não como treino nem como conteúdo fornecido ao modelo. Este run não foi adicionado ao corpus. Uma nova comparação após qualquer ajuste ou treino deve usar outro `taskId` e continuar em workspace isolado.
