# Plano da interface do assistente local

## Objetivo

Transformar a aplicação em um ambiente de trabalho conversacional para projetos do usuário. O usuário conversa com um agente; ferramentas, arquivos, testes e prévias aparecem como partes organizadas desse trabalho, não como detalhes internos expostos a cada momento.

## Princípios

- O projeto ativo é sempre visível.
- A conversa mostra decisões, respostas e resultados; detalhes técnicos ficam recolhidos.
- O agente entende, planeja, pede confirmação quando necessário, executa, valida e resume.
- Ações destrutivas ou ambíguas exigem confirmação clara.
- Ausência de testes é um estado informativo, não uma falha.
- O usuário não precisa conhecer nomes de ferramentas, contratos ou JSON.
- Cada tipo de resultado tem uma apresentação própria.

## Arquitetura da tela

### Referência visual adotada

O protótipo apresentado estabelece a direção visual da aplicação: um workspace de desktop com navegação lateral compacta, uma área central de conversa com identidade própria e um Explorer persistente à direita. A interface deve parecer um editor de código que possui um assistente integrado, e não uma página de chat com ferramentas espalhadas.

- Navegação lateral estreita e silenciosa, sem competir com o trabalho.
- Cartão de projeto ativo com nome, caminho e modelo/runtime.
- Área central com cabeçalho do assistente, contexto do projeto e composição de mensagem no próprio painel.
- Explorer à direita com árvore de arquivos, arquivo selecionado e contexto ativo.
- Ações rápidas contextuais, pequenas e discretas; evitar grupos de botões grandes.
- Superfícies escuras em camadas, bordas suaves, acento de cor reservado para estado ativo e confirmação.
- Espaço vazio intencional no estado inicial, com poucas ações úteis como analisar código ou criar componente.

### Navegação lateral

- Nova conversa.
- Histórico de conversas.
- Projeto ativo e troca de projeto.
- Projetos recentes.
- Estado do runtime.

### Conversa central

- Mensagens naturais do usuário e do assistente.
- Planos resumidos antes de ações relevantes.
- Perguntas de esclarecimento.
- Resultados de arquivos, prévias, pesquisas e testes.
- Atividades técnicas agrupadas em um bloco recolhível.

### Contexto do projeto

- Caminho e nome do projeto ativo.
- Árvore de arquivos.
- Arquivo selecionado/aberto.
- Alterações recentes.
- Verificações disponíveis e último resultado.
- Ações rápidas: abrir projeto, criar arquivo, criar pasta, atualizar e abrir prévia.

## Ciclo de uma tarefa

```text
entender -> verificar contexto -> explicar plano -> confirmar
-> executar -> validar -> resumir -> sugerir próximo passo
```

Nem toda tarefa precisa de confirmação. Perguntas, leituras e pesquisas podem seguir diretamente; criação, edição, exclusão e execução de comandos devem declarar o que será feito.

## Estados da experiência

- Nenhum projeto selecionado.
- Projeto selecionado.
- Projeto vazio.
- Projeto com arquivos.
- Analisando projeto.
- Aguardando confirmação.
- Executando ação.
- Ação concluída.
- Validação pendente.
- Projeto sem verificações configuradas.
- Erro recuperável.
- Erro que exige decisão do usuário.

## Tipos de resultado

- Texto conversacional.
- Plano da tarefa.
- Confirmação.
- Arquivo criado ou editado.
- Diff.
- Prévia web.
- Árvore de arquivos.
- Resultado de testes.
- Diagnóstico.
- Fontes de pesquisa.

## Fases de implementação

1. Shell visual de três áreas e estados do projeto.
2. Modelo único de mensagens e resultados.
3. Orquestração conversacional: plano, autorização, execução e síntese.
4. Painel persistente de arquivos e projeto.
5. Prévia, diff e validação integrados.
6. Matriz de testes manuais e refinamento visual.

## Padrões incorporados da pesquisa de referência

### Gemini

- A barra de prompt deve ser o centro de entrada, com anexos e modos contextuais sem obrigar o usuário a abrir vários painéis.
- Um modo de Canvas deve tratar código, documentos e páginas como artefatos editáveis, com prévia e iteração no mesmo fluxo.
- Sugestões contextuais devem aparecer no estado inicial e depois de resultados, como “analisar código”, “explicar arquivo” e “criar componente”.
- Pesquisa profunda deve apresentar plano, andamento, fontes e síntese final, com rastreabilidade sem despejar o log técnico na conversa.
- O contexto do projeto deve persistir e ser reutilizado entre turnos.

### Claude Code

- Planejamento e execução devem ser estados distintos: primeiro mostrar o que será feito, depois executar.
- Permissões devem ser granulares e aparecer inline, com escopo, caminho e consequência da ação.
- Sessões precisam ser retomáveis, com histórico ligado ao projeto e possibilidade de continuar a última tarefa.
- Detalhes técnicos devem existir em uma visão avançada/expandida, enquanto a conversa mostra uma síntese humana.
- A execução deve respeitar o projeto atual e tornar explícito quando uma operação sai do escopo dele.

### Adaptação para a IA Local do Zero

1. Criar modos de conversa: Chat, Workspace, Planejar e Pesquisa.
2. Transformar o composer em uma barra de prompt com anexos, modo atual e sugestões.
3. Criar uma área de artefato para prévia, texto e código, inspirada no Canvas.
4. Criar cartões de plano e autorização inline, inspirados no permission mode.
5. Criar uma visão “Detalhes” para atividades, logs, ferramentas e verificações.
6. Persistir sessão, projeto ativo, artefatos abertos e última tarefa.
7. Medir a experiência pelos cenários reais: iniciar projeto vazio, editar arquivo, pesquisar, validar e retomar.

Referências consultadas: [Gemini Canvas e colaboração](https://blog.google/products-and-platforms/products/gemini/gemini-collaboration-features/), [Gemini Deep Research e Gems](https://blog.google/products-and-platforms/products/gemini/new-gemini-app-features-march-2025/), [Gems e contexto persistente](https://support.google.com/gemini/answer/15235603), [Claude Code CLI, permissões e retomada](https://docs.anthropic.com/en/docs/claude-code/cli-usage).

## Critério de qualidade

Uma pessoa deve conseguir selecionar um projeto, pedir uma tarefa em linguagem natural, acompanhar o plano, revisar o resultado e continuar trabalhando sem conhecer os nomes das ferramentas internas.
