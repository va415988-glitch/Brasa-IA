# Extensão VS Code

Primeira integração local da IA Local do Zero.

## Uso

1. Inicie o projeto com `./start.sh`.
2. Abra `integrations/vscode` como uma extensão no VS Code.
3. Pressione `F5` para abrir uma janela de desenvolvimento.
4. Abra um workspace e use a paleta de comandos:
   - `IA Local: explicar seleção`;
   - `IA Local: revisar arquivo`;
   - `IA Local: propor alteração`;
   - `IA Local: abrir chat`.

A extensão sincroniza o diretório do primeiro workspace aberto com o runtime e
envia somente a seleção ou até 24 mil caracteres do arquivo para `127.0.0.1`.
Alterações passam por proposta estruturada, comparação lado a lado, confirmação
e `edit_file`, que cria backup e valida o caminho. A extensão não aplica
alterações sem confirmação e mostra a resposta no
Output Channel `IA Local do Zero`.

## Próxima etapa

Adicionar diagnóstico com intervalo, patch em formato diff, prévia e aplicação
confirmada, além de cancelar a requisição ao fechar o editor.
