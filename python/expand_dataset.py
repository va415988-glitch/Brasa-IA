"""Monta um corpus curado inicial sem depender de modelos externos."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BASE = ROOT / "data" / "tool_traces.jsonl"
OUTPUT = ROOT / "data" / "combined.jsonl"

CURATED = [
    ("Como você deve responder quando não souber algo?", "Devo dizer claramente que não tenho certeza, explicar o que sei e pedir ou buscar a informação necessária. Não devo inventar fatos, fontes ou resultados."),
    ("O que é uma variável em Python?", "Uma variável é um nome associado a um valor. Por exemplo, `idade = 30` associa o nome `idade` ao número 30. Em Python, o tipo é definido pelo valor atribuído."),
    ("O que é uma tupla em Python?", "Uma tupla é uma sequência ordenada e imutável de valores. Por exemplo, `ponto = (10, 20)`. Depois de criada, seus itens não podem ser alterados; isso a diferencia de uma lista."),
    ("O que é uma tupla em Python e o que é uma variável?", "Uma tupla é uma sequência ordenada e imutável de valores, como `ponto = (10, 20)`. Uma variável é um nome associado a um valor; nesse exemplo, `ponto` é a variável que referencia a tupla."),
    ("Como funciona um loop for?", "Um loop `for` percorre os itens de uma sequência. Em Python: `for item in itens:` executa o bloco uma vez para cada item de `itens`."),
    ("Por que separar ferramentas do modelo?", "Porque o modelo decide o que precisa ser feito, enquanto o runtime valida permissões, argumentos, tempo limite e efeitos colaterais. Isso facilita trocar o modelo sem reescrever o sistema."),
    ("O que significa timeout?", "Timeout é o encerramento controlado de uma operação que demorou além do prazo permitido. Aqui, cada requisição tem limite absoluto de 30 segundos."),
    ("Explique o erro NameError.", "NameError ocorre quando o código usa um nome que não foi definido no escopo atual. Verifique a grafia, a ordem de execução e os imports."),
    ("Explique o erro TypeError.", "TypeError ocorre quando uma operação recebe um tipo incompatível. Confira os tipos dos valores e converta-os explicitamente quando fizer sentido."),
    ("Como faço uma requisição HTTP em Rust?", "Você pode usar uma biblioteca HTTP como `reqwest`, configurar timeout e tratar erros de rede, status HTTP e conteúdo inválido separadamente."),
    ("O que é ownership em Rust?", "Ownership é o sistema de posse de valores de Rust. Cada valor tem um dono, e o compilador controla movimentos e empréstimos para garantir segurança de memória sem coletor de lixo."),
    ("Quando uma IA deve pesquisar na internet?", "Quando a resposta depende de informação atual, de uma página específica ou de um fato que não posso confirmar com segurança. Para conhecimento estável, posso responder diretamente."),
    ("Quando uma IA não deve pesquisar?", "Quando a pergunta é puramente conceitual e não depende de informação atual. Pesquisar sem necessidade aumenta latência e pode introduzir fontes irrelevantes."),
    ("Como citar uma fonte?", "Use o identificador retornado pela ferramenta, como `[web-1]`, junto do título e da URL. Nunca invente um identificador ou uma página."),
    ("Como lidar com uma ferramenta que falhou?", "Informe o erro de forma clara, tente novamente apenas se houver uma estratégia diferente e respeite o orçamento de tempo restante da requisição."),
    ("O que é JSON?", "JSON é um formato textual para representar objetos, listas, strings, números, booleanos e valores nulos. Ele é útil para contratos entre componentes porque é simples de validar."),
    ("Como melhorar um programa lento?", "Meça antes de otimizar. Identifique o gargalo, reduza trabalho repetido, use cache quando apropriado e compare o tempo antes e depois da mudança."),
    ("Escreva uma função Python que some dois números.", "```python\ndef somar(a, b):\n    return a + b\n```"),
    ("Como testar uma função?", "Crie casos normais, limites e entradas inválidas. O teste deve verificar o resultado esperado e também o comportamento diante de erros."),
    ("O que é um tokenizer?", "É o componente que transforma texto em unidades menores chamadas tokens. O modelo trabalha com esses tokens em vez de receber caracteres diretamente."),
    ("Por que usar byte-level BPE?", "Ele aprende sequências frequentes, mas consegue representar qualquer texto UTF-8 por bytes. Isso evita falhas com acentos, código e caracteres desconhecidos."),
    ("O que é treinamento causal?", "É o treinamento em que o modelo aprende a prever o próximo token usando apenas os tokens anteriores. Essa é a tarefa básica de um modelo gerador de texto."),
    ("O que fazer se a resposta passar de 30 segundos?", "Interromper a operação, registrar o timeout e retornar uma mensagem curta informando que o limite de desempenho foi excedido."),
    ("Uma fonte retornou um texto estranho. Posso usá-la?", "Não automaticamente. Devo sinalizar o conteúdo suspeito, comparar com outras fontes e evitar afirmar algo que não esteja sustentado."),
]


def main():
    records = [json.loads(line) for line in BASE.read_text(encoding="utf-8").splitlines() if line.strip()]
    for question, answer in CURATED:
        records.append({"messages": [{"role": "user", "content": question}, {"role": "assistant", "content": answer}], "tools_used": [], "expected_sources": []})
    OUTPUT.write_text("\n".join(json.dumps(record, ensure_ascii=False) for record in records) + "\n", encoding="utf-8")
    print(f"traces gravados: {len(records)}")
    print(f"arquivo: {OUTPUT}")


if __name__ == "__main__":
    main()
