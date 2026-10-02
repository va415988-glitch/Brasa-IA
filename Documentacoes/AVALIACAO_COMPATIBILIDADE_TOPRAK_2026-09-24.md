# Auditoria do dataset local de uso de ferramentas

## Decisão

O trabalho não depende de `microsoft/ProgramDistill`. Para avançar sem esse repositório, esta auditoria usa o dataset já disponível localmente: `Toprak1yu/agent-tool-use-trajectories`.

O material permanece em quarentena. Esta auditoria não executa chamadas do dataset, não autoriza treino e não trata os nomes externos como aliases seguros para ferramentas locais.

## Evidência local

- Manifesto de origem: `corpus/quarantine/huggingface/4d26ebfd6ae8fec8.json`.
- Dados preparados: `corpus/quarantine/huggingface/4d26ebfd6ae8fec8/prepared.jsonl`.
- 10.000 registros vêm do arquivo Parquet; 9 registros adicionais são trechos do README.
- Licença informada no manifesto: Apache-2.0; a política do projeto ainda exige revisão antes de treino.
- Os 10.000 registros de trajetória contêm 19.538 chamadas de ferramenta.
- 6.170 trajetórias contêm uma nova chamada depois de uma resposta de erro da ferramenta.
- Nenhuma chamada usa exatamente o nome de uma das 34 ferramentas locais.
- Todos os 10.000 registros de trajetória foram analisados; não houve erro de parsing.

## Compatibilidade observada

| Ferramenta externa | Chamadas | Candidatas locais | Compatibilidade | Limite observado |
| --- | ---: | --- | --- | --- |
| `tool_exec` | 15.045 | `terminal_run` | Perfil restrito | A origem permite execução genérica; `terminal_run` aceita apenas operações enumeradas e exige aprovação. |
| `tool_gather` | 2.993 | `list_files`, `list_tree`, `read_file`, `search_files` | Ambígua | O nome não especifica qual operação de leitura atende ao pedido. |
| `analyze_code_vulnerabilities` | 375 | `inspect_code` | Parcial | `inspect_code` enumera símbolos e imports; não detecta vulnerabilidades. |
| `execute_query` | 375 | — | Sem equivalente | Não há ferramenta local de consulta genérica a banco de dados. |
| `execute_bash_command` | 375 | `terminal_run` | Perfil restrito | Comandos Bash livres não correspondem ao perfil fixo local. |
| `search` | 375 | `search_files`, `search_web`, `research_web` | Ambígua | O nome não informa se a busca é no workspace ou na web. |

## Como reproduzir

Na raiz do projeto:

```bash
python3 scripts/audit_tool_use_dataset_compatibility.py
```

Para salvar também uma cópia JSON do relatório:

```bash
python3 scripts/audit_tool_use_dataset_compatibility.py \
  --output /tmp/toprak_tool_compatibility.json
```

O auditor lê apenas os arquivos locais e os contratos em `contracts/`. Ele não acessa a rede, não baixa o ProgramDistill e não chama ferramentas do agente.

## Uso recomendado

O dataset serve como fonte de padrões para desenhar avaliações de seleção de ferramentas, recuperação após erro e decisão de pedir esclarecimento. Os nomes e argumentos externos não devem ser enviados diretamente ao AgentCore: 0% das chamadas têm correspondência nominal exata e várias capacidades externas são mais amplas que os contratos locais.

O caso `toprak-missing-file-recovery` em `config/agent_tool_eval_top5.json` continua sendo uma adaptação humana da ideia de recuperação. Novos casos devem continuar explicitando o comportamento local esperado, em vez de reproduzir chamadas externas automaticamente.
