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

### Prioridades aprovadas para o próximo ciclo

Estas cinco frentes foram escolhidas para expandir o portfólio de ferramentas e
aproximar o agente de um fluxo de trabalho completo. A ordem abaixo define a
sequência de implementação; cada etapa deve produzir ferramentas utilizáveis,
eventos observáveis e evidência de resultado antes de avançar.

1. **Ciclo de execução local:** iniciar processos de perfis reconhecidos,
   acompanhar estado, prontidão e saída em tempo real, e pará-los sob demanda.
   Cada processo terá diretório restrito ao workspace, limites de tempo e
   saída, encerramento previsível e aprovação explícita. A primeira entrega
   deve cobrir servidores de desenvolvimento locais; não aceitará comandos de
   shell arbitrários.
2. **Alterações em vários arquivos:** agrupar mudanças como uma operação
   revisável, mostrar diff por arquivo, validar conflitos antes de aplicar,
   criar recuperação local e permitir desfazer a operação sem perder alterações
   posteriores do usuário.
3. **Diagnóstico de código:** combinar erros reais de compiladores e
   verificadores com busca de símbolos e referências. Cada diagnóstico deve
   apontar arquivo e posição, distinguir observação de hipótese e sugerir uma
   próxima ação verificável.
4. **Teste de interface no navegador:** abrir a aplicação local, interagir com
   controles, coletar screenshot, console e falhas de rede, e devolver as
   evidências no chat. O navegador deve ficar limitado ao alvo autorizado e
   reutilizar o ciclo de execução local para iniciar e encerrar a aplicação.
5. **Multimodalidade local:** ampliar leitura de documentos para OCR e layout,
   compreensão de imagens e screenshots, e transcrição de áudio. Adaptadores
   opcionais devem declarar se estão instalados, limites, confiança e o que foi
   efetivamente processado; vídeo fica para depois dessas bases.

### Regras de implementação compartilhadas

- O agente deve selecionar ferramentas automaticamente a partir da tarefa;
  atalhos de interface são conveniências, não pré-requisitos para o usuário.
- Toda chamada precisa de contrato tipado, pré-condições, limites e resultado
  estruturado. Conteúdo de arquivos e páginas é dado, nunca instrução de
  execução.
- A interface deve mostrar eventos reais: ferramenta escolhida, aprovação
  pendente, execução, saída relevante, falha ou conclusão. Não estimar progresso
  quando não houver medição.
- Escritas precisam de diff e caminho de recuperação. Execuções precisam de
  escopo, limite de recursos e forma clara de cancelamento.
- A etapa 4 depende da etapa 1. Antes de promover uma etapa, validar seu fluxo
  de ponta a ponta com um projeto local pequeno e registrar limitações e
  evidências no relatório de capacidade.

### Primeira entrega: execução local acompanhável

**Estado em 23/09/2026:** o primeiro incremento está integrado ao runtime Rust,
ao planejador Python, à extensão VS Code, ao AgentCore TypeScript e à interface
web. O perfil `auto-dev` reconhece `npm run dev/start` ou `cargo run`; o runtime
retém até três processos ativos, guarda saída incremental em buffers limitados,
verifica URLs locais anunciadas e oferece encerramento pelo identificador.
Compilação e verificações de sintaxe passaram. A aceitação de ponta a ponta
continua pendente: ainda não iniciei um servidor de projeto neste incremento.

Antes deste incremento, o runtime só executava chamadas curtas. A entrega separa
iniciar, consultar e encerrar em operações distintas:

1. `process_start` aceita somente um perfil identificado a partir de manifesto
   reconhecido (por exemplo, script de desenvolvimento declarado no
   `package.json`), valida o workspace e passa argumentos fixos ao executor;
   scripts do próprio projeto podem executar código e aparecem na aprovação.
2. `process_status` recebe o identificador devolvido no início e informa estado
   (`running`, `exited`, `failed` ou `stopped`), prontidão quando
   verificável, código de saída e apenas a saída nova desde o último cursor.
3. `process_stop` encerra apenas o processo que o runtime iniciou e seus filhos
   controlados, e confirma o resultado. Reiniciar o runtime também precisa
   limpar processos que ele próprio deixou ativos.

Cada processo fica associado ao workspace canônico e ao perfil aprovado. A
saída deve ter buffer limitado; atingir o limite marca truncamento, sem crescer
sem controle. Se não houver uma URL/porta declarada ou uma verificação de saúde
confiável, o estado deve dizer “processo ativo; prontidão não confirmada”. A
atividade do chat registra início, atualização de estado, novas linhas
relevantes, encerramento e erro, usando os eventos existentes antes de criar
uma segunda timeline.

**Aceitação:** após aprovar o perfil, o agente inicia um servidor local,
acompanha a saída sem bloquear chamadas de leitura do workspace, confirma a
prontidão somente com evidência, abre a URL local quando solicitada e encerra o
processo pelo identificador. Comandos e argumentos livres continuam rejeitados.

### Segunda entrega: lote de alterações revisável e reversível

**Estado em 23/09/2026:** `apply_batch` e `undo_batch` estão registrados nos
contratos, no catálogo de capacidades, no skill de alterações em vários
arquivos, no planejador Python, no AgentCore TypeScript, na extensão VS Code e
na interface web. O lote aceita até 32 operações de criação, edição ou criação
de pastas, limita o texto total a 256 KiB, rejeita caminhos repetidos e valida
os trechos exatos antes de escrever. A aprovação apresenta o diff por arquivo.

Edições guardam backups locais e cada lote bem-sucedido recebe um identificador
persistido em `.ia-local-backups/batches`. `undo_batch` pode receber esse ID ou
usar o lote aplicado mais recente. Antes de reverter, compara hashes e verifica
se as pastas criadas receberam conteúdo adicional; se encontrar mudanças
posteriores, recusa o undo para preservar o trabalho do usuário. Erros durante
a aplicação disparam rollback best-effort e informam se alguma reversão falhou.
O lote não é uma transação atômica contra falha de energia ou encerramento
forçado do runtime.

**Validação pendente:** compilei o runtime e validei a sintaxe dos clientes e
contratos. Ainda falta exercitar aplicação, rollback por falha intermediária e
undo com e sem alterações posteriores em um workspace descartável.

### Depois das cinco prioridades

Trilhas de aprendizagem adaptativas, criatividade com variações e crítica,
dashboards de tarefas, automações locais e treinamento especializado continuam
no plano, mas não competem com essas cinco entregas no próximo ciclo.

### Média prioridade

- expansão das trilhas de aprendizagem e da avaliação de transferência;
- memória de estilo e ferramentas de criação com variações e crítica;
- dashboards de tarefas, competências e desempenho.

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
