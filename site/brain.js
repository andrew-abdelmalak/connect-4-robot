// Web Worker: runs the repository's own Python in Pyodide. The Pages build
// copies src/connect4_robot/*.py into py/ unchanged, so the page judges moves,
// plans the robot's reply, and reads every camera frame with the same code the
// robot ran. The glue below only calls that code and reports what it did.

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
import connect4_robot.minimax as mm
from connect4_robot.board import apply_move, get_legal_cols, lowest_empty_row
from connect4_robot.game_state import GameState

gs = GameState()

# Count the positions the robot's search visits: its recursion calls the
# module-level name, so wrapping that name counts every node.
search = mm._minimax
nodes = 0
def counted(*args):
    global nodes
    nodes += 1
    return search(*args)
mm._minimax = counted

def accept_json(board_json):
    global nodes
    nodes = 0
    ok, reason = gs.accept(json.loads(board_json))
    return json.dumps({"ok": ok, "reason": reason, "state": gs.to_dict(), "nodes": nodes})

def depth_json(depth):
    gs.depth = int(depth)
    return json.dumps(gs.depth)

# The score of each column the robot could play, searched with a full window
# so every number is exact. The robot's own pruned search picks the first
# column, centre first, with the top score; tests/test_minimax.py checks that.
def think_json():
    scores = None
    if gs.mm_ran and gs.mm_reason.startswith("Minimax"):
        robot, user = gs.robot_color, gs.user_color
        scores = {c: int(search(apply_move(gs.trusted, c, robot), gs.depth - 1, -mm.INF, mm.INF, False, robot, user)[0])
                  for c in get_legal_cols(gs.trusted)}
    return json.dumps({"col": gs.mm_col, "reason": gs.mm_reason, "time": gs.mm_time, "depth": gs.depth, "scores": scores})

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

// OpenCV and NumPy (about 14 MB) load after the game, so the board is playable first.
async function bootVision() {
  await py.loadPackage(["numpy", "opencv-python"]);
  await install(VISION_MODULES);
  py.runPython(`
import base64
import cv2
from connect4_robot.vision import CellClassifier, VisionPipeline, encode_jpeg

b64 = lambda img, q: base64.b64encode(encode_jpeg(img, q)).decode()

# One pipeline per game, like the robot's camera thread: corner smoothing carries over.
camera = None

def frame_json(path):
    global camera
    camera = camera or VisionPipeline(CellClassifier())
    frame = cv2.imread(path)
    board, debug = camera.detect_board(camera.preprocess(frame))
    overlay = camera.draw_detection_overlay(frame)
    return json.dumps({
        "board": board,
        "detected": bool(camera.board_detected),
        "overlay": b64(cv2.resize(overlay, None, fx=0.5, fy=0.5), 70),
        "grid": b64(cv2.resize(debug, None, fx=0.3, fy=0.3), 70),
    })

def camera_reset_json():
    global camera
    camera = None
    return "null"
`);
}

const ready = boot();
const VISION_OPS = new Set(["vision_load", "frame", "camera_reset"]);

self.onmessage = async (event) => {
  const { id, op, args } = event.data;
  try {
    await ready;
    if (VISION_OPS.has(op)) await (visionReady ??= bootVision().catch((err) => { visionReady = null; throw err; }));
    if (op === "vision_load") return self.postMessage({ id, ok: true, value: true });
    // A camera frame arrives as JPEG bytes; the vision code reads it from a file, as cv2 does.
    if (op === "frame") py.FS.writeFile("/home/pyodide/frame.jpg", args[0]);
    const fn = py.globals.get(op + "_json");
    if (!fn) throw new Error("unknown op " + op);
    const t0 = performance.now();
    let out;
    try {
      out = op === "frame" ? fn("/home/pyodide/frame.jpg") : fn(...(args || []));
    } finally {
      fn.destroy();
    }
    self.postMessage({ id, ok: true, value: JSON.parse(out), ms: performance.now() - t0 });
  } catch (err) {
    self.postMessage({ id, ok: false, error: String(err && err.message ? err.message : err) });
  }
};

ready.then(
  () => self.postMessage({ id: 0, ok: true, value: "ready" }),
  (err) => self.postMessage({ id: 0, ok: false, error: String(err) }),
);
