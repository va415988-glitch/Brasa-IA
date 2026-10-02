import assert from "node:assert/strict";
import test from "node:test";
import {researchQueryFromPrompt} from "../src/research-query.ts";

test("consulta conserva nomes qualificados de APIs", () => {
  assert.match(researchQueryFromPrompt("Pesquise a documentação oficial do Python como funciona asyncio.TaskGroup."), /asyncio\.TaskGroup/);
});

test("consulta de pesquisa conserva o assunto e remove instruções de resposta", () => {
  const query = researchQueryFromPrompt(
    "Pesquise a documentação oficial atual do CMake sobre FetchContent. Como posso fixar versões para tornar as dependências reproduzíveis? Traga os links consultados e separe o que a documentação confirma do que é sua recomendação. Não altere arquivos.",
  );

  assert.equal(
    query,
    "site:cmake.org FetchContent FetchContent_Declare GIT_TAG commit hash URL_HASH",
  );
  assert.doesNotMatch(query, /traga os links|separe|não altere arquivos/i);
});

test("consulta sem exigência de documentação oficial não recebe domínio presumido", () => {
  assert.equal(
    researchQueryFromPrompt("Pesquise opções de cache para uma API. Cite duas fontes e não altere arquivos."),
    "opções cache API",
  );
});
