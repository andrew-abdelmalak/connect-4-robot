"""The paper's search passages come from its LaTeX source; the committed index must match."""

import json
import sys

from conftest import REPO

sys.path.insert(0, str(REPO / "scripts"))
import paper_latex


def test_paper_passages_match_the_latex():
    """A stale index fails: rebuild it with scripts/build_search_index.py after editing paper/main.tex."""
    index = json.loads((REPO / "site" / "search-index.json").read_text())
    stored = [(p["section"], p["text"]) for p in index["passages"] if p["doc"] == "Vision-Based-Connect-4-Robot.pdf"]
    assert stored == [(p["section"], p["text"]) for p in paper_latex.passages()]


def test_every_section_is_labelled_like_the_pdf():
    sections = {p["section"] for p in paper_latex.passages()}
    assert {
        "Abstract",
        "I. Introduction",
        "VI-B. Mechanical Drop Reliability Dominates the Real Bottleneck",
    } <= sections
