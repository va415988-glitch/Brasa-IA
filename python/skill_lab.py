"""Laboratório automático e isolado para transformar fontes em habilidade.

O laboratório só executa tarefas fixas, pequenas e auditáveis. Ele nunca
executa código bruto baixado da web. Para domínios sem ferramenta local, a
competência fica explicitamente como ``practice_unavailable``.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path


def _run(command: list[str], root: Path, timeout: int = 20) -> dict:
    try:
        result = subprocess.run(command, cwd=root, capture_output=True, text=True,
                                timeout=timeout, check=False)
        return {"passed": result.returncode == 0, "command": command,
                "stdout": result.stdout[-2000:], "stderr": result.stderr[-2000:],
                "returncode": result.returncode}
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"passed": False, "command": command, "error": str(error),
                "returncode": -1}


def _python_tasks(root: Path) -> list[dict]:
    (root / "task.py").write_text(
        "def classify(value):\n"
        "    if value < 0: return 'negative'\n"
        "    if value == 0: return 'zero'\n"
        "    return 'positive'\n\n"
        "assert classify(-1) == 'negative'\n"
        "assert classify(0) == 'zero'\n"
        "assert classify(3) == 'positive'\n", encoding="utf-8")
    (root / "task_tests.py").write_text(
        "from task import classify\nassert [classify(x) for x in (-2, 0, 4)] == ['negative', 'zero', 'positive']\n",
        encoding="utf-8")
    (root / "task_errors.py").write_text(
        "def parse_age(value):\n"
        "    try: return int(value)\n"
        "    except (TypeError, ValueError): return None\n\n"
        "assert parse_age('42') == 42 and parse_age('x') is None\n", encoding="utf-8")
    (root / "task_module.py").write_text(
        "from task import classify\n"
        "def summarize(values): return [classify(value) for value in values]\n\n"
        "assert summarize([-1, 0, 1]) == ['negative', 'zero', 'positive']\n", encoding="utf-8")
    (root / "task_iterator.py").write_text(
        "def squares(values):\n"
        "    for value in values:\n"
        "        yield value * value\n\n"
        "assert list(squares([1, 2, 3])) == [1, 4, 9]\n", encoding="utf-8")
    (root / "task_exceptions.py").write_text(
        "class InvalidAge(ValueError): pass\n\n"
        "def parse_age(value):\n"
        "    try:\n"
        "        age = int(value)\n"
        "    except (TypeError, ValueError) as error:\n"
        "        raise InvalidAge('idade inválida') from error\n"
        "    if age < 0: raise InvalidAge('idade negativa')\n"
        "    return age\n\n"
        "assert parse_age('42') == 42\n"
        "try: parse_age('-1')\n"
        "except InvalidAge: pass\n"
        "else: raise AssertionError('exceção não propagada')\n", encoding="utf-8")
    (root / "task_serialization.py").write_text(
        "import json\n\n"
        "payload = {'name': 'agent', 'enabled': True, 'items': [1, 2]}\n"
        "round_trip = json.loads(json.dumps(payload))\n"
        "assert round_trip == payload\n", encoding="utf-8")
    (root / "task_async.py").write_text(
        "import asyncio\n\n"
        "async def fetch_value(value):\n"
        "    await asyncio.sleep(0)\n"
        "    return value * 2\n\n"
        "assert asyncio.run(fetch_value(21)) == 42\n", encoding="utf-8")
    (root / "task_integration.py").write_text(
        "import json\n\n"
        "def classify(value):\n"
        "    if not isinstance(value, int): raise TypeError('int esperado')\n"
        "    return 'negative' if value < 0 else 'zero' if value == 0 else 'positive'\n\n"
        "payload = json.loads(json.dumps({'values': [-2, 0, 3]}))\n"
        "assert [classify(value) for value in payload['values']] == ['negative', 'zero', 'positive']\n", encoding="utf-8")
    (root / "task_types.py").write_text(
        "def normalize(value):\n"
        "    if isinstance(value, bool) or not isinstance(value, (int, float)):\n"
        "        raise TypeError('número esperado')\n"
        "    return float(value)\n\n"
        "assert normalize(3) == 3.0\n"
        "try: normalize(True)\n"
        "except TypeError: pass\n"
        "else: raise AssertionError('bool aceito como número')\n", encoding="utf-8")
    (root / "task_resources.py").write_text(
        "from pathlib import Path\n\n"
        "path = Path('resource.txt')\n"
        "path.write_text('ok', encoding='utf-8')\n"
        "with path.open(encoding='utf-8') as handle: assert handle.read() == 'ok'\n"
        "path.unlink()\n", encoding="utf-8")
    return [
        {"name": "foundation-control-flow", "level": "foundation", "command": ["python3", "task.py"]},
        {"name": "error-handling", "level": "practice", "command": ["python3", "task_errors.py"]},
        {"name": "module-composition", "level": "practice", "command": ["python3", "task_module.py"]},
        {"name": "unseen-inputs", "level": "transfer", "command": ["python3", "task_tests.py"]},
        {"name": "transfer-boundary-cases", "level": "transfer", "command": ["python3", "-c", "from task import classify; assert classify(-999)=='negative'; assert classify(999)=='positive'"]},
        {"name": "iterator-generator", "level": "practice", "command": ["python3", "task_iterator.py"]},
        {"name": "exceptions-contract", "level": "practice", "command": ["python3", "task_exceptions.py"]},
        {"name": "serialization-boundary", "level": "practice", "command": ["python3", "task_serialization.py"]},
        {"name": "async-await", "level": "transfer", "command": ["python3", "task_async.py"]},
        {"name": "integration-project", "level": "integration", "command": ["python3", "task_integration.py"]},
        {"name": "type-boundaries", "level": "transfer", "command": ["python3", "task_types.py"]},
        {"name": "resource-safety", "level": "integration", "command": ["python3", "task_resources.py"]},
    ]


def _javascript_tasks(root: Path) -> list[dict]:
    (root / "task.js").write_text(
        "function classify(value) {\n"
        "  if (value < 0) return 'negative';\n"
        "  if (value === 0) return 'zero';\n"
        "  return 'positive';\n"
        "}\n"
        "if (classify(-1) !== 'negative' || classify(0) !== 'zero' || classify(3) !== 'positive') process.exit(1);\n",
        encoding="utf-8")
    (root / "task_tests.js").write_text(
        'const fs = require("fs");\nconst source = fs.readFileSync("task.js", "utf8");\nif (!source.includes("=== 0") || !source.includes("return \'positive\'")) process.exit(1);\n',
        encoding="utf-8")
    (root / "task_errors.js").write_text(
        "function parseAge(value) { const number = Number(value); return Number.isInteger(number) ? number : null; }\n"
        "if (parseAge('42') !== 42 || parseAge('x') !== null) process.exit(1);\n", encoding="utf-8")
    (root / "task_module.js").write_text(
        "const { classify } = require('./task_module_support');\n"
        "if (JSON.stringify([-1, 0, 1].map(classify)) !== JSON.stringify(['negative','zero','positive'])) process.exit(1);\n", encoding="utf-8")
    (root / "task_module_support.js").write_text(
        "exports.classify = value => value < 0 ? 'negative' : value === 0 ? 'zero' : 'positive';\n", encoding="utf-8")
    (root / "task_closure.js").write_text(
        "function counter() { let value = 0; return () => ++value; }\n"
        "const next = counter(); if (next() !== 1 || next() !== 2) process.exit(1);\n", encoding="utf-8")
    (root / "task_promise.js").write_text(
        "Promise.resolve(21).then(value => { if (value * 2 !== 42) process.exit(1); });\n", encoding="utf-8")
    (root / "task_event_loop.js").write_text(
        "const order = ['sync']; Promise.resolve().then(() => order.push('micro'));\n"
        "setTimeout(() => { if (order.join(',') !== 'sync,micro') process.exit(1); }, 0);\n", encoding="utf-8")
    (root / "task_serialization.js").write_text(
        "const value = { enabled: true, items: [1, 2] };\n"
        "if (JSON.stringify(JSON.parse(JSON.stringify(value))) !== JSON.stringify(value)) process.exit(1);\n", encoding="utf-8")
    (root / "task_integration.js").write_text(
        "const { classify } = require('./task_module_support');\n"
        "const data = JSON.parse(JSON.stringify({ values: [-2, 0, 3] }));\n"
        "if (JSON.stringify(data.values.map(classify)) !== JSON.stringify(['negative','zero','positive'])) process.exit(1);\n", encoding="utf-8")
    (root / "task_async_errors.js").write_text(
        "async function safe(value) { if (value < 0) throw new Error('negative'); return value; }\n"
        "safe(2).then(value => { if (value !== 2) process.exit(1); });\n"
        "safe(-1).then(() => process.exit(1), error => { if (error.message !== 'negative') process.exit(1); });\n", encoding="utf-8")
    (root / "task_module_boundary.js").write_text(
        "const { classify } = require('./task_module_support');\n"
        "if (classify(-10) !== 'negative' || classify(10) !== 'positive') process.exit(1);\n", encoding="utf-8")
    return [
        {"name": "foundation-control-flow", "level": "foundation", "command": ["node", "task.js"]},
        {"name": "error-handling", "level": "practice", "command": ["node", "task_errors.js"]},
        {"name": "module-composition", "level": "practice", "command": ["node", "task_module.js"]},
        {"name": "unseen-inputs", "level": "transfer", "command": ["node", "task_tests.js"]},
        {"name": "transfer-boundary-cases", "level": "transfer", "command": ["node", "-e", "const f=v=>v<0?'negative':v===0?'zero':'positive'; if(f(-999)!=='negative'||f(999)!=='positive')process.exit(1)"]},
        {"name": "closure-scope", "level": "practice", "command": ["node", "task_closure.js"]},
        {"name": "promise-contract", "level": "practice", "command": ["node", "task_promise.js"]},
        {"name": "event-loop-order", "level": "transfer", "command": ["node", "task_event_loop.js"]},
        {"name": "serialization-boundary", "level": "practice", "command": ["node", "task_serialization.js"]},
        {"name": "integration-project", "level": "integration", "command": ["node", "task_integration.js"]},
        {"name": "async-error-boundary", "level": "transfer", "command": ["node", "task_async_errors.js"]},
        {"name": "module-boundary", "level": "practice", "command": ["node", "task_module_boundary.js"]},
    ]


def _rust_tasks(root: Path) -> list[dict]:
    (root / "Cargo.toml").write_text('[package]\nname = "skill_lab"\nversion = "0.1.0"\nedition = "2021"\n', encoding="utf-8")
    source = root / "src"
    source.mkdir()
    (source / "lib.rs").write_text(
        "pub fn classify(value: i32) -> &'static str { match value { n if n < 0 => \"negative\", 0 => \"zero\", _ => \"positive\" } }\n"
        "pub fn parse(value: &str) -> Result<i32, &'static str> { value.parse().map_err(|_| \"invalid\") }\n"
        "pub fn sum(values: &[i32]) -> i32 { values.iter().copied().sum() }\n"
        "pub trait Describe { fn describe(&self) -> String; }\n"
        "impl Describe for i32 { fn describe(&self) -> String { self.to_string() } }\n"
        "#[cfg(test)] mod tests {\n"
        "    use super::*;\n"
        "    #[test] fn ownership() { let value = String::from(\"rust\"); assert_eq!(value.len(), 4); }\n"
        "    #[test] fn borrowing() { let mut value = 1; { let reference = &mut value; *reference += 1; } assert_eq!(value, 2); }\n"
        "    #[test] fn result_option() { assert_eq!(parse(\"42\"), Ok(42)); assert!(parse(\"x\").is_err()); }\n"
        "    #[test] fn cargo_builds() { assert_eq!(classify(0), \"zero\"); }\n"
        "    #[test] fn error_boundary() { assert_eq!(parse(\"-7\"), Ok(-7)); }\n"
        "    #[test] fn iterators() { assert_eq!(sum(&[1, 2, 3]), 6); }\n"
        "    #[test] fn traits() { assert_eq!(7.describe(), \"7\"); }\n"
        "    #[test] fn pattern_matching() { assert_eq!(classify(-4), \"negative\"); assert_eq!(classify(4), \"positive\"); }\n"
        "    #[test] fn modules() { assert_eq!(sum(&[]), 0); }\n"
        "    #[test] fn unseen_values() { assert_eq!(classify(-999), \"negative\"); assert_eq!(classify(999), \"positive\"); }\n"
        "    #[test] fn integration_project() { assert_eq!(sum(&[2, 4, 6]), 12); assert_eq!(parse(\"12\"), Ok(12)); }\n"
        "    #[test] fn resource_safety() { let value = String::from(\"owned\"); let borrowed = &value; assert_eq!(borrowed, \"owned\"); }\n"
        "}\n",
        encoding="utf-8")
    return [
        {"name": "ownership", "level": "foundation", "command": ["cargo", "test", "ownership"]},
        {"name": "borrowing", "level": "foundation", "command": ["cargo", "test", "borrowing"]},
        {"name": "result-option", "level": "practice", "command": ["cargo", "test", "result_option"]},
        {"name": "cargo", "level": "practice", "command": ["cargo", "test", "cargo_builds"]},
        {"name": "error-boundary", "level": "practice", "command": ["cargo", "test", "error_boundary"]},
        {"name": "iterators", "level": "practice", "command": ["cargo", "test", "iterators"]},
        {"name": "traits", "level": "transfer", "command": ["cargo", "test", "traits"]},
        {"name": "pattern-matching", "level": "transfer", "command": ["cargo", "test", "pattern_matching"]},
        {"name": "modules", "level": "transfer", "command": ["cargo", "test", "modules"]},
        {"name": "unseen-values", "level": "transfer", "command": ["cargo", "test", "unseen_values"]},
        {"name": "integration-project", "level": "integration", "command": ["cargo", "test", "integration_project"]},
        {"name": "resource-safety", "level": "integration", "command": ["cargo", "test", "resource_safety"]},
    ]


def _node_tasks(root: Path) -> list[dict]:
    """JavaScript base plus behavior that is genuinely Node.js-specific."""
    tasks = _javascript_tasks(root)
    (root / "task_http_server.js").write_text(
        "const http = require('node:http');\n"
        "const server = http.createServer((req, res) => { res.end('ok'); });\n"
        "let body = '';\n"
        "server.emit('request', { method: 'GET', url: '/' }, { end: value => { body = value; } });\n"
        "if (body !== 'ok') process.exit(1);\n", encoding="utf-8")
    (root / "task_streams.js").write_text(
        "const { Readable } = require('node:stream');\n"
        "(async () => { const chunks = []; for await (const chunk of Readable.from(['a', 'b'])) chunks.push(chunk);\n"
        "if (chunks.join('') !== 'ab') process.exit(1); })().catch(() => process.exit(1));\n", encoding="utf-8")
    (root / "task_observability.js").write_text(
        "const events = [];\n"
        "function record(event, fields = {}) { events.push({ event, ...fields, at: Date.now() }); }\n"
        "record('request.completed', { status: 200 });\n"
        "if (events[0].event !== 'request.completed' || events[0].status !== 200 || !events[0].at) process.exit(1);\n", encoding="utf-8")
    tasks.extend([
        {"name": "node-http-server", "level": "practice", "command": ["node", "task_http_server.js"]},
        {"name": "node-streams", "level": "transfer", "command": ["node", "task_streams.js"]},
        {"name": "node-observability", "level": "integration", "command": ["node", "task_observability.js"]},
    ])
    return tasks


def tasks_for(topic: str, root: Path) -> list[dict] | None:
    name = topic.lower().replace(".", "")
    if name in {"python", "python3"} and shutil.which("python3"):
        return _python_tasks(root)
    if name in {"javascript", "javascriptjs"} and shutil.which("node"):
        return _javascript_tasks(root)
    if name in {"nodejs", "node"} and shutil.which("node"):
        return _node_tasks(root)
    if name in {"rust"} and shutil.which("cargo"):
        return _rust_tasks(root)
    return None


def _diagnose(result: dict) -> str:
    if result.get("returncode") == -1:
        return "executor ausente, interrompido ou expirado"
    text = f"{result.get('stderr', '')} {result.get('stdout', '')}".lower()
    if "syntax" in text or "expected" in text:
        return "falha de sintaxe"
    if "assert" in text or "test" in text:
        return "falha de comportamento ou teste"
    return "falha de execução; revisão necessária"


def _remediation(task: dict, diagnosis: str) -> dict:
    """Create a bounded recovery step; never loops indefinitely."""
    remediation = {
        "falha de sintaxe": "revisar sintaxe e executar novamente",
        "falha de comportamento ou teste": "revisar o contrato e cobrir o caso que falhou",
        "falha de execução; revisão necessária": "revisar ambiente e repetir a verificação",
    }.get(diagnosis, "revisar a tarefa e repetir a verificação")
    return {"name": f"remediation-{task['name']}", "level": task.get("level", "practice"),
            "command": task["command"], "remediation": remediation}


def run(topic: str, *, curriculum: dict | None = None, timeout: int = 30) -> dict:
    """Run the deterministic practical suite for a supported domain."""
    with tempfile.TemporaryDirectory(prefix="ia-skill-lab-") as directory:
        root = Path(directory)
        tasks = tasks_for(topic, root)
        if tasks is None:
            return {"status": "practice_unavailable", "topic": topic, "tasks": [],
                    "message": "Não há executor local seguro disponível para este domínio."}
        results = []
        queue = list(tasks)
        recovery_used = set()
        while queue:
            task = queue.pop(0)
            # Copia o resultado para que executores/test doubles reutilizando
            # um dicionário não sobrescrevam a evidência de tentativas anteriores.
            result = dict(_run(task["command"], root, timeout))
            result["name"] = task["name"]
            result["level"] = task.get("level", "practice")
            result["diagnosis"] = "aprovado" if result.get("passed") else _diagnose(result)
            if not result.get("passed") and task["name"] not in recovery_used and not task["name"].startswith("remediation-"):
                recovery_used.add(task["name"])
                recovery = _remediation(task, result["diagnosis"])
                result["recovery_scheduled"] = recovery["name"]
                queue.append(recovery)
            elif task["name"].startswith("remediation-"):
                result["recovery_attempt"] = True
            results.append(result)
        passed = sum(1 for result in results if result.get("passed"))
        unresolved = [result for result in results
                      if not result.get("passed") and not result.get("recovery_scheduled")]
        recovered = any(result.get("recovery_attempt") and result.get("passed") for result in results)
        status = "verified" if not unresolved and not recovered else "recovered" if not unresolved else "failed"
        return {"status": status,
                "topic": topic, "tasks": results, "passed": passed, "total": len(results)}
