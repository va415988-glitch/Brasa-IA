# Plano: portfólio de APIs locais e skills com roteamento automático

**Status:** Fases 0–2 implementadas; catálogo registra 21 capacidades de ferramenta e 8 APIs de serviço, incluindo diálogo híbrido  
**Princípio:** operar localmente por padrão; rede externa é uma fonte opcional, nunca requisito para o ciclo essencial.

## 1. Objetivo

Dar ao agente um catálogo coerente de capacidades locais e um roteador que escolha, para cada tarefa, as skills e APIs mais adequadas. O usuário descreve o resultado; o agente seleciona uma especialidade, monta um plano, executa ferramentas autorizadas e verifica o resultado.

“Portfólio de APIs” significa uma camada local que expõe capacidades sobre o ambiente instalado — workspace, terminal controlado, linguagens, documentos, conhecimento e modelos locais. Não significa clonar serviços externos nem criar dependência de contas, chaves ou endpoints remotos.

## 2. Base que já existe

- `contracts/*.json` descreve ferramentas com argumentos, risco e aprovação; `python/tool_registry.py` carrega e valida esses contratos.
- O runtime Rust mantém os executores reais; `agent-core/src/contracts.ts` também declara um subconjunto de ferramentas. Há listas duplicadas entre as três camadas, então adicionar uma capacidade ainda exige coordenação manual.
- `python/agent_planner.py` escolhe ferramentas por aliases, regras e pontuação; o índice aprendido altera a classificação apenas como sinal secundário.
- `python/portfolio.py` organiza trilhas de tecnologias para aprendizagem. Agora o catálogo também registra os endpoints locais de pesquisa, jobs de aprendizado, estado autônomo e busca no acervo.
- `python/agent_state.py`, `python/competency.py` e `python/skill_lab.py` já registram evidência, prática e competências. Falta um manifesto de skills executáveis, seus requisitos e o roteamento delas por intenção.
- Os fluxos Python e TypeScript já publicam estados no feed `agent-event/v2` após a melhoria do workflow. Esse mesmo feed deve mostrar seleção de skill, API escolhida, motivo e verificação.

## 3. Modelo proposto

Separar três conceitos que hoje podem ser confundidos:

1. **Skill:** procedimento especializado e verificável, como “investigar falha de teste”, “criar interface acessível” ou “resumir contrato”. Contém instruções curtas, pré-condições, critérios de qualidade e competências requeridas.
2. **API/capacidade:** operação tipada que uma skill pode solicitar, como ler arquivo, executar perfil de teste, extrair texto de PDF ou buscar no índice local.
3. **Provider:** implementação concreta da capacidade, priorizando runtime local, binário instalado ou modelo local. Providers remotos só entram como alternativa explicitamente habilitada.

A skill referencia IDs de capacidades, não nomes de executores internos. O roteador seleciona a skill; o broker resolve cada capacidade para um provider disponível e valida o contrato antes de executar.

```mermaid
flowchart LR
    A[Pedido e contexto] --> B[Roteador de skills]
    B --> C[Skill e critérios de aceite]
    C --> D[Catálogo de capacidades]
    D --> E[Provider local disponível]
    E --> F[Executor com contrato e política]
    F --> G[Resultado e evidência]
    G --> H[Verificação e trace]
    H --> I[Aprendizado e avaliações]
```

## 4. Contratos canônicos

### Capacidade/API

Cada capacidade deve declarar ao menos:

- `id`, `version`, nome e descrição orientada ao resultado;
- `input_schema` e `output_schema` tipados;
- `domain_tags` e `capabilities`;
- providers suportados, requisitos locais e teste de disponibilidade;
- efeitos colaterais, nível de risco, aprovação, timeout, limites e idempotência;
- política de rede: `offline`, `local-network` ou `external-optional`;
- evidência produzida e método de verificação;
- política de repetição e compensação/rollback quando aplicável.

### Skill

Cada skill deve declarar:

- `id`, versão, objetivo e sinais de intenção;
- domínios, entradas e pré-condições;
- capacidades/APIs requeridas e alternativas permitidas;
- sequência ou política de decisão, sem embutir credenciais nem shell livre;
- riscos, aprovações necessárias e condições para pedir esclarecimento;
- critérios de aceite e verificador;
- nível de competência e evidências que sustentam a skill;
- conjunto de casos de avaliação, casos negativos e casos de transferência.

Skills são pacotes de procedimento e avaliação, não apenas prompts. Conteúdo aprendido ou recuperado é dado não confiável e não pode sobrepor contratos, aprovações ou limites do executor.

## 5. Roteamento automático

1. **Entender a tarefa:** extrair resultado esperado, domínio, restrições, arquivos, ambiente e riscos.
2. **Recuperar skills candidatas:** aliases e metadados garantem cobertura; busca semântica local melhora recall sem depender de rede.
3. **Checar viabilidade:** filtrar skills cujas APIs/providers não estão instalados ou disponíveis. Preferir caminhos offline.
4. **Ranquear:** combinar correspondência de intenção, contexto do workspace, competências comprovadas, taxa de sucesso verificado e custo/latência. Não permitir que uma métrica histórica ignore risco ou pré-condições.
5. **Escolher ou esclarecer:** selecionar uma skill com confiança suficiente; combinar skills quando a tarefa for composta; perguntar quando a ambiguidade muda o resultado ou a escolha de APIs.
6. **Executar:** gerar plano sob um contrato de tarefa, validar cada chamada e pedir aprovação antes de ações que alterem o workspace ou tenham efeitos externos.
7. **Verificar e aprender:** comparar evidência com critérios de aceite; registrar a decisão, resultado, falhas, recuperação e feedback para avaliação posterior.

O roteador deve expor `skill_candidates`, `selected_skills`, `capability_candidates`, `selected_provider`, confiança, motivo e alternativas consideradas como eventos do workflow. Dados sensíveis e argumentos completos não entram no feed por padrão.

## 6. Primeiro catálogo local

Começar por capacidades que ampliam o ciclo de programação e reaproveitam o runtime atual:

- **Workspace e código:** inspeção, busca, leitura paginada, edição por diff, criação e inspeção de dependências.
- **Terminal controlado:** perfis allowlist para testes, build, lint, typecheck e Git; nunca shell arbitrário como primeiro provider.
- **Verificação:** detecção de ferramentas instaladas, execução isolada, timeout, limite de saída, resultado estruturado e vínculo com o diff.
- **Conhecimento local:** busca em contratos, documentação espelhada, corpora e exemplos avaliados; indexação sem chamada remota durante a tarefa.
- **Documentos e mídia:** leitura e extração local de texto, OCR e metadados; marcar claramente quando inferência visual avançada não está disponível.
- **Modelo local:** geração, embeddings e classificação por endpoints locais; provider detecta modelo ausente e explica a capacidade degradada.

Pesquisa na web pode continuar como provider `external-optional`. Com rede desligada, o agente usa o índice local e informa limites de atualização, sem bloquear tarefas de código e conhecimento já instalado.

## 7. Skills candidatas para a primeira versão

- `workspace-inspection`: entender stack, estrutura, entradas e verificadores.
- `implementation`: decompor requisito e realizar mudança restrita.
- `test-debugging`: interpretar falha, isolar causa, propor correção e repetir verificação.
- `ui-accessibility`: implementar interface com hierarquia, teclado e acessibilidade verificável.
- `document-analysis`: extrair conteúdo local, responder com referências e sinalizar lacunas.
- `technical-writing`: produzir documentação adaptada a público e formato.
- `research-offline-first`: procurar primeiro no acervo local; só usar rede se habilitada/solicitada.
- `external-research`: consultar fontes da web apenas quando a tarefa pedir rede ou dados atuais.
- `task-planning`: decompor trabalho não codificado, dependências, riscos e critérios de conclusão.

Skills compostas devem chamar outras skills via contrato explícito, para que o trace explique cada seleção e o verificador certo seja aplicado.

## 8. Aprendizado e métricas

Não atualizar pesos do modelo automaticamente com toda execução. Primeiro, construir traces avaliáveis e um ciclo de promoção controlado:

- medir roteamento `top-1/top-3`, seleções inválidas e quantas vezes o agente precisou pedir esclarecimento;
- medir conclusão verificada, regressões, replanejamentos úteis, aprovações canceladas e tempo por domínio;
- testar em casos conhecidos, casos adversariais e tarefas novas de transferência;
- separar sucesso do executor, qualidade da resposta e satisfação/correção do usuário;
- promover novas regras, exemplos ou versões de skill somente depois de passar conjunto de regressão;
- manter histórico de versão, proveniência dos exemplos e possibilidade de reverter.

Metas de produto para o piloto: zero dependência de serviço remoto em uma tarefa offline; nenhuma chamada fora dos contratos; toda conclusão de escrita acompanhada de verificação ou marcada explicitamente como não verificada; roteamento e fallback visíveis na timeline.

## 9. Plano de implementação

### Fase 0 — inventário e baseline (concluída)

Mapear contratos, executores Rust, ferramentas Python, portas TypeScript, dependências locais e chamadas de rede. Criar exemplos representativos por domínio e medir o roteador atual. Resultado: matriz `capacidade × provider × disponibilidade × risco` e relatório de duplicações em `planning/INVENTARIO_CAPACIDADES_LOCAIS.md`.

### Fase 1 — catálogo canônico de capacidades (v1 implementada)

O schema versionado combina os contratos JSON existentes com IDs e políticas em `capabilities/metadata.json` e lista APIs HTTP de serviço em `service_apis`. O loader valida cobertura integral dos 21 contratos e valida os endpoints locais registrados. A API de diálogo e o AgentCore usam o checkpoint próprio pelo worker local, sem exigir Ollama. Pesquisa web e início de aprendizado declaram rede externa opcional; consulta do acervo e do estado dos jobs permanece offline. A geração de bindings Rust/TypeScript e probes reais de disponibilidade permanecem pendentes.

### Fase 2 — manifesto de skills e integração com o planejador (v1 implementada)

Skills versionadas em `skills/manifest.json` apontam para capacidades de ferramenta ou APIs HTTP locais. As skills `knowledge-learning` e `autonomous-learning-cycle` conectam solicitações explícitas aos endpoints `/api/learn` e `/api/v1/learning/autonomous/tick`. Fontes passam pela avaliação de evidência e laboratório prático existentes; consultas de estado permanecem offline. `/api/v1/skills/route` aplica gatilhos determinísticos e contexto do workspace, valida capacidades contra o catálogo e devolve uma próxima ação tipada com schemas, aprovações e política de rede. `/rotear <tarefa>` mostra esse plano; as chamadas continuam a ser executadas pelos fluxos locais existentes. `ModelService.plan_tool` usa as APIs selecionadas como allowlist, sem exigir Ollama ou outro modelo externo.

O manifesto também inclui `workspace-status`, `change-review`, `environment-diagnostics` e `local-knowledge-retrieval`. O contexto `workspace_selected` desambigua perguntas sobre o estado do projeto; tópicos políticos explícitos ficam fora dessa rota. A pesquisa na internet segue como capacidade externa opcional, separada das skills offline.

- `workspace-status` consulta o projeto selecionado e pode sugerir estado do processo, diff e verificações; sem workspace, pede seleção.
- `change-review` começa pelo diff Git somente leitura e encaminha verificações apenas quando solicitadas.
- `environment-diagnostics` começa pela inspeção local e usa diagnósticos/verificações quando há evidência suficiente.
- `local-knowledge-retrieval` consulta o índice local e não inclui capacidades externas.

Próximas skills candidatas: `release-readiness` pode combinar diff, inspeção e checks sem publicar; `api-integration` pode combinar documentação local e pesquisa web explícita; análise de datasets deve esperar um leitor tabular com schemas e limites próprios. Cada uma depende de regras de entrada e critérios de aceite próprios antes de entrar no manifesto.

Casos sem skill clara preservam o caminho legado; rota confiante sem argumentos válidos não executa chamada alternativa. A escolha explícita de workspace integra a skill de inspeção. Regressões cobrem pesquisa offline, pesquisa externa solicitada, interface e depuração.

### Fase 3 — broker local e providers

Resolver disponibilidade local e executar capacidades por contratos existentes. Acrescentar providers progressivamente, começando por workspace, Git, verificações e conhecimento local. Cada provider tem health check, limites, isolamento e evidência estruturada.

### Fase 4 — associação de resultados, aprovações e métricas

A primeira integração de seleção e feed está ativa no ciclo de tarefas persistentes. Próximo passo: associar resultados e verificações às skills, medir seleção e sucesso verificado por versão, e consolidar aprovação/pausa/retomada no mesmo ciclo.

### Fase 5 — avaliação e expansão de domínios

Rodar regressões por skill, adicionar domínios não programáticos com seus verificadores próprios e promover apenas versões que melhorem a taxa de sucesso sem degradar segurança, operação offline ou qualidade.

## 10. Critério de conclusão do piloto

O piloto fica pronto quando um pedido de implementação, uma tarefa de depuração e uma tarefa documental:

- escolhem a skill e as APIs esperadas em modo offline;
- explicam a seleção e alternativas no feed de eventos;
- validam argumentos e recusam provider indisponível sem recorrer silenciosamente à rede;
- pausam para autorização quando necessário;
- verificam critérios de aceite e registram evidência compacta;
- passam uma suíte de regressão de roteamento e execução.

## 11. Estado e próxima ação

A Fase 0 está registrada em [INVENTARIO_CAPACIDADES_LOCAIS.md](INVENTARIO_CAPACIDADES_LOCAIS.md): 21 contratos válidos, 16 capacidades offline, 2 com rede herdada da execução do projeto e 3 com rede externa opcional. O catálogo falha fechado quando os conjuntos divergem. O roteador já restringe a seleção de ferramenta no planejador para tarefas com rota clara, e o feed mostra skill/API junto ao plano.

Próxima ação: medir a API de diálogo em conversas reais, propostas e confirmações curtas; depois levar o catálogo e a skill de aprendizado ao caminho direto do AgentCore TypeScript e gerar checks de consistência Rust/TypeScript. A especificação inicial do diálogo está em [API_DIALOGO_HIBRIDA.md](../Documentacoes/API_DIALOGO_HIBRIDA.md).
