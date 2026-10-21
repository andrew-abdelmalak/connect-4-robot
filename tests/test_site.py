"""The site runs the repository's own modules: keep its references honest."""

import re

from conftest import REPO

SITE = REPO / "site"


def test_worker_loads_real_modules():
    brain = (SITE / "brain.js").read_text()
    names = re.findall(r'"([a-z_]+\.py)"', brain)
    assert {"board.py", "minimax.py", "game_state.py", "vision.py"} <= set(names)
    for name in names:
        assert (REPO / "src" / "connect4_robot" / name).exists(), name
    pages = (REPO / ".github" / "workflows" / "pages.yml").read_text()
    for name in names:
        assert name.removesuffix(".py") in pages, f"pages.yml does not ship {name}"


def test_local_assets_exist():
    html = (SITE / "index.html").read_text()
    for ref in re.findall(r'(?:src|href)="([^"#:]+)"', html):
        assert (SITE / ref).exists(), ref
    for ref in re.findall(r'"img/([^"]+\.webp)"', html):
        assert (SITE / "img" / ref).exists(), ref
