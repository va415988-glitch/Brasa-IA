# Brasa com o modelo próprio

Uso diário: `./start.sh`.

O AgentCore e o diálogo usam o checkpoint próprio pelo worker local na porta 3101. A inicialização não instala, verifica nem chama Ollama; configurações antigas de Ollama e Colab são ignoradas. O checkpoint ativo vem de `model/godmode/state.json`; na ausência de um checkpoint ativo, o iniciador usa `model/checkpoints/compact-08-gate-focus.pt`.

O agente lê e altera o workspace pelas APIs locais do runtime. As propostas passam pela validação de caminhos e aprovação local. Leituras de arquivos centrais são enfileiradas pelo AgentCore a partir da inspeção; o modelo é chamado depois que a evidência selecionada estiver disponível. Isso evita uma geração do modelo para cada leitura segura.

Busca web é opcional e independente do modelo. O iniciador lê `IA_LOCAL_BRAVE_SEARCH_API_KEY` do ambiente ou de `.env`; sem a chave, permanece o provider de busca local já configurado.

```dotenv
IA_LOCAL_BRAVE_SEARCH_API_KEY=sua-chave
```

O objetivo é manter inferência, planejamento, ferramentas e APIs do agente sob controle do projeto. A chave Brave só habilita obtenção de páginas e trechos externos; a resposta e as decisões continuam no fluxo local.

O checkpoint é pequeno e experimental; disponibilidade local não implica qualidade equivalente a modelos grandes. Para tarefas repetitivas e verificáveis, o núcleo deve preferir regras, contratos e operações determinísticas, reservando a geração para interpretação e síntese.

Registros de execução histórica com Ollama não descrevem mais o caminho ativo do produto.
