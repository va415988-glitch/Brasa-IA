//! Auditor rápido do corpus JSONL. A transformação completa virá depois deste
//! estágio; por enquanto ele mede qualidade sem alterar os arquivos originais.

use sha2::{Digest, Sha256};
use std::collections::HashSet;
use std::env;
use std::fs::File;
use std::io::{BufRead, BufReader};

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let path = env::args().nth(1).unwrap_or_else(|| "corpus/clean/knowledge.jsonl".into());
    let file = File::open(&path)?;
    let reader = BufReader::new(file);
    let mut ids = HashSet::new();
    let mut hashes = HashSet::new();
    let mut records = 0usize;
    let mut invalid = 0usize;
    let mut duplicated = 0usize;
    let mut chars = 0usize;

    for line in reader.lines() {
        let line = line?;
        if line.trim().is_empty() {
            continue;
        }
        let value: serde_json::Value = match serde_json::from_str(&line) {
            Ok(value) => value,
            Err(_) => {
                invalid += 1;
                continue;
            }
        };
        let Some(text) = value.get("text").and_then(|value| value.as_str()) else {
            invalid += 1;
            continue;
        };
        records += 1;
        chars += text.chars().count();
        let id = value.get("id").and_then(|value| value.as_str()).unwrap_or("");
        if !id.is_empty() && !ids.insert(id.to_owned()) {
            duplicated += 1;
        }
        let mut digest = Sha256::new();
        digest.update(text.as_bytes());
        if !hashes.insert(digest.finalize()) {
            duplicated += 1;
        }
    }

    println!("arquivo: {path}");
    println!("registros válidos: {records}");
    println!("caracteres: {chars}");
    println!("duplicidades: {duplicated}");
    println!("registros inválidos: {invalid}");
    if invalid > 0 || duplicated > 0 {
        std::process::exit(2);
    }
    Ok(())
}
