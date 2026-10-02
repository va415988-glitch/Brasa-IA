"""Export small, review-only coding episodes from the local Brasa task history.

Usage:
    python3 colab/export_verified_episodes_v001.py --output /tmp/brasa-episodes-v001.jsonl

The output is deliberately NOT a training dataset. Review code, provenance,
privacy, and overlap with evaluation tasks before granting training approval.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path


SCHEMA = "brasa-coding-episode-candidate/v1"


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def traces_by_task(path: Path, task_ids: set[str]) -> dict[str, list[dict]]:
    owners = {}
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            if not line.strip():
                continue
            event = json.loads(line)
            trace_id = event.get("trace_id")
            if not isinstance(trace_id, str):
                continue
            if event.get("event") == "turn_started":
                request_id = event.get("request_id", "")
                if isinstance(request_id, str) and request_id.startswith("task-") and "-cycle-" in request_id:
                    owner = request_id.split("-cycle-", 1)[0]
                    if owner in task_ids:
                        owners[trace_id] = owner
    grouped = defaultdict(list)
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            if not line.strip():
                continue
            event = json.loads(line)
            owner = owners.get(event.get("trace_id"))
            if owner:
                grouped[owner].append(event)
    return grouped


def artifact_snapshots(task_dir: Path) -> list[dict]:
    base = task_dir / "sections" / "artifacts"
    index = read_json(base / "index.json")
    snapshots = []
    for item in index:
        name = item.get("storedFile", "")
        path = item.get("path", "")
        if not name or Path(name).name != name or not path or Path(path).is_absolute() or ".." in Path(path).parts:
            raise ValueError("Invalid artifact path")
        content = (base / name).read_text(encoding="utf-8")
        encoded = content.encode("utf-8")
        if len(encoded) != item.get("bytes"):
            raise ValueError("Artifact byte count mismatch")
        snapshots.append({"path": path, "language": item.get("language", ""),
                          "sha256": hashlib.sha256(encoded).hexdigest(), "content": content})
    return snapshots


def verified_episode(task_dir: Path, trace_events: list[dict]) -> tuple[dict | None, str]:
    task = read_json(task_dir / "task.json")
    report = read_json(task_dir / "sections" / "delivery" / "report.json")
    verification = report.get("verification") or {}
    if task.get("status") != "completed" or report.get("status") != "completed":
        return None, "not_completed"
    if verification.get("executed") is not True or verification.get("passed") is not True:
        return None, "report_not_verified"
    if task.get("request", {}).get("objective") not in ("build", "debug", "auto"):
        return None, "not_coding"

    stored_events = []
    for line in (task_dir / "events.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            stored_events.append(json.loads(line)["event"])
    kinds = {event.get("kind") for event in stored_events}
    if "mutation.effect.observed" not in kinds or "artifact.snapshot.saved" not in kinds:
        return None, "no_observed_write"

    snapshots = artifact_snapshots(task_dir)
    if not snapshots:
        return None, "no_artifact_snapshot"
    ordered = sorted(trace_events, key=lambda event: event.get("timestamp", ""))
    actions = []
    writes = set()
    check = None
    before = []
    selected_calls = {}
    for event in ordered:
        if event.get("event") == "plan_selected" and event.get("tool") in ("apply_batch", "project_checks"):
            arguments = event.get("arguments")
            if isinstance(arguments, dict):
                selected_calls[event["tool"]] = arguments
            continue
        if event.get("event") != "tool_result" or event.get("ok") is not True:
            continue
        tool = event.get("tool")
        data = event.get("data") or {}
        if tool == "read_file" and isinstance(data, dict):
            path = data.get("path")
            content = data.get("content")
            if isinstance(path, str) and isinstance(content, str) and not Path(path).is_absolute():
                before.append({"path": path, "content": content, "truncated": bool(data.get("truncated"))})
        elif tool == "apply_batch" and isinstance(data, dict):
            changed = []
            for operation in data.get("operations", []):
                if not isinstance(operation, dict) or operation.get("tool") not in ("create_file", "edit_file"):
                    continue
                result = operation.get("result") or {}
                path = result.get("path")
                if isinstance(path, str) and not Path(path).is_absolute():
                    writes.add(path)
                    changed.append({"tool": operation["tool"], "path": path})
            if data.get("ok") is True and changed:
                arguments = selected_calls.pop("apply_batch", None)
                planned = arguments.get("operations") if isinstance(arguments, dict) else None
                planned_paths = [op.get("arguments", {}).get("path") for op in planned
                                 if isinstance(op, dict) and isinstance(op.get("arguments"), dict)] if isinstance(planned, list) else []
                if planned_paths != [op["path"] for op in changed]:
                    return None, "planned_write_mismatch"
                # A check only proves the state that existed when it ran.
                check = None
                actions.append({"tool": "apply_batch", "arguments": arguments, "observed_operations": changed})
        elif tool == "project_checks" and isinstance(data, dict):
            if writes and data.get("executed") is True and data.get("passed") is True and data.get("exit_code") == 0:
                arguments = selected_calls.pop("project_checks", None)
                if not isinstance(arguments, dict) or not isinstance(arguments.get("check"), str):
                    return None, "planned_check_mismatch"
                check = {key: data.get(key) for key in ("check", "command", "exit_code", "stdout", "stderr", "truncated")}
                actions.append({"tool": "project_checks", "arguments": arguments,
                                "observed_exit_code": data.get("exit_code")})
    if not writes or writes != {item["path"] for item in snapshots}:
        return None, "write_snapshot_mismatch"
    before = [item for item in before if item["path"] in writes]
    if check is None:
        return None, "no_observed_passing_check"
    if check.get("truncated"):
        return None, "check_output_truncated"

    request = task["request"]
    prompt = request.get("prompt", "")
    if not isinstance(prompt, str) or not prompt.strip():
        return None, "missing_prompt"
    workspace = request.get("workspaceRoot", "")
    return {
        "schema": SCHEMA,
        "source_task_id": task_dir.name,
        "origin": "temporary_smoke_workspace" if isinstance(workspace, str) and workspace.startswith("/tmp/") else "user_workspace",
        "review": {"human_reviewed": False, "safe_to_train": False},
        "request": prompt,
        "before_files": before,
        "actions": actions,
        "after_files": snapshots,
        "verification": check,
        "outcome": "completed_with_observed_write_and_passing_check",
    }, "candidate"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks", type=Path, default=Path(".agent-state/tasks"))
    parser.add_argument("--traces", type=Path, default=Path("logs/agent_traces.jsonl"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    task_dirs = [directory for directory in sorted(args.tasks.glob("task-*"))
                 if (directory / "task.json").is_file()
                 and (directory / "sections/delivery/report.json").is_file()]
    grouped = traces_by_task(args.traces, {directory.name for directory in task_dirs})
    counts = defaultdict(int)
    candidates = []
    for task_dir in task_dirs:
        try:
            episode, reason = verified_episode(task_dir, grouped.get(task_dir.name, []))
        except (ValueError, KeyError, OSError, json.JSONDecodeError):
            episode, reason = None, "invalid_record"
        counts[reason] += 1
        if episode is not None:
            candidates.append(episode)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as output:
        for candidate in candidates:
            output.write(json.dumps(candidate, ensure_ascii=False) + "\n")
    print(json.dumps({"output": str(args.output), "candidates": len(candidates),
                      "reasons": dict(sorted(counts.items()))}, ensure_ascii=False))


if __name__ == "__main__":
    main()
