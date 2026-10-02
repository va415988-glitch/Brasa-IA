function functionBody(source: string, start: number): string {
  let depth = 1, quote = "", comment = "";
  for (let index = start; index < source.length; index++) {
    const character = source[index], next = source[index + 1];
    if (comment === "line") { if (character === "\n") comment = ""; continue; }
    if (comment === "block") { if (character === "*" && next === "/") { comment = ""; index++; } continue; }
    if (quote) {
      if (character === "\\") index++;
      else if (character === quote) quote = "";
      continue;
    }
    if (character === "/" && next === "/") { comment = "line"; index++; continue; }
    if (character === "/" && next === "*") { comment = "block"; index++; continue; }
    if (character === "'" || character === '"' || character === "`") { quote = character; continue; }
    if (character === "{") depth++;
    if (character === "}" && --depth === 0) return source.slice(start, index);
  }
  return ""; // Incomplete read: do not join unrelated fragments into a function.
}

/** Bounded source hints, not execution evidence or a JavaScript interpreter. */
export function relativeJsonEndpoints(source: string): string[] {
  const endpoints = new Set<string>();
  const escaped = (value: string) => value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const literalsFor = (callee: string) => {
    const calls = new RegExp("\\b" + escaped(callee) + "\\s*\\(\\s*['\"](/[^'\"\\s]*)['\"]", "g");
    return [...source.matchAll(calls)].map(match => match[1]);
  };
  if (!/\.json\s*\(/.test(source)) return [];
  for (const endpoint of literalsFor("fetch")) endpoints.add(endpoint);
  // Resolve one local wrapper hop: api('/tasks') -> api(path) -> fetch(path).
  // Require the forwarding fetch and JSON parser inside the same function.
  const declarations = [...source.matchAll(/\b(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(\s*([A-Za-z_$][\w$]*)[^)]*\)\s*\{/g)];
  for (let index = 0; index < declarations.length; index++) {
    const declaration = declarations[index];
    const start = declaration.index! + declaration[0].length;
    const body = functionBody(source, start);
    const parameter = escaped(declaration[2]);
    if (!new RegExp("\\bfetch\\s*\\(\\s*" + parameter + "\\s*[,)]").test(body)
        || !/\.json\s*\(/.test(body)) continue;
    for (const endpoint of literalsFor(declaration[1])) endpoints.add(endpoint);
  }
  return [...endpoints].slice(0, 16);
}
