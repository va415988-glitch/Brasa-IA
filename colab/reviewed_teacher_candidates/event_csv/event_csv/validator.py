"""JSONL event validation and deterministic ordering."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def validate_and_parse(jsonl_line: str) -> tuple[dict[str, Any] | None, str | None]:
    """Return an event and no error, or None and a human-readable error."""
    try:
        event = json.loads(jsonl_line)
    except json.JSONDecodeError:
        return None, "invalid_json"

    if not isinstance(event, dict):
        return None, "expected_json_object"
    event_id = event.get("id")
    if type(event_id) is not int:
        return None, "id_must_be_integer"
    timestamp = event.get("timestamp")
    parsed_timestamp = _parse_timestamp(timestamp)
    if parsed_timestamp is None:
        return None, "timestamp_must_be_timezone_aware_iso8601"

    return {"id": event_id, "timestamp": timestamp, "_sort_timestamp": parsed_timestamp}, None


def process_events(jsonl_lines: list[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Validate lines, discard invalid or duplicate IDs, and sort by UTC instant."""
    valid_events: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    seen_ids: set[int] = set()

    for line_number, line in enumerate(jsonl_lines, start=1):
        event, error = validate_and_parse(line)
        if error:
            errors.append({"line": line_number, "error": error})
            continue
        assert event is not None
        event_id = event["id"]
        if event_id in seen_ids:
            errors.append({"line": line_number, "error": "duplicate_id"})
            continue
        seen_ids.add(event_id)
        valid_events.append(event)

    valid_events.sort(key=lambda event: event["_sort_timestamp"])
    for event in valid_events:
        del event["_sort_timestamp"]
    return valid_events, errors
