# Arquivos na barra de contexto

A barra do workspace agora permite criar arquivo (com conteúdo inicial opcional), criar pasta, excluir arquivos e pastas com confirmação e restaurar a última exclusão. A pasta aberta preenche o caminho de criação; o botão Pasta anterior navega um nível acima. Atualize a página para carregar os controles.

As exclusões movem o item inteiro para `.ia-local-backups/trash` no próprio projeto. A recuperação preserva conteúdo binário e subpastas, continua disponível após recarregar/reiniciar e recusa caminhos que já estejam ocupados. Não há exclusão permanente pela barra. Itens protegidos e links simbólicos são recusados.

As operações da interface usam `/api/v1/workspace/entries` com o contrato `workspace-entry/v1` e o projeto esperado. A troca de projeto impede alterações no destino errado. A criação não substitui arquivos existentes, e salvar compara o conteúdo completo com a versão aberta para recusar edições antigas ou parciais. Arquivos de texto seguem o limite existente de 128 KiB. Criar outro arquivo preserva mudanças pendentes no editor.

Essas operações manuais são separadas do catálogo de ferramentas do agente.

## Teste em uma nova conversa

Na raiz do workspace, use Novo arquivo e crie `teste_motor.py` com:

```python
def total_pedido(itens, desconto=0):
    return round(sum(preco * quantidade for preco, quantidade in itens) * (1 - desconto), 2)
```

Abra uma conversa nova e envie:

```text
Execute total_pedido([[19.9, 3], [8.5, 2]], desconto=0.1) em teste_motor.py
```

O resultado esperado é `69.03`, obtido pela leitura do arquivo e avaliação da função no motor local. O teste foi executado pela API e pela interface. Ele verifica integração com código e argumentos do workspace; o código da função foi fornecido pelo teste, portanto não avalia geração livre nem domínio geral de programação pelo modelo.

## Evidências

- Backend: 52 testes Rust, incluindo conflitos de restauração, arquivos vazios, pastas com conteúdo binário, troca de projeto, links e histórico com muitos registros já restaurados.
- Interface: 4 casos automatizados existentes de navegação, streaming e histórico passaram.
- API em projeto temporário: 15/15 casos em `avaliacoes/workspace-explorer-runtime-2026-09-30.json`; auditoria reproduzível com `python3 scripts/audit_workspace_entries.py --output /tmp/workspace-explorer-audit.json`.
- Verificação visual: criação de pasta, criação/edição de arquivo vazio, recusa de sobrescrita, cancelamento, exclusão e restauração de arquivo, recuperação de pasta após recarregar, preservação de edições pendentes ao criar outro arquivo e execução em conversa nova. Nenhum erro JavaScript observado. Captura em `avaliacoes/workspace-explorer-ui-2026-09-30.jpg`.
