"""Move planner: depth-limited minimax with alpha-beta pruning.

Immediate wins and forced blocks are checked first; otherwise the search
scores 4-cell windows and the centre column, exploring centre columns first
so pruning cuts more of the tree.
"""

import time

from .board import COLS, EMPTY, ROWS, Board, apply_move, check_win, get_legal_cols, opponent

MINIMAX_DEPTH = 5

# Heuristic weights for one 4-cell window, from the robot's point of view.
WINDOW_FOUR = 100
WINDOW_THREE_OPEN = 5
WINDOW_TWO_OPEN = 2
WINDOW_OPP_THREE_OPEN = -4
CENTER_CHIP = 3
CENTER_COL = 3
WIN_SCORE = 1_000_000
BLOCK_SCORE = -999_999
INF = 1e18


def _score_window(window: list[int], player: int) -> int:
    opp = opponent(player)
    pc = window.count(player)
    ec = window.count(EMPTY)
    oc = window.count(opp)
    if pc == 4:
        return WINDOW_FOUR
    if pc == 3 and ec == 1:
        return WINDOW_THREE_OPEN
    if pc == 2 and ec == 2:
        return WINDOW_TWO_OPEN
    if oc == 3 and ec == 1:
        return WINDOW_OPP_THREE_OPEN
    return 0


def evaluate(board: Board, player: int) -> int:
    """Heuristic score of a position for player (higher is better)."""
    score = 0
    for r in range(ROWS):  # center column bonus
        if board[r][CENTER_COL] == player:
            score += CENTER_CHIP
    for r in range(ROWS):  # horizontal
        for c in range(COLS - 3):
            score += _score_window([board[r][c + i] for i in range(4)], player)
    for c in range(COLS):  # vertical
        for r in range(ROWS - 3):
            score += _score_window([board[r + i][c] for i in range(4)], player)
    for r in range(ROWS - 3):  # diagonal down-right
        for c in range(COLS - 3):
            score += _score_window([board[r + i][c + i] for i in range(4)], player)
    for r in range(3, ROWS):  # diagonal down-left
        for c in range(COLS - 3):
            score += _score_window([board[r - i][c + i] for i in range(4)], player)
    return score


def _minimax(
    board: Board,
    depth: int,
    alpha: float,
    beta: float,
    maximizing: bool,
    robot: int,
    user: int,
) -> tuple[float, int]:
    legal = get_legal_cols(board)
    if check_win(board, robot):
        return (WIN_SCORE + depth, -1)
    if check_win(board, user):
        return (-WIN_SCORE - depth, -1)
    if not legal or depth == 0:
        return (evaluate(board, robot), -1)
    order = sorted(legal, key=lambda c: abs(c - COLS // 2))  # center-first
    if maximizing:
        best, best_col = -INF, order[0]
        for c in order:
            nb = apply_move(board, c, robot)
            sc, _ = _minimax(nb, depth - 1, alpha, beta, False, robot, user)
            if sc > best:
                best, best_col = sc, c
            alpha = max(alpha, best)
            if alpha >= beta:
                break
        return best, best_col
    best, best_col = INF, order[0]
    for c in order:
        nb = apply_move(board, c, user)
        sc, _ = _minimax(nb, depth - 1, alpha, beta, True, robot, user)
        if sc < best:
            best, best_col = sc, c
        beta = min(beta, best)
        if alpha >= beta:
            break
    return best, best_col


def get_best_move(board: Board, robot: int, user: int, depth: int = MINIMAX_DEPTH) -> tuple[int | None, float, str]:
    """Returns (col, score, reason_str). Quick wins/blocks before full search.

    Raises:
        ValueError: if depth is below 1 (depth 0 would return column -1).
    """
    if depth < 1:
        raise ValueError(f"minimax depth must be at least 1, got {depth}")
    legal = get_legal_cols(board)
    if not legal:
        return None, 0, "No legal columns"
    for c in legal:  # immediate win?
        nb = apply_move(board, c, robot)
        if nb and check_win(nb, robot):
            return c, WIN_SCORE, f"Immediate win in column {c}"
    for c in legal:  # must block?
        nb = apply_move(board, c, user)
        if nb and check_win(nb, user):
            return c, BLOCK_SCORE, f"Blocking opponent win in column {c}"
    t0 = time.time()
    score, col = _minimax(board, depth, -INF, INF, True, robot, user)
    return (
        col,
        score,
        f"Minimax depth={depth} score={int(score)} time={time.time() - t0:.2f}s",
    )
