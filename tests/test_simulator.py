"""The scripted simulator path: every step does what its label says."""

import pytest

from connect4_robot.game_state import GameState
from connect4_robot.simulator import SIM_STATES

# (label start, accepted?, text the validator reason must contain)
EXPECTED = [
    ("START", False, "unchanged"),
    ("VALID #1", True, "RED → col 3"),
    ("ILLEGAL: Floating", False, "Floating chip: col 0 row 4"),
    ("VALID #2", True, "YELLOW → col 3"),
    ("VALID #3", True, "RED → col 1"),
    ("VALID #4", True, "YELLOW → col 6"),
    ("VALID #5", True, "RED → col 2"),
    ("ILLEGAL: Chip disappeared", False, "Chip disappeared at row 5, col 3"),
    ("ILLEGAL: Two chips", False, "2 new chips"),
    ("VALID #6", True, "YELLOW → col 4"),
    ("VALID #7", True, "User (RED) wins"),
    ("GAME OVER", False, "Game already over"),
]


def run_path():
    gs = GameState()
    return [(desc, *gs.accept(board)) for desc, board in SIM_STATES]


def test_path_length():
    assert len(SIM_STATES) == len(EXPECTED)


@pytest.mark.parametrize("step", range(len(EXPECTED)))
def test_step_matches_label(step):
    desc, ok, reason = run_path()[step]
    label, want_ok, want_text = EXPECTED[step]
    assert desc.startswith(label), desc
    assert ok is want_ok, (desc, reason)
    assert want_text in reason, (desc, reason)
