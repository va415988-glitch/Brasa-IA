# Importar datasets do Hugging Face e avaliar o modelo

Este fluxo importa o snapshot completo de um dataset Hugging Face para quarentena, inclusive arquivos de código, configuração e dados, e oferece uma bateria de até 500 casos para o servidor local. O importador aceita snapshots de até 50 GB, baixa em paralelo com retomada e não promove conteúdo ao treinamento automaticamente.

## 1. Importar um dataset

Na raiz do repositório:

```bash
./.venv/bin/python scripts/import_hf_dataset.py
```

O script solicita a URL, consulta os metadados compactos do Hub e informa a licença e o tamanho estimado do snapshot. A resposta padrão não baixa os arquivos; confirme para baixar o repositório completo. Também aceita o identificador `owner/dataset`. A listagem de metadados não carrega a lista inteira de arquivos na resposta inicial, então repositórios grandes não estouram um limite de 2 MiB.

Para iniciar sem a pergunta de confirmação:

```bash
./.venv/bin/python scripts/import_hf_dataset.py --url owner/dataset --download
```

Para auditar só os metadados:

```bash
./.venv/bin/python scripts/import_hf_dataset.py --url owner/dataset --audit-only
```

Instale o cliente oficial necessário para paginação, retomada e downloads paralelos: `python -m pip install -r python/requirements-hf-import.txt`. Datasets privados ou gated podem usar um token definido no ambiente, sem gravá-lo no script:

```bash
export HF_TOKEN='seu-token'
./.venv/bin/python scripts/import_hf_dataset.py
```

Os arquivos do snapshot completo ficam em `corpus/quarantine/huggingface/`, preservando a árvore original. O download usa múltiplos workers e pode ser retomado ao executar de novo após uma interrupção. Não há limite por arquivo nem por quantidade de arquivos; a capacidade de snapshot configurada é de 50 GB. Quando o importador reconhecer pares simples de pergunta e resposta, ele também imprime o caminho de `evaluation.jsonl`, limitado a 500 pares. O conteúdo permanece em quarentena e não é executado.

O snapshot é preservado sem filtrar extensões. A extração auxiliar lê JSONL, JSON, CSV, TSV, Parquet e arquivos textuais de código/configuração; Parquet requer `pyarrow`. Datasets em formato de benchmark executável, como ProgramDistill, não são convertidos automaticamente em pares pergunta/resposta. Eles ficam completos em quarentena, e o avaliador de 500 questões usa a bateria sintética.

## 2. Iniciar o servidor do modelo

O servidor atual usa a porta `3001` por padrão:

```bash
./.venv/bin/python python/model_server.py --port 3001
```

O avaliador consulta `/health` e envia prompts sequencialmente a `/generate`. Por privacidade, recusa endpoints fora de `localhost`/loopback: amostras do dataset não são enviadas para serviços remotos. Chamadas de ferramentas são avaliadas como propostas e não são executadas pelo avaliador.

## 3. Rodar os 500 casos

Com as referências importadas:

```bash
cargo run --manifest-path runtime/Cargo.toml --bin model_intelligence_eval -- \
  --dataset corpus/quarantine/huggingface/<id>/evaluation.jsonl \
  --count 500 \
  --output model/intelligence_eval_report.json
```

O argumento `--dataset` é opcional. Sem ele, o avaliador roda os 500 casos sintéticos. Com menos de 500 referências válidas, usa todas as referências encontradas e completa o restante com casos sintéticos. É possível limitar a execução durante um teste manual:

```bash
cargo run --manifest-path runtime/Cargo.toml --bin model_intelligence_eval -- --count 10 --dry-run
```

`--dry-run` informa a composição sem acessar o servidor. `IA_LOCAL_MODEL_URL` pode substituir a URL padrão `http://127.0.0.1:3001/generate`; `--url` tem precedência.

## Composição da bateria sintética

- 100 de aritmética (adição, subtração, multiplicação e divisão inteira)
- 50 de sequências
- 50 de dedução lógica
- 50 de rastreamento de código
- 50 de extração de informação do contexto
- 50 de seguimento de instrução com resposta exata
- 50 de saída JSON validável
- 50 de conhecimento geral estável
- 50 de roteamento de ferramenta sem executar a ação

## Relatório e interpretação

O relatório JSON inclui taxa de aprovação, média dos scores por caso, resultados por categoria, backend observado, latência p50/p95/máxima e a resposta registrada por caso. Para referências HF, calcula token F1 e considera `0,5` como limiar diagnóstico de aprovação. A saída JSON usa acurácia de campos; as demais categorias usam comparação exata, numérica ou de aliases.

A nota mede o resultado nesta bateria, neste checkpoint e nesta configuração. Não é QI nem uma medida universal de inteligência. Os resultados de backend são separados porque o servidor pode responder por um backend fallback quando não carregar o modelo neural. Três falhas HTTP consecutivas encerram a execução e o relatório marca a execução parcial.
