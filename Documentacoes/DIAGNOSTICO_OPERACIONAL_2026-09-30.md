# Diagnóstico da pesquisa, habilidades e execução

O teste pela aplicação real confirmou problemas de integração e uma limitação independente do checkpoint. Corrigir a API tornou consultas e verificações utilizáveis; isso não tornou o modelo um agente geral capaz de compreender e implementar qualquer pedido.

## Falhas corrigidas

- Perguntas sobre habilidades eram classificadas como ordens para executar testes. Agora seguem a rota de conversa e consultam o catálogo real do runtime.
- Candidatos de habilidades podem misturar ferramentas e APIs de serviços. A seleção acessava `item['tool']` em candidatos sem esse campo; agora usa acesso opcional e a conversa pode aproveitar uma capacidade de leitura selecionada por uma habilidade registrada, respeitando seu catálogo permitido.
- A confiança de um classificador de quatro categorias bloqueava consultas com arquivo ou URL explícitos. Fontes identificadas no pedido agora têm prioridade sobre esse classificador.
- A conversa encerrava o ciclo depois da primeira falha. Agora pode localizar um arquivo, abrir um resultado de busca, tentar outra página ou usar busca depois de uma pesquisa malsucedida, dentro do orçamento do executor.
- Resultados grandes viravam um prefixo de JSON apresentado como trecho de fonte. A compactação preserva a estrutura e as URLs. Observações anteriores a um novo pedido não sustentam sua resposta.
- Um arquivo identificado de forma única numa subpasta voltava a ser solicitado pelo nome curto. A continuação conserva o caminho confirmado pela inspeção. Leituras de JSON podem apresentar campos realmente observados.
- A busca removia pontos de nomes de APIs, e a síntese dividia símbolos como `asyncio.TaskGroup`. Esses nomes são preservados; domínios de filtros `site:` não se tornam assuntos da síntese.
- A resposta de pesquisa podia começar com “Concluí a etapa”, expressão rejeitada pela validação como resposta genérica. A síntese documental deixa de usar esse prefixo.
- A pesquisa utilizava contexto pré-extraído da Brave sem conferir a página original. Agora tenta abrir essa página; se falhar, registra o motivo e marca a evidência alternativa como não conferida na fonte primária.
- A execução de testes afirmava que houve implementação mesmo sem escrita. Agora relata a verificação executada e seu resultado.
- O roteador não encaminhava deltas de resposta. Além disso, o servidor TypeScript e o runtime Rust cortavam mensagens codificadas do streaming. Os deltas agora chegam inteiros ao feed da interface, preservando Unicode.

## Verificação

O runtime foi iniciado e os pedidos passaram pelo endpoint `/api/v1/agent/pursue`, com o worker Python, o AgentCore TypeScript, as ferramentas Rust e a pesquisa configurada. Os testes locais usaram um projeto temporário, com uma versão identificável em um JSON e uma suíte `unittest` real.

| Cenário | Resultado observado |
| --- | --- |
| Pesquisa de `asyncio.TaskGroup` | Consultou documentação e entregou trechos com fontes |
| Pergunta sobre habilidades | Listou o catálogo atual de 34 ferramentas |
| Leitura de nome curto em subpasta | Encontrou e leu `nested/config.json` |
| Recuperação de arquivo na conversa | Leitura inicial, localização e leitura do caminho confirmado |
| Execução de testes | Suíte executada, código de saída 0, resultado observado |
| Consulta de página | Conteúdo obtido e URL citada |

Resultado: **6/6**. A recuperação em conversa também verifica que os deltas do feed efetivamente usado pela interface recompõem toda a resposta, inclusive acentos e emojis. Relatório: `avaliacoes/operational-smoke-2026-09-30.json`.

Reprodução, com a aplicação iniciada por `bash start.sh`:

```bash
.venv/bin/python scripts/smoke_agent_operations.py --output /tmp/ia-smoke-report.json
```

Regressões verificadas: 81 testes do ciclo Python, 20 testes de decisões e recuperação, 13 testes de habilidades, os 16 arquivos de testes TypeScript e sua checagem de tipos, 44 testes Rust e o teste adicional de integridade do streaming Unicode. Falhas HTTP e fontes alternativas também têm testes controlados; isso não equivale a testar todas as combinações de falhas dos provedores externos.

## Gargalo ainda existente

O checkpoint generativo foi reavaliado isoladamente, desabilitando o roteador auxiliar, com quatro casos de decisão e observações simuladas. Resultado: **0/4 decisões válidas**. Relatório: `avaliacoes/cognicao-neural-reteste-2026-09-30.json`.

Esse teste é pequeno e não mede compreensão geral, mas mostra que não podemos atribuir os seis resultados operacionais ao raciocínio do checkpoint. O modelo usa contexto de execução de 32768 tokens, com contexto de treino declarado de 512; ampliar a janela de execução não comprova aprendizagem de contextos maiores.

As respostas documentais que dependem da extração estão identificadas como trechos de fontes. O teste de pesquisa verifica acesso, pertinência mínima e referências; não certifica uma explicação completa de TaskGroup, domínio de programação ou geração de código novo.

Para eliminar o gargalo de cognição, falta um checkpoint que passe avaliações separadas de decisão, compreensão de resultados, recuperação e implementação com testes. A promoção deve depender de tarefas inéditas e executadas, incluindo erros e recusas justificadas, em vez da acurácia de classificação de pedidos ou da presença de texto numa resposta. Nesta correção não houve treino nem troca dos pesos generativos.
