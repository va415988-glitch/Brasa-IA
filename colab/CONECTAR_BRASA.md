# Usar a GPU do Colab no Brasa

> Fluxo anterior, desativado no uso diário. O Brasa agora inicia o gerador Ollama local com `./start.sh`. Consulte `planning/BRASA_LOCAL.md`. Estas instruções ficam apenas como histórico do experimento; importar credenciais não ativa o Colab no agente atual.

## Ativar

1. Envie a versão atualizada de `professor_qwen3_coder_v001.ipynb` ao Colab. Uma cópia antiga no Drive não recebe as alterações locais automaticamente.
2. Selecione uma GPU compatível com o carregamento do Qwen e execute as três primeiras células de código: preparação, dependências e carregamento. Se o modelo já está carregado nesta sessão, pode ir direto à seção nova.
3. Execute as duas células de **Conectar o Brasa ao Qwen desta sessão**. A primeira inicia a inferência autenticada; a segunda abre o túnel e baixa `brasa-colab.env`.
4. Salve o arquivo baixado em `colab/brasa-colab.env`, no computador do Brasa, e execute na raiz do projeto:

   ```bash
   python3 scripts/configure_colab.py colab/brasa-colab.env
   ```

5. Reinicie o Brasa com `./start.sh`. O importador preserva as demais configurações do `.env`. Variáveis já exportadas no terminal têm prioridade sobre esse arquivo.

O importador consulta `/health` com autenticação antes de salvar. Não imprime a chave. O arquivo de conexão é ignorado pelo Git. Não compartilhe esse arquivo: ele permite usar a GPU enquanto o servidor estiver ativo.

## Funcionamento

O AgentCore usa o Qwen para escolher ferramentas e gerar alterações. Envia histórico, catálogo de ferramentas e resultados das leituras selecionadas pelo agente. Esse contexto sai do computador e passa pelo túnel Cloudflare até o Colab. O servidor não executa código do projeto: ferramentas, validação de caminhos, aprovações e verificações continuam locais.

Cada geração usa um job consultado periodicamente, evitando manter uma conexão HTTP longa. Uma geração por vez; contexto máximo de 24.576 tokens e resposta de até 4.096 tokens. Erros de autenticação, GPU ocupada, indisponibilidade e JSON inválido interrompem a proposta. Não há troca silenciosa para o checkpoint local.

`http://127.0.0.1:3200/health` informa `inference: "colab"` quando configurado. Isso indica a seleção do gerador, não confirma disponibilidade permanente da GPU. A chave não aparece nesse endpoint. O streaming mostra o resultado ao terminar cada geração, sem transmitir tokens individualmente.

## Encerrar ou reconectar

Execute a última célula do notebook para desligar o servidor e o túnel. Para voltar ao gerador local:

```bash
python3 scripts/configure_colab.py --disable
./start.sh
```

Uma sessão nova exige importar o novo arquivo. Mantenha o notebook ativo durante o uso interativo. Colab não é hospedagem permanente; recursos e duração variam conforme a sessão ([FAQ do Colab](https://research.google.com/colaboratory/intl/en-GB/faq.html)). O endereço temporário segue o mecanismo de [Quick Tunnels da Cloudflare](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/do-more-with-tunnels/trycloudflare/).

## Estado da implementação

Adaptador, servidor, notebook e configuração local implementados. A checagem de tipos TypeScript passou. A conexão real e a geração na GPU dependem de executar o notebook e importar suas credenciais; não foram demonstradas nesta alteração. Não houve treinamento do modelo.
