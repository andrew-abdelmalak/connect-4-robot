"""Camera thread and Arduino serial controller for the physical robot.

The camera thread publishes JPEG previews and the latest candidate board. The
robot controller sends a column digit ('1'..'7') over serial, then waits for
the camera to show exactly the expected board before the move counts.
"""

import threading
import time

import cv2
import serial

from .board import Board, apply_move, board_copy, boards_equal
from .game_state import GameState
from .vision import CellClassifier, VisionPipeline

ANALYSIS_INTERVAL = 1.0  # seconds between full board analyses (camera mode)
PREVIEW_INTERVAL = 0.2  # seconds between preview frames (~5 FPS)
FRAME_SLEEP = 0.02  # seconds between camera reads
PREVIEW_JPEG_QUALITY = 70
ANALYSIS_JPEG_QUALITY = 80
SERIAL_PORT = "/dev/ttyUSB0"
SERIAL_BAUD = 9600
SERIAL_TIMEOUT = 0.1
ROBOT_SETTLE_SEC = 2.5
VERIFY_TIMEOUT = 10.0
VERIFY_POLL_SEC = 0.4

MODE_MANUAL = "manual"
MODE_SEMI_AUTO = "semi_auto"
MODE_AUTO = "auto"
VALID_OPERATOR_MODES = {MODE_MANUAL, MODE_SEMI_AUTO, MODE_AUTO}


class Camera:
    """Background capture loop that keeps the latest frames and candidate board."""

    def __init__(self, classifier: CellClassifier) -> None:
        self.classifier = classifier
        self.raw_jpg: bytes | None = None
        self.proc_jpg: bytes | None = None
        self.debug_jpg: bytes | None = None
        self.candidate: Board | None = None
        self.lock = threading.Lock()
        self.connected = False
        self.board_detected = False
        self.detection_age = 0

    def start(self) -> None:
        """Start the capture thread."""
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self) -> None:
        vp = VisionPipeline(self.classifier)
        try:
            cap = cv2.VideoCapture(0)
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            if not cap.isOpened():
                print("Camera: could not open /dev/video0")
                return
            self.connected = True
            print("Camera: connected")
        except Exception as e:  # noqa: BLE001 - camera errors end the thread with a message
            print(f"Camera: open error — {e}")
            return

        last_analysis = 0.0
        last_preview = 0.0
        while True:
            now = time.time()
            ret, frame = cap.read()
            if not ret:
                time.sleep(0.1)
                continue
            if now - last_preview >= PREVIEW_INTERVAL:
                last_preview = now
                annotated = vp.draw_detection_overlay(frame)
                _, buf = cv2.imencode(".jpg", annotated, [cv2.IMWRITE_JPEG_QUALITY, PREVIEW_JPEG_QUALITY])
                with self.lock:
                    self.raw_jpg = buf.tobytes()
            if now - last_analysis >= ANALYSIS_INTERVAL:
                last_analysis = now
                warped = vp.preprocess(frame)
                board, dbg = vp.detect_board(warped)
                _, b2 = cv2.imencode(".jpg", warped, [cv2.IMWRITE_JPEG_QUALITY, ANALYSIS_JPEG_QUALITY])
                _, b3 = cv2.imencode(".jpg", dbg, [cv2.IMWRITE_JPEG_QUALITY, ANALYSIS_JPEG_QUALITY])
                with self.lock:
                    self.proc_jpg = b2.tobytes()
                    self.debug_jpg = b3.tobytes()
                    self.candidate = board
                    self.board_detected = vp.board_detected
                    self.detection_age = vp._detection_age
            time.sleep(FRAME_SLEEP)


class RobotController:
    """Dispatches robot moves over serial and verifies them with the camera."""

    def __init__(self, camera: Camera, game: GameState, state_lock: threading.Lock) -> None:
        self.camera = camera
        self.game = game
        self.state_lock = state_lock
        self.camera_mode = False
        self.lock = threading.Lock()
        self.mode = MODE_MANUAL
        self.ser = None
        self.connected = False
        self.busy = False
        self.awaiting_verification = False
        self.last_command: str | None = None
        self.last_sent_col: int | None = None
        self.last_sent_at: float | None = None
        self.last_done_at: float | None = None
        self.last_ack = "No serial activity yet."
        self.last_error: str | None = None
        self.state = "idle"
        self.state_msg = "Manual mode — waiting for confirmation."
        self.expected_board: Board | None = None
        self.expected_col: int | None = None
        self.verify_deadline: float | None = None
        self.settle_until: float | None = None
        self._started = False

    def start(self) -> None:
        """Start the serial and verification loop (once)."""
        if self._started:
            return
        self._started = True
        threading.Thread(target=self._loop, daemon=True).start()

    def reset(self) -> None:
        """Forget any pending move and return to idle."""
        with self.lock:
            self.busy = False
            self.awaiting_verification = False
            self.last_command = None
            self.last_sent_col = None
            self.last_sent_at = None
            self.last_done_at = None
            self.last_error = None
            self.state = "idle"
            self.state_msg = "Reset — waiting for user move."
            self.expected_board = None
            self.expected_col = None
            self.verify_deadline = None
            self.settle_until = None

    def set_mode(self, mode: str) -> tuple[bool, str]:
        """Switch operator mode: manual, semi_auto, or auto. Returns (ok, message)."""
        if mode not in VALID_OPERATOR_MODES:
            return False, f"Unknown mode: {mode}"
        with self.lock:
            self.mode = mode
            if mode == MODE_MANUAL:
                self.state_msg = "Manual mode — user confirms and dispatches."
            elif mode == MODE_SEMI_AUTO:
                self.state_msg = "Semi-auto mode — clean detections auto-accept."
            else:
                self.state_msg = "Auto mode — accepting clean detections and dispatching."
        return True, f"Mode set to {mode}"

    def _ensure_serial(self) -> bool:
        if self.ser is not None:
            self.connected = True
            return True
        try:
            self.ser = serial.Serial(SERIAL_PORT, SERIAL_BAUD, timeout=SERIAL_TIMEOUT)
            self.connected = True
            self.last_error = None
            self.last_ack = f"Connected on {SERIAL_PORT}"
            return True
        except (serial.SerialException, OSError, ValueError) as e:
            self.ser = None
            self.connected = False
            self.last_error = f"Serial unavailable — {e}"
            return False

    def _read_serial(self) -> None:
        if self.ser is None:
            return
        try:
            while self.ser.in_waiting:
                line = self.ser.readline().decode("utf-8", "replace").strip()
                if not line:
                    continue
                self.last_ack = line
                if "DONE" in line or "Cycle Complete" in line:
                    self.last_done_at = time.time()
        except (serial.SerialException, OSError) as e:
            self.last_error = f"Serial read failed — {e}"
            self._close_serial()

    def _close_serial(self) -> None:
        if self.ser is not None:
            try:
                self.ser.close()
            except (serial.SerialException, OSError):
                pass  # port already gone
        self.ser = None
        self.connected = False

    def dispatch_move(self, board: Board, robot_color: int, col0: int) -> tuple[bool, str]:
        """Send column col0 (0-based) to the Arduino and wait for camera verification."""
        cmd = str(col0 + 1)
        expected = apply_move(board, col0, robot_color)
        if expected is None:
            return False, f"Column {col0} is not legal"
        with self.lock:
            if self.busy or self.awaiting_verification:
                return False, "Robot already busy"
            if not self._ensure_serial():
                return False, self.last_error or "Serial unavailable"
            try:
                self.ser.write(cmd.encode("ascii"))
                self.ser.flush()
            except (serial.SerialException, OSError) as e:
                self.last_error = f"Serial write failed — {e}"
                self._close_serial()
                return False, self.last_error
            now = time.time()
            self.busy = True
            self.awaiting_verification = True
            self.last_command = cmd
            self.last_sent_col = col0
            self.last_sent_at = now
            self.last_done_at = None
            self.expected_board = expected
            self.expected_col = col0
            self.settle_until = now + ROBOT_SETTLE_SEC
            self.verify_deadline = now + VERIFY_TIMEOUT
            self.state = "waiting_verify"
            self.state_msg = f"Command sent: column {cmd}. Waiting for board verification."
            self.last_error = None
        return True, self.state_msg

    def _can_auto_accept(self) -> bool:
        with self.lock:
            mode = self.mode
        if mode == MODE_MANUAL:
            return False
        return self.camera.board_detected and self.camera.detection_age == 0

    def _maybe_accept_user_move(self) -> None:
        if not self.camera_mode:
            return
        if not self._can_auto_accept():
            return
        with self.camera.lock:
            cand = board_copy(self.camera.candidate) if self.camera.candidate is not None else None
        if cand is None:
            return
        gs = self.game
        with self.state_lock:
            if gs.winner or gs.is_draw or gs.whose_turn != "user":
                return
            if self.awaiting_verification or self.busy:
                return
            if boards_equal(gs.trusted, cand):
                return
            accepted, _ = gs.accept(cand)
            if accepted and gs.whose_turn == "robot":
                self.maybe_dispatch_robot_locked()

    def maybe_dispatch_robot_locked(self) -> None:
        """Dispatch the planned robot move unless in manual mode. Caller holds state_lock."""
        if not self.camera_mode:
            return
        gs = self.game
        if gs.whose_turn != "robot" or not gs.mm_ran or gs.mm_col is None:
            return
        with self.lock:
            if self.mode == MODE_MANUAL or self.busy or self.awaiting_verification:
                if self.mode == MODE_MANUAL:
                    self.state = "waiting_dispatch"
                    self.state_msg = "Robot move ready — dispatch manually."
                return
        ok, msg = self.dispatch_move(gs.trusted, gs.robot_color, gs.mm_col)
        gs.status_msg = msg if ok else f"Robot dispatch failed — {msg}"

    def _maybe_verify(self) -> None:
        with self.lock:
            if not self.awaiting_verification:
                return
            now = time.time()
            if self.settle_until and now < self.settle_until:
                return
            expected = board_copy(self.expected_board) if self.expected_board is not None else None
            exp_col = self.expected_col
            deadline = self.verify_deadline
        with self.camera.lock:
            cand = board_copy(self.camera.candidate) if self.camera.candidate is not None else None
        if cand is not None and expected is not None and boards_equal(cand, expected):
            with self.state_lock:
                accepted, reason = self.game.accept(cand)
                if accepted:
                    with self.lock:
                        self.busy = False
                        self.awaiting_verification = False
                        self.state = "idle"
                        self.state_msg = f"Robot move verified in column {exp_col + 1}."
                        self.expected_board = None
                        self.expected_col = None
                        self.verify_deadline = None
                        self.settle_until = None
                    if self.game.whose_turn == "robot":
                        self.maybe_dispatch_robot_locked()
                else:
                    with self.lock:
                        self.busy = False
                        self.awaiting_verification = False
                        self.state = "error"
                        self.state_msg = f"Verification board rejected — {reason}"
                        self.last_error = reason
            return
        if deadline and time.time() > deadline:
            with self.lock:
                self.busy = False
                self.awaiting_verification = False
                self.state = "error"
                self.state_msg = "Timed out waiting for verified robot move."
                self.last_error = self.state_msg

    def _loop(self) -> None:
        while True:
            with self.lock:
                self._ensure_serial()
                self._read_serial()
            self._maybe_accept_user_move()
            self._maybe_verify()
            time.sleep(VERIFY_POLL_SEC)

    def to_dict(self) -> dict:
        """Return robot status for the dashboard."""
        with self.lock:
            return {
                "operator_mode": self.mode,
                "serial_connected": self.connected,
                "robot_busy": self.busy,
                "robot_state": self.state,
                "robot_state_msg": self.state_msg,
                "robot_last_command": self.last_command,
                "robot_last_sent_col": self.last_sent_col,
                "robot_last_sent_at": round(self.last_sent_at, 3) if self.last_sent_at else None,
                "robot_last_ack": self.last_ack,
                "robot_last_error": self.last_error,
                "robot_awaiting_verification": self.awaiting_verification,
                "robot_expected_col": self.expected_col,
            }
