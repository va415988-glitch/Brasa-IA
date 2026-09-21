//! Ingestão rápida de texto/JSONL para o acervo local.
//!
//! O formato de saída é compatível com python/ingest_corpus.py. O programa não
//! baixa a internet e não altera os arquivos de origem.

use sha2::{Digest, Sha256};
use std::collections::HashSet;
use std::env;
use std::fs::{self, File};
use std::io::{BufRead, BufReader, BufWriter, Write};
use std::path::{Path, PathBuf};

const MIN_CHARS: usize = 120;
const CHUNK_CHARS: usize = 2400;

fn normalize(text: &str) -> String {
    let text = text.replace("\r\n", "\n").replace('\r', "\n");
    let mut lines = Vec::new();
    for line in text.lines() {
        let clean: String = line
            .chars()
            .filter(|ch| !ch.is_control() || *ch == '\n' || *ch == '\t')
            .collect();
        lines.push(clean.split_whitespace().collect::<Vec<_>>().join(" "));
    }
    let mut result = lines.join("\n");
    while result.contains("\n\n\n") {
        result = result.replace("\n\n\n", "\n\n");
    }
    result.trim().to_owned()
}

fn chunks(text: &str) -> Vec<String> {
    let mut result = Vec::new();
    let mut current = String::new();
    for paragraph in text.split("\n\n").map(str::trim).filter(|p| !p.is_empty()) {
        if !current.is_empty() && current.chars().count() + paragraph.chars().count() + 2 > CHUNK_CHARS {
            result.push(std::mem::take(&mut current));
        }
        if !current.is_empty() {
            current.push_str("\n\n");
        }
        current.push_str(paragraph);
    }
    if !current.is_empty() {
        result.push(current);
    }
    result
}

fn files(path: &Path, output: &Path, result: &mut Vec<PathBuf>) -> std::io::Result<()> {
    if path == output {
        return Ok(());
    }
    if path.is_file() {
        if matches!(path.extension().and_then(|e| e.to_str()), Some("txt" | "md" | "json" | "jsonl")) {
            result.push(path.to_owned());
        }
        return Ok(());
    }
    for entry in fs::read_dir(path)? {
        let entry = entry?;
        let name = entry.file_name();
        if name.to_string_lossy().starts_with('.') {
            continue;
        }
        files(&entry.path(), output, result)?;
    }
    Ok(())
}

fn metadata(value: &serde_json::Value, key: &str, default: &str) -> String {
    value.get(key).and_then(|v| v.as_str()).unwrap_or(default).to_owned()
}

fn emit(text: &str, metadata_value: &serde_json::Value, path: &Path, seen: &mut HashSet<String>, out: &mut BufWriter<File>, accepted: &mut usize, duplicate: &mut usize, rejected: &mut usize) -> Result<(), Box<dyn std::error::Error>> {
    let normalized = normalize(text);
    for chunk in chunks(&normalized) {
        if chunk.chars().count() < MIN_CHARS {
            *rejected += 1;
            continue;
        }
        let mut digest = Sha256::new();
        digest.update(chunk.as_bytes());
        let hash = format!("{:x}", digest.finalize());
        if !seen.insert(hash.clone()) {
            *duplicate += 1;
            continue;
        }
        let record = serde_json::json!({
            "id": format!("doc-{}", &hash[..16]),
            "text": chunk,
            "source": metadata(metadata_value, "source", "user-local"),
            "license": metadata(metadata_value, "license", "unknown-review-required"),
            "language": metadata(metadata_value, "language", "pt-BR"),
            "category": metadata(metadata_value, "category", "general"),
            "path": path.to_string_lossy(),
            "sha256": hash,
        });
        writeln!(out, "{}", serde_json::to_string(&record)?)?;
        *accepted += 1;
    }
    Ok(())
}

fn process_file(path: &Path, seen: &mut HashSet<String>, out: &mut BufWriter<File>, accepted: &mut usize, duplicate: &mut usize, rejected: &mut usize) -> Result<(), Box<dyn std::error::Error>> {
    let extension = path.extension().and_then(|e| e.to_str()).unwrap_or_default();
    if extension == "jsonl" {
        for line in BufReader::new(File::open(path)?).lines() {
            let line = line?;
            if line.trim().is_empty() { continue; }
            let value: serde_json::Value = serde_json::from_str(&line)?;
            if let Some(text) = value.get("text").or_else(|| value.get("content")).and_then(|v| v.as_str()) {
                emit(text, &value, path, seen, out, accepted, duplicate, rejected)?;
            } else {
                *rejected += 1;
            }
        }
    } else {
        let text = fs::read_to_string(path)?;
        emit(&text, &serde_json::Value::Null, path, seen, out, accepted, duplicate, rejected)?;
    }
    Ok(())
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let input = PathBuf::from(env::args().nth(1).unwrap_or_else(|| "corpus/raw".into()));
    let output = PathBuf::from(env::args().nth(2).unwrap_or_else(|| "corpus/clean/knowledge_rust.jsonl".into()));
    if let Some(parent) = output.parent() { fs::create_dir_all(parent)?; }
    let mut paths = Vec::new();
    files(&input, &output, &mut paths)?;
    paths.sort();
    let mut out = BufWriter::new(File::create(&output)?);
    let mut seen = HashSet::new();
    let mut accepted = 0;
    let mut duplicate = 0;
    let mut rejected = 0;
    for path in &paths {
        process_file(path, &mut seen, &mut out, &mut accepted, &mut duplicate, &mut rejected)?;
    }
    out.flush()?;
    println!("arquivos: {}", paths.len());
    println!("documentos aceitos: {accepted}");
    println!("duplicatas: {duplicate}");
    println!("rejeitados: {rejected}");
    println!("saída: {}", output.display());
    Ok(())
}
