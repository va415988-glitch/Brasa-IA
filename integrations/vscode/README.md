# Extensão VS Code

Integração local do assistente **Brasa** (projeto IA Local do Zero).

## Estado atual

Esta integração está em **alpha de teste manual**. O runtime e o contrato de
ferramentas já podem ser exercitados localmente, mas a extensão ainda não é
uma extensão publicada nem uma validação end-to-end do agente. O checkpoint
experimental pode responder corretamente a exemplos curados e ainda falhar
em uma revisão de código inédita; o smoke test registra essa diferença.

Uma VSIX local da versão `0.1.1` foi preparada para o VS Code como
`ia-local-do-zero.ia-local-do-zero`. A janela que já estava aberta precisa ser
recarregada para que o Extension Host carregue essa instalação. A extensão
também contribui uma view **Brasa** na barra lateral secundária, ao lado das
views do Codex e do Claude Code.

## Uso

1. Inicie o projeto com `./start.sh`.
2. Abra `integrations/vscode` como uma extensão no VS Code.
3. Pressione `F5` para abrir uma janela de desenvolvimento.
4. Abra um workspace e use a paleta de comandos:
   - `Brasa: explicar seleção`;
   - `Brasa: revisar arquivo`;
   - `Brasa: propor alteração`;
   - `Brasa: abrir chat`.

Na view **Brasa**, toda mensagem sem anexo entra em
`/api/v1/agent/pursue`; o AgentCore classifica conversa, análise e ação, mantém
o histórico operacional e pede confirmação antes de escritas. No Chat padrão
do VS Code, use `@brasa`; ele oferece `/explain`,
`/review`, `/test` e `/workspace`. O modelo `ia-local-zero` continua disponível
no seletor de modelos. Essas entradas usam o mesmo AgentCore por meio do
runtime em `127.0.0.1:3000`; a extensão não mantém outro loop de ferramentas.

A integração atualmente não registra a API experimental de sessões nativas
do VS Code. A tela vazia ao enviar pela barra lateral foi reproduzida como
erro de sintaxe no JavaScript gerado pelo HTML, corrigido na versão `0.1.1`.
Esse erro não comprova uma falha da API de sessões nativas.

A integração usa também a superfície nativa de Chat do VS Code: as respostas
podem mostrar referências ao arquivo ativo, botões para revisar ou propor uma
alteração e perguntas de continuação para a próxima etapa. Anexos `#arquivo`
recebidos pelo participante são lidos dentro do workspace e incluídos no
contexto enviado ao runtime, dentro de um limite local. A extensão ainda
registra a ferramenta somente leitura `#ia-local-workspace`, para que agentes
do VS Code possam pedir uma inspeção estrutural do projeto e receber
manifestos, entradas, testes e verificações sem alterar arquivos.

Para atualizar a janela atual, execute `Developer: Reload Window` (ou feche e
reabra o VS Code). Se a barra lateral secundária estiver visível, selecione
**Brasa**. No Chat padrão, envie a mensagem para `@brasa`; se preferir,
procure `Brasa` no seletor de modelos. A integração não pede chave
ou cadastro; o runtime precisa estar ativo no computador.

A extensão sincroniza o diretório do primeiro workspace aberto com o runtime e
envia somente a seleção ou até 24 mil caracteres do arquivo para `127.0.0.1`.
Alterações passam por proposta estruturada, comparação lado a lado, confirmação
e `edit_file`, que cria backup e valida o caminho. A extensão não aplica
alterações sem confirmação e mostra a resposta no
Output Channel `Brasa`.

Com o runtime em execução, valide a integração sem abrir o host de extensão:

```bash
npm test
npm run test:webview
npm run smoke -- /caminho/absoluto/do/projeto
```

O smoke test verifica saúde da API, seleção do workspace, leitura de um
arquivo da extensão e uma resposta curada de programação. A revisão de código
é reportada como sinal de qualidade; uma resposta genérica não é considerada
aprovação semântica.

## Limites conhecidos

Esta é uma integração local, ainda sem publicação no Marketplace. O contrato
de provider expõe a janela efetiva atual do checkpoint (256 tokens de entrada),
por isso o modelo pode ser pouco útil em arquivos grandes até que o checkpoint
seja promovido. O smoke test e o teste de ativação cobrem o transporte e o
registro. O participante `@brasa`, o modelo e a view lateral são as
superfícies mantidas da integração. `npm test` também compila o JavaScript
gerado para a webview. `npm run test:webview` exercita envio e aprovações em
Chrome headless, com a ponte do VS Code simulada; aceita `CHROME_BIN` para
selecionar o executável. Esse teste não exige que o runtime esteja ativo.

A ferramenta `#ia-local-workspace` depende do suporte a `vscode.lm.registerTool`
do host. Em versões antigas ela é omitida automaticamente; o participante,
o modelo e a view lateral continuam funcionando.
