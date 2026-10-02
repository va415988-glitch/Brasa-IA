import json
import subprocess
import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))

from fullstack_gate import assess_fullstack_plan
from proactive_implementation import parse_implementation_plan


FULLSTACK_PLAN = {
    "assumptions": ["Node.js nativo, API HTTP local e persistência JSON para manter o fixture reproduzível."],
    "operations": [
        {"tool": "create_file", "arguments": {"path": "package.json", "content": '{"type":"module","scripts":{"test":"node --test tests/api.test.mjs"}}\n'}},
        {"tool": "create_file", "arguments": {"path": "public/index.html", "content": '<!doctype html><main><h1>Pedidos</h1><form id="order-form"><input name="name" required><button>Salvar</button></form><p id="status" role="status"></p></main>\n'}},
        {"tool": "create_file", "arguments": {"path": "server.js", "content": r'''import {createServer} from "node:http";
import {readFile, writeFile} from "node:fs/promises";
import {resolve} from "node:path";

const database = resolve(process.env.DATA_FILE || "orders.json");
async function readOrders() { try { return JSON.parse(await readFile(database, "utf8")); } catch (error) { if (error.code === "ENOENT") return []; throw error; } }
async function writeOrders(orders) { await writeFile(database, JSON.stringify(orders) + "\n"); }
export function createApp() {
  return createServer(async (request, response) => {
    try {
      if (request.method === "GET" && request.url === "/api/orders") { response.setHeader("content-type", "application/json"); response.end(JSON.stringify(await readOrders())); return; }
      if (request.method === "POST" && request.url === "/api/orders") {
        let raw = ""; for await (const chunk of request) raw += chunk;
        const body = JSON.parse(raw); if (!body.name || typeof body.name !== "string") { response.writeHead(400); response.end(JSON.stringify({error: "name obrigatório"})); return; }
        const orders = await readOrders(); const order = {id: orders.length + 1, name: body.name}; orders.push(order); await writeOrders(orders);
        response.writeHead(201, {"content-type": "application/json"}); response.end(JSON.stringify(order)); return;
      }
      if (request.method === "GET" && request.url === "/") { response.setHeader("content-type", "text/html"); response.end(await readFile("public/index.html")); return; }
      response.writeHead(404); response.end(JSON.stringify({error: "not found"}));
    } catch (error) { response.writeHead(500, {"content-type": "application/json"}); response.end(JSON.stringify({error: "internal"})); }
  });
}
'''}},
        {"tool": "create_file", "arguments": {"path": "tests/api.test.mjs", "content": r'''import assert from "node:assert/strict";
import {mkdtemp, readFile} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join} from "node:path";
import test from "node:test";

const root = await mkdtemp(join(tmpdir(), "fullstack-fixture-"));
process.env.DATA_FILE = join(root, "orders.json");
const {createApp} = await import("../server.js");
const server = createApp(); await new Promise(resolve => server.listen(0, resolve));
const base = `http://127.0.0.1:${server.address().port}`;
test.after(() => server.close());

test("frontend is served and empty state is reachable", async () => {
  const response = await fetch(base + "/"); const html = await response.text();
  assert.equal(response.status, 200); assert.match(html, /order-form/);
});
test("API rejects invalid input and persists valid order", async () => {
  const invalid = await fetch(base + "/api/orders", {method: "POST", headers: {"content-type": "application/json"}, body: "{}"});
  assert.equal(invalid.status, 400);
  const created = await fetch(base + "/api/orders", {method: "POST", headers: {"content-type": "application/json"}, body: JSON.stringify({name: "Ana"})});
  assert.equal(created.status, 201); assert.equal((await created.json()).name, "Ana");
  const listed = await fetch(base + "/api/orders"); assert.deepEqual(await listed.json(), [{id: 1, name: "Ana"}]);
  assert.match(await readFile(process.env.DATA_FILE, "utf8"), /Ana/);
});
'''}},
    ],
}


class FullstackHttpEndToEndTests(unittest.TestCase):
    def test_frontend_api_persistence_and_integration_work_together(self):
        plan = parse_implementation_plan(json.dumps(FULLSTACK_PLAN, ensure_ascii=False))
        assessment = assess_fullstack_plan(plan)
        self.assertTrue(assessment["passed"], assessment)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for operation in plan["operations"]:
                args = operation["arguments"]
                path = root / args["path"]
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(args["content"], encoding="utf-8")
            result = subprocess.run(["node", "--test", "tests/api.test.mjs"], cwd=root, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
