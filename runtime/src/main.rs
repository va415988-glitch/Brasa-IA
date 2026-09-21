use base64::engine::general_purpose::URL_SAFE_NO_PAD;
use base64::Engine;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::collections::{HashMap, HashSet, VecDeque};
use std::fs;
use std::io::{self, BufRead, Read, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::{atomic::{AtomicBool, Ordering}, Arc, Mutex};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};
use tiny_http::{Header, Method, Response, Server, StatusCode};
use thiserror::Error;
use sha2::{Digest, Sha256};

const TARGET_REQUEST_MS: u128 = 10_000;
const MAX_REQUEST_MS: u128 = 10_000;
const CHAT_TARGET_MS: u128 = 60_000;
const CHAT_MAX_MS: u128 = 180_000;
const SEARCH_TIMEOUT: Duration = Duration::from_secs(10);
// Aprendizado profundo pode abrir um repositório inteiro e ainda consultar
// referências externas. A resposta comum continua com limite próprio; esta
// etapa tem um orçamento maior para privilegiar precisão.
const RESEARCH_TOTAL_TIMEOUT: Duration = Duration::from_secs(90);
const RESEARCH_SEARCH_TIMEOUT: Duration = Duration::from_secs(6);
const RESEARCH_PAGE_TIMEOUT: Duration = Duration::from_secs(6);
const MAX_FILE_BYTES: u64 = 128 * 1024;

#[derive(Debug, Deserialize)]
struct ToolCall {
    tool: String,
    #[serde(default)]
    arguments: Value,
    #[serde(default)]
    request_id: Option<String>,
}

#[derive(Debug, Deserialize, Serialize)]
struct ChatMessage {
    role: String,
    content: String,
    #[serde(default)]
    attachments: Vec<Value>,
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
    progress: u8,
    timestamp_ms: u128,
    #[serde(skip_serializing_if = "Option::is_none")]
    tool_call_id: Option<String>,
}

#[derive(Debug, Clone, Serialize)]
struct AgentProgress {
    value: Option<u8>,
    known: bool,
}

#[derive(Debug, Clone, Serialize)]
struct AgentEvent {
    schema: &'static str,
    event_id: String,
    seq: u64,
    timestamp_ms: u128,
    session_id: String,
    task_id: String,
    trace_id: String,
    parent_id: Option<String>,
    kind: String,
    phase: String,
    status: String,
    title: String,
    detail: String,
    progress: AgentProgress,
    elapsed_ms: Option<u128>,
    actor: &'static str,
    tool_call_id: Option<String>,
    payload: Value,
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
    let progress = match phase {
        "received" => 5,
        "processing" => 15,
        "model" => elapsed_ms.map(|ms| (35 + (ms / 3000) as u8).min(90)).unwrap_or(35),
        "planning" => 45,
        "learning" => 55,
        "tool" | "started" => 65,
        "verifying" => 85,
        "done" => 100,
        "error" => 100,
        _ => 50,
    };
    let tool_call_id = operation.strip_prefix("tool:").and_then(|value| value.split(':').nth(1)).map(|value| format!("call-{value}"));
    log.events.push_back(ActivityEvent {
        id,
        operation: operation.into(),
        phase: phase.into(),
        message: message.into(),
        done,
        elapsed_ms,
        progress,
        timestamp_ms: event_timestamp_ms(),
        tool_call_id,
    });
    while log.events.len() > 100 {
        log.events.pop_front();
    }
    eprintln!("[atividade][{}][{}] {}{}", operation, phase, message, elapsed_ms.map(|ms| format!(" ({ms} ms)")).unwrap_or_default());
}

fn event_kind(operation: &str, phase: &str, done: bool) -> String {
    let prefix = if operation.starts_with("tool:") { "tool" } else if operation.starts_with("learn:") { "training" } else { "task" };
    let suffix = match phase {
        "received" => "received",
        "requested" => "requested",
        "processing" => "context",
        "model" => "response.progress",
        "planning" => "plan",
        "learning" => "training.progress",
        "started" | "tool" => "started",
        "verifying" => "verification",
        "done" if prefix == "tool" => "completed",
        "done" => "completed",
        "error" => "failed",
        _ if done => "completed",
        _ => "progress",
    };
    format!("{prefix}.{suffix}")
}

fn event_status(phase: &str, done: bool) -> &'static str {
    match phase {
        "error" => "failed",
        "done" if done => "completed",
        "cancelled" => "cancelled",
        _ => "running",
    }
}

fn event_timestamp_ms() -> u128 {
    SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default().as_millis()
}

fn operation_session_id(operation: &str) -> String {
    if operation.starts_with("tool:") {
        operation.split(':').nth(2).unwrap_or("local").to_string()
    } else {
        operation.split(':').nth(1).unwrap_or("local").to_string()
    }
}

fn to_agent_event(event: &ActivityEvent) -> AgentEvent {
    let session_id = operation_session_id(&event.operation);
    AgentEvent {
        schema: "agent-event/v2",
        event_id: format!("evt-{}-{}", event.operation.replace(':', "-"), event.id),
        seq: event.id,
        timestamp_ms: event.timestamp_ms,
        session_id: session_id.clone(),
        task_id: event.operation.clone(),
        trace_id: format!("trace-{session_id}"),
        parent_id: None,
        kind: event_kind(&event.operation, &event.phase, event.done),
        phase: event.phase.clone(),
        status: event_status(&event.phase, event.done).into(),
        title: event.message.clone(),
        detail: event.message.clone(),
        progress: AgentProgress { value: Some(event.progress), known: true },
        elapsed_ms: event.elapsed_ms,
        actor: "runtime-rust",
        tool_call_id: event.tool_call_id.clone(),
        payload: json!({"legacy_operation": event.operation}),
    }
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
    if fs::symlink_metadata(&candidate).map(|m| m.file_type().is_symlink()).unwrap_or(false) {
        return Err(RuntimeError::Workspace("o destino é um link simbólico".into()));
    }
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
    let mut file = fs::OpenOptions::new().write(true).create_new(true).open(&path)
        .map_err(|error| RuntimeError::Workspace(error.to_string()))?;
    file.write_all(content.as_bytes()).map_err(|error| RuntimeError::Workspace(error.to_string()))?;
    let diff = line_diff("", &content);
    let artifact = code_artifact(&relative, "created", "", &content, &diff);
    Ok(json!({"path": relative, "created": true, "bytes": content.as_bytes().len(), "diff": diff, "artifact": artifact}))
}

fn html_escape(value: &str) -> String {
    value.replace('&', "&amp;").replace('<', "&lt;").replace('>', "&gt;").replace('"', "&quot;").replace('\'', "&#039;")
}

fn login_web_page(title: &str, prompt: &str) -> String {
    r##"<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>__TITLE__</title>
  <style>
    :root{color-scheme:dark;font-family:Inter,ui-sans-serif,system-ui,sans-serif}*{box-sizing:border-box}body{margin:0;min-height:100vh;display:grid;place-items:center;overflow:hidden;color:#f8fafc;background:#070b17}.scene{position:fixed;inset:0;background:radial-gradient(circle at 15% 20%,#6d28d944,transparent 30%),radial-gradient(circle at 85% 80%,#0891b244,transparent 32%);animation:shift 12s ease-in-out infinite alternate}.scene:before,.scene:after{content:"";position:absolute;width:28rem;height:28rem;border:1px solid #a78bfa55;border-radius:42% 58% 63% 37%;filter:blur(.2px);animation:float 14s ease-in-out infinite}.scene:before{left:-9rem;top:-8rem}.scene:after{right:-10rem;bottom:-12rem;border-color:#22d3ee55;animation-delay:-5s}.card{position:relative;width:min(430px,calc(100% - 32px));padding:34px;border:1px solid #334155aa;border-radius:26px;background:#0f172add;box-shadow:0 30px 100px #0009;backdrop-filter:blur(18px);animation:enter .7s ease-out}.mark{width:48px;height:48px;display:grid;place-items:center;margin-bottom:22px;border-radius:16px;background:linear-gradient(135deg,#8b5cf6,#06b6d4);box-shadow:0 0 35px #8b5cf655;font-size:23px}.eyebrow{margin:0 0 8px;color:#a5b4fc;font-size:12px;text-transform:uppercase;letter-spacing:.16em}.card h1{margin:0;font-size:31px;letter-spacing:-.04em}.description{margin:12px 0 24px;color:#94a3b8;line-height:1.55;font-size:14px}.field{display:grid;gap:8px;margin-top:15px}.field label{color:#cbd5e1;font-size:13px}.field input{width:100%;padding:13px 14px;border:1px solid #334155;border-radius:12px;outline:0;background:#0b1220;color:#fff;font:inherit}.field input:focus{border-color:#8b5cf6;box-shadow:0 0 0 3px #8b5cf633}.row{display:flex;justify-content:space-between;align-items:center;margin:14px 0 22px;color:#94a3b8;font-size:12px}.row a{color:#c4b5fd}.submit{width:100%;padding:14px;border:0;border-radius:12px;color:#fff;background:linear-gradient(100deg,#7c3aed,#2563eb);font:inherit;font-weight:700;cursor:pointer;transition:transform .2s,box-shadow .2s}.submit:hover{transform:translateY(-2px);box-shadow:0 12px 25px #4f46e566}.status{min-height:20px;margin-top:14px;color:#86efac;text-align:center;font-size:12px}@keyframes float{50%{transform:translate(40px,26px) rotate(30deg)}}@keyframes shift{to{filter:hue-rotate(25deg) saturate(1.2)}}@keyframes enter{from{opacity:0;transform:translateY(18px) scale(.98)}to{opacity:1;transform:none}}
  </style>
</head>
<body><div class="scene"></div><main class="card"><div class="mark">✦</div><p class="eyebrow">__TITLE__</p><h1>Bem-vindo de volta</h1><p class="description">__PROMPT__</p><form onsubmit="event.preventDefault();document.querySelector('.status').textContent='Login demonstrativo enviado.'"><div class="field"><label for="email">E-mail</label><input id="email" type="email" placeholder="voce@exemplo.com" required></div><div class="field"><label for="password">Senha</label><input id="password" type="password" placeholder="••••••••" required></div><div class="row"><label><input type="checkbox"> Lembrar acesso</label><a href="#" onclick="event.preventDefault()">Esqueci a senha</a></div><button class="submit">Entrar</button><div class="status" aria-live="polite"></div></form></main></body>
</html>"##.replace("__TITLE__", title).replace("__PROMPT__", prompt)
}

fn create_web_page(args: &Value, root: &Path) -> Result<Value, RuntimeError> {
    let prompt = required_string(args, "prompt")?;
    if prompt.trim().is_empty() {
        return Err(RuntimeError::InvalidArgument("descreva a página que deve ser criada".into()));
    }
    let requested = args.get("path").and_then(Value::as_str).filter(|path| !path.trim().is_empty()).unwrap_or("preview/index.html");
    let mut relative = requested.to_string();
    if workspace_new_path(root, requested)?.exists() {
        let path = Path::new(requested);
        let stem = path.file_stem().and_then(|value| value.to_str()).unwrap_or("index");
        let extension = path.extension().and_then(|value| value.to_str()).unwrap_or("html");
        let parent = path.parent().and_then(|value| value.to_str()).unwrap_or("");
        for index in 2..1000 {
            let candidate = if parent.is_empty() { format!("{stem}-{index}.{extension}") } else { format!("{parent}/{stem}-{index}.{extension}") };
            if !workspace_new_path(root, &candidate)?.exists() {
                relative = candidate;
                break;
            }
        }
    }
    let title = args.get("title").and_then(Value::as_str).filter(|value| !value.trim().is_empty()).unwrap_or("Página local");
    let safe_title = html_escape(title);
    let safe_prompt = html_escape(prompt.trim());
    let is_login = prompt.to_lowercase().contains("login") || prompt.to_lowercase().contains("entrar");
    let html = if is_login { login_web_page(&safe_title, &safe_prompt) } else { format!(r##"<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{safe_title}</title>
  <style>
    :root {{ color-scheme: dark; font-family: Inter, ui-sans-serif, system-ui, sans-serif; --bg:#08111f; --panel:#101d32; --line:#263b5c; --text:#eff6ff; --muted:#a7b8d1; --accent:#7c3aed; --accent2:#06b6d4; }}
    * {{ box-sizing:border-box; }} body {{ margin:0; min-height:100vh; color:var(--text); background:radial-gradient(circle at 15% 0%,#233b68 0,transparent 38%),linear-gradient(135deg,var(--bg),#111827); }}
    .wrap {{ width:min(1120px,calc(100% - 40px)); margin:auto; }} nav {{ display:flex; justify-content:space-between; align-items:center; padding:24px 0; }}
    .brand {{ font-weight:800; letter-spacing:.03em; }} .pill {{ padding:8px 12px; border:1px solid #49638d; border-radius:999px; color:#c7d2fe; font-size:13px; }}
    .hero {{ display:grid; grid-template-columns:1.15fr .85fr; gap:40px; align-items:center; padding:72px 0 86px; }} h1 {{ max-width:700px; margin:0; font-size:clamp(42px,7vw,78px); line-height:.98; letter-spacing:-.06em; }}
    .gradient {{ background:linear-gradient(90deg,#c4b5fd,var(--accent2)); color:transparent; background-clip:text; }} .lead {{ max-width:610px; margin:24px 0 0; color:var(--muted); font-size:18px; line-height:1.65; }}
    .actions {{ display:flex; gap:12px; margin-top:30px; flex-wrap:wrap; }} button {{ border:0; border-radius:12px; padding:13px 18px; color:white; background:linear-gradient(100deg,var(--accent),#2563eb); font:inherit; cursor:pointer; }} button.secondary {{ border:1px solid var(--line); background:#13223a; }}
    .orb {{ min-height:300px; display:grid; place-items:center; border:1px solid var(--line); border-radius:32px; background:linear-gradient(145deg,#182d4caa,#0b1424dd); box-shadow:0 30px 80px #0006; }} .orb::before {{ content:""; width:150px; height:150px; border-radius:50%; background:radial-gradient(circle at 35% 30%,#fff,#a78bfa 16%,#2563eb 42%,transparent 70%); filter:blur(1px); box-shadow:0 0 80px #38bdf8aa; }}
    .grid {{ display:grid; grid-template-columns:repeat(3,1fr); gap:16px; padding-bottom:80px; }} .card {{ padding:24px; min-height:170px; border:1px solid var(--line); border-radius:20px; background:#0e1a2ccc; }} .card h2 {{ margin:0 0 12px; font-size:19px; }} .card p {{ margin:0; color:var(--muted); line-height:1.6; }} footer {{ padding:24px 0 40px; color:#8293ad; font-size:13px; border-top:1px solid #1c2c45; }}
    @media(max-width:760px) {{ .hero {{ grid-template-columns:1fr; padding:46px 0 60px; }} .grid {{ grid-template-columns:1fr; }} .orb {{ min-height:210px; }} }}
  </style>
</head>
<body>
  <div class="wrap">
    <nav><div class="brand">✦ {safe_title}</div><div class="pill">Protótipo local</div></nav>
    <main>
      <section class="hero"><div><h1>Uma ideia clara merece uma <span class="gradient">boa experiência.</span></h1><p class="lead">{safe_prompt}</p><div class="actions"><button onclick="document.querySelector('#recursos').scrollIntoView({{behavior:'smooth'}})">Explorar</button><button class="secondary" onclick="alert('Protótipo funcionando localmente.')">Testar interação</button></div></div><div class="orb" aria-label="Visual principal"></div></section>
      <section id="recursos" class="grid"><article class="card"><h2>Clareza</h2><p>Estrutura visual para comunicar a proposta sem ruído e orientar a próxima ação.</p></article><article class="card"><h2>Interação</h2><p>Elementos responsivos e uma chamada para ação pronta para evoluir.</p></article><article class="card"><h2>Base extensível</h2><p>HTML, CSS e JavaScript em um único arquivo para testar rapidamente no projeto.</p></article></section>
    </main>
    <footer>Página criada pelo runtime local · arquivo editável no workspace</footer>
  </div>
</body>
</html>
"##) };
    let mut result = create_file(&json!({"path": relative, "content": html}), root)?;
    if let Some(object) = result.as_object_mut() {
        object.insert("preview_html".into(), Value::String(html));
        object.insert("preview_sandboxed".into(), Value::Bool(true));
        object.insert("requested_path".into(), Value::String(requested.to_string()));
    }
    Ok(result)
}

fn language_for_path(path: &str) -> String {
    let extension = Path::new(path).extension().and_then(|value| value.to_str()).unwrap_or("").to_lowercase();
    match extension.as_str() {
        "js" | "jsx" => "javascript",
        "ts" | "tsx" => "typescript",
        "py" => "python",
        "rs" => "rust",
        "json" => "json",
        "md" => "markdown",
        "html" | "htm" => "html",
        "css" | "scss" => "css",
        "toml" => "toml",
        "yaml" | "yml" => "yaml",
        "sql" => "sql",
        "sh" => "shell",
        _ => "text",
    }.into()
}

fn text_hash(text: &str) -> String {
    format!("sha256:{:x}", Sha256::digest(text.as_bytes()))
}

fn line_diff(old_text: &str, new_text: &str) -> Value {
    let old_lines: Vec<&str> = old_text.split('\n').collect();
    let new_lines: Vec<&str> = new_text.split('\n').collect();
    let max = old_lines.len().max(new_lines.len());
    let mut lines = Vec::with_capacity(max.saturating_mul(2));
    for index in 0..max {
        match (old_lines.get(index), new_lines.get(index)) {
            (Some(old), Some(new)) if old == new => lines.push(json!({
                "kind": "context", "old_line": index + 1, "new_line": index + 1, "text": old
            })),
            (Some(old), Some(new)) => {
                lines.push(json!({"kind": "remove", "old_line": index + 1, "new_line": Value::Null, "text": old}));
                lines.push(json!({"kind": "add", "old_line": Value::Null, "new_line": index + 1, "text": new}));
            }
            (Some(old), None) => lines.push(json!({
                "kind": "remove", "old_line": index + 1, "new_line": Value::Null, "text": old
            })),
            (None, Some(new)) => lines.push(json!({
                "kind": "add", "old_line": Value::Null, "new_line": index + 1, "text": new
            })),
            (None, None) => {}
        }
    }
    let changed = lines.iter().filter(|line| line.get("kind").and_then(Value::as_str) != Some("context")).count();
    json!({"format": "line-v1", "changed": changed, "lines": lines, "truncated": false})
}

fn code_artifact(path: &str, status: &str, before: &str, after: &str, diff: &Value) -> Value {
    json!({
        "kind": "code",
        "path": path,
        "language": language_for_path(path),
        "status": status,
        "bytes_before": before.as_bytes().len(),
        "bytes_after": after.as_bytes().len(),
        "lines_after": after.split('\n').count(),
        "base_hash": text_hash(before),
        "result_hash": text_hash(after),
        "diff": diff.clone()
    })
}

fn edit_file(args: &Value, root: &Path) -> Result<Value, RuntimeError> {
    let relative = required_string(args, "path")?;
    let old_text = required_string(args, "old_text")?;
    let new_text = required_string(args, "new_text")?;
    if old_text.is_empty() {
        return Err(RuntimeError::InvalidArgument("o trecho a substituir não pode ser vazio".into()));
    }
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
    let updated = content.replacen(&old_text, &new_text, 1);
    if updated.len() > MAX_FILE_BYTES as usize {
        return Err(RuntimeError::Workspace(format!("arquivo resultante excede o limite de {MAX_FILE_BYTES} bytes")));
    }
    let backup_dir = workspace_new_path(root, ".ia-local-backups")?;
    fs::create_dir_all(&backup_dir).map_err(|error| RuntimeError::Workspace(format!("não foi possível criar backup: {error}")))?;
    let safe_name = relative.replace(['/', '\\'], "__");
    let timestamp = SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default().as_nanos();
    let backup_path = backup_dir.join(format!("{timestamp}__{safe_name}"));
    fs::OpenOptions::new().write(true).create_new(true).open(&backup_path)
        .and_then(|mut file| file.write_all(content.as_bytes()))
        .map_err(|error| RuntimeError::Workspace(format!("não foi possível salvar backup: {error}")))?;
    // A troca por rename evita deixar um arquivo parcialmente escrito.
    let temporary = path.with_file_name(format!(".ia-edit-{timestamp}"));
    let result = (|| -> io::Result<()> {
        let mut file = fs::OpenOptions::new().write(true).create_new(true).open(&temporary)?;
        file.set_permissions(metadata.permissions())?;
        file.write_all(updated.as_bytes())?;
        file.sync_all()?;
        if fs::read_to_string(&path)? != content {
            return Err(io::Error::other("arquivo alterado durante a edição; leia-o novamente"));
        }
        fs::rename(&temporary, &path)
    })();
    if let Err(error) = result {
        let _ = fs::remove_file(&temporary);
        return Err(RuntimeError::Workspace(error.to_string()));
    }
    let diff = line_diff(&content, &updated);
    let artifact = code_artifact(&relative, "applied", &content, &updated, &diff);
    Ok(json!({
        "path": relative,
        "updated": true,
        "bytes": updated.as_bytes().len(),
        "backup": backup_path.strip_prefix(root).unwrap_or(&backup_path),
        "diff": diff,
        "artifact": artifact
    }))
}

fn apply_batch(args: &Value, root: &Path) -> Result<Value, RuntimeError> {
    let operations = args.get("operations").and_then(Value::as_array)
        .ok_or_else(|| RuntimeError::InvalidArgument("operations deve ser uma lista".into()))?;
    if operations.is_empty() || operations.len() > 32 {
        return Err(RuntimeError::InvalidArgument("o lote deve conter entre 1 e 32 operações".into()));
    }
    let total_bytes: usize = operations.iter().map(|op| op.get("arguments").and_then(|a| a.get("content")).and_then(Value::as_str).map(str::len).unwrap_or(0)).sum();
    if total_bytes > 4 * 1024 * 1024 { return Err(RuntimeError::Workspace("conteúdo total do lote excede 4 MiB".into())); }
    let mut applied = Vec::new();
    for operation in operations {
        let tool = operation.get("tool").and_then(Value::as_str).ok_or_else(|| RuntimeError::InvalidArgument("cada operação precisa de tool".into()))?;
        let arguments = operation.get("arguments").cloned().unwrap_or_else(|| json!({}));
        let result = match tool {
            "create_file" => create_file(&arguments, root),
            "edit_file" => edit_file(&arguments, root),
            "create_directory" => create_directory(&arguments, root),
            _ => Err(RuntimeError::InvalidArgument(format!("operação não permitida no lote: {tool}"))),
        };
        match result {
            Ok(value) => applied.push(json!({"tool": tool, "result": value})),
            Err(error) => {
                for item in applied.iter().rev() {
                    let result = &item["result"];
                    if item["tool"] == "create_file" { if let Some(path) = result.get("path").and_then(Value::as_str) { let _ = fs::remove_file(workspace_path(root, path)?); } }
                    if item["tool"] == "edit_file" {
                        if let (Some(path), Some(backup)) = (result.get("path").and_then(Value::as_str), result.get("backup").and_then(Value::as_str)) {
                            let target = workspace_path(root, path)?; let backup_path = workspace_path(root, backup)?;
                            if let Ok(content) = fs::read(&backup_path) { let _ = fs::write(target, content); }
                        }
                    }
                }
                return Err(RuntimeError::Workspace(format!("lote revertido após falha: {error}")));
            }
        }
    }
    Ok(json!({"schema":"engineering-batch/v1","ok":true,"atomic":true,"count":applied.len(),"operations":applied}))
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
    let started = Instant::now();
    let mut scanned = 0;
    while let Some(directory) = stack.pop() {
        let entries = fs::read_dir(directory).map_err(|error| RuntimeError::Workspace(error.to_string()))?;
        for entry in entries {
            scanned += 1;
            if scanned > 20_000 || started.elapsed() > SEARCH_TIMEOUT {
                return Ok(json!({"query":query,"matches":matches,"truncated":true,"reason":"limite de leitura atingido"}));
            }
            let entry = entry.map_err(|error| RuntimeError::Workspace(error.to_string()))?;
            let name = entry.file_name().to_string_lossy().to_string();
            if name.starts_with('.') || matches!(name.as_str(), "target" | "node_modules" | "venv" | "dist" | "build" | "__pycache__") {
                continue;
            }
            if entry.file_type().map_err(|error| RuntimeError::Workspace(error.to_string()))?.is_symlink() {
                continue;
            }
            let path = entry.path();
            if path.is_dir() {
                stack.push(path);
                continue;
            }
            let metadata = entry.metadata().map_err(|error| RuntimeError::Workspace(error.to_string()))?;
            if !metadata.is_file() || metadata.len() > MAX_FILE_BYTES {
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

fn inspect_code(args: &Value, root: &Path) -> Result<Value, RuntimeError> {
    let requested = args.get("path").and_then(Value::as_str).unwrap_or("");
    let start = if requested.is_empty() { root.to_path_buf() } else { workspace_path(root, requested)? };
    let mut stack = vec![start.clone()];
    let ignored = ["target", "node_modules", "dist", "build", ".venv", "venv", "__pycache__", ".git"];
    let supported = ["rs", "py", "js", "jsx", "ts", "tsx", "go", "java", "kt", "rb", "php", "c", "h", "cpp", "hpp"];
    let mut symbols = Vec::new();
    let mut imports = Vec::new();
    let mut files_scanned = 0usize;
    let mut truncated = false;
    while let Some(directory) = stack.pop() {
        let entries = if directory.is_dir() { fs::read_dir(&directory).map_err(|error| RuntimeError::Workspace(error.to_string()))? } else { continue };
        for entry in entries {
            if files_scanned >= 250 || symbols.len() >= 1500 || imports.len() >= 500 {
                truncated = true;
                break;
            }
            let entry = entry.map_err(|error| RuntimeError::Workspace(error.to_string()))?;
            let name = entry.file_name().to_string_lossy().to_string();
            if name.starts_with('.') || ignored.contains(&name.as_str()) { continue; }
            if entry.file_type().map_err(|error| RuntimeError::Workspace(error.to_string()))?.is_symlink() { continue; }
            let path = entry.path();
            if path.is_dir() { stack.push(path); continue; }
            let extension = path.extension().and_then(|value| value.to_str()).unwrap_or("").to_lowercase();
            if !supported.contains(&extension.as_str()) { continue; }
            let metadata = entry.metadata().map_err(|error| RuntimeError::Workspace(error.to_string()))?;
            if metadata.len() > MAX_FILE_BYTES { continue; }
            let content = match fs::read_to_string(&path) { Ok(content) => content, Err(_) => continue };
            files_scanned += 1;
            let relative = path.strip_prefix(root).unwrap_or(&path).display().to_string();
            for (line_number, raw_line) in content.lines().enumerate() {
                let line = raw_line.trim();
                if line.is_empty() || line.starts_with("//") || line.starts_with('#') && !matches!(extension.as_str(), "py" | "rb") { continue; }
                let (kind, marker) = if line.starts_with("fn ") || line.contains(" fn ") { ("function", "fn ") }
                    else if line.starts_with("def ") { ("function", "def ") }
                    else if line.starts_with("function ") { ("function", "function ") }
                    else if line.starts_with("class ") || line.contains(" class ") { ("class", "class ") }
                    else if line.starts_with("struct ") { ("struct", "struct ") }
                    else if line.starts_with("enum ") { ("enum", "enum ") }
                    else if line.starts_with("trait ") || line.starts_with("interface ") { ("interface", if line.starts_with("trait ") { "trait " } else { "interface " }) }
                    else if line.starts_with("mod ") { ("module", "mod ") }
                    else if line.starts_with("func ") { ("function", "func ") }
                    else if line.starts_with("type ") { ("type", "type ") }
                    else { ("", "") };
                if !marker.is_empty() {
                    let remainder = line.strip_prefix(marker).unwrap_or(line);
                    let name: String = remainder.chars().take_while(|character| character.is_alphanumeric() || *character == '_').collect();
                    if !name.is_empty() {
                        symbols.push(json!({"name": name, "kind": kind, "path": relative, "line": line_number + 1, "signature": line.chars().take(240).collect::<String>()}));
                    }
                }
                let is_import = line.starts_with("use ") || line.starts_with("import ") || line.starts_with("from ") || line.starts_with("require(") || line.starts_with("#include");
                if is_import && imports.len() < 500 {
                    imports.push(json!({"path": relative, "line": line_number + 1, "text": line.chars().take(240).collect::<String>()}));
                }
            }
        }
        if truncated { break; }
    }
    symbols.sort_by(|left, right| left["path"].as_str().cmp(&right["path"].as_str()).then(left["line"].as_u64().cmp(&right["line"].as_u64())));
    imports.sort_by(|left, right| left["path"].as_str().cmp(&right["path"].as_str()).then(left["line"].as_u64().cmp(&right["line"].as_u64())));
    Ok(json!({"workspace": root, "path": requested, "files_scanned": files_scanned, "symbols": symbols, "imports": imports, "symbol_count": symbols.len(), "import_count": imports.len(), "truncated": truncated}))
}

fn project_checks(args: &Value, root: &Path) -> Result<Value, RuntimeError> {
    let requested = args.get("check").and_then(Value::as_str).unwrap_or("auto");
    let changed_path = args.get("path").and_then(Value::as_str).unwrap_or_default();
    let has_cargo = root.join("Cargo.toml").is_file();
    let has_pyproject = root.join("pyproject.toml").is_file() || root.join("pytest.ini").is_file() || root.join("tox.ini").is_file() || root.join("setup.cfg").is_file();
    let has_python_tests = root.join("tests").is_dir() || root.join("test").is_dir();
    let has_package = root.join("package.json").is_file();
    let package_has_test_script = has_package && fs::read_to_string(root.join("package.json"))
        .ok()
        .and_then(|content| serde_json::from_str::<Value>(&content).ok())
        .map(|package| package.get("scripts").and_then(|scripts| scripts.get("test"))
            .and_then(Value::as_str)
            .map(|script| !script.trim().is_empty())
            .unwrap_or(false))
        .unwrap_or(false);
    let mut available = Vec::new();
    if has_cargo { available.push("cargo-test"); }
    if package_has_test_script { available.push("npm-test"); }
    if has_pyproject { available.push("pytest"); }
    if has_python_tests { available.push("unittest"); }
    if requested == "list" {
        return Ok(json!({"available": available, "workspace": root, "changed_path": changed_path}));
    }
    let check = if requested == "auto" {
        let extension = Path::new(changed_path).extension().and_then(|value| value.to_str()).unwrap_or("").to_lowercase();
        if matches!(extension.as_str(), "rs") && has_cargo { "cargo-test" }
        else if matches!(extension.as_str(), "js" | "jsx" | "ts" | "tsx" | "mjs" | "cjs") && package_has_test_script { "npm-test" }
        else if matches!(extension.as_str(), "py") && has_pyproject { "pytest" }
        else if matches!(extension.as_str(), "py") && has_python_tests { "unittest" }
        else if has_cargo { "cargo-test" }
        else if package_has_test_script { "npm-test" }
        else if has_pyproject { "pytest" }
        else if has_python_tests { "unittest" }
        else { "none" }
    } else { requested };
    let local_python = root.join(".venv").join("bin").join("python");
    let python_program = local_python.to_str().filter(|_| local_python.is_file()).unwrap_or("python3");
    let python_test_dir = if root.join("tests").is_dir() { "tests" } else { "test" };
    let (program, arguments): (&str, Vec<&str>) = match check {
        "cargo-test" if has_cargo => ("cargo", vec!["test", "--all-targets"]),
        "npm-test" if has_package => ("npm", vec!["test", "--", "--runInBand"]),
        "pytest" if has_pyproject || has_python_tests => (python_program, vec!["-m", "pytest", "-q"]),
        "unittest" if has_pyproject || has_python_tests => (python_program, vec!["-m", "unittest", "discover", "-s", python_test_dir, "-p", "test*.py", "-v"]),
        "none" => return Ok(json!({"available": available, "executed": false, "passed": false, "message": "nenhum verificador reconhecido no workspace"})),
        _ => return Err(RuntimeError::InvalidArgument(format!("verificação não permitida ou indisponível: {requested}"))),
    };
    let started = Instant::now();
    let command = format!("{} {}", program, arguments.join(" "));
    let mut child = match Command::new(program).args(&arguments).current_dir(root)
        .stdout(Stdio::piped()).stderr(Stdio::piped()).spawn() {
        Ok(child) => child,
        Err(error) => return Ok(json!({"check": check, "command": command, "passed": false, "executed": false, "available": available, "message": format!("não foi possível iniciar {program}: {error}")})),
    };
    let timeout = Duration::from_secs(9);
    loop {
        if child.try_wait().map_err(|error| RuntimeError::Workspace(error.to_string()))?.is_some() { break; }
        if started.elapsed() > timeout {
            let _ = child.kill();
            let output = child.wait_with_output().map_err(|error| RuntimeError::Workspace(error.to_string()))?;
            let limit = |bytes: &[u8]| String::from_utf8_lossy(bytes).chars().take(12_000).collect::<String>();
            return Ok(json!({"check": check, "command": command, "passed": false, "executed": true, "timed_out": true, "elapsed_ms": started.elapsed().as_millis(), "stdout": limit(&output.stdout), "stderr": limit(&output.stderr), "message": "a verificação excedeu 9 segundos e foi interrompida"}));
        }
        std::thread::sleep(Duration::from_millis(25));
    }
    let output = child.wait_with_output().map_err(|error| RuntimeError::Workspace(error.to_string()))?;
    let limit = |bytes: &[u8]| String::from_utf8_lossy(bytes).chars().take(12_000).collect::<String>();
    Ok(json!({"check": check, "command": command, "passed": output.status.success(), "executed": true, "exit_code": output.status.code(), "elapsed_ms": started.elapsed().as_millis(), "stdout": limit(&output.stdout), "stderr": limit(&output.stderr), "truncated": output.stdout.len() + output.stderr.len() > 12_000}))
}

fn diagnose_project(args: &Value) -> Result<Value, RuntimeError> {
    let check = args.get("check").and_then(Value::as_str).unwrap_or("verificação");
    let passed = args.get("passed").and_then(Value::as_bool).unwrap_or(false);
    let executed = args.get("executed").and_then(Value::as_bool).unwrap_or(true);
    let stdout = args.get("stdout").and_then(Value::as_str).unwrap_or_default();
    let stderr = args.get("stderr").and_then(Value::as_str).unwrap_or_default();
    let combined = format!("{stdout}\n{stderr}");
    let lower = combined.to_lowercase();
    let category = if !executed {
        "configuration"
    } else if lower.contains("syntaxerror") || lower.contains("parse error") || lower.contains("expected one of") {
        "syntax"
    } else if lower.contains("unresolved import") || lower.contains("cannot find module") || lower.contains("modulenotfounderror") || lower.contains("no module named") {
        "dependency-or-import"
    } else if lower.contains("assert") || lower.contains("test result") || lower.contains("failed") || lower.contains("failure") {
        "test-failure"
    } else if lower.contains("timed out") || lower.contains("timeout") {
        "timeout"
    } else if lower.contains("error") || lower.contains("panic") || lower.contains("traceback") {
        "build-or-runtime"
    } else {
        "unknown"
    };
    let mut evidence = Vec::new();
    for line in combined.lines().map(str::trim).filter(|line| !line.is_empty()) {
        let line_lower = line.to_lowercase();
        if line_lower.contains("error") || line_lower.contains("failed") || line_lower.contains("failure") || line_lower.contains("panic") || line_lower.contains("traceback") || line_lower.contains("assert") || line_lower.starts_with("e ") || line_lower.starts_with("e:") || line_lower.contains("collected") {
            evidence.push(line.chars().take(300).collect::<String>());
            if evidence.len() >= 8 { break; }
        }
    }
    if evidence.is_empty() {
        evidence = combined.lines().map(str::trim).filter(|line| !line.is_empty()).take(4).map(|line| line.chars().take(300).collect::<String>()).collect();
    }
    let next_steps: Vec<&str> = match category {
        "syntax" => vec!["abra a primeira linha indicada pelo compilador", "corrija delimitadores, indentação ou tipos de expressão", "execute a verificação novamente"],
        "dependency-or-import" => vec!["confirme o manifesto e o ambiente ativo", "verifique nome, versão e instalação da dependência", "execute novamente depois de corrigir o import"],
        "test-failure" => vec!["leia a primeira falha, não apenas o resumo final", "reproduza o caso isolado", "corrija o comportamento ou atualize o teste somente se o contrato mudou"],
        "timeout" => vec!["identifique a etapa que consumiu o orçamento", "execute o caso menor e meça o gargalo", "evite aumentar o timeout antes de entender a causa"],
        "configuration" => vec!["adicione ou selecione um verificador compatível", "confirme o manifesto e o comando esperado", "execute a verificação novamente"],
        _ => vec!["leia a primeira mensagem de erro completa", "reproduza o problema com a menor entrada possível", "execute a verificação novamente após a correção"],
    };
    let summary = if passed {
        format!("A verificação {check} passou; não há falha para diagnosticar.")
    } else {
        format!("A verificação {check} não passou. Categoria provável: {category}.")
    };
    Ok(json!({"check": check, "passed": passed, "category": category, "summary": summary, "evidence": evidence, "next_steps": next_steps}))
}

fn propose_repair(args: &Value, root: &Path) -> Result<Value, RuntimeError> {
    let relative = required_string(args, "path")?;
    let old_text = required_string(args, "old_text")?;
    let new_text = required_string(args, "new_text")?;
    let reason = required_string(args, "reason")?;
    let check = args.get("check").and_then(Value::as_str).unwrap_or("auto");
    if old_text.is_empty() {
        return Err(RuntimeError::InvalidArgument("o trecho a substituir não pode ser vazio".into()));
    }
    if reason.trim().is_empty() {
        return Err(RuntimeError::InvalidArgument("a proposta precisa explicar o motivo da correção".into()));
    }
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
        return Err(RuntimeError::Workspace("trecho antigo aparece mais de uma vez; torne a proposta específica".into()));
    }
    let updated = content.replacen(&old_text, &new_text, 1);
    if updated.len() > MAX_FILE_BYTES as usize {
        return Err(RuntimeError::Workspace(format!("arquivo resultante excede o limite de {MAX_FILE_BYTES} bytes")));
    }
    let diff = line_diff(&content, &updated);
    let artifact = code_artifact(&relative, "proposed", &content, &updated, &diff);
    Ok(json!({
        "status": "ready",
        "path": relative,
        "reason": reason,
        "old_text": old_text,
        "new_text": new_text,
        "bytes_before": content.as_bytes().len(),
        "bytes_after": updated.as_bytes().len(),
        "diff": diff,
        "artifact": artifact,
        "verification": {"tool": "project_checks", "arguments": {"check": check}},
        "message": "Proposta validada sem alterar o arquivo. Use apply_repair após revisar o diff."
    }))
}

fn apply_repair(args: &Value, root: &Path) -> Result<Value, RuntimeError> {
    let reason = required_string(args, "reason")?;
    if reason.trim().is_empty() {
        return Err(RuntimeError::InvalidArgument("a correção precisa explicar o motivo da alteração".into()));
    }
    let mut result = edit_file(args, root)?;
    if let Some(object) = result.as_object_mut() {
        object.insert("repair".into(), json!({
            "reason": reason,
            "verification": {"tool": "project_checks", "arguments": {"check": args.get("check").and_then(Value::as_str).unwrap_or("auto")}},
            "message": "Correção aplicada; execute a verificação indicada antes de aceitar o resultado."
        }));
    }
    Ok(result)
}

fn inspect_project(args: &Value, root: &Path) -> Result<Value, RuntimeError> {
    let max_depth = args.get("max_depth").and_then(Value::as_u64).unwrap_or(4).clamp(1, 6) as usize;
    let started = Instant::now();
    let ignored = ["target", "node_modules", "dist", "build", ".venv", "venv", "__pycache__", ".git"];
    let mut stack = vec![(root.to_path_buf(), 0usize)];
    let mut files = Vec::new();
    let mut manifests = Vec::new();
    let mut test_files = Vec::new();
    let mut entrypoints = Vec::new();
    let mut truncated = false;
    while let Some((directory, depth)) = stack.pop() {
        let entries = fs::read_dir(&directory).map_err(|error| RuntimeError::Workspace(error.to_string()))?;
        for entry in entries {
            if started.elapsed() > Duration::from_secs(3) || files.len() >= 400 {
                truncated = true;
                break;
            }
            let entry = entry.map_err(|error| RuntimeError::Workspace(error.to_string()))?;
            let name = entry.file_name().to_string_lossy().to_string();
            if name.starts_with('.') || ignored.contains(&name.as_str()) {
                continue;
            }
            let file_type = entry.file_type().map_err(|error| RuntimeError::Workspace(error.to_string()))?;
            if file_type.is_symlink() {
                continue;
            }
            let path = entry.path();
            let relative = path.strip_prefix(root).unwrap_or(&path).display().to_string();
            if file_type.is_dir() {
                if depth < max_depth {
                    stack.push((path, depth + 1));
                }
                continue;
            }
            if !file_type.is_file() {
                continue;
            }
            files.push(json!({"path": relative, "bytes": entry.metadata().map(|metadata| metadata.len()).unwrap_or(0)}));
            let lower = name.to_lowercase();
            if matches!(lower.as_str(), "cargo.toml" | "package.json" | "pyproject.toml" | "requirements.txt" | "go.mod" | "pom.xml" | "build.gradle" | "gemfile" | "composer.json") {
                manifests.push(relative.clone());
            }
            if lower.contains("test") || lower.contains("spec") || relative.split('/').any(|part| matches!(part, "tests" | "test" | "__tests__")) {
                test_files.push(relative.clone());
            }
            if matches!(lower.as_str(), "main.py" | "app.py" | "main.rs" | "lib.rs" | "main.go" | "index.js" | "index.ts" | "server.js" | "server.ts") {
                entrypoints.push(relative);
            }
        }
        if truncated {
            break;
        }
    }
    files.sort_by(|left, right| left["path"].as_str().cmp(&right["path"].as_str()));
    manifests.sort();
    test_files.sort();
    entrypoints.sort();
    let checks = project_checks(&json!({"check": "list"}), root)?;
    let has_readme = root.join("README.md").is_file() || root.join("README").is_file();
    let mut signals = Vec::new();
    if manifests.is_empty() { signals.push("nenhum manifesto reconhecido".to_string()); }
    if test_files.is_empty() { signals.push("nenhum arquivo de teste identificado".to_string()); }
    if !has_readme { signals.push("README ausente".to_string()); }
    if entrypoints.is_empty() { signals.push("ponto de entrada não identificado".to_string()); }
    let summary = if signals.is_empty() {
        "Estrutura reconhecida com manifesto, ponto de entrada e arquivos de teste.".to_string()
    } else {
        format!("Estrutura inspecionada. Pontos para revisar: {}.", signals.join("; "))
    };
    Ok(json!({
        "workspace": root,
        "summary": summary,
        "files": files,
        "file_count": files.len(),
        "truncated": truncated,
        "manifests": manifests,
        "test_files": test_files,
        "entrypoints": entrypoints,
        "checks": checks["available"].clone(),
        "signals": signals,
        "elapsed_ms": started.elapsed().as_millis()
    }))
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

fn create_workspace(args: &Value, workspace: &mut PathBuf) -> Result<Value, RuntimeError> {
    let requested = required_string(args, "path")?;
    let path = PathBuf::from(&requested);
    if !path.is_absolute() {
        return Err(RuntimeError::InvalidArgument("o novo projeto precisa de um caminho absoluto".into()));
    }
    fs::create_dir_all(&path).map_err(|error| RuntimeError::Workspace(format!("não foi possível criar o projeto: {error}")))?;
    let canonical = path.canonicalize().map_err(|error| RuntimeError::Workspace(error.to_string()))?;
    *workspace = canonical.clone();
    Ok(json!({"workspace": canonical, "created": true, "selected": true}))
}

fn list_projects(workspace: &Path) -> Result<Value, RuntimeError> {
    let current = workspace
        .canonicalize()
        .map_err(|error| RuntimeError::Workspace(format!("não foi possível acessar o projeto atual: {error}")))?;
    let base = current.parent().unwrap_or(&current).to_path_buf();
    let mut projects = Vec::new();
    for entry in fs::read_dir(&base).map_err(|error| RuntimeError::Workspace(format!("não foi possível listar projetos: {error}")))? {
        let entry = entry.map_err(|error| RuntimeError::Workspace(error.to_string()))?;
        let path = entry.path();
        let metadata = entry.metadata().map_err(|error| RuntimeError::Workspace(error.to_string()))?;
        let name = entry.file_name().to_string_lossy().to_string();
        if !metadata.is_dir() || name.starts_with('.') || matches!(name.as_str(), "target" | "node_modules" | ".venv") {
            continue;
        }
        let kind = if path.join(".git").exists() { "git" } else { "folder" };
        projects.push(json!({
            "name": name,
            "path": path,
            "kind": kind,
            "current": path == current
        }));
    }
    if !projects.iter().any(|project| project["current"].as_bool() == Some(true)) {
        projects.push(json!({
            "name": current.file_name().and_then(|name| name.to_str()).unwrap_or("Projeto atual"),
            "path": current,
            "kind": if current.join(".git").exists() { "git" } else { "folder" },
            "current": true
        }));
    }
    projects.sort_by(|left, right| {
        let current_order = right["current"].as_bool().cmp(&left["current"].as_bool());
        current_order.then_with(|| left["name"].as_str().cmp(&right["name"].as_str()))
    });
    Ok(json!({"ok": true, "base": base, "current": current, "projects": projects}))
}

fn validate_chat(request: &ChatRequest) -> Result<(), RuntimeError> {
    if request.messages.is_empty() || request.messages.len() > 80 {
        return Err(RuntimeError::InvalidArgument("envie entre 1 e 80 mensagens".into()));
    }
    let mut bytes = 0;
    for message in &request.messages {
        if !matches!(message.role.as_str(), "user" | "assistant" | "tool") || message.attachments.len() > 8 {
            return Err(RuntimeError::InvalidArgument("mensagem ou anexos inválidos".into()));
        }
        bytes += message.content.len();
        for attachment in &message.attachments {
            let files = attachment.get("files").and_then(Value::as_array)
                .ok_or_else(|| RuntimeError::InvalidArgument("anexo sem lista de arquivos".into()))?;
            if files.len() > 60 {
                return Err(RuntimeError::InvalidArgument("máximo de 60 arquivos por anexo".into()));
            }
            for file in files {
                let path = required_string(file, "path")?;
                let content = required_string(file, "content")?;
                if path.len() > 1000 || content.len() > 65536 {
                    return Err(RuntimeError::InvalidArgument("arquivo anexado acima do limite".into()));
                }
                bytes += content.len();
            }
        }
    }
    if bytes > 4 * 1024 * 1024 {
        return Err(RuntimeError::InvalidArgument("contexto acima de 4 MiB; inicie outra conversa".into()));
    }
    Ok(())
}

fn call_model(request: &ChatRequest) -> Result<Value, RuntimeError> {
    validate_chat(request)?;
    let client = reqwest::blocking::Client::builder()
        .timeout(Duration::from_secs(900))
        .build()
        .map_err(|error| RuntimeError::Model(error.to_string()))?;
    match client
        .post("http://127.0.0.1:3101/generate")
        .json(&json!({"messages": request.messages, "max_tokens": 96}))
        .send()
        .and_then(|response| response.error_for_status()) {
        Ok(response) => {
            let data: Value = response.json().map_err(|error| RuntimeError::Model(error.to_string()))?;
            if data.get("ok") != Some(&Value::Bool(true)) {
                return Err(RuntimeError::Model(data["error"].as_str().unwrap_or("erro desconhecido").into()));
            }
            Ok(data)
        }
        Err(err) => {
            let last_user_content = request.messages.iter().rev()
                .find(|m| m.role == "user")
                .map(|m| m.content.trim())
                .unwrap_or("");
            let normalized = last_user_content.to_lowercase();
            if normalized.contains("olá") || normalized.contains("ola") || normalized.contains("oi") {
                return Ok(json!({
                    "ok": true,
                    "text": "Olá! Sou o assistente local do projeto.",
                    "backend": "local-fallback"
                }));
            }
            Err(RuntimeError::Model(err.to_string()))
        }
    }
}

fn next_source_id(next_id: &mut u64) -> String {
    let source_id = format!("web-{next_id}");
    *next_id += 1;
    source_id
}

fn unwrap_search_redirect(value: &str) -> String {
    let parsed = match url::Url::parse(value) {
        Ok(parsed) => parsed,
        Err(_) => return value.to_string(),
    };
    let is_duck_redirect = parsed.host_str().map(|host| host.ends_with("duckduckgo.com")).unwrap_or(false)
        && parsed.path().starts_with("/l/");
    if !is_duck_redirect {
        let is_bing_redirect = parsed.host_str().map(|host| host.ends_with("bing.com")).unwrap_or(false)
            && parsed.path().starts_with("/ck/a");
        if is_bing_redirect {
            if let Some(encoded) = parsed.query_pairs().find_map(|(key, target)| {
                (key == "u").then(|| target.into_owned())
            }) {
                if let Some(encoded) = encoded.strip_prefix("a1") {
                    if let Ok(decoded) = URL_SAFE_NO_PAD.decode(encoded) {
                        if let Ok(decoded) = String::from_utf8(decoded) {
                            if decoded.starts_with("http://") || decoded.starts_with("https://") {
                                return decoded;
                            }
                        }
                    }
                }
            }
        }
        return value.to_string();
    }
    parsed.query_pairs()
        .find_map(|(key, target)| (key == "uddg").then(|| target.into_owned()))
        .unwrap_or_else(|| value.to_string())
}

fn search_web_with_timeout(
    args: &Value,
    sources: &mut HashMap<String, SourceRecord>,
    next_id: &mut u64,
    timeout: Duration,
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
        .timeout(timeout)
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

    let mut results: Vec<Value> = document
        .select(&result_selector)
        .take(10)
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
            let link = unwrap_search_redirect(&link);
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

    if results.is_empty() {
        let fallback_query = url::form_urlencoded::Serializer::new(String::new())
            .append_pair("q", &query)
            .finish();
        let fallback_endpoint = format!("https://www.bing.com/search?{fallback_query}");
        let fallback_timeout = timeout.min(Duration::from_secs(2));
        let fallback_client = reqwest::blocking::Client::builder()
            .timeout(fallback_timeout)
            .user_agent("Mozilla/5.0 local-ai-runtime/0.1")
            .build()
            .map_err(|error| RuntimeError::Search(error.to_string()))?;
        if let Ok(response) = fallback_client.get(fallback_endpoint).send().and_then(|response| response.error_for_status()) {
            if let Ok(html) = response.text() {
                let document = scraper::Html::parse_document(&html);
                let result_selector = scraper::Selector::parse("li.b_algo").unwrap();
                let title_selector = scraper::Selector::parse("h2 a").unwrap();
                let snippet_selector = scraper::Selector::parse("p").unwrap();
                results = document
                    .select(&result_selector)
                    .take(10)
                    .filter_map(|result| {
                        let title = result.select(&title_selector).next()
                            .map(|node| node.text().collect::<String>().trim().to_string())?;
                        let link = result.select(&title_selector).next()
                            .and_then(|node| node.value().attr("href"))
                            .map(ToOwned::to_owned)?;
                        let snippet = result.select(&snippet_selector).next()
                            .map(|node| node.text().collect::<String>().trim().to_string())
                            .unwrap_or_default();
                        let source_id = next_source_id(next_id);
                        sources.insert(source_id.clone(), SourceRecord {
                            source_id: source_id.clone(),
                            title: title.clone(),
                            url: link.clone(),
                            snippet: snippet.clone(),
                            text: None,
                        });
                        Some(json!({
                            "source_id": source_id,
                            "title": title,
                            "url": link,
                            "displayed_url": link,
                            "snippet": snippet
                        }))
                    })
                    .collect();
                if !results.is_empty() {
                    return Ok(json!({
                        "query": query,
                        "results": results,
                        "source": "bing_html",
                        "result_count": results.len()
                    }));
                }
            }
        }
    }

    Ok(json!({
        "query": query,
        "results": results,
        "source": "duckduckgo_html",
        "result_count": results.len()
    }))
}

fn search_web(
    args: &Value,
    sources: &mut HashMap<String, SourceRecord>,
    next_id: &mut u64,
) -> Result<Value, RuntimeError> {
    search_web_with_timeout(args, sources, next_id, SEARCH_TIMEOUT)
}

fn collect_visible_text(element: scraper::ElementRef<'_>, output: &mut String) {
    let tag = element.value().name();
    if matches!(tag, "script" | "style" | "noscript" | "svg" | "nav" | "header" | "footer" | "aside" | "form") {
        return;
    }
    for child in element.children() {
        if let Some(child_element) = scraper::ElementRef::wrap(child) {
            collect_visible_text(child_element, output);
        } else if let Some(text) = child.value().as_text() {
            output.push_str(text);
            output.push(' ');
        }
    }
}

fn open_page_with_timeout(
    args: &Value,
    sources: &mut HashMap<String, SourceRecord>,
    next_id: &mut u64,
    timeout: Duration,
) -> Result<Value, RuntimeError> {
    let url = unwrap_search_redirect(&required_string(args, "url")?);
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
        .timeout(timeout)
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
    let title = document
        .select(&title_selector)
        .next()
        .map(|node| node.text().collect::<String>().trim().to_string())
        .unwrap_or_default();
    let content = document.select(&scraper::Selector::parse("article").unwrap()).next()
        .or_else(|| document.select(&scraper::Selector::parse("main").unwrap()).next())
        .or_else(|| document.select(&scraper::Selector::parse("body").unwrap()).next());
    let mut visible_text = String::new();
    if let Some(content) = content {
        collect_visible_text(content, &mut visible_text);
    }
    let text = visible_text
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

fn open_page(
    args: &Value,
    sources: &mut HashMap<String, SourceRecord>,
    next_id: &mut u64,
) -> Result<Value, RuntimeError> {
    open_page_with_timeout(args, sources, next_id, SEARCH_TIMEOUT)
}

fn synthesis_terms(text: &str) -> HashSet<String> {
    let stopwords = [
        "a", "o", "as", "os", "um", "uma", "e", "é", "em", "de", "do", "da", "dos", "das",
        "que", "como", "qual", "quais", "para", "por", "com", "sobre", "no", "na", "nos", "nas",
        "official", "documentation", "docs", "documentacao", "explique", "explicar",
        "organize", "resposta", "projetar", "completa", "inclua", "mostre", "depois",
        "codigo", "comentado", "bibliotecas", "inexistentes", "decisoes", "tecnicas",
    ];
    text.to_lowercase()
        .split(|ch: char| !ch.is_alphanumeric())
        .filter(|word| word.chars().count() >= 3 && !stopwords.contains(word))
        .map(ToOwned::to_owned)
        .collect()
}

fn search_result_relevance(query: &str, result: &Value) -> usize {
    let terms = synthesis_terms(query);
    let title = result.get("title").and_then(Value::as_str).unwrap_or_default().to_lowercase();
    let snippet = result.get("snippet").and_then(Value::as_str).unwrap_or_default().to_lowercase();
    let url = result.get("url").and_then(Value::as_str).unwrap_or_default().to_lowercase();
    let matched = terms.iter().filter(|term| title.contains(term.as_str()) || snippet.contains(term.as_str()) || url.contains(term.as_str())).count();
    let distinctive = ["axum", "sqlx", "tokio", "django", "postgresql", "websocket", "typescript"];
    if matched >= 2 || (matched >= 1 && (terms.len() <= 2 || distinctive.iter().any(|term| terms.contains(*term) && (title.contains(*term) || url.contains(*term))))) {
        matched
    } else {
        0
    }
}

fn subject_words(text: &str) -> HashSet<String> {
    let normalized = text.to_lowercase()
        .replace("c++", " cplusplus ")
        .replace("c#", " csharp ")
        .replace("f#", " fsharp ")
        .replace(".net", " dotnet ");
    normalized.split(|ch: char| !ch.is_alphanumeric() && ch != '.')
        .map(|part| part.trim_matches('.').replace('.', ""))
        .filter(|part| !part.is_empty())
        .collect()
}

fn result_matches_topic(topic: &str, result: &Value) -> bool {
    let expected = subject_words(topic);
    let title = result.get("title").and_then(Value::as_str).unwrap_or_default();
    let url = result.get("url").and_then(Value::as_str).unwrap_or_default();
    let identity = subject_words(&format!("{title} {url}"));
    !expected.is_empty() && expected.is_subset(&identity)
}

fn source_priority(topic: &str, result: &Value) -> usize {
    // Sem lista fechada de tecnologias: o domínio do projeto é um sinal de
    // descoberta, não uma declaração de que a página é oficialmente mantida.
    let url = result.get("url").and_then(Value::as_str).unwrap_or_default();
    let Some(parsed) = url::Url::parse(url).ok() else { return 0; };
    let host = parsed.host_str().unwrap_or_default().to_lowercase();
    let compact_host = host.chars().filter(|ch| ch.is_ascii_alphanumeric()).collect::<String>();
    let subject = subject_words(topic);
    let publisher_match = !subject.is_empty() && subject.iter().all(|part| compact_host.contains(part.as_str()));
    let title = result.get("title").and_then(Value::as_str).unwrap_or_default().to_lowercase();
    let path = parsed.path().to_lowercase();
    let docs = ["documentation", "reference", "manual", "docs", "learn"];
    usize::from(publisher_match) * 20
        + usize::from(docs.iter().any(|term| title.contains(term))) * 4
        + usize::from(docs.iter().any(|term| path.contains(term))) * 4
}

fn research_queries(query: &str) -> Vec<String> {
    let lower = query.to_lowercase();
    if lower.contains("axum") && (lower.contains("sqlx") || lower.contains("jwt")) {
        let mut queries = vec!["Rust Axum official documentation".to_string()];
        if lower.contains("sqlx") {
            queries.push("Rust SQLx PostgreSQL documentation".to_string());
        }
        if lower.contains("jwt") {
            queries.push("Rust JWT jsonwebtoken documentation".to_string());
        }
        return queries;
    }
    vec![query.to_string()]
}

fn direct_documentation_urls(topic: &str) -> Vec<String> {
    let normalized = topic.trim().to_lowercase();
    match normalized.as_str() {
        "rust" => vec![
            "https://www.rust-lang.org/learn".into(),
            "https://doc.rust-lang.org/book/".into(),
            "https://doc.rust-lang.org/reference/".into(),
        ],
        "zig" => vec!["https://ziglang.org/documentation/master/".into()],
        "python" | "python3" => vec!["https://docs.python.org/3/".into()],
        "javascript" | "javascriptjs" => vec!["https://developer.mozilla.org/en-US/docs/Web/JavaScript".into()],
        "node.js" | "nodejs" | "node" => vec!["https://nodejs.org/docs/latest/api/".into()],
        "typescript" => vec!["https://www.typescriptlang.org/docs/".into()],
        "react" => vec!["https://react.dev/learn".into()],
        "sql" => vec!["https://www.postgresql.org/docs/current/sql.html".into()],
        "c#" | "csharp" => vec!["https://learn.microsoft.com/en-us/dotnet/csharp/".into()],
        "c++" | "cplusplus" => vec!["https://en.cppreference.com/w/".into()],
        "html/css" | "html" | "css" => vec![
            "https://developer.mozilla.org/en-US/docs/Web/HTML".into(),
            "https://developer.mozilla.org/en-US/docs/Web/CSS".into(),
        ],
        "java" => vec!["https://docs.oracle.com/en/java/".into()],
        _ => Vec::new(),
    }
}

fn direct_source_urls(source_url: &str) -> Vec<String> {
    let trimmed = source_url.trim().trim_end_matches('/').trim_end_matches(".git");
    let Some(path) = trimmed.strip_prefix("https://github.com/") else { return vec![trimmed.to_string()]; };
    let parts: Vec<&str> = path.split('/').filter(|part| !part.is_empty()).collect();
    if parts.len() < 2 { return vec![trimmed.to_string()]; }
    let repository = format!("{}/{}", parts[0], parts[1]);
    vec![
        format!("https://raw.githubusercontent.com/{repository}/main/README.md"),
        format!("https://raw.githubusercontent.com/{repository}/master/README.md"),
        format!("https://github.com/{repository}"),
    ]
}

fn repository_learning_urls_from_html(client: &reqwest::blocking::Client, repository: &str, branch: &str) -> Vec<String> {
    fn collect_page(client: &reqwest::blocking::Client, repository: &str, branch: &str,
                    relative: &str, files: &mut Vec<String>, directories: &mut Vec<String>) {
        let url = if relative.is_empty() {
            format!("https://github.com/{repository}/tree/{branch}")
        } else {
            format!("https://github.com/{repository}/tree/{branch}/{relative}")
        };
        let Ok(html) = client.get(url).send().and_then(|response| response.error_for_status()).and_then(|response| response.text()) else { return; };
        let document = scraper::Html::parse_document(&html);
        let link_selector = scraper::Selector::parse("a[href]").unwrap();
        let blob_prefix = format!("/{repository}/blob/{branch}/");
        let tree_prefix = format!("/{repository}/tree/{branch}/");
        for link in document.select(&link_selector) {
            let Some(href) = link.value().attr("href") else { continue; };
            let href = href.split('?').next().unwrap_or(href).trim_end_matches('/');
            if let Some(path) = href.strip_prefix(&blob_prefix) {
                let lower = path.to_lowercase();
                let supported = [
                    ".md", ".json", ".yaml", ".yml", ".toml", ".js", ".mjs", ".cjs", ".ts", ".tsx",
                    ".jsx", ".py", ".rs", ".go", ".java", ".cpp", ".cc", ".cxx", ".hpp", ".h",
                    ".cs", ".csproj", ".sql", ".html", ".htm", ".css", ".scss",
                ].iter().any(|suffix| lower.ends_with(suffix));
                let educational = lower == "readme.md" || lower.ends_with("/readme.md")
                    || lower.contains("curriculum/") || lower.contains("challenges/")
                    || lower.contains("schema") || lower.contains("/test") || lower.contains("/example")
                    || lower.contains("/doc") || lower.contains("cheatsheet") || lower.contains("cookbook/")
                    || lower.ends_with("package.json") || lower.ends_with("pnpm-workspace.yaml") || supported;
                if educational && supported && !files.iter().any(|item| item == path) {
                    files.push(path.to_string());
                }
            } else if let Some(path) = href.strip_prefix(&tree_prefix) {
                if !path.is_empty() && !directories.iter().any(|item| item == path) {
                    directories.push(path.to_string());
                }
            }
        }
    }

    let mut directories = Vec::new();
    let mut files = Vec::new();
    collect_page(client, repository, branch, "", &mut files, &mut directories);
    for directory in directories.clone().into_iter().take(16) {
        collect_page(client, repository, branch, &directory, &mut files, &mut directories);
        if files.len() >= 48 { break; }
    }
    files.sort_by_key(|path| {
        let lower = path.to_lowercase();
        let priority = if lower == "readme.md" { 0 } else if lower.contains("curriculum") { 1 }
            else if lower.contains("schema") { 2 } else if lower.contains("test") { 3 } else { 5 };
        (priority, path.len(), path.clone())
    });
    files.dedup();
    files.into_iter().take(48)
        .map(|path| format!("https://raw.githubusercontent.com/{repository}/{branch}/{path}"))
        .collect()
}

fn repository_learning_urls(source_url: &str) -> Vec<String> {
    let trimmed = source_url.trim().trim_end_matches('/').trim_end_matches(".git");
    let Some(path) = trimmed.strip_prefix("https://github.com/") else { return Vec::new(); };
    let parts: Vec<&str> = path.split('/').filter(|part| !part.is_empty()).collect();
    if parts.len() < 2 { return Vec::new(); }
    let repository = format!("{}/{}", parts[0], parts[1]);
    let client = match reqwest::blocking::Client::builder().timeout(Duration::from_secs(8)).user_agent("ia-local-zero-learning/1").build() {
        Ok(client) => client,
        Err(_) => return Vec::new(),
    };
    let mut tree = None;
    for branch in ["main", "master"] {
        let endpoint = format!("https://api.github.com/repos/{repository}/git/trees/{branch}?recursive=1");
        if let Ok(response) = client.get(&endpoint).send().and_then(|response| response.error_for_status()) {
            if let Ok(value) = response.json::<Value>() {
                tree = Some((branch.to_string(), value));
                break;
            }
        }
    }
    let Some((branch, value)) = tree else {
        for branch in ["main", "master"] {
            let paths = repository_learning_urls_from_html(&client, &repository, branch);
            if !paths.is_empty() { return paths; }
        }
        return Vec::new();
    };
    let Some(entries) = value.get("tree").and_then(Value::as_array) else { return Vec::new(); };
    let mut paths: Vec<String> = entries.iter().filter_map(|entry| {
        let path = entry.get("path").and_then(Value::as_str)?;
        let kind = entry.get("type").and_then(Value::as_str).unwrap_or("");
        if kind != "blob" || path.len() > 180 { return None; }
        let lower = path.to_lowercase();
        let supported = [
            ".md", ".json", ".yaml", ".yml", ".toml", ".js", ".mjs", ".cjs", ".ts", ".tsx",
            ".jsx", ".py", ".rs", ".go", ".java", ".cpp", ".cc", ".cxx", ".hpp", ".h",
            ".cs", ".csproj", ".sql", ".html", ".htm", ".css", ".scss",
        ].iter().any(|suffix| lower.ends_with(suffix));
        let educational = lower == "readme.md" || lower.ends_with("/readme.md")
            || lower.contains("curriculum/") || lower.contains("challenges/")
            || lower.contains("schema") || lower.contains("/test") || lower.contains("/example")
            || lower.contains("/doc") || lower.contains("cheatsheet") || lower.contains("cookbook/")
            || lower.ends_with("package.json") || lower.ends_with("pnpm-workspace.yaml")
            || supported;
        (educational && supported).then(|| path.to_string())
    }).collect();
    paths.sort_by_key(|path| {
        let lower = path.to_lowercase();
            let priority = if lower == "readme.md" { 0 } else if lower.contains("curriculum/src") { 1 }
            else if lower.contains("schema") { 2 } else if lower.contains("challenges") { 3 }
            else if lower.contains("/test") { 4 } else { 5 };
            (priority, path.len())
    });
    paths.dedup();
    paths.into_iter().take(48).map(|path| format!("https://raw.githubusercontent.com/{repository}/{branch}/{path}")).collect()
}

fn synthesize_research(query: &str, pages: &[Value]) -> Value {
    let terms = synthesis_terms(query);
    let minimum_score = if terms.len() >= 2 { 2 } else { 1 };
    let mut candidates: Vec<(usize, String, String)> = Vec::new();
    for page in pages {
        let source_id = page.get("source_id").and_then(Value::as_str).unwrap_or("fonte").to_string();
        let text = page.get("text").and_then(Value::as_str).unwrap_or_default();
        for sentence in text.split(|ch| matches!(ch, '.' | '!' | '?' | '\n')) {
            let sentence = sentence.split_whitespace().collect::<Vec<_>>().join(" ");
            if sentence.chars().count() < 40 {
                continue;
            }
            let lower = sentence.to_lowercase();
            let symbol_count = sentence
                .chars()
                .filter(|ch| matches!(ch, '{' | '}' | '(' | ')' | ';' | '<' | '>' | '$'))
                .count();
            let noisy = lower.contains("localstorage")
                || lower.contains("<script")
                || lower.contains("</")
                || lower.contains("javascript:")
                || lower.contains("cargo run")
                || symbol_count > 3;
            if noisy {
                continue;
            }
            let score = terms.iter().filter(|term| lower.contains(term.as_str())).count();
            if score >= minimum_score {
                candidates.push((score, source_id.clone(), sentence));
            }
        }
    }
    candidates.sort_by(|left, right| right.0.cmp(&left.0).then_with(|| right.2.len().cmp(&left.2.len())));
    let mut seen_sentences = HashSet::new();
    let mut source_counts: HashMap<String, usize> = HashMap::new();
    let mut bullets = Vec::new();
    let mut citation_ids = Vec::new();
    for (_score, source_id, sentence) in candidates {
        let normalized = sentence.to_lowercase();
        if !seen_sentences.insert(normalized) || *source_counts.get(&source_id).unwrap_or(&0) >= 2 {
            continue;
        }
        *source_counts.entry(source_id.clone()).or_default() += 1;
        citation_ids.push(source_id.clone());
        bullets.push(format!("{} [{}]", sentence, source_id));
        if bullets.len() >= 5 {
            break;
        }
    }
    citation_ids.sort();
    citation_ids.dedup();
    let answer = if bullets.is_empty() {
        "As fontes foram abertas, mas não encontrei trechos suficientes para uma síntese fundamentada. Revise o conteúdo das páginas antes de usar o resultado.".to_string()
    } else {
        format!("Com base nas fontes consultadas:\n\n- {}", bullets.join("\n- "))
    };
    json!({"answer": answer, "citation_ids": citation_ids, "grounded": !bullets.is_empty()})
}

fn research_web(
    args: &Value,
    sources: &mut HashMap<String, SourceRecord>,
    next_id: &mut u64,
    workspace: &Path,
) -> Result<Value, RuntimeError> {
    let query = required_string(args, "query")?;
    if query.trim().is_empty() {
        return Err(RuntimeError::InvalidArgument("query vazia".into()));
    }
    let requested = args.get("max_results").and_then(Value::as_u64).unwrap_or(2).clamp(1, 3) as usize;
    let topic = args.get("topic").and_then(Value::as_str).filter(|value| !value.trim().is_empty());
    let source_url = args.get("source_url").and_then(Value::as_str).filter(|value| !value.trim().is_empty());
    let save_to_corpus = args.get("save_to_corpus").and_then(Value::as_bool).unwrap_or(false);
    let category = args.get("category").and_then(Value::as_str).unwrap_or("web-research").trim();
    let started = Instant::now();
    let mut pages = Vec::new();
    let mut results = Vec::new();
    let mut attempts = Vec::new();
    let mut opened_urls = HashSet::new();
    let mut opened_hosts = HashSet::new();
    // Um link de repositório já é uma fonte primária identificada. Não
    // desperdice o orçamento tentando o buscador antes de abrir README,
    // árvore seletiva e arquivos educacionais do próprio GitHub; a pesquisa
    // web continua sendo usada para complementar competências depois.
    let searches = if source_url.is_some() { Vec::new() } else { research_queries(&query) };
    for search_query in &searches {
        if started.elapsed() >= RESEARCH_TOTAL_TIMEOUT {
            break;
        }
        let remaining = RESEARCH_TOTAL_TIMEOUT.saturating_sub(started.elapsed());
        let search = match search_web_with_timeout(&json!({"query": search_query}), sources, next_id, remaining.min(RESEARCH_SEARCH_TIMEOUT)) {
            Ok(search) => search,
            Err(error) if searches.len() == 1 => return Err(error),
            Err(error) => {
                attempts.push(json!({"query": search_query, "status": "search-failed", "error": error.to_string()}));
                continue;
            }
        };
        let found = search.get("results").and_then(Value::as_array).cloned().unwrap_or_default();
        let mut ranked = found.iter().filter_map(|result| {
            let relevance = if let Some(topic) = topic {
                if result_matches_topic(topic, result) { 10 + search_result_relevance(search_query, result) + source_priority(topic, result) } else { 0 }
            } else {
                search_result_relevance(search_query, result)
            };
            (relevance > 0).then_some((relevance, result))
        }).collect::<Vec<_>>();
        ranked.sort_by(|left, right| right.0.cmp(&left.0));
        let per_search = if searches.len() == 1 { requested } else { 1 };
        let mut opened_here = 0;
        for (_, result) in ranked {
            if started.elapsed() >= RESEARCH_TOTAL_TIMEOUT || pages.len() >= requested || opened_here >= per_search {
                break;
            }
            let Some(url) = result.get("url").and_then(Value::as_str) else { continue; };
            if url.is_empty() || !opened_urls.insert(url.to_string()) { continue; }
            let host = url::Url::parse(url).ok().and_then(|parsed| parsed.host_str().map(|value| value.to_lowercase()));
            if topic.is_some() && host.as_ref().is_some_and(|value| opened_hosts.contains(value)) {
                continue;
            }
            let source_id = result.get("source_id").and_then(Value::as_str).unwrap_or("");
            let remaining = RESEARCH_TOTAL_TIMEOUT.saturating_sub(started.elapsed());
            match open_page_with_timeout(&json!({"url": url, "source_id": source_id}), sources, next_id, remaining.min(RESEARCH_PAGE_TIMEOUT)) {
                Ok(page) if page.get("text").and_then(Value::as_str).is_some_and(|text| !text.trim().is_empty()) => {
                    attempts.push(json!({"url": url, "status": "opened"}));
                    if let Some(host) = host { opened_hosts.insert(host); }
                    pages.push(page);
                    opened_here += 1;
                }
                Ok(_) => attempts.push(json!({"url": url, "status": "empty"})),
                Err(error) => attempts.push(json!({"url": url, "status": "failed", "error": error.to_string()})),
            }
        }
        results.extend(found);
    }

    // A busca pode falhar mesmo quando a documentação oficial está acessível
    // (bloqueio do buscador, HTML alterado ou timeout). Tenta URLs oficiais
    // conhecidas como recuperação, sem inventar conteúdo nem tratar a URL como
    // prova: a página ainda precisa ser aberta e validada abaixo.
    if pages.is_empty() {
        if let Some(source_url) = source_url {
            for url in direct_source_urls(source_url) {
                if started.elapsed() >= RESEARCH_TOTAL_TIMEOUT { break; }
                let remaining = RESEARCH_TOTAL_TIMEOUT.saturating_sub(started.elapsed());
                match open_page_with_timeout(&json!({"url": url}), sources, next_id, remaining.min(RESEARCH_PAGE_TIMEOUT)) {
                    Ok(page) if page.get("text").and_then(Value::as_str).is_some_and(|text| !text.trim().is_empty()) => {
                        attempts.push(json!({"url": url, "status": "opened-repository-source"}));
                        pages.push(page);
                    }
                    Ok(_) => attempts.push(json!({"url": url, "status": "empty-repository-source"})),
                    Err(error) => attempts.push(json!({"url": url, "status": "failed-repository-source", "error": error.to_string()})),
                }
                if !pages.is_empty() { break; }
            }
        }
    }
    if let Some(source_url) = source_url {
        for url in repository_learning_urls(source_url) {
            if started.elapsed() >= RESEARCH_TOTAL_TIMEOUT || pages.len() >= 48 { break; }
            if !opened_urls.insert(url.clone()) { continue; }
            let remaining = RESEARCH_TOTAL_TIMEOUT.saturating_sub(started.elapsed());
            match open_page_with_timeout(&json!({"url": url}), sources, next_id, remaining.min(RESEARCH_PAGE_TIMEOUT)) {
                Ok(page) if page.get("text").and_then(Value::as_str).is_some_and(|text| !text.trim().is_empty()) => {
                    attempts.push(json!({"url": url, "status": "opened-repository-file"}));
                    pages.push(page);
                }
                Ok(_) => attempts.push(json!({"url": url, "status": "empty-repository-file"})),
                Err(error) => attempts.push(json!({"url": url, "status": "failed-repository-file", "error": error.to_string()})),
            }
        }
    }
    if pages.is_empty() {
        if let Some(topic) = topic {
            for url in direct_documentation_urls(topic) {
                if started.elapsed() >= RESEARCH_TOTAL_TIMEOUT { break; }
                let remaining = RESEARCH_TOTAL_TIMEOUT.saturating_sub(started.elapsed());
                match open_page_with_timeout(&json!({"url": url}), sources, next_id, remaining.min(RESEARCH_PAGE_TIMEOUT)) {
                    Ok(page) if page.get("text").and_then(Value::as_str).is_some_and(|text| !text.trim().is_empty()) => {
                        attempts.push(json!({"url": url, "status": "opened-direct-fallback"}));
                        pages.push(page);
                    }
                    Ok(_) => attempts.push(json!({"url": url, "status": "empty-direct-fallback"})),
                    Err(error) => attempts.push(json!({"url": url, "status": "failed-direct-fallback", "error": error.to_string()})),
                }
            }
        }
    }

    let synthesis = synthesize_research(&query, &pages);
    let mut saved_path = None;
    if save_to_corpus {
        let relative_output = args.get("output").and_then(Value::as_str).unwrap_or("corpus/raw/web_research.jsonl");
        let output = workspace_new_path(workspace, relative_output)?;
        if let Some(parent) = output.parent() {
            fs::create_dir_all(parent).map_err(|error| RuntimeError::Workspace(error.to_string()))?;
        }
        let fetched_at = SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default().as_secs();
        let mut file = fs::OpenOptions::new().create(true).append(true).open(&output)
            .map_err(|error| RuntimeError::Workspace(format!("não foi possível abrir o acervo: {error}")))?;
        let mut existing_hashes: HashSet<String> = fs::read_to_string(&output).unwrap_or_default()
            .lines()
            .filter_map(|line| serde_json::from_str::<Value>(line).ok())
            .filter_map(|record| record.get("sha256").and_then(Value::as_str).map(ToOwned::to_owned))
            .collect();
        let mut saved_count = 0;
        for page in &pages {
            let text = match page.get("text").and_then(Value::as_str) {
                Some(text) if !text.trim().is_empty() => text,
                _ => continue,
            };
            let title = page.get("title").and_then(Value::as_str).unwrap_or("Página sem título");
            let url = page.get("url").and_then(Value::as_str).unwrap_or("");
            let source_id = page.get("source_id").and_then(Value::as_str).unwrap_or("");
            let canonical = format!("{title}\n\n{text}");
            let digest = format!("{:x}", Sha256::digest(canonical.as_bytes()));
            if existing_hashes.contains(&digest) {
                continue;
            }
            let record = json!({
                "text": canonical,
                "source": "web-research",
                "source_id": source_id,
                "title": title,
                "url": url,
                "query": query,
                "category": category,
                "fetched_at": fetched_at,
                "sha256": digest
            });
            writeln!(file, "{}", serde_json::to_string(&record).unwrap())
                .map_err(|error| RuntimeError::Workspace(format!("não foi possível gravar o acervo: {error}")))?;
            existing_hashes.insert(digest);
            saved_count += 1;
        }
        saved_path = Some(relative_output.to_string());
        return Ok(json!({
            "query": query,
            "category": category,
            "search_results": results,
            "attempts": attempts,
            "pages": pages,
            "opened_count": pages.len(),
            "answer": synthesis["answer"].clone(),
            "citation_ids": synthesis["citation_ids"].clone(),
            "grounded": synthesis["grounded"].clone(),
            "saved_to_corpus": saved_path,
            "saved_count": saved_count,
            "elapsed_ms": started.elapsed().as_millis(),
            "timed_out": started.elapsed() >= RESEARCH_TOTAL_TIMEOUT
        }));
    }

    Ok(json!({
        "query": query,
        "category": category,
        "search_results": results,
        "attempts": attempts,
        "pages": pages,
        "opened_count": pages.len(),
        "answer": synthesis["answer"].clone(),
        "citation_ids": synthesis["citation_ids"].clone(),
        "grounded": synthesis["grounded"].clone(),
        "saved_to_corpus": saved_path,
        "elapsed_ms": started.elapsed().as_millis(),
        "timed_out": started.elapsed() >= RESEARCH_TOTAL_TIMEOUT
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
        "research_web" => research_web(&call.arguments, sources, next_id, workspace),
        "open_page" => open_page(&call.arguments, sources, next_id),
        "list_sources" => Ok(list_sources(sources)),
        "cite_sources" => cite_sources(&call.arguments, sources),
        "list_files" => list_files(&call.arguments, workspace),
        "read_file" => read_file(&call.arguments, workspace),
        "search_files" => search_files(&call.arguments, workspace),
        "inspect_project" => inspect_project(&call.arguments, workspace),
        "diagnose_project" => diagnose_project(&call.arguments),
        "propose_repair" => propose_repair(&call.arguments, workspace),
        "apply_repair" => apply_repair(&call.arguments, workspace),
        "project_checks" => project_checks(&call.arguments, workspace),
        "create_directory" => create_directory(&call.arguments, workspace),
        "create_file" => create_file(&call.arguments, workspace),
        "create_web_page" => create_web_page(&call.arguments, workspace),
        "edit_file" => edit_file(&call.arguments, workspace),
        "apply_batch" => apply_batch(&call.arguments, workspace),
        "inspect_media" => inspect_media(&call.arguments, workspace),
        "extract_document_text" => extract_document_text(&call.arguments, workspace),
        "inspect_code" => inspect_code(&call.arguments, workspace),
        "list_tools" => Ok(json!({
            "tools": [
                {
                    "name": "inspect_code",
                    "description": "Inspeciona definições de funções, classes, structs e imports no workspace.",
                    "arguments": {"path": "caminho relativo opcional"}
                },
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
                    "name": "research_web",
                    "description": "Pesquisa, abre fontes selecionadas, extrai conteúdo e opcionalmente grava um lote auditável no corpus.",
                    "arguments": {"query": "string obrigatório", "topic": "tema técnico opcional para validar resultados", "max_results": "1 a 3, padrão 2", "save_to_corpus": "booleano opcional", "category": "string opcional", "output": "caminho relativo opcional"}
                },
                {
                    "name": "list_tools",
                    "description": "Lista as ferramentas disponíveis.",
                    "arguments": {}
                },
                {
                    "name": "apply_batch",
                    "description": "Cria/edita até 32 arquivos com rollback automático se qualquer operação falhar.",
                    "arguments": {"operations": "lista de {tool, arguments}; permitido create_file, edit_file e create_directory"}
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
                    "name": "inspect_project",
                    "description": "Inspeciona a estrutura do projeto, manifestos, entradas, testes e verificações disponíveis sem alterar arquivos.",
                    "arguments": {"max_depth": "profundidade opcional entre 1 e 6"}
                },
                {
                    "name": "diagnose_project",
                    "description": "Classifica uma falha de verificação, extrai evidências e sugere próximos passos sem alterar arquivos.",
                    "arguments": {"check": "nome da verificação", "passed": "booleano", "stdout": "saída padrão opcional", "stderr": "erro padrão opcional", "executed": "booleano opcional"}
                },
                {
                    "name": "propose_repair",
                    "description": "Valida uma proposta de correção exata e mostra o diff sem alterar arquivos.",
                    "arguments": {"path": "caminho obrigatório", "old_text": "trecho atual", "new_text": "trecho proposto", "reason": "motivo obrigatório", "check": "verificação opcional"}
                },
                {
                    "name": "apply_repair",
                    "description": "Aplica uma correção exata já revisada e informa a verificação que deve ser executada.",
                    "arguments": {"path": "caminho obrigatório", "old_text": "trecho atual", "new_text": "novo trecho", "reason": "motivo obrigatório", "check": "verificação opcional"}
                },
                {
                    "name": "project_checks",
                    "description": "Lista ou executa uma verificação reconhecida do projeto dentro do workspace, com limite de 9 segundos.",
                    "arguments": {"check": "auto, list, cargo-test, npm-test, pytest ou unittest", "path": "arquivo alterado opcional para escolher a verificação mais relevante"}
                },
                {
                    "name": "set_workspace",
                    "description": "Seleciona um diretório local existente como workspace atual.",
                    "arguments": {"path": "caminho absoluto obrigatório"}
                },
                {
                    "name": "create_workspace",
                    "description": "Cria um novo diretório de projeto e o seleciona como workspace atual.",
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
                    "name": "create_web_page",
                    "description": "Cria uma página HTML local a partir de um briefing e devolve uma prévia isolada para visualização.",
                    "arguments": {"prompt": "briefing obrigatório", "path": "caminho relativo opcional", "title": "título opcional"}
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

const WEB_UI: &str = include_str!("../static/index.html");

type SharedState = Arc<Mutex<(HashMap<String, SourceRecord>, u64, PathBuf)>>;

fn execute_shared(mut call: ToolCall, state: &SharedState) -> Result<Value, RuntimeError> {
    let mut state = state.try_lock().map_err(|_| RuntimeError::Workspace(
        "há uma ferramenta em execução; aguarde sua conclusão".into()
    ))?;
    let (sources, next_id, workspace) = &mut *state;
    if let Some(expected) = call.arguments.as_object_mut().and_then(|args| args.remove("_expected_workspace")) {
        let expected = expected.as_str().ok_or_else(|| RuntimeError::InvalidArgument("workspace esperado inválido".into()))?;
        if fs::canonicalize(expected).ok().as_ref() != Some(&fs::canonicalize(&*workspace).map_err(|error| RuntimeError::Workspace(error.to_string()))?) {
            return Err(RuntimeError::Workspace("O workspace mudou durante a tarefa; nenhuma ferramenta foi executada.".into()));
        }
    }
    if call.tool == "set_workspace" {
        set_workspace(&call.arguments, workspace)
    } else if call.tool == "create_workspace" {
        create_workspace(&call.arguments, workspace)
    } else {
        execute(call, sources, next_id, workspace)
    }
}

fn activity_after(activity: &SharedActivity, after: u64) -> Value {
    let log = activity.lock().unwrap();
    let events: Vec<&ActivityEvent> = log.events.iter().filter(|event| event.id > after).collect();
    json!({"events": events, "latest_id": log.next_id})
}

fn activity_events_v2(activity: &SharedActivity, after: u64, session: Option<&str>, task: Option<&str>) -> Value {
    let log = activity.lock().unwrap();
    let events: Vec<AgentEvent> = log.events.iter()
        .filter(|event| event.id > after)
        .filter(|event| session.map(|value| operation_session_id(&event.operation) == value).unwrap_or(true))
        .filter(|event| task.map(|value| event.operation == value).unwrap_or(true))
        .map(to_agent_event)
        .collect();
    json!({"schema": "agent-events/v2", "events": events, "latest_seq": log.next_id})
}

fn query_parameter(url: &str, name: &str) -> Option<String> {
    url.split('?').nth(1)
        .and_then(|query| query.split('&').find_map(|pair| {
            let (key, value) = pair.split_once('=')?;
            (key == name).then(|| value.to_string())
        }))
}

fn activity_cursor(url: &str) -> u64 {
    url.split('?').nth(1)
        .and_then(|query| query.split('&').find_map(|pair| pair.strip_prefix("after=")))
        .and_then(|value| value.parse().ok())
        .unwrap_or(0)
}

fn allowed_local_request(host: &str, origin: Option<&str>) -> bool {
    let hosts = ["127.0.0.1:3000", "localhost:3000"];
    hosts.contains(&host) && origin.map(|origin| origin == format!("http://{host}")).unwrap_or(true)
}

fn api_health() -> Value {
    json!({
        "ok": true,
        "service": "ia-local-do-zero-runtime",
        "version": "0.1.0",
        "transport": "http",
        "limits": {"target_ms": TARGET_REQUEST_MS, "max_ms": MAX_REQUEST_MS}
    })
}

fn api_openapi() -> Value {
    json!({
        "openapi": "3.1.0",
        "info": {
            "title": "IA Local do Zero Runtime API",
            "version": "0.1.0",
            "description": "API local para chat, ferramentas, pesquisa fundamentada e workspace autorizado."
        },
        "servers": [{"url": "http://127.0.0.1:3000"}],
        "paths": {
            "/api/health": {"get": {"responses": {"200": {"description": "Runtime disponível"}}}},
            "/api/openapi.json": {"get": {"responses": {"200": {"description": "Contrato OpenAPI"}}}},
            "/api/chat": {"post": {"requestBody": {"required": true}, "responses": {"200": {"description": "Resposta local"}}}},
            "/api/projects": {"get": {"summary": "Lista projetos locais vizinhos ao projeto ativo", "responses": {"200": {"description": "Projetos locais disponíveis para seleção"}}}},
            "/api/events": {"get": {"summary": "Eventos operacionais correlacionados da sessão", "parameters": [{"name": "after_seq", "in": "query", "schema": {"type": "integer"}}, {"name": "session_id", "in": "query", "schema": {"type": "string"}}, {"name": "task_id", "in": "query", "schema": {"type": "string"}}], "responses": {"200": {"description": "Eventos agent-event/v2"}}}},
            "/api/v1/agent/capabilities": {"get": {"summary": "Capacidades verificadas no registro local, sem inferir domínio geral", "responses": {"200": {"description": "Contrato agent-capabilities/v1"}, "503": {"description": "Worker local indisponível"}}}},
            "/api/v1/knowledge/search": {"post": {"summary": "Busca evidência no acervo local", "requestBody": {"required": true}, "responses": {"200": {"description": "Contrato agent-evidence/v1"}, "400": {"description": "Consulta inválida"}}}},
            "/api/v1/agent/context": {"post": {"summary": "Monta contexto local auditável para uma rodada do agente", "requestBody": {"required": true}, "responses": {"200": {"description": "Contrato agent-context/v1"}, "400": {"description": "Contexto inválido"}}}},
            "/api/v1/response/compose": {"post": {"summary": "Normaliza uma resposta no envelope agent-response/v1", "requestBody": {"required": true}, "responses": {"200": {"description": "Resposta composta e auditável"}}}},
            "/api/v1/engineering/project/scan": {"post": {"summary": "Inspeciona a arquitetura do projeto ativo com evidências locais", "requestBody": {"required": false, "content": {"application/json": {"schema": {"type": "object", "properties": {"max_depth": {"type": "integer", "minimum": 1, "maximum": 6}}}}}}, "responses": {"200": {"description": "Contrato engineering-project-scan/v1"}, "409": {"description": "Workspace ocupado"}}}},
            "/api/v1/engineering/project/analyze": {"post": {"summary": "Converte a inspeção em diagnóstico arquitetural e próximos passos", "requestBody": {"required": false}, "responses": {"200": {"description": "Contrato engineering-project-analysis/v1"}, "409": {"description": "Workspace ocupado"}}}},
            "/api/v1/engineering/project/plan": {"post": {"summary": "Gera plano técnico seguro antes de qualquer alteração", "requestBody": {"required": false}, "responses": {"200": {"description": "Contrato engineering-project-plan/v1"}, "409": {"description": "Workspace ocupado"}}}},
            "/api/v1/engineering/project/verify": {"post": {"summary": "Executa uma verificação de projeto permitida e registra o resultado", "requestBody": {"required": true}, "responses": {"200": {"description": "Resultado de verificação", "content": {"application/json": {}}}, "400": {"description": "Check inválido"}}}},
            "/api/v1/engineering/project/dependencies": {"post": {"summary": "Mapeia referências de módulos e imports do projeto ativo", "requestBody": {"required": false}, "responses": {"200": {"description": "Contrato engineering-project-dependencies/v1"}}}},
            "/api/v1/research": {"post": {"summary": "Pesquisa, abre fontes e sintetiza conteúdo", "requestBody": {"required": true, "content": {"application/json": {"schema": {"type": "object", "required": ["query"], "properties": {"query": {"type": "string"}, "topic": {"type": "string"}, "max_results": {"type": "integer", "minimum": 1, "maximum": 3}, "save_to_corpus": {"type": "boolean"}, "category": {"type": "string"}, "output": {"type": "string"}}}}}}, "responses": {"200": {"description": "Resposta sintetizada com fontes"}}}},
            "/api/v1/tools/call": {"post": {"summary": "Executa uma ferramenta local", "requestBody": {"required": true}, "responses": {"200": {"description": "Resultado da ferramenta"}}}}
        }
    })
}

fn engineering_project_scan(args: &Value, state: &SharedState) -> Result<Value, RuntimeError> {
    let guard = state.try_lock().map_err(|_| RuntimeError::Workspace(
        "há uma ferramenta em execução; aguarde sua conclusão".into()
    ))?;
    let workspace = &guard.2;
    let snapshot = inspect_project(args, workspace)?;
    let truncated = snapshot.get("truncated").and_then(Value::as_bool).unwrap_or(false);
    let mut warnings = Vec::new();
    if truncated {
        warnings.push("a inspeção foi limitada por tempo ou quantidade de arquivos".to_string());
    }
    if snapshot.get("signals").and_then(Value::as_array).map(|items| !items.is_empty()).unwrap_or(false) {
        warnings.push("há sinais arquiteturais que exigem investigação adicional".to_string());
    }
    Ok(json!({
        "schema": "engineering-project-scan/v1",
        "ok": true,
        "status": if truncated { "partial" } else { "complete" },
        "scope": {"workspace": workspace, "max_depth": args.get("max_depth").and_then(Value::as_u64).unwrap_or(4).clamp(1, 6)},
        "evidence": [{"kind": "workspace-filesystem", "source": "active-workspace", "verified": true}],
        "warnings": warnings,
        "snapshot": snapshot
    }))
}

fn engineering_project_analyze(args: &Value, state: &SharedState) -> Result<Value, RuntimeError> {
    let scan = engineering_project_scan(args, state)?;
    let snapshot = scan.get("snapshot").cloned().unwrap_or_else(|| json!({}));
    let manifests = snapshot.get("manifests").and_then(Value::as_array).cloned().unwrap_or_default();
    let entrypoints = snapshot.get("entrypoints").and_then(Value::as_array).cloned().unwrap_or_default();
    let tests = snapshot.get("test_files").and_then(Value::as_array).cloned().unwrap_or_default();
    let checks = snapshot.get("checks").and_then(Value::as_array).cloned().unwrap_or_default();
    let signals = snapshot.get("signals").and_then(Value::as_array).cloned().unwrap_or_default();
    let mut stack: Vec<String> = Vec::new();
    for manifest in &manifests {
        let name = manifest.as_str().unwrap_or_default();
        let technology = match name.rsplit('/').next().unwrap_or(name) {
            "Cargo.toml" => "Rust",
            "package.json" => "JavaScript/Node.js",
            "pyproject.toml" | "requirements.txt" => "Python",
            "go.mod" => "Go",
            "pom.xml" | "build.gradle" => "Java",
            "Gemfile" => "Ruby",
            "composer.json" => "PHP",
            _ => "ecossistema não classificado",
        };
        if !stack.iter().any(|item| item == technology) { stack.push(technology.to_string()); }
    }
    let mut next_steps = Vec::new();
    if manifests.is_empty() { next_steps.push("identificar ou criar o manifesto principal do projeto".to_string()); }
    if entrypoints.is_empty() { next_steps.push("localizar o ponto de entrada e documentar o fluxo de execução".to_string()); }
    if tests.is_empty() { next_steps.push("criar uma suíte mínima de testes antes de mudanças estruturais".to_string()); }
    if checks.is_empty() { next_steps.push("configurar uma verificação automatizada reproduzível".to_string()); }
    if !signals.is_empty() { next_steps.push("resolver os sinais arquiteturais antes de ampliar o escopo".to_string()); }
    if next_steps.is_empty() { next_steps.push("executar os checks disponíveis e mapear dependências entre módulos".to_string()); }
    Ok(json!({
        "schema": "engineering-project-analysis/v1",
        "ok": true,
        "status": scan.get("status").cloned().unwrap_or(json!("complete")),
        "evidence": scan.get("evidence").cloned().unwrap_or_else(|| json!([])),
        "project": {
            "workspace": snapshot.get("workspace").cloned().unwrap_or(Value::Null),
            "file_count": snapshot.get("file_count").cloned().unwrap_or(json!(0)),
            "stack": stack,
            "manifests": manifests,
            "entrypoints": entrypoints,
            "test_files": tests,
            "available_checks": checks
        },
        "health": {
            "has_manifest": !snapshot.get("manifests").and_then(Value::as_array).map(|v| v.is_empty()).unwrap_or(true),
            "has_entrypoint": !snapshot.get("entrypoints").and_then(Value::as_array).map(|v| v.is_empty()).unwrap_or(true),
            "has_tests": !snapshot.get("test_files").and_then(Value::as_array).map(|v| v.is_empty()).unwrap_or(true),
            "signals": signals
        },
        "next_steps": next_steps,
        "snapshot": snapshot
    }))
}

fn engineering_project_plan(args: &Value, state: &SharedState) -> Result<Value, RuntimeError> {
    let analysis = engineering_project_analyze(args, state)?;
    let project = analysis.get("project").cloned().unwrap_or_else(|| json!({}));
    let health = analysis.get("health").cloned().unwrap_or_else(|| json!({}));
    let has_tests = health.get("has_tests").and_then(Value::as_bool).unwrap_or(false);
    let has_checks = project.get("available_checks").and_then(Value::as_array).map(|v| !v.is_empty()).unwrap_or(false);
    let mut phases = vec![json!({
        "id": "understand",
        "title": "Entender o sistema",
        "actions": ["confirmar stack, entradas e limites do workspace", "mapear os módulos envolvidos na tarefa"],
        "gate": "snapshot arquitetural disponível"
    })];
    if !has_tests {
        phases.push(json!({
            "id": "safety-net",
            "title": "Criar rede de segurança",
            "actions": ["criar ou localizar um teste reproduzível", "registrar o comportamento esperado antes da alteração"],
            "gate": "há uma verificação que falha antes da mudança e passa depois"
        }));
    }
    phases.push(json!({
        "id": "implement",
        "title": "Implementar em mudança pequena",
        "actions": ["propor arquivos e impacto antes de editar", "alterar somente o escopo autorizado", "preservar backup e diff auditável"],
        "gate": "diff mínimo e explicação do motivo"
    }));
    phases.push(json!({
        "id": "verify",
        "title": "Verificar e diagnosticar",
        "actions": ["executar checks determinísticos", "classificar falhas", "não declarar sucesso sem evidência"],
        "gate": if has_checks { "checks disponíveis executados" } else { "verificação configurada ou limitação declarada" }
    }));
    phases.push(json!({
        "id": "report",
        "title": "Relatar resultado",
        "actions": ["resumir arquivos alterados", "mostrar evidências e lacunas", "sugerir próximo passo reversível"],
        "gate": "resposta com status, evidências e riscos"
    }));
    Ok(json!({
        "schema": "engineering-project-plan/v1",
        "ok": true,
        "status": analysis.get("status").cloned().unwrap_or(json!("complete")),
        "evidence": analysis.get("evidence").cloned().unwrap_or_else(|| json!([])),
        "strategy": "inspect_then_plan_then_change_then_verify",
        "requires_confirmation_before_write": true,
        "phases": phases,
        "source_analysis": analysis
    }))
}

fn engineering_project_verify(args: &Value, state: &SharedState) -> Result<Value, RuntimeError> {
    let guard = state.try_lock().map_err(|_| RuntimeError::Workspace("há uma ferramenta em execução; aguarde sua conclusão".into()))?;
    let workspace = &guard.2;
    let check = args.get("check").and_then(Value::as_str).unwrap_or("list");
    let result = project_checks(args, workspace)?;
    let passed = result.get("passed").and_then(Value::as_bool);
    Ok(json!({
        "schema": "engineering-project-verification/v1",
        "ok": true,
        "status": passed.map(|value| if value { "passed" } else { "failed" }).unwrap_or("available"),
        "check": check,
        "workspace": workspace,
        "result": result,
        "evidence": [{"kind": "local-check", "check": check, "verified": passed.is_some()}],
        "policy": {"arbitrary_shell": false, "workspace_scoped": true}
    }))
}

fn engineering_project_dependencies(args: &Value, state: &SharedState) -> Result<Value, RuntimeError> {
    let guard = state.try_lock().map_err(|_| RuntimeError::Workspace("há uma ferramenta em execução; aguarde sua conclusão".into()))?;
    let root = &guard.2;
    let max_depth = args.get("max_depth").and_then(Value::as_u64).unwrap_or(4).clamp(1, 6) as usize;
    let snapshot = inspect_project(&json!({"max_depth": max_depth}), root)?;
    let paths = snapshot.get("files").and_then(Value::as_array).cloned().unwrap_or_default();
    let extensions = ["rs", "py", "js", "jsx", "ts", "tsx", "go", "java", "cs", "cpp", "c", "h"];
    let mut edges = Vec::new();
    let mut scanned = 0usize;
    for item in paths.iter().take(250) {
        let relative = item.get("path").and_then(Value::as_str).unwrap_or_default();
        let extension = relative.rsplit('.').next().unwrap_or_default();
        if !extensions.contains(&extension) { continue; }
        let path = root.join(relative);
        let content = match fs::read_to_string(&path) { Ok(value) => value, Err(_) => continue };
        scanned += 1;
        for line in content.lines().take(5000) {
            let trimmed = line.trim();
            let candidate = if trimmed.starts_with("use ") || trimmed.starts_with("mod ") || trimmed.starts_with("import ") || trimmed.starts_with("from ") || trimmed.contains("require(") {
                Some(trimmed.to_string())
            } else { None };
            if let Some(reference) = candidate {
                if edges.len() < 1000 { edges.push(json!({"from": relative, "reference": reference})); }
            }
        }
    }
    Ok(json!({
        "schema": "engineering-project-dependencies/v1",
        "ok": true,
        "status": if edges.len() >= 1000 { "partial" } else { "complete" },
        "workspace": root,
        "scanned_files": scanned,
        "edge_count": edges.len(),
        "edges": edges,
        "evidence": [{"kind": "source-scan", "verified": true, "bounded": true}],
        "limitations": ["referências são sintáticas e não substituem resolução completa do compilador", "arquivos gerados e dependências externas não são abertos"]
    }))
}

fn parse_http_tool(body: &str, research_endpoint: bool) -> Result<ToolCall, String> {
    let value: Value = serde_json::from_str(body).map_err(|error| format!("JSON inválido: {error}"))?;
    if research_endpoint {
        let arguments = value.get("arguments").cloned().unwrap_or(value.clone());
        return Ok(ToolCall {
            tool: "research_web".into(),
            arguments,
            request_id: value.get("request_id").and_then(Value::as_str).map(ToOwned::to_owned),
        });
    }
    serde_json::from_value(value).map_err(|error| format!("chamada de ferramenta inválida: {error}"))
}

fn openai_compatible_response(body: &str) -> Result<Value, RuntimeError> {
    let payload: Value = serde_json::from_str(body).map_err(|error| RuntimeError::InvalidArgument(format!("JSON inválido: {error}")))?;
    let raw_messages = payload.get("messages").and_then(Value::as_array)
        .ok_or_else(|| RuntimeError::InvalidArgument("messages deve ser uma lista".into()))?;
    let messages = raw_messages.iter().filter_map(|message| {
        let role = message.get("role")?.as_str()?.to_string();
        let content = match message.get("content")? {
            Value::String(text) => text.clone(),
            Value::Array(parts) => parts.iter().filter_map(|part| part.get("text").and_then(Value::as_str)).collect::<Vec<_>>().join("\n"),
            _ => String::new(),
        };
        Some(ChatMessage { role, content, attachments: Vec::new() })
    }).collect::<Vec<_>>();
    let result = call_model(&ChatRequest { messages, request_id: None })?;
    let text = result.get("text").and_then(Value::as_str).unwrap_or("");
    Ok(json!({
        "id": "ia-local-do-zero",
        "object": "chat.completion",
        "created": SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default().as_secs(),
        "model": payload.get("model").and_then(Value::as_str).unwrap_or("ia-local-do-zero"),
        "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 0, "completion_tokens": text.split_whitespace().count(), "total_tokens": text.split_whitespace().count()},
        "local_backend": result.get("backend").cloned().unwrap_or(Value::Null),
        "intent": result.get("intent").cloned().unwrap_or(Value::Null),
    }))
}

fn handle_web_request(mut request: tiny_http::Request, state: SharedState, activity_log: SharedActivity) {
    let host = request.headers().iter().find(|h| h.field.equiv("Host")).map(|h| h.value.as_str()).unwrap_or("");
    let origin = request.headers().iter().find(|h| h.field.equiv("Origin")).map(|h| h.value.as_str());
    if !allowed_local_request(host, origin) {
        let _ = request.respond(Response::from_string(json!({"ok":false,"error":"origem não autorizada"}).to_string()).with_status_code(StatusCode(403)));
        return;
    }
    if request.method() == &Method::Get && request.url() == "/api/health" {
        let response = Response::from_string(api_health().to_string()).with_header(
            Header::from_bytes("Content-Type", "application/json; charset=utf-8").unwrap(),
        );
        let _ = request.respond(response);
        return;
    }
    if request.method() == &Method::Get && request.url() == "/api/openapi.json" {
        let response = Response::from_string(api_openapi().to_string()).with_header(
            Header::from_bytes("Content-Type", "application/json; charset=utf-8").unwrap(),
        );
        let _ = request.respond(response);
        return;
    }
    let script = match request.url() {
        "/static/app.js" => Some(include_str!("../static/app.js")),
        "/static/chat-core.js" => Some(include_str!("../static/chat-core.js")),
        _ => None,
    };
    if request.method() == &Method::Get {
        if let Some(script) = script {
            let _ = request.respond(Response::from_string(script).with_header(
                Header::from_bytes("Content-Type", "text/javascript; charset=utf-8").unwrap()
            ).with_header(Header::from_bytes("Cache-Control", "no-store").unwrap()));
            return;
        }
    }
    if request.method() == &Method::Get && request.url() == "/" {
        let response = Response::from_string(WEB_UI).with_header(
            Header::from_bytes("Content-Type", "text/html; charset=utf-8").unwrap(),
        );
        let _ = request.respond(response);
        return;
    }

    if request.method() == &Method::Get && request.url() == "/api/choose-directory" {
        let chosen = Command::new("zenity")
            .args(["--file-selection", "--directory", "--title=Escolha o projeto"])
            .output()
            .or_else(|_| Command::new("kdialog").args(["--getexistingdirectory", "."]).output());
        let response = match chosen {
            Ok(output) if output.status.success() => {
                let path = String::from_utf8_lossy(&output.stdout).trim().to_string();
                match PathBuf::from(path).canonicalize() {
                    Ok(canonical) if canonical.is_dir() => json!({"ok": true, "path": canonical}),
                    _ => json!({"ok": false, "cancelled": true}),
                }
            }
            Ok(_) => json!({"ok": false, "cancelled": true}),
            Err(error) => json!({"ok": false, "error": format!("não foi possível abrir o gerenciador de arquivos: {error}")}),
        };
        let _ = request.respond(Response::from_string(response.to_string()).with_header(
            Header::from_bytes("Content-Type", "application/json; charset=utf-8").unwrap(),
        ));
        return;
    }

    if request.method() == &Method::Get && request.url() == "/api/projects" {
        let response = match state.lock() {
            Ok(guard) => match list_projects(&guard.2) {
                Ok(projects) => Response::from_string(projects.to_string()),
                Err(error) => Response::from_string(json!({"ok": false, "error": error.to_string()}).to_string()).with_status_code(StatusCode(500)),
            },
            Err(_) => Response::from_string(json!({"ok": false, "error": "estado do runtime indisponível"}).to_string()).with_status_code(StatusCode(503)),
        };
        let _ = request.respond(response.with_header(Header::from_bytes("Content-Type", "application/json; charset=utf-8").unwrap()).with_header(Header::from_bytes("Cache-Control", "no-store").unwrap()));
        return;
    }

    if request.method() == &Method::Post && request.url() == "/v1/chat/completions" {
        let mut body = String::new();
        let response = match request.as_reader().take(2 * 1024 * 1024 + 1).read_to_string(&mut body) {
            Ok(_) if body.len() > 2 * 1024 * 1024 => Response::from_string(json!({"error":{"message":"requisição acima de 2 MiB","type":"invalid_request_error"}}).to_string()).with_status_code(StatusCode(413)),
            Ok(_) => match openai_compatible_response(&body) {
                Ok(data) => Response::from_string(data.to_string()).with_header(Header::from_bytes("Content-Type", "application/json").unwrap()),
                Err(error) => Response::from_string(json!({"error":{"message":error.to_string(),"type":"invalid_request_error"}}).to_string()).with_status_code(StatusCode(400)),
            },
            Err(error) => Response::from_string(json!({"error":{"message":error.to_string(),"type":"invalid_request_error"}}).to_string()).with_status_code(StatusCode(400)),
        };
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

    if request.method() == &Method::Get && request.url().starts_with("/api/events") {
        let after = query_parameter(request.url(), "after_seq")
            .or_else(|| query_parameter(request.url(), "after"))
            .and_then(|value| value.parse().ok())
            .unwrap_or(0);
        let session = query_parameter(request.url(), "session_id");
        let task = query_parameter(request.url(), "task_id");
        let data = activity_events_v2(&activity_log, after, session.as_deref(), task.as_deref());
        let response = Response::from_string(data.to_string()).with_header(
            Header::from_bytes("Content-Type", "application/json; charset=utf-8").unwrap(),
        ).with_header(Header::from_bytes("Cache-Control", "no-store").unwrap());
        let _ = request.respond(response);
        return;
    }

    if request.url() == "/api/learn" && request.method() == &Method::Post {
        let mut body = String::new();
        let result = request.as_reader().take(4097).read_to_string(&mut body);
        let response = if result.is_ok() && body.len() <= 4096 {
            reqwest::blocking::Client::builder().timeout(Duration::from_secs(5)).build()
                .and_then(|client| client.post("http://127.0.0.1:3101/learn")
                    .header("content-type", "application/json").body(body).send())
                .and_then(|upstream| upstream.text())
                .unwrap_or_else(|error| json!({"ok":false,"error":error.to_string()}).to_string())
        } else {
            json!({"ok":false,"error":"tema acima do limite"}).to_string()
        };
        let _ = request.respond(Response::from_string(response).with_header(Header::from_bytes("Content-Type", "application/json").unwrap()));
        return;
    }
    if request.method() == &Method::Get && request.url().starts_with("/api/learn/") {
        let id = request.url().trim_start_matches("/api/learn/");
        let response = if id.len() == 32 && id.chars().all(|ch| ch.is_ascii_hexdigit()) {
            reqwest::blocking::Client::builder().timeout(Duration::from_secs(5)).build()
                .and_then(|client| client.get(format!("http://127.0.0.1:3101/learn/{id}")).send())
                .and_then(|upstream| upstream.text())
                .unwrap_or_else(|error| json!({"ok":false,"error":error.to_string()}).to_string())
        } else {
            json!({"ok":false,"error":"identificador inválido"}).to_string()
        };
        let _ = request.respond(Response::from_string(response).with_header(Header::from_bytes("Content-Type", "application/json").unwrap()));
        return;
    }
    if request.method() == &Method::Get && request.url() == "/api/skills" {
        let response = reqwest::blocking::Client::builder().timeout(Duration::from_secs(5)).build()
            .and_then(|client| client.get("http://127.0.0.1:3101/skills").send())
            .and_then(|upstream| upstream.text())
            .unwrap_or_else(|error| json!({"ok":false,"error":error.to_string(),"skills":{}}).to_string());
        let _ = request.respond(Response::from_string(response).with_header(Header::from_bytes("Content-Type", "application/json").unwrap()));
        return;
    }
    if request.method() == &Method::Get && request.url() == "/api/v1/agent/capabilities" {
        let response = match reqwest::blocking::Client::builder().timeout(Duration::from_secs(5)).build()
            .and_then(|client| client.get("http://127.0.0.1:3101/v1/agent/capabilities").send()) {
            Ok(upstream) => {
                let status = upstream.status().as_u16();
                let body = upstream.text().unwrap_or_else(|error| json!({"ok":false,"error":error.to_string()}).to_string());
                Response::from_string(body).with_status_code(StatusCode(status))
            }
            Err(error) => Response::from_string(json!({"ok":false,"error":format!("worker local indisponível: {error}")}).to_string())
                .with_status_code(StatusCode(503)),
        };
        let _ = request.respond(response
            .with_header(Header::from_bytes("Content-Type", "application/json; charset=utf-8").unwrap())
            .with_header(Header::from_bytes("Cache-Control", "no-store").unwrap()));
        return;
    }
    if request.method() == &Method::Post && request.url() == "/api/v1/engineering/project/scan" {
        let mut body = String::new();
        let response = match request.as_reader().take(65537).read_to_string(&mut body) {
            Ok(_) if body.len() > 65536 => Response::from_string(json!({"ok":false,"error":"argumentos acima do limite"}).to_string()).with_status_code(StatusCode(413)),
            Ok(_) => {
                let args = if body.trim().is_empty() { json!({}) } else {
                    serde_json::from_str::<Value>(&body).unwrap_or_else(|_| json!({"invalid_json": true}))
                };
                if args.get("invalid_json").is_some() {
                    Response::from_string(json!({"ok":false,"error":"JSON inválido"}).to_string()).with_status_code(StatusCode(400))
                } else if !args.is_object() {
                    Response::from_string(json!({"ok":false,"error":"os argumentos devem ser um objeto JSON"}).to_string()).with_status_code(StatusCode(400))
                } else {
                    match engineering_project_scan(&args, &state) {
                        Ok(result) => Response::from_string(result.to_string()),
                        Err(RuntimeError::Workspace(message)) if message.contains("ferramenta em execução") => Response::from_string(json!({"ok":false,"error":message}).to_string()).with_status_code(StatusCode(409)),
                        Err(error) => Response::from_string(json!({"ok":false,"error":error.to_string()}).to_string()).with_status_code(StatusCode(422)),
                    }
                }
            }
            Err(error) => Response::from_string(json!({"ok":false,"error":error.to_string()}).to_string()).with_status_code(StatusCode(400)),
        };
        let _ = request.respond(response
            .with_header(Header::from_bytes("Content-Type", "application/json; charset=utf-8").unwrap())
            .with_header(Header::from_bytes("Cache-Control", "no-store").unwrap()));
        return;
    }
    if request.method() == &Method::Post && request.url() == "/api/v1/engineering/project/analyze" {
        let mut body = String::new();
        let response = match request.as_reader().take(65537).read_to_string(&mut body) {
            Ok(_) if body.len() > 65536 => Response::from_string(json!({"ok":false,"error":"argumentos acima do limite"}).to_string()).with_status_code(StatusCode(413)),
            Ok(_) => {
                let args = if body.trim().is_empty() { json!({}) } else { serde_json::from_str::<Value>(&body).unwrap_or(Value::Null) };
                if !args.is_object() { Response::from_string(json!({"ok":false,"error":"JSON inválido; esperava-se um objeto"}).to_string()).with_status_code(StatusCode(400))
                } else { match engineering_project_analyze(&args, &state) {
                    Ok(result) => Response::from_string(result.to_string()),
                    Err(RuntimeError::Workspace(message)) if message.contains("ferramenta em execução") => Response::from_string(json!({"ok":false,"error":message}).to_string()).with_status_code(StatusCode(409)),
                    Err(error) => Response::from_string(json!({"ok":false,"error":error.to_string()}).to_string()).with_status_code(StatusCode(422)),
                }}
            }
            Err(error) => Response::from_string(json!({"ok":false,"error":error.to_string()}).to_string()).with_status_code(StatusCode(400)),
        };
        let _ = request.respond(response.with_header(Header::from_bytes("Content-Type", "application/json; charset=utf-8").unwrap()).with_header(Header::from_bytes("Cache-Control", "no-store").unwrap()));
        return;
    }
    if request.method() == &Method::Post && request.url() == "/api/v1/engineering/project/plan" {
        let mut body = String::new();
        let response = match request.as_reader().take(65537).read_to_string(&mut body) {
            Ok(_) if body.len() > 65536 => Response::from_string(json!({"ok":false,"error":"argumentos acima do limite"}).to_string()).with_status_code(StatusCode(413)),
            Ok(_) => {
                let args = if body.trim().is_empty() { json!({}) } else { serde_json::from_str::<Value>(&body).unwrap_or(Value::Null) };
                if !args.is_object() { Response::from_string(json!({"ok":false,"error":"JSON inválido; esperava-se um objeto"}).to_string()).with_status_code(StatusCode(400))
                } else { match engineering_project_plan(&args, &state) {
                    Ok(result) => Response::from_string(result.to_string()),
                    Err(RuntimeError::Workspace(message)) if message.contains("ferramenta em execução") => Response::from_string(json!({"ok":false,"error":message}).to_string()).with_status_code(StatusCode(409)),
                    Err(error) => Response::from_string(json!({"ok":false,"error":error.to_string()}).to_string()).with_status_code(StatusCode(422)),
                }}
            }
            Err(error) => Response::from_string(json!({"ok":false,"error":error.to_string()}).to_string()).with_status_code(StatusCode(400)),
        };
        let _ = request.respond(response.with_header(Header::from_bytes("Content-Type", "application/json; charset=utf-8").unwrap()).with_header(Header::from_bytes("Cache-Control", "no-store").unwrap()));
        return;
    }
    if request.method() == &Method::Post && request.url() == "/api/v1/engineering/project/verify" {
        let mut body = String::new();
        let response = match request.as_reader().take(65537).read_to_string(&mut body) {
            Ok(_) if body.len() > 65536 => Response::from_string(json!({"ok":false,"error":"argumentos acima do limite"}).to_string()).with_status_code(StatusCode(413)),
            Ok(_) => match serde_json::from_str::<Value>(&body) {
                Ok(args) if args.is_object() => match engineering_project_verify(&args, &state) {
                    Ok(result) => Response::from_string(result.to_string()),
                    Err(RuntimeError::Workspace(message)) if message.contains("ferramenta em execução") => Response::from_string(json!({"ok":false,"error":message}).to_string()).with_status_code(StatusCode(409)),
                    Err(error) => Response::from_string(json!({"ok":false,"error":error.to_string()}).to_string()).with_status_code(StatusCode(422)),
                },
                _ => Response::from_string(json!({"ok":false,"error":"JSON inválido; esperava-se um objeto"}).to_string()).with_status_code(StatusCode(400)),
            },
            Err(error) => Response::from_string(json!({"ok":false,"error":error.to_string()}).to_string()).with_status_code(StatusCode(400)),
        };
        let _ = request.respond(response.with_header(Header::from_bytes("Content-Type", "application/json; charset=utf-8").unwrap()).with_header(Header::from_bytes("Cache-Control", "no-store").unwrap()));
        return;
    }
    if request.method() == &Method::Post && request.url() == "/api/v1/engineering/project/dependencies" {
        let mut body = String::new();
        let response = match request.as_reader().take(65537).read_to_string(&mut body) {
            Ok(_) if body.len() > 65536 => Response::from_string(json!({"ok":false,"error":"argumentos acima do limite"}).to_string()).with_status_code(StatusCode(413)),
            Ok(_) => {
                let args = if body.trim().is_empty() { json!({}) } else { serde_json::from_str::<Value>(&body).unwrap_or(Value::Null) };
                if !args.is_object() { Response::from_string(json!({"ok":false,"error":"JSON inválido; esperava-se um objeto"}).to_string()).with_status_code(StatusCode(400))
                } else { match engineering_project_dependencies(&args, &state) {
                    Ok(result) => Response::from_string(result.to_string()),
                    Err(RuntimeError::Workspace(message)) if message.contains("ferramenta em execução") => Response::from_string(json!({"ok":false,"error":message}).to_string()).with_status_code(StatusCode(409)),
                    Err(error) => Response::from_string(json!({"ok":false,"error":error.to_string()}).to_string()).with_status_code(StatusCode(422)),
                }}
            }
            Err(error) => Response::from_string(json!({"ok":false,"error":error.to_string()}).to_string()).with_status_code(StatusCode(400)),
        };
        let _ = request.respond(response.with_header(Header::from_bytes("Content-Type", "application/json; charset=utf-8").unwrap()).with_header(Header::from_bytes("Cache-Control", "no-store").unwrap()));
        return;
    }
    if request.method() == &Method::Post && request.url() == "/api/v1/knowledge/search" {
        let mut body = String::new();
        let response = match request.as_reader().take(65537).read_to_string(&mut body) {
            Ok(_) if body.len() > 65536 => Response::from_string(json!({"ok":false,"error":"consulta acima do limite"}).to_string()).with_status_code(StatusCode(413)),
            Ok(_) => reqwest::blocking::Client::builder().timeout(Duration::from_secs(5)).build()
                .and_then(|client| client.post("http://127.0.0.1:3101/v1/knowledge/search")
                    .header("content-type", "application/json").body(body).send())
                .map(|upstream| {
                    let status = upstream.status().as_u16();
                    let body = upstream.text().unwrap_or_else(|error| json!({"ok":false,"error":error.to_string()}).to_string());
                    Response::from_string(body).with_status_code(StatusCode(status))
                })
                .unwrap_or_else(|error| Response::from_string(json!({"ok":false,"error":format!("worker local indisponível: {error}")}).to_string()).with_status_code(StatusCode(503))),
            Err(error) => Response::from_string(json!({"ok":false,"error":error.to_string()}).to_string()).with_status_code(StatusCode(400)),
        };
        let _ = request.respond(response.with_header(Header::from_bytes("Content-Type", "application/json; charset=utf-8").unwrap()).with_header(Header::from_bytes("Cache-Control", "no-store").unwrap()));
        return;
    }
    if request.method() == &Method::Post && request.url() == "/api/v1/agent/context" {
        let mut body = String::new();
        let response = match request.as_reader().take(2 * 1024 * 1024 + 1).read_to_string(&mut body) {
            Ok(_) if body.len() > 2 * 1024 * 1024 => Response::from_string(json!({"ok":false,"error":"contexto acima do limite"}).to_string()).with_status_code(StatusCode(413)),
            Ok(_) => reqwest::blocking::Client::builder().timeout(Duration::from_secs(5)).build()
                .and_then(|client| client.post("http://127.0.0.1:3101/v1/agent/context")
                    .header("content-type", "application/json").body(body).send())
                .map(|upstream| {
                    let status = upstream.status().as_u16();
                    let body = upstream.text().unwrap_or_else(|error| json!({"ok":false,"error":error.to_string()}).to_string());
                    Response::from_string(body).with_status_code(StatusCode(status))
                })
                .unwrap_or_else(|error| Response::from_string(json!({"ok":false,"error":format!("worker local indisponível: {error}")}).to_string()).with_status_code(StatusCode(503))),
            Err(error) => Response::from_string(json!({"ok":false,"error":error.to_string()}).to_string()).with_status_code(StatusCode(400)),
        };
        let _ = request.respond(response.with_header(Header::from_bytes("Content-Type", "application/json; charset=utf-8").unwrap()).with_header(Header::from_bytes("Cache-Control", "no-store").unwrap()));
        return;
    }
    if request.method() == &Method::Post && request.url() == "/api/v1/response/compose" {
        let mut body = String::new();
        let response = match request.as_reader().take(2 * 1024 * 1024 + 1).read_to_string(&mut body) {
            Ok(_) if body.len() > 2 * 1024 * 1024 => Response::from_string(json!({"ok":false,"error":"resposta acima do limite"}).to_string()).with_status_code(StatusCode(413)),
            Ok(_) => reqwest::blocking::Client::builder().timeout(Duration::from_secs(5)).build()
                .and_then(|client| client.post("http://127.0.0.1:3101/v1/response/compose")
                    .header("content-type", "application/json").body(body).send())
                .map(|upstream| {
                    let status = upstream.status().as_u16();
                    let body = upstream.text().unwrap_or_else(|error| json!({"ok":false,"error":error.to_string()}).to_string());
                    Response::from_string(body).with_status_code(StatusCode(status))
                })
                .unwrap_or_else(|error| Response::from_string(json!({"ok":false,"error":format!("worker local indisponível: {error}")}).to_string()).with_status_code(StatusCode(503))),
            Err(error) => Response::from_string(json!({"ok":false,"error":error.to_string()}).to_string()).with_status_code(StatusCode(400)),
        };
        let _ = request.respond(response.with_header(Header::from_bytes("Content-Type", "application/json; charset=utf-8").unwrap()).with_header(Header::from_bytes("Cache-Control", "no-store").unwrap()));
        return;
    }
    if request.method() == &Method::Delete && request.url().starts_with("/api/skills/") {
        let encoded = request.url().trim_start_matches("/api/skills/");
        let topic = url::form_urlencoded::parse(format!("topic={encoded}").as_bytes())
            .next().map(|(_, value)| value.into_owned()).unwrap_or_default();
        let encoded_topic: String = url::form_urlencoded::byte_serialize(topic.as_bytes()).collect();
        let response = reqwest::blocking::Client::builder().timeout(Duration::from_secs(5)).build()
            .and_then(|client| client.delete(format!("http://127.0.0.1:3101/skills/{encoded_topic}")).send())
            .and_then(|upstream| upstream.text())
            .unwrap_or_else(|error| json!({"ok":false,"error":error.to_string()}).to_string());
        let _ = request.respond(Response::from_string(response).with_header(Header::from_bytes("Content-Type", "application/json").unwrap()));
        return;
    }
    if request.method() == &Method::Delete && request.url() == "/api/skills" {
        let response = reqwest::blocking::Client::builder().timeout(Duration::from_secs(5)).build()
            .and_then(|client| client.delete("http://127.0.0.1:3101/skills").send())
            .and_then(|upstream| upstream.text())
            .unwrap_or_else(|error| json!({"ok":false,"error":error.to_string()}).to_string());
        let _ = request.respond(Response::from_string(response).with_header(Header::from_bytes("Content-Type", "application/json").unwrap()));
        return;
    }

    if request.url() == "/api/runs" || request.url().starts_with("/api/runs/") {
        let mut body = String::new();
        let endpoint = format!("http://127.0.0.1:3101{}", request.url().trim_start_matches("/api"));
        let response = (|| -> Result<_, String> {
            request.as_reader().take(2 * 1024 * 1024 + 1).read_to_string(&mut body).map_err(|e| e.to_string())?;
            if body.len() > 2 * 1024 * 1024 { return Err("Pedido acima de 2 MiB".into()); }
            let client = reqwest::blocking::Client::builder().timeout(Duration::from_secs(10)).build().map_err(|e| e.to_string())?;
            let call = match request.method() {
                Method::Get => client.get(&endpoint),
                Method::Post => client.post(&endpoint).header("content-type", "application/json").body(body),
                _ => return Err("Método inválido".into()),
            };
            let upstream = call.send().map_err(|e| e.to_string())?;
            let status = upstream.status().as_u16();
            Ok(Response::from_string(upstream.text().map_err(|e| e.to_string())?).with_status_code(StatusCode(status)))
        })().unwrap_or_else(|error| Response::from_string(json!({"ok":false,"error":error}).to_string()).with_status_code(StatusCode(503)));
        let _ = request.respond(response.with_header(Header::from_bytes("Content-Type", "application/json").unwrap()).with_header(Header::from_bytes("Cache-Control", "no-store").unwrap()));
        return;
    }
    if request.method() == &Method::Post && request.url() == "/api/chat" {
        let mut body = String::new();
        let response = match request.as_reader().take(4 * 1024 * 1024 + 1).read_to_string(&mut body) {
            Ok(_) if body.len() > 4 * 1024 * 1024 => Response::from_string(json!({"ok":false,"error":"requisição acima de 4 MiB"}).to_string()).with_status_code(StatusCode(413)),
            Ok(_) => match serde_json::from_str::<ChatRequest>(&body) {
                Ok(chat) => {
                    let started = Instant::now();
                    let operation = format!("chat:{}", chat.request_id.clone().unwrap_or_else(|| "local".into()));
                    activity(&activity_log, &operation, "received", "Requisição recebida; preparando o contexto.", false, None);
                    activity(&activity_log, &operation, "processing", "Examinando a pergunta e o contexto recebido.", false, None);
                    activity(&activity_log, &operation, "model", "Consultando memória, acervo e modelo local; aguardando a análise.", false, None);
                    let finished = Arc::new(AtomicBool::new(false));
                    let pulse = Arc::clone(&finished);
                    let pulse_log = Arc::clone(&activity_log);
                    let pulse_operation = operation.clone();
                    let monitor = std::thread::spawn(move || {
                        for second in (3..=900).step_by(3) {
                            std::thread::sleep(Duration::from_secs(3));
                            if pulse.load(Ordering::Relaxed) { break; }
                            activity(&pulse_log, &pulse_operation, "model", &format!("Modelo local em análise há {second} s."), false, Some(second as u128 * 1000));
                        }
                    });
                    let model_result = call_model(&chat);
                    finished.store(true, Ordering::Relaxed);
                    drop(monitor);
                    match model_result {
                        Ok(mut data) => {
                            let elapsed = started.elapsed().as_millis();
                            if let Some(object) = data.as_object_mut() {
                                object.insert("elapsed_ms".into(), json!(elapsed));
                                object.insert("target_ms".into(), json!(CHAT_TARGET_MS));
                                object.insert("max_ms".into(), json!(CHAT_MAX_MS));
                                object.insert("performance".into(), json!(if elapsed <= CHAT_TARGET_MS { "within_target" } else if elapsed <= CHAT_MAX_MS { "within_extended_chat_limit" } else { "timeout" }));
                            }
                            activity(&activity_log, &operation, "done", "Resposta pronta.", true, Some(elapsed));
                            Response::from_string(data.to_string()).with_header(
                                Header::from_bytes("Content-Type", "application/json").unwrap(),
                            )
                        }
                        Err(error) => {
                            let elapsed = started.elapsed().as_millis();
                            activity(&activity_log, &operation, "error", &format!("A requisição falhou: {error}"), true, Some(elapsed));
                            Response::from_string(json!({"ok": false, "error": error.to_string(), "elapsed_ms": elapsed, "target_ms": CHAT_TARGET_MS, "max_ms": CHAT_MAX_MS, "performance": "timeout"}).to_string())
                                .with_status_code(StatusCode(if matches!(error, RuntimeError::InvalidArgument(_) | RuntimeError::MissingArgument(_)) {400} else {503}))
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

    let versioned_tool_endpoint = request.url() == "/api/v1/tools/call";
    let research_endpoint = request.url() == "/api/v1/research";
    if request.method() == &Method::Post && (request.url() == "/api/tool-call" || versioned_tool_endpoint || research_endpoint) {
        let mut body = String::new();
        let response = match request.as_reader().take(2 * 1024 * 1024 + 1).read_to_string(&mut body) {
            Ok(_) if body.len() > 2 * 1024 * 1024 => Response::from_string(json!({"ok":false,"error":"requisição acima de 2 MiB"}).to_string()).with_status_code(StatusCode(413)),
            Ok(_) => match parse_http_tool(&body, research_endpoint) {
                Ok(call) => {
                    let started = Instant::now();
                    let tool = call.tool.clone();
                    let operation = format!("tool:{}:{}", tool, call.request_id.clone().unwrap_or_else(|| "local".into()));
                    activity(&activity_log, &operation, "requested", &format!("Chamada validada para {tool}; preparando a execução."), false, None);
                    activity(&activity_log, &operation, "started", &format!("Executando {tool}."), false, None);
                    let result = match execute_shared(call, &state) {
                        Ok(data) => ToolResult { ok: true, tool, data: Some(data), error: None, elapsed_ms: 0, target_ms: TARGET_REQUEST_MS, max_ms: MAX_REQUEST_MS, performance: "pending".into() },
                        Err(error) => ToolResult { ok: false, tool, data: None, error: Some(error.to_string()), elapsed_ms: 0, target_ms: TARGET_REQUEST_MS, max_ms: MAX_REQUEST_MS, performance: "pending".into() },
                    };
                    let elapsed_ms = started.elapsed().as_millis();
                    let result = ToolResult { elapsed_ms, performance: performance_label(elapsed_ms).into(), ..result };
                    activity(&activity_log, &operation, if result.ok { "done" } else { "error" }, if result.ok { "Ferramenta concluída." } else { result.error.as_deref().unwrap_or("Ferramenta falhou.") }, true, Some(elapsed_ms));
                    Response::from_string(serde_json::to_string(&result).unwrap()).with_header(
                        Header::from_bytes("Content-Type", "application/json").unwrap(),
                    )
                }
                Err(error) => Response::from_string(json!({"ok":false,"error":format!("JSON inválido: {error}")}).to_string())
                    .with_status_code(StatusCode(400)),
            },
            Err(error) => Response::from_string(json!({"ok":false,"error":format!("falha ao ler requisição: {error}")}).to_string())
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
    if python.exists() && model_server.exists() {
        match Command::new(&python)
            .arg(&model_server)
            .arg("--port")
            .arg("3101")
            .current_dir(&workspace)
            .spawn()
        {
            Ok(child) => {
                model_worker = Some(child);
                eprintln!("Assistente local iniciado em http://127.0.0.1:3101 (análise e acervo)");
            }
            Err(error) => eprintln!("Worker local indisponível: {error}"),
        }
    } else {
        eprintln!("Python ou worker local não encontrado; chat ficará indisponível");
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
    fn endpoint_openai_compativel_preserva_resposta_local() {
        let body = r#"{"model":"ia-local-do-zero","messages":[{"role":"user","content":"Olá"}]}"#;
        let response = openai_compatible_response(body).unwrap();
        assert_eq!(response["object"], "chat.completion");
        assert_eq!(response["choices"][0]["message"]["role"], "assistant");
        assert!(!response["choices"][0]["message"]["content"].as_str().unwrap().is_empty());
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
    fn pesquisa_remove_redirecionamento_do_duckduckgo() {
        let redirect = "https://duckduckgo.com/l/?uddg=https%3A%2F%2Fdoc.rust-lang.org%2Fbook%2F&rut=test";
        assert_eq!(unwrap_search_redirect(redirect), "https://doc.rust-lang.org/book/");
        assert_eq!(unwrap_search_redirect("https://example.com/a"), "https://example.com/a");
    }

    #[test]
    fn pesquisa_tecnica_nao_abre_resultado_sobre_verbo_explique() {
        let query = "Rust Axum SQLx PostgreSQL JWT API documentation";
        let dictionary = json!({"title": "Sinônimo de Explique", "snippet": "Definição do verbo explicar", "url": "https://example.org/explique"});
        let docs = json!({"title": "Axum documentation", "snippet": "Rust web framework", "url": "https://docs.rs/axum/latest/axum/"});
        assert_eq!(search_result_relevance(query, &dictionary), 0);
        assert!(search_result_relevance(query, &docs) > 0);
        assert_eq!(research_queries(query), vec![
            "Rust Axum official documentation",
            "Rust SQLx PostgreSQL documentation",
            "Rust JWT jsonwebtoken documentation",
        ]);
    }

    #[test]
    fn pesquisa_aceita_qualquer_tema_tecnico_explicito() {
        let cpp = json!({"title": "C++ reference", "url": "https://example.org/cpp"});
        let csharp = json!({"title": "C# guide", "url": "https://example.org/csharp"});
        let novel = json!({"title": "NovaFlux documentation", "url": "https://example.org/novaflux"});
        assert!(result_matches_topic("C++", &cpp));
        assert!(!result_matches_topic("C++", &csharp));
        assert!(result_matches_topic("NovaFlux", &novel));
        assert!(result_matches_topic("Node.js", &json!({"title": "Node.js API", "url": "https://nodejs.org"})));
        assert!(result_matches_topic("R", &json!({"title": "The R Project", "url": "https://r-project.org"})));
    }

    #[test]
    fn pesquisa_tem_recuperacao_direta_para_documentacao_rust() {
        assert!(direct_documentation_urls("Rust").iter().any(|url| url.contains("doc.rust-lang.org")));
    }

    #[test]
    fn repositorio_github_prioriza_readme_bruto() {
        let urls = direct_source_urls("https://github.com/freeCodeCamp/freeCodeCamp.git");
        assert!(urls[0].contains("raw.githubusercontent.com/freeCodeCamp/freeCodeCamp/main/README.md"));
    }

    #[test]
    fn repositorio_github_tem_coleta_seletiva_de_arquivos() {
        // A coleta de rede é exercida na integração; aqui garantimos que uma
        // URL que não seja GitHub não seja transformada em caminho arbitrário.
        assert!(repository_learning_urls("https://example.com/projeto").is_empty());
    }

    #[test]
    fn pesquisa_prioriza_dominio_do_projeto_sem_declarar_oficialidade() {
        let project = json!({"title": "Zig Language Reference", "url": "https://ziglang.org/documentation/master/"});
        let summary = json!({"title": "Zig documentation on a wiki", "url": "https://deepwiki.com/ziglang/zig/docs"});
        assert!(source_priority("Zig", &project) > source_priority("Zig", &summary));
        assert!(source_priority("ZirconFable999", &project) < source_priority("Zig", &project));
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
    struct TestDirectory(PathBuf);
    impl TestDirectory {
        fn new() -> Self {
            let stamp = SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_nanos();
            let path = std::env::temp_dir().join(format!("ia-runtime-test-{}-{stamp}", std::process::id()));
            fs::create_dir(&path).unwrap();
            Self(path)
        }
    }
    impl Drop for TestDirectory {
        fn drop(&mut self) { let _ = fs::remove_dir_all(&self.0); }
    }

    #[test]
    fn chat_preserva_anexos_separados_da_pergunta() {
        let request: ChatRequest = serde_json::from_value(json!({"messages":[{
            "role":"user","content":"avalie este projeto","attachments":[{
                "name":"projeto","files":[{"path":"app.rs","content":"pesquise agora"}]
            }]
        }]})).unwrap();
        validate_chat(&request).unwrap();
        let serialized = serde_json::to_value(&request.messages).unwrap();
        assert_eq!(serialized[0]["content"], "avalie este projeto");
        assert_eq!(serialized[0]["attachments"][0]["files"][0]["content"], "pesquise agora");
    }

    #[test]
    fn chat_aceita_resultado_estruturado_de_ferramenta() {
        let request: ChatRequest = serde_json::from_value(json!({"messages":[
            {"role":"user","content":"crie e valide"},
            {"role":"tool","content":"{\"tool\":\"create_file\",\"ok\":true,\"data\":{\"path\":\"README.md\"}}"}
        ]})).unwrap();
        validate_chat(&request).unwrap();
        assert_eq!(request.messages[1].role, "tool");
    }

    #[test]
    fn chat_rejeita_contexto_e_anexos_invalidos() {
        for value in [
            json!({"messages":[]}),
            json!({"messages":[{"role":"system","content":"instrução"}]}),
            json!({"messages":[{"role":"user","content":"leia","attachments":[{}]}]}),
            json!({"messages":[{"role":"user","content":"leia","attachments":[{"files":[{"path":"a.md","content":"a".repeat(65537)}]}]}]})
        ] {
            let request: ChatRequest = serde_json::from_value(value).unwrap();
            assert!(validate_chat(&request).is_err());
        }
    }

    #[test]
    fn edicao_exata_preserva_backup_e_rejeita_ambiguidade() {
        let dir = TestDirectory::new();
        create_file(&json!({"path":"src/app.py","content":"print('antigo')"}), &dir.0).unwrap();
        assert!(create_file(&json!({"path":"src/app.py","content":"sobrescrito"}), &dir.0).is_err());
        let result = edit_file(&json!({"path":"src/app.py","old_text":"antigo","new_text":"novo"}), &dir.0).unwrap();
        assert_eq!(fs::read_to_string(dir.0.join("src/app.py")).unwrap(), "print('novo')");
        assert_eq!(fs::read_to_string(dir.0.join(result["backup"].as_str().unwrap())).unwrap(), "print('antigo')");
        assert_eq!(result["artifact"]["kind"], "code");
        assert_eq!(result["artifact"]["status"], "applied");
        assert!(result["diff"]["changed"].as_u64().unwrap_or_default() >= 2);
        fs::write(dir.0.join("repeat.txt"), "aa aa").unwrap();
        assert!(edit_file(&json!({"path":"repeat.txt","old_text":"aa","new_text":"bb"}), &dir.0).is_err());
        assert!(edit_file(&json!({"path":"repeat.txt","old_text":"","new_text":"bb"}), &dir.0).is_err());
        assert_eq!(fs::read_to_string(dir.0.join("repeat.txt")).unwrap(), "aa aa");
    }

    #[test]
    fn lote_multi_arquivo_reverte_tudo_quando_uma_operacao_falha() {
        let dir = TestDirectory::new();
        let result = apply_batch(&json!({"operations":[
            {"tool":"create_file","arguments":{"path":"a.txt","content":"a"}},
            {"tool":"create_file","arguments":{"path":"a.txt","content":"duplicado"}}
        ]}), &dir.0);
        assert!(result.is_err());
        assert!(!dir.0.join("a.txt").exists());
    }

    #[test]
    fn tarefa_persistida_rejeita_troca_de_workspace_antes_de_escrever() {
        let first = TestDirectory::new();
        let second = TestDirectory::new();
        let state: SharedState = Arc::new(Mutex::new((HashMap::new(), 1, second.0.clone())));
        let call = ToolCall {tool: "create_file".into(), request_id: None,
            arguments: json!({"path":"not-created.txt", "content":"x", "_expected_workspace":first.0.to_string_lossy()})};
        assert!(execute_shared(call, &state).is_err());
        assert!(!second.0.join("not-created.txt").exists());
        let call = ToolCall {tool: "create_file".into(), request_id: None,
            arguments: json!({"path":"created.txt", "content":"x", "_expected_workspace":second.0.to_string_lossy()})};
        assert!(execute_shared(call, &state).is_ok());
    }

    #[cfg(unix)]
    #[test]
    fn links_nao_permitem_criar_fora_nem_busca_infinita() {
        use std::os::unix::fs::symlink;
        let dir = TestDirectory::new();
        let outside = TestDirectory::new();
        symlink(outside.0.join("missing.txt"), dir.0.join("linked.txt")).unwrap();
        assert!(create_file(&json!({"path":"linked.txt","content":"escape"}), &dir.0).is_err());
        symlink(&outside.0, dir.0.join("external")).unwrap();
        assert!(create_file(&json!({"path":"external/new.txt","content":"escape"}), &dir.0).is_err());
        symlink(&dir.0, dir.0.join("loop")).unwrap();
        fs::write(dir.0.join("good.txt"), "needle").unwrap();
        fs::write(outside.0.join("hidden.txt"), "needle").unwrap();
        let results = search_files(&json!({"query":"needle"}), &dir.0).unwrap();
        assert_eq!(results["matches"].as_array().unwrap().len(), 1);
        assert_eq!(results["matches"][0]["path"], "good.txt");
        assert!(!outside.0.join("missing.txt").exists());
    }

    #[test]
    fn ferramentas_ocupadas_falham_sem_fila_infinita() {
        let state = Arc::new(Mutex::new((HashMap::new(), 1, PathBuf::from("."))));
        let _guard = state.lock().unwrap();
        let call = ToolCall { tool:"list_files".into(), arguments:json!({}), request_id:None };
        assert!(execute_shared(call, &state).is_err());
    }

    #[test]
    fn verificacoes_sao_listadas_e_comandos_arbitrarios_rejeitados() {
        let dir = TestDirectory::new();
        fs::write(dir.0.join("Cargo.toml"), "[package]\nname='fixture'\nversion='0.1.0'\nedition='2021'\n").unwrap();
        let listed = project_checks(&json!({"check":"list"}), &dir.0).unwrap();
        assert_eq!(listed["available"][0], "cargo-test");
        assert!(project_checks(&json!({"check":"sh -c rm -rf /"}), &dir.0).is_err());
        fs::write(dir.0.join("package.json"), "{\"scripts\":{\"build\":\"echo ok\"}}\n").unwrap();
        let without_test = project_checks(&json!({"check":"list"}), &dir.0).unwrap();
        assert!(!without_test["available"].as_array().unwrap().iter().any(|item| item == "npm-test"));
        fs::write(dir.0.join("package.json"), "{\"scripts\":{\"test\":\"node --test\"}}\n").unwrap();
        let with_test = project_checks(&json!({"check":"list"}), &dir.0).unwrap();
        assert!(with_test["available"].as_array().unwrap().iter().any(|item| item == "npm-test"));
        fs::create_dir(dir.0.join("tests")).unwrap();
        fs::write(dir.0.join("tests/test_ok.py"), "import unittest\n\nclass TestOk(unittest.TestCase):\n    def test_ok(self): self.assertTrue(True)\n").unwrap();
        let python_check = project_checks(&json!({"check":"auto","path":"app.py"}), &dir.0).unwrap();
        assert_eq!(python_check["check"], "unittest");
        assert_eq!(python_check["passed"], true);
    }

    #[test]
    fn inspecao_de_projeto_identifica_manifesto_entrada_e_testes() {
        let dir = TestDirectory::new();
        fs::write(dir.0.join("package.json"), "{\"scripts\":{\"test\":\"node --test\"}}\n").unwrap();
        fs::create_dir(dir.0.join("tests")).unwrap();
        fs::write(dir.0.join("server.js"), "console.log('ok')\n").unwrap();
        fs::write(dir.0.join("tests/app.test.js"), "test('ok', () => {})\n").unwrap();
        let result = inspect_project(&json!({"max_depth": 3}), &dir.0).unwrap();
        assert_eq!(result["manifests"][0], "package.json");
        assert!(result["entrypoints"].as_array().unwrap().iter().any(|item| item == "server.js"));
        assert!(result["test_files"].as_array().unwrap().iter().any(|item| item == "tests/app.test.js"));
    }

    #[test]
    fn inspeccao_api_de_engenharia_devolve_contrato_evidencias_e_sinais() {
        let dir = TestDirectory::new();
        fs::write(dir.0.join("README.md"), "# fixture\n").unwrap();
        let state = Arc::new(Mutex::new((HashMap::new(), 1, dir.0.clone())));
        let result = engineering_project_scan(&json!({"max_depth": 2}), &state).unwrap();
        assert_eq!(result["schema"], "engineering-project-scan/v1");
        assert_eq!(result["ok"], true);
        assert_eq!(result["evidence"][0]["verified"], true);
        assert!(result["snapshot"]["file_count"].as_u64().unwrap() >= 1);
    }

    #[test]
    fn analise_api_de_engenharia_produz_saude_stack_e_proximos_passos() {
        let dir = TestDirectory::new();
        fs::write(dir.0.join("Cargo.toml"), "[package]\nname='fixture'\nversion='0.1.0'\n").unwrap();
        let state = Arc::new(Mutex::new((HashMap::new(), 1, dir.0.clone())));
        let result = engineering_project_analyze(&json!({}), &state).unwrap();
        assert_eq!(result["schema"], "engineering-project-analysis/v1");
        assert_eq!(result["project"]["stack"][0], "Rust");
        assert_eq!(result["health"]["has_manifest"], true);
        assert!(!result["next_steps"].as_array().unwrap().is_empty());
    }

    #[test]
    fn diagnostico_classifica_falha_e_preserva_evidencia() {
        let result = diagnose_project(&json!({
            "check": "pytest",
            "passed": false,
            "stderr": "SyntaxError: invalid syntax at app.py:4"
        })).unwrap();
        assert_eq!(result["category"], "syntax");
        assert!(!result["evidence"].as_array().unwrap().is_empty());
        assert!(!result["next_steps"].as_array().unwrap().is_empty());
    }

    #[test]
    fn reparo_explicito_valida_diff_antes_de_aplicar_e_verifica_backup() {
        let dir = TestDirectory::new();
        create_file(&json!({"path":"src/app.py","content":"print('antigo')\n"}), &dir.0).unwrap();
        let args = json!({"path":"src/app.py","old_text":"antigo","new_text":"novo","reason":"corrigir o valor usado pelo teste"});
        let proposal = propose_repair(&args, &dir.0).unwrap();
        assert_eq!(proposal["status"], "ready");
        assert_eq!(fs::read_to_string(dir.0.join("src/app.py")).unwrap(), "print('antigo')\n");
        let applied = apply_repair(&args, &dir.0).unwrap();
        assert_eq!(applied["updated"], true);
        assert_eq!(applied["repair"]["reason"], "corrigir o valor usado pelo teste");
        assert_eq!(fs::read_to_string(dir.0.join("src/app.py")).unwrap(), "print('novo')\n");
    }

    #[test]
    fn pagina_web_cria_arquivo_e_devolve_preview_isolada() {
        let dir = TestDirectory::new();
        let result = create_web_page(&json!({"prompt":"criar uma página para um produto artesanal", "title":"Produto local"}), &dir.0).unwrap();
        assert_eq!(result["created"], true);
        assert_eq!(result["preview_sandboxed"], true);
        assert!(result["preview_html"].as_str().unwrap().contains("Produto local"));
        assert!(dir.0.join("preview/index.html").is_file());
        let login = create_web_page(&json!({"prompt":"cria uma tela de login com animações legais", "path":"preview/login.html", "title":"Tela de login"}), &dir.0).unwrap();
        assert!(login["preview_html"].as_str().unwrap().contains("Bem-vindo de volta"));
        assert!(login["preview_html"].as_str().unwrap().contains("@keyframes float"));
    }

    #[test]
    fn atividade_preserva_correlacao_e_estado_final() {
        let log = Arc::new(Mutex::new(ActivityLog::default()));
        activity(&log, "chat:test-1", "received", "Recebida", false, None);
        activity(&log, "chat:test-2", "error", "Falhou", true, Some(12));
        let result = activity_after(&log, 1);
        assert_eq!(result["events"].as_array().unwrap().len(), 1);
        assert_eq!(result["events"][0]["operation"], "chat:test-2");
        assert_eq!(result["events"][0]["done"], true);
    }

    #[test]
    fn evento_v2_preserva_sessao_tarefa_e_progresso() {
        let log = Arc::new(Mutex::new(ActivityLog::default()));
        activity(&log, "tool:read_file:sess-42", "started", "Lendo arquivo", false, None);
        let result = activity_events_v2(&log, 0, Some("sess-42"), Some("tool:read_file:sess-42"));
        assert_eq!(result["schema"], "agent-events/v2");
        assert_eq!(result["events"].as_array().unwrap().len(), 1);
        assert_eq!(result["events"][0]["schema"], "agent-event/v2");
        assert_eq!(result["events"][0]["session_id"], "sess-42");
        assert_eq!(result["events"][0]["task_id"], "tool:read_file:sess-42");
        assert_eq!(result["events"][0]["progress"]["known"], true);
    }

    #[test]
    fn origem_externa_nao_pode_operar_workspace_local() {
        assert!(allowed_local_request("127.0.0.1:3000", Some("http://127.0.0.1:3000")));
        assert!(allowed_local_request("localhost:3000", None));
        assert!(!allowed_local_request("127.0.0.1:3000", Some("https://site-externo.example")));
        assert!(!allowed_local_request("site-externo.example", None));
        assert!(!allowed_local_request("localhost:3000", Some("null")));
    }

}
