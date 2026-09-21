# Acervo de conhecimento

Este diretório guarda conhecimento amplo para a IA local. O acervo é separado do
corpus de comportamento e ferramentas em `python/data/combined.jsonl`.

## Camadas

- `raw/`: arquivos baixados ou fornecidos pelo usuário, preservando o original;
- `clean/`: documentos normalizados e deduplicados;
- `index/`: índice local para busca rápida;
- `manifest.jsonl`: registro de origem, licença, idioma e categoria;
- `eval/`: perguntas e respostas reservadas para medir a IA.

Conhecimento factual que muda com o tempo deve continuar no acervo e ser
consultado por busca. O modelo aprende linguagem, programação, raciocínio,
formato de resposta e uso de ferramentas; ele não precisa carregar cada fato
atual nos pesos.

## Fontes planejadas

As fontes abaixo são pontos de entrada. O downloader não baixa nada sozinho:
cada coleção precisa ser selecionada, ter sua licença registrada e passar pelo
pipeline de limpeza.

- Wikimedia dumps: enciclopédia e conhecimento geral;
- Project Gutenberg: literatura em domínio público, respeitando a jurisdição;
- Stack Exchange Data Dump: programação e perguntas técnicas, com atribuição e
  as condições da licença aplicável;
- Common Crawl: somente subconjuntos filtrados e com rastreabilidade da origem,
  pois o conteúdo rastreado pode ter termos próprios do site de origem;
- documentação oficial e manuais com licença explícita;
- material próprio do usuário.

## Uso

```bash
cargo run --manifest-path runtime/Cargo.toml --bin corpus_ingest -- corpus/raw corpus/clean/knowledge.jsonl
cargo run --manifest-path runtime/Cargo.toml --bin corpus_index -- corpus/clean/knowledge.jsonl corpus/index/knowledge.json
cargo run --manifest-path runtime/Cargo.toml --bin corpus_mix -- corpus/clean/knowledge.jsonl python/data/combined.jsonl model/train_corpus.jsonl
cargo run --manifest-path runtime/Cargo.toml --bin tokenize_corpus -- model/train_corpus.jsonl model/tokenizer.json model/train_tokens.bin
```

O ingestador, indexador, misturador e tokenizador Rust são o caminho padrão
porque fazem a parte repetitiva com baixo uso de memória. Os scripts Python
continuam disponíveis como referência de validação e comparação.

Para um arquivo individual, o mesmo comando aceita `--input arquivo.txt`. Os
formatos aceitos inicialmente são `.txt`, `.md`, `.html`, `.json` e `.jsonl`.

O pipeline não envia dados para serviços externos e não depende de Ollama.
