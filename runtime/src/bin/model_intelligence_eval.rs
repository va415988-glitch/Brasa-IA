use reqwest::blocking::Client;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::collections::{BTreeMap, HashMap};
use std::env;
use std::fs::{self, File};
use std::io::{BufRead, BufReader};
use std::net::IpAddr;
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};
use url::Url;

const DEFAULT_COUNT: usize = 500;
const MAX_COUNT: usize = 500;

#[derive(Debug)]
struct Config {
    endpoint: Url,
    dataset: Option<PathBuf>,
    output: PathBuf,
    count: usize,
    timeout_seconds: u64,
    dry_run: bool,
}

#[derive(Debug, Deserialize)]
struct ImportedExample {
    id: Option<String>,
    prompt: String,
    reference: String,
    dataset: Option<String>,
}

#[derive(Debug, Clone)]
enum Expected {
    Numeric(String),
    Exact(String),
    Contains(Vec<String>),
    YesNo(String),
    Json(BTreeMap<String, String>),
    Tool(String),
    Reference(String),
}

#[derive(Debug, Clone)]
struct Case {
    id: String,
    category: String,
    prompt: String,
    expected: Expected,
    objective: &'static str,
}

#[derive(Debug, Deserialize)]
struct Generation {
    #[serde(default)]
    text: String,
    #[serde(default)]
    backend: String,
    #[serde(default)]
    tool_call: Option<Value>,
    #[serde(default)]
    tool_calls: Vec<Value>,
}

#[derive(Debug, Serialize)]
struct CaseResult {
    id: String,
    category: String,
    score: f64,
    passed: bool,
    metric: String,
    latency_ms: u128,
    backend: String,
    error: Option<String>,
    response: String,
}

fn usage() {
    println!("Avaliador diagnóstico para o servidor local de IA\n\
Uso: cargo run --manifest-path runtime/Cargo.toml --bin model_intelligence_eval -- [opções]\n\
\nOpções:\n\
  --url URL                 Endpoint /generate (padrão: IA_LOCAL_MODEL_URL ou http://127.0.0.1:3001/generate)\n\
  --dataset ARQUIVO         JSONL evaluation.jsonl criado pelo importador HF\n\
  --count N                 Número de casos, de 1 a 500 (padrão: 500)\n\
  --output ARQUIVO          Relatório JSON (padrão: model/intelligence_eval_report.json)\n\
  --timeout-seconds N       Timeout por resposta (padrão: 120)\n\
  --dry-run                 Mostra categorias e quantidade, sem chamar o servidor\n\
  --help                    Exibe esta ajuda\n\
\nO endpoint precisa estar em localhost/loopback. O avaliador não executa tool calls.");
}

fn parse_args() -> Result<Config, String> {
    let mut endpoint = env::var("IA_LOCAL_MODEL_URL")
        .unwrap_or_else(|_| "http://127.0.0.1:3001/generate".to_string());
    let mut dataset = None;
    let mut output = PathBuf::from("model/intelligence_eval_report.json");
    let mut count = DEFAULT_COUNT;
    let mut timeout_seconds = 120;
    let mut dry_run = false;
    let args: Vec<String> = env::args().skip(1).collect();
    let mut i = 0;
    while i < args.len() {
        match args[i].as_str() {
            "--help" | "-h" => {
                usage();
                std::process::exit(0);
            }
            "--dry-run" => dry_run = true,
            "--url" | "--dataset" | "--count" | "--output" | "--timeout-seconds" => {
                let flag = args[i].clone();
                i += 1;
                let value = args.get(i).ok_or_else(|| format!("faltou valor para {flag}"))?;
                match flag.as_str() {
                    "--url" => endpoint = value.clone(),
                    "--dataset" => dataset = Some(PathBuf::from(value)),
                    "--count" => count = value.parse().map_err(|_| "--count deve ser inteiro".to_string())?,
                    "--output" => output = PathBuf::from(value),
                    "--timeout-seconds" => timeout_seconds = value.parse().map_err(|_| "--timeout-seconds deve ser inteiro".to_string())?,
                    _ => unreachable!(),
                }
            }
            other => return Err(format!("opção desconhecida: {other}")),
        }
        i += 1;
    }
    if !(1..=MAX_COUNT).contains(&count) {
        return Err(format!("--count precisa estar entre 1 e {MAX_COUNT}"));
    }
    if timeout_seconds == 0 {
        return Err("--timeout-seconds precisa ser maior que zero".into());
    }
    let endpoint = Url::parse(&endpoint).map_err(|e| format!("URL inválida: {e}"))?;
    validate_local_endpoint(&endpoint)?;
    Ok(Config { endpoint, dataset, output, count, timeout_seconds, dry_run })
}

fn validate_local_endpoint(url: &Url) -> Result<(), String> {
    if !matches!(url.scheme(), "http" | "https") || url.username() != "" || url.password().is_some() {
        return Err("o endpoint deve ser HTTP(S) e não pode conter credenciais".into());
    }
    let host = url.host_str().ok_or_else(|| "endpoint sem host".to_string())?;
    let loopback = if host.eq_ignore_ascii_case("localhost") {
        true
    } else {
        host.trim_start_matches('[').trim_end_matches(']').parse::<IpAddr>().map(|ip| ip.is_loopback()).unwrap_or(false)
    };
    if !loopback {
        return Err("por privacidade, prompts do dataset só podem ser enviados a localhost/loopback".into());
    }
    Ok(())
}

fn health_url(endpoint: &Url) -> Url {
    let mut health = endpoint.clone();
    health.set_path("/health");
    health.set_query(None);
    health.set_fragment(None);
    health
}

fn push(cases: &mut Vec<Case>, category: &str, prompt: String, expected: Expected, objective: &'static str) {
    let id = format!("{}-{:03}", category, cases.iter().filter(|c| c.category == category).count() + 1);
    cases.push(Case { id, category: category.to_string(), prompt, expected, objective });
}

fn make_cases(dataset: Option<&Path>, count: usize) -> Result<(Vec<Case>, usize, Option<String>), String> {
    let mut cases = Vec::new();
    let mut imported_count = 0;
    let mut dataset_name = None;
    if let Some(path) = dataset {
        let file = File::open(path).map_err(|e| format!("não foi possível abrir dataset {}: {e}", path.display()))?;
        for (line_index, line) in BufReader::new(file).lines().enumerate() {
            if cases.len() >= count { break; }
            let line = line.map_err(|e| format!("erro lendo linha {}: {e}", line_index + 1))?;
            if line.trim().is_empty() { continue; }
            let example: ImportedExample = serde_json::from_str(&line)
                .map_err(|e| format!("JSONL inválido na linha {}: {e}", line_index + 1))?;
            if example.prompt.trim().is_empty() || example.reference.trim().is_empty() { continue; }
            let id = example.id.unwrap_or_else(|| format!("hf-{:04}", imported_count + 1));
            dataset_name = example.dataset.or(dataset_name);
            cases.push(Case {
                id,
                category: "dataset_huggingface".into(),
                prompt: example.prompt,
                expected: Expected::Reference(example.reference),
                objective: "conversation",
            });
            imported_count += 1;
        }
    }
    let builtins = builtin_cases();
    let builtin_count = count.saturating_sub(cases.len());
    cases.extend(builtins.into_iter().take(builtin_count));
    if cases.len() > count { cases.truncate(count); }
    Ok((cases, imported_count, dataset_name))
}

fn builtin_cases() -> Vec<Case> {
    let mut cases = Vec::with_capacity(MAX_COUNT);

    // 100 aritmética: 25 casos de cada operação, com resultados inteiros.
    for i in 0..25_i64 {
        let a = 13 + i * 7;
        let b = 2 + i % 11;
        push(&mut cases, "aritmetica", format!("Calcule {a} + {b}. Responda apenas com o número."), Expected::Numeric((a + b).to_string()), "conversation");
        push(&mut cases, "aritmetica", format!("Calcule {a} - {b}. Responda apenas com o número."), Expected::Numeric((a - b).to_string()), "conversation");
        push(&mut cases, "aritmetica", format!("Calcule {a} × {b}. Responda apenas com o número."), Expected::Numeric((a * b).to_string()), "conversation");
        let dividend = b * (3 + i % 13);
        push(&mut cases, "aritmetica", format!("Calcule {dividend} ÷ {b}. Responda apenas com o número inteiro."), Expected::Numeric((dividend / b).to_string()), "conversation");
    }

    // 50 sequências.
    for i in 0..50_i64 {
        let start = 2 + i;
        let step = 2 + (i % 9);
        let values = (0..5).map(|n| (start + n * step).to_string()).collect::<Vec<_>>().join(", ");
        push(&mut cases, "sequencias", format!("Qual é o próximo número da sequência: {values}, ? Responda apenas com o número."), Expected::Numeric((start + 5 * step).to_string()), "conversation");
    }

    // 50 deduções booleanas simples.
    for i in 0..50_i64 {
        let (premise, question, answer) = match i % 5 {
            0 => (format!("Todos os objetos da classe C{i} são azuis. O item X{i} pertence à classe C{i}."), format!("O item X{i} é azul? Responda apenas sim ou não."), "sim"),
            1 => (format!("Nenhum elemento da classe D{i} é metálico. O item Y{i} pertence à classe D{i}."), format!("O item Y{i} é metálico? Responda apenas sim ou não."), "não"),
            2 => (format!("Se P{i} ocorre, então Q{i} ocorre. P{i} ocorreu."), format!("Podemos concluir que Q{i} ocorre? Responda apenas sim ou não."), "sim"),
            3 => (format!("Se R{i} ocorre, então S{i} ocorre. S{i} não ocorreu."), format!("Podemos concluir que R{i} ocorreu? Responda apenas sim ou não."), "não"),
            _ => (format!("Todo mamífero é animal. O golfinho é mamífero."), "O golfinho é um animal? Responda apenas sim ou não.".to_string(), "sim"),
        };
        push(&mut cases, "logica", format!("{premise} {question}"), Expected::YesNo(answer.into()), "conversation");
    }

    // 50 rastreamentos pequenos de código.
    for i in 0..50_i64 {
        let initial = i % 17;
        let increment = 2 + i % 8;
        let multiplier = 2 + i % 5;
        let expected = (initial + increment) * multiplier;
        let prompt = format!("Sem executar o código, calcule o valor impresso. Responda apenas com o número.\n```python\nx = {initial}\nx += {increment}\nprint(x * {multiplier})\n```");
        push(&mut cases, "rastreio_codigo", prompt, Expected::Numeric(expected.to_string()), "conversation");
    }

    // 50 extrações de contexto para avaliar atenção a detalhes.
    for i in 0..50 {
        let code = format!("REF-{i:03}-AZ");
        let distractor = format!("REF-{:03}-BX", 49 - i);
        let prompt = format!("Leia: pedido {i} tem etiqueta {code}; o registro anterior tinha etiqueta {distractor}. Qual etiqueta pertence ao pedido {i}? Responda somente com a etiqueta.");
        push(&mut cases, "leitura_contexto", prompt, Expected::Contains(vec![code]), "conversation");
    }

    // 50 seguimentos de instruções com saída exata.
    for i in 0..50 {
        let answer = format!("SINAL-{i:03}-OK");
        let prompt = format!("Responda exatamente com `{answer}` e não escreva mais nada.");
        push(&mut cases, "seguir_instrucao", prompt, Expected::Exact(answer), "conversation");
    }

    // 50 saídas JSON com esquema verificável.
    for i in 0..50 {
        let number = i + 1;
        let label = format!("item-{i:02}");
        let mut fields = BTreeMap::new();
        fields.insert("id".into(), label.clone());
        fields.insert("ativo".into(), if i % 2 == 0 { "true" } else { "false" }.into());
        fields.insert("quantidade".into(), number.to_string());
        let prompt = format!("Retorne somente um objeto JSON válido, sem Markdown, com id (string) = \"{label}\", ativo (boolean) = {}, quantidade (inteiro) = {number}.", if i % 2 == 0 { "true" } else { "false" });
        push(&mut cases, "json_estruturado", prompt, Expected::Json(fields), "conversation");
    }

    // 50 fatos estáveis e triviais, com sinônimos aceitos.
    let facts: [(&str, &str, &[&str]); 25] = [
        ("Qual é a capital do Japão?", "tóquio", &["tokyo", "tóquio"]),
        ("Qual é a capital da França?", "paris", &["paris"]),
        ("Qual é a capital de Portugal?", "lisboa", &["lisboa", "lisbon"]),
        ("Qual planeta é conhecido como planeta vermelho?", "marte", &["marte"]),
        ("Qual é a estrela mais próxima da Terra?", "sol", &["sol"]),
        ("Quantos lados tem um triângulo?", "3", &["3", "três"]),
        ("Quantos lados tem um hexágono?", "6", &["6", "seis"]),
        ("Qual gás as plantas absorvem principalmente na fotossíntese?", "dióxido de carbono", &["dióxido de carbono", "co2", "gás carbônico"]),
        ("Qual é a fórmula química da água?", "h2o", &["h2o"]),
        ("Em que continente fica o Egito?", "áfrica", &["áfrica", "africa"]),
        ("Qual é o maior planeta do Sistema Solar?", "júpiter", &["júpiter", "jupiter"]),
        ("Qual é o idioma oficial predominante no Brasil?", "português", &["português", "portugues"]),
        ("Quantos minutos há em uma hora?", "60", &["60", "sessenta"]),
        ("Qual oceano banha a costa leste do Brasil?", "atlântico", &["atlântico", "atlantico"]),
        ("Qual órgão bombeia o sangue no corpo humano?", "coração", &["coração", "coracao"]),
        ("Qual é o resultado de 2 elevado à potência 5?", "32", &["32"]),
        ("Qual é o primeiro mês do ano?", "janeiro", &["janeiro"]),
        ("Qual é o símbolo químico do oxigênio?", "o", &["o"]),
        ("Qual é o maior mamífero conhecido?", "baleia-azul", &["baleia azul", "baleia-azul"]),
        ("Quantos dias tem uma semana?", "7", &["7", "sete"]),
        ("Qual instrumento mede a temperatura?", "termômetro", &["termômetro", "termometro"]),
        ("Qual é o satélite natural da Terra?", "lua", &["lua"]),
        ("Qual é o estado físico da água a 100 °C ao nível do mar, durante a ebulição?", "gasoso", &["gasoso", "vapor"]),
        ("Qual é a raiz quadrada de 81?", "9", &["9", "nove"]),
        ("Que cor resulta da mistura de azul e amarelo em tintas tradicionais?", "verde", &["verde"]),
    ];
    for i in 0..50 {
        let (question, _, answers) = facts[i % facts.len()];
        let aliases = answers.iter().map(|s| s.to_string()).collect();
        push(&mut cases, "conhecimento_geral", format!("{question} Responda de forma breve."), Expected::Contains(aliases), "conversation");
    }

    // 50 decisões de roteamento: avaliar a ferramenta proposta, sem executá-la.
    let tools = [
        ("list_files", "Liste os arquivos e pastas do workspace atual."),
        ("read_file", "Leia o conteúdo do arquivo README.md."),
        ("search_files", "Procure no projeto onde a função `parse_config` é definida."),
        ("inspect_project", "Faça um levantamento da estrutura, stack e pontos de entrada do projeto."),
        ("project_checks", "Execute as verificações e testes configurados neste projeto."),
    ];
    for repeat in 0..10 {
        for (tool, prompt) in tools {
            let wording = if repeat == 0 { prompt.to_string() } else { format!("{} (solicitação de avaliação #{repeat})", prompt) };
            push(&mut cases, "roteamento_ferramentas", format!("Identifique a ferramenta adequada para esta solicitação: {wording} Não execute a ação; apenas selecione a ferramenta."), Expected::Tool(tool.into()), "operate");
        }
    }

    cases
}

fn normalize(value: &str) -> String {
    let lowered = value.to_lowercase()
        .replace('á', "a").replace('à', "a").replace('ã', "a").replace('â', "a")
        .replace('é', "e").replace('ê', "e").replace('í', "i")
        .replace('ó', "o").replace('ô', "o").replace('õ', "o").replace('ú', "u").replace('ç', "c");
    lowered.chars().map(|c| if c.is_alphanumeric() { c } else { ' ' }).collect::<String>()
        .split_whitespace().collect::<Vec<_>>().join(" ")
}

fn tokens(value: &str) -> Vec<String> {
    normalize(value).split_whitespace().map(str::to_string).collect()
}

fn token_f1(reference: &str, response: &str) -> f64 {
    let expected = tokens(reference);
    let actual = tokens(response);
    if expected.is_empty() || actual.is_empty() { return 0.0; }
    let mut counts: HashMap<&str, usize> = HashMap::new();
    for token in &expected { *counts.entry(token).or_default() += 1; }
    let mut matched = 0usize;
    for token in &actual {
        if let Some(available) = counts.get_mut(token.as_str()) {
            if *available > 0 { matched += 1; *available -= 1; }
        }
    }
    if matched == 0 { return 0.0; }
    let precision = matched as f64 / actual.len() as f64;
    let recall = matched as f64 / expected.len() as f64;
    2.0 * precision * recall / (precision + recall)
}

fn numeric_answer(text: &str) -> Option<String> {
    let bytes = text.as_bytes();
    for i in 0..bytes.len() {
        if bytes[i].is_ascii_digit() || (bytes[i] == b'-' && i + 1 < bytes.len() && bytes[i + 1].is_ascii_digit()) {
            let start = i;
            let mut end = i + 1;
            while end < bytes.len() && bytes[end].is_ascii_digit() { end += 1; }
            if end < bytes.len() && bytes[end] == b'.' {
                end += 1;
                while end < bytes.len() && bytes[end].is_ascii_digit() { end += 1; }
            }
            return text.get(start..end).map(str::to_string);
        }
    }
    None
}

fn extract_json(text: &str) -> Option<Value> {
    let start = text.find('{')?;
    let end = text.rfind('}')?;
    if end < start { return None; }
    serde_json::from_str(&text[start..=end]).ok()
}

fn score_case(case: &Case, text: &str, tool_call: Option<&Value>, tool_calls: &[Value]) -> (f64, bool, String) {
    let score = match &case.expected {
        Expected::Numeric(expected) => {
            if numeric_answer(text).as_deref() == Some(expected.as_str()) { 1.0 } else { 0.0 }
        }
        Expected::Exact(expected) => if normalize(text) == normalize(expected) { 1.0 } else { 0.0 },
        Expected::Contains(aliases) => {
            let response = normalize(text);
            if aliases.iter().any(|alias| response.contains(&normalize(alias))) { 1.0 } else { 0.0 }
        }
        Expected::YesNo(expected) => {
            let normalized = normalize(text);
            let first = normalized.split_whitespace().next().unwrap_or("");
            if (expected == "sim" && first == "sim") || (expected == "não" && matches!(first, "nao" | "não")) { 1.0 } else { 0.0 }
        }
        Expected::Json(fields) => {
            if let Some(value) = extract_json(text) {
                let matched = fields.iter().filter(|(key, expected)| {
                    value.get(key.as_str()).map(|actual| match expected.as_str() {
                        "true" => actual == &Value::Bool(true),
                        "false" => actual == &Value::Bool(false),
                        _ => actual.as_str().map(normalize).as_deref() == Some(normalize(expected).as_str()) || actual.to_string() == expected.as_str(),
                    }).unwrap_or(false)
                }).count();
                matched as f64 / fields.len() as f64
            } else { 0.0 }
        }
        Expected::Tool(expected) => {
            let tool = tool_call.and_then(|call| call.get("tool")).and_then(Value::as_str)
                .or_else(|| tool_calls.iter().find_map(|call| call.get("tool").and_then(Value::as_str)));
            if tool == Some(expected.as_str()) { 1.0 } else { 0.0 }
        }
        Expected::Reference(reference) => token_f1(reference, text),
    };
    let metric = match &case.expected {
        Expected::Reference(_) => "token_f1",
        Expected::Json(_) => "json_field_accuracy",
        Expected::Tool(_) => "tool_route_exact",
        _ => "exact_or_alias",
    };
    let passed = match &case.expected {
        Expected::Reference(_) => score >= 0.5,
        Expected::Json(_) => score >= 0.999,
        _ => score >= 1.0,
    };
    (score, passed, metric.into())
}

fn main() {
    if let Err(error) = run() {
        eprintln!("Erro: {error}");
        std::process::exit(2);
    }
}

fn run() -> Result<(), String> {
    let config = parse_args()?;
    let (cases, imported_count, dataset_name) = make_cases(config.dataset.as_deref(), config.count)?;
    let mut categories = BTreeMap::<String, usize>::new();
    for case in &cases { *categories.entry(case.category.clone()).or_default() += 1; }
    if config.dry_run {
        println!("Casos planejados: {}", cases.len());
        println!("Pares HF válidos: {imported_count}; casos sintéticos de complemento: {}", cases.len().saturating_sub(imported_count));
        for (category, count) in categories { println!("  {category}: {count}"); }
        return Ok(());
    }

    let client = Client::builder().timeout(Duration::from_secs(config.timeout_seconds)).build()
        .map_err(|e| format!("não foi possível criar cliente HTTP: {e}"))?;
    let health_response = client.get(health_url(&config.endpoint)).send()
        .map_err(|e| format!("servidor local indisponível em {}: {e}", health_url(&config.endpoint)))?;
    if !health_response.status().is_success() {
        return Err(format!("/health respondeu {}", health_response.status()));
    }
    let health: Value = health_response.json().map_err(|e| format!("resposta inválida em /health: {e}"))?;
    let declared_backend = health.get("backend").and_then(Value::as_str).unwrap_or("unknown").to_string();
    println!("Servidor: {} (backend declarado: {declared_backend})", config.endpoint);
    println!("Executando {} casos; referências HF: {imported_count}; sintéticos: {}", cases.len(), cases.len().saturating_sub(imported_count));

    let start = Instant::now();
    let mut results = Vec::with_capacity(cases.len());
    let mut backend_counts = BTreeMap::<String, usize>::new();
    let mut consecutive_errors = 0usize;
    for (index, case) in cases.iter().enumerate() {
        let request = json!({
            "messages": [{"role": "user", "content": case.prompt}],
            "objective": case.objective,
            "request_id": format!("intelligence-eval-{}", case.id),
        });
        let request_started = Instant::now();
        let response = client.post(config.endpoint.clone()).json(&request).send();
        let latency = request_started.elapsed().as_millis();
        let result = match response {
            Ok(http) => {
                let status = http.status();
                match http.json::<Generation>() {
                    Ok(generation) if status.is_success() => {
                        consecutive_errors = 0;
                        let backend = if generation.backend.is_empty() { declared_backend.clone() } else { generation.backend.clone() };
                        *backend_counts.entry(backend.clone()).or_default() += 1;
                        let (score, passed, metric) = score_case(case, &generation.text, generation.tool_call.as_ref(), &generation.tool_calls);
                        CaseResult { id: case.id.clone(), category: case.category.clone(), score, passed, metric, latency_ms: latency, backend, error: None, response: generation.text.chars().take(1200).collect() }
                    }
                    Ok(generation) => {
                        consecutive_errors += 1;
                        CaseResult { id: case.id.clone(), category: case.category.clone(), score: 0.0, passed: false, metric: "request_error".into(), latency_ms: latency, backend: generation.backend, error: Some(format!("HTTP {status}")), response: generation.text.chars().take(1200).collect() }
                    }
                    Err(error) => {
                        consecutive_errors += 1;
                        CaseResult { id: case.id.clone(), category: case.category.clone(), score: 0.0, passed: false, metric: "request_error".into(), latency_ms: latency, backend: declared_backend.clone(), error: Some(format!("HTTP {status}; JSON: {error}")), response: String::new() }
                    }
                }
            }
            Err(error) => {
                consecutive_errors += 1;
                CaseResult { id: case.id.clone(), category: case.category.clone(), score: 0.0, passed: false, metric: "request_error".into(), latency_ms: latency, backend: declared_backend.clone(), error: Some(error.to_string()), response: String::new() }
            }
        };
        results.push(result);
        if (index + 1) % 25 == 0 || index + 1 == cases.len() {
            eprintln!("Progresso: {}/{} casos", index + 1, cases.len());
        }
        if consecutive_errors >= 3 {
            eprintln!("Interrompido após três falhas consecutivas de comunicação; salvando execução parcial.");
            break;
        }
    }

    let mut category_totals = BTreeMap::<String, (usize, usize, f64)>::new();
    let mut latency_samples = Vec::new();
    for result in &results {
        let entry = category_totals.entry(result.category.clone()).or_default();
        entry.0 += 1;
        if result.passed { entry.1 += 1; }
        entry.2 += result.score;
        latency_samples.push(result.latency_ms);
    }
    latency_samples.sort_unstable();
    let pass_count = results.iter().filter(|r| r.passed).count();
    let mean_score = if results.is_empty() { 0.0 } else { results.iter().map(|r| r.score).sum::<f64>() / results.len() as f64 };
    let pctl = |p: f64| -> u128 {
        if latency_samples.is_empty() { return 0; }
        let index = ((latency_samples.len() as f64 - 1.0) * p).ceil() as usize;
        latency_samples[index.min(latency_samples.len() - 1)]
    };
    let category_report: BTreeMap<String, Value> = category_totals.into_iter().map(|(name, (total, passed, score))| {
        (name, json!({"cases": total, "passed": passed, "pass_rate": passed as f64 / total.max(1) as f64, "mean_score": score / total.max(1) as f64}))
    }).collect();
    let generated_at = SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default().as_secs();
    let report = json!({
        "schema": "ia-local-model-diagnostic-evaluation/v1",
        "generated_at_unix": generated_at,
        "endpoint": config.endpoint.as_str(),
        "health": health,
        "dataset": {"path": config.dataset.as_ref().map(|p| p.display().to_string()), "name": dataset_name, "imported_reference_cases": imported_count, "synthetic_cases": results.len().saturating_sub(imported_count), "training_eligible": false},
        "interpretation": "Métrica diagnóstica desta bateria e desta configuração; não representa QI nem uma medida universal de inteligência.",
        "counts": {"requested": config.count, "planned": cases.len(), "completed": results.len(), "passed": pass_count, "incomplete": results.len() < cases.len()},
        "scores": {"pass_rate": if results.is_empty() {0.0} else {pass_count as f64 / results.len() as f64}, "mean_case_score": mean_score},
        "categories": category_report,
        "observed_backends": backend_counts,
        "latency_ms": {"p50": pctl(0.50), "p95": pctl(0.95), "max": latency_samples.last().copied().unwrap_or(0), "wall_total": start.elapsed().as_millis()},
        "cases": results,
    });
    if let Some(parent) = config.output.parent().filter(|p| !p.as_os_str().is_empty()) {
        fs::create_dir_all(parent).map_err(|e| format!("não foi possível criar {}: {e}", parent.display()))?;
    }
    fs::write(&config.output, serde_json::to_vec_pretty(&report).map_err(|e| e.to_string())?)
        .map_err(|e| format!("não foi possível gravar {}: {e}", config.output.display()))?;
    println!("Concluídos: {}/{}; aprovados: {}; média diagnóstica: {:.1}%; backend: {}", results.len(), cases.len(), pass_count, mean_score * 100.0, declared_backend);
    println!("Relatório: {}", config.output.display());
    Ok(())
}
