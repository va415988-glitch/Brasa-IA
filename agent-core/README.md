# Agent Core

Camada TypeScript do agente local próprio.

Este pacote não carrega nem depende de um modelo externo. Ele define o ciclo
operacional e conversa com o restante do sistema por interfaces:

- Rust implementa WorkspacePort com seleção, permissões, escrita e verificação;
- Python implementa ResearchPort e LearningPort para pesquisa, treino e avaliação;
- TypeScript coordena objetivo, observação, pesquisa, plano, ação e verificação.

O núcleo não acessa o sistema operacional diretamente. Isso mantém as decisões
testáveis e deixa efeitos colaterais sob controle do runtime Rust.

Todas as mensagens sem anexos entram pelo endpoint `/api/v1/agent/pursue` e
passam pelo `AgentCore`, com ou sem workspace selecionado. Conversa comum pode responder sem workspace e, quando o planejador identificar
uma lacuna, consultar fontes web. Com workspace explícito, também pode consultar
arquivos. Pesquisa e estudo sem workspace só podem usar pesquisa web. Inspeção, testes e alterações continuam exigindo workspace explícito.
Mensagens com anexos ainda usam o fluxo legado de `/api/chat` e são a próxima
frente de migração.

RuntimeHttpPorts adapta os endpoints locais /api/v1/tools/call e
/api/v1/research. `LocalContextHttp` recupera contexto de
`127.0.0.1:3101/v1/agent/context`; `LocalPlannerHttp` envia a preparação tipada
para `127.0.0.1:3101/v1/agent/plan` (ou `/stream`). O planejador apenas propõe
uma ferramenta. Zod valida o envelope, a allowlist e os argumentos antes que o
PlanExecutor possa executar a proposta. O ciclo observa o resultado e consulta
o planejador outra vez, com limite de 12 ações e contexto ajustado ao orçamento
de 32k. Caminhos são limitados ao workspace pelo TypeScript e pelo runtime
Rust. Escritas, verificações de projeto e perfis de terminal exigem aprovação.
Uma aprovação retoma a proposta exata que ficou pendente e autoriza uma única
ação.

## APIs de entendimento e retomada

- `POST /api/v1/agent/understand` aceita `agent-request/v2` em `snake_case` e
  devolve `agent-understanding/v1` com interpretação, personalidade, premissas,
  perguntas, critérios e capacidades. A prévia não grava memória/eventos nem
  altera arquivos. Uma preparação pronta pode ser reutilizada no `pursue` por
  até 15 minutos quando pedido, conversa, workspace, histórico, anexos e
  preferências coincidirem.
- `POST /api/v1/agent/pursue` aceita o contrato v2 e continua aceitando os nomes
  camelCase legados. O relatório agora identifica `agent-report/v2` e inclui a
  síntese cognitiva; o AgentCore mantém política, aprovação e execução.
- `POST /api/v1/agent/tasks/{task_id}/resume` aceita `agent-resume/v1` para
  responder perguntas ou aprovar/rejeitar a ação pendente. Repetir o mesmo
  `request_id` e conteúdo devolve a resposta já registrada, sem executar a
  ferramenta novamente.

- `POST /route` (no runtime: `/api/v1/agent/route`) aceita
  `task-route-request/v1` e devolve `task-route/v1`: objetivo, cérebro
  (`conversation`, `creative`, `engineering`, `interface`, `research`,
  `analysis`, `computation`, `learning` ou `operations`), personalidade,
  ferramentas permitidas e fontes de pesquisa (`web`, `package-registry`,
  `wikipedia`, `github`). É uma prévia pura: não lê o workspace nem grava
  estado. A ação inicial de pesquisa do AgentCore usa a mesma decisão.
- `POST /requirements` (no runtime: `/api/v1/engineering/requirements/extract`)
  devolve `agent-requirements/v1` com restrições, critérios de aceite, lacunas
  e perguntas, sem executar nada.

Os validadores públicos ficam em `server-contract.ts`, a preparação e o resumo
em `AgentCore.understand`, e o contrato de recuperação/planejamento local em
`context.ts` e `planner.ts`. `/generate` permanece como adaptador legado.

Os nomes e argumentos das ferramentas do runtime são contratos TypeScript
fechados. O adapter valida caminhos relativos antes de chamar create_file;
caminhos absolutos, `..`, segmentos vazios e bytes nulos são rejeitados antes
de alcançar o runtime. A autoridade final continua sendo o runtime Rust.

## Memória operacional

LocalTaskMemory guarda objetivos, decisões, observações e evidências com
confiança, origem e timestamps. A memória atualiza entradas pela combinação
tipo + chave, pesquisa termos relevantes, aplica limite de retenção e exporta
snapshots versionados. MemoryPort permite trocar essa implementação em memória
por persistência no runtime Rust ou no serviço Python.

Antes de persistir, a memória valida chave, conteúdo, tamanho, confiança e
origem; possíveis credenciais e chaves privadas são rejeitadas. Snapshots
também passam pela mesma validação ao serem restaurados.

## Cérebro TypeScript

O núcleo agora possui uma espinha cognitiva executável:

- CognitiveStateMachine impede transições impossíveis entre observar,
  esclarecer, planejar, executar, verificar, recuperar e entregar;
- analyzeRequirements transforma um pedido em ambiguidades, perguntas,
  restrições e critérios de aceite;
- CognitiveBrain registra a decisão de perguntar ou planejar;
- JsonlBrainEventLog cria um rastro persistente de eventos;
- PlanExecutor executa ferramentas registradas com risco, aprovação,
  timeout, tentativas limitadas e evidência;
- o AgentCore mantém um gate de requisitos opcional para fluxos que realmente
  precisam interromper. O servidor registra lacunas e segue com padrões seguros
  para detalhes não críticos; escritas continuam aguardando aprovação explícita.
- O orçamento de contexto é tipado e determinístico: trabalha com alvo de
  32.768 tokens, reserva 4.096 para geração e registra mensagens descartadas
  ou truncadas. Assim, “suporta 32k” vira política operacional verificável,
  não apenas um número no checkpoint.
- Uma única máquina de estados acompanha todas as ações de uma tarefa. Cada
  resultado verificado retorna o núcleo a planejamento para decidir a próxima
  etapa; somente a entrega encerra a tarefa. Uma aprovação pendente preserva o
  mesmo `taskId`, a proposta exata, a preparação e o snapshot cognitivo. As
  transições e resumos de resultado são acrescentados ao log por tarefa.

O uso de Python continua permitido nos adaptadores de treino e avaliação. Ele
não pode atravessar o núcleo sem contrato TypeScript.

## RAG web

WebResearchService implementa a porta de pesquisa sem acoplar o núcleo a um
provedor específico. DuckDuckGoSearchProvider pesquisa resultados, abre as
páginas encontradas, remove conteúdo de apresentação e devolve evidências com
título, URL, identificador e trecho limpo.

O provedor tem limites de resultados, tamanho de trecho e tempo por página.
Falha em uma fonte não descarta as demais. O conteúdo externo permanece
evidência não confiável; ele não recebe permissão para alterar o workspace ou
executar uma ação.

## Testes

    npm test
    npm run check

## Limites atuais

TypeScript fornece o controle do fluxo, contratos fechados, validação de
argumentos, permissões, transições de estado, aprovação, timeout, histórico e
eventos. Ele não aumenta sozinho a capacidade do modelo. A qualidade das ações
ainda depende do planejador Python/modelo local e de seus dados de treinamento.
O planejador converte a inspeção em um lote de criações/edições, valida caminhos
e trechos observados e encaminha a proposta para aprovação; uma proposta inválida
ou ferramenta fora da allowlist é bloqueada sem alterar arquivos.

O AgentCore limita cada execução a 12 ciclos. Ele exige mudança observável para
declarar uma tarefa de construção concluída e requer uma verificação aprovada
depois da alteração. Exceções e timeouts não são repetidos automaticamente
porque o efeito da chamada pode ser incerto. A UI recebe atualizações de estado
à medida que o PlanExecutor avança.

## Consultas durante a conversa

Com planejador e porta de ferramentas conectados, `conversation` usa o ciclo de
planejar, consultar e observar. O catálogo combina política e disponibilidade:
consultas web e, somente com workspace explícito, leitura local. Escritas e
processos continuam proibidos nessa rota, mesmo que o modelo os proponha. A
seleção do workspace só acontece quando uma leitura local é executada; uma
resposta direta não dispara inspeção. O limite é de seis ações (ou o orçamento
menor configurado); esgotá-lo sem síntese mantém a tarefa bloqueada.

A razão de cada consulta deve identificar a informação que falta. O resultado
volta ao planejador, que pode responder ou escolher outra consulta. Isso permite
buscar informação sem exigir que o usuário diga “pesquise”. Testes com portas
simuladas verificam o ciclo, o catálogo, as restrições e a ausência de falso
sucesso no limite; não medem compreensão ou competência do checkpoint.

O backend Python, quando recebe
`agent-cognition/v1` com catálogo, usa `cognitive_dialogue.py` para propor
`answer`, `consult` ou `blocked`. A proposta identifica lacuna e referências de
observações; nomes, argumentos, caminhos e evidências são validados antes de
chegar ao executor. Fontes vazias ou falhas não sustentam uma resposta factual.
Consultas idênticas não são executadas novamente no mesmo ciclo. A prévia de
conversa também não inspeciona o workspace antecipadamente.

Falhas do protocolo ficam explícitas e não viram respostas de memória curada.
Saudações e expressões aritméticas simples preservam suas rotas determinísticas;
essas rotas não contam como ganho neural. A API legada sem contrato cognitivo
continua disponível. A integração pode ser verificada separadamente com:

    node --experimental-strip-types --test tests/cognitive-python.integration.ts

O teste usa contratos reais e geração/ferramenta simuladas; não prova competência
do modelo. Em 30/09/2026, o checkpoint ativo passou em **0/4** casos da avaliação
neural do novo protocolo. Os testes de software aprovados não autorizam afirmar
que os pesos aprenderam a escolher consultas. Consulte o relatório em
`Documentacoes/COGNICAO_OPERACIONAL_2026-09-30.md`.
