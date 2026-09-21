# Plano de expansão do conhecimento

## Agora

1. Manter o comportamento, o uso de ferramentas e as regras de segurança no
   corpus de treino.
2. Colocar conhecimento estável e material autorizado no acervo local.
3. Indexar por trechos, recuperar evidência e medir latência em cada consulta.
4. Reservar perguntas novas em `corpus/eval/` para evitar vazamento entre treino
   e avaliação.

## Próximas coleções

- programação: documentação oficial, exemplos próprios e Stack Exchange com
  atribuição registrada;
- conhecimento geral: seleção da Wikimedia em português e outros idiomas;
- mundo e cultura: obras em domínio público e material próprio;
- atualidades: páginas consultadas sob demanda, sempre com data e URL;
- tarefas do usuário: workspace autorizado, com exclusão e atualização simples.

## Ordem técnica

1. importar arquivos locais e pequenos lotes aprovados;
2. adicionar detecção de idioma, qualidade e boilerplate;
3. adicionar armazenamento por documento e atualização incremental;
4. conectar recuperação ao contexto do assistente com citações;
5. treinar o modelo com amostras balanceadas de linguagem, programação,
   raciocínio, diálogo e chamadas de ferramenta;
6. avaliar factualidade, código, segurança, ferramentas e o limite rígido de
   30 segundos.

O objetivo não é colocar literalmente tudo nos pesos. Pesos ensinam capacidade e
procedimento; o acervo mantém fatos extensos, atuais e auditáveis. Essa divisão
é o que permite crescer sem aumentar cada resposta até ultrapassar o limite de
desempenho.
