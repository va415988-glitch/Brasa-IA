# Teste prático do ciclo persistente

Reinicie o aplicativo com `./start.sh` para carregar o runtime recompilado e o
worker novo. Selecione um projeto vazio e envie o texto abaixo no chat normal,
sem comando `/`. Cada arquivo deve pedir aprovação; o teste roda após ambos.

````text
Crie estes arquivos e verifique o projeto:

### app.py
```python
def add(a, b):
    return a + b
```

### tests/test_app.py
```python
import unittest
from app import add

class AppTest(unittest.TestCase):
    def test_add(self):
        self.assertEqual(add(2, 3), 5)
```
````

Resultado esperado: duas criações aprovadas individualmente, saída de unittest
com `Ran 1 test` e `OK`, seguida de conclusão. O mesmo cenário é executado pelo
teste de integração em `tests/test_agent_runs.py` usando o binário Rust real.

Para conferir persistência, recarregue a página enquanto aguarda uma aprovação
e reabra a conversa em Recentes. Ela deve recuperar a mesma chamada pendente.
O botão Cancelar tarefa impede a próxima etapa; uma ferramenta já em execução
termina e tem seu resultado registrado antes da parada.

Não reenvie a criação no mesmo projeto para testar retomada: arquivos existentes
são protegidos pelo runtime. Reabra a conversa original.

Esse exercício comprova execução de conteúdo explícito em múltiplos arquivos,
persistência e verificação; não avalia geração autônoma de uma aplicação a partir
de requisitos vagos.
