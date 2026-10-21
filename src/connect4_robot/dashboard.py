"""Flask operator dashboard: live streams, board states, and robot controls.

Run on the Raspberry Pi with ``python -m connect4_robot`` (camera mode) or
``python -m connect4_robot --sim`` (scripted states, no camera or Arduino).
The server listens on every interface without authentication, so run it only
on a trusted network.
"""

import argparse
import socket
import subprocess
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import cv2
import numpy as np
from flask import Flask, Response, jsonify, request

from .board import COLS, EMPTY, P1, P2, ROWS, Board
from .game_state import GameState
from .hardware import SERIAL_BAUD, SERIAL_PORT, Camera, RobotController
from .minimax import MINIMAX_DEPTH
from .simulator import Simulator
from .vision import CellClassifier

try:
    import resource  # POSIX only (the Pi); absent on Windows laptops
except ImportError:
    resource = None

STREAM_PERIOD = 0.25  # seconds between MJPEG frames
MOCK_STREAM_PERIOD = 1.0
DEFAULT_PORT = 5000
CELL_PX = 60  # rendered board cell size
MJPEG_MIMETYPE = "multipart/x-mixed-replace; boundary=frame"
HTML = (Path(__file__).parent / "static" / "dashboard.html").read_text(encoding="utf-8")

app = Flask(__name__)
gs = GameState()
sim = Simulator()
cell_classifier = CellClassifier()
camera = Camera(cell_classifier)
STATE_LOCK = threading.Lock()
robot = RobotController(camera, gs, STATE_LOCK)
APP_MODE = "simulation"  # overridden at startup


def set_app_mode(mode: str) -> None:
    """Switch between "simulation" and "camera" for the routes and the robot loop."""
    global APP_MODE  # noqa: PLW0603 - one process-wide mode, set once at startup
    APP_MODE = mode
    robot.camera_mode = mode == "camera"


def _sys_info() -> dict:
    info = {}
    if resource is not None:
        try:
            r = subprocess.run(
                ["/usr/bin/vcgencmd", "measure_temp"],
                capture_output=True,
                text=True,
                check=False,
                timeout=2,
            )
            info["temp"] = r.stdout.strip().replace("temp=", "").replace("'C", " C")
        except (OSError, subprocess.SubprocessError):
            info["temp"] = "N/A"
        info["mem_mb"] = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)
    return info


def _render_board_jpg(board: Board | None, highlight_col: int | None = None) -> bytes | None:
    """Render board matrix as a JPEG image for MJPEG streams."""
    if board is None:
        return None
    cw = ch = CELL_PX
    img = np.full((ROWS * ch + 30, COLS * cw, 3), (25, 90, 180), dtype=np.uint8)
    chip = {EMPTY: (12, 12, 20), P1: (30, 30, 220), P2: (20, 210, 200)}
    for r in range(ROWS):
        for c in range(COLS):
            cx = c * cw + cw // 2
            cy = r * ch + ch // 2
            cv2.circle(img, (cx, cy), cw // 2 - 4, chip[board[r][c]], -1)
            cv2.circle(img, (cx, cy), cw // 2 - 4, (0, 0, 0), 1)
    if highlight_col is not None and 0 <= highlight_col < COLS:
        hx = highlight_col * cw + cw // 2
        cv2.arrowedLine(
            img,
            (hx, ROWS * ch + 25),
            (hx, ROWS * ch + 5),
            (0, 255, 120),
            2,
            tipLength=0.5,
        )
    for c in range(COLS):
        cv2.putText(
            img,
            str(c),
            (c * cw + cw // 2 - 5, ROWS * ch + 22),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            (200, 200, 200),
            1,
        )
    _, buf = cv2.imencode(".jpg", img)
    return buf.tobytes()


def _mjpeg(getter: Callable[[], bytes | None]) -> Iterator[bytes]:
    while True:
        jpg = getter()
        if jpg:
            yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpg + b"\r\n"
        time.sleep(STREAM_PERIOD)


def _mock_stream(label: str) -> Iterator[bytes]:
    while True:
        img = np.zeros((240, 380, 3), dtype=np.uint8)
        img[:] = (18, 18, 28)
        cv2.putText(
            img,
            "SIMULATION MODE",
            (10, 55),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.85,
            (255, 255, 255),
            2,
        )
        cv2.putText(img, label, (10, 105), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (140, 140, 140), 1)
        cv2.putText(
            img,
            "No camera connected",
            (10, 155),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            (60, 180, 60),
            1,
        )
        _, buf = cv2.imencode(".jpg", img)
        yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + buf.tobytes() + b"\r\n"
        time.sleep(MOCK_STREAM_PERIOD)


# ── routes ────────────────────────────────────────────────────────────────────


@app.route("/")
def index() -> str:
    return HTML


@app.route("/status")
def status() -> Response:
    with STATE_LOCK:
        data = gs.to_dict()
    data["mode"] = APP_MODE
    data["sim_idx"] = sim.index
    data["sim_total"] = sim.total
    data["sim_desc"] = sim.current()[0]
    data["cam_connected"] = camera.connected
    data["board_detected"] = camera.board_detected
    data["detection_age"] = camera.detection_age
    data.update(robot.to_dict())
    data.update(cell_classifier.to_dict())
    data.update(_sys_info())
    return jsonify(data)


@app.route("/step_sim", methods=["POST"])
def step_sim() -> Response | tuple[Response, int]:
    if APP_MODE != "simulation":
        return jsonify({"error": "Not in simulation mode"}), 400
    desc, board = sim.advance()
    with STATE_LOCK:
        accepted, reason = gs.accept(board)
    return jsonify(
        {
            "desc": desc,
            "accepted": accepted,
            "reason": reason,
            "status": gs.to_dict(),
            "sim_idx": sim.index,
            "sim_total": sim.total,
        }
    )


@app.route("/accept_camera", methods=["POST"])
def accept_camera() -> Response | tuple[Response, int]:
    if APP_MODE != "camera":
        return jsonify({"error": "Not in camera mode"}), 400
    with camera.lock:
        cand = camera.candidate
    if cand is None:
        return jsonify({"error": "No candidate board yet"}), 400
    with STATE_LOCK:
        accepted, reason = gs.accept(cand)
        if accepted and gs.whose_turn == "robot":
            robot.maybe_dispatch_robot_locked()
    return jsonify({"accepted": accepted, "reason": reason, "status": gs.to_dict()})


@app.route("/set_mode", methods=["POST"])
def set_mode() -> Response | tuple[Response, int]:
    payload = request.get_json(silent=True) or {}
    mode = payload.get("mode")
    ok, msg = robot.set_mode(mode)
    if not ok:
        return jsonify({"error": msg}), 400
    with STATE_LOCK:
        robot.maybe_dispatch_robot_locked()
        status_dict = gs.to_dict()
    return jsonify({"ok": True, "message": msg, "status": status_dict, "robot": robot.to_dict()})


@app.route("/set_classifier", methods=["POST"])
def set_classifier() -> Response | tuple[Response, int]:
    payload = request.get_json(silent=True) or {}
    mode = payload.get("mode")
    if payload.get("reload"):
        cell_classifier.reload()
    ok, msg = cell_classifier.set_mode(mode)
    if not ok:
        return jsonify({"error": msg}), 400
    return jsonify({"ok": True, "message": msg, "classifier": cell_classifier.to_dict()})


@app.route("/robot_move", methods=["POST"])
def robot_move() -> Response | tuple[Response, int]:
    with STATE_LOCK:
        if gs.whose_turn != "robot" or gs.robot_color is None or gs.mm_col is None:
            return jsonify({"error": "Robot move is not ready"}), 400
        ok, msg = robot.dispatch_move(gs.trusted, gs.robot_color, gs.mm_col)
        if ok:
            gs.status_msg = msg
        status_dict = gs.to_dict()
    if not ok:
        return jsonify({"error": msg, "status": status_dict}), 400
    return jsonify({"ok": True, "message": msg, "status": status_dict, "robot": robot.to_dict()})


@app.route("/reset", methods=["POST"])
def reset_game() -> Response:
    with STATE_LOCK:
        gs.reset()
        sim.reset()
        robot.reset()
    return jsonify({"ok": True})


@app.route("/raw")
def raw() -> Response:
    if APP_MODE == "camera":
        return Response(_mjpeg(lambda: camera.raw_jpg), mimetype=MJPEG_MIMETYPE)
    return Response(_mock_stream("Raw Camera"), mimetype=MJPEG_MIMETYPE)


@app.route("/processed")
def processed() -> Response:
    if APP_MODE == "camera":
        return Response(_mjpeg(lambda: camera.proc_jpg), mimetype=MJPEG_MIMETYPE)
    return Response(
        _mjpeg(lambda: _render_board_jpg(gs.trusted, gs.mm_col if gs.mm_ran else None)),
        mimetype=MJPEG_MIMETYPE,
    )


@app.route("/debug_grid")
def debug_grid() -> Response:
    if APP_MODE == "camera":
        return Response(_mjpeg(lambda: camera.debug_jpg), mimetype=MJPEG_MIMETYPE)
    return Response(
        _mjpeg(lambda: _render_board_jpg(gs.candidate if gs.candidate is not None else gs.trusted, None)),
        mimetype=MJPEG_MIMETYPE,
    )


def _local_ip() -> str:
    """Best-effort LAN address for the startup banner (no packet is sent)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except OSError:
        return "localhost"


def main() -> None:
    """Parse arguments and start the dashboard."""
    parser = argparse.ArgumentParser(description="Connect 4 Brain Dashboard")
    parser.add_argument("--sim", action="store_true", help="Simulation mode (no camera)")
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--depth", type=int, default=MINIMAX_DEPTH)
    args = parser.parse_args()

    gs.depth = args.depth
    set_app_mode("simulation" if args.sim else "camera")

    print("=== Connect 4 Brain Dashboard ===")
    print(f"Mode      : {APP_MODE}")
    print(f"Minimax   : depth {gs.depth}")
    print(f"Classifier: {cell_classifier.mode} ({'loaded' if cell_classifier.loaded else 'fallback'})")

    if APP_MODE == "camera":
        camera.start()
        robot.start()
        print("Camera    : starting (1 FPS analysis, 5 FPS preview)")
    else:
        print(f"Sim states: {sim.total} steps loaded")

    print(f"Dashboard : http://{_local_ip()}:{args.port}")
    print(f"Serial    : {SERIAL_PORT} @ {SERIAL_BAUD}")
    print()

    app.run(host="0.0.0.0", port=args.port, threaded=True)  # noqa: S104 - opened from a laptop on the LAN


if __name__ == "__main__":
    main()
