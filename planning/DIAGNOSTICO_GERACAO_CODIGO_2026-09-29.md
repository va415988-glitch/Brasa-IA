# Diagnóstico do bloqueio crônico na geração de código

**Data:** 29/09/2026  
**Pedido reproduzido:** criar o aplicativo Horas de Estudo em HTML, CSS e JavaScript.  
**Checkpoint:** `model/godmode/context-32768-v1/candidate.safetensors`.  
**Dados da ablação:** [PROMPT_LENGTH_ABLATION_2026-09-29.json](baseline-v1/PROMPT_LENGTH_ABLATION_2026-09-29.json).

## O que o erro realmente significa

O runtime e o AgentCore conseguem inspecionar o workspace, aceitar uma proposta controlada, pedir aprovação, escrever e executar um check. O [experimento separado](BASELINE_CICLO_BRASA_2026-09-29.md) demonstrou isso com dois arquivos Python fornecidos explicitamente. O bloqueio do aplicativo surge **antes da primeira escrita**: o checkpoint não fornece uma proposta de implementação válida. `build.change` e `build.verification` são consequências corretas desse bloqueio, não a causa.

## Três observações que restringem a hipótese

1. O prompt normal produzido por `implementation_prompt` para Horas de Estudo tinha **882 tokens**. O treino de origem usou janelas de **512 tokens** e exemplos com mediana de **46 tokens**, p95 de **81** e máximo de **120** (404 exemplos de treino, medidos com o tokenizer do manifesto). O contexto de execução de 32.768 tokens não significa que os pesos aprenderam a usar instruções desse comprimento.
2. Uma instrução reduzida a **101 tokens** também não produziu JSON válido. Gerou 47 tokens, entrou em repetição de fragmentos e terminou em `structured-plan-invalid-json`. Logo, o comprimento do prompt contribui para a diferença entre treino e uso, mas **encurtá-lo não resolveu a tarefa**.
3. Nas 404 respostas do conjunto de treino de origem, **zero** continham a estrutura de saída `assumptions` + `operations` exigida pelo implementador. Os exemplos são principalmente respostas curtas em prosa; as respostas tinham mediana de 32 tokens. A tarefa de produzir código completo em vários arquivos e JSON estruturado está fora do comportamento supervisionado nesse treino. Isso não prova que nenhum treino futuro funcionará; mostra que o checkpoint ativo não foi ensinado diretamente para o contrato que hoje lhe é cobrado.

As duas tarefas de build do baseline (Water Ledger e Reading Shelf) e o pedido manual de Horas de Estudo falharam no mesmo estágio. Na [avaliação isolada do gerador](baseline-v1/GENERATOR_ISOLATED_2026-09-29.json), os dois casos do baseline tiveram `quality_stop_reason=repeated-fragment`, saída rejeitada e nenhuma chamada de ferramenta.

## Defeito de medição corrigido

O gerador podia parar quando o próximo token iniciaria um ciclo, mas aceitar o fragmento anterior como resposta final. Na instrução curta, o fragmento era texto degenerado com mais de 80 caracteres e foi marcado como `quality_gate_result=accepted`, apesar de não ser JSON. A rota de implementação agora exige JSON decodificável com as chaves `assumptions` e `operations` antes de aceitar uma geração estruturada. Após a correção, a mesma entrada termina em `quality_gate_result=rejected` e `quality_reason=structured-plan-invalid-json`. A validação completa de caminhos, operações e conteúdo continua em `parse_implementation_plan`.

Essa correção melhora a honestidade da telemetria e evita que texto inválido avance como proposta neural. **Não aumenta a capacidade de gerar código do checkpoint.**

## Decisão de engenharia

Não há evidência de que repetir o pedido, aumentar o limite de tokens, liberar o filtro de repetição ou adicionar uma receita específica resolva a capacidade geral. O próximo trabalho deve avaliar um **gerador de referência sob o mesmo contrato**, sem executar escrita, para medir qual parte da falha pertence aos pesos. Em paralelo, se o objetivo continuar sendo um modelo próprio, preparar treino para o contrato real:

1. Criar exemplos revisados de pedido + contexto observado → JSON de proposta válida, incluindo criação, edição, falha de verificação e correção. Exigir diversidade de projetos; não usar os casos reservados nem promover trajetórias bloqueadas como respostas corretas.
2. Medir os comprimentos de entrada e saída desses exemplos. O treino precisa conter sequências comparáveis às entradas reais e respostas longas o bastante para arquivos completos. A janela anunciada só pode ser considerada útil após avaliação semântica.
3. Antes de uma rodada grande, executar um ensaio de memorização controlada de poucas propostas. Se o modelo não conseguir reproduzi-las, investigar alinhamento de tokens, máscara de loss, EOS e decodificação. Esse ensaio valida o pipeline; não conta como generalização.
4. Avaliar em tarefas inéditas com validade de JSON, relevância dos arquivos, cobertura de requisitos e checks de produto. Comparar pesos isolados, gerador de referência e agente completo; promover checkpoint só após ganho reproduzível fora do treino.

**Gate imediato:** para os dois pedidos de build já registrados, obter ao menos uma proposta JSON válida e pertinente no teste isolado, sem receita de domínio e sem pedir que a pessoa forneça o código. Depois disso, reexecutar o ciclo completo em workspaces descartáveis. Passar testes unitários não substitui esse gate.

O [primeiro ensaio de SFT no contrato real](EXPERIMENTO_SFT_IMPLEMENTACAO_2026-09-29.md)
foi executado em checkpoint isolado. A perda caiu, mas nenhuma proposta passou
na avaliação inédita; o gate continua fechado.

## Verificação da mudança de código

- `PYTHONPATH=python .venv/bin/python -m pytest -q tests`: **363 passed**.
- `npm test` em `agent-core`: **73 passed**.
- Ablação curta repetida com o checkpoint ativo: saída rejeitada como JSON inválido, sem proposta de escrita.
