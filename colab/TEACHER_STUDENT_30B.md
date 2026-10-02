# Professor 30B no Colab, Brasa independente no computador

## Papéis

**Professor no Colab:** usar um modelo de código pré-treinado de pelo menos 30B para propor planos, chamadas de ferramenta e arquivos para tarefas de programação. Ele ajuda a criar candidatos de treino e a diagnosticar falhas do agente. Seu uso é offline, por lotes; o editor não o consulta durante o uso normal.

**Aluno local:** modelo menor, escolhido após medir RAM, velocidade e qualidade no computador do usuário. Ele recebe exemplos revisados do professor e de execuções reais. O checkpoint atual de 2 camadas e dimensão 128 não deve ser apresentado como substituto funcional de um modelo de código pré-treinado apenas por aceitar uma janela longa em runtime.

**Runtime local:** mantém as ferramentas, inspeção de arquivos, autorização de mudanças, validação de caminhos, aplicação das operações, verificação executada e registro de episódios. Esses gates não dependem do professor.

Assim, a Brasa deve continuar funcionando quando o Colab estiver desligado. O professor gera dados para melhorar versões futuras; não é uma dependência de produção.

## Primeiro professor candidato

O [Qwen3-Coder-30B-A3B-Instruct](https://huggingface.co/Qwen/Qwen3-Coder-30B-A3B-Instruct) é um candidato inicial para avaliação, não uma escolha definitiva. O cartão oficial declara licença Apache-2.0, 30,5 bilhões de parâmetros totais, 3,3 bilhões ativos e suporte a chamadas de ferramenta. “3,3B ativos” descreve o cálculo por token; os pesos totais ainda precisam ser carregados ou armazenados. Em BF16, apenas os pesos representam aproximadamente 61 GB; em 4 bits, o mínimo aritmético é aproximadamente 15,3 GB, além de metadados, ativações e cache de atenção. A [documentação do Transformers](https://huggingface.co/docs/transformers/en/quantization/bitsandbytes) descreve o carregamento em 4 bits.

O Colab Pro não garante sempre o mesmo acelerador, memória ou duração de sessão; a [FAQ do Colab](https://research.google.com/colaboratory/faq.html) informa que esses recursos variam. Antes de carregar o modelo, a sessão deve medir GPU, VRAM, RAM e espaço temporário. Usar cache de pesos em `/content`, mantendo no Drive somente entradas, saídas, decisões de curadoria e manifestos. Se o runtime não comportar o modelo, interromper sem mudar silenciosamente para um professor menor.

Uma A100 com 80 GB pode ser candidata a carregar BF16 com contexto inicial curto; uma A100 com 40 GB exigirá quantização ou outra estratégia de carga. Isso é uma estimativa de capacidade, não uma garantia de execução: o cache temporário precisa comportar o download e a inferência exige memória além dos pesos. Começar com contexto pequeno e medir pico de VRAM antes de gerar um lote grande.

## Fluxo de dados

1. Selecionar tarefas de coleta distintas de `corpus/eval/`; manter o conjunto reservado intocado.
2. Enviar ao professor apenas contexto necessário e registrar versão/revisão do modelo, hash do pedido, parâmetros de geração e resposta bruta. Guardar em `datasets/raw/teacher/` no Drive.
3. Validar estrutura dos planos e caminhos. Propostas inválidas ou repetitivas ficam em quarentena. Um texto do professor afirmando “testes passaram” não conta como verificação.
4. Executar propostas em workspaces isolados com os gates do runtime. Registrar diff aplicado, arquivos finais, comando, código de saída e saída completa do verificador.
5. Revisar qualidade, segurança, licença, privacidade e semelhança com avaliação. Só depois selecionar exemplos para treino do aluno.
6. Comparar versões do aluno em tarefas reservadas novas, medindo conclusão real com escrita e verificação, além de tempo e consumo local de memória.

## Critério para o aluno local

O computador observado tem 22 GiB de RAM e vídeo Intel Iris Xe, sem GPU NVIDIA detectada. O tamanho do aluno precisa ser escolhido por medição de latência e memória, não por uma meta de parâmetros. O modelo de 30B fica no Colab. O aluno deve ser capaz de produzir uma proposta de múltiplos arquivos válida, tolerar retorno de ferramenta e corrigir falhas de verificação; o runtime só declara conclusão após observar os efeitos.

O próximo artefato operacional deve ser um notebook de geração **offline** com o professor 30B. Ele começa medindo os recursos da sessão, carrega o modelo com quantização quando necessário e grava apenas candidatos com procedência no Drive. Nenhum candidato é rotulado como sucesso de programação sem execução e verificação observadas.

## Notebook operacional v001

`colab/professor_qwen3_coder_v001.ipynb` implementa a primeira sessão do professor: monta o Drive, registra GPU/VRAM, interrompe antes do download se não houver CUDA ou 40 GiB de VRAM, carrega o Qwen em 4 bits, gera uma resposta bruta e grava candidato mais manifesto/hash em `datasets/raw/teacher/`. O cache do modelo permanece no armazenamento efêmero `/content`.

O limite local de 40 GiB é uma barreira conservadora após uma tentativa sem memória suficiente em uma sessão menor, não uma promessa de que qualquer GPU de 40 GiB comportará o carregamento. Contexto, biblioteca e memória livre também afetam o uso. Se houver falta de memória, reduzir primeiro os limites de contexto/geração ou escolher outra sessão com mais VRAM após registrar o resultado; não trocar o modelo sem registrar uma nova configuração experimental.

Cada saída começa com `human_reviewed: false` e `safe_to_train: false`. Este notebook ainda não executa as propostas nem treina o aluno. A promoção depende da revisão e dos gates de execução descritos acima.
