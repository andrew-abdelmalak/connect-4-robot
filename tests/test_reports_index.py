"""Quality of "Ask the reports": the top three passages must hold the answer."""

import json
import sys

import pytest
from conftest import REPO

pytest.importorskip("onnxruntime")
sys.path.insert(0, str(REPO / "scripts"))
from build_reports_index import Embedder, load_vectors, rank  # noqa: E402

# (question a visitor might ask, text (or alternatives) a passage answering it contains)
QUESTIONS = [
    ("How much did the robot cost?", ("6,100", "6100", "10,050", "10,135")),
    ("Which way of reading the board was most accurate?", ("98%", "98 %")),
    ("Why did the robot miss column 7 more often?", "column 7"),
    ("How does the robot know its token landed?", "verif"),
    ("How deep does the minimax search look?", "depth"),
    ("How long does one robot move take?", "7.5"),
    ("What boards does the validator reject?", "chip"),
    ("Which motor moves the carriage?", "TT"),
    ("How fast is image processing on the Raspberry Pi?", "ms"),
    ("What baud rate does the serial link use?", "9600"),
    ("Which papers did the literature review cover?", "chess"),
    ("How many unit tests passed?", "31"),
    ("What problems does lighting cause?", "light"),
    ("How is one token released from the magazine?", "magazine"),
    ("What does the dashboard show?", "dashboard"),
    ("Why is the Gaussian 5x5 filter used?", "Gaussian"),
]
MIN_HIT_RATE = 0.8


@pytest.fixture(scope="module")
def setup():
    index = json.loads((REPO / "site" / "reports-index.json").read_text())
    return index, load_vectors(index), Embedder()


def test_index_shape(setup):
    index, vectors, _ = setup
    assert vectors.shape == (len(index["passages"]), 384)
    assert {p["doc"] for p in index["passages"]} == {
        "Vision-Based-Connect-4-Robot.pdf",
        "MS4_5_Final_Report.pdf",
        "MS3_Closed_Loop_Integration.pdf",
        "MS2_Pipeline_Hardware.pdf",
        "MS1_Literature_Review.pdf",
    }


def test_hit_at_3(setup):
    index, vectors, embed = setup
    items = index["passages"]
    queries = embed([q for q, _ in QUESTIONS])
    misses = []
    for (question, needle), query in zip(QUESTIONS, queries, strict=True):
        top = [items[i]["text"] for i in rank(question, query, items, vectors)[:3]]
        needles = needle if isinstance(needle, tuple) else (needle,)
        if not any(n.lower() in t.lower() for t in top for n in needles):
            misses.append(question)
    rate = 1 - len(misses) / len(QUESTIONS)
    print(f"hit@3 = {rate:.2f}; missed: {misses}")
    assert rate >= MIN_HIT_RATE, misses
