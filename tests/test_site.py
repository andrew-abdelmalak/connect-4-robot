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
    pages = (REPO / ".github" / "workflows" / "pages.yml").read_text()
    for ref in re.findall(r'(?:src|href)="([^"#:]+)"', html):
        path, _, query = ref.partition("?")
        if path.startswith("pdfjs/"):  # the Pages build adds Mozilla's viewer
            assert "unzip -q pdfjs.zip -d _site/pdfjs" in pages
            assert (REPO / "docs" / query.removeprefix("file=../../docs/")).exists(), ref
            continue
        assert (SITE / path).exists(), ref
    for ref in re.findall(r'"img/([^"]+\.webp)"', html):
        assert (SITE / "img" / ref).exists(), ref


def test_clip_results_are_consistent():
    import json

    d = json.loads((SITE / "clip-cells.json").read_text())
    assert d["cells"] == len(d["truth"]) == len(d["hsv"]) == len(d["clip"]) == 42
    assert d["hsv_correct"] == sum(a == b for a, b in zip(d["hsv"], d["truth"], strict=True)) == 42
    assert d["clip_correct"] == sum(a == b for a, b in zip(d["clip"], d["truth"], strict=True))


def _js_const(src: str, name: str) -> str:
    return re.search(rf"const {name} = ([^;]+);", src).group(1)


def test_arduino_worker_runs_the_compiled_sketch():
    worker = (SITE / "arduino.js").read_text()
    pages = (REPO / ".github" / "workflows" / "pages.yml").read_text()
    assert 'fetch("firmware/dispenser.hex")' in worker
    assert "arduino-cli compile --fqbn arduino:avr:uno --output-dir build/dispenser arduino/dispenser" in pages
    assert "cp build/dispenser/dispenser.ino.hex _site/firmware/dispenser.hex" in pages


def test_page_constants_match_the_robot_code():
    """The page copies a few numbers from the firmware and the robot loop; keep them equal."""
    game = (SITE / "game.js").read_text()
    worker = (SITE / "arduino.js").read_text()
    sketch = (REPO / "arduino" / "dispenser" / "dispenser.ino").read_text()
    hardware = (REPO / "src" / "connect4_robot" / "hardware.py").read_text()

    durations = re.search(r"columnDurations\[7\] = \{([^}]+)\}", sketch).group(1)
    sketch_ms = [int(n) for n in re.findall(r"^\s*(\d+),?", durations, re.MULTILINE)]
    assert sketch_ms == [int(n) for n in _js_const(game, "COLUMN_MS").strip("[]").split(",")]

    # Arduino pin n is port D bit n for 0-7, port B bit n-8 for 8-13.
    pins = {k: int(v) for k, v in re.findall(r"const int (\w+) = (\d+);", sketch)}
    pins["ENCODER"] = pins["encoderPin"]
    mapped = re.findall(r'(IN\d|ENA|ENB|ENCODER): \["([BD])", (\d)\]', worker)
    assert len(mapped) == 7
    for name, port, bit in mapped:
        assert pins[name] == int(bit) + (0 if port == "D" else 8), name

    for name in ("ANALYSIS_INTERVAL", "ROBOT_SETTLE_SEC", "VERIFY_POLL_SEC", "VERIFY_TIMEOUT"):
        py = float(re.search(rf"^{name} = ([\d.]+)", hardware, re.MULTILINE).group(1))
        assert float(_js_const(game, name)) == py, name
    assert _js_const(game, "TIMEOUT_MSG").strip('"') in hardware
    assert int(_js_const(game, "TARGET_PULSES")) == int(re.search(r"targetPulses = (\d+);", sketch).group(1))

    from connect4_robot.minimax import WIN_SCORE

    assert int(_js_const(game, "WIN_SCORE").replace("_", "")) == WIN_SCORE
