//! Fontes especializadas de pesquisa: APIs públicas em JSON, sem chave.
//!
//! Cada fonte devolve páginas no mesmo formato de `research_web` (título, URL e
//! texto), então síntese, citações e o worker continuam iguais. O texto é
//! montado a partir de campos estruturados da API, não de HTML de terceiros.
//! A busca HTTP é injetada para que os parsers sejam testados com fixtures.

use serde_json::{json, Value};
use std::time::Duration;

pub const SOURCE_NAMES: [&str; 4] = ["web", "package-registry", "wikipedia", "github"];
const MAX_TEXT: usize = 2_400;

#[derive(Debug, Clone, PartialEq)]
pub struct SourcePage {
    pub provider: &'static str,
    pub title: String,
    pub url: String,
    pub text: String,
}

#[derive(Debug, Clone, PartialEq)]
pub struct PackageLookup {
    pub name: String,
    pub ecosystems: Vec<String>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct SpecializedRequest {
    pub query: String,
    pub sources: Vec<String>,
    pub package: Option<PackageLookup>,
    pub language: String,
}

pub type JsonFetcher<'a> = dyn Fn(&str) -> Result<Value, String> + 'a;

fn text_field(value: &Value, key: &str) -> String {
    value.get(key).and_then(Value::as_str).unwrap_or_default().trim().to_string()
}

fn bounded(text: &str) -> String {
    text.split_whitespace().collect::<Vec<_>>().join(" ").chars().take(MAX_TEXT).collect()
}

fn strip_tags(html: &str) -> String {
    let mut output = String::with_capacity(html.len());
    let mut inside = false;
    for ch in html.chars() {
        match ch {
            '<' => inside = true,
            '>' => { inside = false; output.push(' '); }
            _ if !inside => output.push(ch),
            _ => {}
        }
    }
    output.replace("&quot;", "\"").replace("&amp;", "&").replace("&#39;", "'")
        .replace("&lt;", "<").replace("&gt;", ">")
}

fn encode_component(value: &str) -> String {
    url::form_urlencoded::byte_serialize(value.as_bytes()).collect()
}

/// Nomes aceitos pelos registros: sem espaços, barras extras ou caminhos.
pub fn valid_package_name(name: &str) -> bool {
    let trimmed = name.trim();
    if trimmed.is_empty() || trimmed.len() > 214 || trimmed.contains("..") { return false; }
    let body = trimmed.strip_prefix('@').unwrap_or(trimmed);
    let slashes = body.matches('/').count();
    if slashes > usize::from(trimmed.starts_with('@')) { return false; }
    body.chars().all(|ch| ch.is_ascii_alphanumeric() || matches!(ch, '-' | '_' | '.' | '/'))
        && body.chars().next().is_some_and(|ch| ch.is_ascii_alphanumeric())
}

pub fn package_url(ecosystem: &str, name: &str) -> Option<String> {
    if !valid_package_name(name) { return None; }
    match ecosystem {
        "npm" => Some(format!("https://registry.npmjs.org/{}/latest", name.replace('/', "%2F"))),
        "pypi" => Some(format!("https://pypi.org/pypi/{}/json", encode_component(name))),
        "crates" => Some(format!("https://crates.io/api/v1/crates/{}", encode_component(name))),
        _ => None,
    }
}

pub fn npm_page(name: &str, payload: &Value) -> Option<SourcePage> {
    let version = text_field(payload, "version");
    if version.is_empty() { return None; }
    let package = Some(text_field(payload, "name")).filter(|value| !value.is_empty()).unwrap_or_else(|| name.to_string());
    let description = text_field(payload, "description");
    let license = text_field(payload, "license");
    let homepage = text_field(payload, "homepage");
    let mut text = format!("A versão mais recente do pacote {package} no registro npm é {version} (tag latest consultada agora no registro oficial).");
    if !description.is_empty() { text.push_str(&format!(" Descrição do pacote {package}: {description}.")); }
    if !license.is_empty() { text.push_str(&format!(" Licença declarada do pacote {package}: {license}.")); }
    if !homepage.is_empty() { text.push_str(&format!(" Página do projeto {package}: {homepage}")); }
    Some(SourcePage {
        provider: "npm-registry",
        title: format!("{package} {version} — npm"),
        url: format!("https://www.npmjs.com/package/{package}"),
        text: bounded(&text),
    })
}

pub fn pypi_page(name: &str, payload: &Value) -> Option<SourcePage> {
    let info = payload.get("info")?;
    let version = text_field(info, "version");
    if version.is_empty() { return None; }
    let package = Some(text_field(info, "name")).filter(|value| !value.is_empty()).unwrap_or_else(|| name.to_string());
    let summary = text_field(info, "summary");
    let requires_python = text_field(info, "requires_python");
    let uploaded = payload.get("releases").and_then(|releases| releases.get(&version))
        .and_then(Value::as_array).and_then(|files| files.first())
        .map(|file| text_field(file, "upload_time_iso_8601")).unwrap_or_default();
    let mut text = format!("A versão mais recente do pacote {package} no PyPI é {version}");
    if !uploaded.is_empty() { text.push_str(&format!(", publicada em {}", uploaded.chars().take(10).collect::<String>())); }
    text.push_str(" (consultada agora no índice oficial).");
    if !summary.is_empty() { text.push_str(&format!(" Resumo do pacote {package}: {summary}.")); }
    if !requires_python.is_empty() { text.push_str(&format!(" O pacote {package} exige Python {requires_python}.")); }
    Some(SourcePage {
        provider: "pypi",
        title: format!("{package} {version} — PyPI"),
        url: format!("https://pypi.org/project/{package}/{version}/"),
        text: bounded(&text),
    })
}

pub fn crates_page(name: &str, payload: &Value) -> Option<SourcePage> {
    let krate = payload.get("crate")?;
    let version = Some(text_field(krate, "max_stable_version")).filter(|value| !value.is_empty())
        .unwrap_or_else(|| text_field(krate, "newest_version"));
    if version.is_empty() { return None; }
    let package = Some(text_field(krate, "name")).filter(|value| !value.is_empty()).unwrap_or_else(|| name.to_string());
    let description = text_field(krate, "description");
    let updated = text_field(krate, "updated_at");
    let mut text = format!("A versão estável mais recente do crate {package} no crates.io é {version}");
    if !updated.is_empty() { text.push_str(&format!(", com atualização registrada em {}", updated.chars().take(10).collect::<String>())); }
    text.push_str(" (consultada agora no registro oficial).");
    if !description.is_empty() { text.push_str(&format!(" Descrição do crate {package}: {description}.")); }
    Some(SourcePage {
        provider: "crates-io",
        title: format!("{package} {version} — crates.io"),
        url: format!("https://crates.io/crates/{package}"),
        text: bounded(&text),
    })
}

/// Chaves de artigo encontradas pela busca REST da Wikipedia.
pub fn wikipedia_search_keys(payload: &Value, limit: usize) -> Vec<String> {
    payload.get("pages").and_then(Value::as_array).map(|pages| pages.iter()
        .filter_map(|page| page.get("key").and_then(Value::as_str))
        .filter(|key| !key.is_empty() && !key.contains('/') && key.len() <= 300)
        .take(limit).map(ToOwned::to_owned).collect()).unwrap_or_default()
}

pub fn wikipedia_summary_page(payload: &Value) -> Option<SourcePage> {
    if text_field(payload, "type") == "disambiguation" { return None; }
    let extract = text_field(payload, "extract");
    let title = text_field(payload, "title");
    let url = payload.pointer("/content_urls/desktop/page").and_then(Value::as_str).unwrap_or_default();
    if extract.chars().count() < 40 || title.is_empty() || !url.starts_with("https://") { return None; }
    Some(SourcePage { provider: "wikipedia", title: format!("{title} — Wikipedia"), url: url.to_string(), text: bounded(&extract) })
}

pub fn github_pages(payload: &Value, limit: usize) -> Vec<SourcePage> {
    payload.get("items").and_then(Value::as_array).map(|items| items.iter().take(limit).filter_map(|item| {
        let full_name = text_field(item, "full_name");
        let url = text_field(item, "html_url");
        if full_name.is_empty() || !url.starts_with("https://github.com/") { return None; }
        let description = strip_tags(&text_field(item, "description"));
        let stars = item.get("stargazers_count").and_then(Value::as_u64).unwrap_or(0);
        let language = text_field(item, "language");
        let pushed = text_field(item, "pushed_at");
        let license = item.pointer("/license/spdx_id").and_then(Value::as_str).unwrap_or_default();
        let mut text = format!("O repositório {full_name} no GitHub tem {stars} estrelas");
        if !language.is_empty() { text.push_str(&format!(" e linguagem principal {language}")); }
        if !pushed.is_empty() { text.push_str(&format!("; último push em {}", pushed.chars().take(10).collect::<String>())); }
        text.push('.');
        if !description.trim().is_empty() { text.push_str(&format!(" Descrição do repositório {full_name}: {}.", description.trim())); }
        if !license.is_empty() && license != "NOASSERTION" { text.push_str(&format!(" Licença do repositório {full_name}: {license}.")); }
        Some(SourcePage { provider: "github", title: format!("{full_name} — GitHub"), url, text: bounded(&text) })
    }).collect()).unwrap_or_default()
}

/// Lê e valida os campos opcionais de fontes de `research_web`.
pub fn parse_request(args: &Value, query: &str) -> Result<Option<SpecializedRequest>, String> {
    let Some(raw) = args.get("sources") else { return Ok(None); };
    let list = raw.as_array().ok_or("sources deve ser uma lista")?;
    if list.is_empty() || list.len() > SOURCE_NAMES.len() {
        return Err("sources deve conter entre uma e quatro fontes".into());
    }
    let mut sources = Vec::new();
    for item in list {
        let name = item.as_str().ok_or("fonte inválida")?;
        if !SOURCE_NAMES.contains(&name) { return Err(format!("fonte desconhecida: {name}")); }
        if !sources.iter().any(|known| known == name) { sources.push(name.to_string()); }
    }
    let package = match args.get("package") {
        None | Some(Value::Null) => None,
        Some(value) => {
            let name = value.get("name").and_then(Value::as_str).ok_or("package.name é obrigatório")?.trim().to_string();
            if !valid_package_name(&name) { return Err("nome de pacote inválido".into()); }
            let ecosystems = value.get("ecosystems").and_then(Value::as_array).ok_or("package.ecosystems deve ser uma lista")?
                .iter().map(|item| item.as_str().unwrap_or_default().to_string()).collect::<Vec<_>>();
            if ecosystems.is_empty() || ecosystems.len() > 3
                || ecosystems.iter().any(|item| !matches!(item.as_str(), "npm" | "pypi" | "crates")) {
                return Err("package.ecosystems aceita npm, pypi e crates".into());
            }
            Some(PackageLookup { name, ecosystems })
        }
    };
    if sources.iter().any(|name| name == "package-registry") && package.is_none() {
        return Err("package-registry exige package com name e ecosystems".into());
    }
    let language = args.get("language").and_then(Value::as_str).unwrap_or("pt");
    if !matches!(language, "pt" | "en") { return Err("language deve ser pt ou en".into()); }
    Ok(Some(SpecializedRequest { query: query.to_string(), sources, package, language: language.to_string() }))
}

/// Consulta as fontes especializadas pedidas. Falhas viram tentativas
/// registradas; nenhuma fonte inventa conteúdo quando a API não responde.
pub fn collect(request: &SpecializedRequest, fetch: &JsonFetcher) -> (Vec<SourcePage>, Vec<Value>) {
    let mut pages = Vec::new();
    let mut attempts = Vec::new();
    if request.sources.iter().any(|name| name == "package-registry") {
        if let Some(package) = &request.package {
            for ecosystem in &package.ecosystems {
                let Some(url) = package_url(ecosystem, &package.name) else { continue; };
                let outcome = fetch(&url).map(|payload| match ecosystem.as_str() {
                    "npm" => npm_page(&package.name, &payload),
                    "pypi" => pypi_page(&package.name, &payload),
                    _ => crates_page(&package.name, &payload),
                }.into_iter().collect());
                record_outcome(ecosystem, &url, outcome, &mut pages, &mut attempts);
            }
        }
    }
    if request.sources.iter().any(|name| name == "wikipedia") {
        let lang = request.language.as_str();
        let search_url = format!("https://{lang}.wikipedia.org/w/rest.php/v1/search/page?q={}&limit=2", encode_component(&request.query));
        match fetch(&search_url) {
            Ok(payload) => {
                let keys = wikipedia_search_keys(&payload, 2);
                if keys.is_empty() {
                    record_outcome("wikipedia", &search_url, Ok(Vec::new()), &mut pages, &mut attempts);
                }
                for key in keys {
                    let url = format!("https://{lang}.wikipedia.org/api/rest_v1/page/summary/{}", encode_component(&key));
                    let outcome = fetch(&url).map(|summary| wikipedia_summary_page(&summary).into_iter().collect());
                    record_outcome("wikipedia", &url, outcome, &mut pages, &mut attempts);
                }
            }
            Err(error) => record_outcome("wikipedia", &search_url, Err(error), &mut pages, &mut attempts),
        }
    }
    if request.sources.iter().any(|name| name == "github") {
        let url = format!("https://api.github.com/search/repositories?q={}&sort=stars&order=desc&per_page=3",
            encode_component(&request.query));
        let outcome = fetch(&url).map(|payload| github_pages(&payload, 3));
        record_outcome("github", &url, outcome, &mut pages, &mut attempts);
    }
    (pages, attempts)
}

fn record_outcome(provider: &str, url: &str, outcome: Result<Vec<SourcePage>, String>,
                  pages: &mut Vec<SourcePage>, attempts: &mut Vec<Value>) {
    match outcome {
        Ok(found) if !found.is_empty() => {
            attempts.push(json!({"provider": provider, "url": url, "status": "opened-structured-source", "pages": found.len()}));
            pages.extend(found);
        }
        Ok(_) => attempts.push(json!({"provider": provider, "url": url, "status": "empty-structured-source"})),
        Err(error) => attempts.push(json!({"provider": provider, "url": url, "status": "structured-source-failed",
            "error": error.chars().take(300).collect::<String>()})),
    }
}

/// Busca JSON real com limite de tempo e corpo; usado fora dos testes.
pub fn http_json_fetcher(timeout: Duration) -> impl Fn(&str) -> Result<Value, String> {
    move |url: &str| {
        let client = reqwest::blocking::Client::builder()
            .timeout(timeout)
            .user_agent("brasa-local-runtime/0.1 (pesquisa local; https://github.com/va415988-glitch/Brasa-IA)")
            .build()
            .map_err(|error| error.to_string())?;
        let response = client.get(url).header("Accept", "application/json").send()
            .and_then(|response| response.error_for_status())
            .map_err(|error| error.to_string())?;
        let body = response.text().map_err(|error| error.to_string())?;
        if body.len() > 2 * 1024 * 1024 { return Err("resposta acima de 2 MiB".into()); }
        serde_json::from_str(&body).map_err(|error| format!("JSON inválido: {error}"))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::cell::RefCell;

    #[test]
    fn registros_de_pacotes_viram_paginas_citaveis() {
        let npm = npm_page("react", &json!({"name":"react","version":"19.2.0","description":"React is a JavaScript library for building user interfaces.","license":"MIT","homepage":"https://react.dev/"})).unwrap();
        assert_eq!(npm.provider, "npm-registry");
        assert!(npm.text.contains("versão mais recente do pacote react no registro npm é 19.2.0"));
        assert_eq!(npm.url, "https://www.npmjs.com/package/react");
        let pypi = pypi_page("requests", &json!({"info":{"name":"requests","version":"2.32.5","summary":"Python HTTP for Humans.","requires_python":">=3.9"},
            "releases":{"2.32.5":[{"upload_time_iso_8601":"2025-08-18T20:46:00Z"}]}})).unwrap();
        assert!(pypi.text.contains("2.32.5, publicada em 2025-08-18"));
        assert!(pypi.text.contains("exige Python >=3.9"));
        let krate = crates_page("serde", &json!({"crate":{"name":"serde","max_stable_version":"1.0.228","newest_version":"1.0.228","description":"A serialization framework"}})).unwrap();
        assert!(krate.text.contains("crate serde no crates.io é 1.0.228"));
        assert!(npm_page("x", &json!({})).is_none());
        assert!(pypi_page("x", &json!({"info":{}})).is_none());
    }

    #[test]
    fn nomes_de_pacote_nao_aceitam_caminhos() {
        assert!(valid_package_name("@types/node"));
        assert!(valid_package_name("scikit-learn"));
        for invalid in ["", "../etc", "a/b/c", "nome com espaço", "@a/b/c", "-x"] {
            assert!(!valid_package_name(invalid), "{invalid}");
        }
        assert_eq!(package_url("npm", "@types/node").unwrap(), "https://registry.npmjs.org/@types%2Fnode/latest");
        assert!(package_url("maven", "x").is_none());
    }

    #[test]
    fn wikipedia_e_github_usam_campos_estruturados() {
        assert_eq!(wikipedia_search_keys(&json!({"pages":[{"key":"Rust_(linguagem_de_programação)"},{"key":"a/b"}]}), 2),
            vec!["Rust_(linguagem_de_programação)".to_string()]);
        let page = wikipedia_summary_page(&json!({"type":"standard","title":"Rust","extract":"Rust é uma linguagem de programação multiparadigma focada em segurança.",
            "content_urls":{"desktop":{"page":"https://pt.wikipedia.org/wiki/Rust"}}})).unwrap();
        assert_eq!(page.url, "https://pt.wikipedia.org/wiki/Rust");
        assert!(wikipedia_summary_page(&json!({"type":"disambiguation","title":"X","extract":"x".repeat(80)})).is_none());
        let repos = github_pages(&json!({"items":[{"full_name":"tokio-rs/axum","html_url":"https://github.com/tokio-rs/axum","description":"Ergonomic web framework","stargazers_count":23000,
            "language":"Rust","pushed_at":"2026-10-01T00:00:00Z","license":{"spdx_id":"MIT"}},{"full_name":"x","html_url":"https://evil.test/x"}]}), 3);
        assert_eq!(repos.len(), 1);
        assert!(repos[0].text.contains("23000 estrelas e linguagem principal Rust"));
    }

    #[test]
    fn pedido_valida_fontes_e_pacote() {
        assert_eq!(parse_request(&json!({}), "q").unwrap(), None);
        assert!(parse_request(&json!({"sources": ["telepatia"]}), "q").is_err());
        assert!(parse_request(&json!({"sources": ["package-registry"]}), "q").is_err());
        assert!(parse_request(&json!({"sources": ["web"], "language": "fr"}), "q").is_err());
        let request = parse_request(&json!({"sources": ["package-registry", "web", "web"],
            "package": {"name": "react", "ecosystems": ["npm"]}}), "react").unwrap().unwrap();
        assert_eq!(request.sources, vec!["package-registry", "web"]);
    }

    #[test]
    fn coleta_registra_falhas_sem_inventar_paginas() {
        let requested = RefCell::new(Vec::new());
        let fetch = |url: &str| -> Result<Value, String> {
            requested.borrow_mut().push(url.to_string());
            if url.contains("registry.npmjs.org") { return Ok(json!({"name":"react","version":"19.2.0"})); }
            if url.contains("pypi.org") { return Err("HTTP 404".into()); }
            if url.contains("search/page") { return Ok(json!({"pages":[{"key":"React"}]})); }
            if url.contains("page/summary") { return Ok(json!({"title":"React","extract":"React é uma biblioteca JavaScript para construir interfaces de usuário.",
                "content_urls":{"desktop":{"page":"https://pt.wikipedia.org/wiki/React"}}})); }
            Err("indisponível".into())
        };
        let request = parse_request(&json!({"sources":["package-registry","wikipedia","github"],
            "package":{"name":"react","ecosystems":["npm","pypi"]}}), "React").unwrap().unwrap();
        let (pages, attempts) = collect(&request, &fetch);
        assert_eq!(pages.iter().map(|page| page.provider).collect::<Vec<_>>(), vec!["npm-registry", "wikipedia"]);
        let statuses: Vec<&str> = attempts.iter().filter_map(|item| item["status"].as_str()).collect();
        assert_eq!(statuses.iter().filter(|status| **status == "structured-source-failed").count(), 2);
        assert!(requested.borrow().iter().any(|url| url.starts_with("https://api.github.com/search/repositories?q=React")));
    }
}
