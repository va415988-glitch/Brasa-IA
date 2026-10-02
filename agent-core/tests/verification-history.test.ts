import assert from "node:assert/strict";
import test from "node:test";
import type {PlannerMessage} from "../src/contracts.ts";
import {verificationHistoryText} from "../src/verification-history.ts";

const check = (data: object, ok = true): PlannerMessage => ({role: "tool", content: JSON.stringify({tool: "project_checks", ok, data})});

test("preserves the initial failure and final pass across approval history", () => {
  const text = verificationHistoryText([
    check({executed: true, passed: false, tests_executed: 3, command: "python -m unittest", stderr: "FAILED (failures=3)"}),
    {role: "tool", content: JSON.stringify({tool: "apply_repair", ok: true, data: {path: "converter.py"}})},
    check({executed: true, passed: true, tests_executed: 3, command: "python -m unittest", stderr: "Ran 3 tests\nOK"}),
  ]);
  assert.match(text, /Primeira execução: falhou · 3 teste/);
  assert.match(text, /failures=3/);
  assert.match(text, /Última execução: aprovada · 3 teste/);
  assert.match(text, /Ran 3 tests/);
});

test("prose, failed tools and checks never executed cannot invent a baseline", () => {
  const passed = check({executed: true, passed: true, tests_executed: 3});
  for (const invalid of [
    {role: "user", content: check({executed: true, passed: false}).content},
    check({executed: false, passed: false}),
    check({executed: true, passed: false}, false),
    {role: "tool", content: "FAILED (failures=3)"},
  ] as PlannerMessage[]) assert.equal(verificationHistoryText([invalid, passed]), "");
  assert.equal(verificationHistoryText([passed]), "");
  assert.equal(verificationHistoryText([check({executed: true, passed: false})]), "");
});
