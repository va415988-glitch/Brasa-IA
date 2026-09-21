"""Avaliação estrutural da capacidade de geração longa e contínua."""

import argparse
import json
from pathlib import Path

from checkpoint_io import load_checkpoint
from context_policy import PROFESSIONAL_GENERATION_TARGET, inspect_context
from promote_checkpoint import audit as promotion_audit


def audit(path: Path) -> dict:
    checkpoint = load_checkpoint(path)
    policy = inspect_context(checkpoint.get("config"))
    gates = {
        "context_minimum": policy["production_eligible"],
        "generation_target_configured": policy["generation_policy"]["configured_tokens"] >= PROFESSIONAL_GENERATION_TARGET,
        "checkpoint_complete": checkpoint.get("status") != "partial",
        "trained": int(checkpoint.get("steps") or 0) > 0,
    }
    release = promotion_audit(path)
    return {
        "schema": "long-generation-evaluation/v1",
        "checkpoint": str(path),
        "context": policy,
        "generation_target": PROFESSIONAL_GENERATION_TARGET,
        "gates": gates,
        "structural_ready": all(gates.values()),
        "eligible_for_runtime": release['promotable'],
        "release_blocked_reasons": release['blocked_reasons'],
        "mode": "structural-preflight",
        "next": "eligible for promotion" if release['promotable'] else "satisfy structural and behavioral gates before promotion",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("checkpoint")
    args = parser.parse_args()
    report = audit(Path(args.checkpoint))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report["eligible_for_runtime"] else 1)


if __name__ == "__main__":
    main()
