import json
import math
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def selection_accuracy(rows):
    if not rows:
        return 0.0
    correct = sum(1 for row in rows if row.get("expected") == row.get("actual"))
    return correct / len(rows)


def keyword_hit_rate(rows):
    if not rows:
        return 0.0
    scores = []
    for row in rows:
        answer = (row.get("answer") or "").lower()
        required = row.get("required") or []
        if not required:
            scores.append(1.0)
            continue
        hits = sum(1 for term in required if term.lower() in answer)
        score = 1.0 if hits == len(required) else hits / len(required)
        scores.append(score)
    return sum(scores) / len(scores)


def parse_task_file(path):
    rows = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rows.append(json.loads(line))
    return rows


def evaluate_tool_selection(report_path):
    rows = parse_task_file(report_path)
    return {
        "selection_accuracy": selection_accuracy(rows),
        "sample_size": len(rows),
    }


def latency_summary(rows):
    if not rows:
        return {"p50": 0.0, "p95": 0.0, "max": 0.0}
    values = sorted(float(row.get("elapsed_ms", 0.0)) for row in rows)

    def pct(p):
        idx = min(len(values) - 1, max(0, int(math.ceil(p * len(values))) - 1))
        return values[idx]

    return {"p50": pct(0.50), "p95": pct(0.95), "max": max(values)}


def category_summary(rows):
    by_category = defaultdict(list)
    for row in rows:
        by_category[row.get("category", "uncategorized")].append(row)

    summary = {}
    for category, cat_rows in sorted(by_category.items()):
        tool_ok = sum(1 for row in cat_rows if row.get("tool_ok", False))
        answers = [float(row.get("answer_score", 0.0)) for row in cat_rows]
        summary[category] = {
            "count": len(cat_rows),
            "tool_accuracy": round(tool_ok / len(cat_rows), 3) if cat_rows else 0.0,
            "answer_quality_avg": round(sum(answers) / len(answers), 3) if answers else 0.0,
        }
    return summary


def compare_reports(report_paths, labels=None):
    """Compara itens de benchmark em ordem de qualidade."""
    entries = []
    for index, path in enumerate(report_paths):
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        label = (labels or [None] * len(report_paths))[index] or Path(path).stem
        metric = data.get("tool_accuracy") if isinstance(data.get("tool_accuracy"), (int, float)) else 0.0
        answer = data.get("answer_quality_avg") if isinstance(data.get("answer_quality_avg"), (int, float)) else 0.0
        latency = data.get("latency_ms") or {}
        entries.append({
            "label": label,
            "tool_accuracy": float(metric),
            "answer_quality_avg": float(answer),
            "latency_ms": latency,
            "case_count": int(data.get("case_count", 0) or 0),
            "categories": data.get("categories", {}),
        })
    entries.sort(key=lambda item: (item["tool_accuracy"], item["answer_quality_avg"]), reverse=True)
    return entries


def evaluate_jsonl(path):
    rows = parse_task_file(path)
    overview = {
        "count": len(rows),
        "selection_accuracy": selection_accuracy(rows),
        "keyword_hit_rate": keyword_hit_rate(rows),
        "latency_ms": latency_summary(rows),
    }
    return overview


def command_summary(results):
    stats = defaultdict(int)
    for result in results:
        stats[result.get("tool", "unknown")] += 1
    return dict(sorted(stats.items()))
