# Nova tentativa de aprovação — 20/09/2026

**Resultado: ainda reprovado. O modelo em uso não foi substituído.**

## Correções efetivas

1. Inicialização opcional `scaled-normal-v1`: matrizes com escala controlada, projeções residuais ajustadas e camadas inicializadas separadamente. Novos treinos pelo treinador principal usam essa política. Carregar checkpoints antigos preserva seus pesos.
2. Comparação controlada antes do treino: a inicialização anterior apresentou loss 154,86 e desvio padrão de embedding 1,00; a corrigida apresentou loss 6,96 e desvio 0,02. A máscara causal passou pelo teste de invariância a tokens futuros; os tokens históricos correspondem ao tokenizer utilizado.
3. Promoção exige evidência comportamental e hashes correspondentes de pesos, configuração e tokenizer. Alterar apenas `context_length` não passa: as dimensões dos embeddings também são verificadas.
4. A cópia de safetensors preserva a extensão, os metadados e a evidência, recusa sobrescritas e audita o destino novamente. O pré-voo estrutural deixou de declarar elegibilidade sem evidência comportamental.

## Treinamento executado

- Novo treinamento do zero sobre a mesma arquitetura, sem importar pesos externos: 3.487.232 parâmetros, contexto de 256 tokens.
- 227 exemplos de treino, 19 de validação e **8 pedidos novos reservados**; nenhum pedido reservado entrou no otimizador ou na seleção.
- Limite de 1.000 passos; parada antecipada aos **600**, após três avaliações sem melhora. Melhor checkpoint: passo **300**.
- Inicialização corrigida, taxa inicial 0,0005, batch 8, quatro threads de CPU.
- Loss inicial da nova rede: **6.9614**. Melhor loss de validação: **4.1114**.
- Loss nos oito pedidos reservados: **3.9909**, contra **4.7957** do checkpoint de referência compact-06: redução relativa de **16.8%**.
- Do passo 300 ao 600, a validação piorou de 4,11 para 4,45 enquanto a loss de treino caiu: sinal de memorização do conjunto disponível.

## Avaliação que determina o uso

**0/8 respostas aprovadas: 2 vazias e 6 incoerentes.** A redução da loss não foi interpretada como aprovação. As respostas completas e o julgamento estão em `run-01/candidate.safetensors.evaluation.json`; a avaliação usou geração greedy de até 128 tokens.

O checkpoint também permanece abaixo dos requisitos de 8192 tokens de contexto e de comprovação de geração longa de 4096 tokens. Ferramentas, retenção longa, regressão e custo de inferência isolada não receberam uma nova aprovação nesta rodada. Os motivos formais estão em `promotion_audit.json`.

A correção de inicialização remove um problema real, mas o experimento não demonstrou uma base de linguagem confiável. Repetir este currículo não está resolvendo a generalização. O objetivo solicitado — modelo aprovado para uso — **permanece não alcançado**. Não foi atribuída aprovação manual nem reduzido o requisito de qualidade.

## Verificação e reprodução

Passaram **65 testes**: 49 do assistente, 10 de evidência/promoção, 3 de inicialização/causalidade e 3 do pipeline de ajuste. Um smoke test adicional de dois passos confirmou a política de inicialização no treinador principal. Esses testes validam o software, não aprovam as respostas neurais.

```bash
.venv/bin/python python/finetune_assistant.py --checkpoint model/checkpoints/compact-06-augmented-v2.safetensors --output-dir model/training/senior-creative-v2/repro-01 --heldout model/training/senior-creative-v2/heldout.jsonl --from-scratch --steps 1000 --eval-every 100 --patience 3 --threads 4 --batch-size 8 --learning-rate 0.0005
```

O diretório de saída deve ser novo. Os oito pedidos agora são um conjunto de teste conhecido; se forem usados para orientar novas escolhas, uma futura alegação de avaliação inédita exige outro conjunto reservado. Manifestos, hashes do código, ablação e histórico foram preservados nesta pasta.
