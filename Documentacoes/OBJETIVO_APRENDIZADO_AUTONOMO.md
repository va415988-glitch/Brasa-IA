# Objetivo final: domínio autônomo e profundo

## Visão

A IA Local não deve ser apenas uma geradora de páginas, snippets ou respostas plausíveis. O objetivo é construir um agente capaz de internalizar conhecimentos de forma operacional: compreender uma linguagem, reconhecer padrões, escolher abordagens, pesquisar o que falta, experimentar, testar, diagnosticar falhas e transferir o aprendizado para problemas inéditos.

“Quase inato” é um alvo operacional. Não significa consciência nem conhecimento mágico; significa que o agente não depende de um roteiro humano para começar a raciocinar sobre uma tarefa nova.

## Princípios não negociáveis

1. **Aprendizado autônomo:** o agente mantém uma fila própria de lacunas e inicia ciclos sem receber um prompt manual para cada assunto.
2. **Domínio, não recuperação:** fonte consultada é evidência de estudo, nunca prova de competência.
3. **Prática verificável:** toda competência precisa de prática isolada, tarefa inédita, integração e registro de resultado.
4. **Transferência:** aprender uma linguagem deve melhorar a capacidade de reconhecer conceitos relacionados em outras linguagens, sem copiar equivalências cegamente.
5. **Economia:** fontes, resumos, conceitos e testes são reutilizados; pesquisas repetidas, contextos duplicados e trilhas já dominadas são evitados.
6. **Falha produtiva:** uma prática que falha gera diagnóstico, recuperação limitada e nova tentativa agendada; não gera uma aprovação falsa.
7. **Segurança:** o aprendizado pode pesquisar e executar laboratórios isolados, mas não executa código baixado da web nem altera o sistema operacional sem contratos e aprovação.
8. **Rastreabilidade:** cada avanço aponta para fontes, prática, comando, resultado e critério de promoção.

## Ciclo autossustentável

```text
observar ledger
  → ranquear lacunas e conhecimento vencido
  → reutilizar evidências e fundamentos compartilhados
  → pesquisar fontes primárias limitadas
  → condensar e indexar somente material validado
  → criar/executar prática isolada
  → testar transferência inédita
  → validar integração
  → atualizar competência e agendar a próxima lacuna
```

O ciclo possui orçamento por rodada. Ao atingir o limite de fontes, documentos, passos ou contexto, ele para com um estado verificável e retoma depois; não entra em loop infinito.

## Portfólio inicial

O primeiro portfólio tem 20 trilhas:

- Python; JavaScript; TypeScript; Java; C#; SQL;
- C; C++; Rust; Go;
- PHP; Kotlin; Swift; Dart; Ruby;
- R; Shell/Bash; Lua; Elixir;
- MATLAB/Julia como trilha de computação científica, com as duas variantes registradas.

A lista é um ponto de partida, não uma prisão. A arquitetura é baseada em domínio e contratos, portanto pode incluir novas linguagens, frameworks, ciência, matemática, escrita, pesquisa, design e comunicação.

## Conhecimento compartilhado

As trilhas reaproveitam fundamentos como modelagem de problemas, tipos e dados, composição, erros e testes, debugging, algoritmos, segurança, desempenho, concorrência e documentação. Isso reduz custo e evita que a IA reaprenda o mesmo conceito vinte vezes.

A reutilização nunca substitui a validação específica. Ownership em Rust, ponteiros em C, garbage collection em Java e tipos em TypeScript compartilham ideias, mas possuem contratos diferentes e precisam de testes próprios.

## Critério de competência

Uma linguagem só pode ser promovida quando houver:

- fontes relevantes e independentes;
- cobertura dos conceitos da trilha;
- práticas aprovadas com taxa suficiente;
- tarefa inédita de transferência;
- tarefa de integração;
- ausência de falhas não resolvidas.

Para domínios sem executor local, a IA cria uma prática rubricada e a mantém como pendência explícita. Ela não converte uma resposta textual em domínio automaticamente.

## Primeiro componente aplicado

`python/portfolio.py` define o portfólio, famílias, usos e relações entre trilhas.

`python/autonomous_learning.py` implementa o professor autônomo. Ele:

- lê o ledger atual;
- escolhe a lacuna prioritária;
- gera uma sequência completa de aprendizagem;
- impõe orçamento e deduplicação;
- inicia a pesquisa através do pipeline existente;
- acompanha o ciclo em um arquivo de controle e em um log auditável.

Comandos:

```bash
# Ver a decisão da próxima rodada sem pesquisar
.venv/bin/python python/autonomous_learning.py --plan

# Iniciar um ciclo limitado
.venv/bin/python python/autonomous_learning.py --once

# Manter o professor observando e iniciando ciclos
.venv/bin/python python/autonomous_learning.py --daemon --interval 3600
```

A ativação do daemon é uma decisão operacional única do ambiente. Depois disso, a manutenção da fila e a escolha das trilhas não dependem de novas mensagens do usuário. O daemon não é instalado nem ativado automaticamente: pesquisa na web e execução de práticas são efeitos externos que precisam de uma decisão explícita do ambiente.

O estado também está disponível no runtime, sem despejar o job inteiro no navegador:

```text
GET  /api/v1/learning/autonomous
POST /api/v1/learning/autonomous/tick
```

A interface de Treinamento mostra a próxima trilha, o motivo da escolha, o ciclo ativo e os últimos ciclos. O botão de execução é explícito; a operação continua limitada pelo orçamento e pelo cooldown diário. A auditoria preserva a cauda dos logs de cada ciclo, suficiente para acompanhar o trabalho sem transformar a interface em um bloco ilegível.

### Orçamento aplicado

O ciclo padrão é limitado a 6 páginas de fonte, 6 documentos novos, 16 práticas, 24 passos de ferramenta, aproximadamente 24 mil tokens de contexto e 2 ciclos por dia. Esses limites não são decorativos: a pesquisa recebe `max_results`, o contexto é truncado de forma conservadora, a fila do laboratório inclui recuperações dentro do mesmo teto e o professor entra em `cooldown` ao atingir o limite diário.

Para manter o processo após reinicializações, há um modelo de unidade em `Documentacoes/ia-local-autonomous-learning.service.example`. Ele deve ser revisado e instalado pelo operador; o agente não habilita serviços do sistema sozinho.

## Primeira aplicação validada

A primeira rodada operacional foi executada com o runtime local ativo. O professor escolheu uma lacuna sem solicitação manual, pesquisou dentro do orçamento, registrou logs e deixou a competência como parcial quando não havia executor seguro para Bash/Go. Depois que os executores foram disponibilizados, Go passou por 12/12 práticas isoladas e Bash por 12/12, incluindo ShellCheck; ambas as validações foram registradas como eventos `laboratory_verified`.

Durante essa rodada, uma busca ambígua por `Go` encontrou o aplicativo Google Go. O filtro de contexto rejeitou o falso positivo depois de identificado, o registro foi removido do corpus e do ledger, e a tentativa ficou preservada como falha auditável. A próxima rodada retoma Go, agora com fonte curada do projeto. Esse comportamento é parte do objetivo: aprender autonomamente sem transformar coincidência lexical em conhecimento.

Validação atual: 202 testes Python, 16 testes TypeScript e 33 testes Rust aprovados (32 do runtime e 1 da ferramenta de corpus).

## O que ainda será expandido

1. Mais executores isolados além das linguagens já cobertas.
2. Avaliadores rubricados para escrita, pesquisa, planejamento e design.
3. Revisão automática de conhecimento vencido.
4. Integração do estado do professor à interface, com logs de cada ciclo. **Aplicado nesta etapa.**
5. Adaptação dos testes de transferência ao perfil de cada linguagem. **Bash aplicado; Go bloqueado até haver compilador local.**
