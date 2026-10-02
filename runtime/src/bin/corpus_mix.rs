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

fn mixed_record(kind: &str, category: &str, position: usize, text: String,
                source: &Value, input_path: &str) -> Value {
    let mut record = json!({
        "kind": kind,
        "category": category,
        "position": position,
        "text": text,
        "input_path": input_path,
    });
    if let Some(object) = record.as_object_mut() {
        for key in ["id", "source", "license", "language", "path", "sha256"] {
            if let Some(value) = source.get(key) {
                object.insert(key.to_owned(), value.clone());
            }
        }
    }
    record
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

    #[test]
    fn preserves_source_license_and_input_path() {
        let source = json!({
            "id": "python-docs-1",
            "source": "docs.python.org",
            "license": "PSF License",
            "language": "en",
            "path": "corpus/raw/programming_docs.jsonl",
            "sha256": "abc123",
        });
        let record = mixed_record(
            "knowledge", "programming/python", 3, "lesson text".into(),
            &source, "corpus/clean/knowledge.jsonl",
        );
        assert_eq!(record["source"], "docs.python.org");
        assert_eq!(record["license"], "PSF License");
        assert_eq!(record["input_path"], "corpus/clean/knowledge.jsonl");
        assert_eq!(record["sha256"], "abc123");
    }

    #[test]
    fn missing_provenance_stays_missing_for_the_auditor() {
        let record = mixed_record(
            "behavior", "tools-and-dialogue", 0, "trace".into(),
            &json!({}), "python/data/combined.jsonl",
        );
        assert!(record.get("source").is_none());
        assert!(record.get("license").is_none());
        assert_eq!(record["input_path"], "python/data/combined.jsonl");
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
            records.push(mixed_record(
                "knowledge",
                value.get("category").and_then(Value::as_str).unwrap_or("general"),
                position,
                value.get("text").and_then(Value::as_str).unwrap_or("").to_owned(),
                value,
                &knowledge_path,
            ));
        }
    }
    for _ in 0..behavior_weight {
        for (position, value) in behavior.iter().enumerate() {
            records.push(mixed_record(
                "behavior", "tools-and-dialogue", position, trace_text(value),
                value, &behavior_path,
            ));
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
