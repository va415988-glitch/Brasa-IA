# Integração com Zed

O Zed é o editor prioritário. O runtime agora expõe uma API compatível com
OpenAI em:

```text
http://127.0.0.1:3000/v1/chat/completions
```

Ela aceita `POST` com `model` e `messages` e devolve `chat.completion`, além de
metadados locais como backend e intenção. Nenhum modelo externo é chamado.

## Configuração

1. Execute `./start.sh` na raiz do projeto.
2. Abra as configurações de IA do Zed com `agent: open settings`.
3. Adicione um provedor compatível com OpenAI apontando para
   `http://127.0.0.1:3000/v1`.
4. Use `ia-local-do-zero` como nome do modelo, quando o Zed solicitar.

O formato exato do bloco pode variar conforme a versão do Zed; a configuração
deve ser feita pela seção de provedor compatível com OpenAI ou pelo editor de
configurações. A documentação do Zed descreve o uso de APIs compatíveis com
OpenAI para recursos de IA e previsões de edição
([configurações](https://zed.dev/docs/reference/all-settings),
[edit predictions](https://zed.dev/docs/ai/edit-prediction)).

## Ferramentas no Agent Panel

O servidor MCP local em `mcp_server.py` expõe as ferramentas do runtime ao
Agent Panel. Adicione o bloco de `settings.example.json` às configurações do
Zed, ajustando o caminho absoluto se necessário, e reinicie o Zed. O processo
MCP usa stdio e encaminha chamadas para o runtime em `127.0.0.1`.

As ferramentas incluem leitura, busca, verificações, criação, edição com
backup, pesquisa web e abertura de páginas. A edição continua sujeita ao
contrato do Rust e deve ser confirmada pelo agente do Zed.

## Agente próprio no Zed

O arquivo `acp_server.py` implementa o Agent Client Protocol (ACP) em stdio. O
`acp_launcher.sh` fixa a raiz do projeto e inicia o adaptador com o ambiente
virtual correto. Com o bloco `agent_servers.ia-local-do-zero` de `settings.example.json`, o
Zed inicia o processo como um agente externo chamado **IA Local do Zero**.
Esse agente mantém a conversa no runtime local, sincroniza o workspace da
sessão e envia atualizações `session/update` enquanto processa a mensagem.

O runtime também possui a ferramenta composta `research_web`: ela pesquisa,
remove redirecionamentos do buscador, abre até três fontes dentro do limite de
tempo, limpa scripts e elementos de interface e preserva título, URL,
`source_id` e tempo da operação. A interface oferece essa ação no botão `＋`.
Para gravar o resultado no acervo de forma explícita, use a pesquisa guiada
com a opção `--salvar`; os registros vão para JSONL com categoria, data e
SHA-256, e duplicatas pelo hash são ignoradas.

Pedidos claros de workspace já acionam chamadas de ferramenta no próprio
chat, com progresso visível no painel:

- `Liste os arquivos do workspace`
- `Leia o arquivo runtime/src/main.rs`
- `Busque no código openai_compatible_response`
- `Rode os testes`
- `Crie uma pasta docs/nova`
- `Crie o arquivo notas.md:\nconteúdo do arquivo`
- `Edite o arquivo notas.md`, seguido de blocos `antigo:` e `novo:` com os
  trechos exatos

Leitura, busca e verificações são exibidas como chamadas de ferramenta. Toda
edição passa pelas validações do runtime, mostra um diff ACP, pede permissão
na interface do Zed, exige trecho antigo único e cria backup antes da troca.

Depois de salvar a configuração, abra o seletor de agentes do painel Agent e
escolha **IA Local do Zero**. Se ele não aparecer imediatamente, execute
`agent: open settings` ou reinicie o Zed. Para depurar o processo ACP, use o
comando `dev: open acp logs` do Zed.

O agente ACP não inicia Ollama nem depende de um modelo externo: ele apenas
encaminha a conversa para `127.0.0.1:3000`, respeitando o limite local de 10 s.

## Limites atuais

- a API conversa com o backend local curado e o acervo;
- a conversa nativa do Zed usa o endpoint compatível com OpenAI;
- o MCP expõe ferramentas, e o agente ACP já mostra diffs e pede permissão
  antes de alterar arquivos;
- o runtime mantém allowlist, workspace, backup e limite de 10 segundos;
- a primeira versão ACP já cobre texto, contexto embutido, histórico da sessão,
  cancelamento e feedback incremental; chamadas de ferramentas nativas do Zed
  serão ampliadas na próxima etapa.
