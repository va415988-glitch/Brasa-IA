# Ativação local e laboratório V4 — 01/10/2026

O runtime, o worker de inferência e o AgentCore foram iniciados e verificados
pela rede local real. A interface está em **http://127.0.0.1:3000**. O chat usa
o checkpoint existente de contexto 32768; a V4 fica disponível separadamente
no botão **Laboratório cognitivo · V4**. A V4 foi carregada em memória e gerou
duas propostas reais nessa verificação.

**Ativação não significa competência aprovada.** Os cinco componentes
numéricos da V4 continuam reprovados; programação, pesquisa, planejamento,
comunicação e matemática geral continuam não avaliados pelo protocolo. Não
houve novo treino, promoção de pesos nem criação de redes neurais independentes.
O checkpoint geral tem execução de 32768 tokens e treino de 512; a extensão de
contexto não comprova entendimento semântico em 32768 tokens.

## Uso

1. Abra a interface local e selecione **Laboratório cognitivo · V4**.
2. Forneça uma pergunta numérica sintética e o conteúdo observado, até 200
   caracteres em cada campo. Há exemplos de extração e comparação.
3. Clique em **Analisar com a V4**. Confira a resposta, a decisão estruturada,
   a saída bruta e as provas por núcleo. Cada análise é independente.

O laboratório tem contexto de 512 tokens, uma proposta por pedido e nenhum
executor de ferramentas. Uma consulta proposta é apenas exibida. As respostas
trazem `experimental=true`, `qualified=false`,
`tool_executed=false` e `execution_allowed=false`. Não há fallback para o
chat nem correção das respostas por um oráculo durante a inferência.

## Ajustes realizados

- Proxies de estado e decisão dos núcleos/laboratório no runtime, com limite
  de 64 KiB, prazos e `Cache-Control: no-store`.
- Prontidão exige pesos carregados e erro nulo; o iniciador verifica pesos,
  tokenizer e um forward finito antes de substituir serviços existentes.
- Corrigido o `BrokenPipeError` relatado na partida: `/health` consulta apenas
  a prontidão em memória, responde HTTP 503 quando indisponível e não revalida
  provas. O cache das provas é aquecido antes de abrir o servidor. Desconexões
  esperadas são tratadas sem traceback; outros erros continuam propagando.
  Na partida final, `/health` respondeu em **0,002 s**, sem traceback no log.
- Identificação de processos falha com segurança se `ss` ou `/proc` não puderem
  informar quem ocupa a porta.
- CLI e iniciador usam o mesmo resolvedor do checkpoint ativo.
- Identidade capturada antes/depois da carga e conferida antes/depois da geração:
  pesos, metadados, tokenizer, fontes, contratos, catálogo e origens do reparo
  de contexto. Uma alteração exige nova carga; um certificado atualizado no
  disco não aprova pesos antigos ainda na memória.
- Provas vinculadas aos caminhos exatos das bancadas e às dependências reais
  da geração. A aceitação exige entrada completa e aprovação do decoder.
- O laboratório reutiliza o registro já verificado pela prontidão. Na
  verificação final, sua primeira consulta de estado levou **0,022 s**.

## Recuperação do tokenizer do chat

O tokenizer em `model/godmode/godmode-tokenizer-v1.json` havia sido substituído
por outro BPE, incompatível com o hash registrado nos metadados e no manifesto
de treino. Foi reconstruído pelo procedimento original
`scripts/prepare_godmode_neural.py:expand_tokenizer(8192)`, preservando IDs e
merges da base e adicionando os IDs reservados.

A reconstrução produziu **exatamente** o hash esperado
`79b3f86851c97d39f8405e3f263eb463e447b542d73e817bc18193150c61550d`.
O arquivo só foi substituído após essa comparação. Os pesos, os metadados e
`model/godmode/state.json` permaneceram iguais. O tokenizer divergente e a
reconstrução estão preservados em `model/godmode/recovery-20261001/`, com
proveniência e hashes no manifesto de recuperação.

## Verificação e limites observados

O relatório final `model/cognitive-cores/v1/activation-health-fix-check.json` registra HTTP 200
para runtime, worker e AgentCore, modelo geral carregado, V4 carregada,
nenhum erro de vínculo da prova e as travas verificadas:

| Pedido | Resultado esperado e observado |
| --- | --- |
| Despacho de núcleo sem competência | HTTP 422, zero gerações |
| Pedido fora do escopo experimental | HTTP 422, zero gerações |
| Corpo acima de 64 KiB | HTTP 413 |
| Análise experimental | Uma geração, nenhuma execução, competência não aprovada |

Os dois exemplos ao vivo **erraram semanticamente**, mesmo com JSON válido:

| Fonte e pedido | Saída real | Resultado correto |
| --- | --- | --- |
| `Pipa: limite = 42.`; qual é o limite? | Bloqueou dizendo que o campo não foi informado | 42 |
| `Pipa: versão mínima = 42.`; a versão 41 atende? | Sim | Não |

Esses exemplos conhecidos servem para verificar disponibilidade e isolamento;
não constituem nova bancada reservada. O relatório preserva os textos reais.
Validação estrutural, referência existente e integridade dos pesos não garantem
uma resposta correta.

A prova publicada é `evidence-activation-03.json`. Seus textos congelados foram
reavaliados e vinculados à implementação final, sem nova inferência dos 552
casos e sem mudar os resultados de competência. As provas anteriores foram
preservadas; o índice aponta apenas para a prova atual, evitando ambiguidade.

Validação do software: 145 testes Python e 32 subtestes na bateria final de
health, laboratório e diálogo; 81 testes e 35 subtestes na bateria de
ativação/integridade; 56 testes Rust e nove testes funcionais da interface.
As regressões da cópia neural também passaram. As baterias se sobrepõem e
não devem ser somadas. Isso mede o software, não competência do modelo.

## Reprodução e controle

Na raiz do projeto:

```bash
./start.sh
.venv/bin/python scripts/check_model_activation.py --output model/cognitive-cores/v1/activation-health-fix-check.json
```

O processo desta ativação ficou em segundo plano. O iniciador revisado substitui
somente os serviços identificados desta cópia do projeto. O log final está em
`logs/activation-20261001-health-fix.log`. O estado auditado está em
`model/cognitive-cores/v1/activation-state.json`.

Para desabilitar o laboratório, defina `enabled` como `false` em
`config/experimental_model.json`; isso bloqueia novos pedidos sem substituir
o checkpoint do chat. Para reativar após alterar a configuração, restaure os
pins verificados e reinicie com `./start.sh`. Não restaure o tokenizer
divergente para uso com os pesos atuais.
