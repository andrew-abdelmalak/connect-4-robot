"""Flask dashboard routes in simulation mode."""

import pytest

from connect4_robot import dashboard as cb
from connect4_robot.simulator import SIM_STATES


@pytest.fixture
def client():
    cb.set_app_mode("simulation")
    cb.gs.reset()
    cb.sim.reset()
    cb.robot.reset()
    with cb.app.test_client() as c:
        yield c


def test_index_serves_dashboard(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"Connect 4 Brain Dashboard" in resp.data


def test_status_reports_simulation(client):
    data = client.get("/status").get_json()
    assert data["mode"] == "simulation"
    assert data["sim_total"] == len(SIM_STATES)
    assert data["whose_turn"] == "user"


def test_step_and_reset(client):
    data = client.post("/step_sim").get_json()
    assert data["accepted"] is True
    assert data["status"]["move_count"] == 1
    assert client.post("/reset").get_json() == {"ok": True}
    assert client.get("/status").get_json()["move_count"] == 0


def test_camera_routes_refuse_in_simulation(client):
    assert client.post("/accept_camera").status_code == 400


def test_robot_move_needs_robot_turn(client):
    assert client.post("/robot_move").status_code == 400


@pytest.mark.parametrize("mode", ["manual", "semi_auto", "auto"])
def test_set_operator_mode(client, mode):
    resp = client.post("/set_mode", json={"mode": mode})
    assert resp.get_json()["robot"]["operator_mode"] == mode


def test_unknown_modes_are_rejected(client):
    assert client.post("/set_mode", json={"mode": "turbo"}).status_code == 400
    assert client.post("/set_classifier", json={"mode": "magic"}).status_code == 400


def test_set_classifier(client):
    data = client.post("/set_classifier", json={"mode": "auto"}).get_json()
    assert data["classifier"]["classifier_mode"] == "auto"
    client.post("/set_classifier", json={"mode": "hsv"})


def first_frame(client, path):
    resp = client.get(path)
    frame = next(iter(resp.response))
    resp.close()
    return frame


def test_debug_grid_stream_follows_the_latest_candidate(client):
    resp = client.get("/debug_grid")
    frames = iter(resp.response)
    before = next(frames)
    client.post("/step_sim")
    after = next(frames)
    resp.close()
    assert before != after


def test_processed_stream_shows_trusted_board(client):
    before = first_frame(client, "/processed")
    client.post("/step_sim")
    assert first_frame(client, "/processed") != before


@pytest.mark.parametrize("payload", [{"mode": []}, {"mode": {"a": 1}}, [1, 2], "text"])
def test_malformed_json_is_a_400_not_a_500(client, payload):
    assert client.post("/set_mode", json=payload).status_code == 400
    assert client.post("/set_classifier", json=payload).status_code == 400


def test_simulator_wraps_into_a_fresh_game(client):
    for _ in range(len(SIM_STATES) - 1):
        client.post("/step_sim")  # steps 1..11, ending on GAME OVER
    assert client.post("/step_sim").get_json()["sim_idx"] == 0  # wraps to START
    data = client.post("/step_sim").get_json()  # VALID #1 again
    assert data["accepted"] is True
    assert data["status"]["move_count"] == 1
