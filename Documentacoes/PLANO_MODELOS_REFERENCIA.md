# Modelos de referência

## Objetivo

Usar assistentes fortes como referência de qualidade para descobrir o que falta
na IA Local do Zero. O objetivo é reproduzir capacidades observáveis no nosso
runtime, não copiar pesos, identidade, prompts privados ou respostas sem
revisão.

## O que medir

Cada referência será comparada com a nossa IA em tarefas reservadas:

- entender a intenção antes de agir;
- fazer perguntas quando faltarem dados;
- explicar programação com exemplos corretos;
- corrigir código e criar testes;
- analisar um projeto sem fingir que leu arquivos;
- escolher a ferramenta certa;
- usar argumentos válidos;
- verificar o resultado da ferramenta antes de responder;
- preservar contexto entre mensagens;
- distinguir fatos estáveis de informação atual;
- admitir incerteza sem abandonar a tarefa;
- manter tom natural, direto e adaptado ao usuário.

## Como transformar uma boa resposta em aprendizado

Uma amostra só entra no corpus depois de passar por quatro etapas:

1. registrar a pergunta, contexto e objetivo;
2. comparar respostas e identificar o comportamento que foi bom;
3. reescrever uma resposta própria, verificável e compatível com nossas
   ferramentas;
4. adicionar uma pergunta de avaliação que possa reprovar uma resposta ruim.

O corpus deve armazenar a capacidade ensinada e a evidência de validação. Uma
resposta bonita sem teste não é dado de treinamento confiável.

## Bateria de comparação

As tarefas serão divididas em cinco grupos:

- conhecimento: responder, citar e admitir falta de evidência;
- programação: explicar, implementar, depurar e testar;
- ferramentas: decidir, chamar, acompanhar e conferir;
- projetos: ler estrutura, propor mudança, editar com backup e testar;
- conversa: contexto, tom, concisão e continuação.

Cada tarefa terá critérios objetivos, como código executável, conceitos
obrigatórios, ausência de fonte inventada e resultado esperado da ferramenta.

## Destilação segura

Podemos usar respostas de modelos de referência como inspiração ou dados de
comparação quando tivermos autorização para isso. Não devemos enviar
automaticamente documentos privados, histórico ou código do usuário para um
serviço externo. Também não devemos transformar respostas de terceiros em um
corpus massivo sem verificar os termos de uso e a licença aplicável.

O treinamento próprio deve priorizar:

- exemplos escritos e revisados por nós;
- documentação com licença clara;
- resultados de ferramentas reais;
- correções verificadas em projetos de teste;
- contrastes entre resposta correta e erro comum;
- dados sintéticos que tenham sido executados ou conferidos.

## Arquitetura resultante

O modelo de referência serve como professor e avaliador durante o
desenvolvimento. O produto continua independente: Rust controla ferramentas,
permissões, contexto e tempo; o acervo local fornece conhecimento; componentes
pequenos aprendem roteamento e formato; e um eventual modelo conversacional
local só é promovido quando superar os testes reservados.

## Primeiro baseline

A bateria inicial de 100 tarefas foi criada em
`corpus/eval/reference_tasks.jsonl` e executada pelo
`tests/reference_tasks.py`. O baseline inicial foi 12/100, ou 12% de cobertura
pelos critérios objetivos. Depois da primeira camada procedural
(`python/data/behavior_phase1.jsonl`), o resultado subiu para 24/100. Essa
medição deve ser tratada como in-sample, porque parte das respostas foi escrita
a partir das falhas identificadas; ela comprova o ganho da camada, mas não
substitui um conjunto de paráfrases reservado. Por categoria no baseline:

- programação: 4/20;
- ferramentas: 1/20;
- projetos: 0/20;
- conversa: 5/20;
- segurança: 2/20.

Primeira camada procedural:

- programação: 13/20;
- ferramentas: 1/20;
- projetos: 3/20;
- conversa: 5/20;
- segurança: 2/20.

Esse número é baixo de propósito: a bateria mede tarefas novas, não apenas as
perguntas usadas para escrever as respostas curadas. O relatório completo fica
em `corpus/eval/reference_report.json` e será comparado depois de cada lote de
melhorias.

## Próximo marco

Construir um conjunto reservado de paráfrases, depois atacar as dez maiores
falhas restantes em ferramentas, projetos, conversa e segurança. Cada exemplo
novo deve ser acompanhado por uma verificação ou teste para evitar apenas
decorar perguntas do benchmark.
