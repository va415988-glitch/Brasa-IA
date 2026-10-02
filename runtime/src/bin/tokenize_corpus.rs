//! Tokeniza JSONL usando o tokenizer byte-level BPE do projeto.
//! Mantém o formato little-endian u32 usado pelo treinamento Python.

use serde::Deserialize;
use serde_json::Value;
use std::collections::HashMap;
use std::env;
use std::fs::{self, File};
use std::io::{BufRead, BufReader, Write};

#[derive(Deserialize)]
struct TokenizerFile {
    special_tokens: HashMap<String, u32>,
    vocab: HashMap<String, u32>,
    merges: Vec<[u32; 3]>,
}

struct Tokenizer {
    byte_ids: [u32; 256],
    merges: Vec<(u32, u32, u32)>,
    bos: u32,
    eos: u32,
}

fn has_declared_provenance(record: &Value) -> bool {
    let source = record.get("source").and_then(Value::as_str).unwrap_or("").trim();
    let license = record.get("license").and_then(Value::as_str).unwrap_or("").trim();
    let normalized_license = license.to_ascii_lowercase();
    !source.is_empty()
        && !license.is_empty()
        && !normalized_license.contains("unknown")
        && !normalized_license.contains("verify")
        && !normalized_license.contains("check-per-source")
}

impl Tokenizer {
    fn load(path: &str) -> Result<Self, Box<dyn std::error::Error>> {
        let data: TokenizerFile = serde_json::from_str(&fs::read_to_string(path)?)?;
        let mut byte_ids = [0u32; 256];
        for byte in 0..=255u16 {
            let key = format!("{byte:02x}");
            byte_ids[byte as usize] = *data.vocab.get(&key).ok_or("byte ausente no vocab")?;
        }
        Ok(Self {
            byte_ids,
            merges: data.merges.into_iter().map(|merge| (merge[0], merge[1], merge[2])).collect(),
            bos: *data.special_tokens.get("<bos>").ok_or("<bos> ausente")?,
            eos: *data.special_tokens.get("<eos>").ok_or("<eos> ausente")?,
        })
    }

    fn encode(&self, text: &str) -> Vec<u32> {
        let mut tokens: Vec<u32> = text.bytes().map(|byte| self.byte_ids[byte as usize]).collect();
        for &(left, right, replacement) in &self.merges {
            let mut next = Vec::with_capacity(tokens.len());
            let mut index = 0;
            while index < tokens.len() {
                if index + 1 < tokens.len() && tokens[index] == left && tokens[index + 1] == right {
                    next.push(replacement);
                    index += 2;
                } else {
                    next.push(tokens[index]);
                    index += 1;
                }
            }
            tokens = next;
        }
        tokens
    }
}

fn format_trace(value: &Value) -> String {
    value.get("messages").and_then(Value::as_array).map(|messages| {
        let mut result = String::new();
        for message in messages {
            let role = message.get("role").and_then(Value::as_str).unwrap_or("");
            result.push_str(&format!("<|{role}|>\n"));
            if let Some(call) = message.get("tool_call") {
                result.push_str(&serde_json::to_string(call).unwrap_or_default());
            } else if let Some(content) = message.get("content") {
                if let Some(content) = content.as_str() {
                    result.push_str(content);
                } else {
                    result.push_str(&serde_json::to_string(content).unwrap_or_default());
                }
            }
            result.push('\n');
        }
        result
    }).unwrap_or_else(|| value.get("text").and_then(Value::as_str).unwrap_or("").to_owned())
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let dataset = env::args().nth(1).unwrap_or_else(|| "python/data/combined.jsonl".into());
    let tokenizer_path = env::args().nth(2).unwrap_or_else(|| "model/tokenizer.json".into());
    let output = env::args().nth(3).unwrap_or_else(|| "model/train_tokens_rust.bin".into());
    let val_output = env::var("TOKEN_VAL_OUTPUT").ok();
    let val_fraction: f64 = env::var("TOKEN_VAL_FRACTION").ok().and_then(|value| value.parse::<f64>().ok()).unwrap_or(0.0).clamp(0.0, 0.4);
    let repeat: usize = env::var("TOKEN_REPEAT").ok().and_then(|value| value.parse().ok()).unwrap_or(1).max(1);
    let tokenizer = Tokenizer::load(&tokenizer_path)?;
    let mut tokens = Vec::new();
    let file = File::open(&dataset)?;
    let records: Vec<Value> = BufReader::new(file).lines()
        .filter_map(|line| line.ok())
        .filter(|line| !line.trim().is_empty())
        .map(|line| serde_json::from_str(&line))
        .collect::<Result<Vec<_>, _>>()?;
    let require_provenance = env::var("TOKEN_REQUIRE_PROVENANCE")
        .map(|value| matches!(value.trim().to_ascii_lowercase().as_str(), "1" | "true" | "yes"))
        .unwrap_or(false);
    if require_provenance {
        let pending = records.iter().filter(|record| !has_declared_provenance(record)).count();
        if pending > 0 {
            return Err(format!(
                "tokenização bloqueada: {pending} registros sem origem e licença declaradas; revise o corpus e execute corpus_audit"
            ).into());
        }
    }
    let split = ((records.len() as f64) * (1.0 - val_fraction)).round() as usize;
    let (train_records, val_records) = records.split_at(split.min(records.len()));
    for _ in 0..repeat {
        for record in train_records {
            tokens.push(tokenizer.bos);
            tokens.extend(tokenizer.encode(&format_trace(record)));
            tokens.push(tokenizer.eos);
        }
    }
    let output_path = std::path::Path::new(&output);
    if let Some(parent) = output_path.parent() { fs::create_dir_all(parent)?; }
    let mut file = File::create(output_path)?;
    for token in &tokens { file.write_all(&token.to_le_bytes())?; }
    if let Some(val_path) = val_output {
        let mut validation_tokens = Vec::new();
        for record in val_records {
            validation_tokens.push(tokenizer.bos);
            validation_tokens.extend(tokenizer.encode(&format_trace(record)));
            validation_tokens.push(tokenizer.eos);
        }
        let val_path = std::path::Path::new(&val_path);
        if let Some(parent) = val_path.parent() { fs::create_dir_all(parent)?; }
        let mut val_file = File::create(val_path)?;
        for token in &validation_tokens { val_file.write_all(&token.to_le_bytes())?; }
        println!("validação: {} registros, {} tokens, {}", val_records.len(), validation_tokens.len(), val_path.display());
    }
    println!("registros de treino: {}", train_records.len());
    println!("registros de validação: {}", val_records.len());
    println!("repetições: {repeat}");
    println!("tokens gravados: {}", tokens.len());
    println!("arquivo: {output}");
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::has_declared_provenance;
    use serde_json::json;

    #[test]
    fn requires_source_and_a_declared_license() {
        assert!(has_declared_provenance(&json!({
            "source": "docs.python.org",
            "license": "PSF License"
        })));
        for record in [
            json!({"source": "local", "license": "unknown-review-required"}),
            json!({"source": "local", "license": "CC-BY-SA (verify current terms)"}),
            json!({"source": "", "license": "MIT"}),
            json!({"source": "local"}),
        ] {
            assert!(!has_declared_provenance(&record));
        }
    }
}
