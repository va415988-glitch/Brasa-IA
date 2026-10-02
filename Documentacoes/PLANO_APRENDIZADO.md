# Plano de aprendizado da IA local

Ensinar a IA não será um único treinamento. Será uma sequência de camadas com
critérios de aprovação. Cada camada precisa melhorar uma capacidade observável
sem degradar latência, segurança ou factualidade.

## Arquitetura de responsabilidades

### Rust

Rust será usado no caminho de produção dos dados e da execução:

- leitura concorrente de grandes JSONL e dumps;
- normalização, chunking, deduplicação e hashes;
- validação de manifestos e licenças;
- construção de índices e lotes de tokens;
- recuperação local com limite de tempo;
- runtime, ferramentas, permissões, cache e métricas.

O primeiro componente dessa migração é `runtime/src/bin/corpus_audit.rs`. Ele
audita JSONL rapidamente, conta caracteres, detecta registros inválidos e
duplicatas. A próxima versão deverá mover a transformação de `ingest_corpus.py`
para Rust, mantendo o formato de saída compatível.

### Python

Python continuará concentrado em pesquisa e treinamento:

- tokenizer e experimentos de arquitetura;
- otimização do modelo e checkpoints;
- avaliação científica e comparação de configurações;
- scripts pequenos de aquisição, sempre com origem registrada.

Quando o modelo tiver um backend adequado para CPU, a inferência também poderá
migrar para Rust. Não vamos reescrever o treinamento em Rust antes de medir um
ganho real; o gargalo atual é tamanho e qualidade do corpus, não a linguagem do
script que inicia o otimizador.

## Fases com portas de aprovação

### Fase 0 — fundação

Corpus de comportamento, ferramentas, abstinência, workspace e limite de 30 s.
Já existe e tem testes básicos.

**Aprovar quando:** chamadas de ferramenta são válidas, caminhos ficam
confinados e perguntas desconhecidas não geram respostas inventadas.

### Fase 1 — acervo confiável

Adicionar programação, ciência, matemática, história, cultura, literatura e
conhecimento cotidiano em lotes pequenos. Cada documento precisa ter origem,
idioma, licença, categoria, hash e data de coleta.

**Aprovar quando:** deduplicação, busca, citações e atualização incremental
funcionarem sem ultrapassar 30 s.

### Fase 2 — recuperação e respostas

Usar o índice local para recuperar evidências, selecionar trechos relevantes,
mostrar a origem e distinguir conhecimento estável de informação atual.

**Aprovar quando:** a avaliação factual superar o baseline e o sistema recusar
perguntas sem evidência.

### Fase 3 — treinamento balanceado

Misturar texto geral, programação, diálogos, raciocínio, exemplos de ferramentas
e respostas com fontes. O conjunto de avaliação fica separado. Não repetir o
mesmo corpus para mascarar falta de dados, exceto em smoke tests explícitos.

**Aprovar quando:** perda de validação melhorar, respostas conhecidas forem
corretas e o modelo não perder o comportamento das ferramentas.

### Fase 4 — otimização de hardware

Medir CPU, RAM, tamanho do contexto, throughput, latência p50/p95 e memória.
Depois testar quantização, cache e kernels mais eficientes. O alvo é manter
cada requisição abaixo de 30 s neste computador.

### Fase 5 — adaptação avançada

Só depois do baseline próprio estar medido, avaliar destilação ou adaptação de
modelos maiores. Essa fase continua independente de Ollama e só entra se houver
um plano de gargalo comprovado.

## Mistura inicial de dados

O corpus de treino deverá ser balanceado, aproximadamente:

- 30% linguagem e conhecimento geral;
- 25% programação e engenharia;
- 15% diálogo e explicação;
- 15% ferramentas, fontes e workspace;
- 10% raciocínio e resolução de problemas;
- 5% segurança, incerteza e recusa correta.

Esses percentuais são um ponto de partida para medir, não uma regra permanente.
Cada lote será acompanhado por avaliação antes e depois.

## Prioridade de programação

A programação passa a ser o primeiro domínio de expansão. A ordem de coleta é
Python e Rust, fundamentos de HTTP e web, Git, bancos de dados, testes,
depuração, segurança e desempenho. Documentação oficial e exemplos pequenos
entram com origem e licença; respostas sintéticas só entram quando forem
revisadas e acompanhadas por uma pergunta de avaliação.

O conjunto reservado está em `corpus/eval/programming_questions.jsonl`. Ele
separa iniciante, intermediário e avançado e exige conceitos verificáveis na
resposta. A IA não será considerada boa em programação apenas por definir
termos: precisará explicar decisões, apontar riscos, escrever código pequeno e
analisar projetos reais sem inventar o que não leu.

## Métricas obrigatórias

- qualidade factual com perguntas reservadas;
- compilação e correção de exemplos de código;
- precisão das chamadas de ferramentas;
- taxa de respostas com fonte válida;
- taxa de abstinência correta;
- latência p50/p95 e falhas acima de 30 s;
- RAM, armazenamento e tamanho do checkpoint;
- regressões em português, acentos e código.

O auditor, o ingestador, o indexador, o misturador e o tokenizador inicial em
Rust já existem. O tokenizador grava o mesmo formato little-endian usado pelo
treinamento atual. Ele só será promovido ao fluxo padrão depois da comparação
byte a byte com o Python em corpus de comportamento e no corpus misturado.

Essa comparação foi concluída: 2.028 tokens do corpus de comportamento foram
iguais byte a byte. O arquivo oficial agora foi gerado em Rust a partir de 91
registros misturados, com 16 repetições controladas e 1.148.864 tokens. O
checkpoint existente permanece separado até a avaliação do próximo treinamento.

O primeiro candidato (`compact-02-mixed`) não passou na avaliação de geração:
produziu marcadores quebrados e respostas incoerentes em quatro perguntas. Ele
foi mantido apenas como artefato de comparação; o baseline continua ativo.

O segundo candidato (`compact-03-validated`) teve uma divisão real entre 82
registros de treino e 9 de validação, mas também falhou na geração livre com
marcadores de controle e texto corrompido. A próxima rodada terá testes de
saída como porta obrigatória, além da perda de validação.

A porta automática foi implementada em `python/evaluate_checkpoint.py` e
reprovou tanto o baseline quanto o candidato nas quatro perguntas livres. Isso
confirma que o modelo pequeno ainda não deve responder diretamente; o runtime
usa a memória curada, o acervo local e o quality gate enquanto o treinamento
melhora.

O `compact-04-mixed-tokenizer` também foi reprovado. O tokenizer novo e a
validação reduziram problemas de representação, mas a geração continuou com
repetições e palavras corrompidas. A próxima fase precisa aumentar exemplos de
diálogo e ajustar a arquitetura/objetivo antes de outra rodada longa de CPU.

Foi adicionada uma regra de relevância ao recuperador: uma palavra temática
isolada não pode sustentar uma resposta. Assim, “Quando o Rust foi criado?”
deixa de recuperar um parágrafo sobre ownership e entra no quality gate até o
roteador mandar pesquisar uma fonte adequada.

O primeiro roteador de intenção também foi integrado ao worker e à interface.
Ele distingue `knowledge`, `programming`, `current-research`, `web-research`,
`workspace`, `conversation` e `unknown`. Pesquisa atual e workspace têm
prioridade sobre memória local, e a intenção aparece no metadado da mensagem.

## Rodada de evolução — contexto e conversa

O worker agora possui respostas curadas para identidade, capacidades,
saudações e encerramentos naturais. Perguntas curtas de continuação, como
“E em Rust?”, herdam o assunto da mensagem anterior antes do roteamento e da
recuperação local. O caminho de geração livre também foi reativado, mas só
libera uma resposta quando passa por filtros de marcadores de controle,
repetição, tamanho e relevância; caso contrário, o quality gate permanece como
barreira. A primeira validação HTTP confirmou respostas de 4–8 ms para casos
curados e 210 ms para uma tentativa experimental bloqueada.

Na etapa seguinte, o corpus recebeu um gerador supervisionado local em
`python/build_instruction_dataset.py`. Ele combina os 28 diálogos originais
com exemplos derivados do acervo, sem consulta externa. O pipeline Rust gerou
o corpus misturado e os arquivos `model/train_tokens_augmented_v2.bin` e
`model/val_tokens_augmented_v2.bin`. Uma rodada de smoke test caiu de perda
154 para 126 em cinco passos; a rodada longa `compact-06-augmented-v2` está
em treinamento e o baseline continua ativo até a aprovação.

O treino longo registrou, até o passo 200, perda de treino 5,63 e validação
5,99 em aproximadamente 233 s. Ainda não é critério de promoção: a geração
será avaliada quando a rodada terminar. O script de treino também passou a
salvar checkpoints parciais nas avaliações, permitindo recuperar uma rodada
interrompida sem perder todo o trabalho.

O `compact-06-augmented-v2` terminou com perda de validação 3,9507, mas a
avaliação de geração ficou em 0/4. Ele produziu saída vazia, marcadores de
controle e repetição; portanto foi reprovado e não substituiu o baseline.
Perda menor, sozinha, não é suficiente para liberar respostas livres.
