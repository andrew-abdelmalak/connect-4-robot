"""Board rules: gravity, legal columns, wins in every direction, draws."""

import pytest
from connect4_brain import (
    COLS,
    EMPTY,
    P1,
    P2,
    ROWS,
    _bc,
    apply_move,
    check_draw,
    check_win,
    count_chips,
    empty_board,
    find_new_chip,
    get_legal_cols,
    lowest_empty_row,
)


def test_empty_board_shape():
    board = empty_board()
    assert len(board) == ROWS
    assert all(len(row) == COLS and set(row) == {EMPTY} for row in board)


def test_chips_stack_from_the_bottom():
    board = apply_move(empty_board(), 3, P1)
    board = apply_move(board, 3, P2)
    assert board[ROWS - 1][3] == P1
    assert board[ROWS - 2][3] == P2
    assert lowest_empty_row(board, 3) == ROWS - 3


def test_apply_move_does_not_mutate_input():
    board = empty_board()
    apply_move(board, 0, P1)
    assert board == empty_board()


def test_full_column_is_illegal():
    board = empty_board()
    for i in range(ROWS):
        board = apply_move(board, 2, P1 if i % 2 else P2)
    assert lowest_empty_row(board, 2) == -1
    assert apply_move(board, 2, P1) is None
    assert 2 not in get_legal_cols(board)
    assert get_legal_cols(board) == [0, 1, 3, 4, 5, 6]


@pytest.mark.parametrize(
    "rows",
    [
        ("RRRR...",),  # horizontal
        ("R......", "R......", "R......", "R......"),  # vertical
        ("RYYY...", ".RYY...", "..RY...", "...R..."),  # diagonal up-right
        ("...RYYY", "...YRYY", "...YYRY", "......R"),  # diagonal up-left
    ],
)
def test_four_in_a_row_wins(rows):
    board = _bc(*rows)
    assert check_win(board, P1)
    assert not check_win(board, P2)


def test_three_in_a_row_is_not_a_win():
    assert not check_win(_bc("RRR.R.."), P1)


def test_draw_only_when_top_row_full():
    board = empty_board()
    assert not check_draw(board)
    full = [[P1 if (r + c) % 2 else P2 for c in range(COLS)] for r in range(ROWS)]
    assert check_draw(full)


def test_count_chips():
    board = _bc(".RRR..Y", "...Y...")
    assert count_chips(board, P1) == 3
    assert count_chips(board, P2) == 2


def test_find_new_chip():
    prev = _bc("...R...")
    new = _bc("...R...", "...Y...")
    assert find_new_chip(prev, new) == (ROWS - 2, 3, P2)
    assert find_new_chip(prev, prev) is None
