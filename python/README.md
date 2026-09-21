# Dados e treinamento

## Ajuste supervisionado de engenharia e criatividade

`build_senior_curriculum.py` gera 54 exemplos autorais sintéticos e 12 pedidos
reservados. `finetune_assistant.py` continua os pesos existentes, preservando
arquitetura e tokenizer. Ele combina o novo currículo com replay de conversas
locais, remove perguntas duplicadas e exclui perguntas de avaliação do treino.

```bash
.venv/bin/python python/build_senior_curriculum.py
.venv/bin/python python/finetune_assistant.py --steps 600 --threads 4 --batch-size 8
```

Cada execução exige um diretório de saída novo (`--output-dir`). São gravados
os splits por exemplo, hashes de procedência, histórico de perdas, melhor
checkpoint em safetensors e respostas comparáveis antes/depois. O objetivo
supervisionado ignora tokens do pedido e padding; parte dos exemplos também
inclui o perfil compacto do assistente. O treino usa clipping de gradiente,
aquecimento da taxa de aprendizado, decaimento e parada por estagnação.

A seleção usa a validação, nunca os 12 pedidos finais. A validação contém
replay que pode ter sido visto no pré-treino original, por isso o relatório
também mede os pedidos autorais inéditos. O benchmark legado é apenas uma
verificação de regressão: suas perguntas já aparecem nos dados históricos.
Os resultados não devem ser apresentados como um benchmark independente.

Este fluxo não altera o modelo ativo. Uma queda de loss não prova competência
profissional, e a janela de 256 tokens continua abaixo da política de promoção.
Consulte `model/training/senior-creative-v1/` para os artefatos da execução.

Para experimentos de fundação, `--from-scratch` reutiliza apenas a arquitetura
do checkpoint de referência, inicializando matrizes com escala controlada e
camadas distintas (`scaled-normal-v1`). Essa opção descarta os pesos da origem;
o relatório distingue a perda inicial aleatória da perda do checkpoint de
referência. Checkpoints existentes mantêm seus pesos e sua compatibilidade.

## Promoção com evidência

`promote_checkpoint.py` exige um relatório `checkpoint-release-evidence/v1`
em `<checkpoint>.evaluation.json` ou indicado por `--evaluation`. Ele vincula
avaliação, configuração e tokenizer por SHA-256. Os campos e verificações estão
em `python/release_evidence.py`; resultados sintéticos de testes unitários não
são relatórios de aprovação.

São exigidos resultados documentados de retenção, programação, ferramentas,
verificação, regressão, criatividade e geração longa; casos ausentes ou
reprovados bloqueiam a promoção. Retenção exige observação de 8192 tokens de
entrada; geração longa exige pelo menos 4096 tokens de saída completa, sem
truncamento. Também são exigidas latência p95 de até 30 segundos e medição de
memória. A qualidade semântica precisa constar da avaliação: hashes provam
correspondência dos artefatos, não a veracidade de um julgamento.

A cópia preserva a extensão e os metadados de safetensors, copia a evidência,
não sobrescreve destinos existentes e audita o resultado novamente. Um
pré-voo estrutural aprovado, isoladamente, não declara elegibilidade de uso.

O pipeline inicial usa apenas a biblioteca padrão do Python para preparar e validar dados. O treinamento da rede neural será acoplado depois a um backend de tensores, sem alterar os contratos do runtime Rust.

## Treinar o tokenizer

```bash
python3 python/train_tokenizer.py
```

O tokenizer é byte-level BPE e preserva português, código, JSON e caracteres desconhecidos por meio de bytes UTF-8. O arquivo gerado fica em `model/tokenizer.json`.

## Preparar e treinar a primeira rede

```bash
python3 python/prepare_dataset.py
.venv/bin/python python/train_model.py --steps 1000
.venv/bin/python python/generate.py --prompt '<|user|>\nOlá\n<|assistant|>\n'
```

O preparador repete o corpus 16 vezes por padrão apenas para permitir um smoke test com o dataset inicial. Quando tivermos dados reais, usaremos `--repeat 1`. O treinador usa previsão causal do próximo token, começa com pesos aleatórios e salva um checkpoint em `model/checkpoints/`. Ele exige PyTorch apenas como biblioteca de tensores; nenhum modelo pré-treinado é baixado ou carregado.

## Validar os exemplos

```bash
python3 python/build_dataset.py
```

O script verifica JSON, campos obrigatórios, ferramentas conhecidas e argumentos serializáveis. Ele não acessa Ollama nem baixa modelos.

## Expandir o corpus curado

```bash
python3 python/expand_dataset.py
python3 python/train_tokenizer.py
python3 python/prepare_dataset.py --repeat 1
```

O corpus curado é pequeno e serve para validar o pipeline. Ele não deve ser tratado como treinamento suficiente para uma IA geral.

Sem `--dataset`, a preparação causal combina `python/data/combined.jsonl` com
`python/data/curriculum_apex_v1.jsonl`. Para selecionar entradas específicas,
repita a opção, por exemplo: `--dataset python/data/combined.jsonl`.

## Currículo de comportamento por domínio

`python/data/curriculum_apex_v1.jsonl` adiciona exemplos curados de programação,
criatividade e conhecimento geral ao servidor local. Eles são carregados como
memória de respostas verificadas, separados do acervo factual que pode mudar.

O benchmark mede esses domínios em conjunto com roteamento de ferramentas:

```bash
.venv/bin/python tests/benchmark_model_suite.py --report model/eval_suite_report.json
```

O gate de produção verifica o workflow completo em 12 perguntas de programação,
criatividade e conhecimento geral:

```bash
.venv/bin/python tests/benchmark_workflow_suite.py --report model/workflow_gate_report.json
```

Cada resposta informa uma estratégia e fases públicas do workflow, como
`understand`, `retrieve`, `plan`, `act`, `verify` ou `abstain`. Conhecimento
estável é recuperado antes de ferramentas; pesquisa, workspace e alterações
seguem o planner contratual e a validação de argumentos.

Chamadas de ferramenta passam por `python/tool_registry.py`, que valida campos
obrigatórios, campos extras, tipos básicos e valores enumerados antes da
execução. O planner também registra candidatos, margem e confiança para que
uma decisão local explícita tenha precedência sobre um professor auxiliar.
