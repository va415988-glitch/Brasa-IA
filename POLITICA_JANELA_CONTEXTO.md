# Política de crescimento da janela de contexto

Esta política é um requisito de qualidade do agente. A janela de contexto é parte da capacidade cognitiva operacional e não pode ser tratada como um número cosmético de configuração.

## Regra permanente

- Abaixo de **8.192 tokens**, o checkpoint não pode ser promovido como agente principal.
- O alvo inicial de produção é **16.384 tokens**.
- A janela só pode crescer; nunca deve ser reduzida silenciosamente.
- Alterar `context_length` sem treinar ou adaptar os pesos correspondentes é inválido.
- Checkpoints abaixo do mínimo devem ser marcados como experimentais.

## Escada de promoção

| Estágio | Janela | Uso | Critério mínimo |
|---|---:|---|---|
| Fundação | 8.192 | primeiro agente útil | retenção de requisitos e código multiarquivo |
| Produção inicial | 16.384 | agente principal | tarefas longas sem perda de contexto |
| Expansão | 32.768 | projetos médios | consistência entre sessões e tool calls |
| Escala | 65.536+ | projetos grandes | memória hierárquica, recuperação seletiva e custo controlado |

## Gates obrigatórios para aumentar

Cada novo checkpoint precisa passar por todos os gates:

1. **Compatibilidade:** pesos, embeddings posicionais, máscara causal e tokenizer devem suportar a nova janela.
2. **Retenção:** o agente deve recuperar requisitos colocados no início de uma tarefa longa.
3. **Programação:** deve editar múltiplos arquivos sem perder contratos, imports ou testes.
4. **Ferramentas:** deve continuar corretamente após várias chamadas de ferramentas.
5. **Verificação:** deve executar checks e relatar falhas sem declarar sucesso indevido.
6. **Regressão:** não pode degradar conversas curtas, português, código ou segurança.
7. **Custo:** memória e latência precisam ser medidas antes da promoção.
8. **Rollback:** o checkpoint anterior deve permanecer disponível até a nova versão ser aprovada.

## Operação segura

O runtime deve expor, em toda resposta, a janela efetiva do checkpoint e o estágio de promoção. Se a solicitação exceder a janela, o agente deve compactar, dividir a tarefa ou pedir uma etapa intermediária — nunca truncar silenciosamente.

O crescimento será acompanhado por memória hierárquica, recuperação seletiva, resumos verificáveis e execução incremental. Mais tokens ampliam a capacidade, mas não substituem organização, avaliação e controle de evidências.

## Regra independente de geração

Contexto e geração são capacidades diferentes. Um checkpoint não é elegível
para uso profissional se entender uma tarefa longa, mas não conseguir produzir
uma implementação longa de forma contínua.

Nenhum checkpoint será promovido sem comprovar geração contínua de código,
respostas e planos longos, sem truncamento silencioso, sem repetição destrutiva
e com continuidade após chamadas de ferramentas. O alvo inicial de avaliação é
4.096 tokens gerados por rodada; esse alvo deverá crescer junto com a janela de
contexto.
