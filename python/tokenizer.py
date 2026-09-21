"""Tokenizer byte-level BPE pequeno e independente para o projeto."""

from __future__ import annotations

import json
from pathlib import Path


class ByteBPETokenizer:
    def __init__(self, vocab=None, merges=None, special_tokens=None):
        self.special_tokens = special_tokens or {"<pad>": 0, "<bos>": 1, "<eos>": 2, "<unk>": 3}
        self.vocab = vocab or {}
        self.merges = merges or []
        self._merge_map = {(left, right): new for left, right, new in self.merges}

    @classmethod
    def train(cls, texts, vocab_size=8192, min_frequency=2):
        tokenizer = cls()
        next_id = max(tokenizer.special_tokens.values()) + 1
        for byte in range(256):
            tokenizer.vocab[bytes([byte]).hex()] = next_id
            next_id += 1

        byte_ids = {byte: tokenizer.vocab[bytes([byte]).hex()] for byte in range(256)}
        sequences = [[byte_ids[byte] for byte in text.encode("utf-8")] for text in texts if text]
        while next_id < vocab_size:
            counts = {}
            for sequence in sequences:
                for left, right in zip(sequence, sequence[1:]):
                    counts[(left, right)] = counts.get((left, right), 0) + 1
            if not counts:
                break
            pair, frequency = max(counts.items(), key=lambda item: item[1])
            if frequency < min_frequency:
                break
            left, right = pair
            merged_bytes = tokenizer._bytes_for_id(left) + tokenizer._bytes_for_id(right)
            tokenizer.vocab[merged_bytes.hex()] = next_id
            tokenizer.merges.append([left, right, next_id])
            tokenizer._merge_map[pair] = next_id
            sequences = [tokenizer._apply_pair(sequence, pair, next_id) for sequence in sequences]
            next_id += 1
        return tokenizer

    def _bytes_for_id(self, token_id):
        for encoded, value in self.vocab.items():
            if value == token_id:
                return bytes.fromhex(encoded)
        raise KeyError(f"token inexistente: {token_id}")

    @staticmethod
    def _apply_pair(sequence, pair, replacement):
        result = []
        index = 0
        while index < len(sequence):
            if index + 1 < len(sequence) and (sequence[index], sequence[index + 1]) == pair:
                result.append(replacement)
                index += 2
            else:
                result.append(sequence[index])
                index += 1
        return result

    def encode(self, text, add_bos=False, add_eos=False):
        tokens = [self.vocab[bytes([byte]).hex()] for byte in text.encode("utf-8")]
        for left, right, replacement in self.merges:
            tokens = self._apply_pair(tokens, (left, right), replacement)
        if add_bos:
            tokens.insert(0, self.special_tokens["<bos>"])
        if add_eos:
            tokens.append(self.special_tokens["<eos>"])
        return tokens

    def decode(self, token_ids):
        special_by_id = {value: key for key, value in self.special_tokens.items()}
        chunks = []
        raw = bytearray()

        def flush_raw():
            if raw:
                chunks.append(bytes(raw).decode("utf-8", errors="replace"))
                raw.clear()

        for token_id in token_ids:
            if token_id in special_by_id:
                flush_raw()
                chunks.append(special_by_id[token_id])
            else:
                raw.extend(self._bytes_for_id(token_id))
        flush_raw()
        return "".join(chunks)

    def save(self, path):
        Path(path).write_text(json.dumps({"special_tokens": self.special_tokens, "vocab": self.vocab, "merges": self.merges}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    @classmethod
    def load(cls, path):
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(data["vocab"], data["merges"], data["special_tokens"])
