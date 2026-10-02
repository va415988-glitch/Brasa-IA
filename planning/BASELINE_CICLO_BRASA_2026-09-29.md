# Reexecução do ciclo da Brasa em 29/09/2026

**Objetivo:** medir o próximo gargalo após a análise do ciclo de agente.  
**Dados compactos:** [BASELINE_2026-09-29.json](baseline-v1/BASELINE_2026-09-29.json).  
**Experimentos separados:** [gerador isolado](baseline-v1/GENERATOR_ISOLATED_2026-09-29.json) e [núcleo com proposta controlada](baseline-v1/CORE_CONTROLLED_2026-09-29.json).  
**Execução:** API local `POST /api/v1/agent/pursue`, atravessando runtime Rust, AgentCore e worker Python. Workspaces descartáveis em `/tmp/brasa-eval-20260929/postpatch/`.

## Identidade e limites

- Commit base: `272b3d45f30f3bd2483ab46f8c68a0a25708d52d`. Havia alterações locais extensas; este não é um snapshot integral do código. O JSON guarda SHA-256 do checkpoint e dos principais componentes observados. Não comparar esta execução como se fosse uma versão limpa do commit.
- Checkpoint selecionado: `model/godmode/context-32768-v1/candidate.safetensors`, SHA-256 `0aeafd6f3966c8c59548ca1bb386c87483c62eafe21e95d1cb89260741d1fee3`.
- As avaliações de análise abaixo verificam presença de fatos e caminhos observados. Elas não substituem revisão humana de todas as afirmações. Nenhum check de navegador foi executado, porque nenhuma implementação foi proposta nos casos de build.
- Os quatro pedidos foram sequenciais. No Reading Shelf, a mensagem de mudança recebeu o texto da análise como histórico, mas o AgentCore criou outro `taskId`.

## Resultado

| Caso | Estado do AgentCore | Critério de produto | Primeira falha observada |
| --- | --- | --- | --- |
| Budget Pocket: explicar projeto | `completed` | Aprovado: finalidade, três arquivos, comandos de uso e check documentado | Nenhuma nos critérios desta sondagem |
| Water Ledger: criar app | `blocked` | Reprovado: nenhum arquivo novo e nenhuma verificação | Gerador devolveu fallback de repetição; não houve proposta válida |
| Reading Shelf: explicar persistência | `completed` | Aprovado após correção: chave, leitura, gravação e mudança de `read` citadas com linhas de `app.js` | Antes da correção, o estado era `completed`, mas esses fatos não apareciam na resposta |
| Reading Shelf: busca e filtro | `blocked` | Reprovado: arquivos originais intactos e nenhuma verificação | Gerador devolveu fallback de repetição; não houve proposta válida |

**Contagens desta bateria:** 2/2 análises diagnósticas atenderam aos critérios de texto; 0/2 construções chegaram a uma mudança; 1/3 tarefas de produto foram concluídas, contando a análise e alteração do Reading Shelf como uma tarefa composta. A diferença aparente em relação ao baseline de 28/09 (0/3) é de **+1 tarefa nos mesmos três casos**, mas houve mudanças de código e a amostra é pequena. Não atribuir esse número a uma causa única nem extrapolá-lo para projetos novos.

## Correção feita durante a investigação

A síntese de análise em `python/model_server.py` agora inclui linhas lidas de JavaScript/TypeScript que revelam a chave de `localStorage`, chamadas `getItem`/`setItem` e atribuições ao campo `read`. O texto distingue essas observações de um teste no navegador. Foi acrescentado um teste com o `app.js` da fixture do Reading Shelf. Na reexecução pela API, a resposta citou:

- `app.js:1`: `STORAGE_KEY = "reading-shelf-books"`;
- `app.js:6`: leitura via `localStorage.getItem(STORAGE_KEY)`;
- `app.js:9`: gravação via `localStorage.setItem(STORAGE_KEY, JSON.stringify(books))`;
- `app.js:24`: `book.read = read.checked`.

Isso corrige uma resposta incompleta que passava pelo gate de análise porque o gate exigia citação de arquivo, mas não verificava os fatos específicos pedidos. A extração de linhas é limitada e não equivale a compreensão geral de código arbitrário.

## Onde concentrar o próximo experimento

O funil observado chegou à inspeção nos quatro pedidos. As duas análises produziram respostas verificáveis. **Nenhum dos dois builds produziu uma proposta editável**, apesar de o runtime anunciar ferramentas de escrita; portanto, alterar a política de aprovação ou adicionar mais ferramentas não atacaria a primeira falha desta bateria.

Na avaliação isolada, o gerador recebeu os pedidos de Water Ledger e Reading Shelf com o inventário e, para Reading Shelf, os arquivos centrais lidos. Em ambos, `quality_gate_result=rejected`, `quality_stop_reason=repeated-fragment`, `stop_reason=implementation_proposal_unavailable` e **nenhuma chamada de ferramenta**. Isso confirma o gargalo de geração sem depender da política de aprovação do AgentCore.

Na avaliação controlada, o pedido continha literalmente dois arquivos Python (`app.py` e `test_app.py`). O AgentCore propôs `apply_batch`, aguardou aprovação, criou os arquivos, aguardou aprovação para `project_checks`, executou `unittest` e concluiu com **1 teste aprovado**. O mesmo `taskId` foi mantido nas três etapas. Esse resultado comprova o caminho de aplicação e verificação para uma proposta fornecida; **não comprova geração autônoma** nem cobertura de um aplicativo web inédito.

O próximo teste deve comparar geradores sob o mesmo contrato, com tarefas reservadas e proposta avaliada antes da escrita. Depois, testar o núcleo com propostas controladas que falhem na verificação para medir diagnóstico e reparo. O [plano do agente programador](PLANO_AGENTE_PROGRAMADOR_FUNCIONAL.md) já define uma bateria reservada maior; estes casos continuam sendo diagnóstico de desenvolvimento.

A análise posterior do pedido Horas de Estudo está em [diagnóstico da geração de código](DIAGNOSTICO_GERACAO_CODIGO_2026-09-29.md); ela mede o descompasso entre treino e contrato e corrige um falso aceite do filtro de qualidade.

## Verificação da correção

- `PYTHONPATH=python .venv/bin/python -m pytest -q tests`: **362 passed**, 6 avisos de PyTorch.
- `npm test` em `agent-core`: **73 passed**.
- `git diff --check` nos arquivos alterados: sem erros.
- Reexecução pela API após reiniciar os serviços: Reading Shelf passou nos quatro critérios de evidência de persistência; os dois builds continuaram bloqueados antes de escrever.
