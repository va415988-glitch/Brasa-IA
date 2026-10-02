# Checkpoint de estabilização

Este gate transforma a validação do projeto em uma operação reproduzível. Ele
verifica sintaxe Python e JavaScript, testes do núcleo TypeScript, a suíte
unitária, benchmarks de roteamento, workflow, agente, harness, livros anexados,
deep learning, datasets, programação e o gate neural do checkpoint ativo.

## Execução

Na raiz do projeto:

    ./scripts/checkpoint_stabilization.sh

Os relatórios são gravados em:

- model/checkpoint/stabilization_report.json
- model/checkpoint/stabilization_report.md

O comando falha fechado: qualquer etapa com erro reprova o checkpoint. Um
resultado de 100% significa que todos os critérios automatizados passaram; não
é uma alegação de inteligência geral perfeita.

## Critérios operacionais

- sessões locais devem sobreviver a recarregamento da interface;
- tarefas têm estado persistente, eventos correlacionados e retomada segura;
- escritas exigem aprovação inline e verificação posterior;
- ferramentas permanecem limitadas ao workspace selecionado;
- respostas e resultados devem deixar evidência observável;
- qualquer falha ou ambiguidade deve terminar como pendência explícita, nunca
  como conclusão inventada.
