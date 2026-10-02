# Auditoria do ecossistema da IA

## Estado verificado

- O AgentCore TypeScript possui máquina de estados, análise de requisitos,
  memória, catálogo de capacidades, executor de planos, aprovações, timeouts,
  eventos e critérios de aceite.
- O catálogo local expõe 34 ferramentas e 8 APIs de serviço. Há cobertura para
  workspace, pesquisa, documentos, mídia, processos, verificações e alterações.
- Os 13 testes do `agent-core` passaram.
- A bateria Python focada nas rotas alteradas passou com 59 testes.
- O gate full-stack rejeita propostas que não contenham cliente, servidor,
  contrato, persistência, testes e estados de falha.

## Limitações atuais

### Duas rotas ainda existem

Mensagens sem anexos entram no `/api/v1/agent/pursue` e usam o AgentCore.
Mensagens com anexos recebidas pelo `/api/chat` agora são encaminhadas ao
`/api/v1/agent/pursue` com o contrato de anexos normalizado. `/api/chat` ainda
existe para compatibilidade e para mensagens sem anexos, mas não toma mais a
decisão cognitiva de tarefas multimodais.

### O núcleo coordena, mas não cria competência sozinho

O TypeScript controla estado, contratos e execução. A qualidade do plano ainda
depende do planejador Python, do checkpoint e dos dados de treino. O cérebro
operacional é uma boa fronteira de controle, mas não substitui conhecimento de
arquitetura, frameworks ou depuração.

### Há duplicação de contratos

Python lê `contracts/*.json`; TypeScript mantém um registro de capacidades em
`agent-core/src/capability-registry.ts`. O teste de paridade reduz o risco, mas
as duas fontes ainda podem divergir durante uma evolução.

### A cobertura de APIs é local e deliberadamente limitada

Existem APIs de pesquisa, aprendizado, conhecimento e diálogo. Não há ainda
adaptadores de produção para autenticação, bancos de dados, deploy, filas,
observabilidade externa ou browser end-to-end. O agente pode projetar essas
integrações, mas não consegue validá-las sem contratos e conectores próprios.

### A validação full-stack ainda é estrutural

O gate verifica se uma proposta declara as camadas necessárias. A confirmação
de funcionamento depende dos checks reais do projeto, de integração e, quando
necessário, de um teste de navegador ou serviço externo.

## Prioridade para formar um ecossistema único

1. Fazer os clientes novos usarem diretamente `/api/v1/agent/pursue`; manter
   `/api/chat` somente como adaptador compatível.
2. Gerar o registro TypeScript diretamente dos contratos JSON, eliminando a
   duplicação manual de capacidades.
3. Adicionar portas tipadas para banco, autenticação, browser e deploy, cada uma
   com simulador local e check verificável.
4. Persistir memória operacional e eventos em um armazenamento comum entre
   Rust, TypeScript e Python.
5. Tornar o gate de entrega obrigatório para planos, checks, integração e
   recuperação, sem declarar competência com base só em texto.

## Resultado da auditoria

A IA já possui um esqueleto de cérebro operacional e consegue conduzir projetos
locais pequenos com contratos, aprovação e verificação. Ainda não é correto
chamá-la de dev full-stack autônomo completo: faltam unificação da rota de
anexos, conectores de produção e validação end-to-end de sistemas arbitrários.
