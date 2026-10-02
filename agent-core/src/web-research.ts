import type {Evidence, ResearchPort} from "./contracts.ts";

export interface WebResponse {
  status: number;
  text(): Promise<string>;
}

export interface WebRequestInit {
  headers?: Record<string, string>;
}

export type WebFetcher = (url: string, init?: WebRequestInit) => Promise<WebResponse>;

export interface SearchHit {
  title: string;
  url: string;
  snippet?: string;
}

export interface SearchProvider {
  search(query: string, limit: number): Promise<readonly SearchHit[]>;
}

export interface WebResearchOptions {
  maxResults?: number;
  maxExcerpt?: number;
  timeoutMs?: number;
}

function decodeEntities(value: string): string {
  return value
    .replace(/&nbsp;/gi, " ")
    .replace(/&amp;/gi, "&")
    .replace(/&quot;/gi, '"')
    .replace(/&#39;|&apos;/gi, "'")
    .replace(/&lt;/gi, "<")
    .replace(/&gt;/gi, ">")
    .replace(/&#(\d+);/g, (_match, code: string) => String.fromCodePoint(Number(code)));
}

export function htmlToText(html: string, maxCharacters = 20000): string {
  const content = html
    .replace(/<!--([\s\S]*?)-->/g, " ")
    .replace(/<(head|script|style|noscript|svg|nav|footer|header)[^>]*>[\s\S]*?<\/\1>/gi, " ")
    .replace(/<br\s*\/?\s*>/gi, "\n")
    .replace(/<\/(p|div|article|section|li|h[1-6])\s*>/gi, "\n")
    .replace(/<[^>]+>/g, " ");
  return decodeEntities(content)
    .replace(/[ \t\r\f\v]+/g, " ")
    .replace(/\n\s*\n+/g, "\n")
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean)
    .join("\n")
    .slice(0, maxCharacters)
    .trim();
}

export function htmlTitle(html: string): string {
  const match = html.match(/<title[^>]*>([\s\S]*?)<\/title>/i);
  return match ? htmlToText(match[1], 300).replace(/\s+/g, " ").trim() : "";
}

function normalizeSearchUrl(raw: string): string {
  const candidate = raw.startsWith("//") ? "https:" + raw : raw;
  try {
    const parsed = new URL(candidate, "https://html.duckduckgo.com");
    const redirected = parsed.searchParams.get("uddg");
    return redirected ? decodeURIComponent(redirected) : parsed.toString();
  } catch {
    return "";
  }
}

export function parseDuckDuckGoResults(html: string): SearchHit[] {
  const hits: SearchHit[] = [];
  const pattern = /<a\b(?=[^>]*class=["'][^"']*result__a[^"']*["'])[^>]*href=["']([^"']+)["'][^>]*>([\s\S]*?)<\/a>/gi;
  for (const match of html.matchAll(pattern)) {
    const url = normalizeSearchUrl(match[1]);
    const title = htmlToText(match[2], 300);
    if (!url || !title || !/^https?:\/\//i.test(url)) continue;
    if (!hits.some((item) => item.url === url)) hits.push({title, url});
  }
  return hits;
}

function withTimeout<T>(promise: Promise<T>, timeoutMs: number): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("A operação web excedeu o tempo limite.")), timeoutMs);
    promise.then(
      (value) => {
        clearTimeout(timer);
        resolve(value);
      },
      (error) => {
        clearTimeout(timer);
        reject(error);
      },
    );
  });
}

async function checkedFetch(fetcher: WebFetcher, url: string, timeoutMs: number): Promise<string> {
  const response = await withTimeout(
    fetcher(url, {headers: {"user-agent": "IA-Local-do-Zero/agent-core"}}),
    timeoutMs,
  );
  if (response.status < 200 || response.status >= 400) {
    throw new Error("A página respondeu com HTTP " + response.status + ".");
  }
  return withTimeout(response.text(), timeoutMs);
}

export class DuckDuckGoSearchProvider implements SearchProvider {
  private readonly fetcher: WebFetcher;
  private readonly timeoutMs: number;

  constructor(fetcher?: WebFetcher, timeoutMs = 8000) {
    this.fetcher = fetcher ?? (globalThis.fetch as unknown as WebFetcher);
    this.timeoutMs = timeoutMs;
  }

  async search(query: string, limit: number): Promise<readonly SearchHit[]> {
    const url = "https://html.duckduckgo.com/html/?q=" + encodeURIComponent(query);
    const html = await checkedFetch(this.fetcher, url, this.timeoutMs);
    return parseDuckDuckGoResults(html).slice(0, Math.max(1, Math.min(limit, 10)));
  }
}

export class WebResearchService implements SearchProvider {
  private readonly provider: SearchProvider;
  private readonly fetcher: WebFetcher;
  private readonly options: Required<WebResearchOptions>;

  constructor(provider: SearchProvider, fetcher: WebFetcher, options: WebResearchOptions = {}) {
    this.provider = provider;
    this.fetcher = fetcher;
    this.options = {
      maxResults: options.maxResults ?? 3,
      maxExcerpt: options.maxExcerpt ?? 12000,
      timeoutMs: options.timeoutMs ?? 8000,
    };
  }

  async search(query: string, limit: number): Promise<readonly SearchHit[]> {
    return this.provider.search(query, limit);
  }

  async research(query: string): Promise<readonly Evidence[]> {
    const hits = await this.provider.search(query, this.options.maxResults);
    const evidence: Evidence[] = [];
    const visited = new Set<string>();
    for (const hit of hits) {
      if (visited.has(hit.url)) continue;
      visited.add(hit.url);
      try {
        const html = await checkedFetch(this.fetcher, hit.url, this.options.timeoutMs);
        const text = htmlToText(html, this.options.maxExcerpt);
        if (!text) continue;
        evidence.push({
          title: htmlTitle(html) || hit.title,
          url: hit.url,
          excerpt: text,
          sourceId: "web-" + (evidence.length + 1),
        });
      } catch {
        // Uma fonte indisponível não invalida as outras fontes da pesquisa.
      }
    }
    return evidence;
  }
}

export class DirectWebResearchPort implements ResearchPort {
  private readonly service: WebResearchService;

  constructor(service: WebResearchService) {
    this.service = service;
  }

  research(query: string): Promise<readonly Evidence[]> {
    return this.service.research(query);
  }
}
