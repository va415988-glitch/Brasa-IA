"""Apply technical screening decisions to verified coding episodes.

The resulting JSONL remains an episode corpus, separate from conversational
SFT. This command never marks an episode as ready for model training.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    raw = args.candidates.read_bytes()
    registry_raw = args.registry.read_bytes()
    registry = json.loads(registry_raw)
    if registry.get("schema") != "brasa-episode-review-registry/v1":
        raise ValueError("Unexpected review registry schema")
    if digest(raw) != registry.get("candidate_file_sha256"):
        raise ValueError("Candidate file differs from the reviewed source")
    decisions = registry.get("decisions")
    if not isinstance(decisions, list):
        raise ValueError("Review decisions are missing")
    by_id = {}
    for decision in decisions:
        task_id = decision.get("source_task_id")
        if task_id in by_id or decision.get("decision") not in ("retain_for_human_review", "exclude"):
            raise ValueError("Duplicate task or unsupported decision")
        by_id[task_id] = decision

    records = []
    seen = set()
    for line in raw.decode("utf-8").splitlines():
        if not line.strip():
            continue
        episode = json.loads(line)
        task_id = episode.get("source_task_id")
        if episode.get("schema") != "brasa-coding-episode-candidate/v1" or task_id in seen:
            raise ValueError("Invalid or duplicate episode")
        if episode.get("review") != {"human_reviewed": False, "safe_to_train": False}:
            raise ValueError("Candidate has unexpected approval flags")
        seen.add(task_id)
        decision = by_id.get(task_id)
        if decision is None or digest(line.encode("utf-8")) != decision.get("candidate_record_sha256"):
            raise ValueError("Episode does not match its reviewed record")
        if decision["decision"] == "retain_for_human_review":
            episode["review"] = {"human_reviewed": False, "safe_to_train": False,
                                 "technical_review": "passed", "decision": "retained_for_human_review"}
            records.append(episode)
    if seen != set(by_id):
        raise ValueError("Registry and candidate file cover different tasks")

    args.output_dir.mkdir(parents=True, exist_ok=False)
    output = args.output_dir / "episodes_for_human_review.jsonl"
    output.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in records), encoding="utf-8")
    manifest = {
        "schema": "brasa-curated-coding-episodes/v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_candidate_sha256": digest(raw),
        "review_registry_sha256": digest(registry_raw),
        "episodes_for_human_review": len(records),
        "excluded": len(seen) - len(records),
        "training_status": "not_ready_requires_human_approval_and_episode_to_training_format",
        "output_sha256": digest(output.read_bytes()),
    }
    (args.output_dir / "MANIFEST.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output_dir), "for_human_review": len(records),
                      "excluded": len(seen) - len(records)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
