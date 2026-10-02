# Dados e treinamento

## Pipeline de aprendizado do agente

`ensinar_ia.sh` agora orquestra o aprendizado do planejador local. Ele lê os
datasets de `corpus/training/`, aproveita somente traces concluídos com
resultados positivos, separa treino e validação, gera um índice candidato e
executa as baterias de regressão. O índice ativo não é alterado por padrão.

```bash
./ensinar_ia.sh report
./ensinar_ia.sh run
./ensinar_ia.sh run --promote
```

Cada rodada fica em `model/planner/runs/<id>/`, com manifesto, datasets
efetivamente usados, candidato, validação, logs das baterias e relatório. A
promoção só ocorre quando o candidato é íntegro, não regride na validação e
todas as baterias passam. `--skip-batteries` serve apenas para desenvolvimento
e nunca torna uma rodada elegível para promoção.

O dataset separado de trajetórias do agente fica em
`datasets/agent_workflow_v1/`. Audite seu schema, distribuição de ações e
revisão humana sem treinar com:

```bash
./.venv/bin/python python/audit_agent_workflow.py
```

O auditor não exporta exemplos nem altera flags de revisão. Os candidatos só
entram num experimento depois de aprovados e atribuídos a um split explícito.

## Ajuste supervisionado de engenharia e criatividade

### Motor criativo local

Pedidos classificados como criativos passam por `python/creative_engine.py`.
Essa camada preserva o briefing e escolhe entre os perfis `focused`, `balanced`
e `divergent`. Cada perfil define tentativas, orçamento, temperatura, `top_p`
e pesos de avaliação. Um seletor mede diversidade, repetição, requisitos e
restrições explícitas antes de escolher a saída. As notas ficam na telemetria
da geração para permitir ajuste com dados reais.
O motor continua usando exclusivamente o checkpoint local; ele não consulta
serviços externos nem transforma uma ideia em tarefa de código sem solicitação.

Pedidos de interface também passam pela trilha `interface_design_guidance`:
a implementação precisa justificar a direção visual e cobrir hierarquia,
estados, responsividade, acessibilidade e componentes. A criatividade fica
ligada a decisões de produto e engenharia, em vez de produzir apenas uma
aparência diferente.

Quando o pedido envolve frontend e backend, a trilha full-stack exige um
contrato único entre domínio, API, persistência e interface. Ela inclui estados
de rede, validação no servidor, separação de camadas e testes dos fluxos
críticos, evitando protótipos com dados hardcoded ou APIs que não alimentam a
experiência real.

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

O `RunEngine` persiste um ledger `agent-goal-state/v1` junto de cada execução:
objetivo original, critérios de aceite, evidências, bloqueios, progresso e
próxima ação. Uma continuação como “tente novamente” conserva o objetivo
operacional anterior em vez de substituí-lo pela frase curta. Se o planejador
bloquear sem selecionar uma ferramenta, o motor
faz uma única reconsideração com esse contexto antes de encerrar. A repetição é
limitada; uma nova escrita continua sujeita à aprovação e mudanças continuam
exigindo verificação.

Chamadas de ferramenta passam por `python/tool_registry.py`, que valida campos
obrigatórios, campos extras, tipos básicos e valores enumerados antes da
execução. O planner registra candidatos, margem e confiança para tornar a
seleção local auditável e comparável com exemplos de treino.

### Propostas quando o gerador local não responde

Se o checkpoint não gerar um plano JSON válido, `python/implementation_recipes.py`
pode fornecer uma proposta determinística para padrões explicitamente cobertos.
A receita inicial cobre uma CLI Python de tarefas com persistência JSON,
marcação de conclusão e rejeição de entradas vazias, em workspace vazio ou com
documentação básica. Há também uma continuação estreita para o pedido de uma UI
que organize essa CLI: ela só é ativada quando a inspeção encontra e lê o
`todo_cli.py` esperado com a classe `TaskStore`. Nesse caso, propõe `todo_ui.py`
e testes HTTP, reutiliza o mesmo JSON e serve uma página web somente em
`127.0.0.1:8765`, sem dependências externas. A UI permite adicionar tarefas,
ver pendentes e concluídas e marcar conclusão. Funcionalidades fora desse escopo
fazem a receita se abster.

A proposta gerada passa pelo mesmo validador de caminhos e limites e chega ao
runtime como `apply_batch`, que exige aprovação antes de escrever. Após as
verificações, a resposta lista os caminhos observados no resultado do lote, o
comando, o código de saída e linhas de evidência da execução. Se a verificação
falhar ou não puder rodar, o relatório conserva os arquivos alterados e os dados
da falha; um pedido sem receita compatível continua pendente e informa por quê.

## Leitura local de documentos

`extract_document_text` lê arquivos que estejam dentro do workspace ativo. O
leitor local suporta texto e código, PDF, HTML/XML, RTF, e-mail `.eml`, notebooks
Jupyter, DOCX/XLSX/PPTX, ODT/ODS/ODP e EPUB. Documentos Office modernos e ODF são
extraídos com a biblioteca padrão; PDF usa `pdftotext`; `.doc/.xls/.ppt` usam
LibreOffice quando instalado. OCR de imagem requer Tesseract. A extração tem
limites de tamanho e saída, não executa macros e marca o conteúdo como dado não
confiável para evitar que texto dentro de documentos vire instrução ao agente.
Áudio e vídeo ainda têm inspeção de tipo/tamanho, sem transcrição.

### Geração somente com o checkpoint próprio

O runtime iniciado por `start.sh` usa o checkpoint local indicado por
`IA_LOCAL_CHECKPOINT` ou pelo estado ativo em `model/godmode/state.json`. A
geração de respostas e propostas de código não consulta Ollama, Qwen ou outro
modelo de terceiros. A chave `IA_LOCAL_BRAVE_SEARCH_API_KEY`, quando
configurada, habilita somente a pesquisa web; ela não substitui nem gera texto
no lugar do checkpoint. Sem a chave, o runtime usa o mecanismo de busca local
disponível.

Em pedidos de construção sem receita local compatível, o agente inspeciona o
workspace e consulta a Brave antes de gerar a proposta: uma busca pelo produto,
outras pela tecnologia e pelas formas de testar quando a stack é identificável.
Até três páginas abertas, com URL e texto, entram como evidência transitória no
planejador e são citadas na proposta e na entrega. A API Brave é exigida nesse
fluxo; se a chave faltar ou a consulta falhar, a tarefa mostra o bloqueio. O
resultado bruto não é gravado no acervo de aprendizado do modelo. A pesquisa
fornece contexto transitório e exemplos; o checkpoint local ainda
precisa produzir código válido e a verificação do projeto continua obrigatória.

O fluxo atual de execução e treinamento não inclui geradores externos. Exemplos
de treino exigem seleção explícita, validação de procedência e revisão.
