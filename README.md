# IA Local do Zero

Projeto experimental para construir uma IA local própria, com foco em:

- conversa em português;
- assistência de programação;
- pesquisa na internet;
- uso confiável de ferramentas;
- execução eficiente no computador local.

O perfil em [`SYSTEM-PROMPT.md`](SYSTEM-PROMPT.md) orienta engenharia de software
com rigor sênior e criação além do código (textos, roteiros, campanhas e conceitos).
O worker lê esse arquivo ao montar o contexto e as instruções de geração; em
checkpoints com janela menor que 4096 tokens, usa um resumo compacto. Uma parte
da janela é reservada ao perfil quando o contexto excede o limite. Essas
instruções não treinam os pesos nem garantem competência sênior: os resultados
continuam sujeitos à capacidade do checkpoint e às verificações existentes.

## Arquitetura inicial

- `runtime/`: executor de ferramentas em Rust;
- `contracts/`: contratos JSON das ferramentas;
- `python/`: treinamento, dados e avaliação do modelo;
- `corpus/`: acervo local de conhecimento, manifestos, índice e avaliação;
- `tests/`: casos de avaliação de uso de ferramentas.

O primeiro marco é validar o ciclo `pedido -> ferramenta -> resultado -> resposta` antes de treinar o modelo.

## Treinamento de engenharia e criatividade

Foram executadas duas rodadas supervisionadas, somando 1.000 passos, com
currículo autoral e avaliação reservada. Os pesos candidatos ficaram
experimentais: a perda diminuiu, mas a geração livre reprovou e o modelo ativo
foi preservado. Resultados, limitações e reprodução estão no
[relatório do treinamento](model/training/senior-creative-v1/RELATORIO.md).

Uma nova tentativa corrigiu a inicialização e executou mais 600 passos, com
parada por estagnação. O candidato também reprovou em pedidos inéditos; a
promoção agora exige evidência comportamental vinculada ao artefato. Consulte
o [relatório da tentativa de aprovação](model/training/senior-creative-v2/RELATORIO.md).

## Expansão de conhecimento

Na interface, envie `aprenda <tema>` — por exemplo, `aprenda C++`,
`aprenda Node.js` ou `aprenda NovaFlux` — para iniciar uma pesquisa por
linguagem, framework ou conceito. A tarefa roda em segundo plano e mostra barra de progresso e logs de busca,
validação e indexação. As páginas aceitas entram em `corpus/clean/knowledge.jsonl`
e o índice `corpus/index/knowledge.json` é recarregado pelo chat sem reiniciar.
O comando acrescenta fontes recuperáveis ao acervo; não modifica os pesos do
modelo. Os links são registrados e páginas sem licença conhecida recebem
`unknown-review-required`. A coleta é limitada a duas pesquisas e até seis
páginas por tema. Resultados cujo título/URL não identifica o tema são rejeitados;
perguntas posteriores podem recuperar trechos com a fonte. Isso não garante que o
checkpoint atual consiga gerar uma implementação completa. Vídeos do YouTube
ainda não são transcritos ou analisados.

Para dar mais tempo ao modelo em perguntas difíceis, o chat aceita até
15 minutos na interface e na ligação com o worker. A geração usa
exclusivamente o checkpoint próprio em `model/checkpoints/` e o tokenizer local.
A interface mostra etapas observáveis da operação, não o raciocínio interno do
modelo. Respostas simples da memória local continuam disponíveis como fallback
quando a geração própria reprova no controle de qualidade.

O projeto agora separa duas coisas: comportamento do assistente, em
`python/data/combined.jsonl`, e conhecimento factual, em `corpus/`. O acervo
passa por normalização, divisão em trechos, deduplicação por SHA-256 e índice
lexical local. Assim, fatos podem ser recuperados rapidamente sem transformar
um modelo pequeno em um depósito desatualizado de texto.

Para adicionar material local:

```bash
cargo run --manifest-path runtime/Cargo.toml --bin corpus_ingest -- corpus/raw corpus/clean/knowledge.jsonl
.venv/bin/python python/build_knowledge_index.py
.venv/bin/python python/retrieve_knowledge.py "sua pergunta"
```

O lote de tokens oficial é gerado pelo Rust depois da mistura do corpus:

```bash
cargo run --manifest-path runtime/Cargo.toml --bin corpus_mix -- corpus/clean/knowledge.jsonl python/data/combined.jsonl model/train_corpus.jsonl
TOKEN_REPEAT=16 cargo run --manifest-path runtime/Cargo.toml --bin tokenize_corpus -- model/train_corpus.jsonl model/tokenizer.json model/train_tokens.bin
```

Fontes externas entram em etapas e com licença registrada. O plano detalhado
está em [`corpus/README.md`](corpus/README.md). Coleções grandes como dumps
enciclopédicos e Common Crawl serão filtradas antes de qualquer download amplo;
o conteúdo rastreado pode ter condições próprias no site de origem.

## Regra de desempenho

Ferramentas comuns preservam o alvo de 10 segundos. A conversa com o modelo pode levar até 15 minutos no servidor e na interface. O aprendizado por tema roda como tarefa assíncrona e informa progresso separadamente.

## Ramificação futura: criador de IAs pessoais

Depois que o núcleo estiver funcionando, o projeto poderá virar um programa ou serviço que ajuda cada usuário a criar sua própria IA. O sistema deverá:

1. identificar objetivos, tarefas e preferências do usuário;
2. avaliar CPU, memória, GPU, armazenamento e sistema operacional;
3. recomendar uma arquitetura e um tamanho de modelo compatíveis;
4. montar ferramentas, permissões, memória e interfaces adequadas;
5. preparar dados de treinamento e avaliação;
6. treinar, adaptar ou destilar um modelo conforme o orçamento disponível;
7. entregar uma IA local otimizada para aquele hardware;
8. medir desempenho, qualidade e consumo antes de publicar a configuração.

Essa ideia fica fora do primeiro ciclo, mas influencia as decisões atuais: os componentes devem ser modulares, mensuráveis e independentes do hardware específico desta máquina.

## Executar o runtime

```bash
cargo run --manifest-path runtime/Cargo.toml
```

Para iniciar todos os servidores com um único comando:

```bash
./start.sh
```

O script compila o runtime, inicia a interface em `127.0.0.1:3000` e deixa o
worker do modelo em `127.0.0.1:3101` sob gerenciamento do runtime. Use `Ctrl+C`
para encerrar o grupo inteiro.

Durante o uso, a interface consulta `/api/activity` e mostra a etapa atual, o
estado de erro ou conclusão e o tempo da requisição. O runtime também registra
essas etapas no terminal com o prefixo `[atividade]`, facilitando distinguir
uma operação em andamento de uma falha real.

O contrato novo de eventos operacionais está disponível em
`GET /api/events?after_seq=0`. Ele devolve eventos `agent-event/v2` com sequência,
sessão, tarefa, trace, fase, estado, progresso mensurável e ator. O frontend já
usa esse endpoint e mantém `/api/activity` apenas como compatibilidade durante
a migração. `session_id` e `task_id` podem ser usados para filtrar uma operação
específica sem misturar os logs de conversas diferentes.

Resultados de edição, criação e reparo também carregam um artefato estruturado
com caminho, linguagem, hashes, contagem de linhas e diff `line-v1`. A interface
renderiza esse artefato dentro da conversa, permite abrir o arquivo no Workspace,
copiar o diff ou expandi-lo em foco. Falhas de verificação aparecem como
diagnóstico com saída padrão e erro padrão preservados, e esses objetos seguem
no histórico local da tarefa.

## API local

O contrato está disponível em `GET /api/openapi.json`. Clientes podem usar
`GET /api/health`, `POST /api/v1/tools/call` e a API especializada
`POST /api/v1/research`:

```bash
curl -s http://127.0.0.1:3000/api/v1/research \
  -H 'content-type: application/json' \
  -d '{"query":"Rust ownership","max_results":2}'
```

O resultado traz uma resposta fundamentada, `citation_ids` e as páginas que
foram realmente abertas. Para preparar o acervo local, acrescente
`"save_to_corpus":true`; a etapa de ingestão e indexação continua separada
para que pesquisa não altere o conhecimento usado pelo modelo sem revisão.

No painel **Workspace**, informe um caminho absoluto para selecionar qualquer
diretório local existente como raiz de trabalho. Depois disso, listar, ler,
buscar, criar e editar usam essa pasta; caminhos relativos continuam confinados
à raiz selecionada.

Para começar uma tarefa de programação, use `inspect_project` ou escreva
**Analise meu projeto**. A ferramenta identifica manifestos, pontos de entrada,
arquivos de teste e verificações disponíveis sem modificar arquivos. O fluxo
seguinte deve ler os arquivos relevantes, propor uma alteração pequena e rodar
os testes antes de editar.

O processo lê uma chamada JSON por linha na entrada padrão e devolve um resultado JSON por linha.

Exemplo:

```json
{"tool":"search_web","arguments":{"query":"Rust async runtime"}}
```
