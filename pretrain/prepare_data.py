"""Prepara dados abertos e o tokenizer próprio para o pré-treino do zero.

Etapas (cada uma é retomável; o que já existe em --out é reaproveitado):
  1. baixa texto em streaming (teto por fonte em sources.json) -> out/text/*.txt
  2. treina um BPE byte-level e grava no formato do projeto (ByteBPETokenizer)
  3. prova que o tokenizer rápido (Rust) é idêntico ao tokenizer do projeto
  4. tokeniza tudo -> out/train.bin e out/val.bin (uint16, <eos> entre documentos)

Dependências extras (só para preparar dados): datasets, tokenizers, numpy.
"""

import argparse
import glob
import hashlib
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "python"))
from tokenizer import ByteBPETokenizer  # noqa: E402

DOC_SEP = "\x00"  # separa documentos nos .txt; nunca aparece em texto limpo


def clean(text):
    return str(text).replace(DOC_SEP, " ").strip()


def iter_chat(pattern):
    for path in sorted(glob.glob(str(ROOT / pattern))):
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            try:
                messages = json.loads(line).get("messages", [])
            except json.JSONDecodeError:
                continue
            parts = []
            for message in messages:
                content = message.get("content")
                if message.get("role") in {"user", "assistant"} and isinstance(content, str):
                    parts.append(f"<|{message['role']}|>\n{content}\n")
            if len(parts) >= 2:
                yield "".join(parts)


def iter_text(pattern):
    for path in sorted(glob.glob(str(ROOT / pattern))):
        yield Path(path).read_text(encoding="utf-8")


def iter_nul(pattern):
    """Arquivos locais com documentos já separados por NUL (pretrain/corpus_fetch, data_engine)."""
    for path in sorted(glob.glob(str(ROOT / pattern))):
        yield from read_docs(path)


def iter_hf(source):
    from datasets import load_dataset
    args = [source["dataset"]] + ([source["config"]] if source.get("config") else [])
    for row in load_dataset(*args, split=source["split"], streaming=True):
        yield row[source["field"]]


def download(sources, out, scale):
    text_dir = out / "text"
    text_dir.mkdir(parents=True, exist_ok=True)
    for source in sources:
        target = text_dir / f"{source['name']}.txt"
        if target.exists() and not target.with_suffix(".part").exists():
            print(f"[dados] {source['name']}: já existe, pulando")
            continue
        budget = int(source["mb"] * scale * 1_000_000)
        iterator = {"hf": iter_hf, "chat": lambda s: iter_chat(s["glob"]),
                    "text": lambda s: iter_text(s["glob"]),
                    "nul": lambda s: iter_nul(s["glob"])}[source["kind"]](source)
        written, docs = 0, 0
        part = target.with_suffix(".part")
        try:
            with part.open("w", encoding="utf-8") as handle:
                for text in iterator:
                    text = clean(text)
                    if len(text) < 200 and source["kind"] == "hf":
                        continue
                    handle.write(text + DOC_SEP)
                    written += len(text.encode("utf-8"))
                    docs += 1
                    if written >= budget:
                        break
        except Exception as error:  # uma fonte fora do ar não derruba as demais
            print(f"[dados] {source['name']}: ERRO {type(error).__name__}: {error}")
        if written:
            part.rename(target)
            print(f"[dados] {source['name']}: {docs} docs, {written / 1e6:.0f} MB")
        else:
            part.unlink(missing_ok=True)


def read_docs(path):
    with Path(path).open(encoding="utf-8") as handle:
        buffer = ""
        while chunk := handle.read(1 << 22):
            buffer += chunk
            *docs, buffer = buffer.split(DOC_SEP)
            yield from (d for d in docs if d)
        if buffer:
            yield buffer


def train_tokenizer(out, vocab_size, sample_mb):
    from tokenizers import Tokenizer, models, pre_tokenizers, trainers
    target = out / "tokenizer.json"
    if target.exists():
        print("[tokenizer] já existe, pulando")
        return target
    specials = {"<pad>": 0, "<bos>": 1, "<eos>": 2, "<unk>": 3}
    per_source = int(sample_mb * 1_000_000 / max(1, len(list((out / "text").glob("*.txt")))))
    # Texto embaralhado por fonte para o tokenizer não favorecer o início dos arquivos.
    def sample():
        rng = random.Random(7)
        for path in sorted((out / "text").glob("*.txt")):
            taken = 0
            for doc in read_docs(path):
                if rng.random() < 0.7:
                    yield doc[:20_000]
                    taken += min(len(doc), 20_000)
                    if taken >= per_source:
                        break
    hf = Tokenizer(models.BPE())
    hf.pre_tokenizer = pre_tokenizers.ByteLevel(add_prefix_space=False, use_regex=False)
    trainer = trainers.BpeTrainer(vocab_size=vocab_size - len(specials),
                                  initial_alphabet=pre_tokenizers.ByteLevel.alphabet(), show_progress=False)
    hf.train_from_iterator(sample(), trainer)
    model = json.loads(hf.to_str())["model"]
    # HF usa símbolos unicode para bytes (tabela GPT-2); converte para bytes reais.
    table = {ch: b for b, ch in zip(*_gpt2_table())}
    to_bytes = lambda token: bytes(table[ch] for ch in token)
    tokenizer = ByteBPETokenizer(special_tokens=specials)
    next_id = len(specials)
    ids = {}
    for byte in range(256):
        tokenizer.vocab[bytes([byte]).hex()] = next_id
        ids[bytes([byte])] = next_id
        next_id += 1
    for pair in model["merges"]:
        left, right = pair if isinstance(pair, list) else pair.split(" ")
        left_b, right_b = to_bytes(left), to_bytes(right)
        merged = left_b + right_b
        if merged in ids or left_b not in ids or right_b not in ids:
            continue
        tokenizer.vocab[merged.hex()] = next_id
        tokenizer.merges.append([ids[left_b], ids[right_b], next_id])
        ids[merged] = next_id
        next_id += 1
    tokenizer.save(target)
    print(f"[tokenizer] {next_id} tokens, {len(tokenizer.merges)} merges -> {target}")
    return target


def _gpt2_table():
    """bytes -> caractere unicode, igual ao ByteLevel do HF."""
    keep = list(range(33, 127)) + list(range(161, 173)) + list(range(174, 256))
    chars, extra = list(keep), 0
    for byte in range(256):
        if byte not in keep:
            keep.append(byte)
            chars.append(256 + extra)
            extra += 1
    return keep, [chr(c) for c in chars]


def fast_tokenizer(path):
    """Tokenizer Rust construído a partir do JSON do projeto (mesmos ids e merges)."""
    from tokenizers import Tokenizer, models, pre_tokenizers
    project = json.loads(Path(path).read_text(encoding="utf-8"))
    keep, chars = _gpt2_table()
    symbol = dict(zip(keep, chars))
    to_symbols = lambda raw: "".join(symbol[b] for b in raw)
    by_id = {int(i): bytes.fromhex(h) for h, i in project["vocab"].items()}
    vocab = {name: i for name, i in project["special_tokens"].items()}
    vocab.update({to_symbols(raw): i for i, raw in by_id.items()})
    merges = [(to_symbols(by_id[a]), to_symbols(by_id[b])) for a, b, _ in project["merges"]]
    fast = Tokenizer(models.BPE(vocab=vocab, merges=merges))
    fast.pre_tokenizer = pre_tokenizers.ByteLevel(add_prefix_space=False, use_regex=False)
    return fast


def verify_equivalence(path, out, samples=300):
    slow = ByteBPETokenizer.load(path)
    fast = fast_tokenizer(path)
    rng = random.Random(11)
    checked = 0
    for source in sorted((out / "text").glob("*.txt")):
        docs = [d[:1500] for _, d in zip(range(60), read_docs(source))]
        for doc in rng.sample(docs, min(len(docs), samples // 7 + 1)):
            if slow.encode_fast(doc) != fast.encode(doc).ids:
                raise SystemExit(f"tokenizer rápido difere do tokenizer do projeto em {source.name}")
            if slow.decode(slow.encode_fast(doc)) != doc:
                raise SystemExit(f"decode não reproduz o texto em {source.name}")
            checked += 1
    print(f"[tokenizer] idêntico ao do projeto em {checked} amostras")


def tokenize(out, val_fraction, tokenizer_path):
    import numpy as np
    sha = hashlib.sha256(Path(tokenizer_path).read_bytes()).hexdigest()
    try:
        done = json.loads((out / "meta.json").read_text())
    except (OSError, ValueError):
        done = {}
    if (done.get("tokenizer_sha256") == sha and (out / "train.bin").exists()
            and (out / "train.bin").stat().st_size == 2 * done.get("train_tokens", -1)):
        print(f"[tokens] já tokenizado ({done['train_tokens'] / 1e6:.0f}M tokens), pulando")
        return
    fast = fast_tokenizer(tokenizer_path)
    eos = 2
    rng = random.Random(3)
    for name in ("train", "val"):
        (out / f"{name}.bin").unlink(missing_ok=True)
    counts = {"train": 0, "val": 0}
    handles = {name: (out / f"{name}.bin").open("ab") for name in counts}
    try:
        for path in sorted((out / "text").glob("*.txt")):
            batch = []
            def flush():
                for encoding in fast.encode_batch(batch):
                    split = "val" if rng.random() < val_fraction else "train"
                    ids = np.array(encoding.ids + [eos], dtype=np.uint16)
                    handles[split].write(ids.tobytes())
                    counts[split] += len(ids)
                batch.clear()
            for doc in read_docs(path):
                batch.append(doc)
                if len(batch) >= 2000:
                    flush()
            flush()
            print(f"[tokens] {path.name}: train {counts['train'] / 1e6:.1f}M val {counts['val'] / 1e6:.2f}M")
    finally:
        for handle in handles.values():
            handle.close()
    meta = {"train_tokens": counts["train"], "val_tokens": counts["val"],
            "tokenizer_sha256": hashlib.sha256(Path(tokenizer_path).read_bytes()).hexdigest()}
    (out / "meta.json").write_text(json.dumps(meta, indent=2) + "\n")
    print("[tokens]", meta)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", default="pretrain_data")
    parser.add_argument("--sources", default=str(Path(__file__).with_name("sources.json")))
    parser.add_argument("--scale", type=float, default=1.0, help="multiplica o teto de MB de cada fonte")
    parser.add_argument("--only", default="", help="lista de fontes separadas por vírgula")
    parser.add_argument("--vocab-size", type=int, default=32000)
    parser.add_argument("--tokenizer-sample-mb", type=int, default=400)
    parser.add_argument("--val-fraction", type=float, default=0.005)
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    sources = json.loads(Path(args.sources).read_text(encoding="utf-8"))["sources"]
    if args.only:
        wanted = set(args.only.split(","))
        sources = [s for s in sources if s["name"] in wanted]
    download(sources, out, args.scale)
    path = train_tokenizer(out, args.vocab_size, args.tokenizer_sample_mb)
    verify_equivalence(path, out)
    tokenize(out, args.val_fraction, path)


if __name__ == "__main__":
    main()
