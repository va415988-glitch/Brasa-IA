# Plataforma de APIs de capacidades do agente

## Objetivo

Transformar a IA Local do Zero em um agente de engenharia de software sênior: capaz de entender sistemas, investigar problemas, tomar decisões arquiteturais, implementar mudanças seguras, testar, revisar, explicar trade-offs e aprender com evidências verificáveis.

Áudio e visão entram como sentidos do mesmo agente. Não serão produtos separados. Toda entrada multimodal deve alimentar o mesmo pipeline de entendimento, contexto, planejamento, execução e verificação.

```text
entrada textual / código / áudio / imagem / vídeo
        ↓
percepção e normalização
        ↓
entendimento do pedido e do projeto
        ↓
contexto, memória e evidências
        ↓
plano de engenharia
        ↓
política e autorização
        ↓
implementação / pesquisa / execução
        ↓
testes, revisão e diagnóstico
        ↓
resposta, artefatos, memória e evolução de competência
```

## Princípio de senioridade

Ser um engenheiro sênior não é produzir muito código. O agente precisa demonstrar:

- leitura estrutural de sistemas existentes;
- modelagem de domínio e identificação de invariantes;
- decisões justificadas com trade-offs;
- respeito a requisitos funcionais e não funcionais;
- mudanças pequenas, reversíveis e compatíveis;
- testes significativos, não apenas testes que passam;
- diagnóstico baseado em evidência;
- atenção a segurança, desempenho, observabilidade e operação;
- comunicação clara com pessoas e com outras ferramentas;
- capacidade de reconhecer incerteza e pedir contexto quando necessário.

## Núcleo comum de contratos

Todas as APIs devem usar envelopes versionados:

```json
{
  "schema": "agent-contract/v1",
  "ok": true,
  "request_id": "...",
  "trace_id": "...",
  "session_id": "...",
  "task_id": "...",
  "status": "ready|running|blocked|verified|failed",
  "data": {},
  "evidence": [],
  "warnings": [],
  "errors": [],
  "elapsed_ms": 0
}
```

Nenhuma API deve retornar apenas um texto livre quando a informação pode ser estruturada. O texto é a apresentação final; o contrato é a fonte de verdade.

# APIs prioritárias de engenharia

## 1. Percepção do projeto

### `POST /api/v1/engineering/project/scan`

Mapeia o projeto sem executar código arbitrário.

Retorna:

- linguagens, frameworks e versões;
- manifests e dependências;
- pontos de entrada;
- módulos e camadas;
- testes existentes;
- comandos disponíveis;
- configuração e variáveis esperadas;
- riscos de segurança;
- lacunas de documentação;
- cobertura e arquivos não analisados.

Esse mapa será a base para todas as decisões posteriores. Deve ter hash do snapshot e validade.

### `POST /api/v1/engineering/project/architecture`

Constrói uma visão arquitetural baseada no mapa e nos arquivos reais: componentes, fronteiras, fluxo de dados, dependências e acoplamentos. Toda afirmação deve apontar para arquivos ou símbolos que a sustentem.

## 2. Entendimento e especificação

### `POST /api/v1/engineering/requirements/extract`

Extrai requisitos explícitos, implícitos, restrições, critérios de aceitação, riscos e perguntas em aberto de uma solicitação.

### `POST /api/v1/engineering/spec/plan`

Transforma requisitos em especificação executável: escopo, contratos, modelo de dados, interfaces, estados, erros, testes e critérios de pronto.

### `POST /api/v1/engineering/design/review`

Revisa uma proposta de arquitetura ou design contra requisitos, complexidade, segurança, desempenho, evolução e operação.

## 3. Navegação e compreensão de código

### `POST /api/v1/engineering/code/search`

Busca por símbolos, referências, chamadas, tipos, rotas, eventos e fluxo de dados. Deve devolver contexto semântico, não somente linhas coincidentes.

### `POST /api/v1/engineering/code/explain`

Explica um módulo, função ou fluxo com entradas, saídas, efeitos colaterais, invariantes, dependências e pontos de falha.

### `POST /api/v1/engineering/code/change-impact`

Calcula o impacto provável de uma alteração: consumidores, testes afetados, APIs quebradas, migrações necessárias e risco de regressão.

### `POST /api/v1/engineering/code/symbols`

Indexa funções, classes, traits, tipos, endpoints, schemas e relações entre símbolos. Deve ser incremental e específico para cada linguagem.

## 4. Implementação controlada

### `POST /api/v1/engineering/implementation/plan`

Cria um plano de implementação com arquivos alvo, ordem das mudanças, pré-condições, testes e rollback.

### `POST /api/v1/engineering/patch/propose`

Gera uma proposta de diff sem aplicá-la. Deve conter motivo, risco, arquivos, trechos e compatibilidade.

### `POST /api/v1/engineering/patch/validate`

Valida se o diff é aplicável, não possui ambiguidade, respeita o escopo e não remove comportamento sem justificativa.

### `POST /api/v1/engineering/patch/apply`

Aplica somente um patch validado, com backup, hash anterior, hash posterior e registro de reversão.

## 5. Testes e verificação

### `POST /api/v1/engineering/tests/discover`

Descobre frameworks, comandos, fixtures, testes frágeis, cobertura e lacunas.

### `POST /api/v1/engineering/tests/generate`

Propõe testes a partir de contratos, invariantes, limites e falhas conhecidas. O agente deve explicar por que cada teste existe.

### `POST /api/v1/engineering/tests/run`

Executa apenas comandos pertencentes a um catálogo autorizado. Retorna saída, duração, ambiente, código de saída e artefatos.

### `POST /api/v1/engineering/verify`

Consolida compilação, lint, testes, diff, segurança, migrações, compatibilidade e critérios de aceitação em uma decisão `verified`, `failed` ou `inconclusive`.

## 6. Diagnóstico e manutenção

### `POST /api/v1/engineering/diagnosis`

Correlaciona erro, logs, stack trace, código, ambiente e histórico para formar hipóteses ordenadas por evidência.

### `POST /api/v1/engineering/incident/plan`

Cria plano de contenção, investigação, correção, comunicação e prevenção para falhas operacionais.

### `POST /api/v1/engineering/refactor/analyze`

Identifica duplicação, acoplamento, complexidade, código morto, interfaces instáveis e refatorações candidatas sem aplicar mudanças.

### `POST /api/v1/engineering/dependencies/audit`

Analisa versões, licenças, vulnerabilidades, dependências abandonadas e impacto de atualização.

## 7. Segurança e operação

### `POST /api/v1/security/threat-model`

Gera modelo de ameaças com ativos, limites de confiança, entradas, abuso possível, mitigação e testes.

### `POST /api/v1/security/code-review`

Procura injeção, traversal, exposição de segredo, autorização incompleta, SSRF, desserialização insegura e falhas específicas da stack.

### `POST /api/v1/engineering/observability/design`

Propõe logs estruturados, métricas, traces, alertas, SLOs e sinais de diagnóstico para o sistema.

### `POST /api/v1/engineering/performance/analyze`

Analisa complexidade, I/O, concorrência, memória, consultas, cache, serialização e gargalos com base em medições reais.

### `POST /api/v1/engineering/deploy/plan`

Gera plano de build, configuração, migração, health checks, rollback e verificação pós-deploy. Não executa deploy sem autorização explícita.

# APIs multimodais

## 8. Áudio

### `POST /api/v1/audio/ingest`

Recebe áudio local autorizado, calcula hash, duração, formato, canais, taxa de amostragem e transcodificação necessária.

### `POST /api/v1/audio/transcribe`

Produz transcrição com timestamps, confiança por segmento, idioma detectado e identificação opcional de falantes.

### `POST /api/v1/audio/understand`

Extrai intenção, tarefas, decisões, requisitos, nomes de arquivos, comandos citados e dúvidas de uma conversa ou gravação.

### `POST /api/v1/audio/speak`

Converte resposta aprovada em áudio local, preservando o mesmo `trace_id` e sem permitir que voz sintetizada execute ações.

Regras: o áudio original é evidência bruta; transcrição é interpretação; decisões extraídas só entram na memória após confirmação ou alta confiança com origem registrada.

## 9. Visão

### `POST /api/v1/vision/ingest`

Registra imagem, screenshot, PDF renderizado ou frame de vídeo com hash, dimensões, origem e privacidade.

### `POST /api/v1/vision/understand`

Extrai texto visível, layout, componentes de interface, tabelas, diagramas e regiões relevantes, sempre mantendo coordenadas e confiança.

### `POST /api/v1/vision/diagnose-ui`

Analisa screenshots da aplicação contra regras de usabilidade, contraste, alinhamento, responsividade, estados vazios, feedback e acessibilidade.

### `POST /api/v1/vision/code-review`

Lê screenshots de código ou erros apenas como evidência auxiliar. Nunca substitui a leitura do arquivo real quando o workspace estiver disponível.

### `POST /api/v1/vision/compare`

Compara duas imagens ou estados da interface e identifica alterações, regressões visuais e elementos ausentes.

## 10. Vídeo e interação

### `POST /api/v1/video/inspect`

Extrai frames relevantes, áudio, mudanças de cena, ações na interface e linha do tempo de eventos.

### `POST /api/v1/interaction/replay`

Transforma uma gravação de uso em sequência reproduzível de eventos para diagnóstico. Não executa automaticamente sem confirmação.

# APIs de aprendizagem do engenheiro

## 11. Currículo adaptativo

### `POST /api/v1/learning/engineering/curriculum`

Gera trilha baseada no projeto, stack, lacunas e objetivo: fundamentos, manutenção, arquitetura, testes, segurança, operação e transferência.

### `POST /api/v1/learning/engineering/challenge`

Cria tarefas inéditas que não podem ser resolvidas apenas repetindo um trecho do material estudado.

### `POST /api/v1/learning/engineering/assess`

Avalia código, decisão, teste, diagnóstico e explicação separadamente. A pontuação precisa mostrar dimensões, evidências e falhas.

### `GET /api/v1/learning/engineering/competency/{topic}`

Mostra domínio por dimensão: compreensão, implementação, teste, integração, manutenção, segurança, operação e comunicação.

## Dimensões de competência

Uma competência não deve ser um número único. O registro deve conter:

```json
{
  "topic": "Rust",
  "dimensions": {
    "understanding": 0.0,
    "implementation": 0.0,
    "testing": 0.0,
    "integration": 0.0,
    "debugging": 0.0,
    "security": 0.0,
    "operations": 0.0,
    "communication": 0.0
  },
  "evidence": [],
  "gaps": [],
  "source_repositories": [],
  "last_verified_at": null
}
```

## Ordem de implementação

### Fase A — núcleo de engenharia

1. `project/scan`
2. `code/search`
3. `code/explain`
4. `change-impact`
5. `implementation/plan`
6. `patch/propose` e `patch/validate`
7. `tests/discover` e `tests/run`
8. `verify`

### Fase B — agente sênior operacional

1. `diagnosis`
2. `security/threat-model`
3. `security/code-review`
4. `dependencies/audit`
5. `observability/design`
6. `performance/analyze`
7. `deploy/plan`
8. memória de decisões e revisão arquitetural

### Fase C — aprendizagem verificável

1. currículo multidimensional;
2. desafios inéditos;
3. avaliação por artefatos;
4. transferência entre projetos;
5. atualização de competências somente após verificação.

### Fase D — multimodalidade

1. ingestão e metadados;
2. transcrição e OCR/layout;
3. entendimento de áudio/visão;
4. diagnóstico de interface;
5. integração com contexto e trace;
6. voz e interação em tempo real.

# Critérios para chamar o agente de engenheiro sênior

O agente só pode receber esse rótulo operacionalmente quando conseguir, em projetos não vistos:

- mapear arquitetura com referências corretas;
- explicar consequências antes de editar;
- produzir mudanças pequenas e aplicáveis;
- escrever testes que encontrem falhas reais;
- investigar bugs sem inventar causa;
- considerar segurança e operação;
- revisar o próprio diff;
- verificar o resultado com ferramentas;
- recuperar-se de falhas;
- comunicar limites e decisões para um humano.

Fluência textual, quantidade de documentação ingerida ou percentual de fontes não contam como domínio por si só.
