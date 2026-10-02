# Baseline funcional do núcleo Brasa v1

**Executado em:** 28/09/2026  
**Commit observado antes dos casos:** `272b3d45f30f3bd2483ab46f8c68a0a25708d52d`  
**Resultado:** 0 de 3 tarefas concluídas pela Brasa. Os três bloqueios foram atribuídos a etapas observáveis nos registros da interface e do AgentCore.

## Ambiente e limites do registro

- Os pedidos foram enviados pela interface local da Brasa (`http://127.0.0.1:3000`) em um perfil Chromium descartável. O runtime Rust e o AgentCore locais estavam ativos.
- Cada caso usou um workspace descartável sob `/tmp/brasa-v1-baseline-20260928/`. As cópias reproduzíveis dos projetos de leitura e edição estão em [`baseline-v1/fixtures/`](baseline-v1/fixtures/); a pasta `create-project` começou vazia.
- Em cada tarefa, o AgentCore registrou 34 capacidades disponíveis. Isso confirma que o inventário foi apresentado, mas não prova que as capacidades eram adequadas ou que foram exercitadas.
- O commit do código foi registrado. A configuração completa do processo e a identidade/hash do checkpoint efetivamente carregado não foram capturados neste ensaio; por isso, os resultados descrevem esta execução local, sem alegar reprodutibilidade bit a bit.
- Não houve chamadas de pesquisa Brave nos três casos. Nenhum deles precisava de informação externa.
- Os eventos e relatórios de tarefa permaneceram no armazenamento local do AgentCore. IDs abaixo permitem localizar cada trajetória na lista de tarefas persistidas.

## Casos e resultados

| Caso | Tarefa | Resultado | Etapa observada do bloqueio |
|---|---|---|---|
| Entender um projeto existente | `task-c4e1e743-54fe-4a42-90b4-eabe23e8f405` | Bloqueado | A análise leu os três arquivos centrais; a síntese não satisfez `analysis.references` e não entregou uma explicação útil. |
| Criar um app novo em workspace vazio | `task-6dd0196d-1433-4520-8566-4ca6a4de0af6` | Bloqueado | O planejador encerrou duas chamadas com a mesma mensagem de fallback. Não apresentou proposta de arquivos; os critérios `build.change` e `build.verification` falharam. |
| Alterar app existente após continuação | análise `task-2ff92430-299b-4180-8f14-d6df35cb0031`; pedido de mudança `task-de44579c-b58e-4506-9b21-7658dc994d38` | Bloqueado | A análise leu `README.md` e `app.js` e falhou em `analysis.references`. A mensagem seguinte, na mesma conversa da interface, virou uma tarefa `build` separada. Ela reinspecionou o workspace, chamou duas vezes o planejador e não propôs alteração. |

### 1. Análise do Budget Pocket

Pedido enviado: “Analise o projeto Budget Pocket. Leia os arquivos relevantes e me explique o que ele faz, quais são seus arquivos principais, como posso executar as operações e que verificação existe. Não altere nenhum arquivo.”

O workspace começou com `README.md`, `expense_app.py` e `tests/test_expense_app.py`. O AgentCore selecionou e leu os três arquivos (`analysis.file.read` nos eventos 15, 22 e 29), cobrindo o inventário. Em seguida, o checkpoint repetiu a mensagem de geração de código sem receita compatível, apesar do objetivo de análise. Nenhum artefato ou evidência foi anexado ao relatório. O aceite marcou leitura e cobertura como satisfeitas, mas reprovou a citação de evidência (`analysis.references`). A tarefa terminou `blocked`.

O bloqueio foi seguro no sentido limitado de não alterar arquivos; a análise funcional pedida não foi entregue. A validação do relatório também não transformou o conteúdo lido em uma resposta sustentada.

### 2. Criação do Water Ledger

Pedido enviado: “Crie no workspace ativo um aplicativo web local chamado ‘Water Ledger’ para registrar o consumo diário de água. Requisitos: adicionar um registro com data e litros, mostrar a lista e o total de litros, permitir apagar um registro e manter os dados após recarregar a página. Use HTML, CSS e JavaScript sem dependências externas. Escreva os arquivos reais no projeto ativo, não entregue só uma prévia. Não copie uma receita de lista de tarefas. Faça as verificações possíveis e explique como abrir o app.”

O workspace começou vazio. O inventário detectou corretamente um projeto inicial; o plano incluiu seleção, inspeção, ciclo do planejador e verificação. As chamadas do planejador nos eventos 13–14 e 18–19 retornaram a mesma mensagem: geração local interrompida por repetição e ausência de receita compatível. Não houve aprovação solicitada, diff, gravação, execução de verificação ou artefato. O diretório continuou sem arquivos. A tarefa terminou `blocked` em `build.change` e `build.verification`.

### 3. Análise e alteração do Reading Shelf

Primeiro pedido: “Leia este aplicativo e me explique de forma breve onde os livros são guardados e como o estado de leitura é persistido. Não altere nenhum arquivo ainda.”

O inventário detectou os quatro arquivos (`README.md`, `app.js`, `index.html`, `styles.css`). Foram lidos `README.md` e `app.js`. A geração repetiu a mensagem de fallback e o aceite reprovou `analysis.references`; a explicação pedida não foi dada.

Como continuação, na mesma conversa da interface, foi enviado: “Agora implemente nesse Reading Shelf uma busca por título que ignore maiúsculas/minúsculas e um filtro ‘Somente não lidos’. Preserve os títulos e estados de leitura já salvos, a chave reading-shelf-books, os campos id/read e o desenho atual. Edite os arquivos necessários e verifique o comportamento; não use dependências externas.”

Essa mensagem criou outra tarefa classificada como `build`, com o mesmo workspace. A nova tarefa voltou a ler `README.md` e `app.js`, mas tratou “ambiente técnico” como informação ausente, embora o pedido especificasse uma aplicação web já selecionada. O planejador repetiu o fallback duas vezes; `evidence` e `artifacts` ficaram vazios. Nenhuma alteração ou verificação ocorreu. O estado de leitura e a interface existente ficaram intactos.

## O que o baseline demonstra

1. **Descobrir e selecionar o workspace funcionou no nível básico:** as três pastas corretas foram usadas; o caso vazio foi reconhecido como projeto inicial; os casos existentes foram inventariados.
2. **A observação local funcionou parcialmente:** houve leituras pertinentes nos dois casos de análise e na tentativa de alteração.
3. **A rota de análise cai no caminho de geração de código:** mesmo após leituras úteis, o checkpoint devolveu uma recusa/fallback de build.
4. **A geração livre não passou de planejamento:** nos dois pedidos de build, duas respostas repetidas não produziram uma proposta estruturada, gravação ou verificação.
5. **A continuação não preservou uma tarefa canônica:** a interface manteve a conversa e o workspace, mas o runtime criou um novo `taskId` e reclassificou o pedido. Não há evidência de transferência dos critérios e descobertas da tarefa de análise para o plano de build.
6. **Os bloqueios evitaram falsas alegações de sucesso:** a UI não informou que os apps foram criados ou modificados. Isso é um limite de segurança útil, mas não atende à funcionalidade da primeira versão.

## Ações seguintes

1. Corrigir o despacho de análise para responder com base nos arquivos lidos sem entrar no gerador de código.
2. Corrigir o contrato de geração para transformar uma proposta estruturada em alteração revisável, em vez de repetir o fallback.
3. Fazer cada ação produzir observação tipada e evidência; impedir uma segunda chamada idêntica do planejador quando não houve nova informação.
4. Manter um identificador e requisitos canônicos entre turnos da mesma tarefa, inclusive quando a intenção evoluir de análise para mudança.
5. Repetir estes mesmos três casos pela interface, conferindo diffs, execução e verificações; depois rodar variantes reservadas com prompts diferentes.

Este baseline não prova autonomia segura para editar projetos. Ele localiza falhas concretas antes da aprovação e da gravação, e mantém arquivos reproduzíveis para medir as correções.
