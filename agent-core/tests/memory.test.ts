import assert from "node:assert/strict";
import {mkdtempSync, rmSync} from "node:fs";
import test from "node:test";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {FileTaskMemory, LocalTaskMemory, validateMemoryInput} from "../src/index.ts";

test("atualiza memórias pela chave, pesquisa por relevância e restaura snapshot", async () => {
  let tick = 0;
  const memory = new LocalTaskMemory(10, () => "2026-09-21T00:00:0" + tick++ + "Z");
  const first = await memory.remember({
    kind: "decision",
    key: "linguagem",
    value: "TypeScript no núcleo e Rust no runtime",
    source: "arquitetura",
    confidence: 0.95,
  });
  await memory.remember({
    kind: "evidence",
    key: "typescript",
    value: "Contratos tipados para ferramentas.",
    source: "https://example.test/typescript",
    confidence: 0.7,
  });
  const updated = await memory.remember({
    kind: "decision",
    key: "linguagem",
    value: "TypeScript no núcleo, Rust no runtime e Python no treinamento",
    source: "arquitetura-revisada",
    confidence: 0.99,
  });

  assert.equal(first.id, updated.id);
  assert.equal((await memory.search("Python treinamento"))[0]?.id, first.id);
  assert.equal(memory.byKind("decision").length, 1);

  const restored = new LocalTaskMemory();
  restored.restore(memory.snapshot());
  assert.equal((await restored.search("runtime Rust"))[0]?.value, updated.value);
  assert.equal(restored.byKind("evidence").length, 1);
});

test("limita o número de entradas e mantém as mais recentes", async () => {
  let tick = 0;
  const memory = new LocalTaskMemory(2, () => "2026-09-21T00:00:0" + tick++ + "Z");
  await memory.remember({kind: "observation", key: "um", value: "antiga"});
  await memory.remember({kind: "observation", key: "dois", value: "média"});
  await memory.remember({kind: "observation", key: "três", value: "nova"});

  assert.equal(memory.byKind("observation").length, 2);
  assert.equal((await memory.search("antiga")).length, 0);
  assert.equal((await memory.search("nova"))[0]?.key, "três");
});

test("não registra credenciais nem confiança fora do contrato", async () => {
  const memory = new LocalTaskMemory();

  await assert.rejects(
    memory.remember({kind: "decision", key: "configuração", value: "api_key=sk-123456789012345678901234"}),
    /possível segredo/,
  );
  await assert.rejects(
    memory.remember({kind: "decision", key: "confiança", value: "dado", confidence: 1.5}),
    /entre 0 e 1/,
  );
  assert.throws(
    () => validateMemoryInput({kind: "observation", key: "", value: "dado"}),
    /precisa de uma chave/,
  );
});

test("persiste somente memórias já validadas e restaura após reinício", async () => {
  const directory = mkdtempSync(join(tmpdir(), "ia-agent-memory-"));
  const filePath = join(directory, "memory.json");
  try {
    const first = new FileTaskMemory(filePath);
    await first.remember({kind: "decision", key: "stack", value: "TypeScript e Rust"});
    await assert.rejects(
      first.remember({kind: "decision", key: "segredo", value: "password=nao-guardar"}),
      /possível segredo/,
    );

    const restored = new FileTaskMemory(filePath);
    assert.equal((await restored.search("TypeScript Rust"))[0]?.value, "TypeScript e Rust");
    assert.equal((await restored.search("nao-guardar")).length, 0);
  } finally {
    rmSync(directory, {recursive: true, force: true});
  }
});
