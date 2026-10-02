# Próximo lote de episódios reais de programação

O histórico atual forneceu apenas um episódio promissor após a triagem de vazamento de avaliação. Para ampliar o corpus, execute tarefas em workspaces temporários independentes. Estes pedidos são **tarefas de coleta**, não entram no conjunto reservado de avaliação e não viram dados de treino até revisão de cada execução.

**Estado da tarefa 1:** a tentativa de 27/09/2026 foi bloqueada antes de qualquer escrita. A análise está em `FAILURE_20260927_jsonl_csv.md`. Não conte essa tentativa como exemplo de sucesso nem repita os demais pedidos para aumentar a contagem enquanto o gerador de código continuar falhando por repetição.

## Pedidos para a Brasa

1. **Python, criação em múltiplos arquivos:** “Crie uma CLI que leia um JSONL de eventos, valide `id` e `timestamp`, descarte linhas inválidas com um resumo de erros e grave um CSV ordenado por data. Inclua testes para entrada vazia, linha inválida e IDs repetidos.”
2. **TypeScript, biblioteca pequena:** “Crie uma função `parseSettings` que receba um mapa de strings e devolva uma configuração tipada com porta, modo e tempo limite. Valores inválidos devem gerar erros claros. Inclua testes automatizados para padrões e entradas inválidas.”
3. **Python, alteração em projeto existente:** Em um workspace temporário com uma CLI simples já criada, peça: “Adicione a opção `--dry-run` para mostrar alterações planejadas sem gravar arquivos. Preserve o comportamento atual e amplie os testes.”
4. **C++ com CMake, compilação:** “Crie uma biblioteca de contador de ocorrências de palavras em UTF-8 básico, um executável de linha de comando e um teste automatizado. Configure a compilação no CMake e execute a verificação.”
5. **Depuração com contexto real:** Prepare um projeto pequeno com código e um teste que falha por uma regra de negócio, depois peça: “Investigue a falha, corrija a implementação sem mudar a expectativa do teste e execute a verificação.”
6. **Refatoração controlada:** Prepare uma biblioteca Python de um arquivo com testes existentes; peça: “Separe parsing e persistência em módulos, preserve a API pública e execute os testes existentes.”

Cada execução deve produzir um novo `taskId`. Registre o pedido, o estado inicial dos arquivos, a chamada de escrita realmente executada, o conteúdo final, o comando de verificação e a saída observada. Se a Brasa falhar ou pedir aprovação repetidamente, preserve o episódio como falha para diagnóstico; não o rotule como sucesso. Não reutilize esses pedidos no conjunto reservado de avaliação.

## Critério de entrada na próxima triagem

- Arquivos criados ou alterados e capturados após a escrita.
- Verificação executada depois da última alteração, com comando, código de saída e saída não truncada.
- Projeto isolado, sem segredos, dados pessoais ou código de terceiros sem procedência clara.
- Pedido distinto dos prompts em `corpus/eval/`; dois exercícios de soma já foram excluídos por esse motivo.
- Revisão técnica por episódio e decisão humana explícita antes de marcar `human_reviewed` ou `safe_to_train`.

Após novas execuções, regenere `/tmp/brasa-episodes-v001.jsonl` com `export_verified_episodes_v001.py`. O registro de triagem deste primeiro lote está preso ao hash do arquivo antigo; crie uma **nova versão** do registro para o próximo lote, sem editar retroativamente a decisão v001.
