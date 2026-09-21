# Pontos fortes dos modelos de referência

Este levantamento separa capacidade observável de escala de infraestrutura.
Vamos reproduzir a arquitetura e os comportamentos úteis localmente, sem
copiar pesos, dados privados ou respostas proprietárias.

## Gemini

### Pontos fortes observáveis

- contexto muito longo para documentos, código e mídia;
- multimodalidade nativa com texto, imagem, áudio e vídeo;
- function calling e ferramentas gerenciadas;
- pesquisa, contexto de URL, arquivos e execução de código;
- fluxos agentivos e execução observável.

A documentação do Gemini descreve contexto de um milhão ou mais de tokens,
com uso em entradas multimodais e cache de contexto
([contexto longo](https://ai.google.dev/gemini-api/docs/long-context)). Também
documenta function calling, pesquisa, URL, arquivos e execução de código
([ferramentas](https://ai.google.dev/gemini-api/docs/tools)).

### Aplicação local

1. índice híbrido de texto, código e metadados;
2. ingestão multimodal por extratores especializados;
3. cache de contexto por projeto e documento;
4. chamadas de ferramenta com resultado estruturado;
5. etapas observáveis no chat.

O equivalente local não será um contexto de um milhão de tokens em RAM. Será
seleção hierárquica: mapa do projeto, resumos, trechos relevantes e evidência
original sob demanda.

## Claude

### Pontos fortes observáveis

- respostas longas e bem estruturadas;
- raciocínio em tarefas com várias etapas;
- uso de ferramentas com reflexão após o resultado;
- contexto de conversa e instruções persistentes;
- boa adaptação de tom e colaboração em documentos e código.

A documentação do Claude destaca capacidades de thinking para refletir depois
de usar ferramentas e em raciocínio complexo, além de context awareness nos
modelos mais recentes ([orientações oficiais](https://docs.anthropic.com/en/docs/build-with-claude/prompt-engineering/prompt-templates-and-variables)).

### Aplicação local

1. separar plano interno, execução e verificação;
2. permitir uma segunda passagem curta após cada ferramenta;
3. manter objetivo, decisões, restrições e pendências em memória resumida;
4. adicionar modos iniciante, técnico, executivo e criativo;
5. produzir respostas com hipótese, evidência, ação e validação.

O ganho esperado é colaboração melhor, sem deixar o processo preso em
raciocínio invisível ou ilimitado.

## ChatGPT / OpenAI

### Pontos fortes observáveis

- combinação de modelos especializados por tarefa;
- programação e raciocínio com esforço configurável;
- ferramentas de funções, pesquisa web, arquivos e computador;
- streaming e eventos para experiências responsivas;
- voz, visão, transcrição e geração multimodal.

A documentação oficial lista suporte a texto, imagem, visão, funções, pesquisa
web, file search e computer use em modelos recentes
([modelos e ferramentas](https://platform.openai.com/docs/models)). A API também
expõe eventos de pesquisa e ações como busca, abertura de página e localização
de texto ([streaming de pesquisa](https://platform.openai.com/docs/api-reference/responses-streaming/response/refusal)).

### Aplicação local

1. roteador de modelos ou módulos por dificuldade;
2. contratos JSON rígidos para function calls;
3. eventos de progresso por etapa;
4. ferramentas nativas de código, arquivos e pesquisa;
5. streaming de texto e status para reduzir sensação de espera;
6. avaliação contínua por tarefa, não apenas por resposta bonita.

Esta é a maior oportunidade imediata porque o projeto já possui runtime Rust,
ferramentas e eventos.

## Grok

### Pontos fortes observáveis

- respostas diretas e tom conversacional;
- raciocínio configurável para problemas difíceis;
- foco forte em código e chamadas agentivas de ferramentas;
- acesso a informação recente por pesquisa web e X;
- voz e modalidades especializadas.

A documentação da xAI descreve chamadas agentivas de ferramentas, raciocínio
configurável e pesquisa web/X para dados atuais
([modelos Grok](https://docs.x.ai/developers/models)). Ela também deixa claro
que o modelo não conhece eventos posteriores ao treinamento sem habilitar
pesquisa, uma regra importante para o nosso próprio roteador.

### Aplicação local

1. respostas diretas antes de explicações longas;
2. modo atual que pesquisa automaticamente quando necessário;
3. fontes recentes separadas do conhecimento estático;
4. personalidade configurável sem alterar a factualidade;
5. ferramentas rápidas para tarefas repetitivas.

## Síntese de arquitetura

Os pontos fortes convergem para sete componentes que devemos implementar:

1. **Roteador:** decide responder, pesquisar, ler, editar, testar, criar ou
   pedir esclarecimento.
2. **Memória hierárquica:** sessão, projeto, preferências e conhecimento
   permanente, cada um com escopo e expiração.
3. **Contexto seletivo:** mapa, resumo, trechos e evidência original.
4. **Orquestrador de ferramentas:** contratos, allowlist, timeout, retries e
   resultado verificável.
5. **Planejador curto:** objetivo, etapas, dependências, execução e validação.
6. **Camada de estilo:** tom, nível técnico, criatividade e formato.
7. **Avaliador:** testes objetivos, notas humanas, latência e regressões.

## Ordem de aplicação

### Agora

- concluir roteador de trabalho, criatividade e planejamento;
- criar memória estruturada de objetivo, decisões e preferências;
- padronizar eventos de ferramenta;
- melhorar respostas diretas e naturais;
- criar tarefas de avaliação para cada novo modo.

### Em seguida

- contexto hierárquico para projetos grandes;
- segunda passagem de verificação após ferramentas;
- documentos estruturados, voz e visão;
- criatividade com variações e crítica;
- planejamento de tarefas do dia-a-dia.

### Depois

- módulos especializados por hardware;
- fine-tuning ou adaptadores somente quando os dados e benchmarks justificarem;
- agentes locais configuráveis;
- treinamento contínuo a partir de correções revisadas.

O alvo não é imitar um modelo específico. É combinar a velocidade e o controle
local com as melhores ideias de contexto, ferramentas, raciocínio, conversa e
multimodalidade observadas nesses sistemas.
