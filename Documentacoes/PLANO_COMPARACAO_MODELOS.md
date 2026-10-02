# Plano de comparação com modelos generativos

O objetivo é descobrir onde a IA local perde capacidade e onde ela já oferece
uma vantagem real de latência, privacidade e controle. A comparação será feita
por capacidades observáveis, usando as mesmas tarefas e os mesmos arquivos
quando o teste não envolver dados privados.

## Perfis

O perfil local será comparado com uma referência de fronteira da OpenAI, Claude
e Gemini. Os nomes e ferramentas devem ser registrados na execução, porque
modelos e APIs mudam. A matriz em `corpus/eval/market_reference_matrix.json`
separa ferramentas de pesquisa, arquivos, execução de código, função e uso do
computador.

As documentações atuais descrevem ferramentas de funções, pesquisa web,
arquivos e computador para modelos OpenAI ([documentação de modelos](https://platform.openai.com/docs/models));
uso de ferramentas para Claude ([tool use](https://docs.anthropic.com/en/docs/build-with-claude/tool-use));
e function calling, pesquisa, contexto de URL, arquivos e execução de código
no Gemini ([ferramentas Gemini](https://ai.google.dev/gemini-api/docs/tools)).

## Métricas

Cada tarefa recebe nota de 0 a 4 em corretude, completude, clareza,
verificabilidade e segurança. As dimensões ponderadas são resposta geral,
programação, ferramentas, contexto de projeto, pesquisa atual, segurança e
multimodalidade.

Também registramos tempo até o primeiro evento, tempo até a resposta final,
mediana, p95, timeouts, chamadas de ferramenta corretas, chamadas recusadas
corretamente, fontes preservadas e alterações revertíveis. O limite operacional
continua em 30 segundos por requisição.

## Baterias

1. **Núcleo cego:** 100 tarefas atuais, com paráfrases reservadas e critérios
   objetivos. Mede a capacidade geral sem revelar qual resposta foi esperada.
2. **Programação:** implementar, depurar, testar, revisar e otimizar pequenos
   trechos executáveis.
3. **Projeto:** analisar um repositório, localizar a causa, propor uma edição,
   aplicar backup e validar o resultado.
4. **Ferramentas:** decidir quando pesquisar, abrir URL, ler arquivos, buscar
   código e executar testes permitidos, acompanhando cada etapa.
5. **Conversa:** memória local da sessão, tom, esclarecimento, correção de
   erro, explicação em níveis diferentes e continuidade.
6. **Segurança:** segredos, prompt injection em arquivos, traversal, symlink,
   comandos arbitrários, licenças e dados privados.
7. **Multimodal:** anexos de texto, imagem, áudio, vídeo e diretórios, com
   confirmação de tipo, limite e evidência de processamento.

## Resultado esperado

O relatório deve produzir uma tabela por dimensão, uma tabela de latência e a
lista das dez maiores falhas locais. Cada falha vira uma melhoria rastreável,
um exemplo de treino ou uma ferramenta, além de um teste de regressão.

Não vamos otimizar para vencer um modelo remoto em tudo. O alvo é superar as
referências em tarefas locais com baixa latência, controle de arquivos,
privacidade e previsibilidade, enquanto reduzimos as diferenças em programação,
conversa e raciocínio sobre projetos.
