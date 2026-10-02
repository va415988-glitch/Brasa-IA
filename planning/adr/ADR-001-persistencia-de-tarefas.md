# ADR-001: Persistência local das seções de tarefa

- **Status:** Aceita para a primeira fatia; registrada durante a implementação, sujeita a revisão antes de substituir os armazenamentos existentes.
- **Data:** 24/09/2026.
- **Contexto:** O backend do AgentCore precisa conservar pedido, eventos, evidências e artefatos em diretórios por tarefa. O agente pode pausar por aprovação e o servidor pode reiniciar antes da retomada. A interface precisa receber um resultado da persistência sem confundir falha de gravação com conclusão da tarefa.

## Decisão

A primeira versão usará arquivos locais por tarefa, implementados em `agent-core/src/task-store.ts`, com raiz configurável por `IA_AGENT_TASKS_DIR` e padrão `.agent-state/tasks/`. Cada tarefa terá um `task.json`, um log append-only `events.jsonl` e diretórios `sections/` com arquivos JSON/Markdown separados por contexto, requisitos, plano, evidências, atividade, snapshots dos arquivos alterados, verificação, retomada e entrega. Depois de confirmar uma mutação, o AgentCore tenta reler o arquivo alterado pelo runtime e salva seu conteúdo completo; uma falha dessa leitura fica registrada no fluxo. Escritas de seções serão feitas em arquivo temporário e renomeadas atomicamente. Diretórios e arquivos novos usarão permissões restritas (`0700` e `0600`).

O armazenamento existente de telemetria e memória não será migrado nem removido nesta etapa. A persistência por arquivos é a fonte de consulta dos artefatos e do percurso da tarefa; a definição da autoridade global de eventos e histórico continua pendente de uma decisão posterior.

O servidor aguardará a fila de eventos antes de salvar o relatório final. Erros de persistência serão devolvidos em `taskStorage.saved/error` e não serão apresentados como falha da execução do agente. Uma aprovação pendente será consumida do disco antes de retomar a execução e só será gravada novamente se a tarefa pedir outra aprovação. Na restauração, a chamada pendente precisa ter estrutura válida e sua ferramenta e risco devem corresponder ao registro canônico de capabilities.

## Alternativas consideradas

1. **SQLite para todas as seções e conteúdos.** Não adotada nesta fatia: exigiria migração do esquema de telemetria e decisão sobre conteúdos grandes e retenção antes de a estrutura por seção ser observável.
2. **SQLite como índice e arquivos como artefatos.** Adiada: é opção para busca/listagem futura, após baseline de volume e consulta.
3. **Um JSON monolítico por tarefa.** Rejeitada: dificulta leitura incremental das seções, gravação append-only de eventos e inspeção manual dos artefatos.

## Consequências e limites

- Uma tarefa pode ser inspecionada diretamente no disco sem decodificar o banco de runs.
- A gravação de cada seção é atômica, mas a gravação do conjunto inteiro de seções não é uma única transação. O índice de artefatos sempre aponta para um arquivo de snapshot, inclusive para arquivos válidos de conteúdo vazio. O estado `task.json` e `taskStorage` expõem o resultado final observado.
- A implementação ainda não oferece endpoints de busca/listagem, retenção/expurgo, redação de dados pessoais ou migração de tarefas antigas.
- O identificador de tarefa é validado antes de compor caminhos. A raiz deve permanecer local e controlada pelo operador.
- A restauração cobre apenas uma aprovação pendente no servidor; não reconstrói todas as tarefas interrompidas como processos executáveis.

## Verificação exigida antes de rollout

- Testes unitários para criação das seções, ordenação contínua de eventos após reinício, escrita atômica, snapshots de arquivo alterado (incluindo conteúdo vazio), erro de disco, validação de IDs e rejeição de chamada pendente com capability/risco inválidos.
- Teste de integração do ciclo: iniciar tarefa, persistir, pausar aguardando aprovação, reiniciar servidor, restaurar e retomar sem reaplicar automaticamente a aprovação.
- Inspeção manual de permissões em Linux e comportamento em sistemas que não suportem os mesmos modos POSIX.
- Benchmark de tamanho e tempo de escrita antes de definir retenção ou migrar os históricos existentes.

Essas verificações estão pendentes; não foram executadas nesta decisão.

## Rollback

Definir `IA_AGENT_TASKS_DIR` para outro diretório vazio desativa a leitura dos registros anteriores sem apagá-los. Para voltar ao fluxo sem armazenamento, remover a integração no servidor e manter cópia do diretório até exportação/expurgo aprovado. Nenhum dado deve ser apagado como parte do rollback automático.
