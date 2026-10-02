import assert from "node:assert/strict";
import {readFile, readdir} from "node:fs/promises";
import {join} from "node:path";
import test from "node:test";
import {runtimeCapabilities} from "../src/capability-registry.ts";

test("AgentCore e catálogo JSON expõem os mesmos nomes de ferramentas", async () => {
  const directory = join(import.meta.dirname, "..", "..", "contracts");
  const files = (await readdir(directory)).filter((name) => name.endsWith(".json"));
  const names = await Promise.all(files.map(async (name) => {
    const contract = JSON.parse(await readFile(join(directory, name), "utf8")) as {name?: string};
    assert.equal(typeof contract.name, "string", name);
    return contract.name;
  }));
  assert.deepEqual([...new Set(names)].sort(), Object.keys(runtimeCapabilities).sort());
});
