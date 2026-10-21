"""Robot controller: serial dispatch and camera verification, with fakes."""

import pytest

from connect4_robot import dashboard as cb
from connect4_robot import hardware
from connect4_robot.board import P1, P2
from connect4_robot.board import board_from_rows as _bc


class FakeSerial:
    def __init__(self):
        self.written = []
        self.lines = []

    def write(self, data):
        self.written.append(data)

    def flush(self):
        pass

    @property
    def in_waiting(self):
        return len(self.lines)

    def readline(self):
        return self.lines.pop(0)

    def close(self):
        pass


@pytest.fixture
def rig(monkeypatch):
    cb.set_app_mode("camera")
    cb.gs.reset()
    cb.robot.reset()
    cb.robot.ser = FakeSerial()
    cb.robot.set_mode("auto")
    monkeypatch.setattr(hardware, "ROBOT_SETTLE_SEC", 0.0)
    yield cb.robot
    cb.robot.ser = None
    cb.robot.set_mode("manual")
    cb.set_app_mode("simulation")


def show_camera(board, detected=True):
    with cb.camera.lock:
        cb.camera.candidate = board
        cb.camera.board_detected = detected
        cb.camera.detection_age = 0 if detected else 3


def test_dispatch_sends_one_based_column(rig):
    ok, _ = rig.dispatch_move(_bc("...R..."), P2, 3)
    assert ok
    assert rig.ser.written == [b"4"]
    assert rig.awaiting_verification
    assert rig.expected_board == _bc("...R...", "...Y...")


def test_dispatch_refuses_while_busy(rig):
    rig.dispatch_move(_bc("...R..."), P2, 3)
    ok, msg = rig.dispatch_move(_bc("...R..."), P2, 3)
    assert not ok and "busy" in msg


def test_full_loop_user_move_then_verified_robot_move(rig):
    show_camera(_bc("...R..."))
    rig._maybe_accept_user_move()
    assert cb.gs.user_color == P1
    assert rig.ser.written == [str(cb.gs.mm_col + 1).encode()]
    col = rig.expected_col
    show_camera(rig.expected_board)
    rig._maybe_verify()
    assert rig.state == "idle"
    assert cb.gs.trusted[4 if col == 3 else 5][col] == P2
    assert cb.gs.whose_turn == "user"


def test_wrong_drop_times_out_into_error(rig, monkeypatch):
    show_camera(_bc("...R..."))
    rig._maybe_accept_user_move()
    show_camera(_bc("...R..."))  # token never appears
    rig.verify_deadline = 0.0001
    rig._maybe_verify()
    assert rig.state == "error"
    assert "Timed out" in rig.state_msg


def test_manual_mode_never_auto_accepts(rig):
    rig.set_mode("manual")
    show_camera(_bc("...R..."))
    rig._maybe_accept_user_move()
    assert cb.gs.move_count == 0


def test_stale_detection_is_not_auto_accepted(rig):
    show_camera(_bc("...R..."), detected=False)
    rig._maybe_accept_user_move()
    assert cb.gs.move_count == 0


def test_done_line_is_recorded(rig):
    rig.ser.lines = [b"BUSY 4\n", b"DONE 4\n"]
    rig._read_serial()
    assert rig.last_ack == "DONE 4"
    assert rig.last_done_at is not None


def test_loop_survives_an_exception_and_reports_it(rig, monkeypatch):
    def boom():
        raise RuntimeError("camera glitch")

    monkeypatch.setattr(rig, "_maybe_verify", boom)
    rig.run_once()  # must not raise
    assert rig.state == "error"
    assert "camera glitch" in rig.state_msg


def test_camera_frame_error_does_not_kill_the_thread(monkeypatch):
    from connect4_robot.vision import VisionPipeline

    cam = hardware.Camera(cb.cell_classifier)
    vp = VisionPipeline(cb.cell_classifier)
    monkeypatch.setattr(vp, "preprocess", lambda frame: (_ for _ in ()).throw(ValueError("bad quad")))
    import numpy as np

    cam.process_frame(vp, np.zeros((480, 640, 3), dtype=np.uint8), now=100.0)  # must not raise
    assert cam.last_error and "bad quad" in cam.last_error
