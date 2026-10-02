# Inventário inicial de capacidades locais

**Snapshot:** 2026-09-22  
**Catálogo:** 21 contratos de ferramenta e 6 APIs HTTP locais de serviço.
**Política das ferramentas:** 16 offline, 2 herdam o comportamento do projeto executado e 3 podem acessar a rede quando solicitados. As APIs de serviço separam consultas locais dos fluxos de pesquisa/aprendizado com rede opcional.

## Contratos executáveis

| ID da capacidade | Ferramenta | Domínios | Rede | Aprovação |
|---|---|---|---|---|
| `workspace.file.repair.apply` | `apply_repair` | programming, workspace | `offline` | sim |
| `research.citations.create` | `cite_sources` | research, writing | `offline` | não |
| `workspace.directory.create` | `create_directory` | programming, workspace | `offline` | não |
| `workspace.file.create` | `create_file` | programming, workspace, writing | `offline` | não |
| `ui.prototype.create` | `create_web_page` | programming, design | `offline` | sim |
| `project.failure.diagnose` | `diagnose_project` | programming, debugging | `offline` | não |
| `workspace.file.edit` | `edit_file` | programming, workspace, writing | `offline` | não |
| `document.text.extract` | `extract_document_text` | documents, research | `offline` | não |
| `media.inspect` | `inspect_media` | media, documents | `offline` | não |
| `project.inspect` | `inspect_project` | programming, workspace | `offline` | não |
| `workspace.files.list` | `list_files` | programming, workspace | `offline` | não |
| `research.sources.list` | `list_sources` | research | `offline` | não |
| `research.page.open` | `open_page` | research | `external-optional` | não |
| `project.verify` | `project_checks` | programming, testing | `workspace-inherited` | não |
| `project.repair.propose` | `propose_repair` | programming, debugging | `offline` | não |
| `workspace.file.read` | `read_file` | programming, workspace, documents | `offline` | não |
| `research.web.deep` | `research_web` | research | `external-optional` | não |
| `workspace.code.search` | `search_files` | programming, workspace, debugging | `offline` | não |
| `research.web.search` | `search_web` | research | `external-optional` | não |
| `workspace.select` | `set_workspace` | programming, workspace | `offline` | sim |
| `process.profile.run` | `terminal_run` | programming, testing, workspace | `workspace-inherited` | sim |

## Rotas e registries atuais

- `contracts/*.json` fornece schemas de argumentos/resultados, risco, efeitos, aprovação e timeout; são carregados pelo `python/tool_registry.py`.
- `capabilities/metadata.json` associa cada contrato a um ID estável, domínios, provider local e política de rede. O `CapabilityCatalog` falha ao iniciar se houver ferramenta sem metadata, metadata órfã ou duplicação.
- `skills/manifest.json` referencia essas capacidades; o ciclo persistente Python aplica a lista permitida da rota ao planejador e registra skill/API escolhidas no evento `ready`. `/api/v1/skills/route` resolve skills com regras determinísticas, contexto do workspace e schemas locais, devolvendo `next_action`; a execução continua no AgentCore e passa pelas travas de contrato.
- O caminho direto do AgentCore TypeScript (`/agente`) ainda não aplica esse roteador; ele é uma das próximas integrações a avaliar.
- O executor real está no runtime Rust. `AgentPlanner` em Python ainda usa aliases/regras próprios; o núcleo TypeScript declara um subset tipado de ferramentas. O catálogo não elimina ainda essas listas executoras.
- Endpoints locais de pesquisa/aprendizado e busca no acervo agora estão registrados em `service_apis` dentro de `capabilities/metadata.json`; não são ferramentas `contracts/*.json` e permanecem separados no snapshot.
- As skills `knowledge-learning` e `autonomous-learning-cycle` conectam pedidos explícitos aos jobs `/api/learn` e `/api/v1/learning/autonomous/tick`. A UI exibe skill/API e acompanha progresso; consultar estado não libera um tick ou pesquisa externa.

## Lacunas para fechar

1. Gerar bindings/listas para Rust e TypeScript a partir do catálogo, ou ao menos falhar no CI quando nomes ou schemas divergirem.
2. Substituir disponibilidade `contract_valid` por probes concretos de provider e versão instalada; a simulação atual não executa nem verifica ferramentas do host.
3. Expandir o schema para APIs de contexto, modelos locais e adapters multimodais, com contratos próprios de disponibilidade e evidência.
4. Calibrar confidence/margin do roteador com um conjunto rotulado; os scores atuais são regras de partida, não probabilidades avaliadas.
5. Levar o mesmo contrato de roteamento ao caminho direto do AgentCore TypeScript e associar resultados/verificações às skills nos traces.
