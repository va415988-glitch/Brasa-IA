# Episódios de programação: lote candidato v001

O exportador `export_verified_episodes_v001.py` leu 97 tarefas persistidas. Encontrou 3 com estado concluído, efeito de escrita observado, snapshot dos arquivos alterados e resultado real de `project_checks` executado com código de saída 0. Os outros 94 registros foram excluídos nesta coleta: 19 não concluídos e 75 sem relatório de verificação aprovada.

| Tarefa | Origem | Arquivos finais | Verificação |
| --- | --- | --- | --- |
| `task-84d54d9a-a92b-4c8f-aaac-51be64baea69` | Workspace do usuário | `todo_cli.py`, `tests/test_todo_cli.py` | `unittest`: 4 testes, saída 0 |
| `task-c7df8328-654f-4a73-a535-dd8c3b674932` | Workspace temporário de teste | `app.py`, `test_app.py` | `unittest`: 4 testes, saída 0 |
| `task-6d7d1bfe-aaea-450b-9598-7644dd1e5ec8` | Workspace temporário de teste | `app.py` | `unittest`: 4 testes, saída 0 |

Os 3 registros são **candidatos**, com `human_reviewed: false` e `safe_to_train: false`. A execução dos testes comprova apenas o comportamento coberto por esses testes. Os dois exemplos do workspace temporário são exercícios pequenos e podem contaminar uma avaliação similar; devem ser comparados com o conjunto reservado antes de qualquer aprovação. O exemplo da CLI também requer revisão do código, privacidade e utilidade pedagógica. Nenhum entra automaticamente no treino exportado com 37/3 registros.

## Decisão da revisão

Comparei os pedidos e o código com `corpus/eval/programming_tasks.jsonl`. Os dois exercícios de `add(left, right)` reproduzem as tarefas reservadas `task-py-sum` e `task-py-test` em conteúdo e finalidade; foram excluídos. Revisei o código e os quatro testes da CLI, o resultado de `apply_batch`, os snapshots e a saída completa de `project_checks`. Não encontrei segredo ou caminho pessoal no JSONL exportado. A CLI foi **retida para revisão humana**. O registro de decisão técnica, preso ao hash de cada linha, está em `episode_review_registry_v001.json`.

Ao aplicar o registro com `curate_verified_episodes_v001.py`, o resultado é **1 episódio para revisão humana e 2 excluídos**. O episódio retido continua com `human_reviewed: false` e `safe_to_train: false`: ainda falta a aprovação do usuário, sua conversão para exemplos de chamadas de ferramenta e uma avaliação nova antes de treino.

O usuário informou que executou a célula no Colab e obteve o run `/content/drive/MyDrive/IA-Local/datasets/curated/episodes/v001/runs/run_20260927T190529_737325Z`. A saída remota foi informada pelo usuário; a reprodução local da mesma curadoria produziu 1 episódio retido e 2 excluídos.

O JSONL gerado contém pedido, conteúdo anterior lido quando disponível, argumentos da chamada de ferramenta, operações observadas, snapshots finais com SHA-256 e comando/saída reais da verificação. Caminhos absolutos dos workspaces não são exportados. A exportação local atual está em `/tmp/brasa-episodes-v001.jsonl` (20.878 bytes; SHA-256 `f1819016c77a907df6e17207d060cd2bf6e08c329dbda97a3ea1147bec5a6271`). Para regenerar após novos episódios:

```bash
python3 colab/export_verified_episodes_v001.py --output /tmp/brasa-episodes-v001.jsonl
```

## Guardar os candidatos no Drive

No Colab, execute esta célula e selecione `/tmp/brasa-episodes-v001.jsonl` no seletor de arquivos do computador. A célula confere o hash e mantém o lote fora de `datasets/curated`:

```python
from google.colab import drive, files
from pathlib import Path
import hashlib, json

drive.mount('/content/drive')
uploaded = files.upload()
name = 'brasa-episodes-v001.jsonl'
raw = uploaded[name]
expected = 'f1819016c77a907df6e17207d060cd2bf6e08c329dbda97a3ea1147bec5a6271'
assert hashlib.sha256(raw).hexdigest() == expected, 'Arquivo diferente do lote auditado'
records = [json.loads(line) for line in raw.decode('utf-8').splitlines() if line.strip()]
assert len(records) == 3
assert all(r['schema'] == 'brasa-coding-episode-candidate/v1'
           and r['review'] == {'human_reviewed': False, 'safe_to_train': False}
           for r in records)
target = Path('/content/drive/MyDrive/IA-Local/datasets/curation/v001/episode_candidates')
target.mkdir(parents=True, exist_ok=True)
destination = target / name
if destination.exists():
    assert destination.read_bytes() == raw, 'Já existe outro conteúdo nesse nome'
else:
    destination.write_bytes(raw)
print('Candidatos guardados:', destination, 'registros:', len(records))
```

Depois, carregue `colab/episode_review_registry_v001.json` e `colab/curate_verified_episodes_v001.py` na célula seguinte. Ela guarda o registro de triagem no Drive e cria um run separado com o único episódio retido para revisão humana:

```python
from google.colab import files
from pathlib import Path
from datetime import datetime, timezone
import hashlib, subprocess

uploaded = files.upload()
registry_name = 'episode_review_registry_v001.json'
script_name = 'curate_verified_episodes_v001.py'
by_sha256 = {hashlib.sha256(content).hexdigest(): content for content in uploaded.values()}
registry_bytes = by_sha256['e76f22734c89193bc4d005295d521c7ca0088734de3ee71f8f519f75a291ad5b']
script_bytes = by_sha256['ab7e678ff6c7cda8f24d115cc953cb71e2a927bdb8ef773b9f0fadc0d118ade7']
registry_path = target / registry_name
if registry_path.exists():
    assert registry_path.read_bytes() == registry_bytes, 'O registro no Drive é diferente'
else:
    registry_path.write_bytes(registry_bytes)
script_path = Path('/content') / script_name
script_path.write_bytes(script_bytes)
run_root = Path('/content/drive/MyDrive/IA-Local/datasets/curated/episodes/v001/runs')
run_dir = run_root / datetime.now(timezone.utc).strftime('run_%Y%m%dT%H%M%S_%fZ')
subprocess.run(['python3', str(script_path), '--candidates', str(destination),
                '--registry', str(registry_path), '--output-dir', str(run_dir)], check=True)
print('Episódio para revisão humana:', run_dir)
```

## Próximo critério de avanço

Reservar tarefas novas para medir o agente e coletar episódios maiores de criação e correção em projetos distintos. Cada episódio deve manter o vínculo entre pedido, alteração aplicada, estado final dos arquivos e saída de verificação; um relatório de conclusão isolado não basta. A conversão para treino deve preservar observações e chamadas de ferramenta em mensagens distintas, sem transformar uma execução real em uma resposta final inventada.
