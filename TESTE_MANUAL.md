# Teste manual da interface

## Iniciar

No diretório do projeto:

```bash
cargo run --manifest-path runtime/Cargo.toml -- --web
```

Abra `http://127.0.0.1:3000`.

## Fluxo principal

1. Clique em **Nova conversa** e confirme que a área de mensagens é limpa.
2. Digite `/pesquisar Rust async runtime` e pressione Enter.
3. Confirme que aparecem mensagens em balão, pelo menos cinco cartões de resultado e um tempo em milissegundos.
4. Clique em **Analisar** em um cartão. Confirme que a fonte recebe o conteúdo da página e continua com o mesmo `source_id`.
5. Clique em **Fontes desta sessão**. Confirme que a fonte analisada aparece com snippet e texto.
6. Digite `/abrir https://www.rust-lang.org/`. Confirme que uma nova fonte é criada e o título da página aparece.
7. Redimensione a janela para uma largura pequena. Confirme que a barra lateral some e o chat continua utilizável.
8. Teste uma consulta vazia e uma URL inválida. A interface deve mostrar um erro compreensível sem travar.
9. Digite `Olá, tudo bem?`. A resposta deve indicar se veio da memória curada ou do modelo experimental.
10. Digite `/arquivos` e confirme que a raiz do workspace aparece.
11. Digite `/ler README.md` e confirme que o conteúdo aparece sem sair do workspace.
12. Digite `/buscar MAX_REQUEST_MS` e confirme que aparecem caminhos e linhas correspondentes.
13. Tente `/ler ../etc/hosts`. A operação deve ser recusada por estar fora do workspace.

## Critérios de desempenho

- consulta de pesquisa: idealmente abaixo de 10 segundos;
- abertura de página: idealmente abaixo de 10 segundos;
- qualquer requisição: abaixo de 10 segundos;
- nenhuma requisição pode ultrapassar 10 segundos;
- a interface deve continuar respondendo enquanto uma ferramenta trabalha.

## Resultado esperado

O teste será considerado aprovado quando o fluxo completo `pesquisa -> analisar fonte -> listar fontes` funcionar sem reiniciar o processo e sem perder os identificadores das fontes.

O fluxo conversacional será considerado experimental até que o modelo responda de forma coerente a perguntas fora do corpus curado. Respostas marcadas como `compact-experimental` devem ser tratadas como dados de avaliação, não como qualidade final.
