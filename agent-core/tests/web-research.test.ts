import assert from "node:assert/strict";
import test from "node:test";
import {
  DuckDuckGoSearchProvider,
  WebResearchService,
  htmlToText,
} from "../src/index.ts";
import type {WebFetcher, WebResponse} from "../src/index.ts";

function page(status: number, body: string): WebResponse {
  return {status, text: async () => body};
}

test("faz busca, abre páginas e transforma HTML em evidência limpa", async () => {
  const requested: string[] = [];
  const fetcher: WebFetcher = async (url) => {
    requested.push(url);
    if (url.includes("duckduckgo.com/html")) {
      return page(200, '<a class="result__a" href="https://example.test/one">Fonte um</a><a class="result__a" href="https://example.test/one">Duplicada</a><a class="result__a" href="https://example.test/two">Fonte dois</a>');
    }
    if (url.endsWith("/one")) {
      return page(200, "<html><head><title>Documento um</title><script>alert(1)</script></head><body><nav>Menu</nav><p>Conteúdo útil.</p></body></html>");
    }
    return page(200, "<html><body><article>Segundo conteúdo.</article><footer>Rodapé</footer></body></html>");
  };
  const provider = new DuckDuckGoSearchProvider(fetcher);
  const service = new WebResearchService(provider, fetcher, {maxResults: 3});
  const evidence = await service.research("arquitetura TypeScript");

  assert.equal(evidence.length, 2);
  assert.equal(evidence[0]?.title, "Documento um");
  assert.equal(evidence[0]?.excerpt, "Conteúdo útil.");
  assert.equal(evidence[1]?.excerpt, "Segundo conteúdo.");
  assert.equal(requested.filter((url) => url.endsWith("/one")).length, 1);
});

test("remove scripts, navegação e excesso de espaços", () => {
  assert.equal(htmlToText("<script>ruído</script><p>Olá&nbsp;&amp; mundo</p><footer>fim</footer>"), "Olá & mundo");
});
