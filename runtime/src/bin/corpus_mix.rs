//! Mistura determinística de conhecimento e comportamento para preparar treino.
//! O resultado é JSONL textual, pronto para tokenização posterior.

use serde_json::{json, Value};
use std::env;
use std::fs::{self, File};
use std::io::{BufRead, BufReader, Write};

fn read_lines(path: &str) -> Result<Vec<Value>, Box<dyn std::error::Error>> {
    let file = File::open(path)?;
    Ok(BufReader::new(file).lines().collect::<Result<Vec<_>, _>>()?.into_iter()
        .filter(|line| !line.trim().is_empty())
        .map(|line| serde_json::from_str(&line))
        .collect::<Result<Vec<_>, _>>()?)
}

fn trace_text(value: &Value) -> String {
    value.get("messages").and_then(Value::as_array).map(|messages| {
        messages.iter().map(|message| {
            let role = message.get("role").and_then(Value::as_str).unwrap_or("unknown");
            let value = message.get("tool_call").or_else(|| message.get("content"));
            let content = match value {
                Some(Value::String(text)) => text.clone(),
                Some(Value::Null) | None => String::new(),
                Some(value) => serde_json::to_string(value).expect("JSON válido"),
            };
            format!("<|{role}|>\n{content}\n")
        }).collect::<String>()
    }).unwrap_or_default()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn preserves_tool_calls_and_structured_results() {
        let trace = json!({"messages":[
            {"role":"user","content":"liste arquivos"},
            {"role":"assistant","tool_call":{"tool":"list_files","arguments":{"path":"src"}}},
            {"role":"tool","content":{"ok":true,"entries":["main.rs"]}}
        ]});
        let text = trace_text(&trace);
        assert!(text.contains("\"tool\":\"list_files\""));
        assert!(text.contains("\"path\":\"src\""));
        assert!(text.contains("<|tool|>\n{\"ok\":true,\"entries\":[\"main.rs\"]}"));
    }
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let knowledge_path = env::args().nth(1).unwrap_or_else(|| "corpus/clean/knowledge.jsonl".into());
    let behavior_path = env::args().nth(2).unwrap_or_else(|| "python/data/combined.jsonl".into());
    let output = env::args().nth(3).unwrap_or_else(|| "model/train_corpus.jsonl".into());
    let knowledge = read_lines(&knowledge_path)?;
    let behavior = read_lines(&behavior_path)?;
    let mut records = Vec::new();

    // Pesos são explícitos e auditáveis: uma cópia de cada grupo por padrão.
    // Repetição adicional só ocorre quando o usuário informar um peso maior.
    let knowledge_weight: usize = env::var("KNOWLEDGE_WEIGHT").ok().and_then(|v| v.parse().ok()).unwrap_or(1);
    let behavior_weight: usize = env::var("BEHAVIOR_WEIGHT").ok().and_then(|v| v.parse().ok()).unwrap_or(1);
    for _ in 0..knowledge_weight {
        for (position, value) in knowledge.iter().enumerate() {
            records.push(json!({"kind":"knowledge", "category":value.get("category").and_then(Value::as_str).unwrap_or("general"), "position":position, "text":value.get("text").and_then(Value::as_str).unwrap_or("")}));
        }
    }
    for _ in 0..behavior_weight {
        for (position, value) in behavior.iter().enumerate() {
            records.push(json!({"kind":"behavior", "category":"tools-and-dialogue", "position":position, "text":trace_text(value)}));
        }
    }
    let output_path = std::path::Path::new(&output);
    if let Some(parent) = output_path.parent() { fs::create_dir_all(parent)?; }
    let mut file = File::create(output_path)?;
    for record in &records { writeln!(file, "{}", serde_json::to_string(record)?)?; }
    println!("conhecimento: {}", knowledge.len() * knowledge_weight);
    println!("comportamento: {}", behavior.len() * behavior_weight);
    println!("total: {}", records.len());
    println!("saída: {output}");
    Ok(())
}
