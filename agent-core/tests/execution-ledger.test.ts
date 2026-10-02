import assert from "node:assert/strict";
import test from "node:test";
import {ExecutionLedger} from "../src/execution-ledger.ts";

test("observações repetidas têm identidade pelos argumentos e aceitam entradas novas", () => {
  const ledger = new ExecutionLedger();
  const call = {tool: "calculate" as const, arguments: {expression: "x+y", variables: {x: 7, y: 13}}};
  ledger.observe(call, true);
  assert.equal(ledger.wouldRepeat({tool: "calculate", arguments: {variables: {y: 13, x: 7}, expression: "x+y"}}), true);
  assert.equal(ledger.wouldRepeat({...call, arguments: {...call.arguments, variables: {x: 11, y: 13}}}), false);
});

test("uma falha admite nova tentativa, duas falhas idênticas exigem outra rota", () => {
  const ledger = new ExecutionLedger();
  const call = {tool: "read_file" as const, arguments: {path: "logic.py"}};
  ledger.observe(call, false);
  assert.equal(ledger.wouldRepeat(call), false);
  ledger.observe(call, false);
  assert.equal(ledger.wouldRepeat(call), true);
});

test("mudanças e retomadas invalidam observações; consultas de processo continuam vivas", () => {
  const read = {tool: "read_file" as const, arguments: {path: "logic.py"}, ok: true};
  const ledger = new ExecutionLedger([read]);
  assert.equal(ledger.wouldRepeat(read), true);
  ledger.invalidate();
  assert.equal(ledger.wouldRepeat(read), false);
  const resumed = new ExecutionLedger([read, {tool: "create_directory", arguments: {path: "lib"}, ok: false}]);
  assert.equal(resumed.wouldRepeat(read), false);
  const poll = {tool: "process_status" as const, arguments: {process_id: "worker"}};
  resumed.observe(poll, true);
  assert.equal(resumed.wouldRepeat(poll), false);
  const sources = {tool: "list_sources" as const, arguments: {}};
  resumed.observe(sources, true);
  assert.equal(resumed.wouldRepeat(sources), false);
});
