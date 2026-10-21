"""Shared test setup: hermetic HOME and the repository root."""

import os
import tempfile
from pathlib import Path

# The runtime reads ~/connect4_cell_model.pkl and ~/warp_corners.json at import.
# Point HOME at an empty folder so a developer's real files never leak into tests.
os.environ["HOME"] = tempfile.mkdtemp(prefix="c4home-")

REPO = Path(__file__).resolve().parents[1]
