# Avaliação do chat local com 1.024 perguntas

Data: 2026-09-27.

## Resultado verificado

| Bateria | Resultado | O que mede |
|---|---:|---|
| Suíte Python | 342 aprovados, 144 subtestes aprovados | Contratos, segurança, ferramentas, aprendizado e conversa |
| Interface JavaScript | 14/14 | Roteamento e histórico do chat |
| AgentCore TypeScript | 56/56 | Planejamento, ferramentas e memória |
| Runtime Rust | 42/42 | Contratos e integração do runtime |
| Requisitos do chat | 7/7 | Clarificação, função com testes, continuidade, incerteza e ideias |
| Chat local ampliado | 1.024/1.024 | Fluxo completo com memória curada, regras, ferramentas e checkpoint carregado |
| Pesos neurais isolados | 0/20 na última medição | Geração livre sem memória, ferramentas ou regras |

Comandos reproduzíveis:

```bash
.venv/bin/python -m pytest -q --disable-warnings
.venv/bin/python tests/benchmark_requirements.py --checkpoint model/godmode/context-32768-v1/candidate.safetensors
.venv/bin/python tests/benchmark_chat_1024.py
node --test tests/chat_core.test.cjs tests/test_context_history_ui.cjs
npm test --prefix agent-core
cargo test --manifest-path runtime/Cargo.toml --quiet
```

O checkpoint carregado é `model/godmode/context-32768-v1/candidate.safetensors`. Na carga, o servidor restaura em memória as 512 posições treinadas a partir do checkpoint de origem verificado; o arquivo ativo permanece intacto. Os candidatos de diálogo não foram promovidos porque a avaliação neural isolada reprovou os 20 casos reservados.

## Composição da bateria ampliada

- 768 formulações novas sobre 256 assuntos distintos que já tinham resposta no acervo local. Cada pergunta é diferente das perguntas originais do acervo. A avaliação exige semelhança textual e cobertura de termos com a resposta de referência.
- 64 pedidos de aplicativo com finalidade descrita, mas público e plataforma indefinidos. Devem pedir esclarecimento sem afirmar conclusão.
- 64 pedidos de três conceitos de campanha para produtos e serviços diferentes. Devem trazer três opções e manter o assunto pedido.
- 64 somas com operandos distintos. O oráculo confere o resultado numérico, com ou sem a expressão completa.
- 64 nomes de frameworks inventados. Devem acionar pesquisa e indicar incerteza, sem inventar documentação.

O gerador usa semente fixa e impede coincidências exatas com as perguntas do acervo. `tests/test_benchmark_chat_1024.py` verifica contagem, unicidade e a rejeição de respostas erradas. O relatório detalhado, com pergunta, backend, resposta, critério e tempo por caso, está em `model/chat_1024_benchmark_report.json`.

## Alcance e pendência

O resultado de 1.024/1.024 demonstra funcionamento nos padrões especificados. Ele **não** comprova desempenho geral em 1.024 assuntos inéditos: 768 casos reformulam assuntos conhecidos e os outros 256 são cenários compostos de quatro famílias. O backend das 768 perguntas de conhecimento foi `curated-memory`; os demais casos foram resolvidos por esclarecimento, resposta local estruturada, cálculo determinístico ou encaminhamento de pesquisa. Nenhum desses 1.024 acertos deve ser atribuído à geração livre dos pesos.

A avaliação neural isolada continua em 0/20, inclusive depois da correção posicional e dos novos treinos locais. O diagnóstico e os resultados estão em `Documentacoes/DIAGNOSTICO_PESOS_NEURAIS.md`. Para ampliar a capacidade de resposta fora do acervo, o próximo gate precisa de perguntas abertas sobre assuntos realmente não vistos, com referências independentes e revisão humana da qualidade. Os pesos só devem ser promovidos quando esse gate passar sem substituição por memória ou regras.
