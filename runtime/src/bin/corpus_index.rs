//! Constrói o índice lexical local sem depender de Python.

use serde_json::{json, Value};
use std::collections::{HashMap, HashSet};
use std::env;
use std::fs::{self, File};
use std::io::{BufRead, BufReader, Write};

fn tokens(text: &str) -> Vec<String> {
    let mut result = Vec::new();
    let mut current = String::new();
    for ch in text.chars() {
        if ch.is_alphanumeric() {
            current.extend(ch.to_lowercase());
        } else if current.chars().count() >= 2 {
            result.push(std::mem::take(&mut current));
        } else {
            current.clear();
        }
    }
    if current.chars().count() >= 2 {
        result.push(current);
    }
    result
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let input = env::args().nth(1).unwrap_or_else(|| "corpus/clean/knowledge.jsonl".into());
    let output = env::args().nth(2).unwrap_or_else(|| "corpus/index/knowledge.json".into());
    let file = File::open(&input)?;
    let mut documents = Vec::new();
    let mut postings: HashMap<String, Vec<usize>> = HashMap::new();
    let mut document_frequency: HashMap<String, usize> = HashMap::new();

    for line in BufReader::new(file).lines() {
        let line = line?;
        if line.trim().is_empty() { continue; }
        let value: Value = serde_json::from_str(&line)?;
        let text = value.get("text").and_then(Value::as_str).unwrap_or_default().to_owned();
        let index = documents.len();
        documents.push(value);
        let unique: HashSet<String> = tokens(&text).into_iter().collect();
        for term in &unique {
            postings.entry(term.clone()).or_default().push(index);
            *document_frequency.entry(term.clone()).or_default() += 1;
        }
    }

    let total = documents.len().max(1) as f64;
    let idf: HashMap<String, f64> = document_frequency.into_iter()
        .map(|(term, frequency)| (term, ((1.0 + total) / (1.0 + frequency as f64)).ln() + 1.0))
        .collect();
    let index = json!({"documents": documents, "postings": postings, "idf": idf});
    let output_path = std::path::Path::new(&output);
    if let Some(parent) = output_path.parent() { fs::create_dir_all(parent)?; }
    let mut file = File::create(output_path)?;
    file.write_all(serde_json::to_string(&index)?.as_bytes())?;
    println!("documentos indexados: {}", index["documents"].as_array().map(Vec::len).unwrap_or(0));
    println!("termos indexados: {}", index["postings"].as_object().map(|map| map.len()).unwrap_or(0));
    println!("índice: {output}");
    Ok(())
}
