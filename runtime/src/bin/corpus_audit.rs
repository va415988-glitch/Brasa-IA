//! Auditor rápido do corpus JSONL. A transformação completa virá depois deste
//! estágio; por enquanto ele mede qualidade sem alterar os arquivos originais.

use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet, HashSet};
use std::env;
use std::fs::File;
use std::io::{BufRead, BufReader};

fn license_needs_review(license: &str) -> bool {
    let normalized = license.trim().to_ascii_lowercase();
    normalized.is_empty()
        || normalized.contains("unknown")
        || normalized.contains("verify")
        || normalized.contains("check-per-source")
}

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
    let mut sources = BTreeSet::new();
    let mut licenses = BTreeMap::<String, usize>::new();
    let mut pending_license_review = 0usize;
    let mut missing_source = 0usize;

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
        let source = value.get("source").and_then(|value| value.as_str()).unwrap_or("").trim();
        if source.is_empty() {
            missing_source += 1;
        } else {
            sources.insert(source.to_owned());
        }
        let license = value.get("license").and_then(|value| value.as_str()).unwrap_or("").trim();
        let license_key = if license.is_empty() { "<missing>" } else { license };
        *licenses.entry(license_key.to_owned()).or_default() += 1;
        if license_needs_review(license) {
            pending_license_review += 1;
        }
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
    println!("fontes distintas: {}", sources.len());
    println!("registros sem fonte: {missing_source}");
    println!("licenças pendentes de revisão: {pending_license_review}");
    for (license, count) in licenses {
        println!("licença [{license}]: {count}");
    }
    if invalid > 0 || duplicated > 0 {
        std::process::exit(2);
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::license_needs_review;

    #[test]
    fn missing_and_unverified_licenses_need_review() {
        for license in ["", "unknown-review-required", "CC-BY-SA/GFDL (verify current terms)"] {
            assert!(license_needs_review(license), "{license}");
        }
    }

    #[test]
    fn declared_license_is_reported_without_automatic_approval() {
        assert!(!license_needs_review(" MIT "));
    }
}
