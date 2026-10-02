# Quarta rodada: referência direta e diagnóstico de dados

**Data:** 29/09/2026. **Estado:** checkpoint principal mantido; nenhum ganho funcional inédito confirmado.

## Referência local sem Ollama

O modelo pré-treinado [Qwen2.5-Coder-1.5B-Instruct-GGUF](https://huggingface.co/Qwen/Qwen2.5-Coder-1.5B-Instruct-GGUF), na quantização Q4_K_M, foi executado diretamente pelo `llama.cpp` em CPU. O modelo serviu somente como referência experimental; não foi conectado à rota de produção da Brasa nem usado para treinar seu checkpoint.

O [avaliador isolado](../scripts/evaluate_reference_implementation.py) enviou os mesmos dois prompts inéditos do conjunto `implementation_sft_compact_extended_v2`, sem incluir as respostas de referência. Ele aplicou o parser de operações da Brasa, executou os testes produzidos quando presentes e, em seguida, executou os testes de referência em diretórios temporários. As respostas esperadas do próprio conjunto passaram 2/2, como controle positivo.

| Gerador | Contratos aceitos pelo parser | Testes de referência aprovados |
| --- | ---: | ---: |
| Checkpoint experimental da Brasa, rodada 3 | 0/2 | 0/2 |
| Qwen2.5-Coder 1.5B Q4_K_M, execução direta | 2/2 | 0/2 |

O [resultado bruto da referência](baseline-v1/REFERENCE_IMPLEMENTATION_COMPARISON.json) registra 0/2 JSON estrito, pois o modelo usou blocos Markdown; o parser real da Brasa os aceita e validou 2/2 contratos. Ele criou um pacote `app/` no lugar de `app.py` no caso de conversão de minutos e produziu lógica incorreta para iniciais no outro. Os dois falharam nos testes de referência. Não houve promoção nem substituição de modelo.

O Qwen 3.5 9B não recebeu pontuação comparável: a tentativa pelo Ollama não concluiu o primeiro pedido no limite de cinco minutos, e o uso de Ollama foi retirado do experimento por orientação do usuário. O avaliador conserva a opção de execução direta via `llama.cpp` para experimentos futuros, sem dependência do Ollama.

## Capacidade do checkpoint principal

O checkpoint ativo tem 5.639.680 parâmetros: 4.194.304 na tabela de posições (74,4%), 1.048.576 na incorporação de tokens compartilhada com a saída (18,6%) e 396.800 no restante, incluindo blocos Transformer e normalização (7,0%). Apenas as primeiras 512 posições receberam treino semântico original. A grande tabela posicional representa alocação de parâmetros, não evidência de raciocínio com 32.768 tokens. Isso motiva estudar uma arquitetura com posições compactas e mais capacidade nos blocos, mas não garante melhora sem dados e verificação.

## Fonte local de trajetórias

A [auditoria reproduzível](../scripts/audit_code_agent_implementation_data.py) examinou o primeiro shard local de `UltraData-Code-Agent` sem copiar seu conteúdo para treino. Das 2.330 trajetórias, 2.321 editam arquivos existentes; 191 incluem `Add File`, mas nenhuma dessas criações continha linhas adicionadas no patch inspecionado pelo extrator. Há 2.210 trajetórias com comando de teste depois do último patch e 1.893 com algum texto de aprovação posterior. Estes últimos números são candidatos, não exemplos verificados: um texto de aprovação posterior pode se referir a outro comando.

O corpus parece mais útil inicialmente para aprender reparos de código do que para ensinar criação de projetos do zero. Antes de treinar, o próximo preparador deve ligar o pedido original, o trecho-fonte lido, o patch final e o resultado do teste correspondente; deve descartar casos ambíguos e usar um conjunto inédito separado para decidir promoção.

## Próximo critério

Medir propostas completas que passam testes inéditos, com o checkpoint da Brasa como linha principal. Taxas de JSON, perda e exemplos vistos continuam diagnósticos auxiliares. Nenhuma porcentagem funcional deve subir no relatório sem execução comprovada.
