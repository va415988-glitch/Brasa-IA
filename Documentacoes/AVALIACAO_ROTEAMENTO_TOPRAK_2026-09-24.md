# Avaliação local do roteamento de ferramentas

## Escopo

Esta etapa adapta padrões encontrados em `Toprak1yu/agent-tool-use-trajectories` para os contratos disponíveis neste agente. Ela mede apenas a decisão do `AgentPlanner`: não chama ferramentas, não inicia serviços e não comprova a execução ponta a ponta do AgentCore. O dataset continua em quarentena e não foi usado para treino. `microsoft/ProgramDistill` não é dependência.

## Ajustes no planejador

- Caminhos entre crases são reconhecidos ao escolher `read_file` e ao extrair argumentos.
- A política por extensão agora fica centralizada em `python/document_reading.py`; o planejador e o ciclo do agente concordam quando usar `read_file` ou `extract_document_text`. Configurações YAML são leitura direta; PDF, Office, RTF, notebooks e e-mail passam pelo extrator especializado.
- Uma busca local preserva o termo explicitamente delimitado entre crases e separa o diretório indicado do texto restante do pedido.
- Verbos genéricos como “execute” ou “rode” não bastam para acionar verificações de projeto; o pedido precisa indicar testes ou verificações compatíveis.
- Comandos livres de terminal não caem em ferramentas sem relação. O planejador só reconhece perfis fechados, como `git status --short`, e deixa comandos fora desses perfis sem rota automática.
- Candidatos com pontuação inferior a `1.0` são descartados para que sinais fracos de descrição ou aprendizado não iniciem uma ferramenta por conta própria.
- Se uma leitura explícita falhar com erro confirmado de arquivo ausente, o agente lista uma única vez a pasta indicada. Só lê um substituto se houver um único arquivo no mesmo diretório cujo nome tenha correspondência clara com o assunto; listagem incompleta, empate ou ausência de candidato encerra como pendente, sem adivinhar.
- A bateria online `config/agent_tool_eval_top5.json` agora também verifica quais ferramentas foram chamadas nos casos de inventário, leitura, recuperação e conversa sem ferramentas.

## Casos avaliados

A bateria `config/agent_tool_routing_eval.json` contém seis adaptações com execução esperada no AgentCore e um caso adicional somente de planejador. Os exemplos cobrem leitura e busca local, inspeção de código com limites explícitos, recuperação de caminho ausente, consulta SQL sem ferramenta de banco de dados, Bash fora do perfil permitido e um perfil fixo de `git status`.

Comando de reprodução, na raiz do projeto:

```bash
python3 scripts/evaluate_tool_routing_policy.py
```

Resultado observado em 2026-09-24: **7/7 decisões do planejador aprovadas**. Nenhuma ferramenta foi executada por essa avaliação.

Testes focados no ambiente virtual do projeto:

- `test_model_server_agentic.py`: 39 testes passaram, incluindo a recuperação limitada do arquivo ausente e a recusa a escolher um arquivo sem relação.
- `test_terminal_planner.py`: 2 testes passaram.
- `test_capability_catalog.py`: 12 testes passaram. A asserção da quantidade foi ajustada para comparar o catálogo com os contratos realmente registrados, em vez de congelar o valor histórico `21` quando o catálogo atual já contém 34 ferramentas.

O relatório JSON do avaliador isolado fica em `/tmp/agent-tool-routing-local.json`.

## Próxima validação necessária

Para medir execução real, abra um terminal na raiz do projeto, execute `./start.sh` e mantenha-o aberto. Em outro terminal, rode `python3 tests/benchmark_agent_tool_eval_top5.py`. O runner usa `http://127.0.0.1:3000/api/v1/agent/pursue`, rota do runtime que encaminha a tarefa ao AgentCore na porta 3200; se a conexão for recusada, encerra cedo e informa que o servidor precisa iniciar. O runner registra ferramentas iniciadas/concluídas e o manifesto verifica as esperadas por caso. A execução integrada não foi feita nesta etapa porque não havia servidor/AgentCore ativo no ambiente.

O resultado 7/7 é evidência de que as regras locais escolhem as rotas previstas nestes exemplos. Não é uma medida geral de inteligência, nem prova que argumentos, permissões, recuperação e respostas finais funcionem corretamente no ciclo completo.
