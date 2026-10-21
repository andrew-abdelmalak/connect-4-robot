"""Move planner: tactical shortcuts and a regression set from the original code."""

import json

from conftest import REPO
from connect4_brain import P1, P2, _bc, apply_move, empty_board, get_best_move


def test_takes_immediate_win():
    board = _bc(".YYY...", ".RRR...")
    col, _, reason = get_best_move(board, robot=P2, user=P1)
    assert col in (0, 4)
    assert "Immediate win" in reason


def test_blocks_immediate_threat():
    board = _bc("RRR....", "Y......", "Y......")
    col, _, reason = get_best_move(board, robot=P2, user=P1)
    assert col == 3
    assert "Blocking" in reason


def test_win_beats_block():
    board = _bc("RRR.YYY")
    col, _, reason = get_best_move(board, robot=P2, user=P1)
    assert col == 3
    assert "Immediate win" in reason


def test_opens_in_the_centre():
    col, _, _ = get_best_move(empty_board(), robot=P1, user=P2)
    assert col == 3


def test_no_legal_columns():
    full = [[P1 if (r + c) % 2 else P2 for c in range(7)] for r in range(6)]
    assert get_best_move(full, robot=P1, user=P2)[0] is None


def test_matches_recorded_answers():
    """40 random positions, answers recorded from the original runtime at depth 4."""
    cases = json.loads((REPO / "tests" / "data" / "minimax_golden.json").read_text())
    for case in cases:
        board, player = empty_board(), P1
        for col in case["moves"]:
            board = apply_move(board, col, player)
            player = 3 - player
        col, score, _ = get_best_move(
            board, case["player"], 3 - case["player"], depth=4
        )
        assert (col, int(score)) == (case["col"], case["score"]), case
