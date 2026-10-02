# Plano de integração com editores

A integração deve começar depois de confirmar o comportamento atual com o teste
manual. O editor será um cliente local do runtime, não um segundo cérebro: o
Rust continua controlando permissões, workspace, ferramentas, backups e
timeouts.

## Primeira versão: extensão local

Alvos iniciais:

1. VS Code e editores compatíveis com a API de extensões;
2. depois, um servidor LSP ou protocolo semelhante para outros editores.

Funções da primeira versão:

- enviar seleção atual para explicar ou corrigir;
- revisar arquivo ou projeto aberto;
- buscar referências no workspace;
- gerar teste para a função selecionada;
- mostrar diff antes de aplicar;
- aplicar alteração somente após confirmação;
- executar verificação permitida e mostrar resultado;
- abrir a conversa correspondente no navegador local.

## Contrato local

O editor se conecta apenas a `127.0.0.1`. Cada requisição leva:

- workspace autorizado;
- arquivo e intervalo selecionados;
- conteúdo necessário, com limite de tamanho;
- objetivo da operação;
- identificador da conversa.

O runtime devolve texto, evidências, diagnóstico, patch opcional, eventos de
progresso e resultado de testes. A extensão nunca executa shell arbitrário nem
edita arquivo sem confirmação.

## Segurança e desempenho

- confirmar que o caminho pertence ao workspace;
- excluir segredos, `.env`, binários e arquivos ignorados;
- mostrar diff e backup antes de escrever;
- limitar cada operação a 10 segundos;
- cancelar requisição quando o usuário fechar a ação;
- manter o editor responsivo durante ferramentas;
- nunca enviar o projeto para serviço externo por padrão.

## Ordem de construção

1. endpoint local para seleção e diagnóstico;
2. painel de eventos no editor;
3. diff com aplicação e reversão;
4. geração e execução de testes permitidos;
5. busca semântica no workspace;
6. integração LSP para diagnósticos contínuos;
7. atalhos, comandos e configuração de preferências.

## Critério de prontidão

A integração estará pronta para uso diário quando explicar uma seleção,
encontrar referências, propor um patch, mostrar o diff, aplicar com confirmação
e validar a alteração sem ultrapassar 10 segundos nas operações locais típicas.
