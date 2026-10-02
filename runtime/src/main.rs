#![recursion_limit = "256"]

use base64::engine::general_purpose::URL_SAFE_NO_PAD;
use base64::Engine;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::collections::{HashMap, HashSet, VecDeque};
use std::fs;
use std::io::{self, BufRead, Read, Seek, SeekFrom, Write};
use std::net::{SocketAddr, TcpStream};
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
const DEFAULT_CHAT_GENERATION_TOKENS: u16 = 2048;
const SEARCH_TIMEOUT: Duration = Duration::from_secs(10);
// Aprendizado profundo pode abrir um repositório inteiro e ainda consultar
// referências externas. A resposta comum continua com limite próprio; esta
// etapa tem um orçamento maior para privilegiar precisão.
const RESEARCH_TOTAL_TIMEOUT: Duration = Duration::from_secs(90);
const RESEARCH_SEARCH_TIMEOUT: Duration = Duration::from_secs(6);
const RESEARCH_PAGE_TIMEOUT: Duration = Duration::from_secs(6);
const MAX_FILE_BYTES: u64 = 128 * 1024;
const PROCESS_OUTPUT_LIMIT: usize = 32 * 1024;
const MAX_ACTIVE_PROJECT_PROCESSES: usize = 3;
const MAX_RETAINED_PROJECT_PROCESSES: usize = 24;

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
    #[serde(default)]
    workspace_selected: bool,
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

#[derive(Default)]
struct BoundedProcessOutput {
    bytes: VecDeque<u8>,
    base_cursor: u64,
    next_cursor: u64,
}

impl BoundedProcessOutput {
    fn append(&mut self, bytes: &[u8]) {
        self.bytes.extend(bytes.iter().copied());
        self.next_cursor = self.next_cursor.saturating_add(bytes.len() as u64);
        while self.bytes.len() > PROCESS_OUTPUT_LIMIT {
            self.bytes.pop_front();
            self.base_cursor = self.base_cursor.saturating_add(1);
        }
    }

    fn read_from(&self, cursor: u64) -> Value {
        let truncated = cursor < self.base_cursor;
        let start = cursor.max(self.base_cursor).min(self.next_cursor);
        let offset = start.saturating_sub(self.base_cursor) as usize;
        let bytes: Vec<u8> = self.bytes.iter().skip(offset).copied().collect();
        json!({
            "text": String::from_utf8_lossy(&bytes),
            "cursor": self.next_cursor,
            "truncated": truncated
        })
    }

    fn snapshot(&self) -> String {
        let bytes: Vec<u8> = self.bytes.iter().copied().collect();
        String::from_utf8_lossy(&bytes).into_owned()
    }
}

struct ManagedProcess {
    child: Child,
    process_id: String,
    profile: String,
    command: String,
    workspace: PathBuf,
    state: String,
    exit_code: Option<i32>,
    started_at: Instant,
    order: u64,
    stdout: Arc<Mutex<BoundedProcessOutput>>,
    stderr: Arc<Mutex<BoundedProcessOutput>>,
}

#[derive(Default)]
struct ProcessSupervisor {
    processes: HashMap<String, ManagedProcess>,
    next_order: u64,
}

type SharedProcesses = Arc<Mutex<ProcessSupervisor>>;

fn capture_process_stream<R: Read + Send + 'static>(
    mut stream: R,
    output: Arc<Mutex<BoundedProcessOutput>>,
    activity_log: Option<SharedActivity>,
    operation: String,
    label: &'static str,
) {
    std::thread::spawn(move || {
        let mut chunk = [0_u8; 4096];
        loop {
            match stream.read(&mut chunk) {
                Ok(0) | Err(_) => break,
                Ok(length) => {
                    if let Ok(mut buffer) = output.lock() {
                        buffer.append(&chunk[..length]);
                    }
                    if let Some(activity_log) = activity_log.as_ref() {
                        let text = String::from_utf8_lossy(&chunk[..length]);
                        let clean: String = text.chars()
                            .filter(|character| !character.is_control() || matches!(character, '\n' | '\t'))
                            .collect();
                        let clean = clean.split_whitespace().collect::<Vec<_>>().join(" ");
                        if !clean.is_empty() {
                            let snippet: String = clean.chars().take(320).collect();
                            activity(activity_log, &operation, "processing", &format!("{label}: {snippet}"), false, None);
                        }
                    }
                }
            }
        }
    });
}

fn resolve_dev_profile(root: &Path, requested: &str) -> Result<(String, String, String, Vec<String>), RuntimeError> {
    if requested != "auto-dev" {
        return Err(RuntimeError::InvalidArgument("perfil de execução não permitido".into()));
    }
    let package_json = root.join("package.json");
    if package_json.is_file() {
        let package: Value = serde_json::from_slice(&fs::read(&package_json)
            .map_err(|error| RuntimeError::Workspace(format!("não foi possível ler package.json: {error}")))?)
            .map_err(|error| RuntimeError::InvalidArgument(format!("package.json inválido: {error}")))?;
        let scripts = package.get("scripts").and_then(Value::as_object);
        for script_name in ["dev", "start"] {
            if let Some(script) = scripts.and_then(|value| value.get(script_name)).and_then(Value::as_str) {
                if !script.trim().is_empty() {
                    let args = if script_name == "dev" {
                        vec!["run".to_string(), script_name.to_string(), "--".to_string(), "--host".to_string(), "127.0.0.1".to_string()]
                    } else {
                        vec!["run".to_string(), script_name.to_string()]
                    };
                    let snippet: String = script.trim().chars().take(240).collect();
                    let description = format!("npm run {script_name} (script declarado: {snippet})");
                    return Ok((format!("npm-{script_name}"), "npm".into(), description, args));
                }
            }
        }
    }
    if root.join("Cargo.toml").is_file() {
        return Ok(("cargo-run".into(), "cargo".into(), "cargo run".into(), vec!["run".into()]));
    }
    Err(RuntimeError::InvalidArgument(
        "não encontrei package.json com script dev/start nem Cargo.toml para o perfil auto-dev".into(),
    ))
}

fn read_cursor(args: &Value, field: &str) -> Result<u64, RuntimeError> {
    match args.get(field) {
        None => Ok(0),
        Some(value) => value.as_u64().ok_or_else(|| RuntimeError::InvalidArgument(format!("{field} deve ser um inteiro não negativo"))),
    }
}

fn signal_process_group(process_id: u32, signal: &str) -> bool {
    #[cfg(unix)]
    {
        let utility = ["/bin/kill", "/usr/bin/kill"].iter().find(|path| Path::new(path).is_file());
        if let Some(utility) = utility {
            let group = format!("-{process_id}");
            return Command::new(utility)
                .args([signal, "--", group.as_str()])
                .status()
                .map(|status| status.success())
                .unwrap_or(false);
        }
    }
    false
}

fn terminate_managed_child(child: &mut Child) -> Option<i32> {
    let pid = child.id();
    let signalled = signal_process_group(pid, "-TERM");
    if !signalled {
        let _ = child.kill();
    }
    let deadline = Instant::now() + Duration::from_secs(1);
    loop {
        match child.try_wait() {
            Ok(Some(status)) => return status.code(),
            Ok(None) if Instant::now() < deadline => std::thread::sleep(Duration::from_millis(40)),
            _ => break,
        }
    }
    if !signal_process_group(pid, "-KILL") {
        let _ = child.kill();
    }
    child.wait().ok().and_then(|status| status.code())
}

impl ProcessSupervisor {
    fn refresh(process: &mut ManagedProcess) {
        if matches!(process.state.as_str(), "running" | "starting") {
            if let Ok(Some(status)) = process.child.try_wait() {
                process.exit_code = status.code();
                process.state = if status.success() { "exited" } else { "failed" }.into();
            }
        }
    }

    fn start(
        &mut self,
        requested: &str,
        root: &Path,
        activity_log: Option<SharedActivity>,
    ) -> Result<Value, RuntimeError> {
        let canonical_root = fs::canonicalize(root).map_err(|error| RuntimeError::Workspace(error.to_string()))?;
        let (profile, program, command_label, arguments) = resolve_dev_profile(&canonical_root, requested)?;
        for process in self.processes.values_mut() { Self::refresh(process); }
        let active = self.processes.values().filter(|process| process.workspace == canonical_root && matches!(process.state.as_str(), "starting" | "running")).count();
        if active >= MAX_ACTIVE_PROJECT_PROCESSES {
            return Err(RuntimeError::InvalidArgument(format!("limite de {MAX_ACTIVE_PROJECT_PROCESSES} processos ativos atingido")));
        }
        if self.processes.values().any(|process| process.workspace == canonical_root && process.profile == profile && matches!(process.state.as_str(), "starting" | "running")) {
            return Err(RuntimeError::InvalidArgument(format!("já existe um processo {profile} ativo neste workspace")));
        }

        self.next_order = self.next_order.saturating_add(1);
        if self.processes.len() >= MAX_RETAINED_PROJECT_PROCESSES {
            let oldest_finished = self.processes.values()
                .filter(|process| !matches!(process.state.as_str(), "starting" | "running"))
                .min_by_key(|process| process.order)
                .map(|process| process.process_id.clone());
            if let Some(id) = oldest_finished { self.processes.remove(&id); }
            if self.processes.len() >= MAX_RETAINED_PROJECT_PROCESSES {
                return Err(RuntimeError::InvalidArgument("limite de processos registrados atingido; pare ou aguarde um processo".into()));
            }
        }

        let stamp = SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default().as_nanos();
        let mut digest = Sha256::new();
        digest.update(std::process::id().to_le_bytes());
        digest.update(stamp.to_le_bytes());
        digest.update(self.next_order.to_le_bytes());
        let hash = digest.finalize();
        let process_id = format!("proc-{}", hash[..10].iter().map(|byte| format!("{byte:02x}")).collect::<String>());
        let stdout = Arc::new(Mutex::new(BoundedProcessOutput::default()));
        let stderr = Arc::new(Mutex::new(BoundedProcessOutput::default()));
        let mut command = Command::new(&program);
        command.args(&arguments)
            .current_dir(&canonical_root)
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        #[cfg(unix)]
        {
            use std::os::unix::process::CommandExt;
            command.process_group(0);
        }
        let mut child = command.spawn().map_err(|error| RuntimeError::Workspace(format!("não foi possível iniciar {command_label}: {error}")))?;
        let pid = child.id();
        if let Some(stream) = child.stdout.take() {
            capture_process_stream(stream, Arc::clone(&stdout), activity_log.clone(), format!("process:{process_id}"), "stdout");
        }
        if let Some(stream) = child.stderr.take() {
            capture_process_stream(stream, Arc::clone(&stderr), activity_log, format!("process:{process_id}"), "stderr");
        }
        let process = ManagedProcess {
            child,
            process_id: process_id.clone(),
            profile: profile.clone(),
            command: command_label.clone(),
            workspace: canonical_root,
            state: "running".into(),
            exit_code: None,
            started_at: Instant::now(),
            order: self.next_order,
            stdout,
            stderr,
        };
        self.processes.insert(process_id.clone(), process);
        Ok(json!({"process_id":process_id,"pid":pid,"profile":profile,"command":command_label,"state":"running","started":true,
            "stdout":{"text":"","cursor":0,"truncated":false},"stderr":{"text":"","cursor":0,"truncated":false}}))
    }

    fn find_process_id(&self, requested: Option<&str>, workspace: &Path) -> Result<String, RuntimeError> {
        if let Some(id) = requested.filter(|value| !value.is_empty() && *value != "latest") {
            let process = self.processes.get(id).ok_or_else(|| RuntimeError::InvalidArgument("processo desconhecido ou já removido".into()))?;
            if process.workspace != workspace {
                return Err(RuntimeError::Workspace("o processo pertence a outro workspace".into()));
            }
            return Ok(id.to_string());
        }
        self.processes.values()
            .filter(|process| process.workspace == workspace)
            .max_by_key(|process| (matches!(process.state.as_str(), "starting" | "running"), process.order))
            .map(|process| process.process_id.clone())
            .ok_or_else(|| RuntimeError::InvalidArgument("não há processo registrado neste workspace".into()))
    }

    fn status(&mut self, args: &Value, root: &Path) -> Result<Value, RuntimeError> {
        let allowed = ["process_id", "stdout_cursor", "stderr_cursor", "wait_ms"];
        if args.as_object().map(|object| object.keys().any(|key| !allowed.contains(&key.as_str()))).unwrap_or(true) {
            return Err(RuntimeError::InvalidArgument("process_status recebeu argumentos inválidos".into()));
        }
        let root = fs::canonicalize(root).map_err(|error| RuntimeError::Workspace(error.to_string()))?;
        let requested_id = args.get("process_id").and_then(Value::as_str);
        if args.get("process_id").is_some() && requested_id.is_none() {
            return Err(RuntimeError::InvalidArgument("process_id deve ser uma string".into()));
        }
        let stdout_cursor = read_cursor(args, "stdout_cursor")?;
        let stderr_cursor = read_cursor(args, "stderr_cursor")?;
        let wait_ms = match args.get("wait_ms") {
            None => 0,
            Some(value) => value.as_u64().filter(|value| *value <= 1500)
                .ok_or_else(|| RuntimeError::InvalidArgument("wait_ms deve ser um inteiro entre 0 e 1500".into()))?,
        };
        let deadline = Instant::now() + Duration::from_millis(wait_ms);
        loop {
            let id = self.find_process_id(requested_id, &root)?;
            let process = self.processes.get_mut(&id).ok_or_else(|| RuntimeError::InvalidArgument("processo desconhecido".into()))?;
            Self::refresh(process);
            let stdout = process.stdout.lock().map_err(|_| RuntimeError::Workspace("buffer stdout indisponível".into()))?;
            let stderr = process.stderr.lock().map_err(|_| RuntimeError::Workspace("buffer stderr indisponível".into()))?;
            let output_changed = stdout.next_cursor > stdout_cursor || stderr.next_cursor > stderr_cursor;
            let stdout_result = stdout.read_from(stdout_cursor);
            let stderr_result = stderr.read_from(stderr_cursor);
            let readiness = discover_local_readiness(&stdout.snapshot(), &stderr.snapshot());
            let state = process.state.clone();
            let response = json!({
                "process_id":process.process_id.clone(),"profile":process.profile.clone(),"command":process.command.clone(),
                "state":state.clone(),"exit_code":process.exit_code,"elapsed_ms":process.started_at.elapsed().as_millis(),
                "readiness":readiness,"stdout":stdout_result,"stderr":stderr_result
            });
            if output_changed || !matches!(state.as_str(), "running" | "starting") || Instant::now() >= deadline {
                return Ok(response);
            }
            drop(stderr);
            drop(stdout);
            std::thread::sleep(Duration::from_millis(80));
        }
    }

    fn stop(&mut self, args: &Value, root: &Path) -> Result<Value, RuntimeError> {
        if args.as_object().map(|object| object.keys().any(|key| key != "process_id")).unwrap_or(true) {
            return Err(RuntimeError::InvalidArgument("process_stop aceita somente process_id".into()));
        }
        if args.get("process_id").is_some() && !args["process_id"].is_string() {
            return Err(RuntimeError::InvalidArgument("process_id deve ser uma string".into()));
        }
        let root = fs::canonicalize(root).map_err(|error| RuntimeError::Workspace(error.to_string()))?;
        let id = self.find_process_id(args.get("process_id").and_then(Value::as_str), &root)?;
        let process = self.processes.get_mut(&id).ok_or_else(|| RuntimeError::InvalidArgument("processo desconhecido".into()))?;
        Self::refresh(process);
        if matches!(process.state.as_str(), "running" | "starting") {
            process.exit_code = terminate_managed_child(&mut process.child);
            process.state = "stopped".into();
        }
        let stopped = process.state == "stopped";
        Ok(json!({"process_id":process.process_id.clone(),"profile":process.profile.clone(),"state":process.state.clone(),"exit_code":process.exit_code,"stopped":stopped}))
    }
}

impl Drop for ProcessSupervisor {
    fn drop(&mut self) {
        for process in self.processes.values_mut() {
            if matches!(process.state.as_str(), "running" | "starting") {
                process.exit_code = terminate_managed_child(&mut process.child);
                process.state = "stopped".into();
            }
        }
    }
}

fn discover_local_readiness(stdout: &str, stderr: &str) -> Value {
    let combined = format!("{stdout}\n{stderr}");
    for candidate in combined.split_whitespace() {
        let candidate = candidate.trim_matches(|character: char| matches!(character, '(' | '[' | '{' | '<' | '"' | '\'' | ',' | ';'))
            .trim_end_matches([')', ']', '}', '>', '"', '\'', ',', ';', '.']);
        let Ok(mut url) = url::Url::parse(candidate) else { continue; };
        let host = url.host_str().unwrap_or_default();
        if url.scheme() != "http" || !matches!(host, "localhost" | "127.0.0.1" | "0.0.0.0") { continue; }
        let Some(port) = url.port() else { continue; };
        let addr = SocketAddr::from(([127, 0, 0, 1], port));
        let ready = TcpStream::connect_timeout(&addr, Duration::from_millis(120)).is_ok();
        let _ = url.set_host(Some("127.0.0.1"));
        return json!({"known":true,"ready":ready,"url":url.as_str(),"evidence":"URL local observada na saída e teste TCP em loopback"});
    }
    json!({"known":false,"ready":false,"url":null,"evidence":"nenhuma URL local verificável apareceu na saída"})
}

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
        "blocked" if done => "blocked",
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

fn has_reserved_workspace_component(relative: &str) -> bool {
    Path::new(relative).components().any(|component| {
        matches!(component, std::path::Component::Normal(value) if matches!(value.to_str(), Some(".git" | ".ia-local-backups")))
    })
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
    let include_hidden = args.get("include_hidden").and_then(Value::as_bool).unwrap_or(false);
    let max_entries = args.get("max_entries").and_then(Value::as_u64).unwrap_or(200).clamp(1, 1000) as usize;
    let directory = workspace_path(root, relative)?;
    if !directory.is_dir() {
        return Err(RuntimeError::Workspace("o caminho não é um diretório".into()));
    }
    let mut entries = Vec::new();
    for entry in fs::read_dir(&directory).map_err(|error| RuntimeError::Workspace(error.to_string()))? {
        let entry = entry.map_err(|error| RuntimeError::Workspace(error.to_string()))?;
        let metadata = fs::symlink_metadata(entry.path()).map_err(|error| RuntimeError::Workspace(error.to_string()))?;
        let name = entry.file_name().to_string_lossy().to_string();
        if workspace_skip_name(&name, include_hidden) { continue; }
        entries.push(json!({"name": name, "kind": workspace_entry_kind(metadata.file_type()), "bytes": metadata.len()}));
    }
    entries.sort_by(|left, right| left["name"].as_str().cmp(&right["name"].as_str()));
    let total_entries = entries.len();
    entries.truncate(max_entries);
    Ok(json!({"workspace": root, "path": relative, "entries": entries,
              "total_entries": total_entries, "truncated": total_entries > max_entries}))
}

fn workspace_entry_kind(file_type: fs::FileType) -> &'static str {
    if file_type.is_symlink() { "symlink" } else if file_type.is_dir() { "directory" } else if file_type.is_file() { "file" } else { "other" }
}

fn workspace_skip_name(name: &str, include_hidden: bool) -> bool {
    !include_hidden && (name.starts_with('.') || matches!(name, "target" | "node_modules" | "dist" | "build" | "venv" | "__pycache__"))
}

fn workspace_skip_descent(name: &str) -> bool {
    matches!(name, ".git" | "target" | "node_modules" | ".venv" | "venv" | "dist" | "build" | "__pycache__")
}

fn sorted_workspace_entries(directory: &Path) -> Result<Vec<fs::DirEntry>, RuntimeError> {
    let mut entries = fs::read_dir(directory).map_err(|error| RuntimeError::Workspace(error.to_string()))?
        .collect::<Result<Vec<_>, _>>().map_err(|error| RuntimeError::Workspace(error.to_string()))?;
    entries.sort_by_key(|entry| entry.file_name());
    Ok(entries)
}

fn path_info(args: &Value, root: &Path) -> Result<Value, RuntimeError> {
    let relative = required_string(args, "path")?;
    let path = if relative.is_empty() { root.to_path_buf() } else if root.join(&relative).exists() {
        workspace_path(root, &relative)?;
        root.join(&relative)
    } else {
        workspace_new_path(root, &relative)?
    };
    let metadata = match fs::symlink_metadata(&path) {
        Ok(metadata) => metadata,
        Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(json!({"path":relative,"exists":false})),
        Err(error) => return Err(RuntimeError::Workspace(error.to_string())),
    };
    Ok(json!({"path":relative,"exists":true,"kind":workspace_entry_kind(metadata.file_type()),
        "bytes":metadata.len(),"readonly":metadata.permissions().readonly()}))
}

fn glob_name_matches(pattern: &str, candidate: &str) -> bool {
    let pattern: Vec<char> = pattern.to_lowercase().chars().collect();
    let candidate: Vec<char> = candidate.to_lowercase().chars().collect();
    let mut previous = vec![false; candidate.len() + 1];
    previous[0] = true;
    for token in pattern {
        let mut current = vec![false; candidate.len() + 1];
        if token == '*' { current[0] = previous[0]; }
        for index in 1..=candidate.len() {
            current[index] = if token == '*' { previous[index] || current[index - 1] }
                else { previous[index - 1] && (token == '?' || token == candidate[index - 1]) };
        }
        previous = current;
    }
    previous[candidate.len()]
}

fn find_paths(args: &Value, root: &Path) -> Result<Value, RuntimeError> {
    let pattern = required_string(args, "pattern")?;
    if pattern.is_empty() || pattern.chars().count() > 120 {
        return Err(RuntimeError::InvalidArgument("pattern deve ter entre 1 e 120 caracteres".into()));
    }
    let relative = args.get("path").and_then(Value::as_str).unwrap_or("");
    let start = workspace_path(root, relative)?;
    if !start.is_dir() { return Err(RuntimeError::InvalidArgument("path deve ser uma pasta".into())); }
    let include_hidden = args.get("include_hidden").and_then(Value::as_bool).unwrap_or(false);
    let max_results = args.get("max_results").and_then(Value::as_u64).unwrap_or(100).clamp(1, 200) as usize;
    let started = Instant::now();
    let mut stack = vec![start];
    let mut matches = Vec::new();
    let mut scanned = 0usize;
    let mut truncated = false;
    'walk: while let Some(directory) = stack.pop() {
        for entry in sorted_workspace_entries(&directory)? {
            scanned += 1;
            if scanned > 20_000 || started.elapsed() > Duration::from_secs(3) { truncated = true; break 'walk; }
            let name = entry.file_name().to_string_lossy().to_string();
            if workspace_skip_name(&name, include_hidden) { continue; }
            let kind = entry.file_type().map_err(|error| RuntimeError::Workspace(error.to_string()))?;
            let path = entry.path();
            let relative_path = path.strip_prefix(root).unwrap_or(&path).to_string_lossy().to_string();
            let selected = if pattern.contains('/') { relative_path.as_str() } else { name.as_str() };
            if glob_name_matches(&pattern, selected) {
                matches.push(json!({"path":relative_path,"kind":workspace_entry_kind(kind)}));
                if matches.len() >= max_results { truncated = true; break 'walk; }
            }
            if kind.is_dir() && !workspace_skip_descent(&name) { stack.push(path); }
        }
    }
    matches.sort_by(|a,b| a["path"].as_str().cmp(&b["path"].as_str()));
    Ok(json!({"pattern":pattern,"path":relative,"matches":matches,"truncated":truncated,"scanned":scanned.min(20_000)}))
}

fn list_tree(args: &Value, root: &Path) -> Result<Value, RuntimeError> {
    let relative = args.get("path").and_then(Value::as_str).unwrap_or("");
    let start = workspace_path(root, relative)?;
    if !start.is_dir() { return Err(RuntimeError::InvalidArgument("path deve ser uma pasta".into())); }
    let max_depth = args.get("max_depth").and_then(Value::as_u64).unwrap_or(3).clamp(1, 6) as usize;
    let max_entries = args.get("max_entries").and_then(Value::as_u64).unwrap_or(200).clamp(1, 500) as usize;
    let include_hidden = args.get("include_hidden").and_then(Value::as_bool).unwrap_or(false);
    let started = Instant::now();
    let mut stack = vec![(start, 1usize)];
    let mut entries = Vec::new();
    let mut scanned = 0usize;
    let mut truncated = false;
    'walk: while let Some((directory, depth)) = stack.pop() {
        let mut children = sorted_workspace_entries(&directory)?;
        children.reverse();
        for entry in children {
            scanned += 1;
            if scanned > 20_000 || started.elapsed() > Duration::from_secs(3) { truncated = true; break 'walk; }
            let name = entry.file_name().to_string_lossy().to_string();
            if workspace_skip_name(&name, include_hidden) { continue; }
            let kind = entry.file_type().map_err(|error| RuntimeError::Workspace(error.to_string()))?;
            let path = entry.path();
            let relative_path = path.strip_prefix(root).unwrap_or(&path).to_string_lossy().to_string();
            entries.push(json!({"path":relative_path,"name":name,"kind":workspace_entry_kind(kind),"depth":depth}));
            if entries.len() >= max_entries { truncated = true; break 'walk; }
            if kind.is_dir() && depth < max_depth && !workspace_skip_descent(&name) { stack.push((path, depth + 1)); }
        }
    }
    entries.sort_by(|a,b| a["path"].as_str().cmp(&b["path"].as_str()));
    Ok(json!({"path":relative,"entries":entries,"truncated":truncated,"scanned":scanned.min(20_000)}))
}

fn compare_files(args: &Value, root: &Path) -> Result<Value, RuntimeError> {
    let left = required_string(args, "left")?;
    let right = required_string(args, "right")?;
    let left_path = workspace_path(root, &left)?;
    let right_path = workspace_path(root, &right)?;
    for path in [&left_path, &right_path] {
        let metadata = fs::metadata(path).map_err(|error| RuntimeError::Workspace(error.to_string()))?;
        if !metadata.is_file() || metadata.len() > MAX_FILE_BYTES {
            return Err(RuntimeError::InvalidArgument("compare_files exige dois arquivos textuais de até 128 KiB".into()));
        }
    }
    let left_text = fs::read_to_string(&left_path).map_err(|error| RuntimeError::Workspace(error.to_string()))?;
    let right_text = fs::read_to_string(&right_path).map_err(|error| RuntimeError::Workspace(error.to_string()))?;
    let identical = left_text == right_text;
    let left_lines: Vec<&str> = left_text.lines().collect();
    let right_lines: Vec<&str> = right_text.lines().collect();
    let first = if identical { None } else {
        let differing = left_lines.iter().zip(&right_lines).position(|(a,b)| a != b);
        Some(differing.map(|index| index + 1).unwrap_or_else(|| {
            if left_lines.len() != right_lines.len() { left_lines.len().min(right_lines.len()) + 1 }
            else { left_lines.len().max(1) }
        }))
    };
    let excerpt = |lines: &[&str]| -> String {
        let Some(line) = first else { return String::new(); };
        let start = line.saturating_sub(2);
        lines.iter().skip(start).take(3).map(|item| item.chars().take(300).collect::<String>()).collect::<Vec<_>>().join("\n")
    };
    Ok(json!({"left":left,"right":right,"identical":identical,"first_difference_line":first,
        "left_excerpt":excerpt(&left_lines),"right_excerpt":excerpt(&right_lines),"truncated":false}))
}

fn read_file(args: &Value, root: &Path) -> Result<Value, RuntimeError> {
    let relative = required_string(args, "path")?;
    let path = workspace_path(root, &relative)?;
    let metadata = fs::metadata(&path).map_err(|error| RuntimeError::Workspace(error.to_string()))?;
    if !metadata.is_file() {
        return Err(RuntimeError::Workspace("o caminho não é um arquivo".into()));
    }
    let extension = path.extension().and_then(|value| value.to_str()).unwrap_or("").to_lowercase();
    if ["pdf", "docx", "xlsx", "pptx", "odt", "ods", "odp", "epub", "doc", "xls", "ppt", "rtf", "ipynb", "eml", "png", "jpg", "jpeg", "gif", "webp", "bmp", "pbm", "pgm", "tif", "tiff"].contains(&extension.as_str()) {
        return Err(RuntimeError::Workspace(format!("o formato .{extension} precisa do extrator especializado; use extract_document_text")));
    }
    let line_range_requested = args.get("start_line").is_some() || args.get("end_line").is_some();
    if line_range_requested {
        if args.get("offset").is_some() {
            return Err(RuntimeError::InvalidArgument("offset não pode ser combinado com start_line/end_line".into()));
        }
        if metadata.len() > MAX_FILE_BYTES {
            return Err(RuntimeError::Workspace(format!("arquivo excede o limite de {} bytes para leitura por linhas; use intervalos por offset", MAX_FILE_BYTES)));
        }
        let source = fs::read_to_string(&path).map_err(|error| RuntimeError::Workspace(error.to_string()))?;
        let lines: Vec<&str> = source.split_inclusive('\n').collect();
        let total_lines = lines.len();
        let start_line = args.get("start_line").and_then(Value::as_u64).unwrap_or(1).max(1) as usize;
        let requested_end = args.get("end_line").and_then(Value::as_u64).unwrap_or(start_line as u64) as usize;
        if total_lines == 0 || start_line > total_lines {
            return Err(RuntimeError::InvalidArgument(format!("start_line deve estar entre 1 e {total_lines}")));
        }
        if requested_end < start_line {
            return Err(RuntimeError::InvalidArgument("end_line precisa ser maior ou igual a start_line".into()));
        }
        let max_bytes = args.get("max_bytes").and_then(Value::as_u64).unwrap_or(8 * 1024).clamp(1, MAX_FILE_BYTES) as usize;
        let effective_end = requested_end.min(total_lines);
        let bounded_end = effective_end.min(start_line.saturating_add(399));
        let mut content = String::new();
        let mut actual_end = start_line - 1;
        let mut partial_line = false;
        for (index, line) in lines.iter().enumerate().take(bounded_end).skip(start_line - 1) {
            if !content.is_empty() && content.len().saturating_add(line.len()) > max_bytes { break; }
            if content.is_empty() && line.len() > max_bytes {
                let boundary = line.char_indices().map(|(index, _)| index)
                    .take_while(|index| *index <= max_bytes).last().unwrap_or(0);
                content.push_str(&line[..boundary]);
                actual_end = index + 1;
                partial_line = true;
                break;
            }
            content.push_str(line);
            actual_end = index + 1;
        }
        let truncated = actual_end < effective_end || partial_line;
        return Ok(json!({
            "path": relative,
            "bytes": metadata.len(),
            "offset": null,
            "content_bytes": content.len(),
            "content": content,
            "start_line": start_line,
            "end_line": actual_end,
            "total_lines": total_lines,
            "truncated": truncated,
            "next_start_line": if partial_line { Some(actual_end) } else if truncated && actual_end > 0 { Some(actual_end + 1) } else { None }
        }));
    }
    let offset = args.get("offset").and_then(Value::as_u64).unwrap_or(0);
    if offset >= metadata.len() && metadata.len() > 0 {
        return Err(RuntimeError::Workspace("offset além do fim do arquivo".into()));
    }
    let requested = args.get("max_bytes").and_then(Value::as_u64).unwrap_or(MAX_FILE_BYTES);
    let limit = requested.clamp(1, MAX_FILE_BYTES);
    let mut file = fs::File::open(&path).map_err(|error| RuntimeError::Workspace(error.to_string()))?;
    file.seek(SeekFrom::Start(offset)).map_err(|error| RuntimeError::Workspace(error.to_string()))?;
    let mut bytes = Vec::new();
    file.take(limit).read_to_end(&mut bytes).map_err(|error| RuntimeError::Workspace(error.to_string()))?;
    let end = offset.saturating_add(bytes.len() as u64);
    let truncated = end < metadata.len();
    let content = String::from_utf8_lossy(&bytes).to_string();
    Ok(json!({
        "path": relative,
        "bytes": metadata.len(),
        "offset": offset,
        "content_bytes": bytes.len(),
        "truncated": truncated,
        "next_offset": if truncated { Some(end) } else { None },
        "content": content
    }))
}

fn validate_writable_path(relative: &str) -> Result<(), RuntimeError> {
    if has_reserved_workspace_component(relative) {
        return Err(RuntimeError::Workspace("pasta interna reservada para metadados e histórico do projeto".into()));
    }
    Ok(())
}

fn create_directory(args: &Value, root: &Path) -> Result<Value, RuntimeError> {
    let relative = required_string(args, "path")?;
    validate_writable_path(&relative)?;
    let path = workspace_new_path(root, &relative)?;
    if path.exists() {
        return Err(RuntimeError::Workspace("o diretório já existe".into()));
    }
    fs::create_dir_all(&path).map_err(|error| RuntimeError::Workspace(error.to_string()))?;
    Ok(json!({"path": relative, "created": true, "kind": "directory"}))
}

fn create_file(args: &Value, root: &Path) -> Result<Value, RuntimeError> {
    let relative = required_string(args, "path")?;
    validate_writable_path(&relative)?;
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
    let write_result = file.write_all(content.as_bytes()).and_then(|()| file.sync_all());
    drop(file);
    if let Err(error) = write_result {
        let _ = fs::remove_file(&path);
        return Err(RuntimeError::Workspace(error.to_string()));
    }
    let diff = line_diff("", &content);
    let artifact = code_artifact(&relative, "created", "", &content, &diff);
    Ok(json!({"path": relative, "created": true, "bytes": content.as_bytes().len(), "diff": diff, "artifact": artifact}))
}

fn contained_entry_path(root: &Path, relative: &str, protect_internal: bool) -> Result<PathBuf, RuntimeError> {
    let parts = Path::new(relative);
    if relative.is_empty() || relative.len() > 1024 || relative.contains('\\') || parts.is_absolute()
        || parts.components().any(|part| !matches!(part, std::path::Component::Normal(_))) {
        return Err(RuntimeError::InvalidArgument("informe um caminho relativo de arquivo ou pasta".into()));
    }
    let mut current = root.to_path_buf();
    for part in parts.components() {
        if protect_internal && matches!(part.as_os_str().to_str(), Some(".git" | ".ia-local-backups")) {
            return Err(RuntimeError::InvalidArgument("pasta interna reservada para histórico e recuperação".into()));
        }
        current.push(part);
        match fs::symlink_metadata(&current) {
            Ok(metadata) if metadata.file_type().is_symlink() => return Err(RuntimeError::Workspace("links simbólicos não são aceitos nesta operação".into())),
            Ok(_) => {},
            Err(error) if error.kind() == io::ErrorKind::NotFound => {},
            Err(error) => return Err(RuntimeError::Workspace(error.to_string())),
        }
    }
    workspace_new_path(root, relative)
}

// These are human explorer actions, separate from the agent's tool catalog.
fn workspace_entry_action(args: &Value, state: &SharedState) -> Result<Value, RuntimeError> {
    let object = args.as_object().ok_or_else(|| RuntimeError::InvalidArgument("pedido deve ser um objeto".into()))?;
    if args.get("schema").and_then(Value::as_str) != Some("workspace-entry/v1") {
        return Err(RuntimeError::InvalidArgument("use o contrato workspace-entry/v1".into()));
    }
    let operation = required_string(args, "operation")?;
    let specific: &[&str] = match operation.as_str() {
        "create_file" => &["path", "content"],
        "edit_file" => &["path", "old_text", "new_text"],
        "create_directory" | "trash" => &["path"],
        "restore" => &["trash_id"],
        "list_trash" => &[],
        _ => return Err(RuntimeError::InvalidArgument("operação de arquivo não reconhecida".into())),
    };
    if object.keys().any(|key| !["schema", "workspace_root", "operation", "request_id"].contains(&key.as_str()) && !specific.contains(&key.as_str())) {
        return Err(RuntimeError::InvalidArgument("campos inesperados na operação de arquivo".into()));
    }
    let expected = required_string(args, "workspace_root")?;
    let state = state.try_lock().map_err(|_| RuntimeError::Workspace("workspace ocupado; tente novamente após a operação atual".into()))?;
    let root = state.2.canonicalize().map_err(|error| RuntimeError::Workspace(error.to_string()))?;
    if fs::canonicalize(&expected).ok().as_ref() != Some(&root) {
        return Err(RuntimeError::Workspace("o projeto ativo mudou; nenhuma alteração foi realizada".into()));
    }
    match operation.as_str() {
        "list_trash" => {
            let directory = contained_entry_path(&root, ".ia-local-backups/trash", false)?;
            let mut entries = Vec::new();
            if directory.is_dir() {
                let mut candidates: Vec<_> = fs::read_dir(directory).map_err(|error| RuntimeError::Workspace(error.to_string()))?
                    .filter_map(Result::ok).collect();
                candidates.sort_by_key(|entry| std::cmp::Reverse(entry.file_name()));
                for entry in candidates {
                    let id = entry.file_name().to_string_lossy().to_string();
                    if id.len() > 80 || !id.bytes().all(|value| value.is_ascii_digit() || value == b'-') { continue; }
                    let record = (|| -> Result<Value, RuntimeError> {
                        let payload = contained_entry_path(&root, &format!(".ia-local-backups/trash/{id}/payload"), false)?;
                        if !payload.exists() { return Err(RuntimeError::Workspace("item já restaurado".into())); }
                        let record_path = contained_entry_path(&root, &format!(".ia-local-backups/trash/{id}/record.json"), false)?;
                        if fs::metadata(&record_path).map_err(|error| RuntimeError::Workspace(error.to_string()))?.len() > 4096 {
                            return Err(RuntimeError::Workspace("registro inválido".into()));
                        }
                        let record: Value = serde_json::from_slice(&fs::read(record_path).map_err(|error| RuntimeError::Workspace(error.to_string()))?)
                            .map_err(|error| RuntimeError::Workspace(error.to_string()))?;
                        if record["schema"] != "workspace-trash/v1" || record["trash_id"] != id { return Err(RuntimeError::Workspace("registro inválido".into())); }
                        contained_entry_path(&root, &required_string(&record, "path")?, true)?;
                        Ok(record)
                    })();
                    if let Ok(record) = record {
                        entries.push(record);
                        if entries.len() == 200 { break; }
                    }
                }
            }
            Ok(json!({"entries":entries}))
        },
        "create_file" | "create_directory" | "edit_file" => {
            let relative = required_string(args, "path")?;
            let target = contained_entry_path(&root, &relative, true)?;
            if operation == "create_file" {
                if target.exists() { return Err(RuntimeError::Workspace("este arquivo já existe; abra-o no editor para alterá-lo".into())); }
                create_file(args, &root)
            }
            else if operation == "create_directory" { create_directory(args, &root) }
            else {
                let path = root.join(&relative);
                let metadata = fs::metadata(&path).map_err(|error| RuntimeError::Workspace(error.to_string()))?;
                if !metadata.is_file() || metadata.len() > MAX_FILE_BYTES { return Err(RuntimeError::Workspace("arquivo inválido ou acima do limite".into())); }
                let content = fs::read_to_string(path).map_err(|error| RuntimeError::Workspace(error.to_string()))?;
                if content != required_string(args, "old_text")? {
                    return Err(RuntimeError::Workspace("o arquivo mudou ou foi aberto parcialmente; abra-o novamente antes de salvar".into()));
                }
                edit_file(args, &root)
            }
        },
        "trash" => {
            let relative = required_string(args, "path")?;
            let source = contained_entry_path(&root, &relative, true)?;
            let metadata = fs::symlink_metadata(&source).map_err(|error| RuntimeError::Workspace(error.to_string()))?;
            if !metadata.is_file() && !metadata.is_dir() { return Err(RuntimeError::InvalidArgument("selecione um arquivo ou uma pasta".into())); }
            let stamp = SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default().as_nanos();
            let id = format!("{stamp}-{}", std::process::id());
            let directory = contained_entry_path(&root, &format!(".ia-local-backups/trash/{id}"), false)?;
            fs::create_dir_all(directory.parent().unwrap()).map_err(|error| RuntimeError::Workspace(error.to_string()))?;
            fs::create_dir(&directory).map_err(|error| RuntimeError::Workspace(error.to_string()))?;
            let kind = if metadata.is_dir() { "directory" } else { "file" };
            let record = json!({"schema":"workspace-trash/v1", "path":relative, "kind":kind, "trash_id":id});
            if let Err(error) = create_file_bytes(&directory.join("record.json"), &serde_json::to_vec(&record).unwrap()) {
                let _ = fs::remove_dir(&directory);
                return Err(RuntimeError::Workspace(error.to_string()));
            }
            if let Err(error) = fs::rename(&source, directory.join("payload")) {
                let _ = fs::remove_file(directory.join("record.json"));
                let _ = fs::remove_dir(&directory);
                return Err(RuntimeError::Workspace(format!("não foi possível mover para a lixeira: {error}")));
            }
            Ok(json!({"path":relative, "trashed":true, "kind":kind, "trash_id":id}))
        },
        "restore" => {
            let id = required_string(args, "trash_id")?;
            if id.is_empty() || id.len() > 80 || !id.bytes().all(|value| value.is_ascii_digit() || value == b'-') {
                return Err(RuntimeError::InvalidArgument("identificador da lixeira inválido".into()));
            }
            let record_path = contained_entry_path(&root, &format!(".ia-local-backups/trash/{id}/record.json"), false)?;
            if fs::metadata(&record_path).map_err(|error| RuntimeError::Workspace(error.to_string()))?.len() > 4096 {
                return Err(RuntimeError::Workspace("registro da lixeira inválido".into()));
            }
            let record: Value = serde_json::from_slice(&fs::read(record_path).map_err(|error| RuntimeError::Workspace(error.to_string()))?)
                .map_err(|error| RuntimeError::Workspace(error.to_string()))?;
            if record["schema"] != "workspace-trash/v1" || record["trash_id"] != id {
                return Err(RuntimeError::Workspace("registro da lixeira inconsistente".into()));
            }
            let relative = required_string(&record, "path")?;
            let target = contained_entry_path(&root, &relative, true)?;
            if target.exists() { return Err(RuntimeError::Workspace("já existe um item neste caminho; restauração cancelada para preservar seu conteúdo".into())); }
            let source = contained_entry_path(&root, &format!(".ia-local-backups/trash/{id}/payload"), false)?;
            if !source.exists() { return Err(RuntimeError::Workspace("este item já foi restaurado ou não está mais na lixeira".into())); }
            fs::create_dir_all(target.parent().unwrap()).map_err(|error| RuntimeError::Workspace(error.to_string()))?;
            fs::rename(source, target).map_err(|error| RuntimeError::Workspace(format!("não foi possível restaurar: {error}")))?;
            Ok(json!({"path":relative, "restored":true, "trash_id":id}))
        },
        _ => unreachable!(),
    }
}

fn html_escape(value: &str) -> String {
    value.replace('&', "&amp;").replace('<', "&lt;").replace('>', "&gt;").replace('"', "&quot;").replace('\'', "&#039;")
}

fn web_page_palette(prompt: &str) -> (&'static str, &'static str) {
    let text = prompt.to_lowercase();
    if ["verde", "orgânico", "organico", "natureza", "botânico", "botanico"].iter().any(|word| text.contains(*word)) {
        ("#73bd86", "#d2b16c")
    } else if ["terracota", "acolhedor", "artesanal", "terroso", "quente"].iter().any(|word| text.contains(*word)) {
        ("#d57958", "#e9b65f")
    } else if ["rosa", "lúdico", "ludico", "divertido", "vibrante", "colorido"].iter().any(|word| text.contains(*word)) {
        ("#e767a2", "#55c8c5")
    } else if ["minimalista", "editorial", "neutro", "monocromático", "monocromatico"].iter().any(|word| text.contains(*word)) {
        ("#d5b76d", "#a7b0ba")
    } else {
        ("#7c3aed", "#06b6d4")
    }
}

fn login_web_page(title: &str, prompt: &str, accent: &str, accent2: &str) -> String {
    r##"<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>__TITLE__</title>
  <style>
    :root{color-scheme:dark;font-family:Inter,ui-sans-serif,system-ui,sans-serif}*{box-sizing:border-box}body{margin:0;min-height:100vh;display:grid;place-items:center;overflow:hidden;color:#f8fafc;background:#070b17}.scene{position:fixed;inset:0;background:radial-gradient(circle at 15% 20%,__ACCENT__55,transparent 30%),radial-gradient(circle at 85% 80%,__ACCENT2__55,transparent 32%);animation:shift 12s ease-in-out infinite alternate}.scene:before,.scene:after{content:"";position:absolute;width:28rem;height:28rem;border:1px solid __ACCENT__55;border-radius:42% 58% 63% 37%;filter:blur(.2px);animation:float 14s ease-in-out infinite}.scene:before{left:-9rem;top:-8rem}.scene:after{right:-10rem;bottom:-12rem;border-color:__ACCENT2__55;animation-delay:-5s}.card{position:relative;width:min(430px,calc(100% - 32px));padding:34px;border:1px solid #334155aa;border-radius:26px;background:#0f172add;box-shadow:0 30px 100px #0009;backdrop-filter:blur(18px);animation:enter .7s ease-out}.mark{width:48px;height:48px;display:grid;place-items:center;margin-bottom:22px;border-radius:16px;background:linear-gradient(135deg,__ACCENT__,__ACCENT2__);box-shadow:0 0 35px __ACCENT__55;font-size:23px}.eyebrow{margin:0 0 8px;color:#a5b4fc;font-size:12px;text-transform:uppercase;letter-spacing:.16em}.card h1{margin:0;font-size:31px;letter-spacing:-.04em}.description{margin:12px 0 24px;color:#94a3b8;line-height:1.55;font-size:14px}.field{display:grid;gap:8px;margin-top:15px}.field label{color:#cbd5e1;font-size:13px}.field input{width:100%;padding:13px 14px;border:1px solid #334155;border-radius:12px;outline:0;background:#0b1220;color:#fff;font:inherit}.field input:focus{border-color:__ACCENT__;box-shadow:0 0 0 3px __ACCENT__33}.row{display:flex;justify-content:space-between;align-items:center;margin:14px 0 22px;color:#94a3b8;font-size:12px}.row a{color:#c4b5fd}.submit{width:100%;padding:14px;border:0;border-radius:12px;color:#fff;background:linear-gradient(100deg,__ACCENT__,__ACCENT2__);font:inherit;font-weight:700;cursor:pointer;transition:transform .2s,box-shadow .2s}.submit:hover{transform:translateY(-2px);box-shadow:0 12px 25px __ACCENT__66}.status{min-height:20px;margin-top:14px;color:#86efac;text-align:center;font-size:12px}@keyframes float{50%{transform:translate(40px,26px) rotate(30deg)}}@keyframes shift{to{filter:hue-rotate(25deg) saturate(1.2)}}@keyframes enter{from{opacity:0;transform:translateY(18px) scale(.98)}to{opacity:1;transform:none}}
  </style>
</head>
<body><div class="scene"></div><main class="card"><div class="mark">✦</div><p class="eyebrow">__TITLE__</p><h1>Bem-vindo de volta</h1><p class="description">__PROMPT__</p><form onsubmit="event.preventDefault();document.querySelector('.status').textContent='Login demonstrativo enviado.'"><div class="field"><label for="email">E-mail</label><input id="email" type="email" placeholder="voce@exemplo.com" required></div><div class="field"><label for="password">Senha</label><input id="password" type="password" placeholder="••••••••" required></div><div class="row"><label><input type="checkbox"> Lembrar acesso</label><a href="#" onclick="event.preventDefault()">Esqueci a senha</a></div><button class="submit">Entrar</button><div class="status" aria-live="polite"></div></form></main></body>
</html>"##.replace("__TITLE__", title).replace("__PROMPT__", prompt).replace("__ACCENT__", accent).replace("__ACCENT2__", accent2)
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
    let (accent, accent2) = web_page_palette(&prompt);
    let is_login = prompt.to_lowercase().contains("login") || prompt.to_lowercase().contains("entrar");
    let html = if is_login { login_web_page(&safe_title, &safe_prompt, accent, accent2) } else { format!(r##"<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{safe_title}</title>
  <style>
    :root {{ color-scheme: dark; font-family: Inter, ui-sans-serif, system-ui, sans-serif; --bg:#08111f; --panel:#101d32; --line:#263b5c; --text:#eff6ff; --muted:#a7b8d1; --accent:{accent}; --accent2:{accent2}; }}
    * {{ box-sizing:border-box; }} body {{ margin:0; min-height:100vh; color:var(--text); background:radial-gradient(circle at 15% 0%,#233b68 0,transparent 38%),linear-gradient(135deg,var(--bg),#111827); }}
    .wrap {{ width:min(1120px,calc(100% - 40px)); margin:auto; }} nav {{ display:flex; justify-content:space-between; align-items:center; padding:24px 0; }}
    .brand {{ font-weight:800; letter-spacing:.03em; }} .pill {{ padding:8px 12px; border:1px solid #49638d; border-radius:999px; color:#c7d2fe; font-size:13px; }}
    .hero {{ display:grid; grid-template-columns:1.15fr .85fr; gap:40px; align-items:center; padding:72px 0 86px; }} h1 {{ max-width:700px; margin:0; font-size:clamp(42px,7vw,78px); line-height:.98; letter-spacing:-.06em; }}
    .gradient {{ background:linear-gradient(90deg,var(--accent),var(--accent2)); color:transparent; background-clip:text; }} .lead {{ max-width:610px; margin:24px 0 0; color:var(--muted); font-size:18px; line-height:1.65; }}
    .actions {{ display:flex; gap:12px; margin-top:30px; flex-wrap:wrap; }} button {{ border:0; border-radius:12px; padding:13px 18px; color:white; background:linear-gradient(100deg,var(--accent),var(--accent2)); font:inherit; cursor:pointer; }} button.secondary {{ border:1px solid var(--line); background:#13223a; }}
    .orb {{ min-height:300px; display:grid; place-items:center; border:1px solid var(--line); border-radius:32px; background:linear-gradient(145deg,#182d4caa,#0b1424dd); box-shadow:0 30px 80px #0006; }} .orb::before {{ content:""; width:150px; height:150px; border-radius:50%; background:radial-gradient(circle at 35% 30%,#fff,var(--accent2) 16%,var(--accent) 42%,transparent 70%); filter:blur(1px); box-shadow:0 0 80px color-mix(in srgb,var(--accent2) 65%,transparent); }}
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
    validate_writable_path(&relative)?;
    let old_text = required_string(args, "old_text")?;
    let new_text = required_string(args, "new_text")?;
    let path = workspace_path(root, &relative)?;
    let metadata = fs::metadata(&path).map_err(|error| RuntimeError::Workspace(error.to_string()))?;
    if !metadata.is_file() || metadata.len() > MAX_FILE_BYTES {
        return Err(RuntimeError::Workspace("arquivo inválido ou acima do limite".into()));
    }
    let content = fs::read_to_string(&path).map_err(|error| RuntimeError::Workspace(error.to_string()))?;
    if old_text.is_empty() && !content.is_empty() {
        return Err(RuntimeError::InvalidArgument("trecho vazio só pode preencher um arquivo vazio".into()));
    }
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
        "backup_hash": text_hash(&content),
        "diff": diff,
        "artifact": artifact
    }))
}

fn batch_created_directories(root: &Path, target: &Path, include_target: bool) -> Result<Vec<PathBuf>, RuntimeError> {
    let mut cursor = if include_target { target.to_path_buf() } else {
        target.parent().ok_or_else(|| RuntimeError::Workspace("diretório pai inválido".into()))?.to_path_buf()
    };
    if !cursor.starts_with(root) {
        return Err(RuntimeError::OutsideWorkspace(target.display().to_string()));
    }
    let mut missing = Vec::new();
    while cursor != root && !cursor.exists() {
        missing.push(cursor.clone());
        cursor = cursor.parent().ok_or_else(|| RuntimeError::Workspace("diretório pai inválido".into()))?.to_path_buf();
    }
    missing.reverse();
    Ok(missing)
}

fn atomic_replace_bytes(path: &Path, content: &[u8]) -> io::Result<()> {
    let metadata = fs::metadata(path)?;
    let stamp = SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default().as_nanos();
    let temporary = path.with_file_name(format!(".ia-atomic-{stamp}"));
    let mut file = fs::OpenOptions::new().write(true).create_new(true).open(&temporary)?;
    let write = file.set_permissions(metadata.permissions()).and_then(|()| file.write_all(content)).and_then(|()| file.sync_all());
    drop(file);
    if let Err(error) = write { let _ = fs::remove_file(&temporary); return Err(error); }
    if let Err(error) = fs::rename(&temporary, path) { let _ = fs::remove_file(&temporary); return Err(error); }
    Ok(())
}

fn create_file_bytes(path: &Path, content: &[u8]) -> io::Result<()> {
    let mut file = fs::OpenOptions::new().write(true).create_new(true).open(path)?;
    let result = file.write_all(content).and_then(|()| file.sync_all());
    drop(file);
    if let Err(error) = result { let _ = fs::remove_file(path); return Err(error); }
    Ok(())
}

fn write_json_atomic(path: &Path, value: &Value) -> io::Result<()> {
    let bytes = serde_json::to_vec_pretty(value).map_err(io::Error::other)?;
    if path.exists() { atomic_replace_bytes(path, &bytes) } else { create_file_bytes(path, &bytes) }
}

fn batch_manifest_path(root: &Path, transaction_id: &str) -> Result<PathBuf, RuntimeError> {
    if !transaction_id.starts_with("batch-") || transaction_id.len() != 30
        || !transaction_id[6..].bytes().all(|byte| byte.is_ascii_hexdigit()) {
        return Err(RuntimeError::InvalidArgument("transaction_id inválido".into()));
    }
    workspace_path(root, &format!(".ia-local-backups/batches/{transaction_id}.json"))
}

fn latest_batch_manifest(root: &Path) -> Result<(String, PathBuf), RuntimeError> {
    let directory = workspace_path(root, ".ia-local-backups/batches")?;
    let mut candidates = Vec::new();
    for entry in fs::read_dir(directory).map_err(|error| RuntimeError::Workspace(error.to_string()))? {
        let entry = entry.map_err(|error| RuntimeError::Workspace(error.to_string()))?;
        if entry.path().extension().and_then(|value| value.to_str()) != Some("json") { continue; }
        if entry.metadata().map(|metadata| metadata.len() > 1024 * 1024).unwrap_or(true) { continue; }
        let manifest: Value = match fs::read(entry.path()).ok().and_then(|bytes| serde_json::from_slice(&bytes).ok()) {
            Some(value) => value,
            None => continue,
        };
        if manifest.get("status").and_then(Value::as_str) != Some("applied") { continue; }
        let id = manifest.get("transaction_id").and_then(Value::as_str).unwrap_or_default();
        if !id.starts_with("batch-") || id.len() != 30 { continue; }
        let order = manifest.get("created_at_ns").and_then(Value::as_str).and_then(|value| value.parse::<u128>().ok()).unwrap_or_default();
        candidates.push((order, id.to_string(), entry.path()));
    }
    candidates.into_iter().max_by_key(|item| item.0)
        .map(|(_, id, path)| (id, path))
        .ok_or_else(|| RuntimeError::InvalidArgument("não há lote aplicado disponível para desfazer".into()))
}

fn verify_batch_created_directories(root: &Path, manifest: &Value) -> Result<(), RuntimeError> {
    let directories = manifest.get("created_directories").and_then(Value::as_array).cloned().unwrap_or_default();
    let operations = manifest.get("operations").and_then(Value::as_array).cloned().unwrap_or_default();
    let mut allowed_directories = HashSet::new();
    for value in &directories {
        let relative = value.as_str().ok_or_else(|| RuntimeError::Workspace("manifesto de undo contém uma pasta inválida".into()))?;
        let path = workspace_path(root, relative)?;
        if !path.is_dir() { return Err(RuntimeError::Workspace(format!("a pasta {relative} mudou desde a aplicação; undo cancelado"))); }
        allowed_directories.insert(path);
    }
    let mut allowed_files = HashSet::new();
    for operation in &operations {
        if operation.get("tool").and_then(Value::as_str) == Some("create_file") {
            let relative = operation.get("path").and_then(Value::as_str).ok_or_else(|| RuntimeError::Workspace("manifesto de undo contém um arquivo inválido".into()))?;
            allowed_files.insert(workspace_path(root, relative)?);
        }
    }
    for base in allowed_directories.iter() {
        let mut pending = vec![base.clone()];
        while let Some(directory) = pending.pop() {
            for entry in fs::read_dir(&directory).map_err(|error| RuntimeError::Workspace(error.to_string()))? {
                let entry = entry.map_err(|error| RuntimeError::Workspace(error.to_string()))?;
                let path = entry.path();
                let file_type = entry.file_type().map_err(|error| RuntimeError::Workspace(error.to_string()))?;
                if file_type.is_symlink() {
                    return Err(RuntimeError::Workspace(format!("a pasta {} recebeu um link posterior; undo cancelado", directory.display())));
                }
                if file_type.is_dir() && allowed_directories.contains(&path) {
                    pending.push(path);
                } else if !(file_type.is_file() && allowed_files.contains(&path)) {
                    return Err(RuntimeError::Workspace(format!("a pasta {} contém conteúdo posterior; undo cancelado para preservar alterações", directory.display())));
                }
            }
        }
    }
    Ok(())
}

enum UndoCompensation {
    RecreateFile { path: PathBuf, content: Vec<u8> },
    ReapplyEdit { path: PathBuf, after_content: Vec<u8>, expected_restored_hash: String },
}

fn compensate_undo(changes: &[UndoCompensation]) -> Vec<String> {
    let mut errors = Vec::new();
    for change in changes.iter().rev() {
        match change {
            UndoCompensation::RecreateFile {path, content} => {
                if let Err(error) = create_file_bytes(path, content) { errors.push(format!("recriar {}: {error}", path.display())); }
            }
            UndoCompensation::ReapplyEdit {path, after_content, expected_restored_hash} => {
                let current_hash = fs::read(path).ok().map(|bytes| format!("sha256:{:x}", Sha256::digest(bytes)));
                if current_hash.as_deref() != Some(expected_restored_hash.as_str()) {
                    errors.push(format!("{} mudou durante a reversão e não foi sobrescrito", path.display()));
                } else if let Err(error) = atomic_replace_bytes(path, after_content) {
                    errors.push(format!("restaurar estado aplicado de {}: {error}", path.display()));
                }
            }
        }
    }
    errors
}

fn undo_batch(args: &Value, root: &Path) -> Result<Value, RuntimeError> {
    let object = args.as_object().ok_or_else(|| RuntimeError::InvalidArgument("undo_batch exige um objeto".into()))?;
    if object.keys().any(|key| key != "transaction_id") {
        return Err(RuntimeError::InvalidArgument("undo_batch aceita somente transaction_id opcional".into()));
    }
    let requested_id = args.get("transaction_id").and_then(Value::as_str);
    if args.get("transaction_id").is_some() && requested_id.is_none() {
        return Err(RuntimeError::InvalidArgument("transaction_id deve ser string".into()));
    }
    let root = root.canonicalize().map_err(|error| RuntimeError::Workspace(error.to_string()))?;
    let (transaction_id, manifest_path) = if let Some(id) = requested_id {
        (id.to_string(), batch_manifest_path(&root, id)?)
    } else {
        latest_batch_manifest(&root)?
    };
    if fs::metadata(&manifest_path).map_err(|error| RuntimeError::Workspace(error.to_string()))?.len() > 1024 * 1024 {
        return Err(RuntimeError::Workspace("manifesto de recuperação acima do limite".into()));
    }
    let mut manifest: Value = serde_json::from_slice(&fs::read(&manifest_path).map_err(|error| RuntimeError::Workspace(format!("não foi possível ler a recuperação do lote: {error}")))?)
        .map_err(|error| RuntimeError::Workspace(format!("manifesto de recuperação inválido: {error}")))?;
    if manifest.get("schema").and_then(Value::as_str) != Some("engineering-batch-undo/v1") {
        return Err(RuntimeError::Workspace("schema de recuperação desconhecido".into()));
    }
    if manifest.get("transaction_id").and_then(Value::as_str) != Some(transaction_id.as_str()) {
        return Err(RuntimeError::Workspace("o identificador não corresponde ao manifesto".into()));
    }
    match manifest.get("status").and_then(Value::as_str) {
        Some("undone") => return Ok(json!({"transaction_id":transaction_id,"status":"undone","already_undone":true,"count":0})),
        Some("applied") => {},
        Some(status) => return Err(RuntimeError::Workspace(format!("o lote está no estado {status}; nenhuma reversão iniciada"))),
        None => return Err(RuntimeError::Workspace("o manifesto não tem estado".into())),
    }
    let operations = manifest.get("operations").and_then(Value::as_array).cloned()
        .ok_or_else(|| RuntimeError::Workspace("manifesto sem operações".into()))?;
    if operations.is_empty() || operations.len() > 32 {
        return Err(RuntimeError::Workspace("manifesto excede o limite de operações".into()));
    }
    for operation in &operations {
        let tool = operation.get("tool").and_then(Value::as_str).unwrap_or_default();
        if tool == "create_directory" { continue; }
        let relative = operation.get("path").and_then(Value::as_str).ok_or_else(|| RuntimeError::Workspace("manifesto sem caminho".into()))?;
        let expected_hash = operation.get("expected_hash").and_then(Value::as_str).ok_or_else(|| RuntimeError::Workspace("manifesto sem hash final".into()))?;
        let path = workspace_path(&root, relative)?;
        let actual_hash = format!("sha256:{:x}", Sha256::digest(fs::read(&path).map_err(|error| RuntimeError::Workspace(error.to_string()))?));
        if actual_hash != expected_hash {
            return Err(RuntimeError::Workspace(format!("{relative} mudou depois da aplicação; undo cancelado para preservar alterações posteriores")));
        }
        if tool == "edit_file" {
            let backup = operation.get("backup").and_then(Value::as_str).ok_or_else(|| RuntimeError::Workspace("manifesto sem backup".into()))?;
            let backup_path_value = Path::new(backup);
            if backup_path_value.parent() != Some(Path::new(".ia-local-backups"))
                || !backup_path_value.file_name().and_then(|name| name.to_str()).map(|name| name.split_once("__").is_some_and(|(stamp, _)| !stamp.is_empty() && stamp.bytes().all(|byte| byte.is_ascii_digit()))).unwrap_or(false) {
                return Err(RuntimeError::Workspace("caminho de backup fora do formato interno".into()));
            }
            let backup_path = workspace_path(&root, backup)?;
            if fs::metadata(backup_path).map_err(|error| RuntimeError::Workspace(error.to_string()))?.len() > MAX_FILE_BYTES {
                return Err(RuntimeError::Workspace("backup excede o limite de arquivo".into()));
            }
            let expected_before = operation.get("before_hash").and_then(Value::as_str).ok_or_else(|| RuntimeError::Workspace("manifesto sem hash da versão anterior".into()))?;
            let backup_bytes = fs::read(workspace_path(&root, backup)?).map_err(|error| RuntimeError::Workspace(error.to_string()))?;
            let actual_before = format!("sha256:{:x}", Sha256::digest(&backup_bytes));
            if actual_before != expected_before { return Err(RuntimeError::Workspace(format!("backup de {relative} foi alterado; undo cancelado"))); }
        } else if tool != "create_file" {
            return Err(RuntimeError::Workspace(format!("operação de undo desconhecida: {tool}")));
        }
    }
    verify_batch_created_directories(&root, &manifest)?;

    manifest["status"] = Value::String("undoing".into());
    write_json_atomic(&manifest_path, &manifest).map_err(|error| RuntimeError::Workspace(format!("não foi possível registrar o início do undo: {error}")))?;
    let mut compensations = Vec::new();
    for operation in operations.iter().rev() {
        let tool = operation.get("tool").and_then(Value::as_str).unwrap_or_default();
        if tool == "create_directory" { continue; }
        let relative = operation.get("path").and_then(Value::as_str).unwrap_or_default();
        let path = match workspace_path(&root, relative) { Ok(path) => path, Err(error) => {
            let rollback_errors = compensate_undo(&compensations);
            manifest["status"] = Value::String(if rollback_errors.is_empty() { "applied" } else { "undo_failed" }.into());
            let _ = write_json_atomic(&manifest_path, &manifest);
            return Err(RuntimeError::Workspace(format!("undo interrompido em {relative}: {error}; recuperação: {}", if rollback_errors.is_empty() { "estado aplicado restaurado".into() } else { rollback_errors.join("; ") })));
        }};
        let current = match fs::read(&path) { Ok(content) => content, Err(error) => {
            let rollback_errors = compensate_undo(&compensations);
            manifest["status"] = Value::String(if rollback_errors.is_empty() { "applied" } else { "undo_failed" }.into());
            let _ = write_json_atomic(&manifest_path, &manifest);
            return Err(RuntimeError::Workspace(format!("undo interrompido em {relative}: {error}; recuperação: {}", if rollback_errors.is_empty() { "estado aplicado restaurado".into() } else { rollback_errors.join("; ") })));
        }};
        let expected_hash = operation.get("expected_hash").and_then(Value::as_str).unwrap_or_default();
        let current_hash = format!("sha256:{:x}", Sha256::digest(&current));
        if current_hash != expected_hash {
            let rollback_errors = compensate_undo(&compensations);
            manifest["status"] = Value::String(if rollback_errors.is_empty() { "applied" } else { "undo_failed" }.into());
            let _ = write_json_atomic(&manifest_path, &manifest);
            return Err(RuntimeError::Workspace(format!("{relative} mudou durante o undo; nenhuma alteração posterior foi sobrescrita; recuperação: {}", if rollback_errors.is_empty() { "estado aplicado restaurado".into() } else { rollback_errors.join("; ") })));
        }
        let result = if tool == "create_file" {
            match fs::remove_file(&path) {
                Ok(()) => { compensations.push(UndoCompensation::RecreateFile {path, content: current}); Ok(()) },
                Err(error) => Err(error),
            }
        } else {
            let backup = operation.get("backup").and_then(Value::as_str).unwrap_or_default();
            let backup_path = match workspace_path(&root, backup) { Ok(value) => value, Err(error) => {
                let rollback_errors = compensate_undo(&compensations);
                manifest["status"] = Value::String(if rollback_errors.is_empty() { "applied" } else { "undo_failed" }.into());
                let _ = write_json_atomic(&manifest_path, &manifest);
                return Err(RuntimeError::Workspace(format!("backup inacessível: {error}")));
            }};
            let before = match fs::read(&backup_path) { Ok(value) => value, Err(error) => {
                let rollback_errors = compensate_undo(&compensations);
                manifest["status"] = Value::String(if rollback_errors.is_empty() { "applied" } else { "undo_failed" }.into());
                let _ = write_json_atomic(&manifest_path, &manifest);
                return Err(RuntimeError::Workspace(format!("backup inacessível: {error}")));
            }};
            let before_hash = format!("sha256:{:x}", Sha256::digest(&before));
            match atomic_replace_bytes(&path, &before) {
                Ok(()) => { compensations.push(UndoCompensation::ReapplyEdit {path, after_content: current, expected_restored_hash: before_hash}); Ok(()) },
                Err(error) => Err(error),
            }
        };
        if let Err(error) = result {
            let rollback_errors = compensate_undo(&compensations);
            manifest["status"] = Value::String(if rollback_errors.is_empty() { "applied" } else { "undo_failed" }.into());
            let _ = write_json_atomic(&manifest_path, &manifest);
            return Err(RuntimeError::Workspace(format!("undo interrompido em {relative}: {error}; recuperação: {}", if rollback_errors.is_empty() { "estado aplicado restaurado".into() } else { rollback_errors.join("; ") })));
        }
    }
    let created_directories = manifest.get("created_directories").and_then(Value::as_array).cloned().unwrap_or_default();
    let mut removed_directories = Vec::new();
    for relative in created_directories.iter().rev().filter_map(Value::as_str) {
        let path = workspace_path(&root, relative)?;
        match fs::remove_dir(&path) {
            Ok(()) => removed_directories.push(path),
            Err(error) => {
                for directory in removed_directories.iter().rev() { let _ = fs::create_dir(directory); }
                let rollback_errors = compensate_undo(&compensations);
                manifest["status"] = Value::String(if rollback_errors.is_empty() { "applied" } else { "undo_failed" }.into());
                let _ = write_json_atomic(&manifest_path, &manifest);
                return Err(RuntimeError::Workspace(format!("não foi possível remover {}: {error}; recuperação: {}", relative, if rollback_errors.is_empty() { "estado aplicado restaurado".into() } else { rollback_errors.join("; ") })));
            }
        }
    }
    manifest["status"] = Value::String("undone".into());
    write_json_atomic(&manifest_path, &manifest).map_err(|error| RuntimeError::Workspace(format!("lote desfeito, mas o manifesto não pôde ser atualizado: {error}")))?;
    let undone_paths: Vec<Value> = operations.iter().filter_map(|operation| {
        Some(json!({"tool":operation.get("tool")?.as_str()?,"path":operation.get("path")?.as_str()?}))
    }).collect();
    Ok(json!({"transaction_id":transaction_id,"status":"undone","already_undone":false,"count":operations.len(),"operations":undone_paths}))
}

fn rollback_batch(root: &Path, applied: &[Value], created_directories: &[PathBuf]) -> Vec<String> {
    let mut errors = Vec::new();
    for item in applied.iter().rev() {
        let result = &item["result"];
        match item["tool"].as_str().unwrap_or_default() {
            "create_file" => {
                if let Some(relative) = result.get("path").and_then(Value::as_str) {
                    match workspace_new_path(root, relative) {
                        Ok(path) if !path.exists() => {},
                        Ok(path) => {
                            let expected = result.get("artifact").and_then(|artifact| artifact.get("result_hash")).and_then(Value::as_str);
                            let actual = fs::read(&path).ok().map(|bytes| format!("sha256:{:x}", Sha256::digest(bytes)));
                            if actual.as_deref() != expected {
                                errors.push(format!("{relative} mudou durante rollback e foi preservado"));
                            } else if let Err(error) = fs::remove_file(path) { errors.push(format!("remover {relative}: {error}")); }
                        },
                        Err(error) => errors.push(format!("remover {relative}: {error}")),
                    }
                }
            }
            "edit_file" => {
                if let (Some(relative), Some(backup)) = (result.get("path").and_then(Value::as_str), result.get("backup").and_then(Value::as_str)) {
                    let restore = (|| -> Result<(), RuntimeError> {
                        let target = workspace_path(root, relative)?;
                        let backup_path = workspace_path(root, backup)?;
                        let expected = result.get("artifact").and_then(|artifact| artifact.get("result_hash")).and_then(Value::as_str).unwrap_or_default();
                        let actual = format!("sha256:{:x}", Sha256::digest(fs::read(&target).map_err(|error| RuntimeError::Workspace(error.to_string()))?));
                        if actual != expected { return Err(RuntimeError::Workspace(format!("{relative} mudou durante rollback e foi preservado"))); }
                        let content = fs::read(&backup_path).map_err(|error| RuntimeError::Workspace(error.to_string()))?;
                        let expected_backup = result.get("backup_hash").and_then(Value::as_str).unwrap_or_default();
                        let actual_backup = format!("sha256:{:x}", Sha256::digest(&content));
                        if actual_backup != expected_backup { return Err(RuntimeError::Workspace(format!("backup de {relative} mudou e não foi usado"))); }
                        atomic_replace_bytes(&target, &content).map_err(|error| RuntimeError::Workspace(error.to_string()))
                    })();
                    if let Err(error) = restore { errors.push(format!("restaurar {relative}: {error}")); }
                }
            }
            _ => {}
        }
    }
    for directory in created_directories.iter().rev() {
        match fs::remove_dir(directory) {
            Ok(()) => {},
            Err(error) if error.kind() == io::ErrorKind::NotFound => {},
            Err(error) => errors.push(format!("remover pasta {}: {error}", directory.display())),
        }
    }
    errors
}

fn apply_batch(args: &Value, root: &Path) -> Result<Value, RuntimeError> {
    let object = args.as_object().ok_or_else(|| RuntimeError::InvalidArgument("apply_batch exige um objeto".into()))?;
    if object.len() != 1 || !object.contains_key("operations") {
        return Err(RuntimeError::InvalidArgument("apply_batch aceita somente operations".into()));
    }
    let operations = args.get("operations").and_then(Value::as_array)
        .ok_or_else(|| RuntimeError::InvalidArgument("operations deve ser uma lista".into()))?;
    if operations.is_empty() || operations.len() > 32 {
        return Err(RuntimeError::InvalidArgument("o lote deve conter entre 1 e 32 operações".into()));
    }
    let mut total_bytes = 0usize;
    let mut targets = HashSet::new();
    let mut normalized = Vec::with_capacity(operations.len());
    for operation in operations {
        let operation_object = operation.as_object().ok_or_else(|| RuntimeError::InvalidArgument("cada operação deve ser um objeto".into()))?;
        if operation_object.len() != 2 || !operation_object.contains_key("tool") || !operation_object.contains_key("arguments") {
            return Err(RuntimeError::InvalidArgument("cada operação aceita somente tool e arguments".into()));
        }
        let tool = operation.get("tool").and_then(Value::as_str).ok_or_else(|| RuntimeError::InvalidArgument("cada operação precisa de tool".into()))?;
        let arguments = operation.get("arguments").and_then(Value::as_object).ok_or_else(|| RuntimeError::InvalidArgument("arguments deve ser um objeto".into()))?;
        let path = required_string(&Value::Object(arguments.clone()), "path")?;
        if path.len() > 1024 { return Err(RuntimeError::InvalidArgument("path excede 1024 bytes".into())); }
        let components: Vec<_> = Path::new(&path).components().collect();
        if components.is_empty() || components.len() > 64 || components.iter().any(|component| !matches!(component, std::path::Component::Normal(_))) {
            return Err(RuntimeError::InvalidArgument(format!("o caminho {path} deve conter apenas componentes relativos normais")));
        }
        if components.first().and_then(|component| component.as_os_str().to_str()) == Some(".ia-local-backups") {
            return Err(RuntimeError::InvalidArgument(".ia-local-backups é reservado para recuperação local".into()));
        }
        let target = match tool {
            "create_file" | "create_directory" => workspace_new_path(root, &path)?,
            "edit_file" => workspace_path(root, &path)?,
            _ => return Err(RuntimeError::InvalidArgument(format!("operação não permitida no lote: {tool}"))),
        };
        if !targets.insert(target.clone()) {
            return Err(RuntimeError::InvalidArgument(format!("o lote contém mais de uma operação sobre {path}")));
        }
        match tool {
            "create_file" => {
                if arguments.len() != 2 || !arguments.contains_key("content") { return Err(RuntimeError::InvalidArgument("create_file no lote aceita somente path e content".into())); }
                let content = arguments.get("content").and_then(Value::as_str).ok_or_else(|| RuntimeError::InvalidArgument("content deve ser string".into()))?;
                if content.len() > MAX_FILE_BYTES as usize { return Err(RuntimeError::Workspace(format!("conteúdo de {path} excede {MAX_FILE_BYTES} bytes"))); }
                if target.exists() { return Err(RuntimeError::Workspace(format!("o arquivo {path} já existe"))); }
                total_bytes = total_bytes.saturating_add(content.len());
            }
            "edit_file" => {
                if arguments.len() != 3 || !arguments.contains_key("old_text") || !arguments.contains_key("new_text") { return Err(RuntimeError::InvalidArgument("edit_file no lote aceita somente path, old_text e new_text".into())); }
                let old_text = arguments.get("old_text").and_then(Value::as_str).ok_or_else(|| RuntimeError::InvalidArgument("old_text deve ser string".into()))?;
                let new_text = arguments.get("new_text").and_then(Value::as_str).ok_or_else(|| RuntimeError::InvalidArgument("new_text deve ser string".into()))?;
                if old_text.is_empty() { return Err(RuntimeError::InvalidArgument("old_text não pode ser vazio".into())); }
                if old_text.len() > MAX_FILE_BYTES as usize || new_text.len() > MAX_FILE_BYTES as usize { return Err(RuntimeError::Workspace(format!("edição de {path} excede {MAX_FILE_BYTES} bytes"))); }
                let content = fs::read_to_string(&target).map_err(|error| RuntimeError::Workspace(error.to_string()))?;
                if content.match_indices(old_text).count() != 1 { return Err(RuntimeError::Workspace(format!("o trecho antigo de {path} deve ocorrer exatamente uma vez"))); }
                if content.replacen(old_text, new_text, 1).len() > MAX_FILE_BYTES as usize { return Err(RuntimeError::Workspace(format!("arquivo resultante {path} excede {MAX_FILE_BYTES} bytes"))); }
                total_bytes = total_bytes.saturating_add(old_text.len()).saturating_add(new_text.len());
            }
            "create_directory" => {
                if arguments.len() != 1 { return Err(RuntimeError::InvalidArgument("create_directory no lote aceita somente path".into())); }
                if target.exists() { return Err(RuntimeError::Workspace(format!("o diretório {path} já existe"))); }
            }
            _ => unreachable!(),
        }
        if total_bytes > 256 * 1024 { return Err(RuntimeError::Workspace("texto total do lote excede 256 KiB".into())); }
        normalized.push((tool.to_string(), Value::Object(arguments.clone()), target));
    }

    let root = root.canonicalize().map_err(|error| RuntimeError::Workspace(error.to_string()))?;
    let manifest_dir = workspace_new_path(&root, ".ia-local-backups/batches")?;
    let mut applied = Vec::new();
    let mut created_directories = Vec::new();
    for (tool, arguments, target) in normalized {
        if tool == "create_file" || tool == "create_directory" {
            match batch_created_directories(&root, &target, tool == "create_directory") {
                Ok(directories) => created_directories.extend(directories),
                Err(error) => return Err(error),
            }
        }
        let result = match tool.as_str() {
            "create_file" => create_file(&arguments, &root),
            "edit_file" => edit_file(&arguments, &root),
            "create_directory" => create_directory(&arguments, &root),
            _ => unreachable!(),
        };
        match result {
            Ok(value) => applied.push(json!({"tool": tool, "result": value})),
            Err(error) => {
                let rollback_errors = rollback_batch(&root, &applied, &created_directories);
                let suffix = if rollback_errors.is_empty() { "rollback concluído".to_string() }
                    else { format!("rollback incompleto: {}", rollback_errors.join("; ")) };
                return Err(RuntimeError::Workspace(format!("lote cancelado após falha: {error}; {suffix}")));
            }
        }
    }
    let stamp = SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default().as_nanos();
    let mut digest = Sha256::new();
    digest.update(root.to_string_lossy().as_bytes());
    digest.update(stamp.to_le_bytes());
    digest.update(serde_json::to_vec(&applied).unwrap_or_default());
    let hash = digest.finalize();
    let transaction_id = format!("batch-{}", hash[..12].iter().map(|byte| format!("{byte:02x}")).collect::<String>());
    let undo_operations: Vec<Value> = applied.iter().filter_map(|item| {
        let tool = item.get("tool")?.as_str()?;
        let result = item.get("result")?;
        let path = result.get("path")?.as_str()?;
        if tool == "create_directory" { return Some(json!({"tool":tool,"path":path})); }
        let expected_hash = result.get("artifact")?.get("result_hash")?.as_str()?;
        if tool == "edit_file" {
            let backup = result.get("backup")?.as_str()?;
            let before_hash = result.get("backup_hash")?.as_str()?;
            Some(json!({"tool":tool,"path":path,"backup":backup,"expected_hash":expected_hash,"before_hash":before_hash}))
        } else if tool == "create_file" {
            Some(json!({"tool":tool,"path":path,"expected_hash":expected_hash}))
        } else { None }
    }).collect();
    let created_directory_names: Vec<String> = created_directories.iter()
        .filter_map(|path| path.strip_prefix(&root).ok())
        .map(|path| path.to_string_lossy().replace('\\', "/"))
        .collect();
    let manifest = json!({"schema":"engineering-batch-undo/v1","transaction_id":transaction_id,"status":"applied","created_at_ns":stamp.to_string(),
        "operations":undo_operations,"created_directories":created_directory_names});
    if let Err(error) = fs::create_dir_all(&manifest_dir) {
        let rollback_errors = rollback_batch(&root, &applied, &created_directories);
        let suffix = if rollback_errors.is_empty() { "rollback concluído".to_string() } else { format!("rollback incompleto: {}", rollback_errors.join("; ")) };
        return Err(RuntimeError::Workspace(format!("não foi possível criar o registro de recuperação: {error}; {suffix}")));
    }
    let manifest_path = manifest_dir.join(format!("{transaction_id}.json"));
    let manifest_result = if manifest_path.exists() { Err(io::Error::new(io::ErrorKind::AlreadyExists, "identificador de recuperação duplicado")) }
        else { create_file_bytes(&manifest_path, &serde_json::to_vec_pretty(&manifest).unwrap_or_default()) };
    if let Err(error) = manifest_result {
        let rollback_errors = rollback_batch(&root, &applied, &created_directories);
        let suffix = if rollback_errors.is_empty() { "rollback concluído".to_string() } else { format!("rollback incompleto: {}", rollback_errors.join("; ")) };
        return Err(RuntimeError::Workspace(format!("não foi possível salvar o registro de recuperação: {error}; {suffix}")));
    }
    Ok(json!({"schema":"engineering-batch/v1","ok":true,"atomic":false,"rollback_on_error":true,
        "transaction_id":transaction_id,"undo_available":true,"count":applied.len(),"operations":applied}))
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
        "png" | "jpg" | "jpeg" | "webp" | "gif" | "bmp" | "pbm" | "pgm" | "tif" | "tiff" => "image",
        "wav" | "mp3" | "ogg" | "flac" | "m4a" => "audio",
        "mp4" | "mkv" | "webm" | "mov" => "video",
        "pdf" | "txt" | "md" | "markdown" | "json" | "jsonl" | "csv" | "tsv" | "html" | "htm" | "xml" | "yaml" | "yml" | "toml" | "rst" | "rtf" | "doc" | "docx" | "xls" | "xlsx" | "ppt" | "pptx" | "odt" | "ods" | "odp" | "epub" | "ipynb" | "eml" => "document",
        _ => "unknown",
    };
    if metadata.len() > 16 * 1024 * 1024 || media_type == "unknown" {
        return Ok(json!({"path": relative, "media_type": media_type, "extension": extension, "bytes": metadata.len(),
            "status":"limited", "warnings":["Somente metadados; o leitor local aceita formatos registrados até 16 MiB."]}));
    }
    let project_root = Path::new(env!("CARGO_MANIFEST_DIR")).parent()
        .ok_or_else(|| RuntimeError::Workspace("raiz do leitor local não encontrada".into()))?;
    let python = project_root.join(".venv/bin/python");
    let output = Command::new(python).arg(project_root.join("python/attachment_media.py")).arg("--path").arg(path).output()
        .map_err(|error| RuntimeError::Workspace(format!("não foi possível iniciar o leitor de mídia: {error}")))?;
    if !output.status.success() {
        return Err(RuntimeError::Workspace(String::from_utf8_lossy(&output.stderr).chars().take(500).collect()));
    }
    let mut data: Value = serde_json::from_slice(&output.stdout)
        .map_err(|error| RuntimeError::Workspace(format!("resposta inválida do leitor de mídia: {error}")))?;
    data["path"] = json!(relative);
    Ok(data)
}

fn extract_document_text(args: &Value, root: &Path) -> Result<Value, RuntimeError> {
    let relative = required_string(args, "path")?;
    let path = workspace_path(root, &relative)?;
    let metadata = fs::metadata(&path).map_err(|error| RuntimeError::Workspace(error.to_string()))?;
    if !metadata.is_file() || metadata.len() > 64 * 1024 * 1024 {
        return Err(RuntimeError::Workspace("documento inválido ou acima do limite".into()));
    }
    let project_root = Path::new(env!("CARGO_MANIFEST_DIR")).parent()
        .ok_or_else(|| RuntimeError::Workspace("raiz local do leitor não encontrada".into()))?;
    let reader = project_root.join("python/document_reader.py");
    if !reader.is_file() {
        return Err(RuntimeError::Workspace("leitor local de documentos não está instalado".into()));
    }
    let python_env = project_root.join(".venv/bin/python");
    let mut command = if python_env.is_file() { Command::new(python_env) } else { Command::new("python3") };
    let max_chars = args.get("max_chars").and_then(Value::as_u64).unwrap_or(200_000).clamp(1, 200_000);
    let output = command.arg(reader).arg(&path).arg("--max-chars").arg(max_chars.to_string()).output()
        .map_err(|error| RuntimeError::Workspace(format!("não foi possível iniciar o leitor local: {error}")))?;
    if !output.status.success() {
        return Err(RuntimeError::Workspace(String::from_utf8_lossy(&output.stderr).trim().to_string()));
    }
    let mut extracted: Value = serde_json::from_slice(&output.stdout)
        .map_err(|error| RuntimeError::Workspace(format!("resposta inválida do leitor de documentos: {error}")))?;
    if let Some(object) = extracted.as_object_mut() {
        object.insert("path".into(), json!(relative));
        object.insert("content_trust".into(), json!("untrusted_document_data"));
    }
    Ok(extracted)
}

fn search_files(args: &Value, root: &Path) -> Result<Value, RuntimeError> {
    let query = required_string(args, "query")?.to_lowercase();
    if query.is_empty() {
        return Err(RuntimeError::InvalidArgument("query vazia".into()));
    }
    let max_results = args.get("max_results").and_then(Value::as_u64).unwrap_or(5).clamp(1, 100) as usize;
    let context_lines = args.get("context_lines").and_then(Value::as_u64).unwrap_or(0).min(5) as usize;
    let mut matches = Vec::new();
    let relative = args.get("path").and_then(Value::as_str).unwrap_or("");
    let start = workspace_path(root, relative)?;
    if !start.is_dir() { return Err(RuntimeError::InvalidArgument("path deve ser uma pasta".into())); }
    let mut stack = vec![start];
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
                let source_lines: Vec<&str> = content.lines().collect();
                for (line_number, line) in source_lines.iter().enumerate() {
                    if line.to_lowercase().contains(&query) {
                        let relative = path.strip_prefix(root).unwrap_or(&path).display().to_string();
                        let start_line = (line_number + 1).saturating_sub(context_lines).max(1);
                        let end_line = (line_number + context_lines + 1).min(source_lines.len());
                        let trimmed = line.trim();
                        let text: String = trimmed.chars().take(240).collect();
                        let mut found = json!({
                            "path": relative,
                            "line": line_number + 1,
                            "text": text,
                            "text_truncated": trimmed.chars().count() > 240
                        });
                        if context_lines > 0 {
                            let snippet = source_lines[start_line - 1..end_line].join("\n");
                            found["start_line"] = json!(start_line);
                            found["end_line"] = json!(end_line);
                            found["snippet"] = json!(snippet.chars().take(2000).collect::<String>());
                        }
                        matches.push(found);
                        if matches.len() >= max_results { return Ok(json!({"query": query, "matches": matches, "truncated": true, "max_results": max_results})); }
                    }
                }
            }
        }
    }
    Ok(json!({"query": query, "matches": matches, "truncated": false}))
}

fn local_programming_operation(script: &str, arguments: &[(&str, &str)], root: &Path) -> Result<Value, RuntimeError> {
    let project_root = Path::new(env!("CARGO_MANIFEST_DIR")).parent()
        .ok_or_else(|| RuntimeError::Workspace("raiz das ferramentas de programação ausente".into()))?;
    let mut command = Command::new(project_root.join(".venv/bin/python"));
    command.arg(project_root.join("python").join(script)).arg("--workspace").arg(root)
        .env_remove("PYTHONPATH").env_remove("PYTHONHOME");
    for (flag, value) in arguments { command.arg(flag).arg(value); }
    let output = command.output()
        .map_err(|error| RuntimeError::Workspace(format!("ferramenta local de programação indisponível: {error}")))?;
    if !output.status.success() {
        return Err(RuntimeError::InvalidArgument(String::from_utf8_lossy(&output.stderr).chars().take(1500).collect()));
    }
    serde_json::from_slice(&output.stdout)
        .map_err(|error| RuntimeError::Workspace(format!("resposta de programação fora do contrato: {error}")))
}

fn inspect_code(args: &Value, root: &Path) -> Result<Value, RuntimeError> {
    let requested = args.get("path").and_then(Value::as_str).unwrap_or("");
    workspace_path(root, requested)?;
    local_programming_operation("code_intelligence.py", &[("--path", requested)], root)
}

fn project_checks(args: &Value, root: &Path) -> Result<Value, RuntimeError> {
    let requested = args.get("check").and_then(Value::as_str).unwrap_or("auto");
    let changed_path = args.get("path").and_then(Value::as_str).unwrap_or("");
    if !changed_path.is_empty() { workspace_new_path(root, changed_path)?; }
    local_programming_operation("programming_checks.py", &[("--check", requested), ("--path", changed_path)], root)
}

fn computation_operation(tool: &str, args: &Value, root: &Path) -> Result<Value, RuntimeError> {
    if tool == "evaluate_function" { workspace_path(root, &required_string(args, "path")?)?; }
    let encoded = serde_json::to_string(args)
        .map_err(|error| RuntimeError::InvalidArgument(error.to_string()))?;
    if encoded.len() > 32_768 { return Err(RuntimeError::InvalidArgument("entrada do motor excede 32 KiB".into())); }
    local_programming_operation("execution_engine.py", &[("--tool", tool), ("--arguments", &encoded)], root)
}

fn read_bounded_terminal_output(mut stream: impl Read) -> io::Result<(Vec<u8>, bool)> {
    const LIMIT: usize = 12_000;
    let mut output = Vec::new();
    let mut buffer = [0u8; 4096];
    let mut truncated = false;
    loop {
        let count = stream.read(&mut buffer)?;
        if count == 0 { break; }
        let remaining = LIMIT.saturating_sub(output.len());
        let keep = count.min(remaining);
        output.extend_from_slice(&buffer[..keep]);
        truncated |= keep < count;
    }
    Ok((output, truncated))
}

fn run_git_terminal_profile(operation: &str, root: &Path) -> Result<Value, RuntimeError> {
    let arguments: &[&str] = match operation {
        "git_status" => &["--no-optional-locks", "status", "--short"],
        "git_diff_stat" => &["--no-optional-locks", "diff", "--stat"],
        "git_diff" => &["--no-optional-locks", "diff", "--no-ext-diff", "--no-textconv", "--"],
        _ => return Err(RuntimeError::InvalidArgument("perfil git não permitido".into())),
    };
    let started = Instant::now();
    let command = std::iter::once("git").chain(arguments.iter().copied()).collect::<Vec<_>>().join(" ");
    let mut child = Command::new("git").args(arguments).current_dir(root)
        .env("GIT_OPTIONAL_LOCKS", "0")
        .stdout(Stdio::piped()).stderr(Stdio::piped()).spawn()
        .map_err(|error| RuntimeError::Workspace(format!("não foi possível iniciar git: {error}")))?;
    let stdout = child.stdout.take().ok_or_else(|| RuntimeError::Workspace("stdout indisponível".into()))?;
    let stderr = child.stderr.take().ok_or_else(|| RuntimeError::Workspace("stderr indisponível".into()))?;
    let stdout_reader = std::thread::spawn(move || read_bounded_terminal_output(stdout));
    let stderr_reader = std::thread::spawn(move || read_bounded_terminal_output(stderr));
    let timeout = Duration::from_secs(5);
    let mut timed_out = false;
    let status = loop {
        if let Some(status) = child.try_wait().map_err(|error| RuntimeError::Workspace(error.to_string()))? { break status; }
        if started.elapsed() > timeout {
            timed_out = true;
            let _ = child.kill();
            break child.wait().map_err(|error| RuntimeError::Workspace(error.to_string()))?;
        }
        std::thread::sleep(Duration::from_millis(25));
    };
    let (stdout, stdout_truncated) = stdout_reader.join().map_err(|_| RuntimeError::Workspace("leitor de stdout falhou".into()))?
        .map_err(|error| RuntimeError::Workspace(error.to_string()))?;
    let (stderr, stderr_truncated) = stderr_reader.join().map_err(|_| RuntimeError::Workspace("leitor de stderr falhou".into()))?
        .map_err(|error| RuntimeError::Workspace(error.to_string()))?;
    Ok(json!({"operation":operation, "command":command, "passed":status.success() && !timed_out,
        "timed_out":timed_out, "exit_code":status.code(), "elapsed_ms":started.elapsed().as_millis(),
        "stdout":String::from_utf8_lossy(&stdout), "stderr":String::from_utf8_lossy(&stderr),
        "truncated":stdout_truncated || stderr_truncated,
        "summary":if timed_out { "o comando de inspeção excedeu 5 segundos e foi interrompido" } else { "comando de leitura concluído" }}))
}

fn terminal_run(args: &Value, root: &Path) -> Result<Value, RuntimeError> {
    let object = args.as_object().ok_or_else(|| RuntimeError::InvalidArgument("argumentos do terminal devem ser um objeto".into()))?;
    if object.keys().any(|key| !matches!(key.as_str(), "operation" | "check")) {
        return Err(RuntimeError::InvalidArgument("terminal_run aceita somente operation e check".into()));
    }
    let operation = required_string(args, "operation")?;
    match operation.as_str() {
        "git_status" => {
            if args.get("check").is_some() { return Err(RuntimeError::InvalidArgument("check só é aceito em project_check".into())); }
            run_git_terminal_profile(&operation, root)
        }
        "git_diff_stat" => {
            if args.get("check").is_some() { return Err(RuntimeError::InvalidArgument("check só é aceito em project_check".into())); }
            run_git_terminal_profile(&operation, root)
        }
        "project_check" => {
            let check = args.get("check").and_then(Value::as_str).unwrap_or("auto");
            if !matches!(check, "auto" | "all" | "cargo-test" | "npm-test" | "npm-check" | "npm-build" | "pytest" | "unittest" | "node-test" | "go-test" | "python-syntax" | "node-syntax" | "cpp-syntax" | "c-syntax" | "shell-syntax") {
                return Err(RuntimeError::InvalidArgument("verificação não permitida".into()));
            }
            let result = project_checks(&json!({"check":check}), root)?;
            let command = result.get("command").and_then(Value::as_str).unwrap_or("verificador reconhecido do projeto");
            let summary = result.get("message").and_then(Value::as_str).unwrap_or_else(|| {
                if result.get("passed").and_then(Value::as_bool) == Some(true) { "verificação concluída" } else { "verificação concluída com falha" }
            });
            Ok(json!({"operation":operation, "check":result.get("check").cloned().unwrap_or(json!(check)),
                "command":command, "passed":result.get("passed").cloned().unwrap_or(Value::Null),
                "exit_code":result.get("exit_code").cloned().unwrap_or(Value::Null),
                "timed_out":result.get("timed_out").cloned().unwrap_or(json!(false)),
                "executed":result.get("executed").cloned().unwrap_or(json!(false)),
                "elapsed_ms":result.get("elapsed_ms").cloned().unwrap_or(json!(0)),
                "stdout":result.get("stdout").cloned().unwrap_or(json!("")),
                "stderr":result.get("stderr").cloned().unwrap_or(json!("")),
                "summary":summary, "available":result.get("available").cloned().unwrap_or(json!([]))}))
        }
        _ => Err(RuntimeError::InvalidArgument("operação de terminal não permitida".into())),
    }
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

fn inspection_path_priority(path: &Path) -> u8 {
    let parts = path.iter().map(|part| part.to_string_lossy().to_lowercase()).collect::<Vec<_>>();
    if parts.iter().any(|part| matches!(part.as_str(), "model" | "models" | "corpus" | "datasets" | "data" | "logs" | "artifacts" | "vendor" | "colab")) {
        return 3;
    }
    if parts.iter().any(|part| matches!(part.as_str(), "src" | "app" | "lib" | "runtime" | "python" | "agent-core")) {
        return 0;
    }
    if parts.iter().any(|part| matches!(part.as_str(), "docs" | "documentation" | "documentacoes" | "config" | "tests" | "test")) {
        return 1;
    }
    2
}

fn inspect_project(args: &Value, root: &Path) -> Result<Value, RuntimeError> {
    let max_depth = args.get("max_depth").and_then(Value::as_u64).unwrap_or(4).clamp(1, 6) as usize;
    let started = Instant::now();
    let ignored = ["target", "node_modules", "dist", "build", ".venv", "venv", "__pycache__", ".git", ".cache"];
    let mut stack = vec![(root.to_path_buf(), 0usize)];
    let mut files = Vec::new();
    let mut directories = Vec::new();
    let mut manifests = Vec::new();
    let mut test_files = Vec::new();
    let mut entrypoints = Vec::new();
    let mut truncated = false;
    while let Some((directory, depth)) = stack.pop() {
        let entries = fs::read_dir(&directory).map_err(|error| RuntimeError::Workspace(error.to_string()))?;
        let mut entries = entries.collect::<Result<Vec<_>, _>>()
            .map_err(|error| RuntimeError::Workspace(error.to_string()))?;
        entries.sort_by_key(|entry| entry.file_name().to_string_lossy().to_lowercase());
        let mut child_directories: Vec<(PathBuf, String)> = Vec::new();
        for entry in entries {
            if started.elapsed() > Duration::from_secs(3) || files.len() >= 400 || directories.len() >= 400 {
                truncated = true;
                break;
            }
            let name = entry.file_name().to_string_lossy().to_string();
            if ignored.contains(&name.as_str()) {
                continue;
            }
            let file_type = entry.file_type().map_err(|error| RuntimeError::Workspace(error.to_string()))?;
            if file_type.is_symlink() {
                continue;
            }
            let path = entry.path();
            let relative = path.strip_prefix(root).unwrap_or(&path).display().to_string();
            if file_type.is_dir() {
                directories.push(relative.clone());
                if depth < max_depth && !name.starts_with('.') {
                    child_directories.push((path, name));
                }
                continue;
            }
            if !file_type.is_file() {
                continue;
            }
            files.push(json!({"path": relative, "bytes": entry.metadata().map(|metadata| metadata.len()).unwrap_or(0)}));
            let lower = name.to_lowercase();
            if matches!(lower.as_str(), "cmakelists.txt" | "makefile" | "cargo.toml" | "package.json" | "pyproject.toml" | "requirements.txt" | "go.mod" | "pom.xml" | "build.gradle" | "gemfile" | "composer.json") {
                manifests.push(relative.clone());
            }
            if lower.contains("test") || lower.contains("spec") || relative.split('/').any(|part| matches!(part, "tests" | "test" | "__tests__")) {
                test_files.push(relative.clone());
            }
            if matches!(lower.as_str(), "main.c" | "main.cc" | "main.cpp" | "app.c" | "app.cc" | "app.cpp" | "main.py" | "app.py" | "cli.py" | "todo_cli.py" | "todo_ui.py" | "model_server.py" | "__main__.py" | "manage.py" | "run.py" | "server.py" | "main.rs" | "lib.rs" | "main.go" | "index.js" | "index.ts" | "server.js" | "server.ts") {
                entrypoints.push(relative);
            }
        }
        // A large data, model, or documentation tree must not consume the
        // inspection budget before source directories and entry points.
        child_directories.sort_by_key(|(_, name)| {
            let normalized = name.to_lowercase();
            let priority = match normalized.as_str() {
                "src" | "app" | "lib" | "runtime" | "python" | "agent-core" | "tests" => 0,
                "docs" | "documentation" | "documentacoes" | "config" => 1,
                "model" | "models" | "corpus" | "datasets" | "data" | "logs" | "artifacts" | "vendor" => 3,
                _ => 2,
            };
            (priority, normalized)
        });
        for (path, _) in child_directories.into_iter().rev() {
            stack.push((path, depth + 1));
        }
        // Keep the traversal globally priority-ordered: a data folder found
        // below `python/` must not jump ahead of sibling source directories.
        stack.sort_by(|(left, _), (right, _)| {
            inspection_path_priority(right).cmp(&inspection_path_priority(left))
                .then_with(|| right.cmp(left))
        });
        if truncated {
            break;
        }
    }
    files.sort_by(|left, right| left["path"].as_str().cmp(&right["path"].as_str()));
    directories.sort();
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
        "directories": directories,
        "directory_count": directories.len(),
        "file_count": files.len(),
        "truncated": truncated,
        "manifests": manifests,
            "test_files": test_files,
            "entrypoints": entrypoints,
            "checks": checks["available"].clone(),
            "check_locations": checks["locations"].clone(),
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
        .json(&json!({"messages": request.messages, "request_id": request.request_id,
                      "workspace_selected": request.workspace_selected,
                      "max_tokens": DEFAULT_CHAT_GENERATION_TOKENS}))
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

fn call_agent_core_for_attachments(request: &ChatRequest) -> Result<Value, RuntimeError> {
    validate_chat(request)?;
    let prompt = request.messages.iter().rev()
        .find(|message| message.role == "user")
        .map(|message| message.content.clone())
        .unwrap_or_default();
    let mut attachments = Vec::new();
    for message in &request.messages {
        for attachment in &message.attachments {
            if let Some(files) = attachment.get("files").and_then(Value::as_array) {
                for file in files {
                    attachments.push(json!({
                        "path": file.get("path").and_then(Value::as_str).unwrap_or("attachment"),
                        "content": file.get("content").and_then(Value::as_str).unwrap_or(""),
                        "mediaType": file.get("mediaType").and_then(Value::as_str),
                    }));
                }
            }
        }
    }
    let workspace = std::env::current_dir().map_err(|error| RuntimeError::Workspace(error.to_string()))?;
    let client = reqwest::blocking::Client::builder().timeout(Duration::from_secs(900)).build()
        .map_err(|error| RuntimeError::Model(error.to_string()))?;
    client.post("http://127.0.0.1:3200/pursue")
        .json(&json!({
            "prompt": prompt,
            "objective": "auto",
            "workspaceRoot": workspace,
            "operationId": format!("agent-core-attachment-{}", request.request_id.as_deref().unwrap_or("local")),
            "attachments": attachments,
        }))
        .send()
        .and_then(|response| response.error_for_status())
        .map_err(|error| RuntimeError::Model(format!("AgentCore indisponível: {error}")))?
        .json()
        .map_err(|error| RuntimeError::Model(error.to_string()))
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

fn parse_search_freshness(args: &Value) -> Result<Option<&'static str>, RuntimeError> {
    match args.get("freshness") {
        None | Some(Value::Null) => Ok(None),
        Some(Value::String(value)) => match value.as_str() {
            "pd" => Ok(Some("pd")),
            "pw" => Ok(Some("pw")),
            "pm" => Ok(Some("pm")),
            "py" => Ok(Some("py")),
            _ => Err(RuntimeError::InvalidArgument(
                "freshness deve ser pd, pw, pm ou py".into(),
            )),
        },
        Some(_) => Err(RuntimeError::InvalidArgument(
            "freshness deve ser uma string".into(),
        )),
    }
}

fn brave_llm_context_with_timeout(
    query: &str,
    freshness: Option<&str>,
    sources: &mut HashMap<String, SourceRecord>,
    next_id: &mut u64,
    timeout: Duration,
) -> Result<Option<Vec<Value>>, RuntimeError> {
    let token = std::env::var("IA_LOCAL_BRAVE_SEARCH_API_KEY")
        .unwrap_or_default()
        .trim()
        .to_string();
    if token.is_empty() {
        return Ok(None);
    }
    if query.chars().count() > 600 || query.split_whitespace().count() > 75 {
        return Err(RuntimeError::Search(
            "a consulta excede o limite de 600 caracteres ou 75 palavras da API Brave".into(),
        ));
    }

    let endpoint = "https://api.search.brave.com/res/v1/llm/context";
    let mut request_body = json!({
        "q": query.trim(),
        "country": "BR",
        "search_lang": "pt-br",
        "count": 10,
        "safesearch": "moderate",
        "maximum_number_of_urls": 5,
        "maximum_number_of_tokens": 4096,
        "maximum_number_of_snippets": 24,
        "maximum_number_of_tokens_per_url": 1024,
        "maximum_number_of_snippets_per_url": 6,
        "enable_source_metadata": true
    });
    if let Some(freshness) = freshness {
        request_body["freshness"] = Value::String(freshness.to_string());
    }
    let client = reqwest::blocking::Client::builder()
        .timeout(timeout)
        .build()
        .map_err(|_| RuntimeError::Search("cliente da API de busca indisponível".into()))?;
    let response = client.post(endpoint)
        .header(reqwest::header::ACCEPT, "application/json")
        .header(reqwest::header::CONTENT_TYPE, "application/json")
        .header(reqwest::header::CACHE_CONTROL, "no-cache")
        .header("X-Subscription-Token", &token)
        .json(&request_body)
        .send()
        .map_err(|_| RuntimeError::Search("a API Brave Search está indisponível".into()))?;
    let status = response.status();
    if !status.is_success() {
        return Err(RuntimeError::Search(format!(
            "a API Brave LLM Context retornou HTTP {}", status.as_u16(),
        )));
    }
    let payload = response.json::<Value>()
        .map_err(|_| RuntimeError::Search("a API Brave LLM Context devolveu JSON inválido".into()))?;
    let source_metadata = payload.get("sources").and_then(Value::as_object);
    let mut results = Vec::new();
    for item in payload.pointer("/grounding/generic")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
    {
        let Some(title) = item.get("title").and_then(Value::as_str).filter(|value| !value.trim().is_empty()) else {
            continue;
        };
        let Some(link) = item.get("url").and_then(Value::as_str).filter(|value| {
            url::Url::parse(value).ok().is_some_and(|parsed| {
                matches!(parsed.scheme(), "https" | "http") && parsed.host_str().is_some()
            })
        }) else {
            continue;
        };
        let context_chunks = item.get("snippets").and_then(Value::as_array)
            .into_iter()
            .flatten()
            .filter_map(Value::as_str)
            .map(str::trim)
            .filter(|snippet| !snippet.is_empty())
            .collect::<Vec<_>>();
        if context_chunks.is_empty() {
            continue;
        }
        let context_text = context_chunks.join("\n\n");
        let snippet = context_text.chars().take(700).collect::<String>();
        let metadata = source_metadata
            .and_then(|items| items.get(link))
            .cloned()
            .unwrap_or(Value::Null);
        let source_id = next_source_id(next_id);
        sources.insert(source_id.clone(), SourceRecord {
            source_id: source_id.clone(),
            title: title.trim().to_string(),
            url: link.to_string(),
            snippet: snippet.clone(),
            text: Some(context_text.clone()),
        });
        results.push(json!({
            "source_id": source_id,
            "title": title.trim(),
            "url": link,
            "displayed_url": link,
            "snippet": snippet,
            "context_text": context_text,
            "source_metadata": metadata,
        }));
    }
    Ok(Some(results))
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
    let freshness = parse_search_freshness(args)?;

    let provider = args.get("provider").and_then(Value::as_str).unwrap_or("auto");
    if !matches!(provider, "auto" | "brave") {
        return Err(RuntimeError::InvalidArgument("provedor de busca inválido".into()));
    }
    if let Some(results) = brave_llm_context_with_timeout(&query, freshness, sources, next_id, timeout)? {
        let result_count = results.len();
        return Ok(json!({
            "query": query,
            "results": results,
            "source": "brave-llm-context-api",
            "freshness": freshness,
            "freshness_applied": freshness.is_some(),
            "result_count": result_count
        }));
    }
    if provider == "brave" {
        return Err(RuntimeError::Search("a chave da API Brave não está configurada neste runtime".into()));
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
                        "freshness": freshness,
                        "freshness_applied": false,
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
        "freshness": freshness,
        "freshness_applied": false,
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

fn result_matches_requested_site(query: &str, result: &Value) -> bool {
    let Some(site) = query.split_whitespace()
        .find_map(|word| word.strip_prefix("site:"))
        .map(|value| value.trim().trim_matches('/').to_lowercase()) else {
        return true;
    };
    if site.is_empty() { return true; }
    let Some(host) = result.get("url").and_then(Value::as_str)
        .and_then(|url| url::Url::parse(url).ok())
        .and_then(|url| url.host_str().map(str::to_lowercase)) else {
        return false;
    };
    host == site || host.strip_prefix("www.") == Some(site.as_str())
        || site.strip_prefix("www.") == Some(host.as_str())
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
        "cmake fetchcontent" => vec![
            "https://cmake.org/cmake/help/latest/module/FetchContent.html".into(),
            "https://cmake.org/cmake/help/latest/guide/using-dependencies/index.html".into(),
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
    // Qualified symbols are the subject, not sentence boundaries (asyncio.TaskGroup).
    let anchors: Vec<String> = query.split_whitespace().filter_map(|word| {
        let word = word.trim_matches(|ch: char| !ch.is_alphanumeric() && ch != '_');
        let parts: Vec<&str> = word.split('.').collect();
        (parts.len() > 1 && !word.contains('/') && !word.contains(':') && parts.iter().all(|part| !part.is_empty()))
            .then(|| parts.last().unwrap().to_lowercase())
    }).collect();
    let minimum_score = if terms.len() >= 2 { 2 } else { 1 };
    let mut candidates: Vec<(usize, String, String)> = Vec::new();
    for page in pages {
        let source_id = page.get("source_id").and_then(Value::as_str).unwrap_or("fonte").to_string();
        let text = page.get("text").and_then(Value::as_str).unwrap_or_default();
        let sentence_text = text.replace(". ", ".\n").replace("! ", "!\n").replace("? ", "?\n");
        for sentence in sentence_text.split('\n') {
            let sentence = sentence.split_whitespace().collect::<Vec<_>>().join(" ");
            if sentence.chars().count() < 40 {
                continue;
            }
            let lower = sentence.to_lowercase();
            if !anchors.is_empty() && !anchors.iter().any(|anchor| lower.contains(anchor)) {
                continue;
            }
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
    let freshness = parse_search_freshness(args)?;
    let topic = args.get("topic").and_then(Value::as_str).filter(|value| !value.trim().is_empty());
    let inferred_documentation_topic = if topic.is_none()
        && query.to_lowercase().contains("site:cmake.org")
        && query.to_lowercase().contains("fetchcontent")
    { Some("CMake FetchContent") } else { None };
    let source_url = args.get("source_url").and_then(Value::as_str).filter(|value| !value.trim().is_empty());
    let save_to_corpus = args.get("save_to_corpus").and_then(Value::as_bool).unwrap_or(false);
    let category = args.get("category").and_then(Value::as_str).unwrap_or("web-research").trim();
    let brave_configured = std::env::var("IA_LOCAL_BRAVE_SEARCH_API_KEY")
        .map(|value| !value.trim().is_empty())
        .unwrap_or(false);
    let provider = args.get("provider").and_then(Value::as_str).unwrap_or("auto");
    if !matches!(provider, "auto" | "brave") {
        return Err(RuntimeError::InvalidArgument("provedor de pesquisa inválido".into()));
    }
    if provider == "brave" && !brave_configured {
        return Err(RuntimeError::Search("a chave da API Brave não está configurada neste runtime".into()));
    }
    let brave_storage_allowed = std::env::var("IA_LOCAL_BRAVE_ALLOW_STORAGE")
        .map(|value| matches!(value.trim().to_ascii_lowercase().as_str(), "1" | "true" | "yes"))
        .unwrap_or(false);
    if save_to_corpus && brave_configured && !brave_storage_allowed {
        return Err(RuntimeError::InvalidArgument(
            "o armazenamento de conteúdo da Brave está desabilitado; habilite IA_LOCAL_BRAVE_ALLOW_STORAGE=true somente se seu plano conceder direitos explícitos de armazenamento".into(),
        ));
    }
    let started = Instant::now();
    let mut pages = Vec::new();
    let mut results = Vec::new();
    let mut attempts = Vec::new();
    let mut providers_used = HashSet::new();
    let mut freshness_applied = false;
    let mut opened_urls = HashSet::new();
    let mut opened_hosts = HashSet::new();
    // Um link de repositório já é uma fonte primária identificada. Não
    // desperdice o orçamento tentando o buscador antes de abrir README,
    // árvore seletiva e arquivos educacionais do próprio GitHub; a pesquisa
    // web continua sendo usada para complementar competências depois.
    let searches = if source_url.is_some() {
        Vec::new()
    } else if let Some(items) = args.get("queries") {
        let list = items.as_array().ok_or_else(|| RuntimeError::InvalidArgument("queries deve ser uma lista".into()))?;
        if list.is_empty() || list.len() > 3 {
            return Err(RuntimeError::InvalidArgument("queries deve conter entre uma e três consultas".into()));
        }
        let mut queries = Vec::new();
        for item in list {
            let value = item.as_str().ok_or_else(|| RuntimeError::InvalidArgument("consulta inválida".into()))?.trim();
            if value.is_empty() || value.chars().count() > 600 || value.split_whitespace().count() > 75 {
                return Err(RuntimeError::InvalidArgument("consulta fora dos limites da Brave".into()));
            }
            if !queries.iter().any(|previous| previous == value) { queries.push(value.to_string()); }
        }
        queries
    } else {
        research_queries(&query)
    };
    for (search_index, search_query) in searches.iter().enumerate() {
        if started.elapsed() >= RESEARCH_TOTAL_TIMEOUT {
            break;
        }
        // Brave applies a one-second sliding rate window. Space the bounded
        // documentation queries so later topics are not dropped as HTTP 429.
        if search_index > 0 && (provider == "brave" || brave_configured) {
            std::thread::sleep(Duration::from_millis(1100));
        }
        let remaining = RESEARCH_TOTAL_TIMEOUT.saturating_sub(started.elapsed());
        let search = match search_web_with_timeout(&json!({"query": search_query, "freshness": freshness, "provider": provider}), sources, next_id, remaining.min(RESEARCH_SEARCH_TIMEOUT)) {
            Ok(search) => search,
            Err(error) if searches.len() == 1 => return Err(error),
            Err(error) => {
                attempts.push(json!({"query": search_query, "status": "search-failed", "error": error.to_string()}));
                continue;
            }
        };
        if let Some(source) = search.get("source").and_then(Value::as_str) {
            providers_used.insert(source.to_string());
        }
        freshness_applied |= search.get("freshness_applied").and_then(Value::as_bool).unwrap_or(false);
        let found = search.get("results").and_then(Value::as_array).cloned().unwrap_or_default();
        let mut ranked = found.iter().filter(|result| result_matches_requested_site(search_query, result)).filter_map(|result| {
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
            if let Some(context_text) = result.get("context_text").and_then(Value::as_str)
                .filter(|text| !text.trim().is_empty())
            {
                let title = result.get("title").and_then(Value::as_str).unwrap_or("Fonte Brave");
                let source_id = result.get("source_id").and_then(Value::as_str).unwrap_or("");
                // Discovery context can merge unrelated sections. Verify the
                // original page before using it as the final documentary evidence.
                let remaining = RESEARCH_TOTAL_TIMEOUT.saturating_sub(started.elapsed());
                match open_page_with_timeout(&json!({"url": url, "source_id": source_id}),
                    sources, next_id, remaining.min(RESEARCH_PAGE_TIMEOUT)) {
                    Ok(mut page) if page.get("text").and_then(Value::as_str).is_some_and(|text| !text.trim().is_empty()) => {
                        page["primary_verified"] = json!(true);
                        attempts.push(json!({"url": url, "status": "opened-primary-source"}));
                        pages.push(page);
                        opened_here += 1;
                        continue;
                    }
                    Ok(_) => attempts.push(json!({"url": url, "status": "primary-source-empty"})),
                    Err(error) => attempts.push(json!({"url": url, "status": "primary-source-failed", "error": error.to_string()})),
                }
                if !source_id.is_empty() {
                    if let Some(source) = sources.get_mut(source_id) {
                        source.text = Some(context_text.to_string());
                    }
                }
                attempts.push(json!({"url": url, "status": "used-pre-extracted-context"}));
                pages.push(json!({
                    "source_id": source_id,
                    "url": url,
                    "title": title,
                    "text": context_text,
                    "pre_extracted": true,
                    "primary_verified": false
                }));
                opened_here += 1;
                continue;
            }
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
        if let Some(topic) = topic.or(inferred_documentation_topic) {
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
            "providers_used": providers_used.iter().collect::<Vec<_>>(),
            "category": category,
            "freshness": freshness,
            "freshness_applied": freshness_applied,
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
        "providers_used": providers_used.iter().collect::<Vec<_>>(),
        "category": category,
        "freshness": freshness,
        "freshness_applied": freshness_applied,
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
        "path_info" => path_info(&call.arguments, workspace),
        "find_paths" => find_paths(&call.arguments, workspace),
        "list_tree" => list_tree(&call.arguments, workspace),
        "compare_files" => compare_files(&call.arguments, workspace),
        "git_diff" => run_git_terminal_profile("git_diff", workspace),
        "read_file" => read_file(&call.arguments, workspace),
        "search_files" => search_files(&call.arguments, workspace),
        "inspect_project" => inspect_project(&call.arguments, workspace),
        "diagnose_project" => diagnose_project(&call.arguments),
        "propose_repair" => propose_repair(&call.arguments, workspace),
        "apply_repair" => apply_repair(&call.arguments, workspace),
        "project_checks" => project_checks(&call.arguments, workspace),
        "terminal_run" => terminal_run(&call.arguments, workspace),
        "apply_batch" => apply_batch(&call.arguments, workspace),
        "undo_batch" => undo_batch(&call.arguments, workspace),
        "create_directory" => create_directory(&call.arguments, workspace),
        "create_file" => create_file(&call.arguments, workspace),
        "create_web_page" => create_web_page(&call.arguments, workspace),
        "edit_file" => edit_file(&call.arguments, workspace),
        "inspect_media" => inspect_media(&call.arguments, workspace),
        "extract_document_text" => extract_document_text(&call.arguments, workspace),
        "inspect_code" => inspect_code(&call.arguments, workspace),
        "calculate" | "evaluate_function" => computation_operation(&call.tool, &call.arguments, workspace),
        "list_tools" => Ok(json!({
            "tools": [
                {"name":"calculate","description":"Calcular expressões e agregações com variáveis JSON em motor AST limitado.",
                 "arguments":{"expression":"expressão obrigatória","variables":"objeto JSON opcional"}},
                {"name":"evaluate_function","description":"Interpretar função Python pura local com entradas novas; não executar o módulo.",
                 "arguments":{"path":"arquivo .py relativo","function":"nome","args":"array opcional","kwargs":"objeto opcional"}},
                {
                    "name": "inspect_code",
                    "description": "Inspeciona definições de funções, classes, structs e imports no workspace.",
                    "arguments": {"path": "caminho relativo opcional"}
                },
                {
                    "name": "search_web",
                    "description": "Pesquisa informações atuais na internet.",
                    "arguments": {"query": "string obrigatório", "freshness": "pd (último dia), pw (última semana), pm (último mês) ou py (último ano), opcional"}
                },
                {
                    "name": "open_page",
                    "description": "Abre uma página HTTP(S) e extrai seu texto.",
                    "arguments": {"url": "string obrigatório", "source_id": "string opcional"}
                },
                {
                    "name": "research_web",
                    "description": "Pesquisa, abre fontes selecionadas, extrai conteúdo e opcionalmente grava um lote auditável no corpus.",
                    "arguments": {"query": "string obrigatório", "topic": "tema técnico opcional para validar resultados", "freshness": "pd (último dia), pw (última semana), pm (último mês) ou py (último ano), opcional", "max_results": "1 a 3, padrão 2", "save_to_corpus": "booleano opcional", "category": "string opcional", "output": "caminho relativo opcional"}
                },
                {
                    "name": "list_tools",
                    "description": "Lista as ferramentas disponíveis.",
                    "arguments": {}
                },
                {
                    "name": "apply_batch",
                    "description": "Aplica até 32 mudanças de arquivo como uma unidade aprovada, mostra diff por arquivo e cria um identificador para desfazer sem sobrescrever mudanças posteriores.",
                    "arguments": {"operations": "lista de {tool, arguments}; permitido create_file, edit_file e create_directory"}
                },
                {
                    "name": "undo_batch",
                    "description": "Desfaz um lote local somente se os arquivos ainda tiverem o conteúdo aplicado pelo lote; preserva alterações posteriores e exige aprovação.",
                    "arguments": {"transaction_id": "identificador batch devolvido por apply_batch"}
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
                    "arguments": {"path": "string opcional", "include_hidden": "booleano opcional", "max_entries": "1 a 1000, padrão 200"}
                },
                {
                    "name": "path_info",
                    "description": "Consulta existência, tipo e tamanho de um caminho no workspace.",
                    "arguments": {"path": "caminho relativo obrigatório; vazio consulta a raiz"}
                },
                {
                    "name": "find_paths",
                    "description": "Encontra nomes de arquivos e pastas por padrão com * e ? na árvore local.",
                    "arguments": {"pattern": "padrão obrigatório", "path": "pasta inicial opcional", "include_hidden": "booleano opcional", "max_results": "1 a 200"}
                },
                {
                    "name": "list_tree",
                    "description": "Lista a árvore local com profundidade e número de itens limitados.",
                    "arguments": {"path": "pasta inicial opcional", "max_depth": "1 a 6", "max_entries": "1 a 500", "include_hidden": "booleano opcional"}
                },
                {
                    "name": "compare_files",
                    "description": "Compara dois arquivos textuais locais e aponta a primeira linha diferente.",
                    "arguments": {"left": "primeiro arquivo", "right": "segundo arquivo"}
                },
                {
                    "name": "git_diff",
                    "description": "Mostra o diff Git não preparado, com saída e tempo limitados.",
                    "arguments": {}
                },
                {
                    "name": "read_file",
                    "description": "Lê linhas específicas de um arquivo do workspace; use a posição da busca para abrir somente o trecho relevante.",
                    "arguments": {"path": "string obrigatório", "start_line": "linha inicial inclusiva opcional", "end_line": "linha final inclusiva opcional", "max_bytes": "limite opcional"}
                },
                {
                    "name": "search_files",
                    "description": "Pesquisa texto ou símbolo e devolve ocorrências compactas com caminho e linha.",
                    "arguments": {"query": "string obrigatório", "path": "pasta inicial opcional", "max_results": "máximo opcional entre 1 e 100", "context_lines": "linhas ao redor entre 0 e 5"}
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
                    "description": "Lista ou executa verificações locais limitadas para Python, Node, Rust, Go, C/C++ e shell. Distingue sintaxe, build e testes; zero testes não comprova comportamento.",
                    "arguments": {"check": "auto, all, list, cargo-test, npm-test, npm-check, npm-build, pytest, unittest, node-test, go-test, python-syntax, node-syntax, cpp-syntax, c-syntax ou shell-syntax", "path": "arquivo alterado opcional para escolher a verificação mais relevante"}
                },
                {
                    "name": "terminal_run",
                    "description": "Executa somente perfis aprovados: git status, resumo do diff ou verificação reconhecida do projeto; sem shell e sem executável/argumentos livres.",
                    "arguments": {"operation": "git_status, git_diff_stat ou project_check", "check": "perfil fixo reconhecido pelo catálogo project_checks"}
                },
                {
                    "name": "process_start",
                    "description": "Inicia um servidor do projeto pelo perfil auto-dev, usando um script declarado no package.json ou cargo run; execução exige aprovação.",
                    "arguments": {"profile": "auto-dev"}
                },
                {
                    "name": "process_status",
                    "description": "Consulta estado, prontidão local e saída incremental de um processo iniciado pelo runtime.",
                    "arguments": {"process_id": "identificador opcional; padrão latest", "stdout_cursor": "cursor opcional", "stderr_cursor": "cursor opcional", "wait_ms": "espera opcional até 1500 ms"}
                },
                {
                    "name": "process_stop",
                    "description": "Encerra um processo iniciado pelo runtime e seu grupo de filhos; exige aprovação.",
                    "arguments": {"process_id": "identificador opcional; padrão latest"}
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
                    "description": "Lê documentos, tenta OCR próprio experimental e inspeciona metadados WAV/MP4; não transcreve fala nem interpreta cenas.",
                    "arguments": {"path": "caminho obrigatório"}
                },
                {
                    "name": "extract_document_text",
                    "description": "Extrai texto local de PDF, texto, planilhas, documentos, apresentações, e-books, notebooks e e-mails. Formatos legados e OCR usam utilitários locais opcionais.",
                    "arguments": {"path": "caminho relativo obrigatório", "max_chars": "limite opcional de 1 a 200000 caracteres"}
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

#[cfg(test)]
fn execute_shared(call: ToolCall, state: &SharedState) -> Result<Value, RuntimeError> {
    execute_shared_with_processes(call, state, None, None)
}

fn execute_shared_with_processes(
    mut call: ToolCall,
    state: &SharedState,
    processes: Option<&SharedProcesses>,
    activity_log: Option<&SharedActivity>,
) -> Result<Value, RuntimeError> {
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
    if matches!(call.tool.as_str(), "process_start" | "process_status" | "process_stop") {
        let root = fs::canonicalize(&*workspace).map_err(|error| RuntimeError::Workspace(error.to_string()))?;
        drop(state);
        let processes = processes.ok_or_else(|| RuntimeError::InvalidArgument("controle de processos disponível somente no runtime web".into()))?;
        let mut supervisor = processes.lock().map_err(|_| RuntimeError::Workspace("gerenciador de processos indisponível".into()))?;
        return match call.tool.as_str() {
            "process_start" => {
                let object = call.arguments.as_object().ok_or_else(|| RuntimeError::InvalidArgument("process_start exige um objeto".into()))?;
                if object.len() != 1 || !object.contains_key("profile") {
                    return Err(RuntimeError::InvalidArgument("process_start aceita somente profile".into()));
                }
                let profile = required_string(&call.arguments, "profile")?;
                supervisor.start(&profile, &root, activity_log.cloned())
            }
            "process_status" => supervisor.status(&call.arguments, &root),
            "process_stop" => supervisor.stop(&call.arguments, &root),
            _ => unreachable!(),
        };
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

fn publish_agent_event_json(activity_log: &SharedActivity, body: &str) -> Result<(), String> {
    let value: Value = serde_json::from_str(body).map_err(|error| format!("JSON inválido: {error}"))?;
    let operation = value.get("operation").and_then(Value::as_str).unwrap_or("");
    if !operation.starts_with("agent-core-")
        || operation.len() > 120
        || !operation.chars().all(|character| character.is_ascii_alphanumeric() || matches!(character, '-' | '_' | '.')) {
        return Err("Identificador de execução inválido.".into());
    }
    let phase = value.get("phase").and_then(Value::as_str).unwrap_or("");
    let status = value.get("status").and_then(Value::as_str).unwrap_or("");
    let source_message = value.get("message").and_then(Value::as_str).unwrap_or("").trim();
    if source_message.is_empty() || source_message.len() > 4096 {
        return Err("Mensagem do evento inválida.".into());
    }
    let runtime_phase = match (phase, status) {
        ("observe", _) => "processing",
        ("learn", _) => "learning",
        ("plan", _) => "planning",
        ("act", _) => "tool",
        ("verify", _) => "verifying",
        ("complete", "blocked") => "blocked",
        ("complete", "completed") => "done",
        ("complete", _) => "processing",
        _ => return Err("Fase do evento desconhecida.".into()),
    };
    if !matches!(status, "running" | "completed" | "blocked") {
        return Err("Estado do evento desconhecido.".into());
    }
    let message = if source_message.starts_with("__ANSWER_DELTA__") {
        source_message.to_string()
    } else {
        source_message.chars().take(600).collect::<String>()
    };
    let done = matches!(status, "completed" | "blocked");
    activity(activity_log, operation, runtime_phase, &message, done, None);
    Ok(())
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

fn cognition_proxy_route(method: &Method, path: &str) -> Option<(&'static str, u64)> {
    match (method, path) {
        (Method::Get, "/api/v1/cognition/cores") => Some(("/v1/cognition/cores", 10)),
        (Method::Post, "/api/v1/cognition/cores/decide") => Some(("/v1/cognition/cores/decide", 30)),
        (Method::Get, "/api/v1/models/experimental") => Some(("/v1/models/experimental", 10)),
        (Method::Post, "/api/v1/models/experimental/decide") => Some(("/v1/models/experimental/decide", 30)),
        _ => None,
    }
}

fn read_cognition_proxy_body(reader: &mut dyn Read) -> Result<String, (u16, String)> {
    let mut body = Vec::new();
    reader.take(64 * 1024 + 1).read_to_end(&mut body)
        .map_err(|error| (400, error.to_string()))?;
    if body.len() > 64 * 1024 {
        return Err((413, "pedido acima de 64 KiB".into()));
    }
    String::from_utf8(body).map_err(|error| (400, error.to_string()))
}

fn model_worker_ready(health: &Value) -> bool {
    health["ok"] == true && health["free_generation"] == true
        && health.get("error").is_some_and(Value::is_null)
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
            "/api/agent-events": {"post": {"summary": "Publica progresso estruturado do AgentCore no feed local", "requestBody": {"required": true}, "responses": {"200": {"description": "Evento registrado"}, "400": {"description": "Evento inválido"}}}},
            "/api/v1/capabilities": {"get": {"summary": "Catálogo versionado de APIs locais e políticas de rede", "responses": {"200": {"description": "Contrato local-api-catalog/v1"}, "503": {"description": "Worker local indisponível"}}}},
            "/api/v1/attachments/capabilities": {"get": {"summary": "Leitores e modelos de mídia efetivamente disponíveis", "responses": {"200": {"description": "Contrato local-media-capabilities/v1"}}}},
            "/api/v1/attachments/ingest": {"post": {"summary": "Recebe um arquivo de até 16 MiB e registra evidências locais", "requestBody": {"required": true, "content": {"application/json": {"schema": {"type": "object", "required": ["schema", "name", "data_base64"], "properties": {"schema": {"const": "local-attachment-upload/v1"}, "name": {"type": "string", "maxLength": 180}, "data_base64": {"type": "string", "contentEncoding": "base64"}}}}}}, "responses": {"200": {"description": "Recibo local-attachment/v1 com SHA-256 e dados observados"}, "400": {"description": "Arquivo ou conteúdo inválido"}, "413": {"description": "Corpo acima do limite"}}}},
            "/api/v1/attachments/{asset_id}/evidence": {"get": {"summary": "Recupera a observação canônica persistida de um anexo", "parameters": [{"name": "asset_id", "in": "path", "required": true, "schema": {"type": "string", "pattern": "^[a-f0-9]{64}$"}}], "responses": {"200": {"description": "Observação recebida pelo agente"}, "404": {"description": "Recibo ausente"}}}},
            "/api/v1/dialogue/providers": {"get": {"summary": "Estado dos provedores de diálogo sem expor credenciais", "responses": {"200": {"description": "Contrato agent-dialogue-providers/v1"}, "503": {"description": "Worker local indisponível"}}}},
            "/api/v1/dialogue/turn": {"post": {"summary": "Gera uma resposta contextual pelo contrato agent-dialogue-request/v1", "requestBody": {"required": true}, "responses": {"200": {"description": "Resposta agent-dialogue-response/v1 aprovada pelo gate de qualidade"}, "400": {"description": "Pedido inválido"}, "422": {"description": "Provedores rejeitados ou modo inválido"}, "503": {"description": "Provedor não configurado ou indisponível"}}}},
            "/api/v1/skills/route": {"post": {"summary": "Resolve skills por regras locais e devolve o próximo passo validado pelos contratos; não executa ferramentas", "requestBody": {"required": true}, "responses": {"200": {"description": "Plano determinístico skill-route/v2"}, "400": {"description": "Tarefa ou contexto inválido"}}}},
            "/api/v1/agent/capabilities": {"get": {"summary": "Capacidades verificadas no registro local, sem inferir domínio geral", "responses": {"200": {"description": "Contrato agent-capabilities/v1"}, "503": {"description": "Worker local indisponível"}}}},
            "/api/v1/cognition/cores": {"get": {"summary": "Provas, estados e dependências dos núcleos para o checkpoint carregado", "responses": {"200": {"description": "Contrato cognitive-core-status/v1"}, "503": {"description": "Worker local indisponível"}}}},
            "/api/v1/cognition/cores/decide": {"post": {"summary": "Propõe uma decisão somente após comprovar todos os núcleos exigidos; não executa ferramentas", "requestBody": {"required": true, "description": "Até 64 KiB", "content": {"application/json": {"schema": {"type": "object", "required": ["schema", "cores", "scope", "messages", "cognition"], "properties": {"schema": {"const": "cognitive-core-request/v1"}, "cores": {"type": "array", "minItems": 1, "uniqueItems": true, "items": {"type": "string"}}, "scope": {"type": "string"}, "messages": {"type": "array"}, "cognition": {"type": "object"}}}}}}, "responses": {"200": {"description": "Proposta cognitive-core-response/v1"}, "400": {"description": "Pedido inválido"}, "413": {"description": "Corpo acima de 64 KiB"}, "422": {"description": "Competência não comprovada, entrada fora do escopo ou proposta inválida"}, "503": {"description": "Worker local indisponível"}}}},
            "/api/v1/models/experimental": {"get": {"summary": "Disponibilidade e limites do candidato experimental isolado do chat", "responses": {"200": {"description": "Contrato experimental-model-status/v1"}, "503": {"description": "Worker local indisponível"}}}},
            "/api/v1/models/experimental/decide": {"post": {"summary": "Consulta o candidato experimental no escopo sintético; a proposta não comprova competência nem executa ferramentas", "requestBody": {"required": true, "description": "Até 64 KiB", "content": {"application/json": {"schema": {"type": "object", "required": ["schema", "messages", "cognition"], "properties": {"schema": {"const": "experimental-cognitive-request/v1"}, "messages": {"type": "array"}, "cognition": {"type": "object"}}}}}}, "responses": {"200": {"description": "Proposta experimental-cognitive-response/v1, experimental=true, qualified=false, tool_executed=false"}, "400": {"description": "Pedido inválido"}, "413": {"description": "Corpo acima de 64 KiB"}, "422": {"description": "Entrada fora do escopo ou proposta inválida"}, "503": {"description": "Worker ou candidato experimental indisponível"}}}},
            "/api/v1/learning/autonomous": {"get": {"summary": "Próxima trilha, histórico e auditoria do professor autônomo", "responses": {"200": {"description": "Contrato autonomous-learning-status/v1"}, "503": {"description": "Worker local indisponível"}}}},
            "/api/v1/learning/autonomous/tick": {"post": {"summary": "Observa ou inicia um ciclo limitado de aprendizado autônomo", "responses": {"200": {"description": "Estado do ciclo"}, "503": {"description": "Worker local indisponível"}}}},
            "/api/workflow-review": {"get": {"summary": "Lista trajetórias do planejador para curadoria humana", "responses": {"200": {"description": "Candidatos, estado da revisão e contagens"}, "503": {"description": "Worker local indisponível"}}}},
            "/api/workflow-review/decision": {"post": {"summary": "Registra decisão humana de aprovação ou rejeição de uma trajetória", "requestBody": {"required": true}, "responses": {"200": {"description": "Decisão auditada"}, "400": {"description": "Decisão inválida"}}}},
            "/api/v1/knowledge/search": {"post": {"summary": "Busca evidência no acervo local", "requestBody": {"required": true}, "responses": {"200": {"description": "Contrato agent-evidence/v1"}, "400": {"description": "Consulta inválida"}}}},
            "/api/v1/agent/context": {"post": {"summary": "Monta contexto local auditável com proveniência para uma rodada do agente", "requestBody": {"required": true}, "responses": {"200": {"description": "Contrato agent-context/v2; pedidos legados recebem v1"}, "400": {"description": "Contexto inválido"}}}},
            "/api/v1/agent/understand": {"post": {"summary": "Interpreta um pedido e apresenta premissas, perguntas e capacidades sem alterar o workspace", "requestBody": {"required": true}, "responses": {"200": {"description": "Contrato agent-understanding/v1"}, "400": {"description": "Pedido inválido"}, "503": {"description": "AgentCore indisponível"}}}},
            "/api/v1/workspace/entries": {"post": {"summary": "Gerencia arquivos pela interface: criar, salvar, mover para lixeira e restaurar", "requestBody": {"required": true, "content": {"application/json": {"schema": {"type":"object", "required":["schema","workspace_root","operation"], "properties":{"schema":{"const":"workspace-entry/v1"},"workspace_root":{"type":"string"},"operation":{"enum":["create_file","create_directory","edit_file","trash","restore","list_trash"]},"path":{"type":"string"},"content":{"type":"string"},"old_text":{"type":"string"},"new_text":{"type":"string"},"trash_id":{"type":"string"},"request_id":{"type":"string"}}}}}}, "responses":{"200":{"description":"Operação confirmada no workspace esperado"},"400":{"description":"Pedido inválido, caminho em conflito ou projeto alterado"}}}},
            "/api/v1/agent/pursue": {"post": {"summary": "Executa o ciclo AgentCore TypeScript com aprovação explícita", "requestBody": {"required": true}, "responses": {"200": {"description": "Contrato agent-report/v2 com resumo cognitivo"}, "400": {"description": "Pedido inválido"}, "503": {"description": "AgentCore indisponível"}}}},
            "/api/v1/agent/tasks": {"get": {"summary": "Lista tarefas persistidas do AgentCore; aceita operation_id", "responses": {"200": {"description": "Índices das tarefas"}, "503": {"description": "AgentCore indisponível"}}}},
            "/api/v1/agent/tasks/{task_id}": {"get": {"summary": "Consulta eventos e relatório persistidos de uma tarefa", "responses": {"200": {"description": "Estado e evidências da tarefa"}, "404": {"description": "Tarefa não encontrada"}}}},
            "/api/v1/agent/tasks/{task_id}/resume": {"post": {"summary": "Retoma uma tarefa com esclarecimento, aprovação ou rejeição tipada", "requestBody": {"required": true}, "responses": {"200": {"description": "Contrato agent-resume-response/v1"}, "400": {"description": "Retomada inválida"}, "404": {"description": "Pendência não encontrada"}, "409": {"description": "Ação incompatível ou agente ocupado"}}}},
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
            "available_checks": checks,
            "check_locations": snapshot.get("check_locations").cloned().unwrap_or_else(|| json!({}))
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
    let result = call_model(&ChatRequest { messages, request_id: None, workspace_selected: false })?;
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

fn handle_web_request(mut request: tiny_http::Request, state: SharedState, activity_log: SharedActivity, processes: SharedProcesses) {
    let host = request.headers().iter().find(|h| h.field.equiv("Host")).map(|h| h.value.as_str()).unwrap_or("");
    let origin = request.headers().iter().find(|h| h.field.equiv("Origin")).map(|h| h.value.as_str());
    if !allowed_local_request(host, origin) {
        let _ = request.respond(Response::from_string(json!({"ok":false,"error":"origem não autorizada"}).to_string()).with_status_code(StatusCode(403)));
        return;
    }
    if request.method() == &Method::Post && request.url() == "/api/v1/workspace/entries" {
        let mut body = String::new();
        let started = Instant::now();
        let result = request.as_reader().take(2 * 1024 * 1024 + 1).read_to_string(&mut body)
            .map_err(|error| RuntimeError::InvalidArgument(error.to_string()))
            .and_then(|_| {
                if body.len() > 2 * 1024 * 1024 { return Err(RuntimeError::InvalidArgument("pedido de arquivo acima do limite".into())); }
                let args: Value = serde_json::from_str(&body).map_err(|error| RuntimeError::InvalidArgument(error.to_string()))?;
                let id = args.get("request_id").and_then(Value::as_str).unwrap_or("local");
                if id.len() > 160 || !id.bytes().all(|value| value.is_ascii_alphanumeric() || b"._:-".contains(&value)) {
                    return Err(RuntimeError::InvalidArgument("identificador de operação inválido".into()));
                }
                let operation = format!("workspace-entry:{id}");
                activity(&activity_log, &operation, "running", "Atualizando arquivos do workspace.", false, None);
                let result = workspace_entry_action(&args, &state);
                activity(&activity_log, &operation, if result.is_ok() { "done" } else { "error" },
                    if result.is_ok() { "Arquivo ou pasta atualizado." } else { "Alteração não confirmada." }, true, Some(started.elapsed().as_millis()));
                result
            });
        let response = match result {
            Ok(data) => Response::from_string(json!({"ok":true,"data":data,"elapsed_ms":started.elapsed().as_millis()}).to_string()),
            Err(error) => Response::from_string(json!({"ok":false,"error":error.to_string()}).to_string()).with_status_code(StatusCode(400)),
        };
        let _ = request.respond(response.with_header(Header::from_bytes("Content-Type", "application/json; charset=utf-8").unwrap())
            .with_header(Header::from_bytes("Cache-Control", "no-store").unwrap()));
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

    if request.method() == &Method::Post && request.url() == "/api/agent-events" {
        let mut body = String::new();
        let response = match request.as_reader().take(16 * 1024 + 1).read_to_string(&mut body) {
            Ok(_) if body.len() > 16 * 1024 => Response::from_string(json!({"ok":false,"error":"evento acima de 16 KiB"}).to_string()).with_status_code(StatusCode(413)),
            Ok(_) => match publish_agent_event_json(&activity_log, &body) {
                Ok(()) => Response::from_string(json!({"ok":true}).to_string()),
                Err(error) => Response::from_string(json!({"ok":false,"error":error}).to_string()).with_status_code(StatusCode(400)),
            },
            Err(error) => Response::from_string(json!({"ok":false,"error":error.to_string()}).to_string()).with_status_code(StatusCode(400)),
        };
        let _ = request.respond(response.with_header(Header::from_bytes("Content-Type", "application/json; charset=utf-8").unwrap()).with_header(Header::from_bytes("Cache-Control", "no-store").unwrap()));
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
    if request.method() == &Method::Get && (request.url() == "/api/workflow-review" || request.url().starts_with("/api/workflow-review?")) {
        let response = (|| -> Result<_, String> {
            let query = request.url().split_once('?').map(|(_, query)| query).unwrap_or("");
            let params: Vec<(String, String)> = url::form_urlencoded::parse(query.as_bytes())
                .map(|(key, value)| (key.into_owned(), value.into_owned())).collect();
            if params.iter().any(|(key, _)| !["offset", "limit", "status"].contains(&key.as_str())) {
                return Err("parâmetro de revisão desconhecido".into());
            }
            let value = |key: &str, default: &str| params.iter().find(|(name, _)| name == key).map(|(_, value)| value.clone()).unwrap_or_else(|| default.to_string());
            let offset = value("offset", "0").parse::<usize>().map_err(|_| "offset inválido")?;
            let limit = value("limit", "1").parse::<usize>().map_err(|_| "limite inválido")?;
            let status = value("status", "pending");
            if offset > 100_000 || !(1..=10).contains(&limit) || !["pending", "approved", "rejected", "all"].contains(&status.as_str()) {
                return Err("parâmetros de revisão fora dos limites".into());
            }
            let endpoint = format!("http://127.0.0.1:3101/v1/workflow/candidates?offset={offset}&limit={limit}&status={status}");
            let upstream = reqwest::blocking::Client::builder().timeout(Duration::from_secs(5)).build()
                .map_err(|error| error.to_string())?.get(endpoint).send().map_err(|error| error.to_string())?;
            let code = upstream.status().as_u16();
            let body = upstream.text().map_err(|error| error.to_string())?;
            Ok(Response::from_string(body).with_status_code(StatusCode(code)))
        })().unwrap_or_else(|error| Response::from_string(json!({"ok":false,"error":error}).to_string()).with_status_code(StatusCode(400)));
        let _ = request.respond(response.with_header(Header::from_bytes("Content-Type", "application/json; charset=utf-8").unwrap()).with_header(Header::from_bytes("Cache-Control", "no-store").unwrap()));
        return;
    }
    if request.method() == &Method::Get && request.url() == "/api/v1/capabilities" {
        let response = match reqwest::blocking::Client::builder().timeout(Duration::from_secs(5)).build()
            .and_then(|client| client.get("http://127.0.0.1:3101/v1/capabilities").send()) {
            Ok(upstream) => {
                let status = upstream.status().as_u16();
                let body = upstream.text().unwrap_or_else(|error| json!({"ok":false,"error":error.to_string()}).to_string());
                Response::from_string(body).with_status_code(StatusCode(status))
            }
            Err(error) => Response::from_string(json!({"ok":false,"error":format!("catálogo local indisponível: {error}")}).to_string()).with_status_code(StatusCode(503)),
        };
        let _ = request.respond(response.with_header(Header::from_bytes("Content-Type", "application/json; charset=utf-8").unwrap()).with_header(Header::from_bytes("Cache-Control", "no-store").unwrap()));
        return;
    }

    if let Some((path, timeout_seconds)) = cognition_proxy_route(request.method(), request.url()) {
        let response = (|| -> Result<_, (u16, String)> {
            let is_post = request.method() == &Method::Post;
            let body = if is_post { read_cognition_proxy_body(request.as_reader())? } else { String::new() };
            let client = reqwest::blocking::Client::builder().timeout(Duration::from_secs(timeout_seconds)).build()
                .map_err(|error| (503, error.to_string()))?;
            let endpoint = format!("http://127.0.0.1:3101{path}");
            let call = if is_post {
                client.post(endpoint).header("content-type", "application/json").body(body)
            } else { client.get(endpoint) };
            let upstream = call.send().map_err(|error| (503, format!("worker local indisponível: {error}")))?;
            let status = upstream.status().as_u16();
            let body = upstream.text().map_err(|error| (503, error.to_string()))?;
            Ok(Response::from_string(body).with_status_code(StatusCode(status)))
        })().unwrap_or_else(|(status, error)| Response::from_string(json!({"ok":false,"error":error}).to_string()).with_status_code(StatusCode(status)));
        let _ = request.respond(response
            .with_header(Header::from_bytes("Content-Type", "application/json; charset=utf-8").unwrap())
            .with_header(Header::from_bytes("Cache-Control", "no-store").unwrap()));
        return;
    }

    if request.method() == &Method::Get && request.url() == "/api/v1/dialogue/providers" {
        let response = match reqwest::blocking::Client::builder().timeout(Duration::from_secs(5)).build()
            .and_then(|client| client.get("http://127.0.0.1:3101/v1/dialogue/providers").send()) {
            Ok(upstream) => {
                let status = upstream.status().as_u16();
                let body = upstream.text().unwrap_or_else(|error| json!({"ok":false,"error":error.to_string()}).to_string());
                Response::from_string(body).with_status_code(StatusCode(status))
            }
            Err(error) => Response::from_string(json!({"ok":false,"error":format!("worker local indisponível: {error}")}).to_string()).with_status_code(StatusCode(503)),
        };
        let _ = request.respond(response.with_header(Header::from_bytes("Content-Type", "application/json; charset=utf-8").unwrap()).with_header(Header::from_bytes("Cache-Control", "no-store").unwrap()));
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
    if request.method() == &Method::Get && request.url() == "/api/v1/learning/autonomous" {
        let response = match reqwest::blocking::Client::builder().timeout(Duration::from_secs(5)).build()
            .and_then(|client| client.get("http://127.0.0.1:3101/v1/learning/autonomous").send()) {
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
    if request.method() == &Method::Post && request.url() == "/api/v1/learning/autonomous/tick" {
        let response = match reqwest::blocking::Client::builder().timeout(Duration::from_secs(10)).build()
            .and_then(|client| client.post("http://127.0.0.1:3101/v1/learning/autonomous/tick").send()) {
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
    if request.method() == &Method::Post && request.url() == "/api/workflow-review/decision" {
        let mut body = String::new();
        let response = match request.as_reader().take(16 * 1024 + 1).read_to_string(&mut body) {
            Ok(_) if body.len() > 16 * 1024 => Response::from_string(json!({"ok":false,"error":"decisão acima de 16 KiB"}).to_string()).with_status_code(StatusCode(413)),
            Ok(_) => reqwest::blocking::Client::builder().timeout(Duration::from_secs(8)).build()
                .and_then(|client| client.post("http://127.0.0.1:3101/v1/workflow/review")
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
    if request.method() == &Method::Post && request.url() == "/api/v1/dialogue/turn" {
        let mut body = String::new();
        let response = match request.as_reader().take(2 * 1024 * 1024 + 1).read_to_string(&mut body) {
            Ok(_) if body.len() > 2 * 1024 * 1024 => Response::from_string(json!({"ok":false,"error":"pedido de diálogo acima de 2 MiB"}).to_string()).with_status_code(StatusCode(413)),
            Ok(_) => reqwest::blocking::Client::builder().timeout(Duration::from_secs(300)).build()
                .and_then(|client| client.post("http://127.0.0.1:3101/v1/dialogue/turn")
                    .header("content-type", "application/json").body(body).send())
                .map(|upstream| {
                    let status = upstream.status().as_u16();
                    let body = upstream.text().unwrap_or_else(|error| json!({"ok":false,"error":error.to_string()}).to_string());
                    Response::from_string(body).with_status_code(StatusCode(status))
                })
                .unwrap_or_else(|error| Response::from_string(json!({"ok":false,"error":format!("API de diálogo indisponível: {error}")}).to_string()).with_status_code(StatusCode(503))),
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
    if request.method() == &Method::Post && request.url() == "/api/v1/skills/route" {
        let mut body = String::new();
        let response = match request.as_reader().take(64 * 1024 + 1).read_to_string(&mut body) {
            Ok(_) if body.len() > 64 * 1024 => Response::from_string(json!({"ok":false,"error":"pedido de roteamento acima de 64 KiB"}).to_string()).with_status_code(StatusCode(413)),
            Ok(_) => reqwest::blocking::Client::builder().timeout(Duration::from_secs(5)).build()
                .and_then(|client| client.post("http://127.0.0.1:3101/v1/skills/route")
                    .header("content-type", "application/json")
                    .body(body)
                    .send())
                .map(|upstream| {
                    let status = upstream.status().as_u16();
                    let body = upstream.text().unwrap_or_else(|error| json!({"ok":false,"error":error.to_string()}).to_string());
                    Response::from_string(body).with_status_code(StatusCode(status))
                })
                .unwrap_or_else(|error| Response::from_string(json!({"ok":false,"error":format!("roteador de skills indisponível: {error}")}).to_string()).with_status_code(StatusCode(503))),
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
    if request.method() == &Method::Get && (request.url() == "/api/v1/agent/tasks"
        || request.url().starts_with("/api/v1/agent/tasks?")
        || request.url().starts_with("/api/v1/agent/tasks/")) {
        let endpoint = format!("http://127.0.0.1:3200{}", request.url().trim_start_matches("/api/v1/agent"));
        let response = reqwest::blocking::Client::builder().timeout(Duration::from_secs(10)).build()
            .and_then(|client| client.get(endpoint).send())
            .map(|upstream| {
                let status = upstream.status().as_u16();
                let body = upstream.text().unwrap_or_else(|error| json!({"ok":false,"error":error.to_string()}).to_string());
                Response::from_string(body).with_status_code(StatusCode(status))
            })
            .unwrap_or_else(|error| Response::from_string(json!({"ok":false,"error":format!("AgentCore TypeScript indisponível: {error}")}).to_string()).with_status_code(StatusCode(503)));
        let _ = request.respond(response.with_header(Header::from_bytes("Content-Type", "application/json; charset=utf-8").unwrap()).with_header(Header::from_bytes("Cache-Control", "no-store").unwrap()));
        return;
    }
    if request.method() == &Method::Post && request.url() == "/api/v1/agent/understand" {
        let mut body = String::new();
        let response = match request.as_reader().take(1024 * 1024 + 1).read_to_string(&mut body) {
            Ok(_) if body.len() > 1024 * 1024 => Response::from_string(json!({"ok":false,"error":"pedido do agente acima de 1 MiB"}).to_string()).with_status_code(StatusCode(413)),
            Ok(_) => reqwest::blocking::Client::builder().timeout(Duration::from_secs(45)).build()
                .and_then(|client| client.post("http://127.0.0.1:3200/understand")
                    .header("content-type", "application/json").body(body).send())
                .map(|upstream| {
                    let status = upstream.status().as_u16();
                    let body = upstream.text().unwrap_or_else(|error| json!({"ok":false,"error":error.to_string()}).to_string());
                    Response::from_string(body).with_status_code(StatusCode(status))
                })
                .unwrap_or_else(|error| Response::from_string(json!({"ok":false,"error":format!("AgentCore TypeScript indisponível: {error}")}).to_string()).with_status_code(StatusCode(503))),
            Err(error) => Response::from_string(json!({"ok":false,"error":error.to_string()}).to_string()).with_status_code(StatusCode(400)),
        };
        let _ = request.respond(response.with_header(Header::from_bytes("Content-Type", "application/json; charset=utf-8").unwrap()).with_header(Header::from_bytes("Cache-Control", "no-store").unwrap()));
        return;
    }
    if request.method() == &Method::Post && request.url().starts_with("/api/v1/agent/tasks/")
        && request.url().ends_with("/resume") {
        let task_id = request.url().trim_start_matches("/api/v1/agent/tasks/").trim_end_matches("/resume").trim_end_matches('/').to_string();
        if task_id.is_empty() || task_id.len() > 100 || !task_id.bytes().all(|byte| byte.is_ascii_alphanumeric() || byte == b'_' || byte == b'-') {
            let _ = request.respond(Response::from_string(json!({"ok":false,"error":"Identificador de tarefa inválido"}).to_string())
                .with_status_code(StatusCode(400)).with_header(Header::from_bytes("Content-Type", "application/json; charset=utf-8").unwrap()));
            return;
        }
        let mut body = String::new();
        let response = match request.as_reader().take(1024 * 1024 + 1).read_to_string(&mut body) {
            Ok(_) if body.len() > 1024 * 1024 => Response::from_string(json!({"ok":false,"error":"retomada acima de 1 MiB"}).to_string()).with_status_code(StatusCode(413)),
            Ok(_) => reqwest::blocking::Client::builder().timeout(Duration::from_secs(900)).build()
                .and_then(|client| client.post(format!("http://127.0.0.1:3200/tasks/{task_id}/resume"))
                    .header("content-type", "application/json").body(body).send())
                .map(|upstream| {
                    let status = upstream.status().as_u16();
                    let body = upstream.text().unwrap_or_else(|error| json!({"ok":false,"error":error.to_string()}).to_string());
                    Response::from_string(body).with_status_code(StatusCode(status))
                })
                .unwrap_or_else(|error| Response::from_string(json!({"ok":false,"error":format!("AgentCore TypeScript indisponível: {error}")}).to_string()).with_status_code(StatusCode(503))),
            Err(error) => Response::from_string(json!({"ok":false,"error":error.to_string()}).to_string()).with_status_code(StatusCode(400)),
        };
        let _ = request.respond(response.with_header(Header::from_bytes("Content-Type", "application/json; charset=utf-8").unwrap()).with_header(Header::from_bytes("Cache-Control", "no-store").unwrap()));
        return;
    }
    if request.method() == &Method::Post && request.url() == "/api/v1/agent/pursue" {
        let mut body = String::new();
        let response = match request.as_reader().take(1024 * 1024 + 1).read_to_string(&mut body) {
            Ok(_) if body.len() > 1024 * 1024 => Response::from_string(json!({"ok":false,"error":"pedido do agente acima de 1 MiB"}).to_string()).with_status_code(StatusCode(413)),
            Ok(_) => reqwest::blocking::Client::builder().timeout(Duration::from_secs(900)).build()
                .and_then(|client| client.post("http://127.0.0.1:3200/pursue")
                    .header("content-type", "application/json")
                    .body(body)
                    .send())
                .map(|upstream| {
                    let status = upstream.status().as_u16();
                    let body = upstream.text().unwrap_or_else(|error| json!({"ok":false,"error":error.to_string()}).to_string());
                    Response::from_string(body).with_status_code(StatusCode(status))
                })
                .unwrap_or_else(|error| Response::from_string(json!({"ok":false,"error":format!("AgentCore TypeScript indisponível: {error}")}).to_string()).with_status_code(StatusCode(503))),
            Err(error) => Response::from_string(json!({"ok":false,"error":error.to_string()}).to_string()).with_status_code(StatusCode(400)),
        };
        let _ = request.respond(response.with_header(Header::from_bytes("Content-Type", "application/json; charset=utf-8").unwrap()).with_header(Header::from_bytes("Cache-Control", "no-store").unwrap()));
        return;
    }
    if (request.method() == &Method::Post && request.url() == "/api/v1/attachments/ingest")
        || (request.method() == &Method::Get && (request.url() == "/api/v1/attachments/capabilities"
            || (request.url().starts_with("/api/v1/attachments/") && request.url().ends_with("/evidence")))) {
        let maximum = 24 * 1024 * 1024;
        let mut body = String::new();
        let response = match request.as_reader().take(maximum + 1).read_to_string(&mut body) {
            Ok(_) if body.len() as u64 > maximum => Response::from_string(json!({"ok":false,"error":"Anexo acima do limite de 16 MiB."}).to_string()).with_status_code(StatusCode(413)),
            Ok(_) => reqwest::blocking::Client::builder().timeout(Duration::from_secs(90)).build()
                .and_then(|client| {
                    let endpoint = format!("http://127.0.0.1:3101{}", request.url().trim_start_matches("/api"));
                    if request.method() == &Method::Get { client.get(endpoint).send() }
                    else { client.post(endpoint).header("content-type", "application/json").body(body).send() }
                })
                .map(|upstream| {
                    let status = upstream.status().as_u16();
                    Response::from_string(upstream.text().unwrap_or_else(|error| json!({"ok":false,"error":error.to_string()}).to_string()))
                        .with_status_code(StatusCode(status))
                })
                .unwrap_or_else(|error| Response::from_string(json!({"ok":false,"error":format!("Leitor local indisponível: {error}")}).to_string()).with_status_code(StatusCode(503))),
            Err(error) => Response::from_string(json!({"ok":false,"error":error.to_string()}).to_string()).with_status_code(StatusCode(400)),
        };
        let _ = request.respond(response.with_header(Header::from_bytes("Content-Type", "application/json; charset=utf-8").unwrap())
            .with_header(Header::from_bytes("Cache-Control", "no-store").unwrap()));
        return;
    }
    if request.method() == &Method::Post && request.url() == "/api/chat/stream" {
        let mut body = String::new();
        let read_result = request.as_reader().take(4 * 1024 * 1024 + 1).read_to_string(&mut body);
        if let Err(error) = read_result {
            let response = Response::from_string(json!({"ok":false,"error":format!("falha ao ler requisição: {error}")}).to_string())
                .with_status_code(StatusCode(400));
            let _ = request.respond(response);
            return;
        }
        if body.len() > 4 * 1024 * 1024 {
            let response = Response::from_string(json!({"ok":false,"error":"requisição acima de 4 MiB"}).to_string())
                .with_status_code(StatusCode(413));
            let _ = request.respond(response);
            return;
        }
        let chat = match serde_json::from_str::<ChatRequest>(&body) {
            Ok(chat) => chat,
            Err(error) => {
                let response = Response::from_string(json!({"ok":false,"error":format!("JSON inválido: {error}")}).to_string())
                    .with_status_code(StatusCode(400));
                let _ = request.respond(response);
                return;
            }
        };
        if let Err(error) = validate_chat(&chat) {
            let response = Response::from_string(json!({"ok":false,"error":error.to_string()}).to_string())
                .with_status_code(StatusCode(400));
            let _ = request.respond(response);
            return;
        }

        let started = Instant::now();
        let operation = format!("chat:{}", chat.request_id.clone().unwrap_or_else(|| "local".into()));
        activity(&activity_log, &operation, "received", "Requisição recebida; preparando o contexto.", false, None);
        activity(&activity_log, &operation, "processing", "Examinando a pergunta e o contexto recebido.", false, None);
        activity(&activity_log, &operation, "model", "Gerando a resposta em tempo real.", false, None);
        let upstream = reqwest::blocking::Client::builder().timeout(Duration::from_secs(900)).build()
            .and_then(|client| client.post("http://127.0.0.1:3101/generate/stream")
                .json(&json!({
                "messages": chat.messages,
                "request_id": chat.request_id,
                "workspace_selected": chat.workspace_selected,
                "max_tokens": DEFAULT_CHAT_GENERATION_TOKENS,
                }))
                .send());
        match upstream {
            Ok(upstream) if upstream.status().is_success() => {
                let headers = vec![
                    Header::from_bytes("Content-Type", "text/event-stream; charset=utf-8").unwrap(),
                    Header::from_bytes("Cache-Control", "no-cache, no-store").unwrap(),
                    Header::from_bytes("X-Accel-Buffering", "no").unwrap(),
                ];
                let response = Response::new(
                    StatusCode(200), headers, Box::new(upstream) as Box<dyn Read + Send>, None, None,
                );
                let result = request.respond(response);
                let elapsed = started.elapsed().as_millis();
                if let Err(error) = result {
                    activity(&activity_log, &operation, "error", &format!("O cliente encerrou o fluxo: {error}"), true, Some(elapsed));
                } else {
                    activity(&activity_log, &operation, "done", "Resposta pronta.", true, Some(elapsed));
                }
            }
            Ok(upstream) => {
                let status = upstream.status().as_u16();
                let detail = upstream.text().unwrap_or_else(|error| error.to_string());
                let response = Response::from_string(detail).with_status_code(StatusCode(status));
                let _ = request.respond(response);
                activity(&activity_log, &operation, "error", "O worker recusou o fluxo de resposta.", true, Some(started.elapsed().as_millis()));
            }
            Err(error) => {
                let response = Response::from_string(json!({"ok":false,"error":format!("worker local indisponível: {error}")}).to_string())
                    .with_status_code(StatusCode(503));
                let _ = request.respond(response);
                activity(&activity_log, &operation, "error", "O worker local não iniciou o fluxo de resposta.", true, Some(started.elapsed().as_millis()));
            }
        }
        return;
    }
    if request.method() == &Method::Post && request.url() == "/api/chat" {
        let mut body = String::new();
        let response = match request.as_reader().take(4 * 1024 * 1024 + 1).read_to_string(&mut body) {
            Ok(_) if body.len() > 4 * 1024 * 1024 => Response::from_string(json!({"ok":false,"error":"requisição acima de 4 MiB"}).to_string()).with_status_code(StatusCode(413)),
            Ok(_) => match serde_json::from_str::<ChatRequest>(&body) {
                Ok(chat) => {
                    if chat.messages.iter().any(|message| !message.attachments.is_empty()) {
                        let response = match call_agent_core_for_attachments(&chat) {
                            Ok(data) => Response::from_string(data.to_string()).with_header(
                                Header::from_bytes("Content-Type", "application/json").unwrap(),
                            ),
                            Err(error) => Response::from_string(json!({
                                "ok": false,
                                "error": error.to_string(),
                                "backend": "agent-core-attachments",
                            }).to_string()).with_status_code(StatusCode(503)),
                        };
                        let _ = request.respond(response);
                        return;
                    }
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
                    let activity_detail = if tool == "terminal_run" {
                        match call.arguments.get("operation").and_then(Value::as_str) {
                            Some("git_status") => "git status --short".to_string(),
                            Some("git_diff_stat") => "git diff --stat".to_string(),
                            Some("project_check") => match call.arguments.get("check").and_then(Value::as_str).unwrap_or("auto") {
                                "cargo-test" => "verificação cargo test".to_string(),
                                "npm-test" => "verificação npm test".to_string(),
                                "pytest" => "verificação pytest".to_string(),
                                "unittest" => "verificação unittest".to_string(),
                                _ => "verificação reconhecida do projeto".to_string(),
                            },
                            _ => "perfil de terminal permitido".to_string(),
                        }
                    } else if tool == "process_start" {
                        "servidor local do workspace".to_string()
                    } else if tool == "process_status" {
                        "estado e saída do processo local".to_string()
                    } else if tool == "process_stop" {
                        "encerramento do processo local".to_string()
                    } else if tool == "apply_batch" {
                        format!("lote revisável com {} operação(ões)", call.arguments.get("operations").and_then(Value::as_array).map(Vec::len).unwrap_or(0))
                    } else if tool == "undo_batch" {
                        format!("reversão protegida do lote {}", call.arguments.get("transaction_id").and_then(Value::as_str).unwrap_or("mais recente"))
                    } else { tool.clone() };
                    activity(&activity_log, &operation, "requested", &format!("Ação {activity_detail} recebida; preparando execução."), false, None);
                    activity(&activity_log, &operation, "started", &format!("Executando {activity_detail}."), false, None);
                    let result = match execute_shared_with_processes(call, &state, Some(&processes), Some(&activity_log)) {
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

fn wait_for_model_worker(worker: &mut Child) -> bool {
    let client = match reqwest::blocking::Client::builder().timeout(Duration::from_secs(1)).build() {
        Ok(client) => client,
        Err(error) => {
            eprintln!("Não foi possível verificar a inicialização do modelo: {error}");
            return false;
        }
    };
    let deadline = Instant::now() + Duration::from_secs(30);
    while Instant::now() < deadline {
        match worker.try_wait() {
            Ok(Some(status)) => {
                eprintln!("O serviço do modelo encerrou durante a inicialização: {status}");
                return false;
            }
            Err(error) => {
                eprintln!("Não foi possível verificar o serviço do modelo: {error}");
                return false;
            }
            Ok(None) => {}
        }
        if client.get("http://127.0.0.1:3101/health").send()
            .ok().filter(|response| response.status().is_success())
            .and_then(|response| response.json::<Value>().ok())
            .is_some_and(|health| model_worker_ready(&health)) {
            return true;
        }
        std::thread::sleep(Duration::from_millis(100));
    }
    eprintln!("O serviço do modelo não ficou pronto em 30 segundos; o agente não aceitará tarefas.");
    false
}

fn run_web_server() {
    let server = Server::http("127.0.0.1:3000").expect("não foi possível abrir http://127.0.0.1:3000");
    eprintln!("Interface local: http://127.0.0.1:3000");
    let workspace = std::env::current_dir().expect("não foi possível descobrir o workspace");
    let mut model_worker: Option<Child> = None;
    let mut agent_worker: Option<Child> = None;
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
    // The model opens /health only after loading its model and tool adapters.
    // Wait before exposing the agent so a restart cannot turn a valid request
    // into several immediate connection failures while Python is importing.
    let model_ready = model_worker.as_mut().is_some_and(wait_for_model_worker);
    let agent_server = workspace.join("agent-core/src/server.ts");
    if model_ready && agent_server.exists() {
        match Command::new("node")
            .arg("--experimental-strip-types")
            .arg(&agent_server)
            .env("IA_AGENT_PORT", "3200")
            .env("IA_AGENT_RUNTIME_URL", "http://127.0.0.1:3000")
            .current_dir(&workspace)
            .spawn()
        {
            Ok(child) => {
                agent_worker = Some(child);
                eprintln!("AgentCore TypeScript iniciado em http://127.0.0.1:3200");
            }
            Err(error) => eprintln!("AgentCore TypeScript indisponível: {error}"),
        }
    }
    let state: SharedState = Arc::new(Mutex::new((HashMap::new(), 1, workspace)));
    let activity_log: SharedActivity = Arc::new(Mutex::new(ActivityLog::default()));
    let processes: SharedProcesses = Arc::new(Mutex::new(ProcessSupervisor::default()));

    for request in server.incoming_requests() {
        let state = Arc::clone(&state);
        let activity_log = Arc::clone(&activity_log);
        let processes = Arc::clone(&processes);
        std::thread::spawn(move || handle_web_request(request, state, activity_log, processes));
    }

    if let Some(mut worker) = model_worker {
        let _ = worker.kill();
    }
    if let Some(mut worker) = agent_worker {
        let _ = worker.kill();
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn research_keeps_qualified_symbols_and_rejects_unrelated_sentences() {
        let result = synthesize_research("asyncio.TaskGroup Python", &[json!({
            "source_id": "web-1", "text": "Python asyncio has unrelated background tasks. asyncio.TaskGroup is an asynchronous context manager that waits for all its tasks to finish."
        })]);
        assert_eq!(result["grounded"], true);
        let answer = result["answer"].as_str().unwrap();
        assert!(answer.contains("asyncio.TaskGroup"));
        assert!(!answer.contains("unrelated background"));
    }

    #[test]
    fn openapi_exposes_dialogue_turn_and_provider_status() {
        let spec = api_openapi();
        assert!(spec["paths"]["/api/v1/dialogue/turn"]["post"].is_object());
        assert!(spec["paths"]["/api/v1/dialogue/providers"]["get"].is_object());
        assert!(spec["paths"]["/api/v1/agent/understand"]["post"].is_object());
        assert!(spec["paths"]["/api/v1/agent/pursue"]["post"].is_object());
        assert!(spec["paths"]["/api/v1/agent/tasks/{task_id}/resume"]["post"].is_object());
    }

    #[test]
    fn cognition_proxies_allow_only_explicit_paths_and_methods() {
        assert_eq!(cognition_proxy_route(&Method::Get, "/api/v1/cognition/cores"), Some(("/v1/cognition/cores", 10)));
        assert_eq!(cognition_proxy_route(&Method::Post, "/api/v1/cognition/cores/decide"), Some(("/v1/cognition/cores/decide", 30)));
        assert_eq!(cognition_proxy_route(&Method::Get, "/api/v1/models/experimental"), Some(("/v1/models/experimental", 10)));
        assert_eq!(cognition_proxy_route(&Method::Post, "/api/v1/models/experimental/decide"), Some(("/v1/models/experimental/decide", 30)));
        for (method, path) in [(Method::Post, "/api/v1/cognition/cores"),
                               (Method::Get, "/api/v1/models/experimental/decide"),
                               (Method::Delete, "/api/v1/cognition/cores/decide"),
                               (Method::Get, "/api/v1/models/experimental?checkpoint=other"),
                               (Method::Get, "/api/v1/models/experimental/../tools/call")] {
            assert_eq!(cognition_proxy_route(&method, path), None);
        }
    }

    #[test]
    fn cognition_proxy_checks_byte_limit_before_utf8_decoding() {
        let allowed = vec![b'a'; 64 * 1024];
        assert_eq!(read_cognition_proxy_body(&mut allowed.as_slice()).unwrap().len(), allowed.len());
        let oversized = "á".repeat(32 * 1024 + 1).into_bytes();
        assert_eq!(read_cognition_proxy_body(&mut oversized.as_slice()).unwrap_err().0, 413);
        assert_eq!(read_cognition_proxy_body(&mut [0xff].as_slice()).unwrap_err().0, 400);
    }

    #[test]
    fn model_worker_requires_loaded_generation_and_explicit_null_error() {
        assert!(model_worker_ready(&json!({"ok":true,"free_generation":true,"error":null})));
        for health in [json!({"ok":true,"free_generation":false,"error":null}),
                       json!({"ok":true,"free_generation":true,"error":"pesos inválidos"}),
                       json!({"ok":true,"free_generation":true}),
                       json!({"ok":false,"free_generation":true,"error":null}),
                       json!({"ok":true,"free_generation":"true","error":null}),
                       Value::Null] {
            assert!(!model_worker_ready(&health));
        }
    }

    #[test]
    fn openapi_exposes_gated_cores_and_isolated_experimental_contracts() {
        let spec = api_openapi();
        for path in ["/api/v1/cognition/cores", "/api/v1/models/experimental"] {
            assert!(spec["paths"][path]["get"]["responses"]["503"].is_object());
        }
        for (path, schema) in [("/api/v1/cognition/cores/decide", "cognitive-core-request/v1"),
                               ("/api/v1/models/experimental/decide", "experimental-cognitive-request/v1")] {
            let post = &spec["paths"][path]["post"];
            assert_eq!(post["requestBody"]["content"]["application/json"]["schema"]["properties"]["schema"]["const"], schema);
            assert!(post["responses"]["413"].is_object());
            assert!(post["responses"]["422"].is_object());
        }
    }

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
    fn pesquisa_com_site_explicito_descarta_subdominios_e_dominios_alheios() {
        let query = "site:cmake.org FetchContent GIT_TAG URL_HASH";
        assert!(result_matches_requested_site(query, &json!({"url":"https://cmake.org/cmake/help/latest/module/FetchContent.html"})));
        assert!(result_matches_requested_site(query, &json!({"url":"https://www.cmake.org/documentation/"})));
        assert!(!result_matches_requested_site(query, &json!({"url":"https://discourse.cmake.org/t/example/1"})));
        assert!(!result_matches_requested_site(query, &json!({"url":"https://example.org/cmake"})));
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
    fn pesquisa_cmake_fetchcontent_tem_recuperacao_direta_para_documentacao_oficial() {
        let urls = direct_documentation_urls("CMake FetchContent");
        assert!(urls.iter().any(|url| url == "https://cmake.org/cmake/help/latest/module/FetchContent.html"));
        assert!(urls.iter().all(|url| result_matches_requested_site(
            "site:cmake.org FetchContent GIT_TAG URL_HASH", &json!({"url": url})
        )));
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

    fn explorer_request(state: &SharedState, root: &Path, operation: &str, fields: Value) -> Result<Value, RuntimeError> {
        let mut args = json!({"schema":"workspace-entry/v1", "workspace_root":root, "operation":operation});
        args.as_object_mut().unwrap().extend(fields.as_object().unwrap().clone());
        workspace_entry_action(&args, state)
    }

    #[test]
    fn explorer_cria_arquivo_vazio_e_salva_sem_substituir_versao_alterada() {
        let dir = TestDirectory::new();
        let state = Arc::new(Mutex::new((HashMap::new(), 1, dir.0.clone())));
        explorer_request(&state, &dir.0, "create_file", json!({"path":"src/novo.py","content":""})).unwrap();
        explorer_request(&state, &dir.0, "edit_file", json!({"path":"src/novo.py","old_text":"","new_text":"def square(x): return x*x\n"})).unwrap();
        assert_eq!(fs::read_to_string(dir.0.join("src/novo.py")).unwrap(), "def square(x): return x*x\n");
        assert!(explorer_request(&state, &dir.0, "edit_file", json!({"path":"src/novo.py","old_text":"","new_text":"wrong"})).is_err());
        assert!(explorer_request(&state, &dir.0, "create_file", json!({"path":"src/novo.py","content":"wrong"})).is_err());
        assert_eq!(fs::read_to_string(dir.0.join("src/novo.py")).unwrap(), "def square(x): return x*x\n");
    }

    #[test]
    fn explorer_lixeira_preserva_pasta_e_arquivos_binarios_e_lista_apos_reinicio() {
        let dir = TestDirectory::new();
        let state = Arc::new(Mutex::new((HashMap::new(), 1, dir.0.clone())));
        explorer_request(&state, &dir.0, "create_directory", json!({"path":"assets/nested"})).unwrap();
        fs::write(dir.0.join("assets/nested/data.bin"), [0, 255, 1, 128]).unwrap();
        let trashed = explorer_request(&state, &dir.0, "trash", json!({"path":"assets"})).unwrap();
        assert!(!dir.0.join("assets").exists());
        let restored_state = Arc::new(Mutex::new((HashMap::new(), 1, dir.0.clone())));
        let listing = explorer_request(&restored_state, &dir.0, "list_trash", json!({})).unwrap();
        assert_eq!(listing["entries"].as_array().unwrap().len(), 1);
        assert_eq!(listing["entries"][0]["trash_id"], trashed["trash_id"]);
        explorer_request(&restored_state, &dir.0, "restore", json!({"trash_id":trashed["trash_id"]})).unwrap();
        assert_eq!(fs::read(dir.0.join("assets/nested/data.bin")).unwrap(), vec![0, 255, 1, 128]);
        assert!(explorer_request(&restored_state, &dir.0, "list_trash", json!({})).unwrap()["entries"].as_array().unwrap().is_empty());
    }

    #[test]
    fn explorer_restauracao_preserva_arquivo_novo_em_conflito() {
        let dir = TestDirectory::new();
        let state = Arc::new(Mutex::new((HashMap::new(), 1, dir.0.clone())));
        fs::write(dir.0.join("note.txt"), "original").unwrap();
        let trashed = explorer_request(&state, &dir.0, "trash", json!({"path":"note.txt"})).unwrap();
        fs::write(dir.0.join("note.txt"), "new version").unwrap();
        assert!(explorer_request(&state, &dir.0, "restore", json!({"trash_id":trashed["trash_id"]})).is_err());
        assert_eq!(fs::read_to_string(dir.0.join("note.txt")).unwrap(), "new version");
        fs::remove_file(dir.0.join("note.txt")).unwrap();
        explorer_request(&state, &dir.0, "restore", json!({"trash_id":trashed["trash_id"]})).unwrap();
        assert_eq!(fs::read_to_string(dir.0.join("note.txt")).unwrap(), "original");
    }

    #[test]
    fn explorer_lista_itens_recuperaveis_apos_muitos_registros_ja_restaurados() {
        let dir = TestDirectory::new();
        let state = Arc::new(Mutex::new((HashMap::new(), 1, dir.0.clone())));
        fs::write(dir.0.join("note.txt"), "preserve").unwrap();
        let trashed = explorer_request(&state, &dir.0, "trash", json!({"path":"note.txt"})).unwrap();
        for index in 0..205 {
            fs::create_dir(dir.0.join(format!(".ia-local-backups/trash/9999999999999999999999-{index}"))).unwrap();
        }
        let listing = explorer_request(&state, &dir.0, "list_trash", json!({})).unwrap();
        assert_eq!(listing["entries"].as_array().unwrap().len(), 1);
        assert_eq!(listing["entries"][0]["trash_id"], trashed["trash_id"]);
    }

    #[test]
    fn explorer_rejeita_projeto_trocado_raiz_traversal_e_historico_interno() {
        let dir = TestDirectory::new(); let other = TestDirectory::new();
        let state = Arc::new(Mutex::new((HashMap::new(), 1, dir.0.clone())));
        assert!(explorer_request(&state, &other.0, "create_file", json!({"path":"wrong.txt","content":"wrong"})).is_err());
        for path in ["", ".", "../escape.txt", ".git", ".ia-local-backups"] {
            assert!(explorer_request(&state, &dir.0, "trash", json!({"path":path})).is_err());
        }
        assert!(explorer_request(&state, &dir.0, "trash", json!({"path":other.0})).is_err());
        assert!(!other.0.join("wrong.txt").exists());
        assert!(!dir.0.join("wrong.txt").exists());
        assert!(dir.0.exists());
    }

    #[cfg(unix)]
    #[test]
    fn explorer_rejeita_links_em_origem_pai_e_lixeira() {
        use std::os::unix::fs::symlink;
        let dir = TestDirectory::new(); let other = TestDirectory::new();
        let state = Arc::new(Mutex::new((HashMap::new(), 1, dir.0.clone())));
        fs::write(other.0.join("secret.txt"), "preserve").unwrap();
        symlink(other.0.join("secret.txt"), dir.0.join("linked.txt")).unwrap();
        symlink(&other.0, dir.0.join("outside")).unwrap();
        assert!(explorer_request(&state, &dir.0, "trash", json!({"path":"linked.txt"})).is_err());
        assert!(explorer_request(&state, &dir.0, "create_file", json!({"path":"outside/new.txt","content":"wrong"})).is_err());
        symlink(&other.0, dir.0.join(".ia-local-backups")).unwrap();
        assert!(explorer_request(&state, &dir.0, "trash", json!({"path":"linked.txt"})).is_err());
        assert!(explorer_request(&state, &dir.0, "list_trash", json!({})).is_err());
        assert_eq!(fs::read_to_string(other.0.join("secret.txt")).unwrap(), "preserve");
        assert!(!other.0.join("new.txt").exists());
    }

    #[test]
    fn motor_avalia_codigo_local_sem_executar_o_modulo() {
        let dir = TestDirectory::new();
        fs::write(dir.0.join("logic.py"), "raise RuntimeError('top-level must not run')\ndef weighted(rows):\n    return sum(x*x for x in rows)\n").unwrap();
        let result = computation_operation("evaluate_function", &json!({"path":"logic.py","function":"weighted","args":[[4,9,13]]}), &dir.0).unwrap();
        assert_eq!(result["passed"], true);
        assert_eq!(result["result"], 266);
        assert_eq!(result["top_level_executed"], false);
        assert_eq!(result["source_sha256"].as_str().unwrap().len(), 64);
        let calculated = computation_operation("calculate", &json!({"expression":"(x-y)/3","variables":{"x":1207,"y":22}}), &dir.0).unwrap();
        assert_eq!(calculated["result"], 395.0);
        assert!(computation_operation("evaluate_function", &json!({"path":"../logic.py","function":"weighted"}), &dir.0).is_err());
    }

    #[test]
    fn terminal_run_rejeita_operacoes_e_argumentos_nao_listados() {
        let dir = TestDirectory::new();
        assert!(terminal_run(&json!({"operation":"shell","program":"sh","args":["-c", "echo unsafe"]}), &dir.0).is_err());
        assert!(terminal_run(&json!({"operation":"git_status","extra":"value"}), &dir.0).is_err());
        assert!(terminal_run(&json!({"operation":"project_check","check":"sh -c rm -rf /"}), &dir.0).is_err());
    }

    #[test]
    fn terminal_run_executa_perfil_git_fixo_dentro_do_workspace() {
        let dir = TestDirectory::new();
        fs::create_dir_all(dir.0.join(".git/objects")).unwrap();
        fs::create_dir_all(dir.0.join(".git/refs/heads")).unwrap();
        fs::write(dir.0.join(".git/HEAD"), "ref: refs/heads/main\n").unwrap();
        fs::write(dir.0.join(".git/config"), "[core]\n repositoryformatversion = 0\n bare = false\n").unwrap();
        fs::write(dir.0.join("sample.txt"), "evidência\n").unwrap();
        let result = terminal_run(&json!({"operation":"git_status"}), &dir.0).unwrap();
        assert_eq!(result["operation"], "git_status");
        assert_eq!(result["command"], "git --no-optional-locks status --short");
        assert_eq!(result["passed"], true);
        assert!(result["stdout"].as_str().unwrap_or_default().contains("sample.txt"));
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
    fn listagem_de_workspace_pode_incluir_ocultos_com_limite() {
        let dir = TestDirectory::new();
        fs::create_dir(dir.0.join(".ia-local-backups")).unwrap();
        fs::create_dir(dir.0.join("tests")).unwrap();
        fs::write(dir.0.join("todo_cli.py"), "pass\n").unwrap();
        let ordinary = list_files(&json!({}), &dir.0).unwrap();
        assert_eq!(ordinary["total_entries"], 2);
        assert_eq!(ordinary["truncated"], false);
        let complete = list_files(&json!({"include_hidden": true, "max_entries": 2}), &dir.0).unwrap();
        assert_eq!(complete["total_entries"], 3);
        assert_eq!(complete["truncated"], true);
        assert_eq!(complete["entries"].as_array().unwrap().len(), 2);
        assert_eq!(complete["entries"][0]["name"], ".ia-local-backups");
    }

    #[test]
    fn ferramentas_rejeitam_pastas_internas_reservadas() {
        let dir = TestDirectory::new();
        assert!(create_file(&json!({"path": ".git/config","content": "ignorado"}), &dir.0).is_err());
        assert!(create_file(&json!({"path": ".ia-local-backups/trace.txt","content": "ignorado"}), &dir.0).is_err());
        assert!(create_directory(&json!({"path": ".git/refs/heads"}), &dir.0).is_err());
        assert!(create_directory(&json!({"path": ".ia-local-backups/new-dir"}), &dir.0).is_err());
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
        fs::write(dir.0.join("package.json"), "{\"scripts\":{\"test\":\"node --test\",\"check\":\"tsc --noEmit\"}}\n").unwrap();
        let with_test = project_checks(&json!({"check":"list"}), &dir.0).unwrap();
        assert!(with_test["available"].as_array().unwrap().iter().any(|item| item == "npm-test"));
        assert!(with_test["available"].as_array().unwrap().iter().any(|item| item == "npm-check"));
        let without_checker = project_checks(&json!({"check":"auto"}), &TestDirectory::new().0).unwrap();
        assert_eq!(without_checker["executed"], false);
        assert_eq!(without_checker["passed"], false);
        fs::create_dir(dir.0.join("tests")).unwrap();
        fs::write(dir.0.join("tests/test_ok.py"), "import unittest\n\nclass TestOk(unittest.TestCase):\n    def test_ok(self): self.assertTrue(True)\n").unwrap();
        let python_check = project_checks(&json!({"check":"auto","path":"app.py"}), &dir.0).unwrap();
        assert_eq!(python_check["check"], "unittest");
        assert_eq!(python_check["passed"], true);
    }

    #[test]
    fn detecta_checks_em_subprojetos_e_os_associa_ao_arquivo_alterado() {
        let dir = TestDirectory::new();
        fs::create_dir(dir.0.join("agent-core")).unwrap();
        fs::write(dir.0.join("agent-core/package.json"), "{\"scripts\":{\"test\":\"node --test\",\"check\":\"true\"}}\n").unwrap();
        fs::create_dir(dir.0.join("runtime")).unwrap();
        fs::write(dir.0.join("runtime/Cargo.toml"), "[package]\nname='fixture'\nversion='0.1.0'\n").unwrap();
        let listed = project_checks(&json!({"check":"list"}), &dir.0).unwrap();
        assert_eq!(listed["available"], json!(["cargo-test", "npm-test", "npm-check"]));
        assert_eq!(listed["locations"]["cargo-test"], json!(["runtime"]));
        assert_eq!(listed["locations"]["npm-check"], json!(["agent-core"]));
        let selected = project_checks(&json!({"check":"auto", "path":"agent-core/src/server.ts"}), &dir.0).unwrap();
        assert_eq!(selected["check"], "npm-test");
        assert!(selected["command"].as_str().unwrap().starts_with("cd agent-core && npm test"));
        let check = project_checks(&json!({"check":"npm-check", "path":"agent-core/src/server.ts"}), &dir.0).unwrap();
        assert_eq!(check["check"], "npm-check");
        assert_eq!(check["passed"], true);
        assert!(check["command"].as_str().unwrap().ends_with("npm run check"));
    }

    #[test]
    fn verifica_testes_python_na_raiz_do_projeto() {
        let dir = TestDirectory::new();
        fs::write(dir.0.join("app.py"), "def add(a, b):\n    return a + b\n").unwrap();
        fs::write(dir.0.join("test_app.py"), "import unittest\nfrom app import add\n\nclass TestAdd(unittest.TestCase):\n    def test_sum(self): self.assertEqual(add(1, 2), 3)\n").unwrap();
        let result = project_checks(&json!({"check":"auto","path":"app.py"}), &dir.0).unwrap();
        assert_eq!(result["check"], "unittest");
        assert_eq!(result["passed"], true);
        assert_eq!(result["executed"], true);
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
    fn inspecao_prioriza_codigo_e_manifestos_antes_de_dados_volumosos() {
        let dir = TestDirectory::new();
        fs::create_dir_all(dir.0.join("corpus")).unwrap();
        fs::create_dir_all(dir.0.join("python/data")).unwrap();
        fs::create_dir_all(dir.0.join("runtime/src")).unwrap();
        fs::write(dir.0.join("python/README.md"), "# Instruções\n").unwrap();
        fs::write(dir.0.join("python/model_server.py"), "def main(): pass\n").unwrap();
        fs::write(dir.0.join("runtime/Cargo.toml"), "[package]\nname='fixture'\n").unwrap();
        fs::write(dir.0.join("runtime/src/main.rs"), "fn main() {}\n").unwrap();
        for index in 0..410 {
            fs::write(dir.0.join(format!("python/data/sample-{index:03}.jsonl")), "{}\n").unwrap();
        }

        let result = inspect_project(&json!({"max_depth": 4}), &dir.0).unwrap();
        let paths = result["files"].as_array().unwrap().iter()
            .filter_map(|item| item["path"].as_str())
            .collect::<Vec<_>>();
        assert!(result["truncated"].as_bool().unwrap());
        assert!(result["manifests"].as_array().unwrap().iter().any(|item| item == "runtime/Cargo.toml"));
        assert!(paths.contains(&"python/README.md"));
        assert!(paths.contains(&"python/model_server.py"));
        assert!(paths.contains(&"runtime/src/main.rs"));
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
    fn eventos_agentcore_entram_no_feed_compartilhado_e_mantem_estado_final() {
        let log = Arc::new(Mutex::new(ActivityLog::default()));
        publish_agent_event_json(&log, r#"{"operation":"agent-core-run-1","phase":"plan","status":"running","message":"Plano preparado"}"#).unwrap();
        publish_agent_event_json(&log, r#"{"operation":"agent-core-run-1","phase":"complete","status":"blocked","message":"Aguardando aprovação"}"#).unwrap();
        let result = activity_events_v2(&log, 0, None, Some("agent-core-run-1"));
        assert_eq!(result["events"].as_array().unwrap().len(), 2);
        assert_eq!(result["events"][0]["phase"], "planning");
        assert_eq!(result["events"][0]["status"], "running");
        assert_eq!(result["events"][1]["status"], "blocked");
    }

    #[test]
    fn evento_agentcore_rejeita_operacao_nao_correlacionada() {
        let log = Arc::new(Mutex::new(ActivityLog::default()));
        assert!(publish_agent_event_json(&log, r#"{"operation":"chat-other","phase":"plan","status":"running","message":"no"}"#).is_err());
        assert_eq!(activity_after(&log, 0)["events"].as_array().unwrap().len(), 0);
    }

    #[test]
    fn streaming_preserves_encoded_unicode_payload() {
        let log = Arc::new(Mutex::new(ActivityLog::default()));
        let message = format!("__ANSWER_DELTA__ · {}", "%F0%9F%9A%80".repeat(90));
        publish_agent_event_json(&log, &json!({"operation":"agent-core-stream", "phase":"plan",
            "status":"running", "message":message}).to_string()).unwrap();
        assert_eq!(activity_after(&log, 0)["events"][0]["message"], message);
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
