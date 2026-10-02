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
