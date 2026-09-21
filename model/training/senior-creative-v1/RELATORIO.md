# Treinamento de engenharia e criatividade — 20/09/2026

**Resultado: treinamento executado; candidato reprovado para uso.** Nenhum checkpoint ativo foi substituído. A redução de loss não se converteu em respostas úteis.

## Trabalho executado

- Duas rodadas de continuação dos pesos, totalizando 1.000 passos (600 + 400), em CPU.
- Modelo de 3.487.232 parâmetros, 4 camadas e janela de 256 tokens; tokenizer preservado.
- 54 exemplos autorais sintéticos novos de engenharia, criatividade e confiabilidade.
- 227 exemplos de treino, 19 de validação e 12 pedidos autorais reservados; 566 janelas de treino incluindo variantes com perfil.
- Deduplicação por pergunta normalizada; exclusão das perguntas de avaliação; máscara de loss apenas nas respostas; clipping, warmup, decaimento e seleção pela validação.
- Comparação de quatro checkpoints de origem. Checkpoints, splits, hashes, histórico e respostas antes/depois preservados.

## Resultados

| Rodada | Origem | Passos | Loss de validação antes → depois | Loss final reservada antes → depois |
| --- | --- | ---: | --- | --- |
| 01 | compact-08-gate-focus | 600 | 30.9860 → 5.3452 | 29.8613 → 5.0199 |
| 02 | compact-06-augmented-v2 | 400 | 5.0450 → 4.4194 | 4.9317 → 4.1996 |

A rodada 02 foi selecionada pela menor loss de validação. Seu ganho relativo de loss nos pedidos reservados foi **14.8%** em relação ao próprio checkpoint de origem. Isso mede previsão de tokens com respostas de referência, não sucesso de tarefas.

**Revisão das 12 respostas inéditas: 0 aprovadas; 5 vazias e 7 incoerentes.** Geração greedy, até 128 tokens, com o mesmo prompt de avaliação para cada origem/candidato. As respostas completas estão em `run-02/report.json` e `quality_review.json`.

No teste legado, o modelo ativo marcou 10/12 e o candidato 0/12. Esse teste é apenas de regressão: suas perguntas aparecem no corpus histórico, e o filtro lexical aceita algumas respostas com finais incoerentes. Não é uma medida independente de competência.

## Decisão

**Não promover.** Além da falha qualitativa e da regressão, o checkpoint não atende à política de contexto mínimo de 8192 tokens nem à exigência de geração profissional. A auditoria está em `promotion_audit.json`. Os originais permanecem intactos, conforme `original_checkpoints_integrity.json`.

A validação inclui replay possivelmente visto no treinamento original; apenas os 12 pedidos autorais novos foram reservados desta rodada e não usados no otimizador ou na seleção. Não houve avaliação humana independente. Os dados sintéticos são exemplos didáticos, não certificação de competência.

A evidência deste experimento indica que o ajuste curto sobre a base atual é insuficiente para formar linguagem e seguir instruções de modo confiável. O próximo marco técnico é demonstrar geração coerente em uma base com pré-treinamento mais amplo, antes de investir em especialização sênior ou ampliar a janela. Não foi demonstrado que a arquitetura atual atingiu seu limite absoluto.

## Verificações

49 testes do assistente + 3 testes do pipeline passaram. Cinco exemplos Python do currículo foram executados com verificações de comportamento. Essas verificações validam o pipeline e os exemplos, não a qualidade dos pesos gerados.

## Arquivos e reprodução

- Candidato experimental escolhido: `run-02/candidate.safetensors` e metadados adjacentes.
- Treinador: `python/finetune_assistant.py`.
- Currículo: `python/data/senior_creative_v1.jsonl`; gerador: `python/build_senior_curriculum.py`.
- Procedência e parâmetros completos: `run-01/manifest.json` e `run-02/manifest.json`.

Executar da raiz, com diretório novo:

```bash
.venv/bin/python python/finetune_assistant.py --checkpoint model/checkpoints/compact-06-augmented-v2.safetensors --output-dir model/training/senior-creative-v1/repro-02 --steps 400 --threads 3 --batch-size 8 --learning-rate 0.00015
```

O comando produz artefatos experimentais e não muda o modelo em uso.
