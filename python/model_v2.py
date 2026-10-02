"""Arquitetura moderna do projeto: RMSNorm, RoPE, SwiGLU e atenção com grupos de KV.

Mesma interface de ``model.build_model`` (forward, prefill_with_cache e
forward_next_with_cache, com cache ``(chave, valor)`` por camada), para o servidor
local carregar os pesos sem mudanças. Não há tabela de posições: o contexto de
execução pode superar o de treino apenas com validação separada.
"""

import math


def ffn_size(hidden):
    """SwiGLU usa ~8/3 da largura para igualar o custo de um FFN GELU de 4x."""
    return 64 * max(1, round(8 * hidden / 3 / 64))


def build_model_v2(config):
    import torch
    import torch.nn.functional as F
    from torch import nn

    hidden = int(config["hidden_size"])
    layers = int(config["layers"])
    heads = int(config["attention_heads"])
    kv_heads = int(config.get("kv_heads") or heads)
    vocab = int(config["vocab_size"])
    context = int(config["context_length"])
    theta = float(config.get("rope_theta", 10000.0))
    if hidden % heads or heads % kv_heads or (hidden // heads) % 2:
        raise ValueError("hidden_size, attention_heads e kv_heads incompatíveis")
    head_dim = hidden // heads
    ffn = int(config.get("ffn_hidden") or ffn_size(hidden))

    class RMSNorm(nn.Module):
        def __init__(self):
            super().__init__()
            self.weight = nn.Parameter(torch.ones(hidden))

        def forward(self, x):
            scale = torch.rsqrt(x.float().pow(2).mean(-1, keepdim=True) + 1e-6)
            return (x.float() * scale).to(x.dtype) * self.weight

    def rotate(x, cos, sin):
        # x: [B, H, T, D]; cos/sin: [T, D/2]
        first, second = x[..., : head_dim // 2], x[..., head_dim // 2:]
        return torch.cat((first * cos - second * sin, second * cos + first * sin), dim=-1)

    class Block(nn.Module):
        def __init__(self):
            super().__init__()
            self.attn_norm = RMSNorm()
            self.q = nn.Linear(hidden, heads * head_dim, bias=False)
            self.k = nn.Linear(hidden, kv_heads * head_dim, bias=False)
            self.v = nn.Linear(hidden, kv_heads * head_dim, bias=False)
            self.o = nn.Linear(heads * head_dim, hidden, bias=False)
            self.ffn_norm = RMSNorm()
            self.gate = nn.Linear(hidden, ffn, bias=False)
            self.up = nn.Linear(hidden, ffn, bias=False)
            self.down = nn.Linear(ffn, hidden, bias=False)

        def project(self, x, cos, sin):
            batch, length, _ = x.shape
            q = self.q(x).view(batch, length, heads, head_dim).transpose(1, 2)
            k = self.k(x).view(batch, length, kv_heads, head_dim).transpose(1, 2)
            v = self.v(x).view(batch, length, kv_heads, head_dim).transpose(1, 2)
            return rotate(q, cos, sin), rotate(k, cos, sin), v

        def finish(self, x, attended):
            batch, _, length, _ = attended.shape
            x = x + self.o(attended.transpose(1, 2).reshape(batch, length, heads * head_dim))
            y = self.ffn_norm(x)
            return x + self.down(F.silu(self.gate(y)) * self.up(y))

    class CausalTransformerV2(nn.Module):
        def __init__(self):
            super().__init__()
            self.supports_kv_cache = True
            self.token_embedding = nn.Embedding(vocab, hidden)
            self.blocks = nn.ModuleList(Block() for _ in range(layers))
            self.norm = RMSNorm()
            self.lm_head = nn.Linear(hidden, vocab, bias=False)
            self.lm_head.weight = self.token_embedding.weight
            inverse = 1.0 / theta ** (torch.arange(0, head_dim, 2).float() / head_dim)
            angles = torch.outer(torch.arange(context).float(), inverse)
            self.register_buffer("rope_cos", angles.cos(), persistent=False)
            self.register_buffer("rope_sin", angles.sin(), persistent=False)
            with torch.no_grad():
                for name, parameter in self.named_parameters():
                    if parameter.ndim >= 2:
                        scale = 0.02
                        if name.endswith(("o.weight", "down.weight")):
                            scale /= math.sqrt(2 * layers)
                        nn.init.normal_(parameter, mean=0.0, std=scale)

        def _rope(self, start, length, dtype):
            return (self.rope_cos[start:start + length].to(dtype),
                    self.rope_sin[start:start + length].to(dtype))

        def forward(self, tokens):
            _, length = tokens.shape
            if length > context:
                raise ValueError(f"sequência acima do contexto configurado: {length} > {context}")
            x = self.token_embedding(tokens)
            cos, sin = self._rope(0, length, x.dtype)
            for block in self.blocks:
                q, k, v = block.project(block.attn_norm(x), cos, sin)
                attended = F.scaled_dot_product_attention(q, k, v, is_causal=True, enable_gqa=kv_heads != heads)
                x = block.finish(x, attended)
            return self.lm_head(self.norm(x))

        def prefill_with_cache(self, tokens, cache_capacity=None):
            if self.training or torch.is_grad_enabled():
                raise RuntimeError("prefill com KV cache exige eval e inference_mode")
            batch, length = tokens.shape
            capacity = int(cache_capacity or context)
            if not length <= capacity <= context:
                raise ValueError("capacidade KV incompatível com o contexto")
            x = self.token_embedding(tokens)
            cos, sin = self._rope(0, length, x.dtype)
            caches = []
            for block in self.blocks:
                q, k, v = block.project(block.attn_norm(x), cos, sin)
                cached_key = torch.empty(batch, kv_heads, capacity, head_dim, device=x.device, dtype=k.dtype)
                cached_value = torch.empty_like(cached_key)
                cached_key[:, :, :length].copy_(k)
                cached_value[:, :, :length].copy_(v)
                caches.append((cached_key, cached_value))
                attended = F.scaled_dot_product_attention(q, k, v, is_causal=True, enable_gqa=kv_heads != heads)
                x = block.finish(x, attended)
            return self.lm_head(self.norm(x[:, -1])), caches, length

        def forward_next_with_cache(self, token, position, caches, cache_length):
            if self.training or torch.is_grad_enabled():
                raise RuntimeError("decodificação KV exige eval e inference_mode")
            position, cache_length = int(position), int(cache_length)
            if position != cache_length or position >= context:
                raise ValueError("posição fora da capacidade do KV cache")
            x = self.token_embedding(token.reshape(-1, 1))
            cos, sin = self._rope(position, 1, x.dtype)
            updated = []
            for block, (cached_key, cached_value) in zip(self.blocks, caches):
                q, k, v = block.project(block.attn_norm(x), cos, sin)
                cached_key[:, :, cache_length:cache_length + 1].copy_(k)
                cached_value[:, :, cache_length:cache_length + 1].copy_(v)
                attended = F.scaled_dot_product_attention(
                    q, cached_key[:, :, :cache_length + 1], cached_value[:, :, :cache_length + 1],
                    enable_gqa=kv_heads != heads)
                x = block.finish(x, attended)
                updated.append((cached_key, cached_value))
            return self.lm_head(self.norm(x[:, -1])), updated

    return CausalTransformerV2()
