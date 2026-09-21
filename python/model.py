"""Primeira arquitetura Transformer causal do projeto."""


def build_model(config):
    import torch
    from torch import nn

    class CausalTransformerLM(nn.Module):
        def __init__(self):
            super().__init__()
            hidden = config["hidden_size"]
            layers = config["layers"]
            heads = config["attention_heads"]
            vocab = config["vocab_size"]
            context = config["context_length"]
            self.token_embedding = nn.Embedding(vocab, hidden)
            self.position_embedding = nn.Embedding(context, hidden)
            block = nn.TransformerEncoderLayer(
                d_model=hidden,
                nhead=heads,
                dim_feedforward=hidden * config.get("feed_forward_multiplier", 4),
                dropout=0.0,
                activation="gelu",
                batch_first=True,
                norm_first=True,
            )
            self.blocks = nn.TransformerEncoder(block, num_layers=layers)
            self.norm = nn.LayerNorm(hidden)
            self.lm_head = nn.Linear(hidden, vocab, bias=False)
            self.lm_head.weight = self.token_embedding.weight
            if config.get('initialization') == 'scaled-normal-v1':
                # Embedding e saída compartilham pesos: std=1 (padrão de
                # nn.Embedding) produz logits excessivos no início do treino.
                # Inicializa cada camada separadamente, inclusive as cópias
                # construídas pelo TransformerEncoder.
                with torch.no_grad():
                    for name, parameter in self.named_parameters():
                        if parameter.ndim >= 2:
                            scale = 0.02
                            if name.endswith(('out_proj.weight', 'linear2.weight')):
                                scale /= (2 * layers) ** 0.5
                            nn.init.normal_(parameter, mean=0.0, std=scale)
                        elif name.endswith('bias'):
                            nn.init.zeros_(parameter)
            # A máscara é fixa para o contexto do modelo. Mantê-la como
            # buffer não persistente evita reconstruí-la em cada forward sem
            # alterar a compatibilidade dos checkpoints existentes.
            self.register_buffer("causal_mask", torch.triu(torch.full((context, context), float("-inf")), diagonal=1), persistent=False)

        def forward(self, tokens):
            _, length = tokens.shape
            positions = torch.arange(length, device=tokens.device).unsqueeze(0)
            hidden = self.token_embedding(tokens) + self.position_embedding(positions)
            hidden = self.blocks(hidden, mask=self.causal_mask[:length, :length])
            return self.lm_head(self.norm(hidden))

    return CausalTransformerLM()
