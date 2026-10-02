# Conjunto experimental de reparos

Este conjunto foi extraído dos 30 shards locais de `UltraData-Code-Agent` pelo comando:

```bash
.venv/bin/python scripts/prepare_verified_repair_sft.py \
  --max-shards 30 --max-per-group 20 \
  --output-dir datasets/implementation_repair_sft_v2
```

O [manifesto](manifest.json) registra hashes, contagens e exclusões. Dos 69.895 registros, 2.058 tinham um único patch de atualização, um trecho editável dentro de 2.048 tokens e saída posterior de pelo menos um teste aprovado. O limite de 20 por grupo deixou 753 exemplos para treino, 72 para validação e 85 reservados. O grupo é inferido do caminho do arquivo; as partições não compartilham esses grupos inferidos.

Cada entrada mostra o pedido resumido e o trecho anterior reconstruído do patch. A resposta é uma operação `edit_file` aceita pelo parser da Brasa. O conjunto serve para experimentar reparos pequenos, **não** para alegar capacidade de criar projetos completos. A saída de teste vem da trajetória original e não foi reproduzida no repositório correspondente; o avaliador mede estrutura e igualdade com o patch de referência, não execução funcional do projeto original.

As trajetórias derivam da cópia local de `UltraData-SFT-Agent-2609`, cuja [licença local](../UltraData-SFT-Agent-2609/LICENSE) acompanha o corpus. O checkpoint principal da Brasa não é alterado por este preparador.
