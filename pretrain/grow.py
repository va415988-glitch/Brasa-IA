"""Aumenta os pesos de um checkpoint decoder_transformer_v2 preservando a função.

Crescimento progressivo: treina-se um modelo pequeno (passos baratos), cresce-se
para um maior que calcula exatamente a mesma função e o treino continua de onde
parou. Nenhum conhecimento aprendido é descartado e os passos iniciais, mais
baratos, aproveitam melhor a CPU.

Operações (podem ser combinadas numa única chamada):

* profundidade: novos blocos com as projeções de saída ``o`` e ``down`` zeradas
  são identidade (x + 0 + 0). Por padrão copiam q/k/v/gate/up do bloco vizinho,
  o que dá um ponto de partida com atributos úteis em vez de ruído.
* largura: o fluxo residual ganha dimensões que começam em zero (embedding e
  linhas de ``o``/``down`` novas zeradas). A RMSNorm passa a dividir pela média
  de mais dimensões; os pesos antigos das normas são multiplicados por
  sqrt(H/H') para compensar. Cabeças novas de atenção têm as colunas de ``o``
  zeradas e usam somente cabeças KV novas (a razão heads/kv_heads é mantida, então
  o agrupamento GQA das cabeças antigas não muda). Unidades novas do SwiGLU têm
  as colunas de ``down`` zeradas.

A única diferença numérica vem do épsilon da RMSNorm (1e-6), que não é
reescalado; ``verify_growth`` mede a diferença dos logits e recusa o resultado
acima da tolerância.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "python"))

KEEP_EQUAL = ("architecture", "vocab_size", "qk_norm", "rope_theta")


def _ffn(config):
    from model_v2 import ffn_size
    return int(config.get("ffn_hidden") or ffn_size(int(config["hidden_size"])))


def target_config(source, layers=None, hidden_size=None, attention_heads=None, kv_heads=None,
                  ffn_hidden=None, context_length=None):
    """Configuração crescida; valida que o crescimento pode preservar a função."""
    if source.get("architecture") != "decoder_transformer_v2":
        raise ValueError("o crescimento exige architecture=decoder_transformer_v2")
    heads = int(source["attention_heads"])
    kv = int(source.get("kv_heads") or heads)
    head_dim = int(source["hidden_size"]) // heads
    new_hidden = int(hidden_size or source["hidden_size"])
    new_heads = int(attention_heads or new_hidden // head_dim)
    if new_hidden != new_heads * head_dim:
        raise ValueError(f"hidden_size deve ser cabeças x {head_dim} (head_dim é preservado)")
    group = heads // kv
    new_kv = int(kv_heads or new_heads // group)
    if new_heads % new_kv or new_heads // new_kv != group:
        raise ValueError(f"a razão attention_heads/kv_heads ({group}) precisa ser mantida para preservar o GQA")
    grown = {**source, "layers": int(layers or source["layers"]), "hidden_size": new_hidden,
             "attention_heads": new_heads, "kv_heads": new_kv,
             "context_length": int(context_length or source["context_length"])}
    grown["ffn_hidden"] = int(ffn_hidden or max(_ffn(source), _ffn({"hidden_size": new_hidden})))
    for key, old, new in [("layers", source["layers"], grown["layers"]), ("hidden_size", source["hidden_size"], new_hidden),
                          ("attention_heads", heads, new_heads), ("ffn_hidden", _ffn(source), grown["ffn_hidden"])]:
        if int(new) < int(old):
            raise ValueError(f"{key} não pode diminuir ({old} -> {new})")
    return grown


def block_order(old_layers, new_layers, strategy="interleave"):
    """Ordem final dos blocos: ("old", i) mantém o bloco i; ("new", i) insere um
    bloco identidade inicializado a partir do bloco antigo i."""
    extra = new_layers - old_layers
    order = [("old", index) for index in range(old_layers)]
    if extra <= 0:
        return order
    if strategy == "top":
        return order + [("new", old_layers - 1)] * extra
    # Intercala: o j-ésimo bloco novo entra depois do bloco antigo
    # floor(j * L / extra), espalhando-os uniformemente pela profundidade.
    anchors = sorted(j * old_layers // extra for j in range(extra))
    result = []
    for index in range(old_layers):
        result.append(("old", index))
        result.extend(("new", index) for anchor in anchors if anchor == index)
    return result


def grow_state(state, source, grown, depth_init="copy", depth_strategy="interleave", seed=0, init_std=0.02):
    """Devolve o state_dict do modelo crescido (tensores fp32)."""
    import torch

    generator = torch.Generator().manual_seed(seed)

    def rand(*shape):
        return torch.randn(*shape, generator=generator) * init_std

    old_hidden, new_hidden = int(source["hidden_size"]), int(grown["hidden_size"])
    heads, new_heads = int(source["attention_heads"]), int(grown["attention_heads"])
    kv = int(source.get("kv_heads") or heads)
    new_kv = int(grown["kv_heads"])
    head_dim = old_hidden // heads
    old_ffn, new_ffn = _ffn(source), int(grown["ffn_hidden"])
    norm_scale = math.sqrt(old_hidden / new_hidden)
    state = {key: value.detach().float().cpu() for key, value in state.items()}
    out = {}

    def widen_norm(weight):
        grown_weight = torch.empty(new_hidden)
        grown_weight[:old_hidden] = weight * norm_scale
        grown_weight[old_hidden:] = (weight * norm_scale).mean()
        return grown_weight

    def expand(weight, rows, cols, zero_new_rows=False, zero_new_cols=False):
        """Copia ``weight`` no canto superior esquerdo de uma matriz rows x cols."""
        old_rows, old_cols = weight.shape
        grown_weight = rand(rows, cols)
        if zero_new_rows:
            grown_weight[old_rows:, :] = 0
        if zero_new_cols:
            grown_weight[:, old_cols:] = 0
        grown_weight[:old_rows, :old_cols] = weight
        return grown_weight

    embedding = state["token_embedding.weight"]
    grown_embedding = torch.zeros(embedding.shape[0], new_hidden)
    grown_embedding[:, :old_hidden] = embedding
    out["token_embedding.weight"] = grown_embedding
    out["lm_head.weight"] = grown_embedding  # pesos amarrados
    out["norm.weight"] = widen_norm(state["norm.weight"])

    def widen_block(prefix):
        block = {}
        block["attn_norm.weight"] = widen_norm(state[f"{prefix}.attn_norm.weight"])
        block["ffn_norm.weight"] = widen_norm(state[f"{prefix}.ffn_norm.weight"])
        block["q.weight"] = expand(state[f"{prefix}.q.weight"], new_heads * head_dim, new_hidden)
        block["k.weight"] = expand(state[f"{prefix}.k.weight"], new_kv * head_dim, new_hidden)
        block["v.weight"] = expand(state[f"{prefix}.v.weight"], new_kv * head_dim, new_hidden)
        # Cabeças novas não escrevem no residual; linhas novas manteriam as
        # dimensões novas do residual em zero.
        block["o.weight"] = expand(state[f"{prefix}.o.weight"], new_hidden, new_heads * head_dim,
                                   zero_new_rows=True, zero_new_cols=True)
        block["gate.weight"] = expand(state[f"{prefix}.gate.weight"], new_ffn, new_hidden)
        block["up.weight"] = expand(state[f"{prefix}.up.weight"], new_ffn, new_hidden)
        block["down.weight"] = expand(state[f"{prefix}.down.weight"], new_hidden, new_ffn,
                                      zero_new_rows=True, zero_new_cols=True)
        for name in ("q_norm.weight", "k_norm.weight"):
            if f"{prefix}.{name}" in state:
                block[name] = state[f"{prefix}.{name}"].clone()
        return block

    widened = [widen_block(f"blocks.{index}") for index in range(int(source["layers"]))]
    order = block_order(int(source["layers"]), int(grown["layers"]), depth_strategy)
    for new_index, (kind, reference) in enumerate(order):
        if kind == "old":
            block = widened[reference]
        else:
            neighbour = widened[reference]
            block = {}
            for name, tensor in neighbour.items():
                if name.endswith("norm.weight"):
                    block[name] = tensor.clone()
                elif depth_init == "copy":
                    block[name] = tensor.clone()
                else:
                    block[name] = rand(*tensor.shape)
            # Saídas zeradas: o bloco novo é exatamente a identidade no residual.
            block["o.weight"] = torch.zeros_like(neighbour["o.weight"])
            block["down.weight"] = torch.zeros_like(neighbour["down.weight"])
        for name, tensor in block.items():
            out[f"blocks.{new_index}.{name}"] = tensor
    return out


def verify_growth(state, source, grown_state, grown, tokens=None, tolerance=1e-3):
    """Compara os logits do modelo original e do crescido em fp32."""
    import torch
    from model import build_model

    small = build_model({**source, "context_length": min(int(source["context_length"]), 128)})
    small.load_state_dict({k: v.float() for k, v in state.items()})
    large = build_model({**grown, "context_length": min(int(grown["context_length"]), 128)})
    large.load_state_dict(grown_state)
    small.eval(), large.eval()
    if tokens is None:
        tokens = torch.randint(4, int(source["vocab_size"]), (2, 64), generator=torch.Generator().manual_seed(5))
    with torch.inference_mode():
        before, after = small(tokens).float(), large(tokens).float()
    difference = float((before - after).abs().max())
    scale = float(before.abs().max()) or 1.0
    report = {"max_abs_logit_difference": difference, "relative": difference / scale,
              "small_parameters": sum(p.numel() for p in small.parameters()),
              "large_parameters": sum(p.numel() for p in large.parameters()), "tolerance": tolerance}
    if difference / scale > tolerance:
        raise ValueError(f"crescimento não preservou a função: diferença relativa {difference / scale:.2e}")
    return report


def grow_checkpoint(source_path, output_path, **sizes):
    from checkpoint_io import load_checkpoint, save_checkpoint

    checkpoint = load_checkpoint(source_path)
    source = checkpoint["config"]
    options = {key: sizes.pop(key) for key in ("depth_init", "depth_strategy", "seed") if key in sizes}
    grown = target_config(source, **sizes)
    grown_state = grow_state(checkpoint["state_dict"], source, grown, **options)
    report = verify_growth(checkpoint["state_dict"], source, grown_state, grown)
    lineage = list(checkpoint.get("growth_lineage") or [])
    lineage.append({"from": Path(source_path).name,
                    "from_sha256": hashlib.sha256(Path(source_path).read_bytes()).hexdigest(),
                    "from_shape": {k: source.get(k) for k in ("layers", "hidden_size", "attention_heads", "kv_heads", "ffn_hidden")},
                    "to_shape": {k: grown.get(k) for k in ("layers", "hidden_size", "attention_heads", "kv_heads", "ffn_hidden")},
                    "verification": report, **options})
    grown["name"] = f"{source.get('name', 'brasa')}-grown-{grown['layers']}x{grown['hidden_size']}"
    metadata = {key: value for key, value in checkpoint.items() if key not in {"state_dict", "config"}}
    save_checkpoint({**metadata, "config": grown, "state_dict": grown_state, "growth_lineage": lineage,
                     "source": "function-preserving-growth"}, output_path)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("source", help="checkpoint .safetensors decoder_transformer_v2")
    parser.add_argument("output", help="destino .safetensors")
    for name in ("layers", "hidden-size", "attention-heads", "kv-heads", "ffn-hidden", "context-length"):
        parser.add_argument(f"--{name}", type=int)
    parser.add_argument("--depth-init", choices=("copy", "random"), default="copy")
    parser.add_argument("--depth-strategy", choices=("interleave", "top"), default="interleave")
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()
    report = grow_checkpoint(args.source, args.output, layers=args.layers, hidden_size=args.hidden_size,
                             attention_heads=args.attention_heads, kv_heads=args.kv_heads,
                             ffn_hidden=args.ffn_hidden, context_length=args.context_length,
                             depth_init=args.depth_init, depth_strategy=args.depth_strategy, seed=args.seed)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
