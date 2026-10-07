# Revisão geral do Brasa — 07/10/2026

Escopo: APIs, roteamento automático de recursos, pesquisa na internet, cérebro
criativo, cérebro de desenvolvimento, testes e segurança. Foram lidos o runtime
Rust (`runtime/src/main.rs`), o worker Python (`python/model_server.py` e
módulos) e o AgentCore TypeScript (`agent-core/src`); as três baterias de testes
rodaram antes e depois das mudanças; o stack completo (portas 3000, 3101 e
3200) foi iniciado e exercitado por HTTP.

## Parecer

A infraestrutura do Brasa é sólida: contratos versionados, aprovação explícita
para escrita, lotes reversíveis, ledger de execução, eventos correlacionados,
OpenAPI e centenas de testes. O que limita o Brasa hoje **não é falta de API**:
é o gerador neural. O checkpoint ativo tem 6,7 milhões de parâmetros
(2 camadas × 128) e reprova em geração livre (0/24), programação (0/96) e
criação (0/8). Nesta revisão, um pedido criativo chegou ao motor criativo e o
checkpoint encerrou a geração no primeiro token (`too-short; parada: eos`).

Tudo o que funciona de verdade hoje é determinístico: regras, recuperação do
acervo, motores AST, receitas de implementação, pesquisa extrativa e as
ferramentas do runtime. Por isso esta revisão fez duas coisas:

1. corrigiu falhas de roteamento e pesquisa que desperdiçavam o que já
   funciona;
2. acrescentou APIs que entregam valor **sem depender do modelo** (análises de
   engenharia, fontes estruturadas e roteamento explicável).

A decisão que mais muda o futuro do Brasa está em
[Recomendações estratégicas](#recomendações-estratégicas).

## Achados

### 1. Roteamento automático de recursos

| Severidade | Achado | Estado |
| --- | --- | --- |
| Alta | Pedidos de escrita autoral com “crie”/“escreva” (poema, roteiro, campanha, e-mail) eram classificados como `build`: personalidade de engenharia e ferramentas de escrita no workspace. | Corrigido (`isCreativeWritingRequest`). |
| Alta | O motor criativo era inalcançável pelo AgentCore: o worker forçava `intent=conversation` para todo objetivo `conversation`. | Corrigido; o worker usa `creative_reply` também nessa rota. |
| Alta | Quatro classificadores independentes decidem a intenção e discordam: `classifyObjective` (TS), `route_intent` (Python), `SkillRouter` e o roteador neural `char-ngram`. Ex.: “Qual a versão mais nova do React?” era `research` no TS e `programming` no Python. | Mitigado: `task-route/v1` passa a ser a decisão explicável; a unificação total fica como próximo passo. |
| Média | “Quais são as novidades do Python 3.14?”, “Quem ganhou…?”, “Novidades do tokio” não acionavam pesquisa. | Corrigido. |

Classificação antes → depois (amostra):

| Pedido | Antes | Depois |
| --- | --- | --- |
| Crie um poema sobre o mar | build / engineering | conversation / creative |
| Escreva um e-mail para meu chefe pedindo férias | build / engineering | conversation / creative |
| Me dê 5 ideias de nome para uma cafeteria | conversation / conversation | conversation / creative |
| Quais são as novidades do Python 3.14? | conversation | research |
| Qual a versão mais nova do React? | research (só web) | research com registro npm + web |
| Crie uma landing page para a campanha | build | build (inalterado) |

### 2. Pesquisa na internet

| Severidade | Achado | Estado |
| --- | --- | --- |
| Alta | A pesquisa prévia de build forçava `provider: brave`. Sem a chave configurada, **toda** pesquisa de build falhava, e a falha não aparecia na resposta. | Corrigido: `auto` sem chave; a falha aparece na resposta bloqueada. Com a chave, a falha da Brave continua explícita. |
| Alta | O fallback fundamentado só existia para perguntas sobre **CMake FetchContent**. Qualquer outra pesquisa bem-sucedida era descartada quando o checkpoint não sintetizava (sempre, hoje) e a resposta virava um trecho irrelevante do acervo local. Reproduzido com React: a fonte dizia 19.3.0 e a resposta exibia a página inicial do react.dev. | Corrigido: síntese extrativa geral, literal e com URL. |
| Média | Só dois provedores genéricos: Brave (pago) e scraping de HTML do DuckDuckGo (frágil a bloqueios e mudanças de layout). Nenhuma fonte estruturada. | Ampliado: registros npm/PyPI/crates.io, Wikipedia e GitHub. |
| Baixa | Casos especiais fixos no código: consultas para “axum + sqlx + jwt”, lista curta de termos “distintivos” na relevância e URLs diretas por linguagem. | Registrado; ver recomendações. |
| Baixa | `reqwest` com `rustls-tls` usa raízes embutidas e ignora CAs do sistema; atrás de proxy corporativo a pesquisa falha. | Registrado. |

### 3. Cérebro criativo

`creative_engine.py` é bem desenhado (perfis `focused`/`balanced`/`divergent`,
seleção por diversidade, repetição e restrições), mas depende integralmente da
geração neural. Agora ele recebe os pedidos certos, e `task-route/v1` expõe o
briefing (perfil, variações, restrições `sem …`/`com …`). A qualidade da peça
continua limitada pelo checkpoint.

### 4. Cérebro de desenvolvimento

Pontos fortes: escrita só com aprovação, `apply_batch`/`undo_batch`, checks
reconhecidos sem shell arbitrário, motor AST para avaliar funções, receitas
determinísticas e diagnóstico de falhas.

Lacunas: o plano `PLANO_APIS_CAPACIDADES_ENGENHEIRO_SENIOR.md` previa busca de
referências, impacto de mudança, descoberta de testes, revisão de segurança e
auditoria de dependências (Fases A e B). Nenhuma existia; as cinco foram
implementadas nesta revisão. A geração de código pelo modelo continua em 0/96.

### 5. Qualidade, testes e segurança

- `python/model_server.py` tem cerca de 7.100 linhas e muitos ramos que
  respondem a frases específicas de avaliação (ex.: resumo fixo “Decidimos
  criar uma API própria…”, rota para “causa exata … rust … fechar sozinho”,
  fallback só de CMake). Isso melhora benchmarks sem aumentar capacidade geral.
- Os testes Python gravam em arquivos versionados (`logs/agent_traces.jsonl`,
  `corpus/agent/skills.json`).
- Testes desatualizados: orçamento de geração (1.024 × 2.048 na política
  oficial) e texto antigo sobre Ollama. Atualizados.
- A nova revisão de segurança, rodada no próprio Brasa, encontrou:
  `torch.load(..., weights_only=False)` em `python/checkpoint_io.py:15` e
  `pretrain/train.py:165` (carregar um checkpoint de terceiros executa código
  arbitrário via pickle) e `python/requirements-cpu.txt` sem versões fixadas
  (`torch`, `numpy`, `safetensors`) nem lockfile.
- Falhas pré-existentes que permanecem (não introduzidas aqui): três no
  `RunEngine` legado de `/runs` (`test_agent_runs`), duas de comportamento no
  worker (`test_model_server_agentic`) e duas de ambiente (sem `shellcheck` e
  sem a fonte usada pelo teste de OCR).

## O que mudou

### Correções

| Arquivo | Mudança |
| --- | --- |
| `agent-core/src/requirements.ts` | `isCreativeWritingRequest`; escrita autoral vira conversa criativa; mais sinais de informação atual. |
| `agent-core/src/personality.ts` | Personalidade criativa usa o mesmo detector. |
| `python/model_server.py` | Motor criativo alcançável pelo AgentCore; síntese extrativa geral de pesquisa; falha de pesquisa visível; provider `auto` sem chave Brave. |
| `python/build_research.py` | `brave_search_configured()`. |

### APIs novas

| API | Contrato | O que faz |
| --- | --- | --- |
| `POST /api/v1/agent/route` | `task-route/v1` | Para qualquer pedido: objetivo, cérebro, personalidade, ferramentas permitidas, se precisa de internet (`required`/`recommended`/`none`), fontes e pacote consultado. Não executa nada. |
| `POST /api/v1/engineering/requirements/extract` | `agent-requirements/v1` | Restrições, critérios de aceite, lacunas, perguntas e ambiguidade de um pedido. |
| `POST /api/v1/engineering/code/references` | `engineering-code-references/v1` | Definição, imports, chamadas e testes que citam um símbolo. |
| `POST /api/v1/engineering/code/change-impact` | `engineering-change-impact/v1` | Dependentes, testes afetados, checks recomendados e risco. |
| `POST /api/v1/engineering/tests/discover` | `engineering-tests-discovery/v1` | Frameworks, testes, casos estimados e fontes sem teste. |
| `POST /api/v1/security/code-review` | `security-code-review/v1` | 17 regras: segredos (mascarados), injeção, desserialização, TLS, XSS, CORS, debug, hash fraco, permissões. |
| `POST /api/v1/engineering/dependencies/audit` | `engineering-dependency-audit/v1` | npm, PyPI, crates e Go: versões sem fixação, origens fora do registro, lockfiles e divergências. |
| `POST /api/v1/research` (ampliada) | `sources`, `package`, `language` | Registros npm/PyPI/crates.io, Wikipedia e GitHub como páginas citáveis antes da web. |

As cinco análises também são ferramentas do agente (`code_references`,
`change_impact`, `discover_tests`, `security_scan`, `dependency_audit`), com
contratos em `contracts/`, metadados em `capabilities/metadata.json`, validação
Zod no AgentCore, gatilhos no planejador local e três skills novas
(`security-review`, `dependency-review`, `change-impact-analysis`). O catálogo
passou de 36 para 41 ferramentas. `/v1/chat/completions`, que já existia, foi
documentado no OpenAPI.

Exemplos:

```bash
curl -s localhost:3000/api/v1/agent/route -H 'content-type: application/json' \
  -d '{"prompt":"Qual a versão mais nova do React?"}'

curl -s localhost:3000/api/v1/research -H 'content-type: application/json' \
  -d '{"query":"versão mais nova React","sources":["package-registry","web"],
       "package":{"name":"react","ecosystems":["npm"]}}'

curl -s localhost:3000/api/v1/security/code-review -H 'content-type: application/json' \
  -d '{"min_severity":"high"}'

curl -s localhost:3000/api/v1/engineering/code/change-impact -H 'content-type: application/json' \
  -d '{"path":"python/build_research.py"}'
```

## Recomendações estratégicas

### 1. Decidir o cérebro (decisão do dono do projeto)

Nenhuma camada de API transforma um modelo de 6,7M parâmetros em um assistente
competente. Há três caminhos, não excludentes:

| Caminho | O que é | Prós | Contras |
| --- | --- | --- | --- |
| A. Continuar do zero | Pré-treino próprio no Colab (`pretrain/`, presets de ~40M a ~300M). | Pesos 100% autorais. | Modelos dessa escala costumam produzir texto fluente, mas raciocínio e código fracos; exigem bilhões de tokens e muitas horas de GPU. |
| B. Ajustar pesos abertos | Partir de um modelo aberto (por exemplo, da família Qwen3, que o projeto já usa como professor no Colab), aplicar SFT/LoRA com os dados do Brasa e rodar localmente em formato quantizado. | Salto imediato de competência, continua local e privado, reaproveita contratos, ferramentas e avaliações existentes. | Os pesos de base não são autorais; licença do modelo precisa ser respeitada. |
| C. Provedor externo opcional | Mesma interface `agent-dialogue/v1`, com um provedor de API desligado por padrão. | Qualidade máxima sem GPU local. | Dados saem da máquina; custo por uso; contraria a política atual. |

Recomendação: **B como produto e A como pesquisa**. Toda a infraestrutura
(contratos, aprovação, verificação, roteamento, pesquisa, avaliações) foi
construída de forma independente do modelo e passa a render de verdade quando
o gerador consegue escrever.

### 2. Próximos passos técnicos

1. Fazer de `task-route/v1` a única decisão de roteamento: o worker recebe a
   rota em vez de reclassificar com `route_intent`; aposentar regras lexicais
   duplicadas.
2. Separar avaliação de produção: retirar de `model_server.py` os ramos que
   respondem a frases específicas de benchmarks e medir com prompts inéditos.
3. Dividir `model_server.py` (conversa, pesquisa, build, análise, criação).
4. Pesquisa: Stack Overflow pela API oficial (exige habilitar gzip no
   `reqwest`), cache de resultados por consulta, raízes TLS do sistema
   (`rustls-tls-native-roots`) e remoção dos casos especiais fixos.
5. Segurança: carregar checkpoints com `weights_only=True` ou somente
   Safetensors; fixar versões em `requirements-cpu.txt`.
6. Testes: corrigir as sete falhas pré-existentes e impedir que testes gravem
   em arquivos versionados.

## Verificação

| Bateria | Antes | Depois |
| --- | --- | --- |
| AgentCore (`npm test`) | 145/145 | 150/150 (+ `tsc --noEmit` limpo) |
| Runtime Rust (`cargo test`) | 57/57 no binário principal (com `.venv`) | 64/64 no binário principal; 70/70 no total |
| Python (`unittest discover`) | 673 executados; 10 falhas e 7 erros (6 erros por `pytest`/`pyarrow` ausentes no ambiente) | 683 executados com `pytest`/`pyarrow`; 6 falhas e 1 erro, todos pré-existentes; 4 falhas antigas corrigidas |

Teste ao vivo com o stack completo: todos os endpoints novos responderam pela
porta 3000; `/api/v1/skills/route` escolheu `security_scan` para “Faça uma
revisão de segurança do projeto”; um pedido de versão de pacote saiu do
AgentCore com `sources: ["package-registry", "web"]`.

Limites desta verificação: o container usado não alcança a Wikipedia, o GitHub
nem o DuckDuckGo, e o `reqwest` não confia na CA do proxy local; por isso as
fontes estruturadas foram validadas com fixtures e com os formatos reais de
npm e PyPI obtidos por `curl`, não por uma pesquisa completa ao vivo.
