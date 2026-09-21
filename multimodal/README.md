# Packs de multimodalidade

Multimodalidade entra como packs independentes. Cada pack declara formatos,
capacidades, backend, limites e estado. O núcleo conversa com o pack por
contratos locais, mantendo o modelo conversacional separado da leitura de
arquivos e dos decodificadores.

## Estado atual

- **Documentos:** ativo para texto, Markdown, JSON e extração de PDF com
  `pdftotext` quando instalado;
- **Visão:** adaptador registrado; inspeção de arquivo já pode ser feita, OCR e
  entendimento semântico ainda aguardam backend visual;
- **Áudio:** adaptador registrado; transcrição aguarda backend local;
- **Vídeo:** adaptador registrado; extração semântica aguarda backend local;
- **Geração:** planejada, separada da entrada multimodal.

Nenhum modelo pesado é baixado automaticamente. Cada pack deve respeitar o
limite absoluto de 30 segundos e trabalhar dentro do workspace autorizado.
