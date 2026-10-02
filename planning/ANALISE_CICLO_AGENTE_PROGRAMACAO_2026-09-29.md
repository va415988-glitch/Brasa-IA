# O que um agente de programação faz ao receber uma tarefa?

**Data:** 29/09/2026  
**Objetivo:** transformar a pergunta em um ciclo observável e em medidas que orientem as melhorias da Brasa.  
**Escopo da evidência local:** código e relatórios existentes; esta análise não é uma nova execução do baseline pela interface.

## Resposta curta

Um agente recebe uma intenção incompleta, constrói uma representação operacional do resultado desejado, observa o ambiente, escolhe uma ação que reduza a incerteza ou produza parte do resultado, observa seu efeito e decide novamente. Ele só conclui quando evidências independentes mostram que os requisitos foram atendidos. O raciocínio útil aparece na **qualidade dessas decisões e correções**, não na quantidade de texto explicando um plano.

O ciclo é:

```text
pedido → requisitos e critérios → observação do sistema → hipótese/plano
       → ação limitada → efeito observado → verificação → replanejamento ou entrega
                  ↑______________________________________________|
```

Esse desenho é uma síntese aplicada à engenharia de software. [ReAct](https://arxiv.org/abs/2210.03629) estuda a alternância entre raciocínio, ação e observação. [SWE-agent](https://arxiv.org/abs/2405.15793) mostra que a interface para navegar, editar e executar testes influencia o desempenho. [SWE-bench](https://arxiv.org/abs/2310.06770) avalia mudanças em repositórios reais, onde compreender vários arquivos e executar o sistema importa mais do que emitir código isolado. Nenhum desses trabalhos demonstra que a implementação atual da Brasa já possui essa competência.

## O ciclo, com entradas e saídas verificáveis

| Etapa | O que o agente precisa decidir | Evidência de que fez bem |
| --- | --- | --- |
| 1. Entender | Qual produto ou mudança foi pedida? Quais restrições e ambiguidades importam? | Requisitos identificados e critérios de aceite ligados ao pedido; pergunta apenas quando uma decisão essencial depende do usuário. |
| 2. Situar | Qual workspace, stack, versão e estado inicial? | Inventário, manifestos, arquivos centrais e checks observados, com origem e versão do conteúdo. |
| 3. Diagnosticar | O que já existe? Onde está o comportamento relevante? O que falta? | Hipóteses separadas de fatos; leitura de código, testes e documentação pertinentes. |
| 4. Planejar | Qual menor sequência coerente de alterações satisfaz os critérios? | Proposta de arquivos, interfaces e dependências compatível com a base encontrada. |
| 5. Executar | Qual ação é segura e válida agora? | Chamada tipada, argumentos válidos, diff ou efeito concreto, autorização quando cabível. |
| 6. Verificar | O comportamento pedido funciona no estado atual? | Checks pertinentes executados, inspeção do diff e teste do fluxo principal; resultados ligados à versão dos arquivos. |
| 7. Recuperar | A falha muda qual hipótese ou qual ação? | Nova tentativa baseada em erro observado; sem repetição idêntica ou alegação de sucesso sem efeito. |
| 8. Entregar | O que foi concluído, testado e ficou pendente? | Resposta que relaciona cada requisito à evidência, limitações e próximos passos concretos. |

Uma ação de ferramenta não é, por si só, progresso. Ler um arquivo é útil se muda o diagnóstico ou sustenta a resposta. Escrever um arquivo é útil se implementa um requisito. Um teste verde é útil se exercita o comportamento pedido.

## Como o ciclo muda ao criar, melhorar e manter um sistema

**Do zero.** O workspace começa sem arquitetura. O agente precisa escolher uma estrutura proporcional ao pedido, definir contratos entre componentes, implementar um caminho funcional mínimo e executá-lo. Para um aplicativo web, isso inclui dados, persistência, interface, estados de erro, instruções de uso e verificação do fluxo no navegador. Criar arquivos de uma receita parecida não comprova que entendeu o produto solicitado.

**Melhorar um sistema existente.** O estado atual vira restrição: comportamento público, formato de dados, chaves de persistência, convenções e testes anteriores devem ser preservados. O agente localiza o ponto de mudança, mede o comportamento inicial, faz um diff pequeno, verifica a nova função e repete checks de regressão. Uma mudança de requisito na conversa atualiza a tarefa; não apaga descobertas válidas nem herda restrições temporárias já superadas.

**Manter ao longo do tempo.** Cada nova tarefa começa do estado real deixado pela anterior. O agente precisa reconhecer regressões, migrações, dependências, sinais de operação e custo futuro de mudança. Para serviços em operação, manutenção inclui indicadores, objetivos de serviço e resposta a incidentes; o [SRE Workbook do Google](https://sre.google/workbook/table-of-contents/) trata dessas práticas. Uma bateria de manutenção deve incluir sequências de pedidos, pois uma solução que passa hoje pode tornar a próxima alteração difícil. [SWE-Bench-CL](https://arxiv.org/abs/2507.00014) é uma referência de avaliação cronológica de mudanças em repositórios; [SWE-bench-Live](https://arxiv.org/abs/2505.23419) reforça o valor de tarefas recentes para reduzir a dependência de benchmarks estáticos.

## Confronto com as evidências da Brasa

O [baseline de 28/09](BASELINE_NUCLEO_BRASA_V1_2026-09-28.md) teve **0/3 tarefas concluídas** pela interface: análise de projeto, criação em workspace vazio e alteração de projeto existente. A leitura e o inventário funcionaram parcialmente; a síntese da análise e a proposta de mudança falharam. Esses resultados descrevem aquela execução, não uma taxa geral de sucesso nem o estado após alterações posteriores.

O [relatório neural de 26/09](../model/RELATORIO_ESTADO_MODELO_NEURAL_2026-09-26.md) mediu **0/24 respostas aprovadas** na geração direta do checkpoint ativo e do candidato experimental. Portanto, adicionar mais ferramentas, estados ou espaço de contexto não demonstra, sozinho, capacidade de escolher uma implementação inédita. A geração é uma dependência crítica a ser medida separadamente da orquestração.

O código já possui peças importantes: [AgentCore](../agent-core/src/agent.ts) mantém etapas, leituras, aprovação, recuperação e verificação; [task-acceptance](../agent-core/src/task-acceptance.ts) impede conclusão sem certos efeitos observados; [model_server](../python/model_server.py) sintetiza algumas análises a partir de arquivos lidos e valida propostas de implementação. As suítes unitárias verdes verificam muitos contratos dessas peças. Elas não substituem uma execução completa de um pedido inédito na interface.

### Gargalos ordenados por dependência

| Prioridade | Gargalo | Evidência local | Hipótese testável de melhoria |
| --- | --- | --- | --- |
| P0 | Propor uma mudança nova e válida | Criação e edição bloquearam antes do diff; geração direta 0/24 | Comparar, no mesmo contrato de proposta, pesos atuais, candidato treinado e um gerador de referência. Medir validade sem executar. Corrigir treino/inferência ou selecionar um gerador que demonstre a capacidade. |
| P0 | Responder a partir do que foi lido | A análise leu arquivos, mas não entregou resposta no baseline | Reexecutar a análise com a rota de síntese atual e exigir afirmações ligadas a arquivos e operações reais. |
| P0 | Aceite semântico do produto | O gate de `build` exige escrita e verificação aprovada, mas esses sinais não provam que o produto certo foi construído | Vincular cada requisito a um teste de comportamento independente e conferir o fluxo principal do produto. |
| P1 | Continuidade da mesma tarefa | Análise e pedido seguinte do Reading Shelf receberam IDs separados no baseline | Persistir requisitos, descobertas e versões de arquivos sob uma tarefa canônica e medir preservação em 3 a 5 turnos. |
| P1 | Recuperação informada | O planejador repetiu fallback sem produzir alternativa no baseline | Exigir que uma nova tentativa incorpore observação ou diagnóstico novo; encerrar com causa precisa quando não existir alternativa. |
| P2 | Manutenção de longo prazo | Ainda não há trajetória de evolução demonstrada | Avaliar sequências de alteração, regressão e custo de mudança, após a criação e edição simples funcionarem. |

Essa ordem é uma **inferência de engenharia** a partir dos relatórios e do código. É preciso reexecutar os mesmos casos após cada mudança para confirmar se o gargalo mudou.

## Como calcular melhoria sem confundir atividade com competência

Aproveitar a bateria de 60 tarefas já proposta no [plano do agente programador](PLANO_AGENTE_PROGRAMADOR_FUNCIONAL.md), incluindo criação, mudança, correção e continuidade. Acrescentar casos de explicação fundamentada como diagnóstico separado; eles não devem alterar o denominador do gate de produto já definido. Cada tarefa precisa de critérios observáveis escritos antes da execução. Manter os casos reservados fora de treino, receitas e ajustes de prompt.

Para cada execução, registrar identidade/hash do checkpoint, commit e estado relevante do código, configuração, pedido, workspace inicial, chamadas, diffs, checks, estado final, tempo e intervenção humana. O evento deve permitir atribuir a primeira falha a uma etapa do ciclo.

| Medida | Cálculo | O que revela |
| --- | --- | --- |
| Sucesso por tarefa | tarefas com **todos** os critérios aprovados ÷ tarefas tentadas | Resultado do produto, incluindo bloqueios e timeouts no denominador. |
| Conversão por etapa | tarefas que completam a etapa ÷ tarefas que chegaram à etapa | Onde o ciclo perde mais casos. Ex.: proposta válida após observação. |
| Fidelidade aos requisitos | critérios aprovados ÷ critérios totais, por tarefa e por classe | Funcionalidade parcial e desvio de produto. Não substitui sucesso integral. |
| Validade da proposta | propostas que passam schema, caminhos e coerência com fontes ÷ propostas emitidas | Capacidade de compor mudanças antes de escrever. |
| Recuperação útil | falhas com diagnóstico novo e correção aprovada ÷ falhas recuperáveis | Se feedback de execução altera a decisão. |
| Regressão | tarefas que quebram critérios antigos ÷ tarefas com alteração | Preservação do sistema existente. |
| Consistência | tarefas aprovadas em **todas** as `k` reexecuções ÷ tarefas repetidas | Confiabilidade; usar a mesma configuração e workspaces limpos. |
| Custo por sucesso | tempo total de execução e revisão humana ÷ tarefas concluídas | Se o ganho compensa tentativas e retrabalho. Se não houver sucesso, reportar custo e zero sucessos, sem dividir por zero. |

Para comparar duas versões, usar **as mesmas tarefas e condições**, reportar `sucessos_novo/N - sucessos_anterior/N` em pontos percentuais e também o número absoluto de tarefas que mudaram de resultado. Por exemplo, passar de 0/20 para 6/20 significa +30 pontos percentuais e seis tarefas adicionais; não significa “melhoria infinita” por causa da base zero. Reportar separadamente tarefas reservadas, regressões, custo e dispersão entre repetições. Três casos são úteis para localizar falhas, mas pequenos demais para estimar capacidade geral com precisão.

Uma otimização deve atacar a **primeira etapa que falhou**, não a etapa mais visível. Se 90% das tarefas são entendidas, mas só 10% produzem proposta válida, melhorar a interface final não altera muito o sucesso. Esse exemplo é ilustrativo; os percentuais reais da Brasa ainda precisam ser medidos.

## Próximo experimento que decide o trabalho seguinte

1. Congelar uma cópia descartável dos três casos do baseline, registrar checkpoint e código exatos, e executar cada caso pela interface com limite de tempo definido.
2. Separar a avaliação do **gerador**: fornecer pedido + arquivos lidos e medir se retorna um plano JSON válido, relevante e verificável, sem escrita. Comparar com gerador de referência sob o mesmo contrato.
3. Separar a avaliação do **núcleo**: fornecer propostas válidas controladas e medir se lê, aplica, verifica, recupera e preserva requisitos corretamente.
4. Repetir o agente completo em casos de desenvolvimento e casos reservados. Registrar a primeira falha e decidir a próxima correção pela maior perda de sucesso condicional.

**Critério para dizer que a Brasa começou a agir como agente programador:** ela resolve tarefas inéditas de cada classe com evidência do estado final, preserva o comportamento anterior ao evoluir um sistema e explica precisamente bloqueios reais. Número de ferramentas, tamanho da janela e testes unitários verdes são condições de suporte, não essa demonstração.

**Primeira reexecução:** [baseline de 29/09](BASELINE_CICLO_BRASA_2026-09-29.md). As análises passaram nos critérios diagnósticos após uma correção da síntese; as duas construções continuaram bloqueadas antes da proposta de arquivos.
