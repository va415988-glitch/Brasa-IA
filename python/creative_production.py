"""Blueprints determinísticos para produtos criativos verificáveis.

Esta camada não substitui pesquisa, design ou geração livre. Ela fornece a
primeira entrega segura para um workspace vazio depois que o agente já
inspecionou o projeto e pesquisou a tecnologia necessária.
"""

from __future__ import annotations

import re

from dialogue import is_game_development_request


def _request_summary(question: str) -> str:
    return re.sub(r"\s+", " ", str(question or "")).strip()[:500]


def game_plan_markdown(question: str) -> str:
    objective = _request_summary(question)
    return f"""# Plano do jogo

## Objetivo

{objective}

## Primeira entrega jogável

Construir um protótipo 2D local em TypeScript que tenha estado explícito,
movimentação por teclado, objetivo verificável, pontuação e testes para a
lógica independente da interface.

## Princípios

- A lógica do jogo fica separada da renderização.
- O protótipo começa com formas e recursos locais para não depender de assets externos.
- Estado, movimento, coleta e condição de vitória devem ser testáveis sem navegador.
- Toda dependência adicionada precisa ter finalidade clara.
- A implementação deve terminar com execução, testes e instruções de uso.

## Escopo inicial

1. Criar o manifesto e a configuração TypeScript.
2. Implementar o núcleo de estado e regras.
3. Criar uma interface Canvas mínima e jogável.
4. Adicionar testes para movimento, limites, coleta e vitória.
5. Executar as verificações antes de ampliar narrativa, arte ou áudio.

## Critérios de aceite

- O jogador se move com teclado dentro dos limites do mapa.
- Fragmentos podem ser coletados uma única vez.
- A pontuação acompanha o estado real.
- A condição de vitória é determinística.
- Os testes automatizados passam.
- O projeto explica como testar, compilar e executar.

## Evolução planejada

Depois do núcleo validado: fases, narrativa, diálogos, inventário, áudio,
salvamento, acessibilidade, balanceamento e empacotamento.
"""


def game_scaffold(question: str) -> dict[str, tuple[str, str, str]]:
    """Retorna uma cadeia ordenada de arquivos para o primeiro protótipo."""
    if not is_game_development_request(question):
        return {}
    return {
        "JOGO_PLANO.md": (
            "package.json",
            """{
  "name": "prototipo-jogo-local",
  "private": true,
  "type": "module",
  "scripts": {
    "test": "node --experimental-strip-types --test tests/game.test.js",
    "typecheck": "tsc --noEmit",
    "build": "tsc",
    "start": "python3 -m http.server 4173"
  },
  "engines": {
    "node": ">=22.6"
  },
  "devDependencies": {
    "typescript": "^5.6.3"
  }
}
""",
            "Criar o manifesto do protótipo TypeScript com testes, build e execução local.",
        ),
        "package.json": (
            "tsconfig.json",
            """{
  "compilerOptions": {
    "target": "ES2022",
    "module": "ES2022",
    "moduleResolution": "Bundler",
    "strict": true,
    "outDir": "dist",
    "rootDir": "src",
    "lib": ["ES2022", "DOM"],
    "skipLibCheck": true
  },
  "include": ["src/**/*.ts"]
}
""",
            "Configurar compilação estrita para lógica e interface Canvas.",
        ),
        "tsconfig.json": (
            "src/game.ts",
            """export type Direction = "up" | "down" | "left" | "right";

export type Position = Readonly<{x: number; y: number}>;

export type GameState = Readonly<{
  width: number;
  height: number;
  player: Position;
  fragments: readonly Position[];
  score: number;
}>;

export function createGameState(width = 10, height = 8): GameState {
  return {
    width,
    height,
    player: {x: 1, y: 1},
    fragments: [{x: 3, y: 2}, {x: 7, y: 5}],
    score: 0,
  };
}

export function movePlayer(state: GameState, direction: Direction): GameState {
  const delta: Record<Direction, Position> = {
    up: {x: 0, y: -1},
    down: {x: 0, y: 1},
    left: {x: -1, y: 0},
    right: {x: 1, y: 0},
  };
  const next = {
    x: Math.max(0, Math.min(state.width - 1, state.player.x + delta[direction].x)),
    y: Math.max(0, Math.min(state.height - 1, state.player.y + delta[direction].y)),
  };
  return {...state, player: next};
}

export function collectFragment(state: GameState): GameState {
  const remaining = state.fragments.filter(
    fragment => fragment.x !== state.player.x || fragment.y !== state.player.y,
  );
  if (remaining.length === state.fragments.length) return state;
  return {...state, fragments: remaining, score: state.score + 1};
}

export function isComplete(state: GameState): boolean {
  return state.fragments.length === 0;
}
""",
            "Implementar o núcleo puro e testável das regras do jogo.",
        ),
        "src/game.ts": (
            "src/main.ts",
            """import {
  collectFragment,
  createGameState,
  isComplete,
  movePlayer,
  type Direction,
} from "./game.js";

const canvas = document.querySelector<HTMLCanvasElement>("#game");
const status = document.querySelector<HTMLElement>("#status");
if (!canvas || !status) throw new Error("Interface do jogo não encontrada.");
const context = canvas.getContext("2d");
if (!context) throw new Error("Canvas 2D indisponível.");

let state = createGameState();
const cell = 48;

function render(): void {
  context.fillStyle = "#08111f";
  context.fillRect(0, 0, canvas.width, canvas.height);
  context.strokeStyle = "#18314f";
  for (let x = 0; x <= state.width; x += 1) {
    context.beginPath();
    context.moveTo(x * cell, 0);
    context.lineTo(x * cell, state.height * cell);
    context.stroke();
  }
  for (let y = 0; y <= state.height; y += 1) {
    context.beginPath();
    context.moveTo(0, y * cell);
    context.lineTo(state.width * cell, y * cell);
    context.stroke();
  }
  context.fillStyle = "#f6c85f";
  for (const fragment of state.fragments) {
    context.beginPath();
    context.arc(fragment.x * cell + cell / 2, fragment.y * cell + cell / 2, 10, 0, Math.PI * 2);
    context.fill();
  }
  context.fillStyle = "#54d6ff";
  context.fillRect(state.player.x * cell + 8, state.player.y * cell + 8, cell - 16, cell - 16);
  status.textContent = isComplete(state)
    ? "Todos os fragmentos foram recuperados."
    : "Fragmentos: " + state.score + " / " + (state.score + state.fragments.length);
}

const keyMap: Record<string, Direction> = {
  ArrowUp: "up",
  ArrowDown: "down",
  ArrowLeft: "left",
  ArrowRight: "right",
  w: "up",
  s: "down",
  a: "left",
  d: "right",
};

window.addEventListener("keydown", event => {
  const direction = keyMap[event.key];
  if (!direction) return;
  event.preventDefault();
  state = collectFragment(movePlayer(state, direction));
  render();
});

render();
""",
            "Conectar o núcleo do jogo a uma interface Canvas controlável por teclado.",
        ),
        "src/main.ts": (
            "tests/game.test.js",
            """import assert from "node:assert/strict";
import test from "node:test";
import {
  collectFragment,
  createGameState,
  isComplete,
  movePlayer,
} from "../src/game.ts";

test("movimento respeita os limites do mapa", () => {
  let state = createGameState(4, 4);
  state = movePlayer(state, "left");
  state = movePlayer(state, "left");
  assert.deepEqual(state.player, {x: 0, y: 1});
});

test("fragmento é coletado uma única vez", () => {
  let state = createGameState();
  state = movePlayer(movePlayer(movePlayer(state, "right"), "right"), "down");
  state = collectFragment(state);
  assert.equal(state.score, 1);
  assert.equal(state.fragments.length, 1);
  assert.equal(collectFragment(state), state);
});

test("vitória depende da ausência de fragmentos", () => {
  const state = {...createGameState(), fragments: []};
  assert.equal(isComplete(state), true);
});
""",
            "Adicionar testes executáveis para regras, limites, coleta e vitória.",
        ),
        "tests/game.test.js": (
            "index.html",
            """<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Memórias da Estação</title>
  <link rel="stylesheet" href="styles.css">
</head>
<body>
  <main>
    <p class="eyebrow">Protótipo local</p>
    <h1>Memórias da Estação</h1>
    <p>Use as setas ou WASD para recuperar os fragmentos.</p>
    <canvas id="game" width="480" height="384" aria-label="Mapa do jogo"></canvas>
    <p id="status" aria-live="polite"></p>
  </main>
  <script type="module" src="dist/main.js"></script>
</body>
</html>
""",
            "Criar a página jogável que carrega o build TypeScript.",
        ),
        "index.html": (
            "styles.css",
            """* { box-sizing: border-box; }
body {
  margin: 0;
  min-height: 100vh;
  display: grid;
  place-items: center;
  color: #e8f1ff;
  background: radial-gradient(circle at top, #163250, #050914 62%);
  font-family: Inter, system-ui, sans-serif;
}
main { width: min(92vw, 620px); padding: 32px; }
.eyebrow { color: #54d6ff; letter-spacing: .16em; text-transform: uppercase; }
h1 { margin: 0 0 8px; font-size: clamp(2rem, 7vw, 4rem); }
canvas {
  display: block;
  width: 100%;
  height: auto;
  margin-top: 24px;
  border: 1px solid #31547a;
  border-radius: 14px;
  box-shadow: 0 24px 80px #0009;
}
#status { min-height: 1.5em; color: #f6c85f; }
""",
            "Aplicar uma apresentação responsiva ao primeiro protótipo.",
        ),
    }
