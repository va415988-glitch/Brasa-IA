# Programação verificável — 30/09/2026

As operações de programação foram ampliadas e testadas na aplicação em execução. A auditoria final aprovou **8 de 9 cenários**. O cenário de implementação inédita permanece reprovado. Isso mede esta entrega; não comprova domínio geral de programação.

## Mudanças aplicadas

- `inspect_code` aceita um arquivo direto, além de pastas. O comportamento anterior pulava arquivos e devolvia zero símbolos.
- Python usa `ast`; JavaScript e TypeScript usam o compilador TypeScript instalado no próprio projeto. A inspeção informa declarações, escopos, parâmetros, linhas, chamadas e expressões de retorno, sem importar nem executar o código inspecionado. As respostas citam essas observações.
- Outras linguagens têm navegação lexical, identificada explicitamente. Ela não equivale a análise semântica nem execução.
- `project_checks` reconhece projetos aninhados e escolhe o projeto do arquivo informado. `all` percorre os projetos e perfis reconhecidos dentro de um orçamento compartilhado.
- Saída padrão e saída de erro são drenadas simultaneamente. O resultado mantém uma amostra limitada e o final da saída, onde ficam os resumos de teste. Os processos e seus descendentes são encerrados quando o orçamento termina.
- Nenhum verificador e zero testes deixam de produzir aprovação. Resultados informam se cobrem sintaxe, tipos, construção ou testes. Um pedido de testes de comportamento não pode ser concluído apenas com sintaxe válida.
- A interface aguarda até 65 segundos por verificações; o executor do agente usa esse prazo para `project_checks`. O orçamento dos subprocessos continua limitado, com 45 segundos por comando e 55 segundos por chamada.
- A geração preserva `<`, `>` e `|`, que o filtro anterior confundia com tokens de protocolo. Planos JSON usam validação estrutural, em vez da heurística de vocabulário para prosa. A penalidade de repetição considera a resposta gerada, sem penalizar os tokens do pedido. Exceções do decoder preservam seus diagnósticos.

Nenhuma receita específica para o algoritmo da auditoria foi adicionada. Os pesos do modelo não foram alterados e nenhum modelo externo foi instalado.

## Verificações disponíveis

| Ambiente | Perfis | O que observam |
| --- | --- | --- |
| Python | `unittest`, `pytest`, `python-syntax` | Suíte existente ou parsing sem execução |
| Node / JavaScript / TypeScript | `npm-test`, `node-test`, `npm-check`, `npm-build`, `node-syntax` | Scripts existentes, testes Node sem manifesto ou sintaxe JavaScript |
| Rust | `cargo-test` | Testes locais com Cargo em modo offline |
| Go | `go-test` | Testes, usando o toolchain local e dependências já disponíveis |
| C / C++ | `c-syntax`, `cpp-syntax` | Verificação do compilador; não executa testes de comportamento |
| Shell | `shell-syntax` | Parsing pelo Bash; não executa o script |

Os perfis são fixos. Os comandos exigem os programas e dependências locais correspondentes; não instalam dependências automaticamente. Um script chamado `test` ainda precisa conter testes úteis: sua aprovação não prova que os requisitos do produto foram cobertos.

## Resultados reais

| Cenário | Resultado |
| --- | --- |
| Arquivo Python com função assíncrona e classe | Aprovado |
| Agente explica funções com referência ao arquivo | Aprovado |
| TypeScript com exports, arrow function e método | Aprovado |
| Testes Node sem `package.json` | Aprovado, 1 teste |
| Suíte Python vazia | Aprovado no teste da auditoria: o verificador rejeitou zero testes |
| Teste Python produzindo 200 KB de saída | Aprovado, sem deadlock e com truncamento explícito |
| Teste Go | Aprovado, 1 teste |
| Verificação de C++ pelo compilador | Aprovado como sintaxe, sem alegar teste de comportamento |
| Criar `interval_union` e seus testes | Reprovado: o checkpoint não produziu uma proposta estruturada válida |

A comparação das oito operações foi **0/8 antes e 8/8 depois**. O relatório inicial completo registra 0/9, mas seu nono caso usava incorretamente a aprovação legada e recebeu HTTP 400. O harness foi corrigido para usar `agent-request/v2` e a retomada por aprovação de uma ação concreta. A falha atual da implementação foi observada com esse contrato correto.

Quando a implementação inédita conseguir concluir, o harness também executará cinco casos funcionais independentes, que não são fornecidos ao gerador. Nesta execução eles não puderam rodar, porque não houve implementação válida.

Relatórios:

- [Auditoria inicial](avaliacoes/programming-before-2026-09-30.json)
- [Auditoria final](avaliacoes/programming-after-2026-09-30.json)
- [Avaliação direta da geração própria](avaliacoes/programming-generation-2026-09-30.json)
- [Regressão das operações do agente](avaliacoes/programming-operational-regression-2026-09-30.json)

As verificações automatizadas aprovaram 136 testes Python distribuídos entre ferramentas de programação (14), filtros de geração (5), decoder (5), propostas (19), fluxo do servidor (81) e catálogo (12). O check TypeScript e os 17 arquivos de testes do núcleo passaram. Rust aprovou 45 testes do runtime e 1 teste do corpus. As regressões de streaming e de estado final da interface também passaram.

A regressão pela API real aprovou **6/6** operações: pesquisa, consulta de habilidades, leitura de arquivo, recuperação de caminho ausente com streaming, execução de testes e consulta de página.

## Gargalo restante

O checkpoint em uso possui 5.639.680 parâmetros, duas camadas, dimensão interna 128 e contexto de treino declarado de 512 tokens. A janela de execução de 32.768 tokens não comprova que ele aprendeu tarefas longas ou programação geral. A avaliação direta, sem receitas, produziu **0/2 propostas válidas** nas tarefas inéditas `minutes` e `initials`, além da falha operacional do algoritmo.

O próximo avanço exige treinamento e avaliação do modelo próprio para geração de código e reparos. A promoção de novos pesos precisa depender de tarefas inéditas, testes independentes de comportamento, preservação dos contratos existentes e regressões de uso das ferramentas. Aumentar instruções ou acrescentar receitas para cada pergunta não demonstra essa capacidade.

## Teste inicial reproduzível

Com a aplicação iniciada, execute na raiz do projeto:

```bash
.venv/bin/python scripts/audit_programming_workflows.py --output /tmp/programming-audit.json
```

O teste usa projetos descartáveis e restaura o workspace anterior. A saída atual esperada é **8/9** e código de saída **1**, porque a lacuna de geração continua real. Um resultado 9/9 futuro exigirá também a aprovação dos casos independentes do algoritmo; ainda será necessário ampliar a avaliação para outras tarefas e stacks.
