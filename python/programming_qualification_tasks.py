"""Task library for the programming qualification benchmark.

Every task has a precise Portuguese request, a reference solution and a table
of hidden cases. ``reserved`` tasks are new designs; ``regression`` tasks reuse
the small parametric shapes of the existing implementation training data.
"""
from __future__ import annotations

from typing import Any

ERR = "ERR"
SUFFIX = " A função fica em app.py e os testes em test_app.py. Não modifique os argumentos recebidos."


def T(domain: str, name: str, signature: str, description: str, reference: str, cases: list) -> dict[str, Any]:
    return {"domain": domain, "function": name,
            "request": f"Crie uma função Python {signature} {description}{SUFFIX}",
            "reference": reference.strip("\n") + "\n",
            "cases": [{"args": args, "raises": "ValueError"} if out == ERR else {"args": args, "expect": out}
                      for args, out in cases]}


def _strings() -> list[dict[str, Any]]:
    d = "strings"
    return [
        T(d, "count_vowels", "count_vowels(text)", "que conta as vogais a, e, i, o, u (maiúsculas ou minúsculas, sem considerar acentos) em text.",
          "def count_vowels(text):\n    return sum(1 for c in text.lower() if c in 'aeiou')",
          [(["Brasil"], 2), (["AEIOU"], 5), ([""], 0), (["xyz"], 0), (["Olá mundo"], 3)]),
        T(d, "is_palindrome", "is_palindrome(text)", "que diz se text é palíndromo, ignorando maiúsculas/minúsculas e qualquer caractere que não seja letra ou dígito. Texto vazio é palíndromo.",
          "def is_palindrome(text):\n    s = [c.lower() for c in text if c.isalnum()]\n    return s == s[::-1]",
          [(["Anotaram a data da maratona"], True), (["abc"], False), ([""], True), (["A man, a plan, a canal: Panama"], True), (["ab"], False)]),
        T(d, "reverse_words", "reverse_words(text)", "que inverte a ordem das palavras de text (separadas por espaços em branco) e as junta com um único espaço.",
          "def reverse_words(text):\n    return ' '.join(reversed(text.split()))",
          [(["um dois três"], "três dois um"), (["  a   b "], "b a"), ([""], ""), (["só"], "só")]),
        T(d, "capitalize_words", "capitalize_words(text)", "que devolve text com a primeira letra de cada palavra em maiúscula e as demais em minúscula; as palavras são separadas por espaços em branco e o resultado usa um único espaço entre elas.",
          "def capitalize_words(text):\n    return ' '.join(w[:1].upper() + w[1:].lower() for w in text.split())",
          [(["ola MUNDO"], "Ola Mundo"), (["  x  yZ "], "X Yz"), ([""], ""), (["a"], "A")]),
        T(d, "snake_to_camel", "snake_to_camel(name)", "que converte snake_case em camelCase: a primeira parte fica minúscula e cada parte seguinte começa com maiúscula. Partes vazias (underscores repetidos) são ignoradas.",
          "def snake_to_camel(name):\n    parts = [p for p in name.split('_') if p]\n    if not parts:\n        return ''\n    return parts[0].lower() + ''.join(p[:1].upper() + p[1:].lower() for p in parts[1:])",
          [(["foo_bar_baz"], "fooBarBaz"), (["um"], "um"), (["a__b"], "aB"), ([""], ""), (["MAX_VALUE"], "maxValue")]),
        T(d, "camel_to_snake", "camel_to_snake(name)", "que converte camelCase em snake_case: antes de cada letra maiúscula (que não seja o primeiro caractere) insere um underscore e tudo fica em minúsculas.",
          "def camel_to_snake(name):\n    out = ''\n    for i, c in enumerate(name):\n        if c.isupper() and i > 0:\n            out += '_'\n        out += c.lower()\n    return out",
          [(["fooBarBaz"], "foo_bar_baz"), (["abc"], "abc"), (["Abc"], "abc"), ([""], ""), (["aB"], "a_b")]),
        T(d, "truncate", "truncate(text, limit)", "que devolve text se len(text) <= limit; caso contrário devolve os primeiros limit-3 caracteres seguidos de '...'. Se limit < 3 levanta ValueError.",
          "def truncate(text, limit):\n    if limit < 3:\n        raise ValueError('limit')\n    if len(text) <= limit:\n        return text\n    return text[:limit - 3] + '...'",
          [(["abcdefghij", 8], "abcde..."), (["abc", 3], "abc"), (["abcd", 3], "..."), (["x", 2], ERR), (["", 5], "")]),
        T(d, "char_frequency", "char_frequency(text)", "que devolve um dicionário com a contagem de cada caractere de text, ignorando espaços (' ').",
          "def char_frequency(text):\n    out = {}\n    for c in text:\n        if c != ' ':\n            out[c] = out.get(c, 0) + 1\n    return out",
          [(["aab b"], {"a": 2, "b": 2}), ([""], {}), (["   "], {}), (["abca"], {"a": 2, "b": 1, "c": 1})]),
        T(d, "longest_word", "longest_word(text)", "que devolve a palavra mais longa de text (palavras separadas por espaços em branco). Em empate, a primeira. Texto sem palavras devolve ''.",
          "def longest_word(text):\n    best = ''\n    for w in text.split():\n        if len(w) > len(best):\n            best = w\n    return best",
          [(["a bb ccc dd"], "ccc"), (["ab cd"], "ab"), ([""], ""), (["   "], ""), (["único"], "único")]),
        T(d, "unique_chars", "unique_chars(text)", "que remove de text os caracteres repetidos, mantendo apenas a primeira ocorrência de cada um e preservando a ordem.",
          "def unique_chars(text):\n    seen = set()\n    out = []\n    for c in text:\n        if c not in seen:\n            seen.add(c)\n            out.append(c)\n    return ''.join(out)",
          [(["banana"], "ban"), ([""], ""), (["aAa"], "aA"), (["abc"], "abc")]),
        T(d, "is_anagram", "is_anagram(a, b)", "que diz se a e b são anagramas, ignorando espaços e diferença entre maiúsculas e minúsculas.",
          "def is_anagram(a, b):\n    f = lambda s: sorted(s.replace(' ', '').lower())\n    return f(a) == f(b)",
          [(["Roma", "amor"], True), (["a b", "ba"], True), (["abc", "abd"], False), (["", ""], True), (["aa", "a"], False)]),
        T(d, "caesar", "caesar(text, shift)", "que aplica a cifra de César: desloca cada letra ASCII (a-z e A-Z) shift posições no alfabeto, com volta circular e preservando a caixa; os demais caracteres ficam iguais. shift pode ser negativo ou maior que 26.",
          "def caesar(text, shift):\n    out = []\n    for c in text:\n        if 'a' <= c <= 'z':\n            out.append(chr((ord(c) - 97 + shift) % 26 + 97))\n        elif 'A' <= c <= 'Z':\n            out.append(chr((ord(c) - 65 + shift) % 26 + 65))\n        else:\n            out.append(c)\n    return ''.join(out)",
          [(["abc", 1], "bcd"), (["xyz", 3], "abc"), (["Hello, World!", 13], "Uryyb, Jbeyq!"), (["abc", -1], "zab"), (["abc", 27], "bcd"), (["", 5], "")]),
        T(d, "run_length", "run_length(text)", "que comprime text em sequências: cada grupo de caracteres iguais consecutivos vira o caractere seguido da contagem, por exemplo 'aaabcc' vira 'a3b1c2'. Texto vazio devolve ''.",
          "def run_length(text):\n    out = []\n    i = 0\n    while i < len(text):\n        j = i\n        while j < len(text) and text[j] == text[i]:\n            j += 1\n        out.append(text[i] + str(j - i))\n        i = j\n    return ''.join(out)",
          [(["aaabcc"], "a3b1c2"), ([""], ""), (["x"], "x1"), (["aabbaa"], "a2b2a2"), (["zzzzzzzzzzzz"], "z12")]),
        T(d, "slugify", "slugify(text)", "que gera um slug: converte para minúsculas, remove todo caractere que não seja letra ASCII, dígito ou espaço, troca sequências de espaços por um único '-' e remove hífens nas pontas.",
          "import re\n\n\ndef slugify(text):\n    s = re.sub(r'[^a-z0-9 ]', '', text.lower())\n    return '-'.join(s.split())",
          [(["Olá, Mundo Novo!"], "ol-mundo-novo"), (["  Hello   World  "], "hello-world"), ([""], ""), (["A-B"], "ab"), (["100% Real"], "100-real")]),
        T(d, "count_words", "count_words(text)", "que conta as palavras de text, separadas por qualquer quantidade de espaços em branco.",
          "def count_words(text):\n    return len(text.split())",
          [(["um dois  três"], 3), ([""], 0), (["   "], 0), (["a\tb\nc"], 3)]),
        T(d, "is_balanced", "is_balanced(text)", "que diz se os delimitadores (), [] e {} de text estão balanceados e bem aninhados; os demais caracteres são ignorados.",
          "def is_balanced(text):\n    pairs = {')': '(', ']': '[', '}': '{'}\n    stack = []\n    for c in text:\n        if c in '([{':\n            stack.append(c)\n        elif c in pairs:\n            if not stack or stack.pop() != pairs[c]:\n                return False\n    return not stack",
          [(["([]{})"], True), (["(]"], False), (["(("], False), ([""], True), (["a(b)c"], True), (["())"], False)]),
        T(d, "first_unique_char", "first_unique_char(text)", "que devolve o primeiro caractere de text que aparece uma única vez, ou None se não houver.",
          "def first_unique_char(text):\n    for c in text:\n        if text.count(c) == 1:\n            return c\n    return None",
          [(["aabbcd"], "c"), (["aabb"], None), ([""], None), (["xyx"], "y")]),
        T(d, "center_text", "center_text(text, width, fill)", "que centraliza text em width caracteres usando o caractere fill: se width <= len(text) devolve text; senão coloca (width-len(text))//2 caracteres fill à esquerda e o restante à direita.",
          "def center_text(text, width, fill):\n    total = width - len(text)\n    if total <= 0:\n        return text\n    left = total // 2\n    return fill * left + text + fill * (total - left)",
          [(["ab", 6, "*"], "**ab**"), (["ab", 5, "-"], "-ab--"), (["abc", 2, "."], "abc"), (["", 3, "x"], "xxx")]),
        T(d, "mask_digits", "mask_digits(text)", "que substitui cada dígito (0-9) de text pelo caractere '#', mantendo o resto.",
          "def mask_digits(text):\n    return ''.join('#' if '0' <= c <= '9' else c for c in text)",
          [(["tel 123-45"], "tel ###-##"), ([""], ""), (["abc"], "abc"), (["2026"], "####")]),
        T(d, "only_digits", "only_digits(text)", "que devolve uma string só com os dígitos (0-9) de text, na mesma ordem.",
          "def only_digits(text):\n    return ''.join(c for c in text if '0' <= c <= '9')",
          [(["(11) 98765-4321"], "11987654321"), (["abc"], ""), ([""], ""), (["a1b2c3"], "123")]),
        T(d, "swap_case_text", "swap_case_text(text)", "que inverte a caixa de cada letra de text (maiúscula vira minúscula e vice-versa).",
          "def swap_case_text(text):\n    return ''.join(c.lower() if c.isupper() else c.upper() for c in text)",
          [(["aBc"], "AbC"), ([""], ""), (["123"], "123"), (["Olá"], "oLÁ")]),
        T(d, "wrap_lines", "wrap_lines(text, width)", "que quebra text em linhas com no máximo width caracteres, sem partir palavras (palavras separadas por espaços em branco): devolve uma lista de linhas, cada uma com as palavras unidas por um espaço. Uma palavra maior que width fica sozinha na linha. width < 1 levanta ValueError. Texto sem palavras devolve [].",
          "def wrap_lines(text, width):\n    if width < 1:\n        raise ValueError('width')\n    lines = []\n    current = ''\n    for w in text.split():\n        if not current:\n            current = w\n        elif len(current) + 1 + len(w) <= width:\n            current += ' ' + w\n        else:\n            lines.append(current)\n            current = w\n    if current:\n        lines.append(current)\n    return lines",
          [(["aa bb cc dd", 5], ["aa bb", "cc dd"]), (["abcdefgh ij", 4], ["abcdefgh", "ij"]), (["", 3], []), (["a b", 0], ERR), (["x y z", 3], ["x y", "z"])]),
        T(d, "count_substring", "count_substring(text, sub)", "que conta as ocorrências não sobrepostas de sub em text. Se sub for vazio levanta ValueError.",
          "def count_substring(text, sub):\n    if not sub:\n        raise ValueError('sub')\n    return text.count(sub)",
          [(["aaaa", "aa"], 2), (["abc", "d"], 0), (["abab", "ab"], 2), (["a", ""], ERR)]),
        T(d, "common_prefix", "common_prefix(words)", "que devolve o maior prefixo comum de uma lista de strings; lista vazia devolve ''.",
          "def common_prefix(words):\n    if not words:\n        return ''\n    prefix = words[0]\n    for w in words[1:]:\n        while not w.startswith(prefix):\n            prefix = prefix[:-1]\n    return prefix",
          [([["flor", "flora", "floresta"]], "flor"), ([["a", "b"]], ""), ([[]], ""), ([["solo"]], "solo"), ([["casa", "casaco", "cas"]], "cas")]),
    ]


def _lists() -> list[dict[str, Any]]:
    d = "lists"
    return [
        T(d, "second_largest", "second_largest(nums)", "que devolve o segundo maior valor distinto de uma lista de números, ou None se houver menos de dois valores distintos.",
          "def second_largest(nums):\n    vals = sorted(set(nums), reverse=True)\n    return vals[1] if len(vals) > 1 else None",
          [([[3, 1, 4, 4]], 3), ([[5, 5]], None), ([[]], None), ([[1, 2]], 1), ([[-1, -2, -3]], -2)]),
        T(d, "flatten", "flatten(nested)", "que achata listas aninhadas em qualquer profundidade em uma única lista, preservando a ordem. Elementos que não são listas são mantidos.",
          "def flatten(nested):\n    out = []\n    for x in nested:\n        if isinstance(x, list):\n            out.extend(flatten(x))\n        else:\n            out.append(x)\n    return out",
          [([[1, [2, [3, [4]]], 5]], [1, 2, 3, 4, 5]), ([[]], []), ([[[], [[]]]], []), ([["a", ["b"]]], ["a", "b"])]),
        T(d, "chunk", "chunk(items, size)", "que divide items em listas consecutivas de tamanho size; a última pode ser menor. size < 1 levanta ValueError.",
          "def chunk(items, size):\n    if size < 1:\n        raise ValueError('size')\n    return [items[i:i + size] for i in range(0, len(items), size)]",
          [([[1, 2, 3, 4, 5], 2], [[1, 2], [3, 4], [5]]), ([[], 3], []), ([[1, 2], 5], [[1, 2]]), ([[1], 0], ERR)]),
        T(d, "rotate_left", "rotate_left(items, k)", "que devolve uma nova lista com items rotacionada k posições para a esquerda; k pode ser maior que o tamanho e lista vazia devolve [].",
          "def rotate_left(items, k):\n    if not items:\n        return []\n    k %= len(items)\n    return items[k:] + items[:k]",
          [([[1, 2, 3, 4], 1], [2, 3, 4, 1]), ([[1, 2, 3], 4], [2, 3, 1]), ([[], 3], []), ([[1, 2, 3], 0], [1, 2, 3]), ([[1, 2, 3], 3], [1, 2, 3])]),
        T(d, "moving_sum", "moving_sum(nums, window)", "que devolve a lista das somas de cada janela de window elementos consecutivos. window < 1 levanta ValueError; se window > len(nums) devolve [].",
          "def moving_sum(nums, window):\n    if window < 1:\n        raise ValueError('window')\n    return [sum(nums[i:i + window]) for i in range(len(nums) - window + 1)]",
          [([[1, 2, 3, 4], 2], [3, 5, 7]), ([[1, 2], 3], []), ([[5], 1], [5]), ([[1], 0], ERR), ([[], 1], [])]),
        T(d, "unique_ordered", "unique_ordered(items)", "que remove elementos repetidos de items, mantendo a primeira ocorrência e a ordem original.",
          "def unique_ordered(items):\n    out = []\n    for x in items:\n        if x not in out:\n            out.append(x)\n    return out",
          [([[3, 1, 3, 2, 1]], [3, 1, 2]), ([[]], []), ([["a", "a"]], ["a"]), ([[1, 2, 3]], [1, 2, 3])]),
        T(d, "merge_sorted", "merge_sorted(a, b)", "que mescla duas listas já ordenadas de forma crescente em uma nova lista ordenada.",
          "def merge_sorted(a, b):\n    out = []\n    i = j = 0\n    while i < len(a) and j < len(b):\n        if a[i] <= b[j]:\n            out.append(a[i])\n            i += 1\n        else:\n            out.append(b[j])\n            j += 1\n    return out + a[i:] + b[j:]",
          [([[1, 3, 5], [2, 4, 6]], [1, 2, 3, 4, 5, 6]), ([[], [1]], [1]), ([[1, 1], [1]], [1, 1, 1]), ([[], []], []), ([[1, 2, 3], [10]], [1, 2, 3, 10])]),
        T(d, "running_max", "running_max(nums)", "que devolve, para cada posição, o maior valor visto até ali (inclusive).",
          "def running_max(nums):\n    out = []\n    for x in nums:\n        out.append(x if not out or x > out[-1] else out[-1])\n    return out",
          [([[1, 3, 2, 5, 4]], [1, 3, 3, 5, 5]), ([[]], []), ([[-3, -5]], [-3, -3]), ([[2]], [2])]),
        T(d, "pair_sum", "pair_sum(nums, target)", "que devolve a lista de pares [nums[i], nums[j]] com i < j cuja soma é target, ordenados por i e depois por j.",
          "def pair_sum(nums, target):\n    return [[nums[i], nums[j]] for i in range(len(nums)) for j in range(i + 1, len(nums)) if nums[i] + nums[j] == target]",
          [([[1, 2, 3, 4], 5], [[1, 4], [2, 3]]), ([[], 1], []), ([[2, 2, 2], 4], [[2, 2], [2, 2], [2, 2]]), ([[1, 2], 9], [])]),
        T(d, "intersect", "intersect(a, b)", "que devolve os elementos presentes em a e em b, sem repetição, na ordem em que aparecem em a.",
          "def intersect(a, b):\n    out = []\n    for x in a:\n        if x in b and x not in out:\n            out.append(x)\n    return out",
          [([[1, 2, 2, 3], [2, 3, 4]], [2, 3]), ([[], [1]], []), ([[1], []], []), ([[3, 1], [1, 3]], [3, 1])]),
        T(d, "partition_by", "partition_by(nums, pivot)", "que devolve [menores, iguais, maiores], três listas com os elementos de nums menores, iguais e maiores que pivot, cada uma mantendo a ordem original.",
          "def partition_by(nums, pivot):\n    return [[x for x in nums if x < pivot], [x for x in nums if x == pivot], [x for x in nums if x > pivot]]",
          [([[3, 1, 4, 1, 5], 3], [[1, 1], [3], [4, 5]]), ([[], 1], [[], [], []]), ([[2, 2], 2], [[], [2, 2], []])]),
        T(d, "mode", "mode(nums)", "que devolve o valor mais frequente de nums; em empate, o menor valor. Lista vazia levanta ValueError.",
          "def mode(nums):\n    if not nums:\n        raise ValueError('vazia')\n    counts = {}\n    for x in nums:\n        counts[x] = counts.get(x, 0) + 1\n    best = max(counts.values())\n    return min(k for k, v in counts.items() if v == best)",
          [([[1, 2, 2, 3, 3]], 2), ([[5]], 5), ([[]], ERR), ([[4, 4, 1]], 4), ([[3, 1]], 1)]),
        T(d, "median", "median(nums)", "que devolve a mediana de nums como float: o elemento central da lista ordenada, ou a média dos dois centrais se o tamanho for par. Lista vazia levanta ValueError.",
          "def median(nums):\n    if not nums:\n        raise ValueError('vazia')\n    s = sorted(nums)\n    n = len(s)\n    if n % 2:\n        return float(s[n // 2])\n    return (s[n // 2 - 1] + s[n // 2]) / 2",
          [([[3, 1, 2]], 2.0), ([[1, 2, 3, 4]], 2.5), ([[7]], 7.0), ([[]], ERR), ([[5, 1]], 3.0)]),
        T(d, "zip_fill", "zip_fill(a, b, fill)", "que devolve a lista de pares [x, y] posição a posição, indo até o tamanho da maior lista e usando fill no lugar dos elementos que faltam na menor.",
          "def zip_fill(a, b, fill):\n    n = max(len(a), len(b))\n    return [[a[i] if i < len(a) else fill, b[i] if i < len(b) else fill] for i in range(n)]",
          [([[1, 2, 3], ["a"], 0], [[1, "a"], [2, 0], [3, 0]]), ([[], [], 9], []), ([[1], [2], 0], [[1, 2]]), ([[], [1], None], [[None, 1]])]),
        T(d, "count_inversions", "count_inversions(nums)", "que conta os pares de índices i < j com nums[i] > nums[j].",
          "def count_inversions(nums):\n    return sum(1 for i in range(len(nums)) for j in range(i + 1, len(nums)) if nums[i] > nums[j])",
          [([[2, 4, 1, 3, 5]], 3), ([[1, 2, 3]], 0), ([[3, 2, 1]], 3), ([[]], 0), ([[1, 1]], 0)]),
        T(d, "remove_value", "remove_value(items, value)", "que devolve uma nova lista sem nenhuma ocorrência de value.",
          "def remove_value(items, value):\n    return [x for x in items if x != value]",
          [([[1, 2, 1, 3], 1], [2, 3]), ([[], 1], []), ([[1, 1], 1], []), ([[1, 2], 9], [1, 2])]),
        T(d, "index_of_max", "index_of_max(nums)", "que devolve o índice da primeira ocorrência do maior valor de nums. Lista vazia levanta ValueError.",
          "def index_of_max(nums):\n    if not nums:\n        raise ValueError('vazia')\n    return nums.index(max(nums))",
          [([[1, 9, 3, 9]], 1), ([[5]], 0), ([[]], ERR), ([[-5, -2, -9]], 1)]),
        T(d, "sliding_max", "sliding_max(nums, k)", "que devolve o máximo de cada janela de k elementos consecutivos de nums. k < 1 levanta ValueError; se k > len(nums) devolve [].",
          "def sliding_max(nums, k):\n    if k < 1:\n        raise ValueError('k')\n    return [max(nums[i:i + k]) for i in range(len(nums) - k + 1)]",
          [([[1, 3, 2, 5, 4], 3], [3, 5, 5]), ([[1, 2], 5], []), ([[4, 2], 1], [4, 2]), ([[1], 0], ERR)]),
        T(d, "dedupe_adjacent", "dedupe_adjacent(items)", "que remove elementos repetidos apenas quando consecutivos, mantendo o primeiro de cada sequência.",
          "def dedupe_adjacent(items):\n    out = []\n    for x in items:\n        if not out or out[-1] != x:\n            out.append(x)\n    return out",
          [([[1, 1, 2, 2, 1]], [1, 2, 1]), ([[]], []), ([["a", "a", "a"]], ["a"]), ([[1, 2, 3]], [1, 2, 3])]),
        T(d, "interleave", "interleave(a, b)", "que intercala a[0], b[0], a[1], b[1], ...; quando uma lista acaba, os elementos restantes da outra vêm no final.",
          "def interleave(a, b):\n    out = []\n    for i in range(max(len(a), len(b))):\n        if i < len(a):\n            out.append(a[i])\n        if i < len(b):\n            out.append(b[i])\n    return out",
          [([[1, 2, 3], ["a", "b"]], [1, "a", 2, "b", 3]), ([[], [1, 2]], [1, 2]), ([[], []], []), ([[1], [2]], [1, 2])]),
        T(d, "prefix_sums", "prefix_sums(nums)", "que devolve as somas de prefixos de nums como uma lista de len(nums)+1 elementos, começando em 0.",
          "def prefix_sums(nums):\n    out = [0]\n    for x in nums:\n        out.append(out[-1] + x)\n    return out",
          [([[1, 2, 3]], [0, 1, 3, 6]), ([[]], [0]), ([[-1, 1]], [0, -1, 0]), ([[5]], [0, 5])]),
        T(d, "split_on", "split_on(items, sep)", "que divide items nos elementos iguais a sep (que não aparecem no resultado) e devolve a lista de sublistas, incluindo as vazias; por exemplo [1,0,2,0,0,3] com sep 0 dá [[1],[2],[],[3]]. Lista vazia devolve [[]].",
          "def split_on(items, sep):\n    out = [[]]\n    for x in items:\n        if x == sep:\n            out.append([])\n        else:\n            out[-1].append(x)\n    return out",
          [([[1, 0, 2, 0, 0, 3], 0], [[1], [2], [], [3]]), ([[], 0], [[]]), ([[0], 0], [[], []]), ([[1, 2], 0], [[1, 2]])]),
        T(d, "longest_run", "longest_run(items)", "que devolve o comprimento da maior sequência de elementos iguais consecutivos; lista vazia devolve 0.",
          "def longest_run(items):\n    best = cur = 0\n    prev = object()\n    for x in items:\n        cur = cur + 1 if x == prev else 1\n        prev = x\n        best = max(best, cur)\n    return best",
          [([[1, 1, 2, 2, 2, 1]], 3), ([[]], 0), ([[7]], 1), ([["a", "b"]], 1)]),
        T(d, "missing_numbers", "missing_numbers(nums, n)", "que devolve, em ordem crescente, os inteiros de 1 a n que não aparecem em nums. n < 1 devolve [].",
          "def missing_numbers(nums, n):\n    present = set(nums)\n    return [i for i in range(1, n + 1) if i not in present]",
          [([[1, 3, 5], 6], [2, 4, 6]), ([[], 3], [1, 2, 3]), ([[1, 2], 2], []), ([[1], 0], []), ([[9, 2], 3], [1, 3])]),
    ]


def _numbers() -> list[dict[str, Any]]:
    d = "numbers"
    return [
        T(d, "is_prime", "is_prime(n)", "que diz se o inteiro n é primo; n < 2 não é primo.",
          "def is_prime(n):\n    if n < 2:\n        return False\n    i = 2\n    while i * i <= n:\n        if n % i == 0:\n            return False\n        i += 1\n    return True",
          [([2], True), ([1], False), ([0], False), ([-7], False), ([97], True), ([100], False), ([49], False)]),
        T(d, "gcd_list", "gcd_list(nums)", "que devolve o máximo divisor comum de uma lista de inteiros positivos. Lista vazia levanta ValueError.",
          "from math import gcd\n\n\ndef gcd_list(nums):\n    if not nums:\n        raise ValueError('vazia')\n    r = nums[0]\n    for x in nums[1:]:\n        r = gcd(r, x)\n    return r",
          [([[12, 18, 24]], 6), ([[7]], 7), ([[]], ERR), ([[8, 9]], 1), ([[100, 75]], 25)]),
        T(d, "fibonacci", "fibonacci(n)", "que devolve o n-ésimo número de Fibonacci com fibonacci(0) = 0 e fibonacci(1) = 1. n < 0 levanta ValueError.",
          "def fibonacci(n):\n    if n < 0:\n        raise ValueError('n')\n    a, b = 0, 1\n    for _ in range(n):\n        a, b = b, a + b\n    return a",
          [([0], 0), ([1], 1), ([10], 55), ([-1], ERR), ([30], 832040)]),
        T(d, "digit_sum", "digit_sum(n)", "que devolve a soma dos dígitos decimais do valor absoluto do inteiro n.",
          "def digit_sum(n):\n    return sum(int(c) for c in str(abs(n)))",
          [([123], 6), ([-45], 9), ([0], 0), ([1000], 1)]),
        T(d, "factorial", "factorial(n)", "que devolve n! para inteiro n >= 0, com factorial(0) = 1. n < 0 levanta ValueError.",
          "def factorial(n):\n    if n < 0:\n        raise ValueError('n')\n    r = 1\n    for i in range(2, n + 1):\n        r *= i\n    return r",
          [([0], 1), ([5], 120), ([-3], ERR), ([10], 3628800), ([1], 1)]),
        T(d, "collatz_steps", "collatz_steps(n)", "que conta quantos passos da sequência de Collatz (se n é par, n/2; se ímpar, 3n+1) são necessários para chegar a 1 a partir de n. n < 1 levanta ValueError; collatz_steps(1) é 0.",
          "def collatz_steps(n):\n    if n < 1:\n        raise ValueError('n')\n    steps = 0\n    while n != 1:\n        n = n // 2 if n % 2 == 0 else 3 * n + 1\n        steps += 1\n    return steps",
          [([1], 0), ([6], 8), ([27], 111), ([0], ERR), ([2], 1)]),
        T(d, "to_roman", "to_roman(n)", "que converte um inteiro de 1 a 3999 em numeral romano; fora desse intervalo levanta ValueError.",
          "def to_roman(n):\n    if not 1 <= n <= 3999:\n        raise ValueError('n')\n    table = [(1000, 'M'), (900, 'CM'), (500, 'D'), (400, 'CD'), (100, 'C'), (90, 'XC'), (50, 'L'), (40, 'XL'), (10, 'X'), (9, 'IX'), (5, 'V'), (4, 'IV'), (1, 'I')]\n    out = ''\n    for value, symbol in table:\n        while n >= value:\n            out += symbol\n            n -= value\n    return out",
          [([1994], "MCMXCIV"), ([4], "IV"), ([3999], "MMMCMXCIX"), ([0], ERR), ([4000], ERR), ([58], "LVIII")]),
        T(d, "from_roman", "from_roman(text)", "que converte um numeral romano válido em maiúsculas (símbolos I V X L C D M, com subtração) em inteiro.",
          "def from_roman(text):\n    v = {'I': 1, 'V': 5, 'X': 10, 'L': 50, 'C': 100, 'D': 500, 'M': 1000}\n    total = 0\n    for i, c in enumerate(text):\n        if i + 1 < len(text) and v[c] < v[text[i + 1]]:\n            total -= v[c]\n        else:\n            total += v[c]\n    return total",
          [(["MCMXCIV"], 1994), (["IV"], 4), (["III"], 3), (["XLII"], 42), (["MMXXVI"], 2026)]),
        T(d, "is_perfect", "is_perfect(n)", "que diz se n é um número perfeito (igual à soma dos seus divisores positivos menores que ele); n < 2 não é perfeito.",
          "def is_perfect(n):\n    if n < 2:\n        return False\n    return sum(i for i in range(1, n) if n % i == 0) == n",
          [([6], True), ([28], True), ([12], False), ([1], False), ([0], False), ([496], True)]),
        T(d, "count_divisors", "count_divisors(n)", "que conta os divisores positivos do inteiro n. n < 1 levanta ValueError.",
          "def count_divisors(n):\n    if n < 1:\n        raise ValueError('n')\n    return sum(1 for i in range(1, n + 1) if n % i == 0)",
          [([12], 6), ([1], 1), ([13], 2), ([0], ERR), ([36], 9)]),
        T(d, "to_binary", "to_binary(n)", "que devolve a representação binária do inteiro n >= 0 como string sem prefixo (to_binary(0) é '0'). n < 0 levanta ValueError.",
          "def to_binary(n):\n    if n < 0:\n        raise ValueError('n')\n    return bin(n)[2:]",
          [([5], "101"), ([0], "0"), ([255], "11111111"), ([-1], ERR), ([8], "1000")]),
        T(d, "clamp", "clamp(x, lo, hi)", "que limita x ao intervalo [lo, hi]. Se lo > hi levanta ValueError.",
          "def clamp(x, lo, hi):\n    if lo > hi:\n        raise ValueError('intervalo')\n    return max(lo, min(x, hi))",
          [([5, 0, 10], 5), ([-3, 0, 10], 0), ([42, 0, 10], 10), ([1, 5, 2], ERR), ([3, 3, 3], 3)]),
        T(d, "percentage", "percentage(part, total)", "que devolve part/total*100 arredondado a 2 casas decimais como float. total == 0 levanta ValueError.",
          "def percentage(part, total):\n    if total == 0:\n        raise ValueError('total')\n    return round(part / total * 100, 2)",
          [([1, 4], 25.0), ([1, 3], 33.33), ([0, 5], 0.0), ([5, 0], ERR), ([3, 2], 150.0)]),
        T(d, "lcm", "lcm(a, b)", "que devolve o mínimo múltiplo comum de dois inteiros positivos.",
          "from math import gcd\n\n\ndef lcm(a, b):\n    return a * b // gcd(a, b)",
          [([4, 6], 12), ([7, 3], 21), ([5, 5], 5), ([1, 9], 9)]),
        T(d, "prime_factors", "prime_factors(n)", "que devolve a lista ordenada dos fatores primos de n, com repetição (por exemplo 12 dá [2, 2, 3]). Para n < 2 devolve [].",
          "def prime_factors(n):\n    out = []\n    p = 2\n    while n >= 2 and p * p <= n:\n        while n % p == 0:\n            out.append(p)\n            n //= p\n        p += 1\n    if n >= 2:\n        out.append(n)\n    return out",
          [([12], [2, 2, 3]), ([1], []), ([97], [97]), ([0], []), ([360], [2, 2, 2, 3, 3, 5])]),
        T(d, "sum_of_squares", "sum_of_squares(n)", "que devolve 1² + 2² + ... + n². n < 0 levanta ValueError e n = 0 devolve 0.",
          "def sum_of_squares(n):\n    if n < 0:\n        raise ValueError('n')\n    return sum(i * i for i in range(1, n + 1))",
          [([3], 14), ([0], 0), ([-1], ERR), ([10], 385)]),
        T(d, "is_power_of_two", "is_power_of_two(n)", "que diz se o inteiro n é uma potência de dois (1, 2, 4, 8, ...); zero e negativos não são.",
          "def is_power_of_two(n):\n    return n > 0 and n & (n - 1) == 0",
          [([1], True), ([64], True), ([6], False), ([0], False), ([-8], False), ([1024], True)]),
        T(d, "next_prime", "next_prime(n)", "que devolve o menor número primo estritamente maior que n.",
          "def next_prime(n):\n    def prime(k):\n        if k < 2:\n            return False\n        i = 2\n        while i * i <= k:\n            if k % i == 0:\n                return False\n            i += 1\n        return True\n    n += 1\n    while not prime(n):\n        n += 1\n    return n",
          [([1], 2), ([7], 11), ([13], 17), ([-5], 2), ([89], 97)]),
        T(d, "trailing_zeros", "trailing_zeros(n)", "que conta os zeros ao final de n! (fatorial de n), sem calcular o fatorial inteiro. n < 0 levanta ValueError.",
          "def trailing_zeros(n):\n    if n < 0:\n        raise ValueError('n')\n    count = 0\n    while n:\n        n //= 5\n        count += n\n    return count",
          [([5], 1), ([25], 6), ([4], 0), ([100], 24), ([-1], ERR)]),
        T(d, "round_half_up", "round_half_up(x)", "que arredonda x para o inteiro mais próximo, resolvendo metades para cima (math.floor(x + 0.5)), e devolve int.",
          "import math\n\n\ndef round_half_up(x):\n    return math.floor(x + 0.5)",
          [([2.5], 3), ([2.4], 2), ([-2.5], -2), ([0.5], 1), ([3], 3)]),
        T(d, "reverse_digits", "reverse_digits(n)", "que inverte os dígitos do inteiro n >= 0 e devolve um int (por exemplo 1200 dá 21). n < 0 levanta ValueError.",
          "def reverse_digits(n):\n    if n < 0:\n        raise ValueError('n')\n    return int(str(n)[::-1])",
          [([123], 321), ([1200], 21), ([0], 0), ([-5], ERR), ([7], 7)]),
        T(d, "parse_base", "parse_base(text, base)", "que converte text, escrito na base indicada (entre 2 e 16, dígitos 0-9 e a-f em qualquer caixa), no inteiro decimal. Dígito inválido para a base ou base fora de 2..16 levanta ValueError.",
          "def parse_base(text, base):\n    if not 2 <= base <= 16 or not text:\n        raise ValueError('base')\n    total = 0\n    for c in text.lower():\n        d = '0123456789abcdef'.find(c)\n        if d < 0 or d >= base:\n            raise ValueError('digito')\n        total = total * base + d\n    return total",
          [(["ff", 16], 255), (["101", 2], 5), (["12", 2], ERR), (["7", 8], 7), (["Z", 16], ERR), (["10", 17], ERR)]),
        T(d, "triangle_type", "triangle_type(a, b, c)", "que classifica um triângulo pelos lados: 'invalido' se algum lado for <= 0 ou a soma dos dois menores lados for menor ou igual ao maior; senão 'equilatero' (três iguais), 'isosceles' (exatamente dois iguais) ou 'escaleno'.",
          "def triangle_type(a, b, c):\n    s = sorted([a, b, c])\n    if s[0] <= 0 or s[0] + s[1] <= s[2]:\n        return 'invalido'\n    if a == b == c:\n        return 'equilatero'\n    if a == b or b == c or a == c:\n        return 'isosceles'\n    return 'escaleno'",
          [([3, 3, 3], "equilatero"), ([3, 3, 5], "isosceles"), ([3, 4, 5], "escaleno"), ([1, 2, 3], "invalido"), ([0, 1, 1], "invalido")]),
        T(d, "sum_between", "sum_between(a, b)", "que soma todos os inteiros entre a e b, inclusive; funciona também quando a > b.",
          "def sum_between(a, b):\n    lo, hi = min(a, b), max(a, b)\n    return sum(range(lo, hi + 1))",
          [([1, 4], 10), ([4, 1], 10), ([5, 5], 5), ([-2, 2], 0)]),
    ]


def _records() -> list[dict[str, Any]]:
    d = "records"
    R = [{"id": 1, "cat": "a", "v": 10}, {"id": 2, "cat": "b", "v": 5}, {"id": 3, "cat": "a", "v": 7}]
    return [
        T(d, "group_by_key", "group_by_key(rows, key)", "que agrupa uma lista de dicionários pelo valor de row[key], devolvendo um dicionário valor -> lista de linhas na ordem original.",
          "def group_by_key(rows, key):\n    out = {}\n    for r in rows:\n        out.setdefault(r[key], []).append(r)\n    return out",
          [([R, "cat"], {"a": [R[0], R[2]], "b": [R[1]]}), ([[], "cat"], {}), ([[{"k": "z"}, {"k": "z"}], "k"], {"z": [{"k": "z"}, {"k": "z"}]})]),
        T(d, "total_by_category", "total_by_category(items)", "que recebe dicionários com as chaves 'category' e 'amount' e devolve o total de amount por category.",
          "def total_by_category(items):\n    out = {}\n    for it in items:\n        out[it['category']] = out.get(it['category'], 0) + it['amount']\n    return out",
          [([[{"category": "x", "amount": 3}, {"category": "y", "amount": 1}, {"category": "x", "amount": 4}]], {"x": 7, "y": 1}), ([[]], {}), ([[{"category": "q", "amount": -2}, {"category": "q", "amount": 2}]], {"q": 0})]),
        T(d, "merge_dicts", "merge_dicts(a, b)", "que devolve um novo dicionário com as chaves de a e b; em chaves repetidas vale o valor de b.",
          "def merge_dicts(a, b):\n    out = dict(a)\n    out.update(b)\n    return out",
          [([{"x": 1, "y": 2}, {"y": 3, "z": 4}], {"x": 1, "y": 3, "z": 4}), ([{}, {}], {}), ([{"a": 1}, {}], {"a": 1})]),
        T(d, "invert_dict", "invert_dict(mapping)", "que inverte um dicionário (valor vira chave e chave vira valor). Se dois itens tiverem o mesmo valor levanta ValueError.",
          "def invert_dict(mapping):\n    out = {}\n    for k, v in mapping.items():\n        if v in out:\n            raise ValueError('valor repetido')\n        out[v] = k\n    return out",
          [([{"a": "x", "b": "y"}], {"x": "a", "y": "b"}), ([{}], {}), ([{"a": "x", "b": "x"}], ERR)]),
        T(d, "pick_keys", "pick_keys(mapping, keys)", "que devolve um novo dicionário só com as chaves de keys que existem em mapping.",
          "def pick_keys(mapping, keys):\n    return {k: mapping[k] for k in keys if k in mapping}",
          [([{"a": 1, "b": 2, "c": 3}, ["a", "c", "z"]], {"a": 1, "c": 3}), ([{}, ["a"]], {}), ([{"a": 1}, []], {})]),
        T(d, "omit_keys", "omit_keys(mapping, keys)", "que devolve um novo dicionário sem as chaves listadas em keys.",
          "def omit_keys(mapping, keys):\n    return {k: v for k, v in mapping.items() if k not in keys}",
          [([{"a": 1, "b": 2, "c": 3}, ["b"]], {"a": 1, "c": 3}), ([{"a": 1}, ["z"]], {"a": 1}), ([{}, ["a"]], {})]),
        T(d, "count_by", "count_by(rows, key)", "que conta quantas linhas (dicionários) têm cada valor de row[key], devolvendo um dicionário valor -> contagem.",
          "def count_by(rows, key):\n    out = {}\n    for r in rows:\n        out[r[key]] = out.get(r[key], 0) + 1\n    return out",
          [([R, "cat"], {"a": 2, "b": 1}), ([[], "cat"], {}), ([[{"k": "x"}], "k"], {"x": 1})]),
        T(d, "sort_records", "sort_records(rows, key, descending)", "que devolve uma nova lista de dicionários ordenada por row[key]; se descending for True a ordem é decrescente. A ordenação é estável (empates mantêm a ordem original, inclusive no modo decrescente).",
          "def sort_records(rows, key, descending):\n    return sorted(rows, key=lambda r: r[key], reverse=descending)",
          [([R, "v", False], [R[1], R[2], R[0]]), ([R, "v", True], [R[0], R[2], R[1]]), ([[], "v", False], []), ([[{"k": 1, "n": "a"}, {"k": 1, "n": "b"}], "k", True], [{"k": 1, "n": "a"}, {"k": 1, "n": "b"}])]),
        T(d, "find_by_id", "find_by_id(rows, target)", "que devolve o primeiro dicionário de rows cujo campo 'id' é igual a target, ou None se não existir.",
          "def find_by_id(rows, target):\n    for r in rows:\n        if r.get('id') == target:\n            return r\n    return None",
          [([R, 2], R[1]), ([R, 9], None), ([[], 1], None), ([[{"x": 1}, {"id": 5}], 5], {"id": 5})]),
        T(d, "set_nested", "set_nested(data, path, value)", "que devolve uma cópia profunda de data (dicionário) onde o valor em path (lista de chaves) é value, criando dicionários intermediários quando faltarem. O original não é alterado. path vazio levanta ValueError.",
          "import copy\n\n\ndef set_nested(data, path, value):\n    if not path:\n        raise ValueError('path')\n    out = copy.deepcopy(data)\n    cur = out\n    for k in path[:-1]:\n        if not isinstance(cur.get(k), dict):\n            cur[k] = {}\n        cur = cur[k]\n    cur[path[-1]] = value\n    return out",
          [([{}, ["a", "b"], 1], {"a": {"b": 1}}), ([{"a": {"x": 1}}, ["a", "y"], 2], {"a": {"x": 1, "y": 2}}), ([{"a": 1}, ["a"], 5], {"a": 5}), ([{}, [], 1], ERR)]),
        T(d, "get_nested", "get_nested(data, path, default)", "que devolve o valor em data seguindo a lista de chaves path; se alguma chave faltar (ou o caminho passar por algo que não é dicionário) devolve default.",
          "def get_nested(data, path, default):\n    cur = data\n    for k in path:\n        if not isinstance(cur, dict) or k not in cur:\n            return default\n        cur = cur[k]\n    return cur",
          [([{"a": {"b": 2}}, ["a", "b"], 0], 2), ([{"a": 1}, ["a", "b"], "x"], "x"), ([{}, ["z"], None], None), ([{"a": 1}, [], 9], {"a": 1})]),
        T(d, "filter_by", "filter_by(rows, key, value)", "que devolve as linhas (dicionários) em que row.get(key) é igual a value, na ordem original.",
          "def filter_by(rows, key, value):\n    return [r for r in rows if r.get(key) == value]",
          [([R, "cat", "a"], [R[0], R[2]]), ([R, "cat", "z"], []), ([[{"x": 1}], "cat", None], [{"x": 1}])]),
        T(d, "average_by", "average_by(rows, group_key, value_key)", "que devolve um dicionário com a média (float) de row[value_key] para cada valor de row[group_key].",
          "def average_by(rows, group_key, value_key):\n    sums = {}\n    counts = {}\n    for r in rows:\n        g = r[group_key]\n        sums[g] = sums.get(g, 0) + r[value_key]\n        counts[g] = counts.get(g, 0) + 1\n    return {g: sums[g] / counts[g] for g in sums}",
          [([R, "cat", "v"], {"a": 8.5, "b": 5.0}), ([[], "cat", "v"], {}), ([[{"g": "x", "n": 1}, {"g": "x", "n": 2}], "g", "n"], {"x": 1.5})]),
        T(d, "top_n", "top_n(rows, key, n)", "que devolve as n linhas com maior row[key], em ordem decrescente; empates mantêm a ordem original. Se n for maior que o número de linhas devolve todas; n <= 0 devolve [].",
          "def top_n(rows, key, n):\n    if n <= 0:\n        return []\n    return sorted(rows, key=lambda r: r[key], reverse=True)[:n]",
          [([R, "v", 2], [R[0], R[2]]), ([R, "v", 9], [R[0], R[2], R[1]]), ([R, "v", 0], []), ([[], "v", 3], [])]),
        T(d, "rename_keys", "rename_keys(mapping, renames)", "que devolve um novo dicionário em que as chaves presentes em renames (antigo -> novo) são trocadas; as demais ficam iguais.",
          "def rename_keys(mapping, renames):\n    return {renames.get(k, k): v for k, v in mapping.items()}",
          [([{"a": 1, "b": 2}, {"a": "x"}], {"x": 1, "b": 2}), ([{}, {"a": "x"}], {}), ([{"a": 1}, {}], {"a": 1})]),
        T(d, "deep_merge", "deep_merge(a, b)", "que mescla recursivamente dois dicionários: quando a mesma chave tem dicionários nos dois lados eles são mesclados; nos demais casos vale o valor de b. Devolve um novo dicionário.",
          "def deep_merge(a, b):\n    out = dict(a)\n    for k, v in b.items():\n        if isinstance(v, dict) and isinstance(out.get(k), dict):\n            out[k] = deep_merge(out[k], v)\n        else:\n            out[k] = v\n    return out",
          [([{"a": {"x": 1}, "b": 1}, {"a": {"y": 2}, "b": 2}], {"a": {"x": 1, "y": 2}, "b": 2}), ([{}, {"a": 1}], {"a": 1}), ([{"a": {"x": 1}}, {"a": 5}], {"a": 5}), ([{"a": 5}, {"a": {"x": 1}}], {"a": {"x": 1}})]),
        T(d, "flatten_dict", "flatten_dict(data)", "que achata um dicionário aninhado em um dicionário de um nível, unindo as chaves com '.', por exemplo {'a': {'b': 1}} dá {'a.b': 1}. Dicionários vazios aninhados são descartados.",
          "def flatten_dict(data):\n    out = {}\n    for k, v in data.items():\n        if isinstance(v, dict):\n            for kk, vv in flatten_dict(v).items():\n                out[k + '.' + kk] = vv\n        else:\n            out[k] = v\n    return out",
          [([{"a": {"b": 1, "c": {"d": 2}}, "e": 3}], {"a.b": 1, "a.c.d": 2, "e": 3}), ([{}], {}), ([{"a": {}}], {})]),
        T(d, "dict_diff", "dict_diff(old, new)", "que compara dois dicionários e devolve {'added': [...], 'removed': [...], 'changed': [...]} com as chaves (cada lista em ordem alfabética) só presentes em new, só presentes em old e presentes nos dois com valores diferentes.",
          "def dict_diff(old, new):\n    return {'added': sorted(k for k in new if k not in old), 'removed': sorted(k for k in old if k not in new),\n            'changed': sorted(k for k in old if k in new and old[k] != new[k])}",
          [([{"a": 1, "b": 2}, {"b": 3, "c": 4}], {"added": ["c"], "removed": ["a"], "changed": ["b"]}), ([{}, {}], {"added": [], "removed": [], "changed": []}), ([{"a": 1}, {"a": 1}], {"added": [], "removed": [], "changed": []})]),
        T(d, "fill_defaults", "fill_defaults(rows, defaults)", "que devolve uma nova lista de dicionários em que cada linha recebe os pares de defaults para as chaves que ela não tem; valores existentes (inclusive None) são mantidos.",
          "def fill_defaults(rows, defaults):\n    return [{**defaults, **r} for r in rows]",
          [([[{"a": 1}, {"b": 2}], {"a": 0, "b": 0}], [{"a": 1, "b": 0}, {"a": 0, "b": 2}]), ([[], {"a": 1}], []), ([[{"a": None}], {"a": 5}], [{"a": None}])]),
        T(d, "dedupe_by", "dedupe_by(rows, key)", "que remove linhas com valor repetido em row[key], mantendo a primeira de cada valor e a ordem original.",
          "def dedupe_by(rows, key):\n    seen = set()\n    out = []\n    for r in rows:\n        if r[key] not in seen:\n            seen.add(r[key])\n            out.append(r)\n    return out",
          [([R, "cat"], [R[0], R[1]]), ([[], "cat"], []), ([R, "id"], R)]),
        T(d, "pluck", "pluck(rows, key)", "que devolve a lista dos valores de row[key], ignorando as linhas que não têm a chave.",
          "def pluck(rows, key):\n    return [r[key] for r in rows if key in r]",
          [([R, "v"], [10, 5, 7]), ([[{"a": 1}, {"b": 2}], "a"], [1]), ([[], "a"], [])]),
        T(d, "sum_field", "sum_field(rows, key)", "que soma os valores de row[key], ignorando as linhas que não têm a chave. Sem valores devolve 0.",
          "def sum_field(rows, key):\n    return sum(r[key] for r in rows if key in r)",
          [([R, "v"], 22), ([[{"a": 1}, {"b": 2}], "a"], 1), ([[], "a"], 0)]),
        T(d, "index_by", "index_by(rows, key)", "que devolve um dicionário row[key] -> linha; se um valor se repetir, vale a última linha.",
          "def index_by(rows, key):\n    return {r[key]: r for r in rows}",
          [([R, "cat"], {"a": R[2], "b": R[1]}), ([[], "id"], {}), ([[{"id": "p"}], "id"], {"p": {"id": "p"}})]),
        T(d, "missing_fields", "missing_fields(record, required)", "que devolve, em ordem alfabética, os nomes de required que não existem em record ou têm valor None.",
          "def missing_fields(record, required):\n    return sorted(k for k in required if record.get(k) is None)",
          [([{"a": 1, "b": None}, ["a", "b", "c"]], ["b", "c"]), ([{}, []], []), ([{"x": 0}, ["x"]], []), ([{}, ["z", "y"]], ["y", "z"])]),
    ]


def reserved_tasks() -> list[dict[str, Any]]:
    tasks = _strings() + _lists() + _numbers() + _records()
    for task in tasks:
        task["suite"] = "reserved"
        task["id"] = f"{task['domain']}-{task['function']}"
    return tasks


def regression_tasks() -> list[dict[str, Any]]:
    """Familiar parametric shapes: simple one-liners like the training examples."""
    d = "strings"
    out: list[dict[str, Any]] = []
    for letter in "arst":
        out.append(T(d, f"count_{letter}", f"count_{letter}(text)", f"que conta quantas vezes a letra '{letter}' aparece em text, sem diferenciar maiúsculas de minúsculas.",
                     f"def count_{letter}(text):\n    return text.lower().count('{letter}')",
                     [(["Arara"], "Arara".lower().count(letter)), ([""], 0), (["xyz"], "xyz".count(letter)), (["RASTREAR"], "rastrear".count(letter))]))
    for word in ["Olá", "Oi", "Bom dia", "Boa noite"]:
        slug = word.lower().replace("á", "a").replace(" ", "_")
        out.append(T(d, f"saudar_{slug}", f"saudar_{slug}(nome)", f"que devolve '{word}, ' seguido de nome e de '!'.",
                     f"def saudar_{slug}(nome):\n    return '{word}, ' + nome + '!'",
                     [(["Ana"], f"{word}, Ana!"), ([""], f"{word}, !"), (["João Silva"], f"{word}, João Silva!")]))
    for n in [2, 3, 4, 5]:
        out.append(T(d, f"repetir_{n}", f"repetir_{n}(text)", f"que repete text {n} vezes separado por '-'.",
                     f"def repetir_{n}(text):\n    return '-'.join([text] * {n})",
                     [(["ab"], "-".join(["ab"] * n)), ([""], "-" * (n - 1)), (["x"], "-".join(["x"] * n))]))
    for prefix in ["A", "Br", "pre", "un"]:
        out.append(T(d, f"comeca_com_{prefix.lower()}", f"comeca_com_{prefix.lower()}(text)", f"que diz se text começa exatamente com '{prefix}' (diferenciando maiúsculas).",
                     f"def comeca_com_{prefix.lower()}(text):\n    return text.startswith('{prefix}')",
                     [([prefix + "xyz"], True), (["xyz"], False), ([""], False), ([prefix.swapcase() + "q"], prefix.swapcase() == prefix)]))
    for n in [1, 2, 3, 5]:
        out.append(T(d, f"primeiros_{n}", f"primeiros_{n}(text)", f"que devolve os primeiros {n} caracteres de text (ou text inteiro se for mais curto).",
                     f"def primeiros_{n}(text):\n    return text[:{n}]",
                     [(["abcdefgh"], "abcdefgh"[:n]), (["a"], "a"), ([""], "")]))
    for name, (l, r) in {"colchetes": "[]", "parenteses": "()", "chaves": "{}", "angulos": "<>"}.items():
        out.append(T(d, f"envolver_{name}", f"envolver_{name}(text)", f"que devolve text envolvido por '{l}' no início e '{r}' no fim.",
                     f"def envolver_{name}(text):\n    return '{l}' + text + '{r}'",
                     [(["x"], f"{l}x{r}"), ([""], f"{l}{r}"), (["a b"], f"{l}a b{r}")]))
    d = "lists"
    for k in [2, 3, 5, 7]:
        out.append(T(d, f"soma_multiplos_{k}", f"soma_multiplos_{k}(nums)", f"que soma os elementos de nums que são múltiplos de {k} (zero conta como múltiplo).",
                     f"def soma_multiplos_{k}(nums):\n    return sum(x for x in nums if x % {k} == 0)",
                     [([list(range(0, 31))], sum(x for x in range(31) if x % k == 0)), ([[]], 0), ([[1, -k, k * 2]], -k + k * 2)]))
    for n in [1, 2, 3, 4]:
        out.append(T(d, f"pegar_{n}", f"pegar_{n}(items)", f"que devolve uma nova lista com os primeiros {n} elementos de items.",
                     f"def pegar_{n}(items):\n    return items[:{n}]",
                     [([[1, 2, 3, 4, 5, 6]], [1, 2, 3, 4, 5, 6][:n]), ([[9]], [9]), ([[]], [])]))
    for t in [0, 5, 10, 100]:
        out.append(T(d, f"contar_maiores_{t}", f"contar_maiores_{t}(nums)", f"que conta os elementos de nums estritamente maiores que {t}.",
                     f"def contar_maiores_{t}(nums):\n    return sum(1 for x in nums if x > {t})",
                     [([[t - 1, t, t + 1, t + 50]], 2), ([[]], 0), ([[t]], 0)]))
    for f in [2, 3, 10, -1]:
        name = f"escalar_{'menos_' if f < 0 else ''}{abs(f)}"
        out.append(T(d, name, f"{name}(nums)", f"que devolve uma nova lista com cada elemento de nums multiplicado por {f}.",
                     f"def {name}(nums):\n    return [x * ({f}) for x in nums]",
                     [([[1, 2, 3]], [f, 2 * f, 3 * f]), ([[]], []), ([[0]], [0])]))
    for t in [0, 3, 10, 50]:
        out.append(T(d, f"manter_acima_{t}", f"manter_acima_{t}(nums)", f"que devolve os elementos de nums estritamente maiores que {t}, na ordem original.",
                     f"def manter_acima_{t}(nums):\n    return [x for x in nums if x > {t}]",
                     [([[t + 2, t - 1, t, t + 7]], [t + 2, t + 7]), ([[]], []), ([[t]], [])]))
    for n in [1, 2, 3, 4]:
        out.append(T(d, f"ultimos_{n}", f"ultimos_{n}(items)", f"que devolve uma nova lista com os últimos {n} elementos de items (ou todos, se houver menos).",
                     f"def ultimos_{n}(items):\n    return items[-{n}:]",
                     [([[1, 2, 3, 4, 5, 6]], [1, 2, 3, 4, 5, 6][-n:]), ([[9]], [9]), ([[]], [])]))
    d = "numbers"
    for n in [3, 7, 10, 25]:
        out.append(T(d, f"somar_{n}", f"somar_{n}(x)", f"que devolve x + {n}.",
                     f"def somar_{n}(x):\n    return x + {n}", [([0], n), ([5], 5 + n), ([-n], 0)]))
    for k in [2, 3, 4, 9]:
        out.append(T(d, f"multiplo_de_{k}", f"multiplo_de_{k}(n)", f"que diz se o inteiro n é múltiplo de {k}.",
                     f"def multiplo_de_{k}(n):\n    return n % {k} == 0", [([k * 5], True), ([k * 5 + 1], False), ([0], True), ([-k], True)]))
    for c in [1, 5, 10, 100]:
        out.append(T(d, f"quadrado_mais_{c}", f"quadrado_mais_{c}(x)", f"que devolve x ao quadrado mais {c}.",
                     f"def quadrado_mais_{c}(x):\n    return x * x + {c}", [([0], c), ([3], 9 + c), ([-2], 4 + c)]))
    for p in [10, 20, 25, 50]:
        out.append(T(d, f"desconto_{p}", f"desconto_{p}(preco)", f"que devolve o preço com {p}% de desconto, como float.",
                     f"def desconto_{p}(preco):\n    return preco * (100 - {p}) / 100",
                     [([200], 200 * (100 - p) / 100), ([0], 0.0), ([80], 80 * (100 - p) / 100)]))
    for m in [10, 50, 100, 255]:
        out.append(T(d, f"limitar_a_{m}", f"limitar_a_{m}(x)", f"que devolve x, mas nunca mais que {m}.",
                     f"def limitar_a_{m}(x):\n    return min(x, {m})", [([m + 1], m), ([m], m), ([-5], -5)]))
    for a, b in [(1, 5), (0, 10), (-5, 5), (10, 20)]:
        n = f"entre_{'m' if a < 0 else ''}{abs(a)}_{b}"
        out.append(T(d, n, f"{n}(x)", f"que diz se x está entre {a} e {b}, inclusive.",
                     f"def {n}(x):\n    return {a} <= x <= {b}", [([a], True), ([b], True), ([b + 1], False), ([a - 1], False)]))
    d = "records"
    for field in ["name", "age", "city", "email"]:
        out.append(T(d, f"campo_{field}", f"campo_{field}(registro)", f"que devolve registro[{field!r}] ou None se a chave não existir.",
                     f"def campo_{field}(registro):\n    return registro.get({field!r})",
                     [([{field: "v"}], "v"), ([{}], None), ([{"outro": 1}], None)]))
    for field in ["price", "qty", "score", "total"]:
        out.append(T(d, f"somar_{field}", f"somar_{field}(rows)", f"que soma o campo '{field}' de cada dicionário em rows (todas as linhas têm o campo).",
                     f"def somar_{field}(rows):\n    return sum(r[{field!r}] for r in rows)",
                     [([[{field: 1}, {field: 2}, {field: 3}]], 6), ([[]], 0), ([[{field: 10}]], 10)]))
    for age in [18, 30, 40, 65]:
        out.append(T(d, f"nomes_acima_{age}", f"nomes_acima_{age}(pessoas)", f"que devolve a lista de r['name'] das pessoas (dicionários com 'name' e 'age') com idade estritamente maior que {age}, na ordem original.",
                     f"def nomes_acima_{age}(pessoas):\n    return [p['name'] for p in pessoas if p['age'] > {age}]",
                     [([[{"name": "a", "age": age + 1}, {"name": "b", "age": age}, {"name": "c", "age": age + 20}]], ["a", "c"]), ([[]], []), ([[{"name": "z", "age": age}]], [])]))
    for key in ["id", "slug", "code", "uid"]:
        out.append(T(d, f"tem_{key}", f"tem_{key}(rows)", f"que conta quantos dicionários de rows têm a chave '{key}'.",
                     f"def tem_{key}(rows):\n    return sum(1 for r in rows if {key!r} in r)",
                     [([[{key: 1}, {"x": 1}, {key: None}]], 2), ([[]], 0), ([[{"x": 1}]], 0)]))
    for field, default in [("name", "anon"), ("city", "n/a"), ("lang", "pt"), ("role", "user")]:
        out.append(T(d, f"com_padrao_{field}", f"com_padrao_{field}(registro)", f"que devolve um novo dicionário igual a registro, mas com '{field}' igual a {default!r} se a chave não existir.",
                     f"def com_padrao_{field}(registro):\n    return {{{field!r}: {default!r}, **registro}}",
                     [([{}], {field: default}), ([{field: "x"}], {field: "x"}), ([{"k": 1}], {field: default, "k": 1})]))
    for key in ["a", "b", "c", "d"]:
        out.append(T(d, f"sem_{key}", f"sem_{key}(registro)", f"que devolve um novo dicionário igual a registro sem a chave '{key}'.",
                     f"def sem_{key}(registro):\n    return {{k: v for k, v in registro.items() if k != {key!r}}}",
                     [([{"a": 1, "b": 2, "c": 3, "d": 4}], {k: v for k, v in {"a": 1, "b": 2, "c": 3, "d": 4}.items() if k != key}), ([{}], {}), ([{"z": 0}], {"z": 0})]))
    for task in out:
        task["suite"] = "regression"
        task["id"] = f"{task['domain']}-{task['function']}"
    return out
