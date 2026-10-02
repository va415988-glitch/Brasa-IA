# event_csv — implementação revista

Implementação corrigida a partir da proposta bruta do professor do run `run_20260927T230140_634863Z`. A resposta original permanece preservada no ZIP e não foi editada.

## Contrato

- Cada linha JSONL deve ser um objeto com `id` inteiro (booleanos não contam como inteiros) e `timestamp` ISO 8601 com fuso horário.
- Linhas malformadas, inválidas ou com IDs duplicados são descartadas e contadas; a primeira ocorrência válida do ID é mantida.
- A ordenação compara instantes normalizados para UTC. O texto original do timestamp é preservado no CSV.
- O CSV UTF-8 contém as colunas `id,timestamp`; o módulo `csv` cuida de quoting e escaping.
- A CLI retorna 0 sem linhas descartadas, 1 quando termina com descarte parcial, e 2 em erro de I/O.

## Verificação

Na raiz deste diretório, execute:

```bash
PYTHONPATH=. python3 -m unittest discover -s tests -v
```

Exemplo de uso:

```bash
PYTHONPATH=. python3 -m event_csv.cli --input events.jsonl --output events.csv
```
