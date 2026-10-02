# Revisão do professor Qwen3-Coder: run 20260927T230140

## Proveniência observada

- Arquivo recebido: `qwen3-coder-30b-a3b-instruct-20260927T231147Z-1-001.zip`
- SHA-256 do ZIP: `d58d39aead346a1905588c9aecdcefdb34ed5ba2e479355f6c8068cb8bec3859`
- Run concluído: `run_20260927T230140_634863Z`
- Tarefa: `8f6e7217-d267-4ff5-abcf-612ff87995ae`
- Modelo: `Qwen/Qwen3-Coder-30B-A3B-Instruct`
- Revisão: `b2cff646eb4bb1d68355c01b18ae02e7cf42d120`
- GPU registrada: NVIDIA A100-SXM4-80GB (80 GiB)
- Arquivo candidato SHA-256: `c5de00ad73dd003313a3ce5129df5417d0bb08e41fc48413bbb2e7dbf96473e5`
- SHA-256 do texto gerado: `c2a7720e994c9a48340770c503cb1fc62dc4b36e0f87ae27d5da9749371bbdb1`

O ZIP também contém cinco diretórios de runs sem arquivos. O único candidato JSON está acompanhado de manifesto que lista um arquivo e seu hash. A resposta levou cerca de 2 min 18 s e tem 4.659 caracteres. Isso confirma geração e persistência; não demonstra que o código foi executado.

## Revisão técnica estática

**Decisão: manter pendente; não aprovar para treino.** A resposta entrega estrutura, implementação e cinco testes unitários, mas não satisfaz completamente a tarefa:

1. `validate_and_parse` verifica se `id` existe e se é inteiro, mas não valida o conteúdo ou formato de `timestamp`.
2. A serialização CSV é feita por concatenação de strings. Valores com vírgulas, aspas ou quebras de linha podem corromper o CSV. Deve usar o módulo padrão `csv` (`csv.writer` ou `csv.DictWriter`).
3. `json.loads` pode retornar uma lista, número, string ou `null`. Para alguns desses valores, a expressão `'id' not in data` levanta `TypeError`, que não é capturado. O parser deve exigir objeto JSON e classificar esse caso como linha inválida.
4. A escrita usa a codificação padrão do ambiente, em vez de declarar UTF-8; a CLI também não tem testes de integração para arquivos e argumentos.
5. Os testes não cobrem timestamp ausente/inválido nem quoting CSV; portanto não detectariam os problemas acima.

Os exemplos de ordenação usam timestamps ISO 8601 no mesmo formato e fuso, então não demonstram a ordenação correta de formatos ou fusos diferentes. A tarefa pode ser corrigida e executada em workspace isolado, com os testes ampliados, antes de qualquer conversão para treino.

## Resultado da aprovação técnica

A proposta foi reconstruída e corrigida em `colab/reviewed_teacher_candidates/event_csv/`. O original dentro do ZIP permanece imutável.

**Implementação derivada aprovada como solução verificada para esta tarefa.** A revisão derivada exige `id` inteiro e timestamp ISO 8601 com fuso, rejeita valores JSON que não sejam objetos, ordena instantes em UTC, preserva o timestamp original e usa `csv.DictWriter` com UTF-8. Também inclui testes da CLI e de entradas inválidas.

Verificação executada na pasta da implementação:

```text
PYTHONPATH=. python3 -m unittest discover -s tests -v
Ran 9 tests in 0.002s
OK
```

Os nove testes cobrem entrada vazia, JSON inválido e não objeto, ID inválido/booleano, timestamp ausente/inválido/sem fuso, IDs duplicados, ordenação entre fusos, escrita CSV que requer quoting e caminho CLI de sucesso parcial com resumo de erros. Nenhum serviço Brave ou API externa é usado.

**A resposta bruta do professor continua não aprovada para treino como resposta correta.** Ela não continha as correções; a versão aprovada é uma implementação derivada e verificada, não o texto original. Para treinar comportamento de correção, preserve a resposta original como proposta inicial e registre o diff/correção e as evidências como turno posterior de feedback, depois de revisão de privacidade e da política do conjunto.

| Arquivo derivado | SHA-256 |
| --- | --- |
| `README.md` | `ea9a6bb510aab4fc4ac4cfa68bd29f75fc9dae24ea2a9065a69af723e4182993` |
| `event_csv/__init__.py` | `8d3251267facb38a78d2352e598d964723a897bc9e99498db215aac0316c6180` |
| `event_csv/cli.py` | `23b2b7a176ea1cba041ca8440b75c3471b55f2f0c15a0e79c458b770dc4bffb6` |
| `event_csv/validator.py` | `672065bc255820405591575f3a006005bcbd02ee344c771b99fed387024a1bc8` |
| `tests/test_event_csv.py` | `cda90b4c8134a98f787425ed033bacfd5b577a2fa796d82f2c8a55d01ba12736` |
