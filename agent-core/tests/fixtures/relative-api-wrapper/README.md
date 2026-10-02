# Meu dia

Aplicação local de tarefas para uma pessoa. Permite cadastrar, listar, concluir, reabrir e excluir tarefas. Títulos vazios são rejeitados pelo backend; os dados persistem em SQLite.

## Iniciar

Requer Python 3.10 ou superior com o módulo padrão sqlite3. Não há dependências para instalar.

```sh
python3 app.py
```

Abra http://127.0.0.1:8765. Encerre com Ctrl+C. O banco `tasks.sqlite3` fica ao lado de `app.py`; reiniciar a aplicação preserva os dados. Para outra porta ou arquivo: `python3 app.py --port 8766 --database /caminho/tasks.sqlite3`.

## Verificar

```sh
python3 -m unittest discover -s tests -v
```

Os testes usam servidores HTTP em portas temporárias e bancos isolados. Cobrem operações pela API, títulos inválidos, tarefa ausente, página inicial e persistência após reinício. O banco de uso normal não é alterado.

## Arquivos e API

- `app.py`: servidor em 127.0.0.1, validação e SQLite com consultas parametrizadas.
- `index.html`: interface responsiva ligada à API local.
- `tests/test_app.py`: testes HTTP e de persistência.
- `GET /api/tasks`: lista de `{id, title, completed}`.
- `POST /api/tasks` com `{"title":"Minha tarefa"}`: cria e retorna 201.
- `PATCH /api/tasks/1` com `{"completed":true}`: atualiza conclusão.
- `DELETE /api/tasks/1`: exclui a tarefa.

Requisições inválidas retornam 400 com `{"error":"mensagem"}`; tarefas ausentes retornam 404. Não há login ou integração com serviços externos. O servidor foi feito para uso local individual.
