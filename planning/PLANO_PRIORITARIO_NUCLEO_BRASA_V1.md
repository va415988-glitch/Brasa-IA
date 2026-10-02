# Plano prioritário para o núcleo da Brasa v1

**Data:** 28/09/2026  
**Status:** execução em andamento; Fase 0 tem um baseline inicial pela interface, registrado em [relatório de 28/09/2026](BASELINE_NUCLEO_BRASA_V1_2026-09-28.md). A identidade completa do checkpoint e a rodada reservada ainda faltam.  
**Objetivo:** tornar a Brasa capaz de receber uma tarefa de software pela interface, reconhecer o workspace e as capacidades disponíveis, planejar, agir com limites, verificar o resultado e continuar ou explicar um bloqueio com evidências.

Este documento prioriza o trabalho já detalhado em [reestruturação do núcleo](PLANO_REESTRUTURACAO_NUCLEO_AGENTE.md), [agente programador funcional](PLANO_AGENTE_PROGRAMADOR_FUNCIONAL.md) e [portfólio de APIs e skills](PLANO_PORTFOLIO_APIS_E_SKILLS.md). Não substitui esses planos; define a ordem prática para a próxima versão testável.

## Diagnóstico de partida

Há uma base operacional real: AgentCore tem estados cognitivos, requisitos, critérios de aceite, registro de capacidades, execução com validação de argumentos, limites de ações, persistência de tarefas, aprovações e eventos. O runtime Rust fornece acesso controlado ao workspace. A busca Brave LLM Context agora funciona no runtime depois de corrigir o idioma para `pt-br`.

Os componentes ainda não demonstram competência de ponta a ponta. A rota livre de geração pode repetir ou falhar antes de produzir uma alteração válida; os relatórios anteriores registram bloqueio de geração e desvio para uma receita de tarefas que não atendia ao pedido de outro produto. Uma busca ampla também trouxe fontes de qualidade mista, enquanto restringir a consulta à documentação oficial retornou evidência melhor. Portanto, chamadas bem-sucedidas e arquivos criados não são o critério de avanço.

O núcleo consegue impor limites, registrar atividade e bloquear falsas conclusões. Ele precisa melhorar principalmente na descoberta do ambiente, escolha de capacidades, manutenção dos requisitos, conversão do plano em alterações pequenas, recuperação e avaliação do produto executado. A capacidade do modelo de gerar propostas válidas deve ser medida em uma trilha separada: orquestração não substitui essa competência.

## Solução de núcleo orientada por pesquisa

O padrão útil não é um agente que “sabe tudo”, mas um controlador que mantém uma tarefa explícita, escolhe uma capacidade disponível, observa seu efeito e decide de novo. ReAct formaliza a alternância entre raciocínio e ação. SWE-agent mostra que a interface oferecida ao modelo muda sua capacidade de navegar, editar e testar código. Toolformer demonstra que selecionar ferramentas, formar argumentos e incorporar resultados também depende de competência aprendida no modelo. Reflexion investiga o uso de feedback de execução em memória episódica. τ-bench avalia o estado final da tarefa e a consistência em várias tentativas. Essas linhas se complementam; nenhuma, isoladamente, torna um modelo competente.

### Arquitetura proposta

1. **Estado canônico da tarefa.** Guardar o pedido ativo, objetivo, restrições, critérios de aceite, workspace, descobertas com origem, dúvidas, plano, última observação, aprovações, tentativas e verificações sob um ID que sobreviva às mensagens seguintes. Uma continuação atualiza a tarefa; um novo produto cria outra.
2. **Observação antes de inferência.** Descobrir stack e comandos do workspace por arquivos reais. Registrar fatos como `{valor, origem, trecho/linha, hash observado}` e separar fatos, hipóteses e desconhecidos. Ao mudar arquivo, invalidar qualquer evidência derivada do conteúdo anterior.
3. **Catálogo de capacidades acionável.** Expor ao decisor somente ferramentas realmente presentes, cada uma com schema de argumentos, pré-condições, efeito, risco, reversibilidade e evidência de sucesso. Ajustar a ACI: por exemplo, leitura delimitada, diff pequeno, check selecionado por manifesto e resultado legível, em vez de entregar uma lista longa de primitivas equivalentes.
4. **Ciclo fechado de ação.** Em cada volta, produzir uma decisão estruturada (`chamar ferramenta`, `concluir com evidência`, `pedir dado`, `bloquear com motivo`), validar argumentos e política, solicitar aprovação quando cabível, executar, observar a resposta tipada e comparar com os critérios. A resposta em prosa não conta como efeito de ferramenta.
5. **Recuperação com novidade.** Identificar repetição por assinatura de ferramenta e argumentos, registrar erro/efeito conhecido e exigir nova observação ou estratégia antes de tentar outra vez. Permitir até uma recuperação genuinamente diferente por causa; se ela não existir, expor o bloqueio exato. Falha do gerador não deve fingir que a ferramenta de edição está indisponível.
6. **Verificação por resultado.** Usar verificadores independentes da narrativa do modelo: conteúdo/diff no workspace, comando de teste detectado, inicialização local e interação pedida. Concluir apenas com critérios observáveis; separar “ação aceita”, “arquivo mudou”, “check executou” e “resultado passou”.
7. **Memória procedural revisável.** Guardar uma lição curta somente a partir de feedback observado e associá-la a stack/condições. Reutilizar como sugestão, não como receita autoritativa. Não transformar respostas externas ou instruções em workspace em permissão para agir.
8. **Competência do planejador.** Construir exemplos rotulados de tarefa → ferramentas e argumentos válidos → observações → recuperação → estado final. Avaliar e, se necessário, ajustar o checkpoint para esse protocolo. Métricas e feedback não devem se reduzir à fluência da resposta; usar sucesso por critério, chamadas válidas, regressões, repetição, qualidade das evidências e consistência entre reexecuções.

### Mapeamento para os defeitos observados

| Evidência do baseline | Mudança necessária |
|---|---|
| A análise leu os arquivos, mas recebeu a mensagem genérica de falha do gerador e não sintetizou os achados. | Rota de análise com contrato de síntese separado: conclusão ancorada nos resultados de leitura; validador de citações por caminho realmente observado; nunca encaminhar texto de falha de build como se fosse a análise. |
| Dois pedidos de build repetiram o mesmo fallback, sem chamada de ferramenta, gravação ou check. | Validar protocolo de decisão; detectar saída idêntica; recuperar uma vez com uma alternativa real baseada no catálogo e nos critérios. Se o checkpoint não souber compor ferramentas, parar sem consumir ciclos e registrar competência ausente para avaliação/treino. |
| A edição na conversa seguinte recebeu outro `taskId`, reinspecionou os mesmos arquivos e não recebeu a análise estruturada anterior. | Resolver o ID de conversa para tarefa; classificar adição, continuação, mudança de objetivo ou novo escopo; carregar achados/requisitos relevantes e descartar instruções temporárias que já foram superadas, como “ainda não altere”. |
| O registro conhece o número de ferramentas, mas isso não comprova seleção ou uso correto. | Avaliar qualidade da escolha, argumentos e efeito, não só o evento `runtime.capabilities.observed`. |

### Prioridade de implantação

Começar por três mudanças de núcleo ligadas ao baseline: (a) sintetizador de análise estritamente baseado nos resultados de leitura; (b) detecção de resposta/ferramenta repetida e recuperação limitada com diagnóstico novo; (c) estado de continuação ligado à conversa e persistido. Em seguida, ajustar a interface de ferramentas e alimentar o planejador com trajetórias de ferramenta revisadas. Manter a primeira stack pequena (HTML/CSS/JS sem dependências e Python simples), porque ampliar a lista de ferramentas antes de o ciclo funcionar adiciona escolhas sem aumentar competência.

O verificador inicial deve usar o benchmark descartável já criado, com novos pedidos reservados e estado final conferido. Repetir cada caso várias vezes após alterar o checkpoint ou o planejador; um sucesso isolado não demonstra confiabilidade. A métrica `pass^k` de τ-bench inspira medir quantas tarefas passam em todas as k repetições, mas os limiares concretos devem ser definidos com os resultados da própria Brasa.

### Fontes primárias

- Yao et al., [ReAct: Synergizing Reasoning and Acting in Language Models](https://arxiv.org/abs/2210.03629) — alternância entre ações e observações externas.
- Schick et al., [Toolformer: Language Models Can Teach Themselves to Use Tools](https://arxiv.org/abs/2302.04761) — decisão de quando chamar API, argumentos e incorporação do retorno por competência do modelo.
- Yang et al., [SWE-agent: Agent-Computer Interfaces Enable Automated Software Engineering](https://arxiv.org/abs/2405.15793) — efeito da interface ferramenta-agente em navegação, edição e testes de software.
- Shinn et al., [Reflexion: Language Agents with Verbal Reinforcement Learning](https://arxiv.org/abs/2303.11366) — feedback verbal em memória episódica, sem atualização de pesos.
- Yao et al., [τ-bench: A Benchmark for Tool-Agent-User Interaction in Real-World Domains](https://arxiv.org/abs/2406.12045) — estado final como critério e consistência em execuções repetidas.

As recomendações acima são uma síntese aplicada dessas fontes ao código e ao baseline da Brasa; os artigos não testaram este runtime nem garantem que uma implementação específica aumentará sua taxa de sucesso.

## Resultado que a v1 deve provar

Em um workspace descartável escolhido para avaliação, o usuário descreve um aplicativo pequeno em linguagem natural. A Brasa deve:

1. Identificar o diretório, a estrutura, a stack, os manifestos e as capacidades de leitura, escrita e verificação que realmente estão disponíveis.
2. Preservar o pedido, as restrições e os critérios de aceite durante a tarefa e nas mensagens seguintes.
3. Consultar documentação externa somente quando isso resolver uma incerteza atual ou técnica; priorizar fontes oficiais e manter fontes e trechos associados às afirmações.
4. Planejar alterações coerentes com o projeto, ler antes de editar e propor mudanças incrementais revisáveis.
5. Aplicar mudanças pelos contratos e aprovações do runtime, observar os efeitos e executar verificações pertinentes.
6. Corrigir falhas recuperáveis sem repetir a mesma ação sem nova evidência; continuar enquanto existir um próximo passo seguro.
7. Concluir apenas quando os critérios do pedido tiverem evidência observada. Caso contrário, mostrar o estado parcial e o motivo específico do bloqueio.

A demonstração deve usar tarefa inédita reservada, sem depender de uma receita fixa como lista de tarefas. A interface deve mostrar a trajetória real e permitir revisar o diff, o resultado e as verificações.

## Sequência de execução

### Fase 0 — Baseline confiável

**Entrega:** conjunto pequeno e reproduzível de tarefas e relatório do comportamento atual.

**Progresso:** baseline inicial executado em 28/09/2026. Os três casos bloquearam antes de qualquer gravação; o relatório registra os IDs, eventos, critérios reprovados e fixtures. Falta capturar identidade/hash do checkpoint e repetir uma rodada reservada antes de fechar esta fase.

- Capturar commit, configuração do runtime, checkpoint efetivamente carregado e estado limpo do workspace.
- Preparar três casos descartáveis: entender um projeto existente, criar um app pequeno descrito em linguagem natural e alterar um app existente após um requisito de continuação.
- Reservar variantes de teste que não sejam usadas para ajustar regras ou prompts.
- Registrar por caso: intenção interpretada, critérios, capacidades disponíveis, chamadas, argumentos, efeitos, diffs, verificações, estado final e esforço de correção humano.

**Gate:** cada falha pode ser atribuída a uma etapa observável do ciclo. Números sem artefato e sem denominador não contam como baseline.

### Fase 1 — Descobrir o ambiente e as capacidades reais

**Entrega:** inventário de workspace e capacidades fornecido ao AgentCore antes do primeiro plano.

- Distinguir workspace ausente, vazio e existente. Em workspace existente, identificar stack e comandos pelos manifestos e arquivos reais.
- Para cada capacidade, expor descrição, argumentos, pré-condições, efeito esperado, risco, reversibilidade, requisitos de aprovação e evidência que deve voltar.
- Mostrar apenas capacidades disponíveis no runtime atual. Uma skill pode sugerir um fluxo, mas cada ação precisa corresponder a uma capacidade contratada.
- Dar preferência a inspeção local antes de inferir estrutura, dependências ou comandos.

**Gate:** nos três casos do baseline, o inventário corresponde ao que o runtime consegue observar e o plano não inventa ferramentas ou arquivos.

### Fase 2 — Tornar a tarefa persistente e executável

**Entrega:** uma representação canônica de tarefa que sobrevive ao ciclo de ferramenta e às continuações.

- Preservar pedido original, workspace, requisitos, restrições, critérios, perguntas pendentes, evidências, plano, aprovações, orçamento e estado.
- Classificar cada nova mensagem como continuação, requisito adicional, substituição ou nova tarefa; atualizar campos estruturados em vez de depender apenas do histórico textual.
- Ligar cada etapa do plano a uma capacidade disponível, um efeito observável e um critério de aceite.
- Dividir geração em mudanças pequenas, preferindo uma proposta revisável por unidade lógica em vez de um lote grande que falha inteiro por truncamento.

**Gate:** a mudança de requisito continua a mesma tarefa, preserva as restrições anteriores e invalida qualquer verificação afetada por novas edições.

### Fase 3 — Fechar o ciclo agir–observar–decidir

**Entrega:** o AgentCore controla a tarefa até a verificação ou até um bloqueio baseado em evidência.

- Após cada ação, transformar o retorno do runtime em observação tipada e voltar ao decisor.
- Separar chamada bem-sucedida, alteração observada, critério satisfeito e tarefa concluída.
- Após erro, classificar efeito conhecido/incerto, inspecionar o estado, escolher recuperação diferente quando segura e limitar tentativas repetidas.
- Para construção, revisar diff e relacionar os arquivos alterados a cada requisito; executar check compatível com os manifestos e, quando aplicável, iniciar o app e validar o fluxo solicitado.
- Manter aprovação exatamente associada ao conteúdo e ao efeito revisados.

**Gate:** uma falha induzida é detectada, diagnosticada e corrigida, ou termina como bloqueio honesto sem duplicar efeito nem alegar sucesso.

### Fase 4 — Usar a Brave como evidência, com controle

**Entrega:** pesquisa atual integrada à decisão do núcleo, não uma resposta paralela que encerra a tarefa.

- Acionar pesquisa para fatos voláteis, APIs, dependências, documentação desconhecida e diagnóstico que dependa de comportamento atual; não pesquisar por padrão em toda mensagem.
- Produzir consultas com termos técnicos e domínios oficiais adequados à stack. Usar filtro de relevância estrito ou Goggles quando precisão for mais importante que cobertura.
- Apresentar conteúdo da Brave como evidência não confiável: citar origem, conferir o trecho e nunca tratar instruções da página como autorização para executar ações.
- Limitar consultas e contexto ao necessário. Não enviar código privado, segredos ou conteúdo integral do workspace em consultas.
- Manter armazenamento no corpus desligado. Não usar resultados para treino, ajuste ou avaliação do modelo no plano atual; reavaliar retenção da conversa segundo os direitos do plano.

**Gate:** num conjunto de perguntas com fontes esperadas, as fontes usadas são pertinentes e autorizadas; uma consulta ampla de baixa qualidade não pode sustentar sozinha uma alteração técnica.

### Fase 5 — Validar pela interface e liberar uma beta restrita

**Entrega:** pacote de avaliação repetível, trajetória visível na UI e limitações publicadas.

- Executar tarefas reservadas em pastas temporárias com runtime reiniciado e chave carregada localmente.
- Conferir criação, alteração, continuidade em 3–5 turnos, execução, falha e recuperação; confirmar que nenhum caso passa por receita hardcoded.
- Comparar diff, comportamento real do app e critérios do usuário. Uma suíte verde que não testa o pedido não aprova a tarefa.
- Corrigir primeiro a etapa com maior taxa de falha. Expandir stack e ferramentas apenas depois de a primeira stack passar os gates.

**Gate de beta:** tarefas inéditas de criação e edição concluem na interface com alterações coerentes, checks observados, recuperação demonstrada e zero falsas conclusões na bateria reservada. Publicar taxa de sucesso, falhas restantes, stack coberta e necessidade de revisão humana.

## Ordem do próximo ciclo

1. Completar os dados pendentes da Fase 0 e capturar o baseline inicial já executado.
2. Corrigir a rota de análise e síntese; repetir o caso de leitura.
3. Eliminar ciclos de planejador repetidos e instrumentar o resultado de cada ferramenta.
4. Persistir a tarefa canônica entre turnos e testar a mudança de análise para implementação.
5. Repetir os três casos na interface e verificar os efeitos, checks e a evidência de aceite.
6. Avaliar competências do checkpoint separadamente; se o planejador local continuar sem produzir decisões estruturadas, comparar outro gerador no mesmo contrato de ferramentas e reportar essa troca claramente.

## Fora da primeira versão

- Treino com resultados da Brave ou retenção de respostas da API no acervo sem direito expresso.
- Autonomia irrestrita, edição sem aprovação, execução de comandos arbitrários e tarefas destrutivas.
- Suporte anunciado a stacks sem avaliação específica.
- Adição de ferramentas, memória ou animações como substitutos de sucesso verificado.

## Referências de implementação

- [AgentCore e limites atuais](../agent-core/README.md)
- [Registro e política de capacidades](../agent-core/src/capability-registry.ts)
- [Requisitos e aceite](../agent-core/src/requirements.ts)
- [Executor do plano](../agent-core/src/plan-executor.ts)
- [Runtime e Brave LLM Context](../runtime/src/main.rs)
- [Contrato operacional atual](../Documentacoes/FLUXO_AGENTE.md)
- [Plano detalhado de reestruturação](PLANO_REESTRUTURACAO_NUCLEO_AGENTE.md)
- [Plano do agente programador funcional](PLANO_AGENTE_PROGRAMADOR_FUNCIONAL.md)
- [Plano de APIs e skills](PLANO_PORTFOLIO_APIS_E_SKILLS.md)
