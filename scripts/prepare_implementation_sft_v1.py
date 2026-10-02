#!/usr/bin/env python3
"""Prepare reviewed, disjoint file-proposal examples for an isolated SFT probe."""

from __future__ import annotations

import hashlib
import json
import argparse
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

from proactive_implementation import compact_implementation_prompt, implementation_prompt, parse_implementation_plan
from tokenizer import ByteBPETokenizer


CASES = (
    ("train", "clamp", "Crie uma função Python clamp(value, minimum, maximum) que limita um número ao intervalo informado. Inclua testes unittest.",
     "def clamp(value, minimum, maximum):\n    if minimum > maximum:\n        raise ValueError('intervalo inválido')\n    return max(minimum, min(value, maximum))\n",
     "import unittest\nfrom app import clamp\n\nclass ClampTests(unittest.TestCase):\n    def test_limites(self):\n        self.assertEqual(clamp(8, 0, 5), 5)\n        self.assertEqual(clamp(-2, 0, 5), 0)\n        self.assertEqual(clamp(3, 0, 5), 3)\n\n    def test_intervalo_invalido(self):\n        with self.assertRaises(ValueError):\n            clamp(1, 5, 0)\n"),
    ("train", "word_count", "Crie uma função Python count_words(text) que conta palavras separadas por espaços. Inclua testes unittest.",
     "def count_words(text):\n    return len(text.split())\n",
     "import unittest\nfrom app import count_words\n\nclass WordCountTests(unittest.TestCase):\n    def test_texto(self):\n        self.assertEqual(count_words('um  dois\\ntrês'), 3)\n        self.assertEqual(count_words('  '), 0)\n"),
    ("train", "deduplicate", "Crie uma função Python unique_items(items) que remove duplicatas preservando a ordem. Inclua testes unittest.",
     "def unique_items(items):\n    result = []\n    for item in items:\n        if item not in result:\n            result.append(item)\n    return result\n",
     "import unittest\nfrom app import unique_items\n\nclass UniqueItemsTests(unittest.TestCase):\n    def test_ordem(self):\n        self.assertEqual(unique_items([3, 1, 3, 2, 1]), [3, 1, 2])\n        self.assertEqual(unique_items([]), [])\n"),
    ("train", "temperature", "Crie uma função Python celsius_to_fahrenheit(value) que converte Celsius em Fahrenheit. Inclua testes unittest.",
     "def celsius_to_fahrenheit(value):\n    return value * 9 / 5 + 32\n",
     "import unittest\nfrom app import celsius_to_fahrenheit\n\nclass TemperatureTests(unittest.TestCase):\n    def test_conversao(self):\n        self.assertEqual(celsius_to_fahrenheit(0), 32)\n        self.assertEqual(celsius_to_fahrenheit(100), 212)\n"),
    ("validation", "palindrome", "Crie uma função Python is_palindrome(text) que ignora espaços e maiúsculas. Inclua testes unittest.",
     "def is_palindrome(text):\n    normalized = ''.join(text.lower().split())\n    return normalized == normalized[::-1]\n",
     "import unittest\nfrom app import is_palindrome\n\nclass PalindromeTests(unittest.TestCase):\n    def test_espacos_e_caixa(self):\n        self.assertTrue(is_palindrome('Ame a ema'))\n        self.assertFalse(is_palindrome('Python'))\n"),
    ("heldout", "minutes", "Crie uma função Python total_minutes(hours, minutes) que converte horas e minutos em minutos, rejeitando valores negativos. Inclua testes unittest.",
     "def total_minutes(hours, minutes):\n    if hours < 0 or minutes < 0:\n        raise ValueError('tempo negativo')\n    return hours * 60 + minutes\n",
     "import unittest\nfrom app import total_minutes\n\nclass MinutesTests(unittest.TestCase):\n    def test_conversao(self):\n        self.assertEqual(total_minutes(2, 15), 135)\n        with self.assertRaises(ValueError):\n            total_minutes(-1, 0)\n"),
    ("heldout", "initials", "Crie uma função Python initials(name) que retorna as iniciais maiúsculas das palavras de um nome. Inclua testes unittest.",
     "def initials(name):\n    return ''.join(part[0].upper() for part in name.split())\n",
     "import unittest\nfrom app import initials\n\nclass InitialsTests(unittest.TestCase):\n    def test_nome(self):\n        self.assertEqual(initials('ana maria silva'), 'AMS')\n        self.assertEqual(initials('  '), '')\n"),
)


EXTRA_TRAIN_CASES = (
    ("double", "Crie uma função Python double(value) que retorna o dobro de um número. Inclua testes unittest.",
     "def double(value):\n    return value * 2\n", "self.assertEqual(double(7), 14)\n        self.assertEqual(double(-3), -6)"),
    ("square", "Crie uma função Python square(value) que retorna o quadrado de um número. Inclua testes unittest.",
     "def square(value):\n    return value * value\n", "self.assertEqual(square(4), 16)\n        self.assertEqual(square(-3), 9)"),
    ("rectangle_area", "Crie uma função Python rectangle_area(width, height) que calcula a área de um retângulo. Inclua testes unittest.",
     "def rectangle_area(width, height):\n    return width * height\n", "self.assertEqual(rectangle_area(3, 5), 15)\n        self.assertEqual(rectangle_area(0, 5), 0)"),
    ("hours_to_seconds", "Crie uma função Python hours_to_seconds(hours) que converte horas em segundos. Inclua testes unittest.",
     "def hours_to_seconds(hours):\n    return hours * 3600\n", "self.assertEqual(hours_to_seconds(2), 7200)\n        self.assertEqual(hours_to_seconds(0), 0)"),
    ("average", "Crie uma função Python average(values) que calcula a média de uma lista e rejeita lista vazia. Inclua testes unittest.",
     "def average(values):\n    if not values:\n        raise ValueError('lista vazia')\n    return sum(values) / len(values)\n",
     "self.assertEqual(average([2, 4, 6]), 4)\n        with self.assertRaises(ValueError):\n            average([])"),
    ("sum_positive", "Crie uma função Python sum_positive(values) que soma apenas os números positivos de uma lista. Inclua testes unittest.",
     "def sum_positive(values):\n    return sum(value for value in values if value > 0)\n",
     "self.assertEqual(sum_positive([-2, 3, 5]), 8)\n        self.assertEqual(sum_positive([]), 0)"),
    ("even_numbers", "Crie uma função Python even_numbers(values) que retorna somente números pares na ordem original. Inclua testes unittest.",
     "def even_numbers(values):\n    return [value for value in values if value % 2 == 0]\n",
     "self.assertEqual(even_numbers([1, 2, 4, 5]), [2, 4])\n        self.assertEqual(even_numbers([]), [])"),
    ("reverse_words", "Crie uma função Python reverse_words(text) que inverte a ordem das palavras. Inclua testes unittest.",
     "def reverse_words(text):\n    return ' '.join(reversed(text.split()))\n",
     "self.assertEqual(reverse_words('um dois três'), 'três dois um')\n        self.assertEqual(reverse_words(''), '')"),
    ("capitalize_words", "Crie uma função Python capitalize_words(text) que inicia cada palavra com letra maiúscula. Inclua testes unittest.",
     "def capitalize_words(text):\n    return ' '.join(word.capitalize() for word in text.split())\n",
     "self.assertEqual(capitalize_words('ana maria'), 'Ana Maria')\n        self.assertEqual(capitalize_words(''), '')"),
    ("last_word", "Crie uma função Python last_word(text) que retorna a última palavra ou string vazia. Inclua testes unittest.",
     "def last_word(text):\n    words = text.split()\n    return words[-1] if words else ''\n",
     "self.assertEqual(last_word('olá mundo'), 'mundo')\n        self.assertEqual(last_word('  '), '')"),
    ("vowel_count", "Crie uma função Python vowel_count(text) que conta vogais ignorando maiúsculas. Inclua testes unittest.",
     "def vowel_count(text):\n    return sum(char.lower() in 'aeiou' for char in text)\n",
     "self.assertEqual(vowel_count('Ana'), 2)\n        self.assertEqual(vowel_count('xyz'), 0)"),
    ("remove_empty", "Crie uma função Python remove_empty(values) que remove strings vazias de uma lista. Inclua testes unittest.",
     "def remove_empty(values):\n    return [value for value in values if value != '']\n",
     "self.assertEqual(remove_empty(['a', '', 'b']), ['a', 'b'])\n        self.assertEqual(remove_empty([]), [])"),
    ("within_range", "Crie uma função Python within_range(value, lower, upper) que verifica limites inclusivos. Inclua testes unittest.",
     "def within_range(value, lower, upper):\n    return lower <= value <= upper\n",
     "self.assertTrue(within_range(5, 0, 5))\n        self.assertFalse(within_range(6, 0, 5))"),
    ("first_word", "Crie uma função Python first_word(text) que retorna a primeira palavra ou string vazia. Inclua testes unittest.",
     "def first_word(text):\n    words = text.split()\n    return words[0] if words else ''\n",
     "self.assertEqual(first_word('olá mundo'), 'olá')\n        self.assertEqual(first_word('  '), '')"),
    ("absolute_difference", "Crie uma função Python absolute_difference(left, right) que retorna a diferença absoluta. Inclua testes unittest.",
     "def absolute_difference(left, right):\n    return abs(left - right)\n",
     "self.assertEqual(absolute_difference(3, 8), 5)\n        self.assertEqual(absolute_difference(8, 3), 5)"),
    ("join_words", "Crie uma função Python join_words(words, separator) que une palavras com o separador informado. Inclua testes unittest.",
     "def join_words(words, separator):\n    return separator.join(words)\n",
     "self.assertEqual(join_words(['a', 'b'], '-'), 'a-b')\n        self.assertEqual(join_words([], ','), '')"),
)


def expanded_cases():
    for case_id, request, source, assertions in EXTRA_TRAIN_CASES:
        function_name = case_id
        tests = (f"import unittest\nfrom app import {function_name}\n\n"
                 f"class GeneratedTests(unittest.TestCase):\n    def test_behavior(self):\n        {assertions}\n")
        yield "train", case_id, request, source, tests


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default="datasets/implementation_sft_v1")
    parser.add_argument("--prompt-style", choices=("full", "compact"), default="full")
    parser.add_argument("--extended", action="store_true")
    args = parser.parse_args()
    output = ROOT / args.output_dir
    output.mkdir(parents=True, exist_ok=True)
    tokenizer = ByteBPETokenizer.load(ROOT / "model/godmode/godmode-tokenizer-v1.json")
    rows = {split: [] for split in ("train", "validation", "heldout")}
    lengths = []
    cases = CASES + tuple(expanded_cases()) if args.extended else CASES
    for split, case_id, request, source, tests in cases:
        plan = {"assumptions": [], "operations": [
            {"tool": "create_file", "arguments": {"path": "app.py", "content": source}},
            {"tool": "create_file", "arguments": {"path": "test_app.py", "content": tests}},
        ]}
        answer = json.dumps(plan, ensure_ascii=False, separators=(",", ":"))
        parse_implementation_plan(answer, existing_paths=set(), existing_directories=set())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "app.py").write_text(source, encoding="utf-8")
            (root / "test_app.py").write_text(tests, encoding="utf-8")
            import subprocess
            result = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", directory, "-p", "test*.py"],
                                    cwd=directory, capture_output=True, text=True, timeout=10)
            if result.returncode:
                raise RuntimeError(f"{case_id}: {result.stderr}")
        prompt = (compact_implementation_prompt(request) if args.prompt_style == "compact"
                  else implementation_prompt(request))
        row = {"id": case_id, "messages": [{"role": "user", "content": prompt}]}
        if split == "heldout":
            row["reference_answer"] = answer
        else:
            row["messages"].append({"role": "assistant", "content": answer})
        rows[split].append(row)
        lengths.append({"id": case_id, "split": split, "prompt_tokens": len(tokenizer.encode_fast(prompt)),
                        "answer_tokens": len(tokenizer.encode_fast(answer))})
    for split, records in rows.items():
        (output / f"{split}.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in records), encoding="utf-8")
    keys = {split: {row["id"] for row in records} for split, records in rows.items()}
    assert not (keys["train"] & keys["validation"] or keys["train"] & keys["heldout"] or keys["validation"] & keys["heldout"])
    manifest = {"schema": "brasa-implementation-sft/v1", "prompt_style": args.prompt_style, "cases": lengths,
                "splits": {key: len(value) for key, value in rows.items()},
                "files": {split: hashlib.sha256((output / f"{split}.jsonl").read_bytes()).hexdigest() for split in rows},
                "scope": "single-function two-file protocol smoke test; no claim of full-project generalization"}
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
