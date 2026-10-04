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
| `build_agent_sft.py` | Gera `datasets/agent_sft_v1`: decisões no protocolo de `python/cognitive_dialogue.py` (answer/consult/blocked) com ferramentas reais do runtime em Rust sobre 105 repositórios (`agent_sft_repos.txt`), pesquisa com artigos reais da Wikipédia, respostas humanas e diálogos escritos. Todo alvo passa por `validate_decision` |
| `agent_sft_authored.txt` | Diálogos escritos por LLM (Claude), em texto simples para revisão; entram rotulados `authored-llm-v1` |
| `eval_agent_sft.py` | Mede um checkpoint no held-out agêntico: decisões válidas, decisão certa, ferramenta e argumento, evidência e sobreposição da resposta. `--backend server` (CPU, caminho do servidor) ou `--backend direct` (GPU no Colab; 37 de 39 saídas idênticas às do servidor) |
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

## SFT agêntico (`datasets/agent_sft_v1`)

O modelo aprende a decidir como agente no formato que o servidor já usa: recebe pedido,
histórico, observações (`obs-N`) e catálogo de ferramentas, e devolve
`{"decision", "text", "gap", "evidence_ids", "tool_call"}`. Um exemplo por decisão, no estilo
`compact-v1` (o `sft.py` grava `cognitive_prompt_style` no checkpoint).

| Parte | Origem | Decisões |
| --- | --- | ---: |
| Repositórios: visão geral, onde está definido, explicar arquivo, listar, testes, caminho errado, busca vazia, injeção em README, sem ferramentas | runtime real sobre 105 repositórios | ~5,2 mil |
| Pesquisa: buscar, abrir a fonte, responder citando; sem internet, bloquear | Wikipédia pt real (título, URL, texto) | ~5,6 mil |
| Responder sem ferramenta | OpenAssistant 2 e Dolly (humanos) | ~3,9 mil |
| Conversa natural | diálogos escritos por LLM, rotulados | 273 |

Regras que tornam a política aprendível (sem elas o modelo recebia sinais contraditórios):
pesquisa só quando a pessoa pede ou exige fonte; resposta pelo trecho da busca só quando ela
pede resposta curta; respostas usam só fatos visíveis nas observações depois do corte do
`build_frame`; `.env`, chaves e credenciais nunca são lidos. Treino e held-out são separados
por repositório e por artigo.

Prova de que é aprendível (CPU, `small` de 39 M, ~1 100 exemplos balanceados por tipo,
2 épocas, `eval_agent_sft.py` no held-out):

| Tipo | 1ª rodada | 2ª rodada (com as regras acima) |
| --- | ---: | ---: |
| Decisões válidas (total) | 83% | 84% |
| Decisão certa (total) | 80% | 80% |
| Ferramenta e argumento certos | 68% | 79% |
| Pesquisa web: decisão certa | 25% | 62% |
| Resposta direta: decisão certa | 38% | 62% |
| Repositórios: visão geral / onde está | 88% | 100% |
| Diálogo escrito por LLM: decisão certa | 25% | 0% |

O último caso mostra o risco do conjunto: o modelo pequeno cola frases-modelo repetidas das
trajetórias em perguntas livres. Por isso a versão final dobra as respostas humanas diretas
(OpenAssistant 2 e Dolly, variadas) e o SFT completo mistura conversas humanas inteiras.
Esses testes usam o modelo de 39 M na CPU; o `base` (100 M) treinado na GPU tem mais capacidade para escapar das frases-modelo, e o mesmo avaliador mede isso.

Para regenerar: `cargo build --release` em `runtime/`, depois
`python pretrain/build_agent_sft.py --clone pretrain/agent_sft_repos.txt --workspaces <pasta>`.

**Integração pendente no produto.** O treino sozinho não muda o chat: (1) o roteador por regex
de `python/dialogue.py` manda pedidos como "O que consegue me dizer do sistema…" para a rota de
conversa sem ferramentas; (2) o caminho cognitivo só roda quando o AgentCore envia
`objective: conversation` com o catálogo; (3) nesse caminho, `model/cognitive-router/active.json`
(classificador de caracteres, 595 linhas) tem prioridade sobre o checkpoint.

## Segunda rodada no Colab (03/10/2026): `base` com a receita nova

A100-SXM4-80GB, torch 2.11, **~144 mil tokens/s** estável (dados lidos do Drive sem gargalo).
Perda de validação: passo 250 → 5,327 · 500 → 4,110 · 1000 → 3,746 · 1500 → 3,591 · 2000 → 3,505 ·
2500 → 3,451 · 2750 → **3,422 (ppl 30,6)**, ainda no platô da WSD (sem o decaimento final). Para comparação,
o `small` antigo completo terminou em ppl 41,5.

O servidor morreu no passo ~2950, aos 89 min: o Colab encerra sessões após ~90 min sem interação, e rodar
código pela extensão do VS Code não conta como interação. Último checkpoint salvo: passo 2500. Desde então
o notebook treina em blocos de 78 min (rodar a célula de novo retoma) e salva a cada 250 passos.

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
