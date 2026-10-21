"""Trusted-state validator: the six rules, colour assignment, turns, game over."""

from connect4_brain import P1, P2, GameState, _bc, empty_board


def accepted(gs, *rows):
    ok, reason = gs.accept(_bc(*rows))
    assert ok, reason
    return reason


def rejected(gs, *rows):
    ok, reason = gs.accept(_bc(*rows))
    assert not ok
    return reason


def test_rule_1_unchanged_board_is_rejected():
    gs = GameState()
    assert "unchanged" in rejected(gs, ".......")


def test_rule_2_disappearing_chip_is_rejected():
    gs = GameState()
    accepted(gs, "...R...")
    assert "disappeared" in rejected(gs, "....Y..")
    assert gs.trusted == _bc("...R...")


def test_rule_3_two_new_chips_are_rejected():
    gs = GameState()
    assert "2 new chips" in rejected(gs, "..RR...")


def test_rule_4_floating_chip_is_rejected():
    gs = GameState()
    assert "Floating chip" in rejected(gs, ".......", "...R...")


def test_rule_5_full_column_cannot_change():
    # A full column has no empty cell, so a new chip can never appear there:
    # the "column already full" check is implied by rules 3 and 4. Changing a
    # chip's colour in a full column is still rejected.
    gs = GameState()
    gs.trusted = _bc("R", "Y", "R", "Y", "R", "Y")
    gs.user_color, gs.robot_color = P1, P2
    candidate = [row[:] for row in gs.trusted]
    candidate[0][0] = P1
    assert "No new chip" in gs.accept(candidate)[1]


def test_rule_6_wrong_colour_for_turn_is_rejected():
    gs = GameState()
    accepted(gs, "...R...")  # user is RED, robot's turn now
    assert "robot's turn" in rejected(gs, "..RR...")


def test_first_chip_assigns_colours():
    gs = GameState()
    accepted(gs, "...Y...")
    assert gs.user_color == P2
    assert gs.robot_color == P1
    assert gs.whose_turn == "robot"


def test_robot_turn_runs_minimax():
    gs = GameState()
    accepted(gs, "...R...")
    assert gs.mm_ran
    assert gs.mm_col in gs.to_dict()["legal_cols"]


def test_win_ends_game_and_blocks_more_moves():
    gs = GameState()
    for rows in [
        ("R......",),
        ("R......", "Y......"),
        ("RR.....", "Y......"),
        ("RR.....", "YY....."),
        ("RRR....", "YY....."),
        ("RRR....", "YYY...."),
    ]:
        accepted(gs, *rows)
    assert "wins" in accepted(gs, "RRRR...", "YYY....")
    assert gs.winner == "User"
    assert gs.whose_turn is None
    assert "Game already over" in rejected(gs, "RRRR...", "YYYY...")


def test_reset_clears_everything():
    gs = GameState()
    accepted(gs, "...R...")
    gs.reset()
    assert gs.trusted == empty_board()
    assert gs.user_color is None
    assert gs.move_count == 0
