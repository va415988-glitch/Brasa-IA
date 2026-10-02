# Revisão do snapshot v001 para a primeira curadoria

## Resultado da auditoria

O Colab confirmou 101 arquivos e 141.943.017 bytes contra o manifesto. Não houve JSON/JSONL inválido. Há 27 arquivos candidatos a treino, 10 reservados para avaliação e 41 em quarentena. O exportador terminou sem dados porque todas as fontes estavam pendentes; esse era o comportamento esperado da primeira versão do notebook.

A revisão abaixo usou o ZIP v001 de mesmo hash enviado ao Drive. No momento da revisão, os 101 arquivos do ZIP correspondiam aos arquivos locais. O ZIP temporário de Downloads foi removido após a conferência do snapshot no Colab. O relatório não altera o snapshot bruto.

## Primeiro lote selecionado

O manifesto `python/data/godmode_datasets_manifest_v1.json` declara autoria local determinística e registra os hashes de `godmode_knowledge_v1.jsonl` e `godmode_procedures_v1.jsonl`. Esses hashes conferem com o snapshot. Revisei o texto integral de 40 registros úteis para programação, depuração, uso de ferramentas e verificação:

| Fonte | Registros selecionados | SHA-256 do arquivo |
| --- | ---: | --- |
| `python/data/godmode_knowledge_v1.jsonl` | 29 | `c5beae25fa29037301b17bfeb976b432b7cbe9b4bf17a9c1248c57df696a0736` |
| `python/data/godmode_procedures_v1.jsonl` | 11 | `9d272d64706227bc39aea63c2b322c0d0fb0356ca91ce820b009328f23a118eb` |

Na divisão determinística do notebook, o lote gera **37 registros de treino e 3 de validação**. Nenhum dos 40 prompts coincide literalmente com os 158 prompts reservados que a auditoria extraiu de `corpus/eval/`.

O registro 39 de `godmode_knowledge_v1.jsonl` foi rejeitado. Ele ensina a pedir aprovação antes de qualquer escrita em arquivo, regra que reproduz o bloqueio observado na Brasa. Os demais registros desses dois arquivos permanecem pendentes, inclusive alguns de levantamento de requisitos e multimodalidade que não atendem ao foco deste lote.

## Fontes rejeitadas nesta exportação

| Fonte | Motivo |
| --- | --- |
| `corpus/training/teacher_plans_qwen3_30b.jsonl` | Vazio. |
| `python/data/behavior_augmented.jsonl` | Contém resultados de ferramenta simulados tratados na conversa como evidência observada. |
| `python/data/behavior_augmented_v2.jsonl` | Cópia byte a byte de `behavior_augmented.jsonl`. |
| `python/data/combined.jsonl` | Repete exemplos presentes em `behavior_augmented.jsonl` e `tool_traces.jsonl`. |
| `python/data/tool_traces.jsonl` | Repete exemplos e inclui respostas simuladas de pesquisa e abertura de página. |

Os 27 candidatos contêm **71 repetições exatas de mensagens normalizadas** entre ou dentro de arquivos. `planner_sft.jsonl`, por exemplo, tem 5 linhas e somente 2 conversas distintas. O exportador também deduplica mensagens, mas rejeitar fontes redundantes deixa a origem mais clara.

## Fontes ainda pendentes

**Procedência ou licença a esclarecer:** `corpus/training/dataset_deepseek_code.jsonl`, `python/data/agent_harness_curriculum_v1.jsonl`, `python/data/databricks_genai_curriculum_v1.jsonl`, `python/data/deep_learning_book_curriculum_v1.jsonl`, `python/data/hf_datasets_curriculum_v1.jsonl` e `python/data/little_book_deep_learning_curriculum_v1.jsonl`.

- O manifesto do PDF da Databricks registra `license_status: not_found_in_pdf`; a regra local de paráfrase não comprova permissão de reutilização.
- [A obra de François Fleuret](https://fleuret.org/public/lbdl.pdf) declara CC BY-NC-SA 4.0. A compatibilidade com o uso pretendido precisa ser decidida antes de aprovar essa fonte.
- O repositório [huggingface/datasets no commit registrado](https://github.com/huggingface/datasets/blob/3e2c1a6c33b883fd9710f55f7b6fa4ab64d0ee07/LICENSE) contém Apache-2.0; as linhas de currículo ainda precisam de revisão de qualidade e atribuição antes de entrar no conjunto.
- `dataset_deepseek_code.jsonl` registra `qwen-coder-32b` como fonte, sem identificar checkpoint e termos de uso exatos.

**Qualidade, finalidade ou origem a revisar:** `corpus/training/dataset_sintetico.jsonl`, `corpus/training/planner_corrections.jsonl`, `corpus/training/planner_sft.jsonl`, `python/data/agentic_curriculum_v1.jsonl`, `python/data/behavior_expanded.jsonl`, `python/data/behavior_phase1.jsonl`, `python/data/contextual_local_qa_v1.jsonl`, `python/data/curriculum_apex_v1.jsonl`, `python/data/local_dialogue_context_v1.jsonl`, `python/data/neural_context_2048_v1.jsonl`, `python/data/neural_gate_focus_v1.jsonl`, `python/data/neural_professional_v1.jsonl`, `python/data/open_programming_curriculum_v1.jsonl` e `python/data/senior_creative_v1.jsonl`.

`planner_corrections.jsonl` contém rótulos de roteamento como `create_file`; precisa de um conjunto próprio para selecionar ferramentas. Misturá-lo ao treino de resposta conversacional ensinaria o assistente a responder com o nome da ferramenta. `open_programming_curriculum_v1.jsonl` contém exemplos de código promissores, mas ainda pede verificação de origem e dos trechos executáveis.

## Próxima coleta

Os 40 registros selecionados ensinam princípios de comportamento, mas não ensinam a concluir uma tarefa de programação inteira. A próxima versão deve registrar episódios com pedido, contexto do repositório, chamadas de ferramenta, diff aplicado, comando de verificação, saída real e estado final. Só episódios com escrita e verificação observadas devem entrar como exemplos positivos. Um conjunto reservado de tarefas novas medirá se isso melhora a capacidade do agente de criar arquivos e concluir projetos.
