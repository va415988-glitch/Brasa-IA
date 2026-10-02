# Avaliação de datasets para evolução do agente — 2026-09-23

## Resumo

O maior ganho imediato dos datasets é ensinar e medir decisões de workflow:
quando responder, pedir esclarecimento, chamar uma ferramenta, continuar após
o resultado e verificar a conclusão. O dataset do Colab foi copiado para
`datasets/agent_workflow_v1`. O schema é legível e os dois candidatos passam
pela checagem local, mas ainda não estão aprovados para treino. Eles servem
para confirmar a ingestão, não para demonstrar aprendizado.

O pipeline local já aprende um componente especializado de seleção de
ferramentas a partir de corpus revisável e traces completos. A auditoria atual
mostra que a qualidade e a diversidade dos dados limitam esse ciclo antes de
qualquer decisão sobre professor externo ou treino neural maior.

## Estado observado

### Colab

O schema `1.0` exige pedido, contexto, etapas com estado/ação/observação,
verificação, resposta final, origem, split e qualidade. As ações permitidas são
`inspect`, `search`, `terminal`, `edit`, `ask_user` e `respond`. O dataset
curado tem zero linhas; `raw/candidates_seed_v1.jsonl` tem duas:

- `candidate-main-cpp-001`: observação de `src/main.cpp`, ação `respond`; a
  resposta se limita ao trecho e declara que a implementação de `Run()` não
  está disponível.
- `candidate-clarification-001`: cenário sintético sem contexto, ação
  `ask_user`; pergunta qual projeto e qual mudança o usuário quer.

Ambos têm `human_reviewed=false`, `safe_to_train=false` e `split=unassigned`.
O auditor local confirma schema válido, IDs únicos e zero erros; seu status é
`ready_for_review`, com dois exemplos pendentes e nenhum elegível para treino.
O navegador do Drive ainda pede login, mas agora os arquivos locais permitem
continuar sem acessar a conta.

Triagem semântica preliminar do assistente, sem alterar flags:

- `candidate-main-cpp-001` está fundamentado no código incluído no contexto.
  A resposta descreve `main()` e limita corretamente o que afirma sobre
  `Application::Run()`.
- `candidate-clarification-001` combina com um pedido sem projeto, objetivo ou
  histórico no contexto; a pergunta proposta pede os dois dados que faltam.

Os dois exemplos parecem bons rascunhos para revisão humana e cobrem decisões
distintas. Um exemplo por ação ainda não fornece variedade para ensinar essas
classes; não iniciei treino nem atribuí splits.

### Dados locais

Executado o relatório somente de leitura:

```bash
./.venv/bin/python python/agent_learning_pipeline.py report
```

Resultado do snapshot local:

- 20.553 traces agrupados;
- 182 traces completos com ferramenta bem-sucedida aceitos como exemplos
  positivos (0,89% do total);
- 12.357 sem evento de plano e 8.014 sem conclusão verificável;
- 55 exemplos de corpus utilizáveis e 182 de traces;
- 237 exemplos antes da deduplicação e 69 depois dela;
- `quality.status=needs-attention`, que bloqueia elegibilidade de promoção.

Esses números descrevem os registros presentes, não uma medida de capacidade
do modelo. Os traces incompletos não entram como exemplos positivos. Podem
servir futuramente para diagnosticar instrumentação ou construir exemplos de
recuperação, depois de rotulados.

## O que cada família de dataset pode ensinar

| Dados | Capacidade que pode melhorar | Uso recomendado |
|---|---|---|
| Fluxos do agente | Escolher entre responder, perguntar, agir, verificar ou encerrar | Política de workflow com estado, ação e resultado |
| Traces de ferramentas | Selecionar ferramenta e argumentos a partir de pedidos reais | Reranker especializado que o projeto já treina |
| Diálogos revisados | Continuidade, tom e formato de resposta | Treino conversacional separado; avaliar geração livre |
| Documentos com fonte | Conhecimento factual e citações atualizáveis | Recuperação local; manter fatos extensos fora dos pesos |
| Avaliações reservadas | Generalização e regressões | Held-out separado de treino e validação |

Não misturar essas famílias sem registrar a finalidade. Uma pergunta de avaliação
que entra no treino deixa de ser uma medida independente.

## Compatibilidade do dataset do Colab

O treinador atual em `python/train_planner.py` lê chamadas estruturadas em
`messages[].tool_call` ou pares de instrução e rótulo de ferramenta. O dataset
usa `steps[].action.kind`, estado e observação; a auditoria agora valida esse
schema em `python/audit_agent_workflow.py`. A conversão para treino ainda pede
uma camada própria. `respond` e `ask_user` são decisões de política, não nomes
de ferramentas do catálogo atual. Para `inspect`, `search`, `terminal` e `edit`,
o importador também deve conferir `action.tool` e `action.arguments` contra os
contratos reais, sem executar os comandos registrados nos exemplos.

Tratamento antes de qualquer treino:

1. Manter os arquivos brutos na pasta de dataset e registrar hashes, versão do
   schema e origem quando criar um manifesto de importação.
2. Revisar privacidade, evidência, rótulos e resposta. Nenhum candidato não
   revisado entra em treino.
3. Converter decisões de chamada de ferramenta para o formato que o reranker
   local já aceita.
4. Manter `respond` e `ask_user` como classes distintas num conjunto de
   política de workflow. Não convertê-las silenciosamente em ferramentas.
5. Separar exemplos por tarefa/origem em treino, validação e held-out; evitar
   variantes quase idênticas entre as divisões.
6. Comparar baseline e candidato nos mesmos casos: ação correta, argumentos,
   necessidade de esclarecimento, conclusão, verificação, falhas e latência.
7. Promover somente após ausência de regressões nas baterias existentes e nos
   casos reservados.

## O que o Colab e a GPU acrescentam

O reranker de ferramentas atual é baseado em contagens de características e
executa em CPU; a A100 não traz benefício relevante para essa primeira
comparação. A GPU passa a ser útil quando houver corpus revisado suficiente
para comparar uma política neural ou treinar/adaptar um modelo maior. Dois
candidatos são insuficientes para justificar essa rodada.

O primeiro experimento pode ser local e reversível: integrar exemplos aprovados,
medir o reranker contra casos reservados e examinar erros por tipo de decisão.
Isso permite quantificar se os dados ajudam antes de investir numa rodada
neural ou em geração de dados por professor.

## Proveniência de treino

Esta avaliação não baixou nem usou modelos externos. O fluxo atual usa os dados
do projeto, sujeitos a revisão e validação antes de qualquer treino.

## Próximo passo

O validador local está disponível em `python/audit_agent_workflow.py` e pode ser
executado com `./.venv/bin/python python/audit_agent_workflow.py`. O próximo
trabalho é revisar os dois candidatos e expandir exemplos para `inspect`,
`search`, `terminal`, `edit` e verificação. Após revisão e divisão, podemos
construir um conjunto de avaliação e comparar o baseline. Não houve treino nem
alteração do checkpoint.
