# Programação guiada por código e exemplos — 30/09/2026

O pedido do conversor falhava antes de criar qualquer arquivo. O checkpoint próprio não produzia uma proposta estruturada válida, e o fallback existente não atendia ao pedido. A reprodução anterior registrou `blocked`, zero aprovações e zero arquivos.

O novo motor simbólico usa a função e os resultados esperados fornecidos no pedido. Não contém uma receita de conversão de temperatura nem altera o checkpoint neural. Ele prepara uma proposta de arquivos que passa pelos validadores e pelas aprovações existentes do runtime.

## Comportamento implementado

1. Reconhece uma função Python fornecida, os caminhos relativos da implementação e dos testes, e exemplos explícitos de entrada e saída.
2. Propõe a criação da implementação original, ainda com o defeito, e de testes `unittest` separados.
3. Após aprovação, executa a suíte real pelo perfil registrado `project_checks`.
4. Se a suíte falha, lê a implementação confirmada no workspace e avalia mudanças pequenas de constantes e operadores no intérprete AST limitado.
5. Só propõe um reparo quando uma única mudança da busca satisfaz todos os exemplos. Uma busca incompleta ou ambígua não autoriza uma escolha.
6. Mostra o caminho e o diff da correção antes da segunda aprovação. Após aplicar, executa os mesmos testes novamente.
7. A resposta final preserva a saída real da primeira execução com falha e da última execução aprovada. O acompanhamento recebe os eventos da verificação observada.

Os arquivos de teste não são alterados para acomodar a correção. Arquivos já existentes não são sobrescritos pela proposta inicial. Edições continuam exigindo correspondência exata do trecho lido e aprovação.

## Outros defeitos encontrados na verificação

- Uma edição do mesmo tamanho, realizada dentro do mesmo segundo, podia reutilizar bytecode Python antigo. Cada verificação Python agora usa um diretório temporário de cache e não grava bytecode. O teste de regressão preserva deliberadamente tamanho e timestamp para reproduzir esse defeito.
- O último evento de aprovação perdia o caminho e o diff de `apply_repair`; a interface selecionava esse evento. Ele agora preserva ambos, inclusive nos cartões reconstruídos após a retomada.
- Checks realizados como ferramentas não publicavam os eventos usados pelo acompanhamento. O acompanhamento podia mostrar “Nova verificação necessária” após uma execução aprovada. Agora recebe a observação concreta do check.

## Evidências

- `avaliacoes/example-programming-before-2026-09-30.json`: reprodução do bloqueio original, sem escrita.
- `avaliacoes/example-programming-runtime-2026-09-30.json`: auditoria pela API real em projetos temporários, incluindo aprovações, arquivos, saída dos checks, hashes dos testes, diffs e acompanhamento.
- `avaliacoes/example-programming-ui-2026-09-30.jpg`: resultado observado pela interface do chat.
- `scripts/audit_example_programming.py`: auditoria reproduzível; restaura o workspace anterior ao terminar e só aprova ações dos projetos descartáveis que cria.

Casos da auditoria:

| Caso | Resultado exigido |
| --- | --- |
| Conversor fornecido pelo usuário | Três falhas com `+ 30`; mesmos três testes aprovados com `+ 32`; entrada adicional `37.5 → 99.5` |
| Soma de lista e argumento nomeado | Reparar multiplicador `2 → 3`; três testes aprovados; entrada adicional com resultado `41` |
| Comparação no limite | Reparar `< → <=`; três testes aprovados; duas entradas adicionais próximas de zero |
| Duas correções possíveis | Executar os testes e manter a tarefa pendente, preservando a implementação original |

As entradas adicionais não participam da busca do reparo. Os exemplos e nomes desses casos existem no script de auditoria, não no motor instalado.

Verificação local: 10 testes do motor de exemplos, 19 de propostas proativas, 14 das ferramentas de programação, 81 do planejamento Python e 134 do AgentCore. TypeScript e JavaScript também passaram na verificação de sintaxe/tipos.

## Limites concretos

Este motor resolve uma classe limitada de pedidos: funções Python puras fornecidas no texto, com 2 a 12 exemplos literais explícitos e uma correção pequena. Aceita fontes de até 8 KiB e 500 nós AST; busca até 160 candidatos por até dois segundos. Imports, efeitos externos, anotações, decoradores e argumentos variádicos não são suportados neste caminho. Não sintetiza aplicações arbitrárias nem prova correção fora dos casos verificados.

A geração livre de programas pelo checkpoint próprio continua sendo uma capacidade separada que exige avaliação e evolução. O ganho entregue aqui é transformar código e exemplos disponíveis em arquivos, execução, investigação, reparo e evidências observáveis, sem depender de o modelo gerar o JSON operacional corretamente.

Para repetir o teste do conversor, atualize a interface e use um workspace vazio. Aprovar a primeira proposta cria os arquivos e executa os testes; aprovar a segunda aplica o diff e executa a suíte novamente.
