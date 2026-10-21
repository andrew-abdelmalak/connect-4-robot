// Web Worker: runs the repository's own Python in Pyodide. The Pages build
// copies src/connect4_robot/*.py into py/ unchanged, so the page judges moves,
// plans the robot's reply, and reads the board photo with the same code the
// robot ran. The page only draws and animates.

const PYODIDE = "https://cdn.jsdelivr.net/pyodide/v314.0.7/full/";
const GAME_MODULES = ["__init__.py", "board.py", "minimax.py", "game_state.py"];
const VISION_MODULES = ["vision.py"];

let py = null;
let visionReady = null;

async function install(names) {
  for (const name of names) {
    const res = await fetch("py/" + name);
    if (!res.ok) throw new Error(`could not load ${name} (${res.status})`);
    py.FS.writeFile("/home/pyodide/connect4_robot/" + name, await res.text());
  }
}

async function boot() {
  const { loadPyodide } = await import(PYODIDE + "pyodide.mjs");
  py = await loadPyodide({ indexURL: PYODIDE });
  py.FS.mkdirTree("/home/pyodide/connect4_robot");
  await install(GAME_MODULES);
  py.runPython(`
import json, sys
sys.path.insert(0, "/home/pyodide")
from connect4_robot.board import apply_move, lowest_empty_row
from connect4_robot.game_state import GameState

gs = GameState()

def accept_json(board_json):
    ok, reason = gs.accept(json.loads(board_json))
    return json.dumps({"ok": ok, "reason": reason, "state": gs.to_dict()})

def drop_json(col, player, board_json=None):
    board = json.loads(board_json) if board_json else gs.trusted
    return json.dumps(apply_move(board, col, player))

def lowest_json(col):
    return json.dumps(lowest_empty_row(gs.trusted, col))

def reset_json():
    gs.reset()
    return json.dumps(gs.to_dict())
`);
}

// OpenCV and NumPy (about 14 MB) load only when the visitor asks for the vision demo.
async function bootVision() {
  await py.loadPackage(["numpy", "opencv-python"]);
  await install(VISION_MODULES);
  const photo = await fetch("img/board-sample.png");
  if (!photo.ok) throw new Error(`could not load the board photo (${photo.status})`);
  py.FS.writeFile("/home/pyodide/board-sample.png", new Uint8Array(await photo.arrayBuffer()));
  py.runPython(`
import base64
import cv2
from connect4_robot.vision import CellClassifier, VisionPipeline, encode_jpeg, load_corners, warp_and_condition

PHOTO = cv2.imread("/home/pyodide/board-sample.png")

def vision_json(mode):
    vp = VisionPipeline(CellClassifier())
    if mode == "auto":
        warped = vp.preprocess(PHOTO)  # what the robot does with every frame
    else:
        vp._src = load_corners(PHOTO.shape)  # the grid's own corners
        warped = warp_and_condition(PHOTO, vp._src)
    board, debug = vp.detect_board(warped)
    overlay = vp.draw_detection_overlay(PHOTO)
    b64 = lambda img, q: base64.b64encode(encode_jpeg(img, q)).decode()
    return json.dumps({
        "board": board,
        "detected": bool(vp.board_detected),
        "overlay": b64(cv2.resize(overlay, None, fx=0.5, fy=0.5), 80),
        "grid": b64(cv2.resize(debug, None, fx=0.6, fy=0.6), 80),
    })
`);
}

const ready = boot();

self.onmessage = async (event) => {
  const { id, op, args } = event.data;
  try {
    await ready;
    if (op === "vision") await (visionReady ??= bootVision());
    const fn = py.globals.get(op + "_json");
    if (!fn) throw new Error("unknown op " + op);
    const t0 = performance.now();
    let out;
    try {
      out = fn(...(args || []));
    } finally {
      fn.destroy();
    }
    self.postMessage({ id, ok: true, value: JSON.parse(out), ms: performance.now() - t0 });
  } catch (err) {
    if (op === "vision") visionReady = null;
    self.postMessage({ id, ok: false, error: String(err && err.message ? err.message : err) });
  }
};

ready.then(
  () => self.postMessage({ id: 0, ok: true, value: "ready" }),
  (err) => self.postMessage({ id: 0, ok: false, error: String(err) }),
);
