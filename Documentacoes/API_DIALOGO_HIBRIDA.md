# API de diálogo do checkpoint próprio

## Objetivo

`agent-dialogue/v1` prepara histórico e evidências, chama o checkpoint neural
treinado neste projeto e valida a resposta. O runtime não encaminha conversas a
Ollama, OpenAI-compatible ou outro modelo gerador. A web é uma fonte opcional de
evidências; quem escreve a resposta continua sendo o checkpoint próprio.

## Rotas

### `GET /api/v1/dialogue/providers`

Retorna `agent-dialogue-providers/v1`, com `mode: "own-checkpoint"` e o estado
do checkpoint carregado. Não retorna chaves ou credenciais.

### `POST /api/v1/dialogue/turn`

Recebe `agent-dialogue-request/v1`:

```json
{
  "schema": "agent-dialogue-request/v1",
  "request_id": "turno-123",
  "messages": [
    {"role": "user", "content": "Tenho uma proposta para melhorar a conversa."},
    {"role": "assistant", "content": "Qual é a proposta?"},
    {"role": "user", "content": "Podemos criar uma API própria?"}
  ],
  "evidence": [
    {"source": "https://exemplo.org/documentacao", "text": "Trecho consultado..."}
  ]
}
```

`provider` pode ser omitido ou definido como `local`/`own-checkpoint`; os dois
valores sempre selecionam o checkpoint do projeto. Valores como `external` e
`auto` são rejeitados. O pedido aceita até 80 mensagens, mas o contexto entregue
ao gerador mantém no máximo oito mensagens recentes e até oito evidências. Texto
de páginas externas é dado não confiável, não uma instrução.

A resposta `agent-dialogue-response/v1` registra o backend, a identificação do
checkpoint, o gate de qualidade e as tentativas. Se o checkpoint estiver
indisponível ou a resposta reprovar, a rota retorna erro; ela não troca para
outro modelo gerador. Fallbacks de regras que existam no chat principal são
identificados separadamente e não são apresentados como geração neural.

## Busca web

Quando `IA_LOCAL_BRAVE_SEARCH_API_KEY` está configurada no ambiente do runtime,
`search_web` chama o endpoint LLM Context da Brave Search API. Ele retorna
trechos de páginas já extraídos, títulos, URLs e metadados; `research_web`
reutiliza esses trechos para compor evidências sem baixar as mesmas páginas de
novo. O checkpoint próprio continua responsável por gerar a resposta. O endpoint
Answers da Brave, que gera respostas, não é usado. Sem a chave, a busca usa o
scraper existente do DuckDuckGo. Uma falha da API configurada aparece como erro
e não dispara uma troca silenciosa de provedor.

A requisição envia `Cache-Control: no-cache` (a Brave descreve esse controle
como best effort). Os trechos brutos são removidos dos tool traces e da cópia
persistida do histórico do navegador; a resposta final do assistente continua
salva como parte normal da conversa. Para salvar conteúdo retornado pela Brave
no acervo, é preciso definir `IA_LOCAL_BRAVE_ALLOW_STORAGE=true`, e somente se o
plano contratado conceder direitos explícitos de armazenamento. A chave de busca,
por si só, não concede direitos sobre o conteúdo das páginas. RAG melhora a
informação disponível durante a resposta, mas não atualiza os pesos.

## Verificação manual

```bash
curl http://127.0.0.1:3000/api/v1/dialogue/providers

curl -X POST http://127.0.0.1:3000/api/v1/dialogue/turn \
  -H 'Content-Type: application/json' \
  -d '{"schema":"agent-dialogue-request/v1","messages":[{"role":"user","content":"Você concorda com essa ideia?"}]}'
```
