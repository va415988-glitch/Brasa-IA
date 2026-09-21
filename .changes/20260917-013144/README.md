# IA Local do Zero

Projeto experimental para construir uma IA local própria, com foco em:

- conversa em português;
- assistência de programação;
- pesquisa na internet;
- uso confiável de ferramentas;
- execução eficiente no computador local.

## Arquitetura inicial

- `runtime/`: executor de ferramentas em Rust;
- `contracts/`: contratos JSON das ferramentas;
- `python/`: treinamento, dados e avaliação do modelo;
- `corpus/`: acervo local de conhecimento, manifestos, índice e avaliação;
- `tests/`: casos de avaliação de uso de ferramentas.

O primeiro marco é validar o ciclo `pedido -> ferramenta -> resultado -> resposta` antes de treinar o modelo.

## Expansão de conhecimento

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

Toda requisição deve terminar em até 30 segundos. Esse é o limite absoluto: respostas acima disso são consideradas falha de desempenho. Cada ferramenta deverá ter um timeout próprio e o orçamento restante será propagado para as etapas seguintes.

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

No painel **Workspace**, informe um caminho absoluto para selecionar qualquer
diretório local existente como raiz de trabalho. Depois disso, listar, ler,
buscar, criar e editar usam essa pasta; caminhos relativos continuam confinados
à raiz selecionada.

O processo lê uma chamada JSON por linha na entrada padrão e devolve um resultado JSON por linha.

Exemplo:

```json
{"tool":"search_web","arguments":{"query":"Rust async runtime"}}
```
