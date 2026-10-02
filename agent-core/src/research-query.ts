const responseDirectives = /(?:^|[.;?!\n])\s*(?:traga|forne[cç]a|inclua|cite|liste|separe|distinga|não altere|nao altere|não modifique|nao modifique)\b[\s\S]*$/i;
const researchNoise = new Set([
  "a", "o", "as", "os", "um", "uma", "e", "em", "de", "do", "da", "dos", "das",
  "que", "como", "qual", "quais", "para", "por", "com", "sobre", "no", "na", "nos", "nas",
  "posso", "podemos", "devo", "devemos", "tornar", "atual", "atualizada", "atualizado",
  "oficial", "oficiais", "documentacao", "documentacoes", "fonte", "fontes", "link", "links",
  "cite", "traga", "separe", "distinga", "pesquise", "pesquisar", "busque", "buscar", "procure",
  "procurar", "investigue", "investigar", "the", "and", "with", "how", "can", "for", "from",
  "into", "latest", "official", "documentation", "docs", "sources", "source",
]);

const officialDocumentationSites: Array<{topic: RegExp; domain: string}> = [
  {topic: /\bcmake\b/i, domain: "cmake.org"},
  {topic: /\bpython\b/i, domain: "docs.python.org"},
  {topic: /\brust\b/i, domain: "doc.rust-lang.org"},
  {topic: /\btypescript\b/i, domain: "typescriptlang.org"},
  {topic: /\bnode(?:\.js)?\b/i, domain: "nodejs.org"},
];

export type SearchFreshness = "pd" | "pw" | "pm" | "py";

/** Traduz períodos explícitos para os filtros de atualidade aceitos pela Brave. */
export function searchFreshnessFromPrompt(prompt: string): SearchFreshness | undefined {
  if (/\b(?:hoje|agora|nas? últimas?\s+24\s+horas?|últimas?\s+24\s+horas?)\b/i.test(prompt)) return "pd";
  if (/\b(?:esta|nesta|nessa|na)\s+semana\b|\b(?:últimos?|últimas?)\s+(?:7|sete)\s+dias\b/i.test(prompt)) return "pw";
  if (/\b(?:este|neste|esse|nesse)\s+m[eê]s\b|\b(?:últimos?|últimas?)\s+30\s+dias\b/i.test(prompt)) return "pm";
  if (/\b(?:este|neste|esse|nesse)\s+ano\b|\b(?:últimos?|últimas?)\s+12\s+meses\b/i.test(prompt)) return "py";
  return undefined;
}

/** Remove instruções de formato do pedido e conserva os termos que identificam o assunto da busca. */
export function researchQueryFromPrompt(prompt: string): string {
  const asksForOfficialSources = /\b(?:documenta[cç][aã]o oficial|fontes oficiais|site oficial)\b/i.test(prompt);
  let query = prompt.trim()
    .replace(/^(?:por favor[, ]*)?(?:pesquise|pesquisar|busque|buscar|procure|procurar|investigue|investigar)\s+/i, "")
    .replace(responseDirectives, "")
    .replace(/\b(?:não|nao)\s+(?:altere|modifique|edite)\s+(?:nenhum\s+)?arquivos?\b[\s\S]*$/i, "")
    .replace(/\bcomo\s+(?:posso|podemos|devo|devemos|eu posso|eu devo)\b/gi, "")
    .replace(/[?!,;:]+|\.(?=\s|$)/g, " ")
    .replace(/\s+/g, " ")
    .trim();

  if (!query) query = prompt.trim();
  query = query.split(/\s+/).filter((word) => {
    const folded = word.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase();
    return folded.length >= 3 && !researchNoise.has(folded);
  }).join(" ") || query;
  if (asksForOfficialSources && /\bcmake\b/i.test(prompt) && /\bfetchcontent\b/i.test(prompt)
      && /\b(?:reproduz|vers|fixar|fixe|pin|pinned)\w*\b/i.test(prompt)) {
    query = "site:cmake.org FetchContent FetchContent_Declare GIT_TAG commit hash URL_HASH";
  }
  if (asksForOfficialSources && !/\bsite:/i.test(query)) {
    const site = officialDocumentationSites.find((item) => item.topic.test(query));
    if (site) query = `site:${site.domain} ${query}`;
  }
  return query.slice(0, 2000);
}
