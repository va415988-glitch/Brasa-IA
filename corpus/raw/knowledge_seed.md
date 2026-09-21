# Caderno inicial de conhecimento

## Programação

Um programa é uma sequência de instruções que transforma entradas em saídas. Uma função agrupa uma operação reutilizável; bons nomes, entradas explícitas e efeitos previsíveis tornam o código mais fácil de testar. Uma variável associa um nome a um valor. Uma lista é mutável e ordenada; uma tupla é ordenada e imutável; um conjunto representa valores sem repetição; um dicionário associa chaves a valores.

Em Python, exceções representam situações que impedem a operação normal. O código deve capturar apenas erros que saiba tratar e deve preservar a mensagem original. Testes úteis cobrem casos normais, limites, entradas vazias, tipos inválidos e falhas esperadas. Complexidade O(n) cresce proporcionalmente ao tamanho da entrada; O(log n) cresce mais lentamente; medir o programa é melhor do que adivinhar o gargalo.

Em Rust, ownership define quem é responsável por cada valor. Um valor pode ser movido, emprestado de forma imutável várias vezes ou emprestado de forma mutável uma vez por vez. O compilador usa essas regras para evitar uso após liberação, referências inválidas e vários acessos mutáveis concorrentes.

HTTP é um protocolo de requisição e resposta. GET costuma ler, POST costuma criar ou executar uma operação, PUT substitui e DELETE remove. Códigos 2xx indicam sucesso, 4xx indicam problema na requisição ou autorização e 5xx indicam falha no servidor. Toda integração de rede precisa de timeout, validação de status, limite de tamanho e tratamento de conteúdo inválido.

## Sistemas e segurança

Um processo é uma instância de um programa em execução; uma thread é uma linha de execução dentro de um processo. Concorrência permite organizar tarefas que se sobrepõem; paralelismo executa trabalho ao mesmo tempo em múltiplos núcleos. A sincronização precisa proteger estado compartilhado sem criar deadlocks.

O princípio do menor privilégio concede a cada componente somente o acesso necessário. Entradas externas devem ser tratadas como não confiáveis. Caminhos de arquivo devem ser normalizados e confinados ao workspace permitido; comandos do sistema devem ter argumentos estruturados, timeout e registro do resultado.

## Matemática e ciência

Uma média resume valores somando-os e dividindo pela quantidade; a mediana é o valor central após ordenar; a mediana costuma ser mais resistente a valores extremos. Correlação indica associação entre variáveis e não prova causalidade. Uma hipótese científica deve poder ser testada e potencialmente refutada. Medidas precisam de unidade, método e incerteza.

## Conhecimento cotidiano

Para decidir entre alternativas, defina o objetivo, liste restrições, estime custos e riscos, faça um pequeno teste e registre o resultado. Em uma explicação, comece pela resposta, dê um exemplo concreto e mencione limites relevantes. Quando houver dúvida factual, declare a incerteza e busque fontes primárias ou múltiplas fontes independentes.

## Método da assistente

A assistente deve distinguir conhecimento estável de informação atual. Conceitos e explicações podem vir do modelo; preços, leis, horários, versões de software e notícias devem ser pesquisados. Ferramentas devem ser chamadas com argumentos válidos, respeitar permissões e tempo restante, e suas fontes devem ser citadas. Se não houver evidência suficiente, a resposta correta é admitir a lacuna.
