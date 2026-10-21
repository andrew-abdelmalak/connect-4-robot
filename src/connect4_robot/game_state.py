"""Trusted game state: the validator that gates every board before it counts.

A candidate board from the camera (or the simulator) only replaces the trusted
board if it is one legal move away: the board changed, no chip disappeared,
exactly one chip appeared, it landed at the lowest empty row, its column was
not full, and its colour matches whose turn it is. The column-full rule is
implied by the gravity rule (a new chip always sits in an empty cell) and is
kept as an explicit guard.
"""

import time

from .board import (
    COLS,
    EMPTY,
    ROWS,
    Board,
    board_copy,
    check_draw,
    check_win,
    color_name,
    count_chips,
    empty_board,
    get_legal_cols,
    lowest_empty_row,
    opponent,
)
from .minimax import MINIMAX_DEPTH, get_best_move


class GameState:
    """Trusted board, turn order, winner, and the robot's planned move."""

    def __init__(self, depth: int = MINIMAX_DEPTH) -> None:
        self.depth = depth
        self.reset()

    def reset(self) -> None:
        """Start a new game: empty board, colours unassigned, user to move."""
        self.trusted: Board = empty_board()
        self.previous: Board = empty_board()
        self.candidate: Board | None = None
        self.user_color: int | None = None
        self.robot_color: int | None = None
        self.whose_turn: str | None = "user"
        self.winner: str | None = None
        self.is_draw = False
        self.last_col: int | None = None
        self.last_player: int | None = None
        self.move_count = 0
        self.status_msg = "Waiting for first move (user plays first)."
        self.reject_reason: str | None = None
        self.mm_col: int | None = None
        self.mm_score: float | None = None
        self.mm_reason = "Not yet computed."
        self.mm_time: float | None = None
        self.mm_ran = False

    def _validate(self, prev: Board, new_b: Board) -> tuple[bool, int, int, str]:
        """Check prev→new_b is a legal single-chip update.
        Returns (valid, col, player, reason)."""
        if prev == new_b:
            return False, -1, -1, "Board unchanged — no move detected"
        diffs = [(r, c) for r in range(ROWS) for c in range(COLS) if prev[r][c] != new_b[r][c]]
        for r, c in diffs:
            if prev[r][c] != EMPTY and new_b[r][c] == EMPTY:
                return False, -1, -1, f"Chip disappeared at row {r}, col {c}"
        new_chips = [(r, c) for r, c in diffs if prev[r][c] == EMPTY and new_b[r][c] != EMPTY]
        if len(new_chips) == 0:
            return False, -1, -1, "No new chip found despite board change"
        if len(new_chips) > 1:
            return False, -1, -1, f"{len(new_chips)} new chips — impossible in one turn"
        r, c = new_chips[0]
        player = new_b[r][c]
        expected_r = lowest_empty_row(prev, c)
        if expected_r == -1:
            return False, -1, -1, f"Column {c} was already full"
        if r != expected_r:
            return (
                False,
                -1,
                -1,
                (f"Floating chip: col {c} row {r}, expected row {expected_r}"),
            )
        if self.user_color is not None:
            if player == self.user_color and self.whose_turn != "user":
                return False, -1, -1, "User color chip but it is robot's turn"
            if player == self.robot_color and self.whose_turn != "robot":
                return False, -1, -1, "Robot color chip but it is user's turn"
        return True, c, player, f"Valid: {color_name(player)} in col {c}, row {r}"

    def accept(self, candidate: Board) -> tuple[bool, str]:
        """Try to accept candidate as new trusted board. Returns (ok, reason)."""
        self.candidate = candidate
        if self.winner or self.is_draw:
            msg = "Game already over — reset to play again"
            self.reject_reason = msg
            return False, msg
        valid, col, player, reason = self._validate(self.trusted, candidate)
        if not valid:
            self.reject_reason = reason
            self.status_msg = f"REJECTED: {reason}"
            return False, reason
        self.reject_reason = None
        if self.user_color is None:
            self.user_color = player
            self.robot_color = opponent(player)
        self.previous = board_copy(self.trusted)
        self.trusted = board_copy(candidate)
        self.last_col = col
        self.last_player = player
        self.move_count += 1
        who = "User" if player == self.user_color else "Robot"
        if check_win(self.trusted, player):
            self.winner = who
            self.whose_turn = None
            self.status_msg = f"GAME OVER — {who} ({color_name(player)}) wins!"
            return True, self.status_msg
        if check_draw(self.trusted):
            self.is_draw = True
            self.whose_turn = None
            self.status_msg = "GAME OVER — Draw (board full)"
            return True, "Draw"
        self.whose_turn = "robot" if who == "User" else "user"
        self.status_msg = f"Accepted: {color_name(player)} → col {col}. Now {self.whose_turn}'s turn."
        if self.whose_turn == "robot" and self.robot_color is not None:
            self._run_minimax()
        else:
            self.mm_ran = False
            self.mm_col = None
            self.mm_reason = "Waiting for user's move."
        return True, self.status_msg

    def _run_minimax(self) -> None:
        t0 = time.time()
        col, score, reason = get_best_move(self.trusted, self.robot_color, self.user_color, self.depth)
        self.mm_col = col
        self.mm_score = score
        self.mm_reason = reason
        self.mm_time = round(time.time() - t0, 3)
        self.mm_ran = True

    def to_dict(self) -> dict:
        """Return the state as a JSON-ready dict for the dashboard."""
        return {
            "trusted": self.trusted,
            "previous": self.previous,
            "candidate": self.candidate,
            "user_color": color_name(self.user_color),
            "robot_color": color_name(self.robot_color),
            "whose_turn": self.whose_turn,
            "winner": self.winner,
            "is_draw": self.is_draw,
            "last_col": self.last_col,
            "last_player": color_name(self.last_player),
            "move_count": self.move_count,
            "status_msg": self.status_msg,
            "reject_reason": self.reject_reason,
            "mm_col": self.mm_col,
            "mm_score": int(self.mm_score) if self.mm_score is not None else None,
            "mm_reason": self.mm_reason,
            "mm_time": self.mm_time,
            "mm_ran": self.mm_ran,
            "user_chips": count_chips(self.trusted, self.user_color) if self.user_color else 0,
            "robot_chips": count_chips(self.trusted, self.robot_color) if self.robot_color else 0,
            "legal_cols": get_legal_cols(self.trusted),
        }
