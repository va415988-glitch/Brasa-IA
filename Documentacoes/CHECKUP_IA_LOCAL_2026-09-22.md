# Checkup da IA local — 22/09/2026

## Resumo

O runtime e o worker estavam ativos, e os fluxos determinísticos do agente passaram nos testes. O checkpoint neural marcado como ativo ainda não demonstrou geração aberta confiável: uma pergunta simples levou 73 s para cair numa resposta memorizada; um teste neural direto de 16 tokens foi rejeitado como irrelevante. A avaliação de 100/100 verificações não mede os pesos neurais: as 12 sondagens listadas nela responderam pelo dataset local de competências.

O chat informa estados enquanto trabalha. Ele mostra etapas e pulsos de execução, mas não transmite os tokens da resposta. O principal gargalo observado é a geração neural antes do fallback para conteúdo curado.

## Estado operacional

- `GET /api/health`: runtime Rust saudável em `127.0.0.1:3000`.
- `GET /health` do worker: backend `local-neural`, sem erro, checkpoint `model/godmode/training-neural-v1/candidate.safetensors`.
- Configuração ativa: 2 camadas, dimensão 128, vocabulário 8.192, contexto de produção 16.384 tokens. O metadado do treino registra contexto de treino de 512 tokens.
- O sidecar do checkpoint ainda o classifica como `experimental`. O relatório de treino registra 1.500 passos, mas a loss de validação e a loss heldout do candidato são idênticas às do baseline: 3,9019 e 3,7211, respectivamente.
- O próprio estado do perfil God Mode declara que não representa onisciência, nível humano neural ou compreensão multimodal irrestrita.

## Testes executados

| Bateria | Resultado |
|---|---:|
| AgentCore (`npm test --prefix agent-core`) | 29/29 aprovados |
| `test_learning.py` | 23/23 aprovados |
| `test_agent_learning_pipeline.py` | 9/9 aprovados |
| `test_agent_traces.py` | 2/2 aprovados |
| `test_model_assessment.py` | 2/2 aprovados |
| `test_autonomous_learning.py` | 10/10 aprovados |
| `test_agent_state.py` | 11/11 aprovados |
| Benchmark de roteamento e continuação | 12/12 escolhas corretas; média de 22,41 ms |

O benchmark de fluxo também completou `create_web_page → project_checks → completed`, manteve o mesmo trace e recuperou uma falha de verificação com `diagnose_project`. Na busca web simulada, escolheu a segunda fonte após a primeira falhar. Esse benchmark simula resultados de ferramentas; não executa as 12 operações no workspace.

## Capacidade de resposta e latência

1. **Chat local ponta a ponta:** enviei “Qual é o resultado de 19 + 23? Responda apenas com o número.” O trace registrou a resposta correta, `42`, pelo backend `curated-memory`, em 73.044,7 ms. O cliente de teste tinha timeout de 35 s e encerrou a espera; o servidor concluiu a requisição aos 73 s.
2. **Pesos neurais isolados:** chamei diretamente `local_reply`, sem memória nem dataset, para gerar uma função Python, limitando a saída a 16 tokens. Levou 9.288 ms e retornou `None`; o gate marcou `not-relevant`.
3. **Bateria de 15 casos:** a execução sem teto continuava ativa após 8 min 48 s e foi encerrada. Repeti com teto de 64 tokens por resposta e limite total de 240 s; também não produziu relatório antes do timeout. Não há p50/p95 válido para essa bateria.

O primeiro caso revela um desvio específico: a frase de aritmética correspondia a uma resposta curada, mas sua forma (“o resultado de…”) não satisfez o padrão de pedido determinístico. O caminho neural foi tentado antes de o sistema usar o fallback `42`.

O relatório existente `model/eval_suite_report.json` contém 15 casos, seleção de ferramenta de 100%, qualidade lexical média 0,544 e latência p50/p95 de 25,51/49,43 ms. Porém, não registra o checkpoint nem metadados do modelo, então não serve como baseline reproduzível do checkpoint ativo. A qualidade é contagem de termos esperados, não avaliação semântica.

## Aprendizado e ciclos

- O ledger contém 20 competências: 1 dominada, 18 parcialmente conhecidas e 1 em estudo.
- TypeScript é a única dominada: 43 documentos, 2 hosts independentes, 15/15 práticas aprovadas, 4 tarefas de transferência e uma integração registrada. Os critérios locais de conclusão estão todos satisfeitos.
- A próxima trilha é Go, com progresso de 55%. O ledger registra 12/12 práticas, 5 transferências e integração, mas nenhuma fonte/documento, nenhum host independente e nenhuma cobertura curricular; por isso permanece parcial.
- Um ciclo é limitado a 6 páginas de origem, 6 documentos novos, 16 práticas, 24 passos, 24 mil tokens e 2 ciclos concluídos por dia. A política só promove a competência após evidências e práticas verificadas; pesquisar não basta.
- A auditoria registra 5 inícios de ciclo e 3 términos; dois inícios não têm término correspondente. Dos três términos, um ficou `unknown` e dois terminaram como execução `completed`, mas com laboratório `practice_unavailable`, competência ainda parcial e progresso de 13,75% e 6,25%. “Ciclo concluído” indica que a execução terminou, não que a habilidade foi dominada.
- O ciclo autônomo atual está inativo; o planejador indica Go como próximo tema. Os testes de estado e de ciclos passaram, mas os registros históricos justificam verificar retomada e reconciliação de ciclos interrompidos.

O aprendizado tem duas camadas distintas: os ciclos atualizam acervo, evidências e ledger; a alteração de pesos acontece em um pipeline offline separado. Portanto, aprender um tema no chat não significa que o checkpoint foi retreinado.

## Feedback de estado no chat

**Existe e foi observado no endpoint real.** Para a chamada de chat acima, o runtime publicou estados correlacionados de recebida, processamento e inferência; em seguida publicou um pulso a cada 3 s e terminou com `Resposta pronta` aos 73 s. A interface consulta `/api/events` a cada 450 ms e mantém o estado visível por pelo menos 900 ms.

O feedback é de etapas, não de geração: não há tokens parciais, tokens/s, estimativa de término nem progresso percentual para uma resposta de chat. O worker pode continuar computando depois que um cliente externo atinge seu próprio timeout; no teste, o runtime terminou e registrou o trace mesmo após o cliente de 35 s parar de esperar.

## Prioridades identificadas no checkup

1. **Desvio determinístico:** resolvido para aritmética curada em linguagem natural; o teste ao vivo caiu de 73 s para menos de 0,2 s.
2. **Proveniência de avaliação:** resolvida no benchmark neural isolado; agora não consulta o adaptador curado e identifica os pesos por caso.
3. **Gate de promoção do checkpoint:** pendente. Ainda é necessário exigir ganhos contra baseline em validação/heldout e utilidade em tarefas abertas antes de promover pesos.
4. **Custo e controle de geração:** parcialmente instrumentado com tokens/s de decodificação. Pré-carga, cancelamento por abandono, orçamento por intenção e diferença de contexto treino/produção ainda precisam de avaliação própria.
5. **Ciclos:** concorrência, início incompleto, falha e interrupção passam a ser registrados; o resumo expõe estado da competência e bloqueio de prática. A auditoria histórica antiga permanece imutável e ainda contém inícios sem término para análise manual.
6. **Feedback visual:** etapas em tempo real foram confirmadas. Ainda não há streaming de tokens, tokens/s ou ETA na interface; as métricas novas são pós-geração, não previsões em andamento.

## Melhorias implementadas após o checkup

- **Resposta determinística sem espera neural:** ampliei o reconhecimento de pedidos aritméticos como “qual é o resultado de…”. Com o worker recarregado, o mesmo pedido retornou `42` pelo backend `curated-memory` em 98 ms no runtime (91 ms no contrato do worker), contra 73.044,7 ms no checkup — cerca de 745 vezes menos tempo nesta comparação específica. O teste também confirma que `local_reply` nem é chamado nesse caminho.
- **Benchmark realmente neural:** `tests/benchmark_neural_generation.py` agora chama apenas `local_reply`, sem o adaptador de dataset curado; cada linha identifica `raw-local-checkpoint` e o relatório marca `local-checkpoint-weights`. Adicionei `--limit` para amostras rápidas e métricas de decodificação (`generation_elapsed_ms`, `tokens_per_second`).
- **Medição do checkpoint ativo:** executei um caso do checkpoint `candidate.safetensors`, com teto de 16 tokens. O checkpoint carregou, mas a saída foi reprovada como `not-relevant` (0/1 caso útil); gerou 16 tokens em 2.498,73 ms, ou 6,4 tokens/s de decodificação. O tempo total do processo foi 9.591 ms e inclui carga/inicialização; esta amostra curta não é uma avaliação geral do modelo.
- **Ciclos de aprendizado auditáveis:** transições de ciclo agora usam lock de arquivo entre threads/processos; o controle registra a reserva `cycle_starting` antes de iniciar o job, persiste falhas do executor e classifica jobs ausentes após reinício como `interrupted`. Interrupções contam no limite diário. O status compacto distingue término da execução do estado/progresso da competência e informa quando a prática ficou indisponível por falta de executor local seguro.
- **Estado do chat:** após o reload, o evento do pedido rápido apareceu correlacionado em `/api/events` com `received → processing → model → done`, concluindo em 98 ms. Para tarefas longas, continuam disponíveis os pulsos periódicos; o chat ainda não transmite tokens parciais nem estima tokens/s/ETA durante a resposta.

## Validação e escopo

As baterias Python de aprendizado, traces, avaliação, estado, ciclos e roteamento passaram: **78/78 testes**. O teste de concorrência provou que dois `tick()` simultâneos criam só um job; regressões adicionais cobrem job ausente e falha no início. Nenhum peso/checkpoint foi treinado ou modificado. A amostra neural foi gravada em `/tmp/ia-local-neural-raw-onecase.json`; o teste ao vivo adicionou um trace de conclusão em `logs/agent_traces.jsonl`.

O primeiro passe do checkup não alterou código; as mudanças acima foram feitas depois da autorização do usuário. O relatório inicial e as evidências originais continuam preservados. Principais referências: `model/godmode/state.json`, `model/godmode/training-neural-v1/report.json`, `model/godmode/training-neural-v1/candidate.safetensors.json`, `model/godmode/training-neural-v1/verification_100.json`, `logs/autonomous_learning.jsonl` e `logs/agent_traces.jsonl`.
