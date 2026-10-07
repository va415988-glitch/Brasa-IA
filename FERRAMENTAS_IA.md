# Ferramentas da IA Local

Levantamento do catálogo de contratos e dos executores do runtime em 24/09/2026. O agente recebe os contratos de `contracts/`, com identificadores em `capabilities/metadata.json`; o runtime executa as operações em `runtime/src/main.rs`.

## Antes desta atualização: 26 ferramentas catalogadas

| Grupo | Ferramentas |
| --- | --- |
| Workspace e edição (11) | `set_workspace`, `list_files`, `read_file`, `search_files`, `inspect_project`, `create_directory`, `create_file`, `edit_file`, `apply_batch`, `undo_batch`, `create_web_page` |
| Diagnóstico e execução (5) | `diagnose_project`, `propose_repair`, `apply_repair`, `project_checks`, `terminal_run` |
| Processos (3) | `process_start`, `process_status`, `process_stop` |
| Pesquisa e fontes (5) | `search_web`, `research_web`, `open_page`, `list_sources`, `cite_sources` |
| Documentos e mídia (2) | `extract_document_text`, `inspect_media` |

O runtime também executava `inspect_code`, `list_tools` e `create_workspace`, mas faltavam os contratos e os metadados necessários para o agente descobri-las. Elas agora constam do catálogo. `create_workspace` exige aprovação.

## Cinco ferramentas implementadas nesta atualização

| Ferramenta | Uso | Limites |
| --- | --- | --- |
| `path_info` | Existência, tipo, tamanho e permissão de um caminho. | Dentro do workspace; links externos são rejeitados. |
| `find_paths` | Busca nomes com `*` e `?`, sem ler conteúdo. | Até 200 resultados, 20 mil entradas ou 3 segundos. |
| `list_tree` | Mostra a hierarquia de pastas e arquivos. | Profundidade até 6 e até 500 itens. |
| `compare_files` | Compara dois arquivos de texto e aponta a primeira linha diferente. | Arquivos locais de até 128 KiB cada. |
| `git_diff` | Lê o diff Git não preparado. | Sem shell nem diff externo; saída limitada a 12 KiB e tempo de 5 segundos. |

O catálogo passa a ter **34 ferramentas**: 26 anteriores, cinco novas e três que já existiam apenas no runtime. As novas consultas são somente leitura.

## Melhorias em ferramentas anteriores

- `list_files` identifica links simbólicos sem segui-los na listagem e preserva a opção de incluir itens ocultos.
- `search_files` aceita `path` para restringir a busca a uma pasta do workspace.
- O planejador prioriza ferramentas locais para perguntas sobre arquivos, estrutura e diff. A resposta final usa os dados retornados; falhas de ferramenta são informadas como falhas.

## Exemplos de pedidos

- “O arquivo `README.md` existe? Mostre seus metadados.” → `path_info`
- “Encontre arquivos `*.py` no workspace.” → `find_paths`
- “Mostre a árvore de arquivos do projeto.” → `list_tree`
- “Compare os arquivos `a.py` e `b.py`.” → `compare_files`
- “Mostre o git diff.” → `git_diff`
- “Quais ferramentas você possui?” → `list_tools`

## Atualização de 07/10/2026: 41 ferramentas e fontes estruturadas

Cinco análises estáticas de engenharia entraram no catálogo. Todas são somente
leitura, não executam código do workspace e devolvem caminho e linha.

| Ferramenta | API HTTP | Uso |
| --- | --- | --- |
| `code_references` | `POST /api/v1/engineering/code/references` | Definição, imports, chamadas e testes que citam um símbolo. |
| `change_impact` | `POST /api/v1/engineering/code/change-impact` | Dependentes, testes afetados, checks recomendados e risco antes de alterar um arquivo ou símbolo. |
| `discover_tests` | `POST /api/v1/engineering/tests/discover` | Frameworks, arquivos e casos de teste, e fontes sem teste correspondente. |
| `security_scan` | `POST /api/v1/security/code-review` | Segredos (mascarados), injeção, desserialização insegura, TLS desativado, XSS e configuração. |
| `dependency_audit` | `POST /api/v1/engineering/dependencies/audit` | Versões sem fixação, origens fora do registro, lockfiles ausentes e divergências (offline). |

`research_web` e `POST /api/v1/research` aceitam `sources` (`web`,
`package-registry`, `wikipedia`, `github`), `package` (`name` e `ecosystems`:
`npm`, `pypi`, `crates`) e `language`. As fontes estruturadas entram como
páginas citáveis antes da busca web e não consomem o limite `max_results`.

O roteador unificado `POST /api/v1/agent/route` indica, para cada pedido, o
cérebro, a personalidade, as ferramentas permitidas e as fontes de pesquisa.
Detalhes e achados da revisão em
[`Documentacoes/REVISAO_GERAL_2026-10-07.md`](Documentacoes/REVISAO_GERAL_2026-10-07.md).

Exemplos de pedidos:

- “Onde a função `parse_config` é usada?” → `code_references`
- “Qual o impacto de mudar `app/billing.py`?” → `change_impact`
- “Quais testes existem no projeto?” → `discover_tests`
- “Faça uma revisão de segurança do projeto.” → `security_scan`
- “Audite as dependências.” → `dependency_audit`
- “Qual a versão mais nova do React?” → `research_web` com `package-registry`
