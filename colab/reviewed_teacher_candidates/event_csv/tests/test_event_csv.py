from __future__ import annotations

import csv
import json
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import tempfile
import unittest

from event_csv.cli import main
from event_csv.validator import process_events, validate_and_parse


def event(event_id: int, timestamp: str, **extra: object) -> str:
    return json.dumps({"id": event_id, "timestamp": timestamp, **extra}, ensure_ascii=False)


class ValidatorTests(unittest.TestCase):
    def test_empty_input(self) -> None:
        self.assertEqual(process_events([]), ([], []))

    def test_invalid_json_is_reported_and_discarded(self) -> None:
        events, errors = process_events(['{"id": 1'])
        self.assertEqual(events, [])
        self.assertEqual(errors, [{"line": 1, "error": "invalid_json"}])

    def test_non_object_json_is_reported_instead_of_crashing(self) -> None:
        for raw in ("null", "[]", "42", '"text"'):
            with self.subTest(raw=raw):
                record, error = validate_and_parse(raw)
                self.assertIsNone(record)
                self.assertEqual(error, "expected_json_object")

    def test_missing_or_non_integer_id_is_rejected(self) -> None:
        for raw in (
            '{"timestamp":"2026-01-01T00:00:00Z"}',
            '{"id":true,"timestamp":"2026-01-01T00:00:00Z"}',
            '{"id":"1","timestamp":"2026-01-01T00:00:00Z"}',
        ):
            with self.subTest(raw=raw):
                _, error = validate_and_parse(raw)
                self.assertEqual(error, "id_must_be_integer")

    def test_missing_invalid_and_naive_timestamps_are_rejected(self) -> None:
        rows = [
            '{"id":1}',
            '{"id":2,"timestamp":"not-a-date"}',
            '{"id":3,"timestamp":"2026-01-01T10:00:00"}',
        ]
        events, errors = process_events(rows)
        self.assertEqual(events, [])
        self.assertEqual([item["error"] for item in errors], [
            "timestamp_must_be_timezone_aware_iso8601",
            "timestamp_must_be_timezone_aware_iso8601",
            "timestamp_must_be_timezone_aware_iso8601",
        ])

    def test_duplicate_ids_are_discarded(self) -> None:
        rows = [
            event(1, "2026-01-01T00:00:00Z"),
            event(1, "2026-01-02T00:00:00Z"),
        ]
        events, errors = process_events(rows)
        self.assertEqual([item["id"] for item in events], [1])
        self.assertEqual(errors, [{"line": 2, "error": "duplicate_id"}])

    def test_sorting_compares_instants_across_time_zones(self) -> None:
        rows = [
            event(1, "2026-01-01T01:00:00+02:00"),  # 23:00Z on the previous day
            event(2, "2026-01-01T00:00:00Z"),
        ]
        events, errors = process_events(rows)
        self.assertEqual(errors, [])
        self.assertEqual([item["id"] for item in events], [1, 2])


class CliTests(unittest.TestCase):
    def test_cli_writes_escaped_utf8_csv_sorted_by_time(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "events.jsonl"
            output_path = root / "events.csv"
            rows = [
                event(2, "2026-01-01T00:00:00,123Z"),
                event(1, "2025-12-31T23:00:00Z"),
            ]
            input_path.write_text("\n".join(rows) + "\n", encoding="utf-8")

            status = main(["--input", str(input_path), "--output", str(output_path)])

            self.assertEqual(status, 0)
            with output_path.open(encoding="utf-8", newline="") as stream:
                parsed = list(csv.DictReader(stream))
            self.assertEqual([row["id"] for row in parsed], ["1", "2"])
            self.assertEqual(parsed[1]["timestamp"], "2026-01-01T00:00:00,123Z")

    def test_cli_returns_one_for_partial_success_and_prints_error_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "events.jsonl"
            output_path = root / "events.csv"
            input_path.write_text(
                event(1, "2026-01-01T00:00:00Z") + "\nnot-json\n",
                encoding="utf-8",
            )
            output = StringIO()
            with redirect_stdout(output):
                status = main(["--input", str(input_path), "--output", str(output_path)])
            self.assertEqual(status, 1)
            self.assertTrue(output_path.exists())
            self.assertIn("Linhas descartadas: 1", output.getvalue())
            self.assertIn("invalid_json: 1", output.getvalue())


if __name__ == "__main__":
    unittest.main()
