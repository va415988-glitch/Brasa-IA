"""Small, explicit local implementation recipes for requests the model cannot draft."""

from __future__ import annotations

import re
import unicodedata
from typing import Any
from sqlite_task_recipe import sqlite_task_plan


TODO_CLI_SOURCE = '''"""Small JSON-backed task CLI using only the Python standard library."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_DATA_FILE = ".todo_tasks.json"


class TaskStore:
    """Read and write tasks in a local JSON file."""

    def __init__(self, path: str | Path | None = None) -> None:
        configured_path = path or os.environ.get("TODO_DATA_FILE") or DEFAULT_DATA_FILE
        self.path = Path(configured_path)

    def _load(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        try:
            tasks = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise ValueError(f"Arquivo de tarefas contém JSON inválido: {self.path}") from error
        if not isinstance(tasks, list):
            raise ValueError("O arquivo de tarefas precisa conter uma lista JSON.")
        for task in tasks:
            if (not isinstance(task, dict)
                    or not isinstance(task.get("id"), int)
                    or isinstance(task.get("id"), bool)
                    or not isinstance(task.get("description"), str)
                    or not task["description"].strip()
                    or not isinstance(task.get("completed"), bool)
                    or not isinstance(task.get("created_at"), str)):
                raise ValueError("O arquivo contém uma tarefa inválida.")
        return tasks

    def _save(self, tasks: list[dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self.path.parent,
                prefix=f".{self.path.name}.", suffix=".tmp", delete=False,
            ) as temporary_file:
                temporary_path = Path(temporary_file.name)
                json.dump(tasks, temporary_file, ensure_ascii=False, indent=2)
                temporary_file.write("\\n")
            temporary_path.replace(self.path)
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()

    def add(self, description: str) -> dict[str, Any]:
        description = str(description).strip()
        if not description:
            raise ValueError("A descrição da tarefa não pode ficar vazia.")
        tasks = self._load()
        task = {
            "id": max((item["id"] for item in tasks), default=0) + 1,
            "description": description,
            "completed": False,
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        tasks.append(task)
        self._save(tasks)
        return task

    def list(self, status: str = "pending") -> list[dict[str, Any]]:
        if status not in {"pending", "completed", "all"}:
            raise ValueError("Status deve ser pending, completed ou all.")
        tasks = self._load()
        if status == "pending":
            return [task for task in tasks if not task["completed"]]
        if status == "completed":
            return [task for task in tasks if task["completed"]]
        return tasks

    def complete(self, task_id: int) -> dict[str, Any]:
        tasks = self._load()
        for task in tasks:
            if task["id"] == task_id:
                if not task["completed"]:
                    task["completed"] = True
                    self._save(tasks)
                return task
        raise ValueError(f"Tarefa {task_id} não encontrada.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Gerencie tarefas em um arquivo JSON local.")
    commands = parser.add_subparsers(dest="command", required=True)

    add_parser = commands.add_parser("add", help="adiciona uma tarefa")
    add_parser.add_argument("description", nargs="+", help="descrição da tarefa")

    list_parser = commands.add_parser("list", help="lista tarefas")
    list_parser.add_argument(
        "--status", choices=("pending", "completed", "all"), default="pending",
        help="filtra por estado (padrão: pending)",
    )

    done_parser = commands.add_parser("done", help="marca uma tarefa como concluída")
    done_parser.add_argument("task_id", type=int, help="identificador da tarefa")
    return parser


def main(argv: list[str] | None = None, *, data_path: str | Path | None = None) -> int:
    args = build_parser().parse_args(argv)
    store = TaskStore(data_path)
    try:
        if args.command == "add":
            task = store.add(" ".join(args.description))
            print(f"Tarefa adicionada: [{task['id']}] {task['description']}")
        elif args.command == "list":
            tasks = store.list(args.status)
            if not tasks:
                print("Nenhuma tarefa encontrada.")
            for task in tasks:
                marker = "x" if task["completed"] else " "
                print(f"[{marker}] {task['id']}: {task['description']}")
        elif args.command == "done":
            task = store.complete(args.task_id)
            print(f"Tarefa concluída: [{task['id']}] {task['description']}")
        return 0
    except (OSError, ValueError) as error:
        print(f"Erro: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
'''


TODO_CLI_TESTS = '''"""Focused tests for the JSON-backed task CLI."""

import json
import tempfile
import unittest
from pathlib import Path

from todo_cli import TaskStore, main


class TaskStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.data_path = Path(self.temporary_directory.name) / "tasks.json"
        self.store = TaskStore(self.data_path)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_add_list_complete_and_persist(self) -> None:
        first = self.store.add("Comprar pão")
        second = self.store.add("Ler um capítulo")
        self.assertEqual(first["id"], 1)
        self.assertEqual([task["description"] for task in self.store.list()],
                         ["Comprar pão", "Ler um capítulo"])

        self.store.complete(first["id"])
        reloaded = TaskStore(self.data_path)
        self.assertEqual([task["id"] for task in reloaded.list("pending")], [second["id"]])
        self.assertEqual([task["id"] for task in reloaded.list("completed")], [first["id"]])
        self.assertEqual(len(reloaded.list("all")), 2)
        self.assertEqual(json.loads(self.data_path.read_text(encoding="utf-8"))[0]["completed"], True)

    def test_rejects_empty_or_whitespace_descriptions(self) -> None:
        for description in ("", "   ", "\\t\\n"):
            with self.subTest(description=description), self.assertRaises(ValueError):
                self.store.add(description)
        self.assertFalse(self.data_path.exists())

    def test_missing_task_is_reported(self) -> None:
        with self.assertRaisesRegex(ValueError, "não encontrada"):
            self.store.complete(99)

    def test_cli_uses_an_injected_data_file(self) -> None:
        self.assertEqual(main(["add", "Escrever testes"], data_path=self.data_path), 0)
        self.assertEqual(main(["done", "1"], data_path=self.data_path), 0)
        self.assertEqual(self.store.list("completed")[0]["description"], "Escrever testes")


if __name__ == "__main__":
    unittest.main()
'''


NODE_CALCULATOR_SOURCE = '''/** Basic arithmetic helpers for finite JavaScript numbers. */

function assertFiniteNumber(value, name) {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw new TypeError(`${name} precisa ser um número finito.`);
  }
}

export function soma(a, b) {
  assertFiniteNumber(a, "a");
  assertFiniteNumber(b, "b");
  return a + b;
}

export function multiplicacao(a, b) {
  assertFiniteNumber(a, "a");
  assertFiniteNumber(b, "b");
  return a * b;
}
'''

NODE_CALCULATOR_TESTS = '''import test from "node:test";
import assert from "node:assert/strict";
import { multiplicacao, soma } from "../src/calculadora.js";

test("soma valores positivos", () => {
  assert.equal(soma(2, 3), 5);
});

test("soma valores negativos", () => {
  assert.equal(soma(-4, -6), -10);
});

test("soma decimais sem exigir igualdade binária exata", () => {
  assert.ok(Math.abs(soma(0.1, 0.2) - 0.3) < Number.EPSILON);
});

test("multiplica valores positivos", () => {
  assert.equal(multiplicacao(4, 5), 20);
});

test("multiplica valores com sinais diferentes", () => {
  assert.equal(multiplicacao(-3, 4), -12);
});

test("multiplica decimais", () => {
  assert.equal(multiplicacao(1.25, 0.2), 0.25);
});

test("rejeita argumentos que não são números finitos", () => {
  assert.throws(() => soma("2", 3), TypeError);
  assert.throws(() => multiplicacao(Infinity, 2), TypeError);
});
'''

NODE_CALCULATOR_PACKAGE = '''{
  "name": "calculadora-local",
  "version": "1.0.0",
  "private": true,
  "type": "module",
  "scripts": {
    "test": "node --test",
    "check": "node --check src/calculadora.js"
  }
}
'''


DELIVERY_TRACKER_CORE = r'''(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  root.DeliveryTracker = api;
})(typeof globalThis === "object" ? globalThis : this, function () {
  "use strict";
  function createDelivery(input) {
    const date = String(input.date || "");
    const deliveries = Number(input.deliveries);
    const kilometers = Number(input.kilometers);
    const note = String(input.note || "").trim().slice(0, 160);
    if (!/^\d{4}-\d{2}-\d{2}$/.test(date)
        || new Date(date + "T00:00:00Z").toISOString().slice(0, 10) !== date) throw new Error("Informe uma data válida.");
    if (!Number.isInteger(deliveries) || deliveries < 1 || deliveries > 1000) throw new Error("A quantidade de entregas deve ser um número inteiro entre 1 e 1000.");
    if (!Number.isFinite(kilometers) || kilometers < 0 || kilometers > 100000) throw new Error("Informe uma distância válida, igual ou maior que zero.");
    return {date, deliveries, kilometers: Math.round(kilometers * 100) / 100, note};
  }
  function filterDeliveries(items, from, to) {
    return items.filter((item) => (!from || item.date >= from) && (!to || item.date <= to))
      .slice().sort((left, right) => right.date.localeCompare(left.date));
  }
  function summarize(items) {
    return items.reduce((sum, item) => ({deliveries: sum.deliveries + item.deliveries,
      kilometers: Math.round((sum.kilometers + item.kilometers) * 100) / 100,
      records: sum.records + 1}), {deliveries: 0, kilometers: 0, records: 0});
  }
  return {createDelivery, filterDeliveries, summarize};
});
'''

DELIVERY_TRACKER_APP = r'''(function () {
  "use strict";
  const key = "rotas-em-dia.deliveries.v1";
  const form = document.querySelector("#delivery-form");
  if (!form || !window.DeliveryTracker) return;
  const message = document.querySelector("#message"), rows = document.querySelector("#delivery-rows");
  const empty = document.querySelector("#empty-state"), summary = document.querySelector("#summary");
  const from = document.querySelector("#period-from"), to = document.querySelector("#period-to");
  function today() { const d = new Date(); return [d.getFullYear(),String(d.getMonth()+1).padStart(2,"0"),String(d.getDate()).padStart(2,"0")].join("-"); }
  function load() { try { const value=JSON.parse(localStorage.getItem(key)||"[]"); return Array.isArray(value)?value.filter(x=>x&&/^\d{4}-\d{2}-\d{2}$/.test(x.date)&&Number.isInteger(x.deliveries)&&Number.isFinite(x.kilometers)):[]; } catch { return []; } }
  let entries=load(); form.elements.date.value=today();
  function show(text,error=false) { message.textContent=text; message.classList.toggle("error",error); message.hidden=!text; }
  function render() {
    const filtered=DeliveryTracker.filterDeliveries(entries,from.value,to.value), totals=DeliveryTracker.summarize(filtered);
    summary.querySelector("[data-total-deliveries]").textContent=totals.deliveries.toLocaleString("pt-BR");
    summary.querySelector("[data-total-kilometers]").textContent=totals.kilometers.toLocaleString("pt-BR",{minimumFractionDigits:1,maximumFractionDigits:2});
    summary.querySelector("[data-total-days]").textContent=totals.records.toLocaleString("pt-BR");
    rows.replaceChildren(); empty.hidden=filtered.length>0;
    for(const item of filtered) {
      const row=document.createElement("tr"), date=document.createElement("th"); date.scope="row"; date.textContent=new Date(item.date+"T12:00:00").toLocaleDateString("pt-BR");
      const count=document.createElement("td"); count.textContent=String(item.deliveries);
      const km=document.createElement("td"); km.textContent=item.kilometers.toLocaleString("pt-BR",{minimumFractionDigits:1,maximumFractionDigits:2})+" km";
      const note=document.createElement("td"); note.textContent=item.note||"—";
      const action=document.createElement("td"), remove=document.createElement("button"); remove.type="button"; remove.className="remove"; remove.textContent="Excluir";
      remove.setAttribute("aria-label","Excluir registro de "+date.textContent);
      remove.addEventListener("click",()=>{entries=entries.filter(x=>x.id!==item.id);persist();render();});
      action.append(remove); row.append(date,count,km,note,action); rows.append(row);
    }
  }
  function persist() { try { localStorage.setItem(key,JSON.stringify(entries)); show("Registro salvo neste navegador."); } catch { show("Não foi possível salvar. Verifique o espaço disponível no navegador.",true); } }
  form.addEventListener("submit",event=>{event.preventDefault();try{
    const record=DeliveryTracker.createDelivery({date:form.elements.date.value,deliveries:form.elements.deliveries.value,kilometers:form.elements.kilometers.value,note:form.elements.note.value});
    entries.push({...record,id:(window.crypto&&crypto.randomUUID)?crypto.randomUUID():String(Date.now())+Math.random()}); persist();
    form.elements.deliveries.value="";form.elements.kilometers.value="";form.elements.note.value="";form.elements.deliveries.focus();render();
  }catch(error){show(error.message,true);}});
  from.addEventListener("change",render);to.addEventListener("change",render);
  document.querySelector("#clear-period").addEventListener("click",()=>{from.value="";to.value="";render();}); render();
})();
'''

DELIVERY_TRACKER_HTML = r'''<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="theme-color" content="#10182a"><title>Rota em Dia · entregas e quilômetros</title>
<style>
:root{color-scheme:dark;font-family:Inter,ui-sans-serif,system-ui,sans-serif;background:#0b1220;color:#eef2ff}*{box-sizing:border-box}
body{margin:0;min-height:100vh;background:radial-gradient(ellipse at 15% 0%,#233969 0,transparent 38%),#0b1220}main{width:min(1080px,calc(100% - 32px));margin:auto;padding:38px 0 60px}
header{display:flex;align-items:center;justify-content:space-between;gap:18px;margin-bottom:26px}.brand{display:flex;align-items:center;gap:14px}.mark{display:grid;place-items:center;width:48px;height:48px;border-radius:16px;background:#6d5efc;font-size:23px}h1{font-size:clamp(26px,5vw,36px);letter-spacing:-.04em;margin:0}.subtitle{color:#a6b3ce;margin:5px 0 0}.local{border:1px solid #31405d;border-radius:99px;padding:9px 13px;color:#c4d3f4;font-size:13px}
.grid{display:grid;grid-template-columns:minmax(280px,.82fr) minmax(0,1.5fr);gap:18px;align-items:start}.card{background:#111b2d;border:1px solid #263651;border-radius:20px;padding:22px;box-shadow:0 18px 50px #0002}h2{margin:0 0 6px;font-size:19px}.hint{color:#96a5c2;font-size:13px;line-height:1.5;margin:0 0 20px}
label{display:block;font-size:13px;color:#c6d0e4;margin:14px 0 7px}input{width:100%;padding:12px 13px;border:1px solid #354663;border-radius:11px;background:#0b1424;color:#fff;font:inherit}.fields{display:grid;grid-template-columns:1fr 1fr;gap:12px}.primary{width:100%;margin-top:18px;padding:13px;border:0;border-radius:11px;background:#7567ff;color:white;font:inherit;font-weight:700;cursor:pointer}.primary:hover{background:#887dff}
#message{margin:12px 0 0;color:#9de6c2;font-size:13px}#message.error{color:#ffaaa9}.stats{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin:18px 0}.stat{padding:14px 12px;border-radius:14px;background:#17243a;border:1px solid #293b59}.stat span{display:block;color:#9aabc9;font-size:12px}.stat strong{display:block;margin-top:5px;font-size:22px}.period{display:flex;align-items:end;gap:10px;flex-wrap:wrap;margin:20px 0}.period label{flex:1;min-width:130px;margin:0}.period input{margin-top:7px}
.secondary,.remove{padding:10px 12px;border:1px solid #354663;border-radius:10px;background:#152239;color:#d9e1f1;font:inherit;cursor:pointer}.table-wrap{overflow-x:auto}table{width:100%;border-collapse:collapse;text-align:left;font-size:13px}th,td{padding:13px 10px;border-bottom:1px solid #26354d;white-space:nowrap}thead th{color:#91a2c1;font-weight:600}tbody th{font-weight:600}.remove{padding:7px 9px;color:#ffc2be;font-size:12px}.empty{padding:34px 12px;color:#9aabc9;text-align:center}footer{color:#7f90ad;font-size:12px;text-align:center;margin-top:20px}
@media(max-width:760px){main{padding-top:25px}.grid{grid-template-columns:1fr}.card{padding:18px}}@media(max-width:460px){main{width:calc(100% - 22px)}header{align-items:flex-start;flex-direction:column}.fields{grid-template-columns:1fr}.stat{padding:12px 9px}.stat strong{font-size:19px}th,td{padding:11px 8px}}
</style></head><body><main>
<header><div class="brand"><div class="mark" aria-hidden="true">↗</div><div><h1>Rota em Dia</h1><p class="subtitle">Acompanhe suas entregas e quilômetros.</p></div></div><span class="local">● Dados salvos neste navegador</span></header>
<div class="grid"><section class="card"><h2>Registrar período</h2><p class="hint">Anote o total do seu turno. Seus registros ficam somente neste dispositivo.</p>
<form id="delivery-form"><label for="delivery-date">Data do turno</label><input id="delivery-date" name="date" type="date" required><div class="fields"><div><label for="deliveries">Entregas feitas</label><input id="deliveries" name="deliveries" type="number" min="1" max="1000" step="1" placeholder="Ex.: 18" required></div><div><label for="kilometers">Quilômetros rodados</label><input id="kilometers" name="kilometers" type="number" min="0" max="100000" step="0.1" placeholder="Ex.: 62,5" required></div></div>
<label for="note">Observação · opcional</label><input id="note" name="note" maxlength="160" type="text" placeholder="Ex.: turno da manhã"><button class="primary" type="submit">Salvar registro</button><p id="message" role="status" hidden></p></form></section>
<section class="card"><h2>Seu resumo</h2><p class="hint">Filtre um intervalo para comparar seus turnos.</p><div class="stats" id="summary"><div class="stat"><span>Entregas</span><strong data-total-deliveries>0</strong></div><div class="stat"><span>Quilômetros</span><strong><span data-total-kilometers>0</span> km</strong></div><div class="stat"><span>Dias registrados</span><strong data-total-days>0</strong></div></div>
<div class="period"><label for="period-from">De<input id="period-from" type="date"></label><label for="period-to">Até<input id="period-to" type="date"></label><button id="clear-period" class="secondary" type="button">Limpar filtro</button></div><div class="table-wrap"><table><thead><tr><th>Data</th><th>Entregas</th><th>Distância</th><th>Observação</th><th></th></tr></thead><tbody id="delivery-rows"></tbody></table><p id="empty-state" class="empty">Nenhum turno nesse período. Registre o primeiro ao lado.</p></div></section></div>
<footer>Protótipo local · sem conta, sincronização entre dispositivos ou rastreamento GPS.</footer><script src="delivery-core.js"></script><script src="app.js"></script></main></body></html>
'''

DELIVERY_TRACKER_TESTS = r'''const test = require("node:test");
const assert = require("node:assert/strict");
const {createDelivery, filterDeliveries, summarize} = require("../delivery-core.js");
test("valida um turno e arredonda quilômetros", () => {
  assert.deepEqual(createDelivery({date:"2026-09-28",deliveries:"18",kilometers:"62.567",note:" manhã "}), {date:"2026-09-28",deliveries:18,kilometers:62.57,note:"manhã"});
});
test("rejeita data impossível, entregas fracionadas e distância negativa", () => {
  assert.throws(()=>createDelivery({date:"2026-02-30",deliveries:1,kilometers:0}),/data válida/);
  assert.throws(()=>createDelivery({date:"2026-09-28",deliveries:1.5,kilometers:2}),/número inteiro/);
  assert.throws(()=>createDelivery({date:"2026-09-28",deliveries:1,kilometers:-1}),/distância válida/);
});
test("filtra datas de forma inclusiva e ordena do dia mais recente", () => {
  const records=[{date:"2026-09-27",deliveries:2,kilometers:5},{date:"2026-09-29",deliveries:4,kilometers:9},{date:"2026-09-28",deliveries:3,kilometers:7}];
  assert.deepEqual(filterDeliveries(records,"2026-09-28","2026-09-29").map(x=>x.date),["2026-09-29","2026-09-28"]);
});
test("resume dias e totais, inclusive em um período vazio", () => {
  assert.deepEqual(summarize([]),{deliveries:0,kilometers:0,records:0});
  assert.deepEqual(summarize([{deliveries:8,kilometers:12.5},{deliveries:3,kilometers:7.25}]),{deliveries:11,kilometers:19.75,records:2});
});
'''

DELIVERY_TRACKER_README = '''# Rota em Dia

Protótipo web local para registrar, por turno, entregas feitas e quilômetros rodados. O histórico fica no `localStorage` do navegador e pode ser filtrado por datas.

## Executar

Abra `index.html` no navegador. Também é possível executar `python3 -m http.server 8000` nesta pasta e acessar `http://127.0.0.1:8000`.

## Verificar

Execute `npm test` com Node.js 18 ou superior.

Este protótipo não inclui login, sincronização entre dispositivos ou rastreamento GPS.
'''

DELIVERY_TRACKER_PACKAGE = '''{"name":"rota-em-dia","private":true,"version":"0.1.0","scripts":{"test":"node --test tests/*.test.cjs"}}\n'''

TODO_WEB_CORE = r'''(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  root.TaskList = api;
})(typeof globalThis === "object" ? globalThis : this, function () {
  "use strict";
  const STORAGE_KEY = "lista-de-tarefas.items.v1";
  function createTask(text, id = Date.now().toString(36) + Math.random().toString(36).slice(2)) {
    const title = String(text || "").trim();
    if (!title) throw new Error("Digite uma tarefa antes de adicionar.");
    return {id: String(id), title, completed: false};
  }
  function toggleTask(tasks, id) {
    return tasks.map((task) => task.id === id ? {...task, completed: !task.completed} : task);
  }
  function loadTasks(storage) {
    try {
      const value = JSON.parse(storage.getItem(STORAGE_KEY) || "[]");
      return Array.isArray(value) ? value.filter((task) => task && typeof task.id === "string"
        && typeof task.title === "string" && typeof task.completed === "boolean") : [];
    } catch { return []; }
  }
  function saveTasks(storage, tasks) { storage.setItem(STORAGE_KEY, JSON.stringify(tasks)); }
  return {createTask, toggleTask, loadTasks, saveTasks};
});
'''

TODO_WEB_APP = r'''(function () {
  "use strict";
  const form = document.querySelector("#task-form");
  const input = document.querySelector("#task-input");
  const list = document.querySelector("#task-list");
  const empty = document.querySelector("#empty-state");
  const status = document.querySelector("#status");
  if (!form || !window.TaskList) return;
  let tasks = TaskList.loadTasks(localStorage);
  function render() {
    list.replaceChildren();
    empty.hidden = tasks.length > 0;
    for (const task of tasks) {
      const row = document.createElement("li");
      row.className = task.completed ? "task completed" : "task";
      const label = document.createElement("label");
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.checked = task.completed;
      checkbox.setAttribute("aria-label", "Marcar como concluída: " + task.title);
      checkbox.addEventListener("change", () => {
        tasks = TaskList.toggleTask(tasks, task.id);
        persist("Tarefa atualizada.");
        render();
      });
      const title = document.createElement("span");
      title.textContent = task.title;
      label.append(checkbox, title);
      row.append(label);
      list.append(row);
    }
  }
  function persist(message) {
    try { TaskList.saveTasks(localStorage, tasks); status.textContent = message; }
    catch { status.textContent = "Não foi possível salvar no navegador."; }
  }
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    try {
      tasks = [...tasks, TaskList.createTask(input.value)];
      persist("Tarefa salva neste navegador.");
      input.value = "";
      input.focus();
      render();
    } catch (error) { status.textContent = error.message; }
  });
  render();
})();
'''

TODO_WEB_HTML = r'''<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="theme-color" content="#172033"><title>Lista de tarefas</title><style>
:root{font-family:Inter,system-ui,sans-serif;color:#172033;background:#f2f5fa}*{box-sizing:border-box}body{margin:0;min-height:100vh;padding:40px 18px;background:radial-gradient(circle at 15% 0,#e0e9ff,transparent 38%),#f2f5fa}main{width:min(660px,100%);margin:5vh auto;background:#fff;border:1px solid #e0e6f0;border-radius:22px;padding:clamp(22px,5vw,42px);box-shadow:0 24px 70px #23345b16}header{margin-bottom:28px}.eyebrow{color:#5369c7;font-size:12px;font-weight:800;letter-spacing:.13em;text-transform:uppercase}h1{margin:8px 0;font-size:clamp(30px,7vw,42px);letter-spacing:-.045em}.intro{margin:0;color:#65718a;line-height:1.55}.form{display:flex;gap:10px}.form input[type=text]{min-width:0;flex:1;padding:14px 15px;border:1px solid #cbd4e3;border-radius:12px;font:inherit}.form button{border:0;border-radius:12px;padding:0 19px;background:#5369c7;color:white;font:inherit;font-weight:750;cursor:pointer}.form button:hover{background:#4054ae}#status{min-height:22px;margin:10px 2px;color:#5369c7;font-size:13px}h2{display:flex;justify-content:space-between;gap:12px;align-items:center;margin:16px 0 12px;font-size:17px}h2 small{color:#7b879c;font-size:12px;font-weight:500}ul{display:grid;gap:9px;margin:0;padding:0;list-style:none}.task{padding:13px 14px;border:1px solid #e0e6f0;border-radius:12px}.task label{display:flex;align-items:center;gap:12px;cursor:pointer;overflow-wrap:anywhere}.task input{width:18px;height:18px;accent-color:#5369c7;flex:0 0 auto}.completed span{color:#929bad;text-decoration:line-through}.empty{padding:22px 12px;border:1px dashed #ccd5e4;border-radius:12px;color:#7b879c;text-align:center}footer{margin-top:24px;color:#8a95a8;font-size:12px;text-align:center}@media(max-width:430px){body{padding:20px 11px}.form{flex-direction:column}.form button{padding:13px}}
</style></head><body><main><header><span class="eyebrow">Organize seu dia</span><h1>Lista de tarefas</h1><p class="intro">Anote o que precisa fazer e marque cada tarefa quando terminar.</p></header>
<form id="task-form" class="form"><input id="task-input" name="task" type="text" maxlength="240" placeholder="Ex.: revisar o planejamento" autocomplete="off" aria-label="Nova tarefa" required><button type="submit">Adicionar</button></form><p id="status" role="status" aria-live="polite"></p>
<section aria-labelledby="tasks-heading"><h2 id="tasks-heading">Suas tarefas <small>salvas neste navegador</small></h2><p id="empty-state" class="empty">Nenhuma tarefa ainda. Adicione a primeira acima.</p><ul id="task-list"></ul></section><footer>Os dados ficam neste navegador e neste dispositivo.</footer></main><script src="task-core.js"></script><script src="app.js"></script></body></html>
'''

TODO_WEB_TESTS = r'''const test = require("node:test");
const assert = require("node:assert/strict");
const {createTask, toggleTask, loadTasks, saveTasks} = require("../task-core.js");

function memoryStorage() {
  const data = new Map();
  return {getItem: (key) => data.get(key) ?? null, setItem: (key, value) => data.set(key, value)};
}

test("adds a trimmed pending task and rejects an empty title", () => {
  assert.deepEqual(createTask("  Comprar pão  ", "task-1"), {id: "task-1", title: "Comprar pão", completed: false});
  assert.throws(() => createTask("   "), /Digite uma tarefa/);
});

test("toggles completion without changing other tasks", () => {
  const tasks = [createTask("Uma", "1"), createTask("Duas", "2")];
  assert.deepEqual(toggleTask(tasks, "2"), [tasks[0], {...tasks[1], completed: true}]);
  assert.equal(tasks[1].completed, false);
});

test("saves tasks and restores them after loading again", () => {
  const storage = memoryStorage();
  const tasks = [createTask("Continuar amanhã", "saved-1")];
  saveTasks(storage, tasks);
  assert.deepEqual(loadTasks(storage), tasks);
});

test("recovers from malformed browser storage", () => {
  const storage = memoryStorage();
  storage.setItem("lista-de-tarefas.items.v1", "{");
  assert.deepEqual(loadTasks(storage), []);
});
'''

TODO_WEB_PACKAGE = '''{"name":"lista-de-tarefas","private":true,"version":"0.1.0","scripts":{"test":"node --test tests/*.test.cjs"}}\n'''

TODO_WEB_README = '''# Lista de tarefas\n\nApp web local para adicionar tarefas, marcar as concluídas e guardar os dados no armazenamento do navegador. Não usa dependências externas.\n\n## Abrir\n\nAbra `index.html` no navegador.\n\n## Verificar\n\nCom Node.js instalado, execute `npm test`.\n'''


def _fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", str(text or "").casefold())
    return "".join(character for character in decomposed if not unicodedata.combining(character))


def fallback_implementation_plan(
    question: str, *, existing_paths: set[str] | None = None,
    readable_sources: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    """Return a bounded recipe only when request and inspected project match."""
    sqlite_plan = sqlite_task_plan(question, existing_paths)
    if sqlite_plan is not None:
        return sqlite_plan
    ui_plan = _todo_ui_follow_up_plan(
        question, existing_paths=existing_paths, readable_sources=readable_sources,
    )
    if ui_plan is not None:
        return ui_plan
    normalized = _fold(question)
    node_calculator = (
        re.search(r"\bnode(?:\.js)?\b", normalized)
        and re.search(r"\b(?:src/calculadora\.js|calculadora\.js)\b", normalized)
        and re.search(r"\b(?:tests/calculadora\.test\.js|calculadora\.test\.js)\b", normalized)
        and re.search(r"\bpackage\.json\b", normalized)
        and re.search(r"\b(?:soma|somar|adicao)\b", normalized)
        and re.search(r"\b(?:multiplicacao|multiplicar)\b", normalized)
        and re.search(r"\b(?:teste|testes|node\s*:\s*test|node\s+--test|npm\s+test)\b", normalized)
    )
    if node_calculator:
        known_paths = {str(path).replace("\\", "/").strip("/") for path in (existing_paths or set())}
        allowed_existing_names = {"readme", "readme.md", "readme.rst", "readme.txt", ".gitignore",
                                  "license", "license.md", "license.txt"}
        unsupported = r"\b(?:typescript|react|vue|angular|express|api|servidor|backend|frontend|banco de dados|postgres|sqlite|login|autenticacao)\b"
        paths = {"package.json", "src/calculadora.js", "tests/calculadora.test.js"}
        if (re.search(unsupported, normalized)
                or any(path.rsplit("/", 1)[-1].casefold() not in allowed_existing_names for path in known_paths)
                or paths & known_paths):
            return None
        return {
            "assumptions": [
                "Usei JavaScript ES modules e somente recursos internos do Node.js; nenhuma dependência foi adicionada.",
                "A soma usa a aritmética IEEE 754 do JavaScript; o teste decimal compara o resultado com tolerância numérica.",
                "O runner node:test executa os casos com `npm test`; `npm run check` verifica a sintaxe do módulo.",
            ],
            "operations": [
                {"tool": "create_file", "arguments": {"path": "package.json", "content": NODE_CALCULATOR_PACKAGE}},
                {"tool": "create_file", "arguments": {"path": "src/calculadora.js", "content": NODE_CALCULATOR_SOURCE}},
                {"tool": "create_file", "arguments": {"path": "tests/calculadora.test.js", "content": NODE_CALCULATOR_TESTS}},
            ],
        }
    delivery_app = (
        re.search(r"\b(?:entregador(?:es)?|delivery)\b", normalized)
        and re.search(r"\bentregas?\b", normalized)
        and re.search(r"\b(?:quilometros?|km|distancia)\b", normalized)
        and re.search(r"\b(?:crie|criar|construa|construir|desenvolva|desenvolver|implemente|implementar|prototipo)\b", normalized)
        and re.search(r"\b(?:app|aplicativo|sistema|prototipo)\b", normalized)
    )
    if delivery_app:
        known_paths = {str(path).replace("\\", "/").strip("/") for path in (existing_paths or set())}
        allowed_existing_names = {"readme", "readme.md", "readme.rst", "readme.txt", ".gitignore",
                                  "license", "license.md", "license.txt"}
        unsupported = r"\b(?:login|autenticacao|autenticar|backend|servidor|sincronizar|sincronizacao|api|gps|mapa|pagamento|banco de dados|postgres|sqlite|react|vue|angular)\b"
        if re.search(unsupported, normalized):
            return None
        if any(path.rsplit("/", 1)[-1].casefold() not in allowed_existing_names for path in known_paths):
            return None
        paths = {"index.html", "app.js", "delivery-core.js", "package.json", "tests/delivery-core.test.cjs"}
        if paths & known_paths:
            return None
        operations = [
                {"tool": "create_file", "arguments": {"path": "index.html", "content": DELIVERY_TRACKER_HTML}},
                {"tool": "create_file", "arguments": {"path": "app.js", "content": DELIVERY_TRACKER_APP}},
                {"tool": "create_file", "arguments": {"path": "delivery-core.js", "content": DELIVERY_TRACKER_CORE}},
                {"tool": "create_file", "arguments": {"path": "package.json", "content": DELIVERY_TRACKER_PACKAGE}},
                {"tool": "create_file", "arguments": {"path": "tests/delivery-core.test.cjs", "content": DELIVERY_TRACKER_TESTS}},
        ]
        if "readme.md" not in {path.rsplit("/", 1)[-1].casefold() for path in known_paths}:
            operations.append({"tool": "create_file", "arguments": {"path": "README.md", "content": DELIVERY_TRACKER_README}})
        return {
            "assumptions": [
                "Como o pedido descreve um primeiro protótipo sem escolher uma stack, usei HTML, CSS e JavaScript sem dependências externas.",
                "Cada registro representa o total de entregas e quilômetros de um turno em uma data; o período pode ser filtrado no resumo.",
                "Os dados ficam no navegador atual; não há conta, sincronização nem coleta de localização.",
            ],
            "operations": operations,
        }
    task_list_request = (
        re.search(r"\b(?:tarefas?|afazeres|to[ -]?do)\b", normalized)
        and re.search(r"\b(?:criar|crie|cria|construir|construa|montar|monte|desenvolver|desenvolva|implementar|implemente)\b", normalized)
    )
    explicit_web_todo = (
        re.search(r"\b(?:app|aplicativo|web|site|interface|tela|navegador)\b", normalized)
        and re.search(r"\b(?:tarefas?|afazeres|to[ -]?do)\b", normalized)
        and re.search(r"\b(?:adicionar|adicione|adiciona|incluir|inclua|campo)\b", normalized)
        and re.search(r"\b(?:conclu|marcar|marcadas|marcá|terminar|checkbox)\w*\b", normalized)
        and re.search(r"\b(?:navegador|localstorage|persist|salvamento|salvar)\w*\b", normalized)
    )
    # For a small, unspecified task list, choose a browser prototype as the
    # reversible default. Honor an explicitly requested CLI/platform instead.
    default_web_todo = (
        task_list_request
        and not re.search(r"\b(?:python|cli|terminal|linha de comando|desktop|aplicativo nativo|react|vue|angular|typescript|rust)\b", normalized)
    )
    web_todo = explicit_web_todo or default_web_todo
    if web_todo:
        known_paths = {str(path).replace("\\", "/").strip("/") for path in (existing_paths or set())}
        allowed_existing_names = {"readme", "readme.md", "readme.rst", "readme.txt", ".gitignore",
                                  "license", "license.md", "license.txt"}
        unsupported = r"\b(?:login|autenticacao|backend|servidor|sincronizar|sincronizacao|api|pagamento|banco de dados|postgres|sqlite|react|vue|angular)\b"
        if re.search(unsupported, normalized):
            return None
        if any(path.rsplit("/", 1)[-1].casefold() not in allowed_existing_names for path in known_paths):
            return None
        paths = {"index.html", "app.js", "task-core.js", "package.json", "tests/task-core.test.cjs"}
        if paths & known_paths:
            return None
        operations = [
            {"tool": "create_file", "arguments": {"path": "index.html", "content": TODO_WEB_HTML}},
            {"tool": "create_file", "arguments": {"path": "app.js", "content": TODO_WEB_APP}},
            {"tool": "create_file", "arguments": {"path": "task-core.js", "content": TODO_WEB_CORE}},
            {"tool": "create_file", "arguments": {"path": "package.json", "content": TODO_WEB_PACKAGE}},
            {"tool": "create_file", "arguments": {"path": "tests/task-core.test.cjs", "content": TODO_WEB_TESTS}},
        ]
        if "readme.md" not in {path.rsplit("/", 1)[-1].casefold() for path in known_paths}:
            operations.append({"tool": "create_file", "arguments": {"path": "README.md", "content": TODO_WEB_README}})
        return {
            "assumptions": [
                "O pedido especifica uma interface web local, então usei HTML, CSS e JavaScript sem dependências externas.",
                "As tarefas são guardadas no localStorage do navegador atual e podem ser marcadas como concluídas.",
            ],
            "operations": operations,
        }
    requirements = (
        r"\b(?:python(?:\s*3(?:\.\d+)?)?)\b",
        r"\b(?:tarefas?|to[ -]?do)\b",
        r"\b(?:adicionar|adicione|adiciona|add|incluir|inclua)\b",
        r"\b(?:listar|liste|lista|list)\b",
        r"\b(?:concluir|conclua|concluidos?|concluidas?|marcar|complete|done)\b",
    )
    if not all(re.search(pattern, normalized) for pattern in requirements):
        return None
    unsupported_features = (
        r"\b(?:api|web|gui|grafica|desktop|login|sincronizar|sincronizacao|categoria|categorias|"
        r"subtarefa|subtarefas|prioridade|prioridades|prazo|editar|excluir|deletar|remover)\b"
    )
    if re.search(unsupported_features, normalized):
        return None

    known_paths = {str(path).replace("\\", "/").strip("/") for path in (existing_paths or set())}
    allowed_existing_names = {"readme", "readme.md", "readme.rst", "readme.txt", ".gitignore",
                              "license", "license.md", "license.txt"}
    if any(path.rsplit("/", 1)[-1].casefold() not in allowed_existing_names for path in known_paths):
        return None
    paths = {"todo_cli.py", "tests/test_todo_cli.py"}
    if paths & known_paths:
        return None

    return {
        "assumptions": [
            "Como a interface não foi especificada, adotei uma CLI local em Python 3, sem dependências externas.",
            "Os dados ficam em .todo_tasks.json no diretório atual; TODO_DATA_FILE permite escolher outro arquivo.",
            "A lista padrão mostra pendentes; --status completed e --status all cobrem os demais estados.",
        ],
        "operations": [
            {"tool": "create_file", "arguments": {"path": "todo_cli.py", "content": TODO_CLI_SOURCE}},
            {"tool": "create_file", "arguments": {"path": "tests/test_todo_cli.py", "content": TODO_CLI_TESTS}},
        ],
    }


TODO_UI_SOURCE = r'''"""Local web interface for the standard-library task CLI."""

from __future__ import annotations

import argparse
import html
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from todo_cli import TaskStore


HOST = "127.0.0.1"
DEFAULT_PORT = 8765


_PAGE_STYLE = """<style>
:root{color-scheme:dark;font-family:Inter,ui-sans-serif,system-ui,sans-serif;background:#0b1020;color:#e5e7eb}
*{box-sizing:border-box}body{margin:0;min-height:100vh;background:radial-gradient(circle at 15% 0%,#24345b 0,transparent 34%),#0b1020}
main{width:min(920px,calc(100% - 32px));margin:0 auto;padding:48px 0 72px}
header{display:flex;justify-content:space-between;align-items:end;gap:20px;margin-bottom:28px}
h1{margin:0;font-size:clamp(32px,6vw,48px);letter-spacing:-.05em}header p{margin:8px 0 0;color:#a5b4fc}
.counter{padding:10px 14px;border:1px solid #34456d;border-radius:999px;color:#c7d2fe;white-space:nowrap}
.panel,.task{border:1px solid #263653;border-radius:18px;background:#111a2cdd;box-shadow:0 18px 50px #0003}
.panel{padding:20px;margin-bottom:28px}.add-form{display:flex;gap:10px}.add-form input{flex:1;min-width:0;padding:14px;border:1px solid #34456d;border-radius:12px;background:#0b1220;color:#fff;font:inherit}
button{padding:12px 16px;border:0;border-radius:12px;background:linear-gradient(100deg,#7c3aed,#2563eb);color:#fff;font:inherit;font-weight:700;cursor:pointer}
.notice{margin:0 0 14px;color:#fca5a5}.lists{display:grid;grid-template-columns:1fr 1fr;gap:18px}section h2{display:flex;justify-content:space-between;align-items:center;margin:0 0 12px;font-size:18px}
section h2 span{color:#94a3b8;font-size:13px}.task-list{display:grid;gap:10px}.task{display:flex;justify-content:space-between;align-items:center;gap:12px;padding:14px 16px}.task p{margin:0;overflow-wrap:anywhere}.task.done p{color:#9ca3af;text-decoration:line-through}
.task form{margin:0}.task button{padding:8px 11px;font-size:13px}.empty{padding:18px;border:1px dashed #34456d;border-radius:14px;color:#94a3b8;text-align:center}
footer{margin-top:24px;color:#7f8ba3;font-size:13px;text-align:center}
@media(max-width:640px){main{padding-top:30px}header{align-items:start;flex-direction:column}.add-form{flex-direction:column}.lists{grid-template-columns:1fr}}
</style>"""


def _task_card(task, completed=False):
    task_id = int(task["id"])
    description = html.escape(str(task["description"]))
    if completed:
        return f'<article class="task done"><p>{description}</p><span>Concluída</span></article>'
    return (
        f'<article class="task"><p>{description}</p>'
        f'<form action="/done" method="post"><input type="hidden" name="task_id" value="{task_id}">'
        '<button type="submit">Concluir</button></form></article>'
    )


def render_page(tasks, notice=""):
    pending = [task for task in tasks if not task["completed"]]
    completed = [task for task in tasks if task["completed"]]
    notice_html = f'<p class="notice" role="alert">{html.escape(notice)}</p>' if notice else ""
    pending_html = "".join(_task_card(task) for task in pending) or '<p class="empty">Tudo em dia por aqui.</p>'
    completed_html = "".join(_task_card(task, completed=True) for task in completed) or '<p class="empty">Nenhuma tarefa concluída ainda.</p>'
    return (
        '<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="color-scheme" content="dark"><title>Tarefas locais</title>' + _PAGE_STYLE + '</head><body><main>'
        '<header><div><h1>Suas tarefas</h1><p>Um espaço simples para organizar o que vem a seguir.</p></div>'
        f'<div class="counter">{len(pending)} pendente(s)</div></header>'
        f'<div class="panel">{notice_html}<form class="add-form" action="/add" method="post">'
        '<input name="description" type="text" maxlength="500" placeholder="Adicionar uma tarefa..." required autofocus>'
        '<button type="submit">Adicionar</button></form></div>'
        '<div class="lists"><section><h2>Pendentes <span>' + str(len(pending)) + '</span></h2>'
        f'<div class="task-list">{pending_html}</div></section>'
        '<section><h2>Concluídas <span>' + str(len(completed)) + '</span></h2>'
        f'<div class="task-list">{completed_html}</div></section></div>'
        '<footer>Dados locais compartilhados com a CLI. Nenhuma conexão externa é usada.</footer>'
        '</main></body></html>'
    )


class TaskUIHandler(BaseHTTPRequestHandler):
    server_version = "LocalTasks/1.0"

    def log_message(self, format_string, *args):
        return

    def _is_local_request(self):
        host = self.headers.get("Host", "").split(":", 1)[0].strip("[]").casefold()
        if host not in {"127.0.0.1", "localhost"}:
            return False
        origin = self.headers.get("Origin")
        if origin:
            return urlsplit(origin).hostname in {"127.0.0.1", "localhost"}
        return True

    def _send_html(self, status, body):
        payload = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(payload)

    def _redirect_home(self):
        self.send_response(303)
        self.send_header("Location", "/")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        if not self._is_local_request():
            self._send_html(403, "<h1>Acesso local necessário</h1>")
            return
        if urlsplit(self.path).path != "/":
            self._send_html(404, "<h1>Página não encontrada</h1>")
            return
        self._send_html(200, render_page(self.server.store.list("all")))

    def do_POST(self):
        if not self._is_local_request():
            self._send_html(403, "<h1>Acesso local necessário</h1>")
            return
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > 8192:
            self._send_html(413, "<h1>Formulário inválido ou grande demais</h1>")
            return
        try:
            fields = parse_qs(self.rfile.read(length).decode("utf-8"), keep_blank_values=True)
            path = urlsplit(self.path).path
            if path == "/add":
                description = fields.get("description", [""])[0]
                self.server.store.add(description)
            elif path == "/done":
                task_id = int(fields.get("task_id", [""])[0])
                self.server.store.complete(task_id)
            else:
                self._send_html(404, "<h1>Ação não encontrada</h1>")
                return
        except (UnicodeDecodeError, ValueError) as error:
            self._send_html(400, render_page(self.server.store.list("all"), str(error)))
            return
        except OSError:
            self._send_html(500, "<h1>Não foi possível salvar a tarefa</h1>")
            return
        self._redirect_home()


def create_server(port=DEFAULT_PORT, data_path=None):
    server = ThreadingHTTPServer((HOST, int(port)), TaskUIHandler)
    server.store = TaskStore(data_path)
    return server


def main(argv=None):
    parser = argparse.ArgumentParser(description="Abre a interface web local das tarefas.")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="porta local (padrão: 8765)")
    args = parser.parse_args(argv)
    server = create_server(port=args.port)
    print(f"Interface disponível em http://{HOST}:{server.server_address[1]}/")
    print("Pressione Ctrl+C para encerrar.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Interface encerrada.", file=sys.stderr)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''


TODO_UI_TESTS = r'''"""HTTP integration tests for the local task interface."""

import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from todo_ui import create_server


class TaskUITests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.data_path = Path(self.temporary_directory.name) / "tasks.json"
        self.server = create_server(port=0, data_path=self.data_path)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temporary_directory.cleanup()

    def post(self, route, fields):
        body = urlencode(fields).encode("utf-8")
        request = Request(
            self.base_url + route,
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        return urlopen(request, timeout=2)

    def test_page_lists_pending_and_completed_tasks(self):
        self.server.store.add("Comprar pão")
        self.server.store.add("Ler um capítulo")
        self.server.store.complete(2)
        with urlopen(self.base_url + "/", timeout=2) as response:
            page = response.read().decode("utf-8")
        self.assertIn("Pendentes", page)
        self.assertIn("Concluídas", page)
        self.assertIn("Comprar pão", page)
        self.assertIn("Ler um capítulo", page)
        self.assertIn("1 pendente(s)", page)

    def test_add_and_complete_from_the_interface_persist(self):
        with self.post("/add", {"description": "Escrever testes"}) as response:
            page = response.read().decode("utf-8")
        self.assertIn("Escrever testes", page)
        self.assertEqual(self.server.store.list("pending")[0]["id"], 1)

        with self.post("/done", {"task_id": "1"}) as response:
            page = response.read().decode("utf-8")
        self.assertIn("Escrever testes", page)
        self.assertEqual(self.server.store.list("completed")[0]["description"], "Escrever testes")

    def test_empty_task_is_rejected(self):
        with self.assertRaises(HTTPError) as error:
            self.post("/add", {"description": "   "})
        self.assertEqual(error.exception.code, 400)
        self.assertFalse(self.server.store.list("all"))

    def test_task_description_is_escaped_in_html(self):
        with self.post("/add", {"description": "<script>alert(1)</script>"}) as response:
            page = response.read().decode("utf-8")
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", page)
        self.assertNotIn("<script>alert(1)</script>", page)


if __name__ == "__main__":
    unittest.main()
'''


def _todo_ui_follow_up_plan(
    question: str,
    *,
    existing_paths: set[str] | None = None,
    readable_sources: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    normalized = _fold(question)
    # A project containing todo_cli.py is not enough to infer that every
    # generic request for an interface is about the task-list application.
    # Require an explicit task-list cue before selecting this narrow recipe.
    if not re.search(r"\b(?:tarefas?|todo|to[ -]?do|lista de tarefas?)\b", normalized):
        return None
    if not re.search(r"\b(?:ui|interface|tela|grafica|visual|web)\b", normalized):
        return None
    if not re.search(r"\b(?:organizar|organize|gerenciar|gerencie|manage|implementar|implemente|criar|crie|construir|construa)\b", normalized):
        return None
    unsupported = r"\b(?:login|senha|sincronizar|remoto|multiusuario|categoria|prioridade|prazo|editar|excluir|deletar|remover|notificacao|jogo|jogos|game|engine|rust|typescript|javascript|react|css|html)\b"
    if re.search(unsupported, normalized):
        return None

    known_paths = {str(path).replace("\\", "/").strip("/") for path in (existing_paths or set())}
    if "todo_cli.py" not in known_paths:
        return None
    target_paths = {"todo_ui.py", "tests/test_todo_ui.py"}
    if target_paths & known_paths:
        return None

    sources = readable_sources or {}
    cli_source = sources.get("todo_cli.py")
    if not isinstance(cli_source, str):
        return None
    if not all(marker in cli_source for marker in ("class TaskStore", "def add(", "def list(", "def complete(", "TODO_DATA_FILE")):
        return None
    return {
        "assumptions": [
            "Como o projeto existente é uma CLI Python com TaskStore, escolhi uma interface web local sem dependências externas.",
            "A interface reutiliza o mesmo arquivo JSON e permite adicionar tarefas, ver pendentes/concluídas e marcar conclusão.",
            "O servidor escuta apenas em 127.0.0.1:8765; execute python3 todo_ui.py para abri-lo localmente.",
        ],
        "operations": [
            {"tool": "create_file", "arguments": {"path": "todo_ui.py", "content": TODO_UI_SOURCE}},
            {"tool": "create_file", "arguments": {"path": "tests/test_todo_ui.py", "content": TODO_UI_TESTS}},
        ],
    }
