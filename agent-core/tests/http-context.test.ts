import assert from "node:assert/strict";
import test from "node:test";
import {readFileSync} from "node:fs";
import {relativeJsonEndpoints} from "../src/http-context.ts";

test("follows the actual application's wrapper from call site to fetch", () => {
  const source = readFileSync(new URL("./fixtures/relative-api-wrapper/index.html", import.meta.url), "utf8");
  assert.deepEqual(relativeJsonEndpoints(source), ["/api/tasks", "/api/tasks/"]);
});

test("works with renamed helpers and routes outside /api", () => {
  assert.deepEqual(relativeJsonEndpoints(`
    async function requestJson(resource, method = 'GET') {
      const response = await fetch(resource, {method});
      return response.json();
    }
    requestJson('/orders');
  `), ["/orders"]);
});

test("does not borrow fetch from an unrelated function or incomplete body", () => {
  assert.deepEqual(relativeJsonEndpoints(`
    function render(path) { return path; }
    async function load(path) { return (await fetch(path)).json(); }
    render('/orders');
  `), []);
  assert.deepEqual(relativeJsonEndpoints(`async function load(path) {
    return (await fetch(path)).json(); load('/orders');`), []);
});
