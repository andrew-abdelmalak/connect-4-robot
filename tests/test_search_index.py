"""Quality of "Ask the paper": the top three passages must hold the answer."""

import json
import re
import sys

import pytest
from conftest import REPO

pytest.importorskip("onnxruntime")
sys.path.insert(0, str(REPO / "scripts"))
from build_search_index import Embedder, embed_text, load_vectors, rank

# (question a visitor might ask, regex a passage answering it matches, case-insensitive)
QUESTIONS = [
    ("How much did the robot cost?", r"6,?100|10,050|10,135"),
    ("Which way of reading the board was most accurate?", r"98\s?%"),
    ("Why did the robot miss column 7 more often?", r"column[ -]?7|outer column"),
    ("How does the robot know its token landed?", r"verif(y|ied|ication)"),
    ("How deep does the minimax search look?", r"depth[- ]5|depth 5"),
    ("How long does one robot move take?", r"7\.5\s?s"),
    ("What boards does the validator reject?", r"floating|disappear"),
    ("Which motor moves the carriage?", r"\bTT\b"),
    ("How fast is image processing on the Raspberry Pi?", r"\d\.\d+\s?ms|\bms per\b"),
    ("What baud rate does the serial link use?", r"\b9600\b"),
    ("Which papers did the literature review cover?", r"W.lflein|Ayub|Zubek|chess"),
    ("How many unit tests passed?", r"\b31\b"),
    ("What problems does lighting cause?", r"glare|illumination|lighting (shift|change)"),
    ("How is one token released from the magazine?", r"magazine"),
    ("What does the operator dashboard show?", r"MJPEG|stream"),
    ("Why was the 5x5 Gaussian filter chosen?", r"5\s?[x×]\s?5"),
]
MIN_HIT_RATE = 0.8


@pytest.fixture(scope="module")
def setup():
    index = json.loads((REPO / "site" / "search-index.json").read_text())
    return index, load_vectors(index), Embedder()


def test_index_shape(setup):
    index, vectors, _ = setup
    assert vectors.shape == (len(index["passages"]), 384)
    assert {p["doc"] for p in index["passages"]} == {
        "Vision-Based-Connect-4-Robot.pdf",
        "Vision-Based-Connect-4-Robot-Supplement.pdf",
    }


def test_index_matches_the_model(setup):
    """A stale or hand-edited index fails: stored vectors must match fresh embeddings."""
    index, vectors, embed = setup
    for i in (0, len(index["passages"]) // 2, len(index["passages"]) - 1):
        p = index["passages"][i]
        fresh = embed([embed_text(p)])[0]
        assert float(fresh @ vectors[i]) > 0.97, p["doc"]


def test_hit_at_3(setup):
    index, vectors, embed = setup
    items = index["passages"]
    queries = embed([q for q, _ in QUESTIONS])
    misses = []
    for (question, needle), query in zip(QUESTIONS, queries, strict=True):
        top = [items[i]["text"] for i in rank(question, query, items, vectors)[:3]]
        if not any(re.search(needle, t, re.IGNORECASE) for t in top):
            misses.append(question)
    rate = 1 - len(misses) / len(QUESTIONS)
    print(f"hit@3 = {rate:.2f}; missed: {misses}")
    assert rate >= MIN_HIT_RATE, misses
