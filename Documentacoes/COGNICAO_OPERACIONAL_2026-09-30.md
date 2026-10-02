# Cognição operacional — implementação e evidências

## Resultado

O ciclo de conversa agora permite que o planejador escolha responder, consultar
informações ou declarar uma lacuna que impede a conclusão. O caminho está
integrado entre AgentCore TypeScript, contrato HTTP do planejador e ModelService
Python. A capacidade do checkpoint ativo de tomar essas decisões **não foi
demonstrada**: a avaliação neural reservada desta mudança teve **0/4** aprovações.

## Comportamento implementado

- A consulta informa o que falta saber, usando o campo `gap` como razão da ação.
- O contexto contém objetivo, restrições, histórico, catálogo e observações com IDs.
- O catálogo Python é a interseção das consultas conhecidas e das capacidades
  fornecidas pelo núcleo; a disponibilidade real continua sendo verificada no núcleo.
- Cada resultado volta ao planejador. Uma conclusão após consultas precisa citar
  IDs existentes de observações bem-sucedidas e utilizáveis. Referências inventadas,
  falhas e buscas vazias são rejeitadas como suporte factual.
- Uma proposta inválida pode receber uma correção limitada. Falta de geração
  válida ou bloqueio explícito chega ao núcleo como pendência, sem virar sucesso.
- Consultas idênticas não são executadas repetidamente. O orçamento de conversa
  continua limitado a seis ações, ou ao limite menor configurado.
- Leitura local exige workspace explícito. Escrita, processos e gravação no corpus
  não pertencem à política de conversa. O executor mantém autoridade final.
- A seleção do workspace é adiada até a leitura; inclusive a prévia de uma
  conversa deixa de inspecionar arquivos desnecessariamente.
- Saudações e cálculos aritméticos simples mantêm as rotas determinísticas já
  existentes. Pedidos mistos não são reduzidos à parte aritmética.

O protocolo registra decisões e lacunas curtas, não uma cadeia privada de pensamento.
O conteúdo de fontes continua sendo dado não confiável, sem autoridade para
ampliar o catálogo ou autorizar ações.

## Validação

| Camada | Resultado | O que comprova |
| --- | --- | --- |
| AgentCore | 114 testes aprovados | Fluxo, políticas, consulta, reavaliação, limites e regressões |
| TypeScript | `npm run check` aprovado | Compatibilidade estática dos contratos e consumidores |
| Python | 104 testes e 46 subtestes aprovados | Propostas, validações e regressões de diálogo/agente |
| Integração TypeScript/Python | 2 testes aprovados | Serialização, validação Python, retorno da observação e preservação do bloqueio |
| Checkpoint ativo | 0/4 | Não demonstrou decisões válidas nos casos avaliados |

Os testes de integração usam geração e ferramentas simuladas explicitamente. Não
são testes de entendimento do modelo nem de pesquisa web ao vivo. O benchmark
neural usa o checkpoint real e observações sintéticas, sem executar ferramentas,
registrar traces de treino, promover pesos ou substituir falhas por respostas curadas.

O relatório reproduzível está em
[avaliacoes/cognicao-neural-2026-09-30.json](avaliacoes/cognicao-neural-2026-09-30.json).
Ele inclui o hash do checkpoint, orçamento, decisões e diagnóstico de geração.
O checkpoint avaliado é `model/godmode/context-32768-v1/candidate.safetensors`.
Seus metadados registram contexto de treino de 512 tokens e contexto de execução
de 32.768 tokens. As gerações foram interrompidas por repetição ou baixa diversidade
e não produziram o envelope solicitado. Isso não permite atribuir o problema
apenas ao formato JSON ou apenas ao contexto: é necessário avaliar ambos.

## Reprodução

Na raiz do projeto:

```bash
.venv/bin/python -m pytest -q tests/test_cognitive_dialogue.py tests/test_model_server_agentic.py tests/test_dialogue_api.py
npm run check --prefix agent-core
npm test --prefix agent-core
node --experimental-strip-types --test agent-core/tests/cognitive-python.integration.ts
.venv/bin/python scripts/evaluate_cognitive_dialogue.py --report /tmp/cognicao-neural.json
```

A integração precisa do Python e das dependências locais em `.venv`. A avaliação
neural usa `selected_checkpoint()` por padrão; `--checkpoint` permite comparar
um candidato explicitamente. Nenhum desses comandos promove o candidato.

## Limites e implicações

Validar IDs comprova procedência da observação, não que a fonte sustente toda a
resposta. Contradições, relevância, necessidade da consulta e compreensão semântica
ainda precisam de avaliações independentes. A bateria neural tem apenas quatro
casos diagnósticos; não é uma medida abrangente de inteligência.

Pedidos tipados antes atendidos por fallback genérico podem agora ficar bloqueados
quando o modelo não produzir uma decisão válida. Essa mudança fica explícita;
não foi escondida com respostas prontas. A rota legada sem contrato cognitivo
continua disponível. Nenhum serviço foi reiniciado e nenhum peso foi treinado,
trocado ou promovido nesta tarefa.

O próximo avanço nos pesos deve ser medido com trajetórias curtas de decisão,
consulta, resultado e revisão, separadas de um conjunto inédito de avaliação.
Antes de ampliar dados ou contexto, um candidato precisa gerar decisões válidas,
usar a evidência correta e reconhecer falhas. Exemplos que já estão no treino ou
respostas provenientes de regras não devem ser contabilizados como ganho neural.
