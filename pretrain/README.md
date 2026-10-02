# Pré-treino do zero

Treina um modelo de linguagem **próprio**, sem pesos nem modelos externos, com a
arquitetura de `python/model.py` e o tokenizer byte-BPE de `python/tokenizer.py`.
O que entra de fora são apenas **textos abertos** (ver `sources.json`, com licenças).
O resultado é um `.safetensors` no mesmo formato que o servidor local já lê.

| Peça | Papel |
| --- | --- |
| `sources.json` | Mistura de dados: Português (FineWeb-2, Wikipédia), inglês educacional, código Python, matemática e os textos do próprio projeto |
| `prepare_data.py` | Baixa em streaming, treina o tokenizer, **prova que o tokenizer rápido é idêntico ao do projeto** e gera `train.bin`/`val.bin` |
| `train.py` | Pré-treino com bf16/fp16, `torch.compile`, acumulação de gradiente, cosseno com aquecimento, avaliação, checkpoints retomáveis e parada por tempo |
| `sft.py` | Ajuste supervisionado: perda só nas respostas, conversas do projeto + humanas abertas (OpenAssistant, Dolly), mistura de pré-treino contra esquecimento, amostras ao final |
| `python/model_v2.py` | Arquitetura `decoder_transformer_v2`: RMSNorm, RoPE, SwiGLU, atenção com grupos de KV (SDPA/flash). Mesma interface de KV cache do `model.py`; `build_model` despacha pelo campo `architecture`, e checkpoints antigos seguem intactos |
| `brasa_pretrain.ipynb` | Roteiro para o Colab: monta o Drive, clona, prepara dados e treina |

## Presets

| Preset | Parâmetros (vocab 32k) | Hardware realista |
| --- | ---: | --- |
| `tiny` | 0,6 M (vocab 2k) | CPU, só para testar o pipeline |
| `small` | ~40 M, contexto 1024 | T4 (dias) / A100 (horas) |
| `base` | ~100 M, contexto 2048 | A100 ou L4 |
| `large` | ~300 M, contexto 2048 | A100 por dias; precisa de bem mais dados |

O orçamento padrão é 20 tokens por parâmetro (limitado a ~4 épocas dos dados).

## Verificado nesta máquina (CPU)

Pipeline completo com `tiny` e 23 MB de texto real (Wikipédia pt, código Python e
dados do projeto): o tokenizer rápido ficou idêntico ao do projeto, a perda caiu de
~7,6 (acaso) para 5,93 em 183 passos, e o checkpoint carregou em `build_model`,
com `prefill_with_cache` igual ao `forward`. Depois, na arquitetura v2: 4 testes
(`tests/test_model_v2.py`) provam causalidade e que o KV cache reproduz o forward completo;
o SFT rodou de ponta a ponta (a validação caiu), e o `ModelService` do servidor carregou
o checkpoint v2 e gerou por ele. **Não** foi treinado nenhum modelo útil aqui; isso exige a GPU.
O ganho da v2 sobre a v1 em qualidade e velocidade ainda não foi medido na GPU.

## Limites honestos

- Pré-treino só ensina a língua, fatos e padrões de código. **Ele não conversa nem
  segue instruções**: isso é o `sft.py`. Depois, a bancada de programação
  (`QUALIFICACAO_PROGRAMACAO_2026-10-02.md`) mede se serve; só os dados de treino
  precisam ser disjuntos das 192 tarefas dela.
- OpenAssistant e Dolly são escritos por pessoas, mas são poucos milhares de conversas
  em português: o SFT dará o formato de conversa, não conhecimento nem habilidade de código.
- Um modelo de 42–110 M parâmetros tem capacidade limitada. Espere texto coerente e
  código simples, não o nível de um modelo de bilhões de parâmetros.
- O contexto de treino é o do preset (RoPE não extrapola sozinho; estender exige validação).
- Os pesos não ativam sozinhos o chat: o servidor exige as provas do projeto.
- `codeparrot-clean` mistura licenças do GitHub; revise antes de redistribuir pesos.
