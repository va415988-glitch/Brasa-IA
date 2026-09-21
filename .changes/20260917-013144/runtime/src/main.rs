use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::collections::{HashMap, VecDeque};
use std::fs;
use std::io::{self, BufRead, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};
use tiny_http::{Header, Method, Response, Server, StatusCode};
use thiserror::Error;

const TARGET_REQUEST_MS: u128 = 30_000;
const MAX_REQUEST_MS: u128 = 30_000;
const SEARCH_TIMEOUT: Duration = Duration::from_secs(10);
const MAX_FILE_BYTES: u64 = 128 * 1024;

#[derive(Debug, Deserialize)]
struct ToolCall {
    tool: String,
    #[serde(default)]
    arguments: Value,
    #[serde(default)]
    request_id: Option<String>,
}

#[derive(Debug, Deserialize)]
struct ChatMessage {
    role: String,
    content: String,
}

#[derive(Debug, Deserialize)]
struct ChatRequest {
    messages: Vec<ChatMessage>,
    #[serde(default)]
    request_id: Option<String>,
}

#[derive(Debug, Serialize)]
struct ToolResult {
    ok: bool,
    tool: String,
    data: Option<Value>,
    error: Option<String>,
    elapsed_ms: u128,
    target_ms: u128,
    max_ms: u128,
    performance: String,
}

#[derive(Debug, Clone, Serialize)]
struct ActivityEvent {
    id: u64,
    operation: String,
    phase: String,
    message: String,
    done: bool,
    elapsed_ms: Option<u128>,
}

#[derive(Debug, Default)]
struct ActivityLog {
    next_id: u64,
    events: VecDeque<ActivityEvent>,
}

type SharedActivity = Arc<Mutex<ActivityLog>>;

fn activity(activity: &SharedActivity, operation: &str, phase: &str, message: &str, done: bool, elapsed_ms: Option<u128>) {
    let mut log = activity.lock().unwrap();
    log.next_id += 1;
    let id = log.next_id;
    log.events.push_back(ActivityEvent {
        id,
        operation: operation.into(),
        phase: phase.into(),
        message: message.into(),
        done,
        elapsed_ms,
    });
    while log.events.len() > 100 {
        log.events.pop_front();
    }
    eprintln!("[atividade][{}][{}] {}{}", operation, phase, message, elapsed_ms.map(|ms| format!(" ({ms} ms)")).unwrap_or_default());
}

#[derive(Debug, Clone, Serialize)]
struct SourceRecord {
    source_id: String,
    title: String,
    url: String,
    snippet: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    text: Option<String>,
}

fn performance_label(elapsed_ms: u128) -> &'static str {
    if elapsed_ms <= TARGET_REQUEST_MS {
        "within_target"
    } else if elapsed_ms <= MAX_REQUEST_MS {
        "within_hard_limit"
    } else {
        "timeout"
    }
}

#[derive(Debug, Error)]
enum RuntimeError {
    #[error("ferramenta desconhecida: {0}")]
    UnknownTool(String),
    #[error("argumento obrigatório ausente: {0}")]
    MissingArgument(String),
    #[error("argumento inválido: {0}")]
    InvalidArgument(String),
    #[error("falha na pesquisa: {0}")]
    Search(String),
    #[error("fonte desconhecida: {0}")]
    UnknownSource(String),
    #[error("caminho fora do workspace: {0}")]
    OutsideWorkspace(String),
    #[error("falha no workspace: {0}")]
    Workspace(String),
    #[error("modelo conversacional indisponível: {0}")]
    Model(String),
}

fn required_string(args: &Value, name: &str) -> Result<String, RuntimeError> {
    args.get(name)
        .and_then(Value::as_str)
        .map(ToOwned::to_owned)
        .ok_or_else(|| RuntimeError::MissingArgument(name.to_string()))
}

fn workspace_path(root: &Path, relative: &str) -> Result<PathBuf, RuntimeError> {
    let candidate = if relative.trim().is_empty() {
        root.to_path_buf()
    } else {
        root.join(relative)
    };
    let canonical = candidate
        .canonicalize()
        .map_err(|error| RuntimeError::Workspace(error.to_string()))?;
    if !canonical.starts_with(root) {
        return Err(RuntimeError::OutsideWorkspace(relative.to_string()));
    }
    Ok(canonical)
}

fn workspace_new_path(root: &Path, relative: &str) -> Result<PathBuf, RuntimeError> {
    if relative.trim().is_empty() || Path::new(relative).is_absolute() || Path::new(relative).components().any(|component| matches!(component, std::path::Component::ParentDir)) {
        return Err(RuntimeError::InvalidArgument("caminho relativo obrigatório".into()));
    }
    let root = root.canonicalize().map_err(|error| RuntimeError::Workspace(error.to_string()))?;
    let candidate = root.join(relative);
    let mut existing_parent = candidate.parent().ok_or_else(|| RuntimeError::Workspace("diretório pai inválido".into()))?;
    while !existing_parent.exists() {
        existing_parent = existing_parent.parent().ok_or_else(|| RuntimeError::Workspace("diretório pai inválido".into()))?;
    }
    let existing_parent = existing_parent.canonicalize().map_err(|error| RuntimeError::Workspace(error.to_string()))?;
    if !existing_parent.starts_with(&root) {
        return Err(RuntimeError::OutsideWorkspace(relative.to_string()));
    }
    Ok(candidate)
}

fn list_files(args: &Value, root: &Path) -> Result<Value, RuntimeError> {
    let relative = args.get("path").and_then(Value::as_str).unwrap_or("");
    let directory = workspace_path(root, relative)?;
    if !directory.is_dir() {
        return Err(RuntimeError::Workspace("o caminho não é um diretório".into()));
    }
    let mut entries = Vec::new();
    for entry in fs::read_dir(&directory).map_err(|error| RuntimeError::Workspace(error.to_string()))? {
        let entry = entry.map_err(|error| RuntimeError::Workspace(error.to_string()))?;
        let metadata = entry.metadata().map_err(|error| RuntimeError::Workspace(error.to_string()))?;
        let name = entry.file_name().to_string_lossy().to_string();
        if name.starts_with('.') || name == "target" || name == ".venv" {
            continue;
        }
        entries.push(json!({"name": name, "kind": if metadata.is_dir() { "directory" } else { "file" }, "bytes": metadata.len()}));
    }
    entries.sort_by(|left, right| left["name"].as_str().cmp(&right["name"].as_str()));
    Ok(json!({"workspace": root, "path": relative, "entries": entries}))
}

fn read_file(args: &Value, root: &Path) -> Result<Value, RuntimeError> {
    let relative = required_string(args, "path")?;
    let path = workspace_path(root, &relative)?;
    let metadata = fs::metadata(&path).map_err(|error| RuntimeError::Workspace(error.to_string()))?;
    if !metadata.is_file() {
        return Err(RuntimeError::Workspace("o caminho não é um arquivo".into()));
    }
    if metadata.len() > MAX_FILE_BYTES {
        return Err(RuntimeError::Workspace(format!("arquivo excede o limite de {MAX_FILE_BYTES} bytes")));
    }
    let content = fs::read_to_string(&path).map_err(|error| RuntimeError::Workspace(error.to_string()))?;
    Ok(json!({"path": relative, "bytes": metadata.len(), "content": content}))
}

fn create_directory(args: &Value, root: &Path) -> Result<Value, RuntimeError> {
    let relative = required_string(args, "path")?;
    let path = workspace_new_path(root, &relative)?;
    if path.exists() {
        return Err(RuntimeError::Workspace("o diretório já existe".into()));
    }
    fs::create_dir_all(&path).map_err(|error| RuntimeError::Workspace(error.to_string()))?;
    Ok(json!({"path": relative, "created": true, "kind": "directory"}))
}

fn create_file(args: &Value, root: &Path) -> Result<Value, RuntimeError> {
    let relative = required_string(args, "path")?;
    let content = required_string(args, "content")?;
    if content.as_bytes().len() > MAX_FILE_BYTES as usize {
        return Err(RuntimeError::Workspace(format!("conteúdo excede o limite de {MAX_FILE_BYTES} bytes")));
    }
    let path = workspace_new_path(root, &relative)?;
    if path.exists() {
        return Err(RuntimeError::Workspace("o arquivo já existe; use edit_file para alterá-lo".into()));
    }
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).map_err(|error| RuntimeError::Workspace(error.to_string()))?;
    }
    fs::write(&path, content.as_bytes()).map_err(|error| RuntimeError::Workspace(error.to_string()))?;
    Ok(json!({"path": relative, "created": true, "bytes": content.as_bytes().len()}))
}

fn edit_file(args: &Value, root: &Path) -> Result<Value, RuntimeError> {
    let relative = required_string(args, "path")?;
    let old_text = required_string(args, "old_text")?;
    let new_text = required_string(args, "new_text")?;
    let path = workspace_path(root, &relative)?;
    let metadata = fs::metadata(&path).map_err(|error| RuntimeError::Workspace(error.to_string()))?;
    if !metadata.is_file() || metadata.len() > MAX_FILE_BYTES {
        return Err(RuntimeError::Workspace("arquivo inválido ou acima do limite".into()));
    }
    let content = fs::read_to_string(&path).map_err(|error| RuntimeError::Workspace(error.to_string()))?;
    let occurrences = content.match_indices(&old_text).count();
    if occurrences == 0 {
        return Err(RuntimeError::Workspace("trecho antigo não encontrado exatamente".into()));
    }
    if occurrences > 1 {
        return Err(RuntimeError::Workspace("trecho antigo aparece mais de uma vez; torne a edição específica".into()));
    }
    let backup_dir = root.join(".ia-local-backups");
    fs::create_dir_all(&backup_dir).map_err(|error| RuntimeError::Workspace(format!("não foi possível criar backup: {error}")))?;
    let safe_name = relative.replace(['/', '\\'], "__");
    let timestamp = SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default().as_millis();
    let backup_path = backup_dir.join(format!("{timestamp}__{safe_name}"));
    fs::write(&backup_path, content.as_bytes()).map_err(|error| RuntimeError::Workspace(format!("não foi possível salvar backup: {error}")))?;
    let updated = content.replacen(&old_text, &new_text, 1);
    if updated.as_bytes().len() > MAX_FILE_BYTES as usize {
        return Err(RuntimeError::Workspace(format!("arquivo resultante excede o limite de {MAX_FILE_BYTES} bytes")));
    }
    fs::write(&path, updated.as_bytes()).map_err(|error| RuntimeError::Workspace(error.to_string()))?;
    Ok(json!({"path": relative, "updated": true, "bytes": updated.as_bytes().len(), "backup": backup_path.strip_prefix(root).unwrap_or(&backup_path)}))
}

fn inspect_media(args: &Value, root: &Path) -> Result<Value, RuntimeError> {
    let relative = required_string(args, "path")?;
    let path = workspace_path(root, &relative)?;
    let metadata = fs::metadata(&path).map_err(|error| RuntimeError::Workspace(error.to_string()))?;
    if !metadata.is_file() {
        return Err(RuntimeError::Workspace("o caminho não é um arquivo".into()));
    }
    if metadata.len() > 512 * 1024 * 1024 {
        return Err(RuntimeError::Workspace("mídia acima do limite de inspeção".into()));
    }
    let extension = path.extension().and_then(|value| value.to_str()).unwrap_or("").to_lowercase();
    let media_type = match extension.as_str() {
        "png" | "jpg" | "jpeg" | "webp" | "gif" => "image",
        "wav" | "mp3" | "ogg" | "flac" | "m4a" => "audio",
        "mp4" | "mkv" | "webm" | "mov" => "video",
        "pdf" | "txt" | "md" | "json" => "document",
        _ => "unknown",
    };
    Ok(json!({"path": relative, "media_type": media_type, "extension": extension, "bytes": metadata.len()}))
}

fn extract_document_text(args: &Value, root: &Path) -> Result<Value, RuntimeError> {
    let relative = required_string(args, "path")?;
    let path = workspace_path(root, &relative)?;
    let metadata = fs::metadata(&path).map_err(|error| RuntimeError::Workspace(error.to_string()))?;
    if !metadata.is_file() || metadata.len() > 64 * 1024 * 1024 {
        return Err(RuntimeError::Workspace("documento inválido ou acima do limite".into()));
    }
    let extension = path.extension().and_then(|value| value.to_str()).unwrap_or("").to_lowercase();
    let text = if extension == "pdf" {
        let mut child = Command::new("pdftotext")
            .arg("-layout").arg(&path).arg("-")
            .stdout(Stdio::piped()).stderr(Stdio::piped()).spawn()
            .map_err(|error| RuntimeError::Workspace(format!("pdftotext indisponível: {error}")))?;
        let started = Instant::now();
        loop {
            if child.try_wait().map_err(|error| RuntimeError::Workspace(error.to_string()))?.is_some() { break; }
            if started.elapsed() > SEARCH_TIMEOUT {
                let _ = child.kill();
                return Err(RuntimeError::Workspace("extração de PDF excedeu 10 segundos".into()));
            }
            std::thread::sleep(Duration::from_millis(20));
        }
        let output = child.wait_with_output().map_err(|error| RuntimeError::Workspace(error.to_string()))?;
        if !output.status.success() {
            return Err(RuntimeError::Workspace(String::from_utf8_lossy(&output.stderr).trim().to_string()));
        }
        String::from_utf8_lossy(&output.stdout).into_owned()
    } else {
        fs::read_to_string(&path).map_err(|error| RuntimeError::Workspace(error.to_string()))?
    };
    let limited = text.chars().take(200_000).collect::<String>();
    Ok(json!({"path": relative, "text": limited, "truncated": text.chars().count() > 200_000}))
}

fn search_files(args: &Value, root: &Path) -> Result<Value, RuntimeError> {
    let query = required_string(args, "query")?.to_lowercase();
    if query.is_empty() {
        return Err(RuntimeError::InvalidArgument("query vazia".into()));
    }
    let mut matches = Vec::new();
    let mut stack = vec![root.to_path_buf()];
    while let Some(directory) = stack.pop() {
        let entries = fs::read_dir(directory).map_err(|error| RuntimeError::Workspace(error.to_string()))?;
        for entry in entries {
            let entry = entry.map_err(|error| RuntimeError::Workspace(error.to_string()))?;
            let name = entry.file_name().to_string_lossy().to_string();
            if name.starts_with('.') || name == "target" || name == ".venv" {
                continue;
            }
            let path = entry.path();
            if path.is_dir() {
                stack.push(path);
                continue;
            }
            let metadata = entry.metadata().map_err(|error| RuntimeError::Workspace(error.to_string()))?;
            if metadata.len() > MAX_FILE_BYTES {
                continue;
            }
            if let Ok(content) = fs::read_to_string(&path) {
                for (line_number, line) in content.lines().enumerate() {
                    if line.to_lowercase().contains(&query) {
                        let relative = path.strip_prefix(root).unwrap_or(&path).display().to_string();
                        matches.push(json!({"path": relative, "line": line_number + 1, "text": line.trim()}));
                        if matches.len() >= 100 { return Ok(json!({"query": query, "matches": matches, "truncated": true})); }
                    }
                }
            }
        }
    }
    Ok(json!({"query": query, "matches": matches, "truncated": false}))
}

fn set_workspace(args: &Value, workspace: &mut PathBuf) -> Result<Value, RuntimeError> {
    let requested = required_string(args, "path")?;
    let candidate = PathBuf::from(&requested);
    let canonical = candidate
        .canonicalize()
        .map_err(|error| RuntimeError::Workspace(format!("não foi possível acessar o diretório: {error}")))?;
    if !canonical.is_dir() {
        return Err(RuntimeError::Workspace("o caminho escolhido não é um diretório".into()));
    }
    *workspace = canonical.clone();
    Ok(json!({"workspace": canonical, "selected": true}))
}

fn chat_prompt(request: &ChatRequest) -> String {
    let mut prompt = String::new();
    for message in &request.messages {
        prompt.push_str(&format!("<|{}|>\n{}\n", message.role, message.content));
    }
    prompt.push_str("<|assistant|>\n");
    prompt
}

fn call_model(request: &ChatRequest) -> Result<Value, RuntimeError> {
    let client = reqwest::blocking::Client::builder()
        .timeout(Duration::from_secs(25))
        .build()
        .map_err(|error| RuntimeError::Model(error.to_string()))?;
    let response = client
        .post("http://127.0.0.1:3101/generate")
        .json(&json!({"prompt": chat_prompt(request), "max_tokens": 96}))
        .send()
        .and_then(|response| response.error_for_status())
        .map_err(|error| RuntimeError::Model(error.to_string()))?;
    let data: Value = response.json().map_err(|error| RuntimeError::Model(error.to_string()))?;
    if data.get("ok") != Some(&Value::Bool(true)) {
        return Err(RuntimeError::Model(data["error"].as_str().unwrap_or("erro desconhecido").into()));
    }
    Ok(data)
}

fn next_source_id(next_id: &mut u64) -> String {
    let source_id = format!("web-{next_id}");
    *next_id += 1;
    source_id
}

fn search_web(
    args: &Value,
    sources: &mut HashMap<String, SourceRecord>,
    next_id: &mut u64,
) -> Result<Value, RuntimeError> {
    let query = required_string(args, "query")?;
    if query.trim().is_empty() {
        return Err(RuntimeError::InvalidArgument("query vazia".into()));
    }

    let encoded_query = url::form_urlencoded::Serializer::new(String::new())
        .append_pair("q", &query)
        .finish();
    let endpoint = format!("https://html.duckduckgo.com/html/?{encoded_query}");
    let client = reqwest::blocking::Client::builder()
        .timeout(SEARCH_TIMEOUT)
        .user_agent("local-ai-runtime/0.1")
        .build()
        .map_err(|error| RuntimeError::Search(error.to_string()))?;
    let html = client
        .get(endpoint)
        .send()
        .and_then(|response| response.error_for_status())
        .and_then(|response| response.text())
        .map_err(|error| RuntimeError::Search(error.to_string()))?;

    let document = scraper::Html::parse_document(&html);
    let result_selector = scraper::Selector::parse(".result").unwrap();
    let title_selector = scraper::Selector::parse(".result__title a").unwrap();
    let snippet_selector = scraper::Selector::parse(".result__snippet").unwrap();
    let url_selector = scraper::Selector::parse(".result__url").unwrap();

    let results: Vec<Value> = document
        .select(&result_selector)
        .take(5)
        .enumerate()
        .filter_map(|(_index, result)| {
            let title = result
                .select(&title_selector)
                .next()
                .map(|node| node.text().collect::<String>().trim().to_string())?;
            let link = result
                .select(&title_selector)
                .next()
                .and_then(|node| node.value().attr("href"))
                .unwrap_or_default()
                .to_string();
            let link = if link.starts_with("//") {
                format!("https:{link}")
            } else {
                link
            };
            let snippet = result
                .select(&snippet_selector)
                .next()
                .map(|node| node.text().collect::<String>().trim().to_string())
                .unwrap_or_default();
            let displayed_url = result
                .select(&url_selector)
                .next()
                .map(|node| node.text().collect::<String>().trim().to_string())
                .unwrap_or_default();
            let source_id = next_source_id(next_id);
            sources.insert(
                source_id.clone(),
                SourceRecord {
                    source_id: source_id.clone(),
                    title: title.clone(),
                    url: link.clone(),
                    snippet: snippet.clone(),
                    text: None,
                },
            );
            Some(json!({
                "source_id": source_id,
                "title": title,
                "url": link,
                "displayed_url": displayed_url,
                "snippet": snippet
            }))
        })
        .collect();

    Ok(json!({
        "query": query,
        "results": results,
        "source": "duckduckgo_html",
        "result_count": results.len()
    }))
}

fn open_page(
    args: &Value,
    sources: &mut HashMap<String, SourceRecord>,
    next_id: &mut u64,
) -> Result<Value, RuntimeError> {
    let url = required_string(args, "url")?;
    let requested_source_id = args
        .get("source_id")
        .and_then(Value::as_str)
        .map(ToOwned::to_owned);
    if let Some(source_id) = &requested_source_id {
        if !sources.contains_key(source_id) {
            return Err(RuntimeError::UnknownSource(source_id.clone()));
        }
    }
    let source_id = requested_source_id.unwrap_or_else(|| next_source_id(next_id));
    let parsed = url::Url::parse(&url)
        .map_err(|error| RuntimeError::InvalidArgument(format!("URL inválida: {error}")))?;
    if !matches!(parsed.scheme(), "http" | "https") {
        return Err(RuntimeError::InvalidArgument(
            "a URL deve usar http ou https".into(),
        ));
    }

    let client = reqwest::blocking::Client::builder()
        .timeout(SEARCH_TIMEOUT)
        .user_agent("local-ai-runtime/0.1")
        .build()
        .map_err(|error| RuntimeError::Search(error.to_string()))?;
    let response = client
        .get(parsed)
        .send()
        .and_then(|response| response.error_for_status())
        .map_err(|error| RuntimeError::Search(error.to_string()))?;
    let final_url = response.url().to_string();
    let html = response
        .text()
        .map_err(|error| RuntimeError::Search(error.to_string()))?;

    let document = scraper::Html::parse_document(&html);
    let title_selector = scraper::Selector::parse("title").unwrap();
    let content_selector = scraper::Selector::parse("article, main, body").unwrap();
    let title = document
        .select(&title_selector)
        .next()
        .map(|node| node.text().collect::<String>().trim().to_string())
        .unwrap_or_default();
    let text = document
        .select(&content_selector)
        .next()
        .map(|node| node.text().collect::<Vec<_>>().join(" "))
        .unwrap_or_default()
        .split_whitespace()
        .collect::<Vec<_>>()
        .join(" ");
    let text = text.chars().take(20_000).collect::<String>();

    let snippet = sources
        .get(&source_id)
        .map(|source| source.snippet.clone())
        .unwrap_or_default();
    sources.insert(
        source_id.clone(),
        SourceRecord {
            source_id: source_id.clone(),
            title: title.clone(),
            url: final_url.clone(),
            snippet,
            text: Some(text.clone()),
        },
    );

    Ok(json!({
        "source_id": source_id,
        "url": final_url,
        "title": title,
        "text": text,
        "truncated": text.len() == 20_000
    }))
}

fn list_sources(sources: &HashMap<String, SourceRecord>) -> Value {
    let mut records: Vec<&SourceRecord> = sources.values().collect();
    records.sort_by(|left, right| left.source_id.cmp(&right.source_id));
    json!({"sources": records})
}

fn cite_sources(args: &Value, sources: &HashMap<String, SourceRecord>) -> Result<Value, RuntimeError> {
    let source_ids = args
        .get("source_ids")
        .and_then(Value::as_array)
        .ok_or_else(|| RuntimeError::MissingArgument("source_ids".into()))?;
    let mut citations = Vec::new();
    for source_id in source_ids {
        let source_id = source_id
            .as_str()
            .ok_or_else(|| RuntimeError::InvalidArgument("source_ids deve conter strings".into()))?;
        let source = sources
            .get(source_id)
            .ok_or_else(|| RuntimeError::InvalidArgument(format!("fonte desconhecida: {source_id}")))?;
        citations.push(json!({
            "source_id": source.source_id,
            "citation": format!("[{}] {} — {}", source.source_id, source.title, source.url),
            "title": source.title,
            "url": source.url
        }));
    }
    Ok(json!({"citations": citations}))
}

fn execute(
    call: ToolCall,
    sources: &mut HashMap<String, SourceRecord>,
    next_id: &mut u64,
    workspace: &Path,
) -> Result<Value, RuntimeError> {
    match call.tool.as_str() {
        "search_web" => search_web(&call.arguments, sources, next_id),
        "open_page" => open_page(&call.arguments, sources, next_id),
        "list_sources" => Ok(list_sources(sources)),
        "cite_sources" => cite_sources(&call.arguments, sources),
        "list_files" => list_files(&call.arguments, workspace),
        "read_file" => read_file(&call.arguments, workspace),
        "search_files" => search_files(&call.arguments, workspace),
        "create_directory" => create_directory(&call.arguments, workspace),
        "create_file" => create_file(&call.arguments, workspace),
        "edit_file" => edit_file(&call.arguments, workspace),
        "inspect_media" => inspect_media(&call.arguments, workspace),
        "extract_document_text" => extract_document_text(&call.arguments, workspace),
        "list_tools" => Ok(json!({
            "tools": [
                {
                    "name": "search_web",
                    "description": "Pesquisa informações atuais na internet.",
                    "arguments": {"query": "string obrigatório"}
                },
                {
                    "name": "open_page",
                    "description": "Abre uma página HTTP(S) e extrai seu texto.",
                    "arguments": {"url": "string obrigatório", "source_id": "string opcional"}
                },
                {
                    "name": "list_tools",
                    "description": "Lista as ferramentas disponíveis.",
                    "arguments": {}
                },
                {
                    "name": "list_sources",
                    "description": "Lista as fontes coletadas nesta sessão.",
                    "arguments": {}
                },
                {
                    "name": "cite_sources",
                    "description": "Gera citações para fontes coletadas nesta sessão.",
                    "arguments": {"source_ids": "array de strings obrigatório"}
                },
                {
                    "name": "list_files",
                    "description": "Lista arquivos e diretórios dentro do workspace autorizado.",
                    "arguments": {"path": "string opcional"}
                },
                {
                    "name": "read_file",
                    "description": "Lê um arquivo pequeno dentro do workspace autorizado.",
                    "arguments": {"path": "string obrigatório"}
                },
                {
                    "name": "search_files",
                    "description": "Pesquisa texto dentro de arquivos do workspace autorizado.",
                    "arguments": {"query": "string obrigatório"}
                },
                {
                    "name": "set_workspace",
                    "description": "Seleciona um diretório local existente como workspace atual.",
                    "arguments": {"path": "caminho absoluto obrigatório"}
                },
                {
                    "name": "create_directory",
                    "description": "Cria uma pasta dentro do workspace autorizado.",
                    "arguments": {"path": "caminho relativo obrigatório"}
                },
                {
                    "name": "create_file",
                    "description": "Cria um arquivo novo dentro do workspace autorizado.",
                    "arguments": {"path": "caminho relativo obrigatório", "content": "conteúdo obrigatório"}
                },
                {
                    "name": "edit_file",
                    "description": "Substitui exatamente um trecho existente de um arquivo.",
                    "arguments": {"path": "caminho obrigatório", "old_text": "trecho atual", "new_text": "novo trecho"}
                },
                {
                    "name": "inspect_media",
                    "description": "Inspeciona tipo e tamanho de uma mídia local.",
                    "arguments": {"path": "caminho obrigatório"}
                },
                {
                    "name": "extract_document_text",
                    "description": "Extrai texto de TXT, Markdown, JSON ou PDF local.",
                    "arguments": {"path": "caminho obrigatório"}
                }
            ]
        })),
        other => Err(RuntimeError::UnknownTool(other.to_string())),
    }
}

fn main() {
    if std::env::args().any(|argument| argument == "--web") {
        run_web_server();
        return;
    }

    let stdin = io::stdin();
    let stdout = io::stdout();
    let mut output = stdout.lock();
    let mut sources = HashMap::new();
    let mut next_source_id = 1;
    let workspace = std::env::current_dir().expect("não foi possível descobrir o workspace");

    for line in stdin.lock().lines() {
        let started = Instant::now();
        let line = match line {
            Ok(line) if !line.trim().is_empty() => line,
            Ok(_) => continue,
            Err(error) => {
                let result = ToolResult {
                    ok: false,
                    tool: "runtime".into(),
                    data: None,
                    error: Some(error.to_string()),
                    elapsed_ms: started.elapsed().as_millis(),
                    target_ms: TARGET_REQUEST_MS,
                    max_ms: MAX_REQUEST_MS,
                    performance: performance_label(started.elapsed().as_millis()).into(),
                };
                writeln!(output, "{}", serde_json::to_string(&result).unwrap()).unwrap();
                continue;
            }
        };

        let result = match serde_json::from_str::<ToolCall>(&line) {
            Ok(call) => {
                let tool = call.tool.clone();
                match execute(call, &mut sources, &mut next_source_id, &workspace) {
                    Ok(data) => ToolResult {
                        ok: true,
                        tool,
                        data: Some(data),
                        error: None,
                        elapsed_ms: 0,
                        target_ms: TARGET_REQUEST_MS,
                        max_ms: MAX_REQUEST_MS,
                        performance: "pending".into(),
                    },
                    Err(error) => ToolResult {
                        ok: false,
                        tool,
                        data: None,
                        error: Some(error.to_string()),
                        elapsed_ms: 0,
                        target_ms: TARGET_REQUEST_MS,
                        max_ms: MAX_REQUEST_MS,
                        performance: "pending".into(),
                    },
                }
            }
            Err(error) => ToolResult {
                ok: false,
                tool: "unknown".into(),
                data: None,
                error: Some(format!("JSON inválido: {error}")),
                elapsed_ms: started.elapsed().as_millis(),
                target_ms: TARGET_REQUEST_MS,
                max_ms: MAX_REQUEST_MS,
                performance: performance_label(started.elapsed().as_millis()).into(),
            },
        };

        let elapsed_ms = started.elapsed().as_millis();
        let result = ToolResult {
            elapsed_ms,
            target_ms: TARGET_REQUEST_MS,
            max_ms: MAX_REQUEST_MS,
            performance: performance_label(elapsed_ms).into(),
            ..result
        };

        writeln!(output, "{}", serde_json::to_string(&result).unwrap()).unwrap();
        output.flush().unwrap();
    }
}

const WEB_UI: &str = r##"<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>IA Local do Zero</title>
  <style>
    :root { color-scheme: dark; font-family: Inter, ui-sans-serif, system-ui, sans-serif; }
    * { box-sizing: border-box; }
    body { margin: 0; min-height: 100vh; background: #212121; color: #ececec; }
    .app { display: flex; min-height: 100vh; }
    aside { width: 274px; display: flex; flex-direction: column; padding: 14px 12px; background: #171717; border-right: 1px solid #2b2b2b; }
    .brand { display: flex; align-items: center; gap: 10px; padding: 8px 9px 16px; font-weight: 700; font-size: 16px; }
    .logo { width: 32px; height: 32px; display: grid; place-items: center; border-radius: 10px; background: linear-gradient(135deg, #6366f1, #06b6d4); color: white; }
    .new-chat { width: 100%; padding: 11px 12px; border: 1px solid #4b4b4b; border-radius: 9px; background: #252525; color: #fff; text-align: left; cursor: pointer; font: inherit; }
    .new-chat:hover { background: #303030; }
    .sidebar-action { width: 100%; padding: 9px 10px; border: 0; border-radius: 8px; background: transparent; color: #b8b8b8; text-align: left; cursor: pointer; font: inherit; font-size: 13px; }
    .sidebar-action:hover { background: #242424; color: #fff; }
    .workspace-card { display: flex; align-items: center; gap: 9px; margin-top: 7px; padding: 10px; border: 1px solid #343434; border-radius: 9px; background: #202020; color: #dedede; cursor: pointer; }
    .workspace-card:hover { background: #292929; }
    .workspace-card .side-icon { flex: 0 0 25px; } .workspace-card strong { display: block; font-size: 12px; } .workspace-card small { display: block; margin-top: 3px; color: #8d8d8d; font-size: 10px; }
    .side-title { margin: 22px 8px 8px; color: #8b8b8b; font-size: 11px; text-transform: uppercase; letter-spacing: .08em; }
    .history-list { display: grid; gap: 2px; max-height: 230px; overflow-y: auto; }
    .history-item { padding: 9px 10px; border-radius: 8px; color: #c8c8c8; font-size: 13px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; cursor: pointer; }
    .history-item:hover, .history-item.active { background: #2a2a2a; color: #fff; }
    .history-empty { padding: 4px 8px; color: #4b5563; font-size: 11px; }
    .side-item { display: flex; align-items: center; gap: 10px; padding: 9px 10px; margin: 2px 0; border: 1px solid transparent; border-radius: 9px; color: #cbd5e1; font-size: 13px; cursor: pointer; }
    .side-item.primary { background: #202020; border-color: #303030; }
    .side-item.primary:hover { background: #2a2a2a; }
    .tool-grid { display: grid; grid-template-columns: 1fr; gap: 4px; }
    .side-item:hover { background: #252525; }
    .side-item.active { background: #2f2f2f; border-color: #454545; color: #fff; }
    .side-icon { width: 25px; height: 25px; display: grid; place-items: center; border-radius: 7px; background: #242424; color: #a5b4fc; font-size: 15px; }
    .side-label { display: grid; gap: 2px; } .side-label small { color: #6b7280; font-size: 10px; }
    .local { margin-top: auto; padding: 18px 8px 4px; color: #8b8b8b; font-size: 11px; }
    .dot { display: inline-block; width: 8px; height: 8px; margin-right: 6px; border-radius: 50%; background: #22c55e; }
    main { display: flex; flex: 1; flex-direction: column; min-width: 0; }
    header { display: flex; justify-content: space-between; align-items: center; min-height: 62px; padding: 12px 30px; border-bottom: 1px solid #303030; background: #212121; }
    .title { font-weight: 650; } .subtitle { color: #8b8b8b; font-size: 12px; margin-top: 3px; }
    .header-actions { display: flex; align-items: center; gap: 10px; }
    .model-selector { padding: 7px 10px; border-radius: 8px; color: #d4d4d4; font-size: 13px; }
    .badge { padding: 6px 9px; border: 1px solid #1e40af; border-radius: 999px; color: #93c5fd; font-size: 12px; }
    .live-status { display: flex; align-items: center; gap: 8px; min-height: 28px; padding: 7px 28px; border-bottom: 1px solid #1f2937; color: #9ca3af; font-size: 12px; background: #0e1420; }
    .live-status.busy { color: #c4b5fd; } .live-status.done { color: #86efac; } .live-status.error { color: #fca5a5; }
    .status-dot { width: 8px; height: 8px; flex: 0 0 8px; border-radius: 50%; background: #64748b; }
    .live-status.busy .status-dot { background: #a78bfa; box-shadow: 0 0 0 4px #8b5cf633; animation: pulse 1.2s infinite; }
    @keyframes pulse { 50% { opacity: .35; transform: scale(.8); } }
    .chat { width: min(900px, 100%); flex: 1; margin: 0 auto; padding: 46px 30px 24px; overflow-y: auto; }
    .welcome { text-align: center; margin: 62px auto 58px; color: #a3a3a3; }
    .welcome h1 { color: #f3f4f6; font-size: 30px; font-weight: 650; margin: 0 0 10px; }
    .welcome p { margin: 0; }
    .message { display: flex; gap: 12px; margin: 24px 0; align-items: flex-start; }
    .message > div:not(.avatar) { min-width: 0; }
    .message.user { justify-content: flex-end; }
    .avatar { flex: 0 0 30px; height: 30px; display: grid; place-items: center; border-radius: 9px; background: #1f2937; color: #a5b4fc; font-size: 12px; }
    .message.user .avatar { order: 2; background: #3730a3; color: #e0e7ff; }
    .bubble { max-width: min(680px, 78%); padding: 12px 15px; border-radius: 14px; line-height: 1.55; white-space: pre-wrap; overflow-wrap: anywhere; }
    .message.assistant .bubble { background: transparent; border: 0; padding-left: 0; }
    .message.user .bubble { background: #2f2f2f; color: #f2f2f2; }
    .message.user > div:not(.avatar) { width: fit-content; max-width: min(680px, 78vw); }
    .message.user .bubble { width: fit-content; min-width: 44px; }
    .meta { margin-top: 7px; color: #6b7280; font-size: 11px; }
    .action-message { display: flex; gap: 10px; align-items: flex-start; margin: 16px 0; padding: 10px 12px; border: 1px solid #343434; border-radius: 10px; background: #252525; color: #bdbdbd; font-size: 12px; }
    .action-message.busy { border-color: #4f46e5; } .action-message.done { border-color: #276749; } .action-message.error { border-color: #9b2c2c; }
    .action-icon { width: 22px; height: 22px; display: grid; place-items: center; border-radius: 6px; background: #343434; color: #a5b4fc; }
    .action-body { min-width: 0; flex: 1; } .action-title { color: #e5e5e5; font-weight: 600; } .action-log { margin-top: 4px; color: #9b9b9b; }
    .sources { margin-top: 10px; display: grid; gap: 8px; }
    .source { padding: 10px 12px; border: 1px solid #263247; border-radius: 9px; background: #0f1624; font-size: 13px; }
    .source a { color: #93c5fd; text-decoration: none; } .source small { display: block; color: #9ca3af; margin-top: 4px; }
    .analyze { margin-top: 8px; margin-right: 8px; padding: 5px 9px; border: 1px solid #3730a3; border-radius: 7px; background: #312e81; color: #e0e7ff; cursor: pointer; font-size: 12px; }
    .external { display: inline-block; margin-top: 8px; color: #a5b4fc !important; font-size: 12px; }
    .composer-wrap { position: sticky; bottom: 0; width: min(900px, 100%); padding: 12px 30px 24px; margin: 0 auto; background: linear-gradient(#21212100, #212121 22%); }
    .chips { display: flex; gap: 8px; margin-bottom: 10px; overflow-x: auto; }
    .chip { white-space: nowrap; padding: 7px 10px; border: 1px solid #374151; border-radius: 999px; background: transparent; color: #cbd5e1; cursor: pointer; }
    .composer { display: flex; align-items: flex-end; gap: 8px; padding: 9px; border: 1px solid #4a4a4a; border-radius: 14px; background: #2f2f2f; box-shadow: 0 8px 30px #0005; }
    .attach-wrap { position: relative; flex: 0 0 auto; }
    .attach-button { width: 34px; height: 34px; border: 0; border-radius: 9px; background: transparent; color: #cfcfcf; cursor: pointer; font-size: 22px; }
    .attach-button:hover { background: #454545; }
    .attach-menu { position: absolute; z-index: 5; left: 0; bottom: 42px; display: none; min-width: 190px; padding: 6px; border: 1px solid #4a4a4a; border-radius: 10px; background: #252525; box-shadow: 0 12px 30px #0008; }
    .attach-menu.open { display: grid; gap: 3px; }
    .attach-menu button { padding: 9px 10px; border: 0; border-radius: 7px; background: transparent; color: #e5e5e5; text-align: left; cursor: pointer; font: inherit; font-size: 13px; }
    .attach-menu button:hover { background: #383838; }
    .attachment-list { display: flex; gap: 7px; flex-wrap: wrap; margin-bottom: 8px; }
    .attachment-chip { display: flex; align-items: center; gap: 7px; max-width: 260px; padding: 6px 8px; border: 1px solid #4a4a4a; border-radius: 8px; background: #292929; color: #dedede; font-size: 12px; }
    .attachment-chip span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .attachment-chip button { border: 0; background: transparent; color: #aaa; cursor: pointer; }
    .attachment-preview { width: 28px; height: 28px; object-fit: cover; border-radius: 5px; }
    .attachment-chip.tool { border-color: #4f46e5; color: #c7d2fe; }
    .sidebar-note { margin: 8px 8px; padding: 10px; border: 1px solid #303030; border-radius: 8px; color: #8d8d8d; font-size: 11px; line-height: 1.45; }
    textarea { flex: 1; min-height: 28px; max-height: 140px; resize: vertical; border: 0; outline: 0; background: transparent; color: #fff; padding: 7px; font: inherit; }
    .send { width: 38px; height: 38px; padding: 0; border: 0; border-radius: 10px; background: #4f46e5; color: #fff; cursor: pointer; font-size: 18px; }
    .hint { text-align: center; color: #4b5563; font-size: 11px; margin-top: 9px; }
    .tool-panel { display: none; width: min(850px, 100%); margin: 18px auto 0; padding: 0 28px; }
    .tool-panel.open { display: block; }
    .panel-card { padding: 18px; border: 1px solid #263247; border-radius: 14px; background: #111827; box-shadow: 0 10px 28px #0002; }
    .panel-head { display: flex; justify-content: space-between; gap: 16px; align-items: flex-start; }
    .panel-title { color: #f3f4f6; font-weight: 700; }
    .panel-description { color: #9ca3af; font-size: 12px; margin-top: 4px; }
    .panel-close { border: 0; background: transparent; color: #9ca3af; cursor: pointer; font-size: 18px; }
    .panel-form { display: flex; gap: 8px; margin-top: 14px; }
    .panel-form input { flex: 1; min-width: 0; padding: 10px 12px; border: 1px solid #374151; border-radius: 9px; outline: 0; background: #0b0f19; color: #fff; font: inherit; }
    .panel-form textarea { flex: 1; min-height: 90px; max-height: 180px; padding: 10px 12px; border: 1px solid #374151; border-radius: 9px; outline: 0; background: #0b0f19; color: #fff; font: inherit; resize: vertical; }
    .panel-form button, .panel-action { padding: 10px 13px; border: 0; border-radius: 9px; background: #4f46e5; color: #fff; cursor: pointer; font: inherit; }
    .panel-result { display: grid; gap: 8px; margin-top: 14px; max-height: 330px; overflow: auto; }
    .panel-empty { color: #6b7280; font-size: 13px; padding: 8px 0; }
    .panel-result .source { display: block; }
    .entry { display: flex; justify-content: space-between; gap: 12px; align-items: center; padding: 10px 12px; border: 1px solid #263247; border-radius: 9px; background: #0f1624; font-size: 13px; }
    .entry small { display: block; color: #6b7280; margin-top: 3px; }
    .entry button { padding: 5px 8px; border: 1px solid #374151; border-radius: 7px; background: #1f2937; color: #cbd5e1; cursor: pointer; }
    @media (max-width: 720px) { aside { display: none; } header { padding: 15px 18px; } .chat, .composer-wrap { padding-left: 16px; padding-right: 16px; } }
  </style>
</head>
<body>
  <div class="app">
    <aside>
      <div class="brand"><div class="logo">✦</div>IA Local do Zero</div>
      <button class="new-chat" onclick="newChat()">＋ Nova conversa</button>
      <button class="sidebar-action" onclick="focusConversationSearch()">⌕ Pesquisar conversas</button>
      <div class="workspace-card" onclick="openTool('workspace')"><span class="side-icon">▦</span><span><strong>Workspace</strong><small>Selecionar projeto local</small></span></div>
      <div class="side-title">Recentes</div>
      <div class="history-list" id="history-list"><div class="history-empty">Nenhuma conversa salva</div></div>
      <div class="side-title">Ferramentas</div>
      <div class="sidebar-note">Use o botão <strong>＋</strong> na barra de escrita para anexar arquivos ou ativar uma ferramenta manualmente. O assistente também escolhe a ferramenta quando a mensagem pedir.</div>
      <div class="local"><span class="dot"></span>Runtime local online<br><span style="margin-left:14px">Rust · 127.0.0.1:3000</span></div>
    </aside>
    <main>
      <header><div><div class="title">Assistente local</div><div class="subtitle">Conversa e ferramentas sob seu controle</div></div><div class="header-actions"><div class="model-selector">IA Local do Zero⌄</div><div class="badge">● Local</div></div></header>
      <div class="live-status" id="live-status" aria-live="polite"><span class="status-dot"></span><span id="live-status-text">Pronto para receber uma requisição.</span></div>
      <section class="tool-panel" id="tool-panel"></section>
      <section class="chat" id="chat">
        <div class="welcome" id="welcome"><h1>Como posso ajudar?</h1><p>Pesquise na internet ou explore as fontes coletadas nesta sessão.</p></div>
        <div class="message assistant"><div class="avatar">✦</div><div><div class="bubble">Estou conectado ao runtime local. Meu modelo próprio está em treinamento e só libera respostas quando encontra uma resposta confiável. Também já consigo pesquisar, abrir páginas, ler o workspace e rastrear fontes.</div><div class="meta">Runtime Rust · modelo próprio experimental</div></div></div>
      </section>
      <div class="composer-wrap">
        <div class="attachment-list" id="attachment-list"></div><div class="composer"><div class="attach-wrap"><button class="attach-button" onclick="toggleAttachmentMenu()" aria-label="Anexar ou ativar ferramenta">＋</button><div class="attach-menu" id="attach-menu"><button onclick="selectTool('search_web')">⌕ Pesquisar na internet</button><button onclick="selectTool('open_page')">↗ Analisar URL</button><button onclick="selectTool('list_files')">▦ Explorar workspace</button><button onclick="selectTool('search_files')">⌘ Buscar no código</button><button onclick="pickAttachment('document')">▤ Anexar documento</button><button onclick="pickAttachment('directory')">▦ Anexar pasta</button><button onclick="pickAttachment('image')">▧ Anexar imagem</button><button onclick="pickAttachment('audio')">◉ Anexar áudio</button><button onclick="pickAttachment('video')">▣ Anexar vídeo</button></div><input id="attachment-input" type="file" hidden onchange="handleAttachments(event)"></div><textarea id="message" rows="1" placeholder="Escreva uma mensagem..."></textarea><button class="send" onclick="send()">↑</button></div>
        <div class="hint">As consultas de pesquisa saem para a internet. O runtime e o histórico de fontes ficam locais.</div>
      </div>
    </main>
  </div>
  <script>
    const chat = document.getElementById('chat'), input = document.getElementById('message');
    let conversation = [];
    let currentConversationId = null;
    let activityCursor = 0;
    let activityTimer = null;
    let attachments = [];
    let selectedTool = null;
    const liveActions = new Map();
    const HISTORY_KEY = 'ia-local-zero-conversations-v1';
    function setLiveStatus(event) { const bar = document.getElementById('live-status'), text = document.getElementById('live-status-text'); if (!bar || !text) return; bar.className = 'live-status ' + (event.done ? (event.phase === 'error' ? 'error' : 'done') : 'busy'); text.textContent = event.message + (event.elapsed_ms != null ? ` · ${event.elapsed_ms} ms` : ''); }
    function requestId(prefix) { return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2,8)}`; }
    function startAction(operation, title) { const row = document.createElement('div'); row.className = 'action-message busy'; row.dataset.operation = operation; row.innerHTML = `<div class="action-icon">✦</div><div class="action-body"><div class="action-title">${escapeHtml(title)}</div><div class="action-log">Aguardando atualização…</div></div>`; chat.appendChild(row); chat.scrollTop = chat.scrollHeight; liveActions.set(operation, row); return row; }
    function updateAction(event) { const row = liveActions.get(event.operation); if (!row) return; row.className = `action-message ${event.done ? (event.phase === 'error' ? 'error' : 'done') : 'busy'}`; const log = row.querySelector('.action-log'); if (log) log.textContent = event.message + (event.elapsed_ms != null ? ` · ${event.elapsed_ms} ms` : ''); chat.scrollTop = chat.scrollHeight; if (event.done) setTimeout(() => liveActions.delete(event.operation), 5000); }
    async function pollActivity() { try { const response = await fetch(`/api/activity?after=${activityCursor}`, {cache:'no-store'}); if (!response.ok) return; const data = await response.json(); for (const event of (data.events || [])) { activityCursor = Math.max(activityCursor, event.id); setLiveStatus(event); updateAction(event); } } catch (_) {} }
    activityTimer = setInterval(pollActivity, 350); pollActivity();
    function readHistory() { try { return JSON.parse(localStorage.getItem(HISTORY_KEY) || '[]'); } catch (_) { return []; } }
    function toggleAttachmentMenu() { document.getElementById('attach-menu')?.classList.toggle('open'); }
    function selectTool(tool) { selectedTool = tool; document.getElementById('attach-menu')?.classList.remove('open'); renderAttachments(); }
    function clearSelectedTool() { selectedTool = null; renderAttachments(); }
    function pickAttachment(kind) { const input = document.getElementById('attachment-input'); input.dataset.kind = kind; input.multiple = kind === 'directory'; input.webkitdirectory = kind === 'directory'; input.accept = kind === 'directory' ? '' : kind === 'document' ? '.txt,.md,.json,.pdf,.csv,.py,.js,.rs,.html,.css' : (kind + '/*'); input.click(); document.getElementById('attach-menu')?.classList.remove('open'); }
    function renderAttachments() { const target = document.getElementById('attachment-list'); if (!target) return; const tools = selectedTool ? `<div class="attachment-chip tool"><b>✦</b><span>Ferramenta: ${escapeHtml({search_web:'pesquisa na internet',open_page:'analisar URL',list_files:'workspace',search_files:'buscar no código'}[selectedTool] || selectedTool)}</span><button onclick="clearSelectedTool()" aria-label="Remover ferramenta">×</button></div>` : ''; target.innerHTML = tools + attachments.map((item, index) => `<div class="attachment-chip">${item.url ? `<img class="attachment-preview" src="${item.url}" alt="">` : `<b>${item.kind === 'directory' ? '▦' : item.kind === 'document' ? '▤' : item.kind === 'image' ? '▧' : item.kind === 'audio' ? '◉' : '▣'}</b>`}<span title="${escapeHtml(item.name)}">${escapeHtml(item.name)}</span><button onclick="removeAttachment(${index})" aria-label="Remover anexo">×</button></div>`).join(''); }
    function removeAttachment(index) { const item = attachments.splice(index, 1)[0]; if (item?.url) URL.revokeObjectURL(item.url); renderAttachments(); }
    function handleAttachments(event) { const files = Array.from(event.target.files || []); const kind = event.target.dataset.kind || 'document'; if (kind === 'directory' && files.length) addDirectoryAttachment(files); else files.forEach(file => addAttachment(file, kind)); event.target.value = ''; event.target.webkitdirectory = false; renderAttachments(); }
    function addDirectoryAttachment(files) { const root = (files[0].webkitRelativePath || files[0].name).split('/')[0]; const item = {kind:'directory', name:`Pasta: ${root}/`, size:files.reduce((sum, file) => sum + file.size, 0), text:null, url:null}; attachments.push(item); const readable = files.filter(file => file.size <= 64 * 1024 && /\.(txt|md|json|csv|py|js|ts|rs|html|css|toml|yaml|yml|sql)$/i.test(file.name)).slice(0, 80); Promise.all(readable.map(file => new Promise(resolve => { const reader = new FileReader(); reader.onload = () => resolve(`[Arquivo: ${file.webkitRelativePath || file.name}]\n${String(reader.result || '').slice(0, 12000)}`); reader.onerror = () => resolve(''); reader.readAsText(file); }))).then(parts => { item.text = parts.filter(Boolean).join('\n\n').slice(0, 120000); renderAttachments(); }); }
    function addAttachment(file, kind) { const item = {file, kind, name:file.name || `anexo-${Date.now()}`, size:file.size || 0, url: kind === 'image' ? URL.createObjectURL(file) : null, text:null}; if (kind === 'document' && file.size <= 256 * 1024 && !/\.pdf$/i.test(file.name)) { const reader = new FileReader(); reader.onload = () => { item.text = String(reader.result || '').slice(0, 120000); renderAttachments(); }; reader.readAsText(file); } attachments.push(item); }
    input.addEventListener('paste', event => { const files = Array.from(event.clipboardData?.files || []); if (!files.length) return; event.preventDefault(); files.forEach(file => addAttachment(file, file.type.startsWith('image/') ? 'image' : file.type.startsWith('audio/') ? 'audio' : file.type.startsWith('video/') ? 'video' : 'document')); renderAttachments(); });
    function attachmentContext() { return attachments.map(item => item.text ? `[Anexo: ${item.name}]\n${item.text}` : `[Anexo ${item.kind}: ${item.name} · ${item.size} bytes]`).join('\n\n'); }
    function writeHistory(items) { localStorage.setItem(HISTORY_KEY, JSON.stringify(items.slice(0, 30))); }
    function saveConversation() {
      if (!conversation.some(message => message.role === 'user')) return;
      const first = conversation.find(message => message.role === 'user');
      const title = (first?.content || 'Nova conversa').replace(/\s+/g, ' ').trim().slice(0, 42);
      const items = readHistory();
      const record = {id: currentConversationId || String(Date.now()), title, updatedAt: Date.now(), messages: conversation};
      const index = items.findIndex(item => item.id === record.id);
      if (index >= 0) items[index] = record; else items.unshift(record);
      items.sort((a, b) => b.updatedAt - a.updatedAt);
      currentConversationId = record.id;
      writeHistory(items); renderHistory();
    }
    function renderHistory(filter='') {
      const list = document.getElementById('history-list'); const query = filter.trim().toLowerCase(); const items = readHistory().filter(item => !query || item.title.toLowerCase().includes(query));
      list.innerHTML = items.length ? items.map(item => `<div class="history-item ${item.id === currentConversationId ? 'active' : ''}" title="${escapeHtml(item.title)}" onclick="loadConversation('${escapeHtml(item.id)}')">${escapeHtml(item.title)}</div>`).join('') : '<div class="history-empty">Nenhuma conversa salva</div>';
    }
    function focusConversationSearch() { const query = window.prompt('Pesquisar nas conversas salvas:'); if (query !== null) renderHistory(query); }
    function loadConversation(id) {
      const item = readHistory().find(entry => entry.id === id); if (!item) return;
      saveConversation(); currentConversationId = item.id; conversation = item.messages || []; chat.innerHTML = '';
      conversation.forEach(message => addMessage(message.role === 'user' ? 'user' : 'assistant', message.content, message.role === 'user' ? '' : 'histórico local'));
      renderHistory();
    }
    function addMessage(kind, text, meta, cards) {
      document.getElementById('welcome')?.remove();
      const row = document.createElement('div'); row.className = 'message ' + kind;
      const avatar = document.createElement('div'); avatar.className = 'avatar'; avatar.textContent = kind === 'user' ? 'Você' : '✦';
      const body = document.createElement('div'); const bubble = document.createElement('div'); bubble.className = 'bubble'; bubble.textContent = text; body.appendChild(bubble);
      if (cards?.length) { const sources = document.createElement('div'); sources.className = 'sources'; cards.forEach(card => { const item = document.createElement('div'); item.className = 'source'; const link = document.createElement('a'); link.href = card.url; link.target = '_blank'; link.rel = 'noreferrer'; link.textContent = `[${card.source_id}] ${card.title || 'Fonte'}`; const small = document.createElement('small'); small.textContent = card.snippet || (card.text ? card.text.slice(0, 360) + (card.text.length > 360 ? '…' : '') : ''); item.append(link, small); if (!card.text) { const analyze = document.createElement('button'); analyze.className = 'analyze'; analyze.textContent = 'Analisar aqui'; analyze.onclick = () => call('open_page', {url: card.url, source_id: card.source_id}); item.appendChild(analyze); } const external = document.createElement('a'); external.className = 'external'; external.href = card.url; external.target = '_blank'; external.rel = 'noreferrer'; external.textContent = 'Abrir no navegador ↗'; item.appendChild(external); sources.appendChild(item); }); body.appendChild(sources); }
      const foot = document.createElement('div'); foot.className = 'meta'; foot.textContent = meta || ''; body.appendChild(foot); row.append(avatar, body); chat.appendChild(row); chat.scrollTop = chat.scrollHeight;
    }
    const panel = document.getElementById('tool-panel');
    const toolNames = {
      search: ['Pesquisar na internet', 'Consulte fontes externas e mantenha os resultados nesta área.'],
      url: ['Analisar uma página', 'Abra uma URL, extraia o texto e registre a fonte para esta sessão.'],
      workspace: ['Explorar workspace', 'Escolha qualquer diretório local existente para navegar e editar.'],
      code: ['Buscar no código', 'Encontre ocorrências em arquivos pequenos do workspace.'],
      sources: ['Fontes desta sessão', 'Revise, abra e acompanhe as fontes já coletadas.'],
      media: ['Multimídia', 'Packs locais para documentos, imagens, áudio e vídeo.']
    };
    function escapeHtml(value) { return String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[c])); }
    function panelShell(name, form, body='') { const [title, description] = toolNames[name]; panel.innerHTML = `<div class="panel-card"><div class="panel-head"><div><div class="panel-title">${title}</div><div class="panel-description">${description}</div></div><button class="panel-close" onclick="closeTool()">×</button></div>${form}<div class="panel-result" id="panel-result">${body}</div></div>`; panel.classList.add('open'); document.querySelectorAll('.side-item').forEach(item => item.classList.toggle('active', item.dataset.tool === name)); }
    function closeTool() { panel.classList.remove('open'); panel.innerHTML = ''; document.querySelectorAll('.side-item').forEach(item => item.classList.remove('active')); }
    function openTool(name) {
      if (name === 'search') panelShell(name, `<div class="panel-form"><input id="panel-input" placeholder="Ex.: novidades do Rust" autofocus><button onclick="runPanelSearch()">Pesquisar</button></div>`);
      else if (name === 'url') panelShell(name, `<div class="panel-form"><input id="panel-input" placeholder="https://exemplo.com" autofocus><button onclick="runPanelUrl()">Analisar</button></div>`);
      else if (name === 'workspace') panelShell(name, `<div class="panel-form"><input id="panel-input" placeholder="caminho absoluto do projeto ou relativo ao workspace" autofocus><button onclick="runPanelWorkspace()">Listar</button></div><div class="panel-form"><button class="panel-action" onclick="showCreateFile()">＋ Novo arquivo</button><button class="panel-action" onclick="showCreateDirectory()">＋ Nova pasta</button></div><div class="panel-description" style="margin-top:10px">O diretório informado como caminho absoluto passa a ser a nova raiz do workspace.</div>`);
      else if (name === 'code') panelShell(name, `<div class="panel-form"><input id="panel-input" placeholder="termo ou trecho de código" autofocus><button onclick="runPanelCode()">Buscar</button></div>`);
      else if (name === 'sources') { panelShell(name, '', '<div class="panel-empty">Carregando fontes…</div>'); runPanelSources(); }
      else if (name === 'media') panelShell(name, `<div class="panel-form"><input id="media-path" placeholder="caminho local da mídia ou documento"><button onclick="inspectPanelMedia()">Inspecionar</button><button onclick="extractPanelDocument()">Extrair PDF</button></div>`, '<div class="entry"><div>▧ Documentos<small>PDF, TXT, Markdown e JSON · ativo</small></div></div><div class="entry"><div>▧ Visão<small>Imagens · adaptador pronto para backend visual</small></div></div><div class="entry"><div>◉ Áudio<small>WAV, MP3, OGG, FLAC · adaptador pronto</small></div></div><div class="entry"><div>▣ Vídeo<small>MP4, MKV, WebM, MOV · adaptador pronto</small></div></div><div class="entry"><div>✦ Geração<small>Imagem, áudio e vídeo · planejada</small></div></div>');
      document.getElementById('panel-input')?.addEventListener('keydown', event => { if (event.key === 'Enter') { event.preventDefault(); ({search:runPanelSearch,url:runPanelUrl,workspace:runPanelWorkspace,code:runPanelCode}[name] || (()=>{}))(); } });
    }
    function renderPanelSources(cards) { const target = document.getElementById('panel-result'); if (!target) return; if (!cards?.length) { target.innerHTML = '<div class="panel-empty">Nenhuma fonte registrada nesta sessão.</div>'; return; } target.innerHTML = cards.map(card => `<div class="source"><a href="${escapeHtml(card.url || '#')}" target="_blank" rel="noreferrer">[${escapeHtml(card.source_id)}] ${escapeHtml(card.title || 'Fonte')}</a><small>${escapeHtml(card.snippet || (card.text || '').slice(0, 360))}</small>${card.text ? '' : `<button class="analyze" onclick="runPanelOpen('${escapeHtml(card.source_id)}','${escapeHtml(card.url || '')}')">Analisar aqui</button>`}</div>`).join(''); }
    async function toolRequest(tool, arguments_, request_id=null) { const response = await fetch('/api/tool-call', {method:'POST', headers:{'content-type':'application/json'}, body:JSON.stringify({tool, arguments:arguments_, request_id:request_id || requestId('panel')})}); return await response.json(); }
    async function runPanelSearch() { const query = document.getElementById('panel-input')?.value.trim(); if (!query) return; const target = document.getElementById('panel-result'); target.innerHTML = '<div class="panel-empty">Pesquisando…</div>'; const data = await toolRequest('search_web', {query}); if (!data.ok) { target.textContent = data.error; return; } renderPanelSources(data.data.results); }
    async function runPanelUrl() { const url = document.getElementById('panel-input')?.value.trim(); if (!url) return; const target = document.getElementById('panel-result'); target.innerHTML = '<div class="panel-empty">Abrindo e extraindo texto…</div>'; const data = await toolRequest('open_page', {url}); if (!data.ok) { target.textContent = data.error; return; } target.innerHTML = `<div class="source"><a href="${escapeHtml(data.data.url || url)}" target="_blank" rel="noreferrer">${escapeHtml(data.data.title || 'Página analisada')}</a><small>${escapeHtml((data.data.text || '').slice(0, 1800))}</small></div>`; }
    async function runPanelWorkspace(path) { let value = typeof path === 'string' ? path : (document.getElementById('panel-input')?.value.trim() || ''); const target = document.getElementById('panel-result'); if (target) target.innerHTML = '<div class="panel-empty">Lendo workspace…</div>'; if (value.startsWith('/')) { const selected = await toolRequest('set_workspace', {path:value}); if (!selected.ok) { if (target) target.textContent = selected.error; return; } value = ''; } const data = await toolRequest('list_files', {path:value}); if (!data.ok) { if (target) target.textContent = data.error; return; } if (!data.data.entries.length) { target.innerHTML = '<div class="panel-empty">Diretório vazio.</div>'; return; } target.innerHTML = `<div class="panel-description">Workspace atual: ${escapeHtml(data.data.workspace)}</div>` + data.data.entries.map(entry => `<div class="entry"><div>${entry.kind === 'directory' ? '▣' : '▤'} ${escapeHtml(entry.name)}<small>${entry.kind} · ${entry.bytes} bytes</small></div>${entry.kind === 'directory' ? `<button onclick="runPanelWorkspace('${escapeHtml((value ? value + '/' : '') + entry.name)}')">Abrir</button>` : `<button onclick="runPanelRead('${escapeHtml((value ? value + '/' : '') + entry.name)}')">Ler</button>`}</div>`).join(''); }
    function showCreateFile() { const target = document.getElementById('panel-result'); target.innerHTML = '<div class="panel-form"><input id="create-file-path" placeholder="caminho, ex.: app/main.py"><textarea id="create-file-content" placeholder="conteúdo inicial"></textarea><button onclick="runCreateFile()">Criar</button></div>'; }
    function showCreateDirectory() { const target = document.getElementById('panel-result'); target.innerHTML = '<div class="panel-form"><input id="create-directory-path" placeholder="caminho, ex.: app/src"><button onclick="runCreateDirectory()">Criar pasta</button></div>'; }
    function confirmMutation(action, path) { return window.confirm(`${action}\n\n${path}\n\nA operação só será executada depois desta confirmação.`); }
    async function runCreateFile() { const path = document.getElementById('create-file-path')?.value.trim(); const content = document.getElementById('create-file-content')?.value || ''; if (!path || !confirmMutation('Criar este arquivo?', path)) return; const data = await toolRequest('create_file', {path, content}); document.getElementById('panel-result').innerHTML = data.ok ? `<div class="panel-empty">Arquivo criado: ${escapeHtml(data.data.path)}</div>` : `<div class="panel-empty">${escapeHtml(data.error)}</div>`; }
    async function runCreateDirectory() { const path = document.getElementById('create-directory-path')?.value.trim(); if (!path || !confirmMutation('Criar esta pasta?', path)) return; const data = await toolRequest('create_directory', {path}); document.getElementById('panel-result').innerHTML = data.ok ? `<div class="panel-empty">Pasta criada: ${escapeHtml(data.data.path)}</div>` : `<div class="panel-empty">${escapeHtml(data.error)}</div>`; }
    async function runPanelRead(path) { const target = document.getElementById('panel-result'); target.innerHTML = '<div class="panel-empty">Lendo arquivo…</div>'; const data = await toolRequest('read_file', {path}); if (!data.ok) { target.textContent = data.error; return; } target.innerHTML = `<div class="source"><strong>${escapeHtml(data.data.path)}</strong><small>${escapeHtml(data.data.content.slice(0, 5000))}</small></div>`; }
    async function runPanelCode() { const query = document.getElementById('panel-input')?.value.trim(); if (!query) return; const target = document.getElementById('panel-result'); target.innerHTML = '<div class="panel-empty">Buscando no código…</div>'; const data = await toolRequest('search_files', {query}); if (!data.ok) { target.textContent = data.error; return; } target.innerHTML = data.data.matches.length ? data.data.matches.map(match => `<div class="entry"><div>${escapeHtml(match.path)}<small>linha ${match.line} · ${escapeHtml(match.text)}</small></div><button onclick="runPanelRead('${escapeHtml(match.path)}')">Ler</button></div>`).join('') : '<div class="panel-empty">Nenhuma ocorrência encontrada.</div>'; }
    async function runPanelSources() { const data = await toolRequest('list_sources', {}); if (data.ok) renderPanelSources(data.data.sources); else document.getElementById('panel-result').textContent = data.error; }
    async function runPanelOpen(source_id, url) { const data = await toolRequest('open_page', {source_id, url}); if (data.ok) renderPanelSources([data.data]); }
    async function inspectPanelMedia() { const path = document.getElementById('media-path')?.value.trim(); if (!path) return; const target = document.getElementById('panel-result'); target.innerHTML = '<div class="panel-empty">Inspecionando mídia…</div>'; const data = await toolRequest('inspect_media', {path}); if (!data.ok) { target.textContent = data.error; return; } target.innerHTML = `<div class="entry"><div>${escapeHtml(data.data.path)}<small>${escapeHtml(data.data.media_type)} · ${data.data.extension || 'sem extensão'} · ${data.data.bytes} bytes</small></div></div>`; }
    async function extractPanelDocument() { const path = document.getElementById('media-path')?.value.trim(); if (!path) return; const target = document.getElementById('panel-result'); target.innerHTML = '<div class="panel-empty">Extraindo documento…</div>'; const data = await toolRequest('extract_document_text', {path}); if (!data.ok) { target.textContent = data.error; return; } target.innerHTML = `<div class="source"><strong>${escapeHtml(data.data.path)}</strong><small>${escapeHtml(data.data.text.slice(0, 6000))}</small></div>`; }
    async function runChatTool(tool, arguments_, label) { if (['create_file','create_directory','edit_file'].includes(tool) && !confirmMutation(label + '?', arguments_.path || 'arquivo selecionado')) return; const id = requestId('tool'); const operation = `tool:${tool}:${id}`; startAction(operation, label); const data = await toolRequest(tool, arguments_, id); addMessage('assistant', data.ok ? `${label}: ${JSON.stringify(data.data)}` : (data.error || 'A operação falhou.'), `${tool} · ${data.ok ? 'concluído' : 'erro'}`); }
    async function call(tool, arguments_) {
      const started = performance.now(); const id = requestId('tool'); const operation = `tool:${tool}:${id}`; startAction(operation, `Executando ${tool}`);
      const response = await fetch('/api/tool-call', {method:'POST', headers:{'content-type':'application/json'}, body:JSON.stringify({tool, arguments:arguments_, request_id:id})});
      const data = await response.json(); const elapsed = data.elapsed_ms ?? Math.round(performance.now() - started);
      if (!data.ok) { addMessage('assistant', data.error || 'A ferramenta falhou.', `${tool} · erro · ${elapsed} ms`); return data; }
      if (tool === 'search_web') addMessage('assistant', `Encontrei ${data.data.result_count} resultado(s) para “${data.data.query}”.`, `${tool} · ${elapsed} ms`, data.data.results);
      else if (tool === 'list_sources') addMessage('assistant', `${data.data.sources.length} fonte(s) registrada(s) nesta sessão.`, `${tool} · ${elapsed} ms`, data.data.sources);
      else if (tool === 'open_page') addMessage('assistant', `${data.data.title || 'Página aberta.'}\n\n${(data.data.text || '').slice(0, 900)}`, `${tool} · ${elapsed} ms`, [data.data]);
      else addMessage('assistant', JSON.stringify(data.data, null, 2), `${tool} · ${elapsed} ms`);
      return data;
    }
    async function chatModel(value) { conversation.push({role:'user', content:value}); const started = performance.now(); const id = requestId('chat'); const operation = `chat:${id}`; startAction(operation, 'Processando mensagem'); const response = await fetch('/api/chat', {method:'POST', headers:{'content-type':'application/json'}, body:JSON.stringify({messages:conversation, request_id:id})}); const data = await response.json(); const elapsed = data.elapsed_ms ?? Math.round(performance.now() - started); if (!data.ok) { addMessage('assistant', data.error || 'Modelo próprio indisponível.', `modelo · erro · ${elapsed} ms`); return; } conversation.push({role:'assistant', content:data.text}); addMessage('assistant', data.text || 'O modelo não gerou texto.', `modelo próprio · ${data.intent || 'unknown'} · ${data.backend || data.model || 'local'} · ${elapsed} ms`); saveConversation(); }
    function autoTool(value) { const lower = value.toLowerCase(); if (/^https?:\/\//i.test(value)) return 'open_page'; if (/^\/(pesquisar|pesquisa)\s+/.test(lower)) return 'search_web'; if (/\b(pesquise|pesquisar|notícia|noticias|atual|agora|hoje|preço|preco|cotação|cotacao|clima|versão atual|versao atual)\b/.test(lower)) return 'search_web'; if (lower.startsWith('/arquivos')) return 'list_files'; if (lower.startsWith('/buscar ')) return 'search_files'; return null; }
    function send() { const value = input.value.trim(); const attachmentText = attachmentContext(); if (!value && !attachmentText) return; const visible = [value, attachments.length ? `📎 ${attachments.map(item => item.name).join(', ')}` : ''].filter(Boolean).join('\n'); addMessage('user', visible); input.value = ''; attachments.forEach(item => item.url && URL.revokeObjectURL(item.url)); attachments = []; const promptValue = [value, attachmentText].filter(Boolean).join('\n\n'); const tool = selectedTool || autoTool(value); selectedTool = null; renderAttachments(); if (tool === 'search_web') call('search_web', {query:value.replace(/^\/(pesquisar|pesquisa)\s+/i, '').trim()}); else if (tool === 'open_page') call('open_page', {url:value.replace(/^\/abrir\s+/i, '').trim()}); else if (tool === 'list_files') call('list_files', {path:value.replace(/^\/arquivos\s*/i, '').trim()}); else if (tool === 'search_files') call('search_files', {query:value.replace(/^\/buscar\s+/i, '').trim()}); else if (value.startsWith('/ler ')) call('read_file', {path:value.slice(5).trim()}); else if (value.startsWith('/criar pasta ')) runChatTool('create_directory', {path:value.slice(13).trim()}, 'Pasta criada'); else if (value.startsWith('/criar arquivo ')) { const parts=value.slice(15).split('\n'); runChatTool('create_file', {path:parts.shift().trim(), content:parts.join('\n')}, 'Arquivo criado'); } else if (value.startsWith('/editar arquivo ')) { const parts=value.slice(16).split('\n---\n'); const head=parts.shift().split('\n'); runChatTool('edit_file', {path:head.shift().trim(), old_text:head.join('\n'), new_text:parts.join('\n---\n')}, 'Arquivo editado'); } else if (value === '/fontes') listSources(); else chatModel(promptValue); }
    function quick(prefix) { input.value = prefix; input.focus(); }
    function listSources() { openTool('sources'); }
    function newChat() { saveConversation(); currentConversationId = null; conversation = []; selectedTool = null; attachments.forEach(item => item.url && URL.revokeObjectURL(item.url)); attachments = []; renderAttachments(); closeTool(); chat.innerHTML = '<div class="welcome" id="welcome"><h1>Como posso ajudar?</h1><p>Converse com o modelo próprio ou explore suas ferramentas locais.</p></div>'; renderHistory(); }
    input.addEventListener('keydown', e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); } });
    renderHistory();
  </script>
</body>
</html>"##;

type SharedState = Arc<Mutex<(HashMap<String, SourceRecord>, u64, PathBuf)>>;

fn activity_after(activity: &SharedActivity, after: u64) -> Value {
    let log = activity.lock().unwrap();
    let events: Vec<&ActivityEvent> = log.events.iter().filter(|event| event.id > after).collect();
    json!({"events": events, "latest_id": log.next_id})
}

fn activity_cursor(url: &str) -> u64 {
    url.split('?').nth(1)
        .and_then(|query| query.split('&').find_map(|pair| pair.strip_prefix("after=")))
        .and_then(|value| value.parse().ok())
        .unwrap_or(0)
}

fn handle_web_request(mut request: tiny_http::Request, state: SharedState, activity_log: SharedActivity) {
    if request.method() == &Method::Get && request.url() == "/" {
        let response = Response::from_string(WEB_UI).with_header(
            Header::from_bytes("Content-Type", "text/html; charset=utf-8").unwrap(),
        );
        let _ = request.respond(response);
        return;
    }

    if request.method() == &Method::Get && request.url().starts_with("/api/activity") {
        let data = activity_after(&activity_log, activity_cursor(request.url()));
        let response = Response::from_string(data.to_string()).with_header(
            Header::from_bytes("Content-Type", "application/json").unwrap(),
        );
        let _ = request.respond(response);
        return;
    }

    if request.method() == &Method::Post && request.url() == "/api/chat" {
        let mut body = String::new();
        let response = match request.as_reader().read_to_string(&mut body) {
            Ok(_) => match serde_json::from_str::<ChatRequest>(&body) {
                Ok(chat) => {
                    let started = Instant::now();
                    let operation = format!("chat:{}", chat.request_id.clone().unwrap_or_else(|| "local".into()));
                    activity(&activity_log, &operation, "received", "Requisição recebida; preparando o contexto.", false, None);
                    activity(&activity_log, &operation, "model", "Consultando o modelo próprio e o roteador de intenção.", false, None);
                    match call_model(&chat) {
                        Ok(mut data) => {
                            let elapsed = started.elapsed().as_millis();
                            if let Some(object) = data.as_object_mut() {
                                object.insert("elapsed_ms".into(), json!(elapsed));
                                object.insert("target_ms".into(), json!(TARGET_REQUEST_MS));
                                object.insert("performance".into(), json!(performance_label(elapsed)));
                            }
                            activity(&activity_log, &operation, "done", "Resposta pronta.", true, Some(elapsed));
                            Response::from_string(data.to_string()).with_header(
                                Header::from_bytes("Content-Type", "application/json").unwrap(),
                            )
                        }
                        Err(error) => {
                            let elapsed = started.elapsed().as_millis();
                            activity(&activity_log, &operation, "error", &format!("A requisição falhou: {error}"), true, Some(elapsed));
                            Response::from_string(json!({"ok": false, "error": error.to_string(), "elapsed_ms": elapsed, "target_ms": TARGET_REQUEST_MS, "max_ms": MAX_REQUEST_MS, "performance": performance_label(elapsed)}).to_string())
                                .with_status_code(StatusCode(503))
                        }
                    }
                },
                Err(error) => Response::from_string(json!({"ok": false, "error": format!("JSON inválido: {error}")}).to_string())
                    .with_status_code(StatusCode(400)),
            },
            Err(error) => Response::from_string(json!({"ok": false, "error": format!("falha ao ler requisição: {error}")}).to_string())
                .with_status_code(StatusCode(400)),
        };
        let _ = request.respond(response);
        return;
    }

    if request.method() == &Method::Post && request.url() == "/api/tool-call" {
        let mut body = String::new();
        let response = match request.as_reader().read_to_string(&mut body) {
            Ok(_) => match serde_json::from_str::<ToolCall>(&body) {
                Ok(call) => {
                    let started = Instant::now();
                    let tool = call.tool.clone();
                    let operation = format!("tool:{}:{}", tool, call.request_id.clone().unwrap_or_else(|| "local".into()));
                    activity(&activity_log, &operation, "started", &format!("Executando {tool}."), false, None);
                    let result = {
                        let mut state = state.lock().unwrap();
                        let (sources, next_id, workspace) = &mut *state;
                        match if tool == "set_workspace" {
                            set_workspace(&call.arguments, workspace)
                        } else {
                            execute(call, sources, next_id, workspace)
                        } {
                            Ok(data) => ToolResult { ok: true, tool, data: Some(data), error: None, elapsed_ms: 0, target_ms: TARGET_REQUEST_MS, max_ms: MAX_REQUEST_MS, performance: "pending".into() },
                            Err(error) => ToolResult { ok: false, tool, data: None, error: Some(error.to_string()), elapsed_ms: 0, target_ms: TARGET_REQUEST_MS, max_ms: MAX_REQUEST_MS, performance: "pending".into() },
                        }
                    };
                    let elapsed_ms = started.elapsed().as_millis();
                    let result = ToolResult { elapsed_ms, performance: performance_label(elapsed_ms).into(), ..result };
                    activity(&activity_log, &operation, if result.ok { "done" } else { "error" }, if result.ok { "Ferramenta concluída." } else { result.error.as_deref().unwrap_or("Ferramenta falhou.") }, true, Some(elapsed_ms));
                    Response::from_string(serde_json::to_string(&result).unwrap()).with_header(
                        Header::from_bytes("Content-Type", "application/json").unwrap(),
                    )
                }
                Err(error) => Response::from_string(format!("{{\"error\":\"JSON inválido: {error}\"}}"))
                    .with_status_code(StatusCode(400)),
            },
            Err(error) => Response::from_string(format!("{{\"error\":\"falha ao ler requisição: {error}\"}}"))
                .with_status_code(StatusCode(400)),
        };
        let _ = request.respond(response);
        return;
    }

    let _ = request.respond(Response::empty(404));
}

fn run_web_server() {
    let server = Server::http("127.0.0.1:3000").expect("não foi possível abrir http://127.0.0.1:3000");
    eprintln!("Interface local: http://127.0.0.1:3000");
    let workspace = std::env::current_dir().expect("não foi possível descobrir o workspace");
    let mut model_worker: Option<Child> = None;
    let python = workspace.join(".venv/bin/python");
    let model_server = workspace.join("python/model_server.py");
    let checkpoint = workspace.join("model/checkpoints/compact-01.pt");
    if python.exists() && model_server.exists() && checkpoint.exists() {
        match Command::new(&python)
            .arg(&model_server)
            .arg("--port")
            .arg("3101")
            .current_dir(&workspace)
            .spawn()
        {
            Ok(child) => {
                model_worker = Some(child);
                eprintln!("Modelo próprio iniciado em http://127.0.0.1:3101");
            }
            Err(error) => eprintln!("Modelo próprio indisponível: {error}"),
        }
    } else {
        eprintln!("Checkpoint próprio não encontrado; chat ficará indisponível");
    }
    let state: SharedState = Arc::new(Mutex::new((HashMap::new(), 1, workspace)));
    let activity_log: SharedActivity = Arc::new(Mutex::new(ActivityLog::default()));

    for request in server.incoming_requests() {
        let state = Arc::clone(&state);
        let activity_log = Arc::clone(&activity_log);
        std::thread::spawn(move || handle_web_request(request, state, activity_log));
    }

    if let Some(mut worker) = model_worker {
        let _ = worker.kill();
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn lista_ferramentas_disponiveis() {
        let mut sources = HashMap::new();
        let mut next_source_id = 1;
        let call = ToolCall {
            tool: "list_tools".into(),
            arguments: json!({}),
            request_id: None,
        };
        let result = execute(call, &mut sources, &mut next_source_id, Path::new(".")).unwrap();
        assert!(result["tools"].as_array().unwrap().iter().any(|tool| {
            tool["name"] == "search_web"
        }));
    }

    #[test]
    fn pesquisa_exige_consulta() {
        let mut sources = HashMap::new();
        let mut next_source_id = 1;
        let call = ToolCall {
            tool: "search_web".into(),
            arguments: json!({}),
            request_id: None,
        };
        assert!(matches!(
            execute(call, &mut sources, &mut next_source_id, Path::new(".")),
            Err(RuntimeError::MissingArgument(name)) if name == "query"
        ));
    }

    #[test]
    fn ferramenta_desconhecida_falha() {
        let mut sources = HashMap::new();
        let mut next_source_id = 1;
        let call = ToolCall {
            tool: "does_not_exist".into(),
            arguments: json!({}),
            request_id: None,
        };
        assert!(matches!(execute(call, &mut sources, &mut next_source_id, Path::new(".")), Err(RuntimeError::UnknownTool(name)) if name == "does_not_exist"));
    }

    #[test]
    fn gera_citacao_de_fonte_registrada() {
        let mut sources = HashMap::new();
        sources.insert(
            "web-1".into(),
            SourceRecord {
                source_id: "web-1".into(),
                title: "Página de teste".into(),
                url: "https://example.com".into(),
                snippet: "Resumo".into(),
                text: None,
            },
        );
        let result = cite_sources(&json!({"source_ids": ["web-1"]}), &sources).unwrap();
        assert_eq!(result["citations"][0]["source_id"], "web-1");
        assert!(result["citations"][0]["citation"]
            .as_str()
            .unwrap()
            .contains("Página de teste"));
    }

    #[test]
    fn fonte_desconhecida_nao_pode_ser_atualizada() {
        let mut sources = HashMap::new();
        let mut next_source_id = 1;
        let call = ToolCall {
            tool: "open_page".into(),
            arguments: json!({
                "source_id": "web-404",
                "url": "https://example.com"
            }),
            request_id: None,
        };
        assert!(matches!(
            execute(call, &mut sources, &mut next_source_id, Path::new(".")),
            Err(RuntimeError::UnknownSource(source_id)) if source_id == "web-404"
        ));
    }

    #[test]
    fn workspace_rejeita_traversal() {
        let root = Path::new("/tmp");
        assert!(matches!(
            workspace_path(root, "../etc"),
            Err(RuntimeError::OutsideWorkspace(_)) | Err(RuntimeError::Workspace(_))
        ));
    }
}
