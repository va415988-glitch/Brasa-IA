# Qualificação do núcleo de programação — 02/10/2026

Foi implementado o avaliador de entrega que faltava ao núcleo `programming`.
O checkpoint ativo (`model/godmode/context-32768-v1/candidate.safetensors`) foi
medido e **reprovou: 0/96 no reservado e 0/96 na regressão**. Nenhum peso foi
treinado ou promovido, e o catálogo de núcleos não foi alterado.

## O que foi construído

| Peça | Papel |
| --- | --- |
| `python/programming_qualification.py` | Valida a proposta com o contrato do produto (`parse_implementation_plan`), grava os arquivos em diretório descartável, roda os testes da própria proposta e depois os testes independentes ocultos, em subprocesso com limites de CPU, memória e arquivo |
| `python/programming_qualification_tasks.py` | 192 tarefas Python com pedido preciso, solução de referência e casos ocultos |
| `scripts/prepare_programming_qualification.py` | Gera a bancada em `datasets/programming_qualification_v1/`, prova que todas as referências passam nos próprios testes e congela hashes em `protocol.json` |
| `scripts/evaluate_programming_qualification.py` | Gera uma proposta por tarefa. Backends: `checkpoint` (modelo próprio), `reference` e `null` (controles) |
| `scripts/certify_programming_core.py` | Reexecuta todas as saídas brutas, sem confiar em notas gravadas, e compara com os limiares do catálogo |
| `tests/test_programming_qualification.py` | 14 testes: formato da bancada, rejeição de comportamento errado, de testes ausentes, de laço infinito, de mutação dos argumentos e de tipos, bloqueio do arquivo reservado e limiares |

## Bancada

- Duas suítes de 96 tarefas, com 24 em cada domínio: `strings`, `lists`, `numbers`, `records`.
- **Reservado:** 96 designs novos, com função, assinatura e casos de borda distintos.
- **Regressão:** formas paramétricas simples, como as do treino de implementação, com parâmetros novos. Não prova transferência.
- Os arquivos que o gerador vê não contêm casos, respostas nem referências. Entradas, oráculo e referências ficam em arquivos separados, com hash no protocolo.
- Critérios, vindos do catálogo: ao menos 90% no total, 75% por domínio, zero respostas inseguras e 24 casos por domínio, em cada suíte.
- Uma tarefa só passa se o contrato for válido, os testes da própria proposta rodarem (pelo menos um) e passarem, e os testes ocultos passarem. Não há nota parcial. Os testes ocultos exigem tipo exato (`tuple` não vale por `list`) e que os argumentos não sejam modificados.

## Controles do avaliador

| Controle | Resultado esperado | Obtido |
| --- | --- | --- |
| Soluções de referência | 96/96 | 96/96 |
| Saída vazia | 0/96 | 0/96 |

Os testes automatizados cobrem ainda soluções plausíveis, mas erradas, que passam
nos próprios testes e reprovam nos ocultos.

## Resultado do checkpoint ativo

| Suíte | Aprovadas | Contrato válido |
| --- | ---: | ---: |
| Reservado | 0/96 | 0 |
| Regressão | 0/96 | 0 |

O modelo emite o token de fim de sequência logo após o pedido (1 token gerado,
`stop_reason: eos`) e o filtro de qualidade registra `structured-plan-too-short`.
Todas as 192 saídas brutas são vazias. O resultado é uma falha total, e não uma
questão de limiar. Isso confirma a auditoria de 30/09, que já apontava 0/2
propostas válidas.

Artefatos: `model/qualification/programming-v1/` (relatórios brutos, controles e
`certificate-active.json`).

## Limites

- Mede só funções Python isoladas. **Não mede integração em projeto existente**,
  que o catálogo exige para a prova completa (`executed-tests-and-unseen-integration`).
  Por isso o certificado usa o estado `passed_function_level`, com
  `registry_eligible: false`, e nunca `passed`.
- Os testes ocultos rodam em subprocesso com limites de recursos, **sem isolamento
  de rede ou sistema de arquivos**. Serve para avaliar modelos próprios, não código hostil.
- Depois de usada para decidir treino ou seleção, a bancada reservada deixa de
  ser reservada; é preciso criar tarefas novas.

## Reprodução

```bash
.venv/bin/python scripts/prepare_programming_qualification.py
.venv/bin/python scripts/evaluate_programming_qualification.py --suite reserved --backend checkpoint --checkpoint <pesos> --output <relatório>
.venv/bin/python scripts/certify_programming_core.py --reserved-report <r> --regression-report <g> --output <certificado>
```

O certificador recusa sobrescrever relatórios e certificados.
