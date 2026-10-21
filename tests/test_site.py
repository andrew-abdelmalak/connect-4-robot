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
    shipped = re.search(r"cp src/connect4_robot/\{([^}]+)\}\.py", pages).group(1).split(",")
    assert set(names) == {f"{n}.py" for n in shipped}, (names, shipped)


def test_local_assets_exist():
    html = (SITE / "index.html").read_text()
    for ref in re.findall(r'(?:src|href)="([^"#:]+)"', html):
        assert (SITE / ref).exists(), ref
    for ref in re.findall(r'"img/([^"]+\.webp)"', html):
        assert (SITE / "img" / ref).exists(), ref


def test_clip_results_are_consistent():
    import json

    d = json.loads((SITE / "clip-cells.json").read_text())
    assert d["cells"] == len(d["truth"]) == len(d["hsv"]) == len(d["clip"]) == 42
    assert d["hsv_correct"] == sum(a == b for a, b in zip(d["hsv"], d["truth"], strict=True)) == 42
    assert d["clip_correct"] == sum(a == b for a, b in zip(d["clip"], d["truth"], strict=True))
