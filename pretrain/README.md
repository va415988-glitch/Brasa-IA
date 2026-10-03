# Pré-treino do zero

Treina um modelo de linguagem **próprio**, sem pesos nem modelos externos, com a
arquitetura de `python/model.py` e o tokenizer byte-BPE de `python/tokenizer.py`.
O que entra de fora são apenas **textos abertos** (ver `sources.json`, com licenças).
O resultado é um `.safetensors` no mesmo formato que o servidor local já lê.

| Peça | Papel |
| --- | --- |
| `sources.json` | Mistura de dados: Português (FineWeb-2, Wikipédia), inglês educacional, código Python, matemática e os textos do próprio projeto |
| `prepare_data.py` | Baixa em streaming, treina o tokenizer, **prova que o tokenizer rápido é idêntico ao do projeto** e gera `train.bin`/`val.bin` |
| `train.py` | Pré-treino com Muon (+ AdamW em embeddings/normas), agenda WSD, z-loss, bf16/fp16, `torch.compile`, acumulação de gradiente, avaliação, checkpoints retomáveis e parada por tempo |
| `sft.py` | Ajuste supervisionado: perda só nas respostas, conversas do projeto + humanas abertas (OpenAssistant, Dolly), mistura de pré-treino contra esquecimento, amostras ao final. Conversas acima do contexto mantêm os turnos iniciais que cabem |
| `python/model_v2.py` | Arquitetura `decoder_transformer_v2`: RMSNorm, RoPE, SwiGLU, atenção com grupos de KV (SDPA/flash) e QK-norm opcional (`qk_norm`, ligado nos presets). Mesma interface de KV cache do `model.py`; `build_model` despacha pelo campo `architecture`, e checkpoints antigos seguem intactos |
| `build_code_sft.py` | Converte o MBPP (974 problemas Python escritos por pessoas, CC-BY 4.0) no contrato de plano JSON da bancada; só entram os que passam no parser do produto e nos próprios testes, e ficam fora os que têm o nome de uma tarefa da bancada |
| `brasa_pretrain.ipynb` | Roteiro para o Colab: monta o Drive, clona, prepara dados, treina, continua (`--init`) e faz o SFT |

## Presets

| Preset | Parâmetros (vocab 32k) | Hardware realista |
| --- | ---: | --- |
| `tiny` | 0,6 M (vocab 2k) | CPU, só para testar o pipeline |
| `small` | ~40 M, contexto 1024 | T4 (dias) / A100 (horas) |
| `base` | ~100 M, contexto 2048 | A100 ou L4 |
| `large` | ~300 M, contexto 2048 | A100 por dias; precisa de bem mais dados |

O orçamento padrão é 20 tokens por parâmetro (limitado a ~4 épocas dos dados).
Modelos pequenos seguem melhorando bem além disso. Com a agenda WSD, basta retomar o
mesmo `--out` com um `--tokens` maior **antes** do decaimento (os últimos 20% dos
passos) para estender o treino sem recomeçar.

## Receita de treino (A/B medido)

Mesmo modelo (`tiny`, 0,6 M), mesmos dados (9,5 M tokens: Wikipédia pt, código Python,
dados do projeto), 250 passos de 8 192 tokens, CPU. Perda de validação ao final:

| Receita | val_loss | ppl |
| --- | ---: | ---: |
| AdamW + cosseno, LR 6e-4 (antiga) | 6,022 | 413 |
| AdamW + cosseno, LR 2e-3 (antiga com LR ajustado) | 5,646 | 283 |
| AdamW + WSD + QK-norm + z-loss, LR 2e-3 | 5,309 | 202 |
| **Muon + WSD + QK-norm + z-loss, LR 2e-3 (padrão)** | **4,635** | **103** |

Na CPU, o Muon custou ~2,8x mais tempo por passo neste modelo minúsculo; mesmo no
mesmo tempo de relógio ele já estava à frente (5,48 no passo 100). Na GPU, e com
matrizes maiores, a ortogonalização pesa bem menos, mas isso ainda não foi medido
aqui. Um modelo de 0,6 M e 2 M tokens não garante o mesmo ganho em 40–100 M: confirme
na GPU comparando com `--optimizer adamw --schedule cosine --no-qk-norm --z-loss 0`.

## Verificado nesta máquina (CPU)

Pipeline completo com `tiny` e 23 MB de texto real (Wikipédia pt, código Python e
dados do projeto): o tokenizer rápido ficou idêntico ao do projeto, a perda caiu de
~7,6 (acaso) para 5,93 em 183 passos, e o checkpoint carregou em `build_model`,
com `prefill_with_cache` igual ao `forward`. Depois, na arquitetura v2: 4 testes
(`tests/test_model_v2.py`) provam causalidade e que o KV cache reproduz o forward completo;
o SFT rodou de ponta a ponta (a validação caiu), e o `ModelService` do servidor carregou
o checkpoint v2 e gerou por ele. **Não** foi treinado nenhum modelo útil aqui; isso exige a GPU.
O ganho da v2 sobre a v1 em qualidade e velocidade ainda não foi medido na GPU.

## Continuar um treino (`--init`)

`--init pesos.safetensors` parte de um checkpoint (a arquitetura vem dele, inclusive sem
QK-norm) com otimizador e agenda novos. Serve para seguir um treino cujo LR já decaiu.
Estados `last.pt` da receita antiga também retomam, com
`--optimizer adamw --schedule cosine --no-qk-norm --z-loss 0`.

A/B no `tiny`: partindo do mesmo checkpoint (val_loss 5,646), mais 250 passos deram
4,920 com AdamW + cosseno e **4,522 com Muon + WSD**.

## Primeira rodada no Colab (02/10/2026)

Pacote exportado do Drive e verificado em `datasets/Dataset_03-10-2026/` (fora do Git):

- dados: 1,50 bi de tokens de treino, 7,6 M de validação; hash do tokenizer confere;
- `small` (39,2 M parâmetros, receita antiga): 2 990 passos = 784 M tokens, **só ~52% dos
  dados**, com o LR já no mínimo; perplexidade 41,5 numa amostra do `val.bin`;
- SFT (projeto + OpenAssistant + Dolly): perplexidade 50,5 no mesmo `val.bin` (esquecimento);
  responde fatos simples ("Brasília"), mas entra em laço e não sabe quem é;
- bancada de programação (12 tarefas reservadas): 0/12, nenhuma no contrato JSON;
  o SFT nunca tinha visto esse formato. Daí o `build_code_sft.py`.
- SFT continuado na CPU (2 épocas, 1h33) sobre o SFT acima, com as conversas do projeto e
  os 961 planos MBPP: validação do SFT 2,646 → 1,307. Bancada completa (96 reservadas,
  2048 tokens): contrato JSON válido **0 → 22**, testes próprios 0 → 1, aprovadas **0 → 0**.
  O formato foi aprendido; a lógica não (funções erradas e asserts em laço). Custo:
  perplexidade no `val.bin` 50,5 → 56,2. O próximo salto depende de pré-treino maior na GPU
  (`--init` sobre o resto dos dados, ou o preset `base`), não de mais SFT.

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
