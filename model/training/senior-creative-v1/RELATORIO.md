# Treinamento de engenharia e criatividade — 20/09/2026

**Resultado: treinamento executado; candidato reprovado para uso.** Nenhum checkpoint ativo foi substituído. A redução de loss não se converteu em respostas úteis.

## Trabalho executado

- Duas rodadas de continuação dos pesos, totalizando 1.000 passos (600 + 400), em CPU.
- Modelo de 3.487.232 parâmetros, 4 camadas e janela de 256 tokens; tokenizer preservado.
- 54 exemplos autorais sintéticos novos de engenharia, criatividade e confiabilidade.
- 227 exemplos de treino, 19 de validação e 12 pedidos autorais reservados; 566 janelas de treino incluindo variantes com perfil.
- Deduplicação por pergunta normalizada; exclusão das perguntas de avaliação; máscara de loss apenas nas respostas; clipping, warmup, decaimento e seleção pela validação.
- Comparação de quatro checkpoints de origem. Checkpoints, splits, hashes, histórico e respostas antes/depois preservados.

## Resultados

| Rodada | Origem | Passos | Loss de validação antes → depois | Loss final reservada antes → depois |
| --- | --- | ---: | --- | --- |
| 01 | compact-08-gate-focus | 600 | 30.9860 → 5.3452 | 29.8613 → 5.0199 |
| 02 | compact-06-augmented-v2 | 400 | 5.0450 → 4.4194 | 4.9317 → 4.1996 |

A rodada 02 foi selecionada pela menor loss de validação. Seu ganho relativo de loss nos pedidos reservados foi **14.8%** em relação ao próprio checkpoint de origem. Isso mede previsão de tokens com respostas de referência, não sucesso de tarefas.

**Revisão das 12 respostas inéditas: 0 aprovadas; 5 vazias e 7 incoerentes.** Geração greedy, até 128 tokens, com o mesmo prompt de avaliação para cada origem/candidato. As respostas completas estão em `run-02/report.json` e `quality_review.json`.

No teste legado, o modelo ativo marcou 10/12 e o candidato 0/12. Esse teste é apenas de regressão: suas perguntas aparecem no corpus histórico, e o filtro lexical aceita algumas respostas com finais incoerentes. Não é uma medida independente de competência.

## Decisão

**Não promover.** Além da falha qualitativa e da regressão, o checkpoint não atende à política de contexto mínimo de 8192 tokens nem à exigência de geração profissional. A auditoria está em `promotion_audit.json`. Os originais permanecem intactos, conforme `original_checkpoints_integrity.json`.

A validação inclui replay possivelmente visto no treinamento original; apenas os 12 pedidos autorais novos foram reservados desta rodada e não usados no otimizador ou na seleção. Não houve avaliação humana independente. Os dados sintéticos são exemplos didáticos, não certificação de competência.

A evidência deste experimento indica que o ajuste curto sobre a base atual é insuficiente para formar linguagem e seguir instruções de modo confiável. O próximo marco técnico é demonstrar geração coerente em uma base com pré-treinamento mais amplo, antes de investir em especialização sênior ou ampliar a janela. Não foi demonstrado que a arquitetura atual atingiu seu limite absoluto.

## Verificações

49 testes do assistente + 3 testes do pipeline passaram. Cinco exemplos Python do currículo foram executados com verificações de comportamento. Essas verificações validam o pipeline e os exemplos, não a qualidade dos pesos gerados.

## Arquivos e reprodução

- Candidato experimental escolhido: `run-02/candidate.safetensors` e metadados adjacentes.
- Treinador: `python/finetune_assistant.py`.
- Currículo: `python/data/senior_creative_v1.jsonl`; gerador: `python/build_senior_curriculum.py`.
- Procedência e parâmetros completos: `run-01/manifest.json` e `run-02/manifest.json`.

Executar da raiz, com diretório novo:

```bash
.venv/bin/python python/finetune_assistant.py --checkpoint model/checkpoints/compact-06-augmented-v2.safetensors --output-dir model/training/senior-creative-v1/repro-02 --steps 400 --threads 3 --batch-size 8 --learning-rate 0.00015
```

O comando produz artefatos experimentais e não muda o modelo em uso.

## Iteração de avaliação aberta — 21/09/2026

**Resultado: comportamento do agente melhorado; geração neural ainda não promovida.**

Foram executadas duas continuações de treino e uma bateria de programação com perguntas fora do conjunto de treino imediato:

- `run-05`, continuando `run-02`: validação **4.3972 → 4.3415** e reserva **4.2203 → 4.1277** em 400 passos.
- `run-06`, continuando `run-05` com oito exemplos de programação: validação **4.3415 → 4.3153** e reserva aberta **4.3484 → 4.2500**; melhor passo 250/300.
- Os dois candidatos permaneceram reprovados. As amostras livres do checkpoint continuaram com texto incoerente ou vazio em tarefas inéditas; o runtime segue usando `compact-08-gate-focus.pt` com gate de qualidade.

O agente recebeu um currículo aberto de programação (`python/data/open_programming_curriculum_v1.jsonl`) e quatro perguntas held-out (`model/training/senior-creative-v1/open-programming-heldout-v1.jsonl`). A camada procedural agora responde de forma verificável a classes recorrentes — código com testes, rotas HTTP que usam banco, medição de desempenho e validação de argumentos — e bloqueia recuperação local sem relação sem inventar uma resposta.

## Evidência de comportamento

- `139 passed`, 3 avisos do PyTorch e 33 subtestes.
- `cargo test --manifest-path runtime/Cargo.toml`: 32 testes do runtime passaram.
- Currículo agêntico: 4/4; seleção de ferramentas: 12/12 (100%); fluxo multi-etapas completado com continuidade de trace.
- Benchmark aberto de programação: **12/12 respostas acionáveis**, 8 de currículo e 4 held-out, todas com backend curado e conteúdo mínimo verificável.
- Replay manual na UI confirmou: resposta com código e testes, avaliação de desempenho, leitura de dois arquivos grandes com continuação por `offset` e bloqueio de uma pergunta técnica sem evidência pertinente.

Essa pontuação mede o contrato do agente e as respostas curadas, não prova que o checkpoint neural adquiriu programação geral. A próxima promoção exige geração livre coerente em avaliações não vistas; até lá, respostas sem evidência continuam explicitamente bloqueadas.

## Fontes externas incorporadas — 21/09/2026

Foram consultadas a [categoria de Agentes de IA da Data Science Academy](https://blog.dsacademy.com.br/categoria/agentes-de-ia/) e o [Deep Learning Book Brasil](https://www.deeplearningbook.com.br/). O material foi usado como referência para exemplos originais, não como cópia integral. Os temas aproveitados foram:

- harness como a combinação entre modelo, contexto, ferramentas, memória, validação, permissões, orquestração e observabilidade;
- estado persistente, ciclo descobrir–planejar–executar–verificar, limites de custo/passos, worktrees e separação entre implementação e revisão;
- desenvolvimento orientado a especificações, schemas fechados, avaliação contínua, golden datasets e quality gates;
- fundamentos de treinamento: forward/backward pass, backpropagation, função de perda, regularização, overfitting, separação treino/validação/teste e paralelismo em GPU.

O novo currículo está em `python/data/agent_harness_curriculum_v1.jsonl`, com procedência por exemplo, e os seis casos não vistos em `model/training/senior-creative-v1/agent-harness-heldout-v1.jsonl`. O benchmark `tests/benchmark_agent_harness.py` obteve 13/13 exemplos acionáveis e 6/6 casos held-out bloqueados sem recuperar documentos irrelevantes.

A continuação `run-07` foi deliberadamente reprovada: validação **4.3153 → 4.3153**, held-out **4.3597 → 4.3597**, melhor passo 0/300. Isso indica que acrescentar conhecimento ao corpus não corrigiu a geração neural; a camada de harness e os quality gates melhoraram, mas o checkpoint ativo não deve ser substituído.

## Dataset derivado de `huggingface/datasets` — 21/09/2026

O repositório foi fixado no commit `3e2c1a6c33b883fd9710f55f7b6fa4ab64d0ee07` e auditado sob Apache-2.0. Em vez de copiar a base inteira, foram selecionados e reescritos 12 padrões de engenharia de dados a partir da documentação versionada: JSONL e splits, `map`, streaming, `Dataset` versus `IterableDataset`, cache e fingerprints, schema, PyTorch, sharding, dataset cards e procedência. O manifesto de cada exemplo registra arquivo de origem, revisão e licença em `python/data/hf_datasets_curriculum_v1.jsonl`.

Os seis casos held-out estão em `model/training/senior-creative-v1/hf-datasets-heldout-v1.jsonl`. O benchmark `tests/benchmark_hf_datasets_curriculum.py` marcou **12/12** exemplos úteis e **6/6** casos inéditos bloqueados sem recuperação irrelevante.

A rodada `run-08`, continuando `run-06`, melhorou a loss de validação **4.3153 → 4.2762** e a held-out **4.3316 → 4.2194**, com melhor passo 250/300. A geração livre do candidato ainda falhou no quality gate em todos os seis casos inéditos; portanto, o ganho é uma indicação de aprendizado de tokens, não prova de competência. O checkpoint não foi promovido.

## Trilha de 100 capítulos do Deep Learning Book — 21/09/2026

O índice público do [Deep Learning Book](https://www.deeplearningbook.com.br/indice/) lista 100 capítulos, de perceptron, gradiente e regularização a CNNs, RNNs, GANs, aprendizado por reforço, BERT, GPT, CLIP, Transformers e um guia de machine learning. O mapa completo, com trilhas e prioridade, está em `python/data/deep_learning_book_map_v1.json`; ele registra o índice e a data de consulta sem copiar o texto dos capítulos.

Foram reescritos **27 exemplos originais** em `python/data/deep_learning_book_curriculum_v1.jsonl`, cobrindo diagnóstico e verificação de treino, visão, sequências, modelos generativos, reforço e modelos de linguagem. Oito formulações inéditas ficaram fora do otimizador em `model/training/senior-creative-v1/deep-learning-book-heldout-v1.jsonl`. O benchmark `tests/benchmark_deep_learning_book.py` verificou **100/100 capítulos mapeados, 27/27 respostas curadas e 8/8 casos inéditos bloqueados sem recuperação irrelevante**.

A continuação `run-09`, partindo de `run-08`, reduziu a loss de validação **4.2554 → 4.1957** e a loss held-out **4.0603 → 3.8913** em 300 passos; melhor passo 300. Mesmo com essa redução, a geração livre devolveu `null` nos oito casos reservados e todos permaneceram no quality gate. O candidato **não foi promovido**; o ganho mede previsão de tokens com respostas de referência, não competência autônoma. O checkpoint ativo continua protegido.

## PDFs anexados: GenAI e fundamentos — 21/09/2026

Os PDFs `e-book GenAI.pdf` (Databricks, 118 páginas, criado em 2024) e `lbdl-a5-booklet.pdf` (François Fleuret, versão 1.2 de 2024, 90 páginas físicas e 179 páginas impressas) foram lidos, tiveram hash registrado e passaram por inspeção visual de páginas. O segundo declara licença **Creative Commons BY-NC-SA 4.0**; no primeiro não foi encontrada licença explícita no arquivo. O manifesto está em `python/data/attached_books_manifest_v1.json`.

O material foi incorporado somente como **29 paráfrases originais**: 12 sobre produção de GenAI, RAG, fine-tuning, pré-treinamento, avaliação e rastreabilidade; 17 sobre fundamentos de deep learning, atenção, arquiteturas, síntese, quantização, adapters e merge de modelos. O texto integral, exemplos promocionais, comandos e instruções dos PDFs não entram no corpus e não são executados. Os currículos são `python/data/databricks_genai_curriculum_v1.jsonl` e `python/data/little_book_deep_learning_curriculum_v1.jsonl`; os 10 casos inéditos estão em `attached-books-heldout-v1.jsonl`.

O benchmark `tests/benchmark_attached_books.py` confirmou **29/29 respostas curadas e 10/10 casos inéditos bloqueados sem recuperar documentos irrelevantes**. A rodada `run-10`, continuando `run-09`, teve validação **4.1958 → 4.1821**, mas a perda held-out piorou **4.0189 → 4.3621**; a geração livre foi vazia nos 10 casos. O candidato foi rejeitado e o checkpoint ativo não mudou. O aumento de conteúdo melhora a camada de referência, mas não resolve a insuficiência de pré-treinamento do modelo neural.

## Contrato de tarefa, evidências e entrega verificável — 21/09/2026

Esta iteração transformou o ciclo do agente em um protocolo observável, independente da qualidade do checkpoint neural. Cada tarefa iniciada pelo endpoint persistente recebe um contrato `task-contract/v1` em `python/agent_contract.py`, derivado do pedido do usuário antes da primeira ferramenta. O contrato registra intenção, escopo no workspace, risco, efeitos colaterais, necessidade de aprovação, orçamento de passos, critérios de aceitação e políticas de segurança. Pedidos de escrita e ações destrutivas são classificados conservadoramente; texto recuperado de arquivos e páginas é tratado como dado, nunca como instrução do executor.

O executor agora mantém um ledger compacto de evidências por chamada: ferramenta, identificador, hash do resultado, caminhos observados, resumo e sequência. Uma tarefa não pode terminar como entrega verificada quando uma escrita ainda não passou por verificação, quando existe falha de ferramenta ou quando o orçamento foi atingido. O estado final inclui `delivery.status`, contagem de evidências e a marca explícita `verified`, evitando declarar conclusão com base apenas na intenção do planejador.

O fluxo do chat exibe o contrato ao lado do painel de etapas, com estado (`draft`, `observing`, `awaiting-verification`, `verified`), risco, efeito, aprovação e critérios. A separação impede que o histórico operacional seja confundido com a especificação da tarefa. A interface foi recompilada e validada no navegador local: uma leitura de arquivos mostrou simultaneamente `Contrato · verified` e `Etapas`, enquanto a API preservou uma evidência e a entrega verificada.

Foram adicionados sete testes unitários de contrato, um teste de persistência do ledger e validações de sintaxe/build da interface. A suíte completa marcou **151 testes Python**, com 3 avisos conhecidos do PyTorch, e `cargo test --manifest-path runtime/Cargo.toml` marcou **32 testes do runtime** mais o teste de mistura de corpus. Este marco fortalece a autonomia operacional, mas não promove o checkpoint neural: a geração livre continua reprovada nos casos inéditos e segue protegida pelo quality gate.

## Workspace como IDE e ações compactas — 21/09/2026

O Workspace foi reestruturado para o fluxo de um editor de código. O Explorer passa a ocupar a coluna lateral do modo de projeto, o editor ocupa a área central com aba, caminho, contagem de linhas, estado local, preview e ações de revisão, e a conversa permanece em um painel inferior dedicado ao agente. Quando nenhum arquivo está aberto, o editor mostra um estado vazio orientado a ação; ao abrir um arquivo, a área passa a ser o foco principal. O Explorer pode ser recolhido e sua preferência fica persistida localmente.

As atividades do agente deixaram de ser cartões grandes com bordas fortes. Agora aparecem como linhas de estado compactas, mantendo contrato e etapas recolhíveis para auditoria sem interromper a leitura da conversa. O overlay de código segue o mesmo fluxo: diff, aceitar/rejeitar, abrir no Workspace e acesso ao painel do navegador ficam agrupados no mesmo contexto.

O binário Rust foi recompilado e o smoke test visual no navegador confirmou a nova aba de editor, estado vazio, painel inferior de conversa, abertura real de `preview/index.html`, indicador de alteração e ação `Contrato · verified`. Após a primeira inspeção, o contraste foi elevado: superfícies do IDE, Explorer, editor e conversa agora usam fundo claro e texto escuro para manter leitura em monitores grandes. A suíte de comportamento do agente não foi alterada por esta mudança visual.

Uma segunda inspeção mostrou que o editor vazio ainda podia consumir a altura inteira da janela. O layout foi ajustado para limitar o editor aberto a aproximadamente metade da viewport, reduzir o estado vazio a uma faixa compacta e deixar o chat com crescimento e rolagem próprios. O status superior também passou a truncar mensagens longas, impedindo que resultados de pesquisa ocupem a barra inteira.

## Integração VS Code e teste de utilidade — 21/09/2026

A integração com VS Code foi classificada como **alpha de teste manual**, não
como agente pronto para uso real. O runtime respondeu ao health check, à
seleção de workspace e à leitura de `integrations/vscode/package.json`; uma
pergunta curada sobre ownership em Rust também retornou resposta verificável.
O mesmo smoke test enviou uma revisão inédita de JavaScript e recebeu
documentação genérica de JavaScript, sem identificar o acesso `items[1]`. Esse
resultado é uma reprovação semântica apesar do HTTP ter retornado sucesso.

A extensão agora mantém até oito etapas de `tool_call`, devolve resultados
estruturados ao runtime e pede confirmação modal para ferramentas de escrita.
Também corrige a prévia de alteração para substituir exatamente a seleção e
recusa operar sobre um documento com mudanças não salvas. O planejador passou
a priorizar um caminho de arquivo único explícito, mantendo a estratégia de
listar primeiro quando o pedido cita vários arquivos. A suíte completa marcou
**151 testes Python**; `npm test` e os checks estáticos da extensão passaram.

A extensão também contribui o participante nativo `@ia-local` para o Chat do
VS Code, com `/explain`, `/review`, `/test` e `/workspace`. O participante usa
o mesmo ciclo local de ferramentas, histórico da conversa, cancelamento e
confirmação para escritas. Para corrigir a integração vista no seletor de
modelos, o manifesto agora declara o provider `ia-local` e a extensão registra
`vscode.lm.registerLanguageModelChatProvider`; o modelo `IA Local do Zero`
passa a ser oferecido ao lado de Codex e Claude no model picker. A extensão
exige VS Code 1.104 ou superior, quando essa API foi finalizada.

Como o screenshot do usuário era a lista de sessões de agentes (e não o
model picker), a extensão também declara o tipo de sessão `ia-local` em
`contributes.chatSessions` e registra `registerChatSessionItemProvider` e
`registerChatSessionContentProvider`. Isso cria a entrada **IA Local do Zero**
na mesma seleção de sessões de Chat/Codex/Claude e conecta a sessão ao handler
local, mantendo o `@ia-local` como compatibilidade.

O teste de ativação verificou o registro do provider e seus três métodos; um
smoke adicional chamou o modelo `ia-local-zero` contra o runtime real e recebeu
uma resposta verificável sobre ownership em Rust. O smoke HTTP completo passou
health check, seleção de workspace, leitura de arquivo e a resposta curada
(4/4 transportes); a revisão inédita de JavaScript continuou reprovada no
quality gate por retornar documentação genérica. Portanto, a ponte agora
funciona tecnicamente, mas a qualidade do agente ainda não é suficiente para
chamá-lo de autônomo.

O checkpoint continua fora de produção: a política local informa janela efetiva
de 256 tokens, mínimo de 8192 e `production_eligible=false`. A extensão foi
empacotada como VSIX local e instalada no VS Code (`ia-local-do-zero.ia-local-do-zero`
0.1.0); ainda é necessário recarregar a janela aberta para o Extension Host
carregar a versão nova. O log do VS Code 1.138 mostrou que a instância normal
recusa `chatSessionsProvider` para extensões não autorizadas; uma janela de
desenvolvimento isolada com `--enable-proposed-api ia-local-do-zero.ia-local-do-zero`
ativou `onChatSession:ia-local` sem erro. A extensão agora captura essa recusa
para manter `@ia-local` e o modelo disponíveis no host comum. A revisão
semântica em tarefas inéditas também continua inconsistente, portanto isso
ainda não é um agente autônomo de produção.
