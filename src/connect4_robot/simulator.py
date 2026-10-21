"""Scripted simulator path: a short game with illegal frames mixed in.

Used by the dashboard's simulation mode (no camera or Arduino) to show the
validator accepting legal moves and rejecting impossible ones.
"""

from .board import Board, empty_board
from .board import board_from_rows as _bc


def _build_sim_states() -> list[tuple[str, Board]]:
    # ── valid game sequence ──────────────────────────────────────────────────
    # Row 5 = bottom, row 0 = top  |  Chips fall down
    b1 = _bc("...R...")  # User: RED col 3
    b2 = _bc("...R...", "...Y...")  # Robot: YELLOW col 3 (on top)
    b3 = _bc(".R.R...", "...Y...")  # User: RED col 1
    b4 = _bc(".R.R..Y", "...Y...")  # Robot: YELLOW col 6
    b5 = _bc(".RRR..Y", "...Y...")  # User: RED col 2  → threat: cols 0 and 4
    b6 = _bc(".RRRY.Y", "...Y...")  # Robot: YELLOW col 4 — blocks 1-2-3-4, col 0 still open
    b7 = _bc("RRRRY.Y", "...Y...")  # User: RED col 0 → WINS (0,1,2,3)

    # ── illegal test states ──────────────────────────────────────────────────
    # Each is compared against the trusted board at that point in the path.
    b_float = _bc("...R...", "R......")  # vs b1: col0 row4 chip, but row5 empty
    b_disap = _bc(".RR...Y", "...Y...")  # vs b5: RED in col 3 vanished
    b_two = _bc(".RRRYYY", "...Y...")  # vs b5: YELLOW in cols 4 and 5 at once
    b_after = _bc("RRRRYYY", "...Y...")  # vs b7: any move after the win

    return [
        ("START — board is empty (press Step to begin)", empty_board()),
        ("VALID #1: User plays RED → col 3 (assigns colors)", b1),
        ("ILLEGAL: Floating chip — col 0 row 4 with row 5 empty", b_float),
        ("VALID #2: Robot plays YELLOW → col 3 (stacked)", b2),
        ("VALID #3: User plays RED → col 1", b3),
        ("VALID #4: Robot plays YELLOW → col 6", b4),
        ("VALID #5: User RED → col 2 (3-in-a-row threat: 1,2,3)", b5),
        ("ILLEGAL: Chip disappeared (col 3 RED vanished)", b_disap),
        ("ILLEGAL: Two chips at once (cols 4 and 5 both new YELLOW)", b_two),
        ("VALID #6: Robot YELLOW → col 4 (blocks 1-4; col 0 still open)", b6),
        ("VALID #7: User RED → col 0  *** USER WINS: 0,1,2,3 ***", b7),
        ("GAME OVER: moves after a win are rejected until Reset", b_after),
    ]


SIM_STATES = _build_sim_states()


class Simulator:
    """Cursor over SIM_STATES that wraps around at the end."""

    def __init__(self) -> None:
        self._idx = 0

    def current(self) -> tuple[str, Board]:
        """Return (description, board) of the current step."""
        return SIM_STATES[self._idx]

    def advance(self) -> tuple[str, Board]:
        """Move to the next step (wrapping) and return it."""
        self._idx = (self._idx + 1) % len(SIM_STATES)
        return self.current()

    def reset(self) -> None:
        """Go back to the first step."""
        self._idx = 0

    @property
    def index(self) -> int:
        """Index of the current step."""
        return self._idx

    @property
    def total(self) -> int:
        """Number of scripted steps."""
        return len(SIM_STATES)
