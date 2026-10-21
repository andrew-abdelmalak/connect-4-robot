"""Connect-4 board model: a 6x7 grid of EMPTY, P1 (red), and P2 (yellow).

Row 0 is the top of the board and row 5 the bottom, so chips fall toward
higher row indices. Boards are plain lists of lists so the same code runs on
the Raspberry Pi and in the browser (Pyodide) without NumPy.
"""

ROWS = 6
COLS = 7
EMPTY = 0
P1 = 1  # RED
P2 = 2  # YELLOW
CONNECT = 4

Board = list[list[int]]


def empty_board() -> Board:
    """Return a new board with every cell EMPTY."""
    return [[EMPTY] * COLS for _ in range(ROWS)]


def board_copy(b: Board) -> Board:
    """Return a deep copy of a board."""
    return [row[:] for row in b]


def get_legal_cols(board: Board) -> list[int]:
    """Return the columns whose top cell is still empty."""
    return [c for c in range(COLS) if board[0][c] == EMPTY]


def lowest_empty_row(board: Board, col: int) -> int:
    """Highest row-index (lowest on board) that is empty. -1 if column full."""
    for r in range(ROWS - 1, -1, -1):
        if board[r][col] == EMPTY:
            return r
    return -1


def apply_move(board: Board, col: int, player: int) -> Board | None:
    """Return new board with chip dropped, or None if illegal."""
    r = lowest_empty_row(board, col)
    if r == -1:
        return None
    nb = board_copy(board)
    nb[r][col] = player
    return nb


def check_win(board: Board, player: int) -> bool:
    """Return True if player has 4 in a row anywhere."""
    span = range(CONNECT)
    for r in range(ROWS):  # horizontal
        for c in range(COLS - 3):
            if all(board[r][c + i] == player for i in span):
                return True
    for c in range(COLS):  # vertical
        for r in range(ROWS - 3):
            if all(board[r + i][c] == player for i in span):
                return True
    for r in range(ROWS - 3):  # diagonal down-right
        for c in range(COLS - 3):
            if all(board[r + i][c + i] == player for i in span):
                return True
    for r in range(3, ROWS):  # diagonal down-left
        for c in range(COLS - 3):
            if all(board[r - i][c + i] == player for i in span):
                return True
    return False


def check_draw(board: Board) -> bool:
    """Return True when the top row is full (no legal move left)."""
    return all(board[0][c] != EMPTY for c in range(COLS))


def count_chips(board: Board, player: int) -> int:
    """Return how many chips of one player are on the board."""
    return sum(board[r][c] == player for r in range(ROWS) for c in range(COLS))


def opponent(player: int) -> int:
    """Return the other player."""
    return P2 if player == P1 else P1


def color_name(p: int | None) -> str:
    """Return "RED", "YELLOW", or "None" for a player value."""
    if p == P1:
        return "RED"
    if p == P2:
        return "YELLOW"
    return "None"


def board_from_rows(*rows_bottom_up: str) -> Board:
    """Build board from strings read bottom-up. R=P1(RED), Y=P2(YEL), .=empty."""
    b = empty_board()
    for i, row in enumerate(rows_bottom_up):
        r = ROWS - 1 - i
        for c, ch in enumerate(row[:COLS]):
            if ch == "R":
                b[r][c] = P1
            elif ch == "Y":
                b[r][c] = P2
    return b
