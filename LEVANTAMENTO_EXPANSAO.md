# Levantamento da expansão para um assistente geral local

O projeto já possui uma base incomum para um assistente local: runtime Rust,
acervo pesquisável, ferramentas de web, URL, workspace, busca em código,
anexos, verificações de projeto, eventos de progresso, histórico e uma camada
procedural de respostas. O próximo salto não é apenas acumular textos; é
transformar essa base em modos de trabalho reutilizáveis.

## Onde ele já é forte

### Engenharia e projetos

É a área mais madura. A IA já pode inspecionar estrutura, localizar arquivos,
identificar manifestos, pesquisar código, analisar sintaxe, executar testes
permitidos e acompanhar evidências. O ganho potencial é grande porque a
ferramenta local pode editar e validar sem enviar o projeto para fora.

### Pesquisa e aprendizagem

Pesquisa web, fontes rastreáveis e acervo local permitem responder perguntas
atuais e construir material de estudo. O próximo ganho será organizar fontes em
progresso, revisão, contradições e resumos reutilizáveis.

### Trabalho com documentos

Anexos e multimodalidade permitem transformar documentos em resumo, briefing,
checklist, plano, revisão ou minuta. Isso atende relatórios, propostas,
reuniões, especificações, currículos e documentação técnica.

### Conversa orientada a tarefa

O histórico e os eventos já permitem uma conversa de trabalho. A IA pode
manter objetivo, decisões, pendências e próximo passo, em vez de tratar cada
mensagem como uma pergunta isolada.

## O que a expansão pode trazer

### Assistente de trabalho

- transformar pedido vago em objetivo e plano;
- produzir documentos em formatos diferentes;
- revisar texto com critérios definidos;
- extrair tarefas, responsáveis e prazos;
- comparar alternativas e registrar decisão;
- acompanhar uma tarefa até a validação final.

O principal benefício será reduzir o trabalho de preparação e revisão, não
apenas gerar texto.

### Assistente de programação

- entender um repositório novo;
- explicar arquitetura e dependências;
- implementar mudanças pequenas ou grandes por etapas;
- criar testes antes ou depois da alteração;
- localizar regressões;
- gerar documentação sincronizada com o código.

O diferencial local será trabalhar diretamente com o projeto, com evidência,
backup, permissões e execução controlada.

### Assistente criativo

- gerar muitas ideias com filtros de tom, público e objetivo;
- transformar uma ideia em outline, roteiro ou storyboard;
- criticar uma criação com critérios escolhidos;
- produzir variações sem perder elementos obrigatórios;
- combinar texto, imagem, áudio e vídeo;
- manter uma bíblia de projeto para obras longas.

Aqui o gargalo atual é menos ferramenta e mais geração espontânea. Será preciso
um módulo de variação, memória de estilo e avaliação humana rápida.

### Assistente do dia-a-dia

- organizar listas e checklists;
- planejar estudos, compras e rotinas;
- resumir informações pessoais autorizadas;
- preparar mensagens;
- explicar assuntos práticos;
- ajudar a decidir entre opções.

Esse módulo exige escopo claro de memória, confirmação antes de ações externas e
distinção entre sugestão e execução.

### Professor e parceiro de aprendizagem

- adaptar explicação ao nível do usuário;
- criar exercícios e corrigir respostas;
- montar trilhas por objetivo;
- recuperar erros recorrentes;
- revisar conhecimento antigo;
- usar fontes e documentos do próprio usuário.

O ganho será medido por retenção, acerto em tarefas novas e capacidade de
explicar o mesmo conceito em diferentes níveis.

## Gargalos que a expansão precisa resolver

1. **Geração geral:** a base procedural é rápida, mas ainda não substitui um
   modelo generativo para respostas abertas e criativas.
2. **Roteamento:** a IA precisa decidir se deve responder, pesquisar, ler,
   editar, testar, planejar ou perguntar.
3. **Memória:** histórico, preferências e conhecimento do projeto precisam de
   escopos separados e expiração controlável.
4. **Contexto:** projetos e documentos grandes exigem seleção, resumo e
   referências sem estourar orçamento de memória.
5. **Avaliação:** palavras-chave ajudam no diagnóstico, mas precisamos de
   testes executáveis, rubricas e revisão humana para qualidade aberta.
6. **Multimodalidade:** cada tipo de mídia precisa de extração, limites,
   feedback e indicação clara do que foi realmente processado.
7. **Ações:** toda alteração deve ter intenção, prévia, confirmação adequada,
   backup, validação e reversão.

## Priorização

### Alta prioridade

- roteador de intenção e modo de resposta;
- memória de objetivo, decisões e preferências;
- packs de tarefas de trabalho e documentos;
- criação e edição de projetos com validação;
- benchmark com paráfrases e tarefas novas;
- respostas generativas para conversa aberta.

### Média prioridade

- trilhas de aprendizagem;
- criatividade com variações e crítica;
- voz, transcrição e análise de áudio;
- visão aplicada a documentos e imagens;
- dashboards de tarefas e desempenho.

### Longo prazo

- automações agendadas locais;
- adaptação ao hardware do usuário;
- treinamento especializado com dados revisados;
- criação de agentes configuráveis por perfil;
- distribuição da plataforma para outros computadores.

## Critério para dizer que virou um bom chat para tudo

Ele não precisa ser o melhor modelo em todas as respostas. Deve conseguir:

- entender o objetivo antes de agir;
- escolher a ferramenta adequada;
- responder diretamente quando souber;
- pesquisar quando a informação exigir atualização;
- trabalhar com arquivos e projetos reais;
- manter contexto útil;
- criar e revisar conteúdo;
- declarar limites e incerteza;
- mostrar progresso;
- terminar em até 30 segundos ou explicar claramente o bloqueio;
- permitir que o usuário confira e desfaça ações.

Com esses critérios, a expansão deixa de ser uma coleção de funções e vira um
assistente geral local coerente.
