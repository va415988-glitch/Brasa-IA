"""Primeira arquitetura Transformer causal do projeto."""


def extend_position_embeddings(position_weights, target_context, source_context=None):
    """Estende posições sem alterar as que receberam gradiente no treino.

    Checkpoints antigos podem ter uma tabela alocada maior que as sequências
    usadas no treino. ``source_context`` delimita a faixa que recebeu loss.
    A interpolação fornece apenas uma cauda experimental; o prefixo treinado
    precisa permanecer exato para preservar o comportamento em prompts curtos.
    """
    import torch.nn.functional as F

    target_context = int(target_context)
    source_context = min(
        int(source_context or position_weights.shape[0]),
        int(position_weights.shape[0]),
    )
    if target_context < 1 or source_context < 1:
        raise ValueError("contexto posicional precisa ser positivo")
    learned = position_weights[:source_context]
    if target_context == source_context:
        return learned.contiguous()
    extended = F.interpolate(
        learned.float().T.unsqueeze(0),
        size=target_context,
        mode="linear",
        align_corners=True,
    ).squeeze(0).T.contiguous().to(position_weights.dtype)
    extended[:source_context] = learned
    return extended


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
            copy_size = int(config.get('copy_attention_size') or 0)
            self.supports_kv_cache = True
            if copy_size:
                boundary = list(config.get('copy_boundary_ids') or [])
                if not boundary or any(not isinstance(i, int) or i < 0 or i >= vocab for i in boundary):
                    raise ValueError('Cópia neural exige um delimitador de resposta válido.')
                self.register_buffer('copy_boundary', torch.tensor(boundary), persistent=False)
                self.copy_query = nn.Linear(hidden, copy_size, bias=False)
                self.copy_key = nn.Linear(hidden, copy_size, bias=False)
                self.copy_gate = nn.Linear(hidden, 1)
                if config.get('copy_transition'):
                    self.copy_continue = nn.Linear(hidden, 1)
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
        def forward(self, tokens, return_copy_state=False):
            _, length = tokens.shape
            if length > config["context_length"]:
                raise ValueError(
                    f"sequência acima do contexto configurado: {length} > {config['context_length']}"
                )
            positions = torch.arange(length, device=tokens.device).unsqueeze(0)
            hidden = self.token_embedding(tokens) + self.position_embedding(positions)
            # Em contextos longos, calcular todas as linhas de atenção de uma
            # vez faz o backend CPU materializar tensores quadráticos enormes.
            # Consultas em blocos mantêm as mesmas chaves/valores causais e os
            # mesmos pesos, limitando a memória temporária a bloco x contexto.
            chunk_size = int(config.get("attention_chunk_size", 512))
            if length > chunk_size:
                for layer in self.blocks.layers:
                    residual = hidden
                    query_input = layer.norm1(hidden) if layer.norm_first else hidden
                    attended = []
                    key_positions = torch.arange(length, device=tokens.device)
                    for start in range(0, length, chunk_size):
                        end = min(start + chunk_size, length)
                        query_positions = torch.arange(start, end, device=tokens.device)
                        future_keys = key_positions.unsqueeze(0) > query_positions.unsqueeze(1)
                        mask = torch.zeros_like(future_keys, dtype=hidden.dtype).masked_fill_(future_keys, float("-inf"))
                        query = query_input[:, start:end, :]
                        result = layer.self_attn(
                            query, query_input, query_input,
                            attn_mask=mask,
                            need_weights=False,
                        )[0]
                        attended.append(result)
                    attention_output = torch.cat(attended, dim=1)
                    if layer.norm_first:
                        hidden = residual + layer.dropout1(attention_output)
                        feed_input = layer.norm2(hidden)
                        feed = layer.linear2(layer.dropout(layer.activation(layer.linear1(feed_input))))
                        hidden = hidden + layer.dropout2(feed)
                    else:
                        hidden = layer.norm1(residual + layer.dropout1(attention_output))
                        feed = layer.linear2(layer.dropout(layer.activation(layer.linear1(hidden))))
                        hidden = layer.norm2(hidden + layer.dropout2(feed))
                if self.blocks.norm is not None:
                    hidden = self.blocks.norm(hidden)
            else:
                causal_mask = torch.triu(
                    torch.full((length, length), float("-inf"), device=tokens.device),
                    diagonal=1,
                )
                hidden = self.blocks(hidden, mask=causal_mask)
            hidden = self.norm(hidden)
            logits = self.lm_head(hidden)
            if hasattr(self, 'copy_query'):
                return self._mix_copy_logits(tokens, hidden, logits, return_copy_state)
            if return_copy_state:
                raise ValueError('Estado de cópia exige uma cabeça de cópia.')
            return logits

        def _mix_copy_logits(self, tokens, hidden, logits, return_state=False):
            """Learn a generation/copy mixture; source choice has no task-specific rules."""
            batch, length = tokens.shape
            size = len(self.copy_boundary)
            starts = torch.full_like(tokens, -1)
            if length >= size:
                matches = (tokens.unfold(1, size, 1) == self.copy_boundary).all(-1)
                indices = torch.arange(length - size + 1, device=tokens.device).expand(batch, -1)
                starts[:, size - 1:] = torch.where(matches, indices, -1)
            # Each query sees only boundaries already completed at that position.
            # Finding the last boundary globally would leak future role markers.
            source_end = starts.cummax(1).values
            positions = torch.arange(length, device=tokens.device)
            allowed = positions[None, None, :] < source_end[:, :, None]
            query = self.copy_query(hidden)
            key = self.copy_key(hidden)
            scores = query @ key.transpose(1, 2) / query.shape[-1] ** 0.5
            attention = scores.masked_fill(~allowed, -1e4).softmax(-1) * allowed
            mixture = self.copy_gate(hidden).sigmoid() * allowed.any(-1, keepdim=True)
            continuation = None
            if hasattr(self, 'copy_continue'):
                continuation = self.copy_continue(hidden).sigmoid()
                previous = torch.zeros_like(attention[:, 0])
                active = (source_end >= 0).any(0).nonzero()
                first = int(active[0]) if len(active) else length
                aligned = [attention[:, :first]]
                # One UnbindBackward stacks query gradients once. Repeated
                # slices otherwise allocate a full B x S x S gradient per query.
                base_queries = attention.unbind(1)
                continuation_queries = continuation.unbind(1)
                mixture_queries = mixture.unbind(1)
                for step in range(first, length):
                    shifted = nn.functional.pad(previous[:, :-1], (1, 0)) * allowed[:, step]
                    weight = continuation_queries[step]
                    if step:
                        same_source = (source_end[:, step] == source_end[:, step - 1]).unsqueeze(-1)
                        weight = weight * mixture_queries[step - 1] * same_source
                    else:
                        weight = weight * 0
                    current = (1 - weight) * base_queries[step] + weight * shifted
                    mass = current.sum(-1, keepdim=True)
                    previous = torch.where(mass > 1e-8, current / mass.clamp_min(1e-8), base_queries[step])
                    aligned.append(previous.unsqueeze(1))
                attention = torch.cat(aligned, dim=1)
            copied = torch.zeros_like(logits).scatter_add(
                -1, tokens[:, None, :].expand(batch, length, length), attention)
            probabilities = (1 - mixture) * logits.softmax(-1) + mixture * copied
            result = probabilities.clamp_min(1e-9).log()
            if return_state:
                return result, {'attention': attention, 'mixture': mixture.squeeze(-1),
                                'continuation': None if continuation is None else continuation.squeeze(-1),
                                'source_end': source_end}
            return result

        @staticmethod
        def _project_qkv(layer, hidden):
            attention = layer.self_attn
            if not attention._qkv_same_embed_dim or attention.bias_k is not None or attention.bias_v is not None or attention.add_zero_attn:
                raise ValueError("KV cache só suporta a atenção padrão desta arquitetura")
            query, key, value = nn.functional.linear(
                hidden, attention.in_proj_weight, attention.in_proj_bias,
            ).chunk(3, dim=-1)
            batch, length, _ = query.shape
            heads = attention.num_heads
            width = attention.head_dim
            reshape = lambda tensor: tensor.view(batch, length, heads, width).transpose(1, 2).contiguous()
            return reshape(query), reshape(key), reshape(value)

        @staticmethod
        def _attention_output(layer, attended):
            batch, heads, length, width = attended.shape
            merged = attended.transpose(1, 2).contiguous().view(batch, length, heads * width)
            return layer.self_attn.out_proj(merged)

        @staticmethod
        def _finish_layer(layer, hidden, residual, attention_output):
            if layer.norm_first:
                hidden = residual + layer.dropout1(attention_output)
                feed_input = layer.norm2(hidden)
                feed = layer.linear2(layer.dropout(layer.activation(layer.linear1(feed_input))))
                return hidden + layer.dropout2(feed)
            hidden = layer.norm1(residual + layer.dropout1(attention_output))
            feed = layer.linear2(layer.dropout(layer.activation(layer.linear1(hidden))))
            return layer.norm2(hidden + layer.dropout2(feed))

        def prefill_with_cache(self, tokens, cache_capacity=None):
            """Processa o prompt uma vez e prepara KV para geração incremental.

            Retorna só os logits do último token, evitando materializar logits
            para cada posição do prompt longo. O cache é pré-alocado para não
            copiar toda a janela a cada token gerado.
            """
            if self.training or torch.is_grad_enabled():
                raise RuntimeError("prefill com KV cache exige eval e inference_mode")
            batch, length = tokens.shape
            if length > config["context_length"]:
                raise ValueError(
                    f"sequência acima do contexto configurado: {length} > {config['context_length']}"
                )
            capacity = int(cache_capacity or config["context_length"])
            if not length <= capacity <= int(config["context_length"]):
                raise ValueError("capacidade KV incompatível com o contexto")
            positions = torch.arange(length, device=tokens.device).unsqueeze(0)
            hidden = self.token_embedding(tokens) + self.position_embedding(positions)
            key_positions = torch.arange(length, device=tokens.device)
            chunk_size = max(1, int(config.get("attention_chunk_size", 512)))
            caches = []
            for layer in self.blocks.layers:
                residual = hidden
                query_input = layer.norm1(hidden) if layer.norm_first else hidden
                query, key, value = self._project_qkv(layer, query_input)
                cache_shape = (batch, layer.self_attn.num_heads, capacity, layer.self_attn.head_dim)
                cached_key = torch.empty(cache_shape, device=tokens.device, dtype=key.dtype)
                cached_value = torch.empty(cache_shape, device=tokens.device, dtype=value.dtype)
                cached_key[:, :, :length].copy_(key)
                cached_value[:, :, :length].copy_(value)
                caches.append((cached_key, cached_value))
                outputs = []
                for start in range(0, length, chunk_size):
                    end = min(start + chunk_size, length)
                    query_positions = torch.arange(start, end, device=tokens.device)
                    allowed = key_positions.unsqueeze(0) <= query_positions.unsqueeze(1)
                    attended = nn.functional.scaled_dot_product_attention(
                        query[:, :, start:end], key, value,
                        attn_mask=allowed.unsqueeze(0).unsqueeze(0),
                        dropout_p=0.0,
                    )
                    outputs.append(self._attention_output(layer, attended))
                hidden = self._finish_layer(layer, hidden, residual, torch.cat(outputs, dim=1))
            if self.blocks.norm is not None:
                hidden = self.blocks.norm(hidden)
            if hasattr(self, 'copy_query'):
                normalized = self.norm(hidden)
                logits, state = self._mix_copy_logits(tokens, normalized, self.lm_head(normalized), True)
                memory_keys = torch.empty(batch, capacity, self.copy_key.out_features,
                                          device=tokens.device, dtype=normalized.dtype)
                memory_ids = torch.empty(batch, capacity, device=tokens.device, dtype=tokens.dtype)
                memory_keys[:, :length].copy_(self.copy_key(normalized))
                memory_ids[:, :length].copy_(tokens)
                caches.append({'copy_keys': memory_keys, 'copy_ids': memory_ids,
                               'source_end': state['source_end'][:, -1],
                               'attention': state['attention'][:, -1], 'mixture': state['mixture'][:, -1]})
                return logits[:, -1], caches, length
            logits = self.lm_head(self.norm(hidden[:, -1]))
            return logits, caches, length

        def forward_next_with_cache(self, token, position, caches, cache_length):
            """Avança um token causalmente sem recalcular as chaves anteriores."""
            if self.training or torch.is_grad_enabled():
                raise RuntimeError("decodificação KV exige eval e inference_mode")
            position = int(position)
            cache_length = int(cache_length)
            if position != cache_length or position >= int(config["context_length"]):
                raise ValueError("posição fora da capacidade do KV cache")
            if hasattr(self, 'copy_query') and (len(caches) != len(self.blocks.layers) + 1
                    or not isinstance(caches[-1], dict) or 'copy_keys' not in caches[-1]):
                raise ValueError('KV cache de cópia exige a memória de origem.')
            hidden = self.token_embedding(token.reshape(-1, 1)) + self.position_embedding(
                torch.tensor([[position]], device=token.device),
            )
            updated_caches = []
            for layer, (cached_key, cached_value) in zip(self.blocks.layers, caches):
                residual = hidden
                query_input = layer.norm1(hidden) if layer.norm_first else hidden
                query, key, value = self._project_qkv(layer, query_input)
                cached_key[:, :, cache_length:cache_length + 1].copy_(key)
                cached_value[:, :, cache_length:cache_length + 1].copy_(value)
                attended = nn.functional.scaled_dot_product_attention(
                    query,
                    cached_key[:, :, :cache_length + 1],
                    cached_value[:, :, :cache_length + 1],
                    dropout_p=0.0,
                )
                hidden = self._finish_layer(layer, hidden, residual, self._attention_output(layer, attended))
                updated_caches.append((cached_key, cached_value))
            if self.blocks.norm is not None:
                hidden = self.blocks.norm(hidden)
            if hasattr(self, 'copy_query'):
                memory = caches[-1]
                if position >= memory['copy_ids'].shape[1]:
                    raise ValueError('posição fora da capacidade da memória de cópia')
                normalized = self.norm(hidden[:, -1])
                memory['copy_keys'][:, position].copy_(self.copy_key(normalized))
                memory['copy_ids'][:, position].copy_(token.reshape(-1))
                length = position + 1
                previous_end = memory['source_end']
                source_end = previous_end.clone()
                size = len(self.copy_boundary)
                if length >= size:
                    completed = (memory['copy_ids'][:, length - size:length] == self.copy_boundary).all(-1)
                    source_end = torch.where(completed, length - size, source_end)
                allowed = torch.arange(length, device=token.device)[None, :] < source_end[:, None]
                scores = (self.copy_query(normalized).unsqueeze(1) @
                          memory['copy_keys'][:, :length].transpose(1, 2)).squeeze(1) / self.copy_key.out_features ** .5
                attention = scores.masked_fill(~allowed, -1e4).softmax(-1) * allowed
                mixture = self.copy_gate(normalized).sigmoid() * allowed.any(-1, keepdim=True)
                if hasattr(self, 'copy_continue'):
                    previous = nn.functional.pad(memory['attention'], (1, 0)) * allowed
                    weight = (self.copy_continue(normalized).sigmoid() * memory['mixture'][:, None]
                              * (source_end == previous_end)[:, None])
                    current = (1 - weight) * attention + weight * previous
                    mass = current.sum(-1, keepdim=True)
                    attention = torch.where(mass > 1e-8, current / mass.clamp_min(1e-8), attention)
                logits = self.lm_head(normalized)
                copied = torch.zeros_like(logits).scatter_add(-1, memory['copy_ids'][:, :length], attention)
                probabilities = (1 - mixture) * logits.softmax(-1) + mixture * copied
                memory['source_end'] = source_end
                memory['attention'] = attention
                memory['mixture'] = mixture.squeeze(-1)
                updated_caches.append(memory)
                return probabilities.clamp_min(1e-9).log(), updated_caches
            return self.lm_head(self.norm(hidden[:, -1])), updated_caches

    return CausalTransformerLM()
