// Web Worker: runs the repository's own Python game modules in Pyodide.
// The page sends candidate boards; GameState.accept (the six-rule validator)
// and get_best_move (depth-5 minimax) answer exactly as they do on the robot.

const PYODIDE = "https://cdn.jsdelivr.net/pyodide/v314.0.7/full/";
const MODULES = ["__init__.py", "board.py", "minimax.py", "game_state.py"];

let py = null;

async function boot() {
  const { loadPyodide } = await import(PYODIDE + "pyodide.mjs");
  py = await loadPyodide({ indexURL: PYODIDE });
  py.FS.mkdirTree("/home/pyodide/connect4_robot");
  for (const name of MODULES) {
    const res = await fetch("py/" + name);
    if (!res.ok) throw new Error(`could not load ${name} (${res.status})`);
    py.FS.writeFile("/home/pyodide/connect4_robot/" + name, await res.text());
  }
  py.runPython(`
import json, sys
sys.path.insert(0, "/home/pyodide")
from connect4_robot.board import apply_move
from connect4_robot.game_state import GameState

gs = GameState()

def accept_json(board_json):
    ok, reason = gs.accept(json.loads(board_json))
    return json.dumps({"ok": ok, "reason": reason, "state": gs.to_dict()})

def drop_json(col, player):
    return json.dumps(apply_move(gs.trusted, col, player))

def reset_json():
    gs.reset()
    return json.dumps(gs.to_dict())
`);
}

const ready = boot();

self.onmessage = async (event) => {
  const { id, op, args } = event.data;
  try {
    await ready;
    const fn = py.globals.get(op + "_json");
    if (!fn) throw new Error("unknown op " + op);
    const t0 = performance.now();
    const out = fn(...(args || []));
    fn.destroy();
    self.postMessage({ id, ok: true, value: JSON.parse(out), ms: performance.now() - t0 });
  } catch (err) {
    self.postMessage({ id, ok: false, error: String(err && err.message ? err.message : err) });
  }
};

ready.then(
  () => self.postMessage({ id: 0, ok: true, value: "ready" }),
  (err) => self.postMessage({ id: 0, ok: false, error: String(err) }),
);
