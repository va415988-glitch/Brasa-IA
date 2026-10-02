# Motores de execução locais

O chat ganhou dois recursos computacionais, integrados aos contratos Python, TypeScript e Rust. Eles trabalham sobre expressões, código e argumentos fornecidos em cada pedido. A avaliação calcula o resultado desses dados; o modelo não precisa ter memorizado a resposta.

| Motor | Entrada | Resultado observado |
| --- | --- | --- |
| `calculate` | Expressão e variáveis JSON | Valor calculado, tipo, operações, tempo e erro específico quando houver |
| `evaluate_function` | Arquivo `.py`, função, argumentos e argumentos nomeados | Resultado para essa entrada, arquivo, linha e SHA-256 da fonte avaliada |

O interpretador AST local suporta aritmética, condições, listas e dicionários, compreensões, geradores, laços, funções auxiliares do mesmo arquivo e recursão limitada. Os pedidos explícitos passam por extração de expressões e literais; a descoberta de ferramentas também disponibiliza os dois motores ao planejador. Essa extração é uma rota operacional de escopo definido, sem demonstração de compreensão geral de linguagem livre.

Os cálculos simples rodam dentro do serviço Python, inclusive sem workspace. A avaliação de funções segue o ciclo de proposta, execução pelo runtime Rust e síntese do resultado. O arquivo precisa estar no workspace selecionado. O serviço verifica o contrato, os argumentos, a identificação da fonte e o resultado antes de confirmar a avaliação. A avaliação do módulo inteiro e a suíte de testes continuam sendo operações distintas.

## Exemplos no chat

```text
Calcule sum(x*x for x in range(17)) + 23
```

Resultado verificado: `1519`. Alterar os números produz uma nova avaliação.

```text
Calcule (income-cost)/len(items) com variáveis {"income":1937,"cost":221,"items":[1,2,3,4]}
```

Resultado: `429.0`.

Para um arquivo existente com uma função compatível:

```text
Execute weighted([[2, 5], [7, 8]], bonus=3) em logic.py
```

Também aceita `Execute a função weighted em logic.py com argumentos JSON {"args":[[[2,5],[7,8]]],"kwargs":{"bonus":3}}`.

Pedidos de avaliação que já especificam função, arquivo e entradas recebem uma classificação operacional. Isso remove o questionário genérico sobre usuário, stack e escopo de produto que impedia o motor de ser chamado.

## Controle de progresso

O AgentCore registra observações por ferramenta e argumentos. Nos ciclos operacionais, uma observação estável bem-sucedida não é repetida sem mudança posterior observada. Duas falhas iguais exigem outra rota. O planejador recebe orientação para usar os resultados, mudar argumentos ou investigar outra hipótese.

Uma alteração observada pelas ferramentas de escrita invalida as observações; uma escrita que falha também permite nova inspeção, pois pode ter alterado parcialmente o estado. Na retomada, o histórico mantém essa regra. Consultas dinâmicas de processos, catálogo e fontes continuam disponíveis. Conversa e análise preservam seus controles de repetição já existentes.

Esse mecanismo trata ciclos de ferramentas. A repetição de prosa e a qualidade do código gerado dependem também do checkpoint e da decodificação.

## Escopo e limites

O motor interpreta um subconjunto de Python. Imports e instruções do topo do módulo não são executados. Classes, funções assíncronas, acesso a arquivos ou rede, objetos do ambiente e construções não suportadas produzem erros explícitos. Ele não oferece execução geral de Python nem isolamento por sistema operacional.

Os limites incluem 20.000 operações AST, prazo lógico de um segundo, até 1.000 itens por coleção/iteração, 24 chamadas aninhadas, 64 níveis de expressão, fonte de até 128 KiB, textos de até 8.192 caracteres e argumentos/resultado JSON de até 32 KiB cada. Há limites adicionais para inteiros, potências e expansão de valores compostos. Caminhos absolutos, traversal e links simbólicos são rejeitados.

`passed: true` comprova apenas a avaliação daquela entrada no subconjunto suportado. O campo `verification_scope` registra `single-input-in-supported-python-subset`. Testar um programa completo continua exigindo os checks do projeto. Os pesos próprios permanecem responsáveis pela geração autoral; interfaces criativas e generalização de programação ainda exigem treinamento e avaliações separados.

## Validação

- Python: 113 testes passaram, incluindo 12 dos motores e regressões de modelos, catálogo e contratos.
- TypeScript: 131 casos passaram com isolamento de testes desativado, além da checagem de tipos. Foram exercitados bloqueio de releitura idêntica, retomada, invalidação e propagação de falhas do motor.
- Rust: 46 testes passaram, incluindo a chamada ao interpretador a partir de um workspace temporário.
- Interface: os dois arquivos de regressão de streaming e histórico passaram.
- API e chat reais: 18/18 verificações passaram. Incluem argumentos novos, mudança de resultado com novas entradas, inteiros grandes, composição de geradores, interrupção de laços, rejeição de imports/acesso ao ambiente e síntese após uma única chamada à ferramenta de função.

As funções da auditoria foram escritas pelo avaliador e fornecidas como arquivos temporários. Esse resultado demonstra execução e integração; não mede geração neural de funções novas nem domínio geral de programação.

Relatório final: [execution-engines-runtime-2026-09-30.json](avaliacoes/execution-engines-runtime-2026-09-30.json). As rodadas anteriores foram preservadas: uma detectou o questionário indevido; outra detectou um campo opcional `stop_reason: null` rejeitado pelo contrato TypeScript. A correção omite esse campo em avaliações bem-sucedidas e comunica falhas com motivo explícito. O controlador preserva o estado bloqueado dessas falhas.

Para repetir a auditoria com os serviços iniciados:

```bash
.venv/bin/python scripts/audit_execution_engines.py --output /tmp/execution-engines-audit.json
```

O script cria fixtures temporárias e restaura o workspace ativo ao terminar.
