"""Build site/reports-index.json, the search index behind "Ask the reports".

Each page of the paper and the four milestone reports is cut into short
passages, and each passage gets a vector from the all-MiniLM-L6-v2 sentence
model, computed exactly as transformers.js computes it in the browser (same
ONNX file, mean pooling, unit length). The browser embeds a question with the
same model and ranks passages with rank() below, mirrored in site/ask.js.

Run with ``uv run --group search python scripts/build_reports_index.py``.
Needs poppler's pdftotext.
"""

import base64
import functools
import json
import re
import subprocess
import unicodedata
from pathlib import Path

import numpy as np
import paper_latex

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "site" / "reports-index.json"
MODEL = "Xenova/all-MiniLM-L6-v2"
REVISION = "751bff37182d3f1213fa05d7196b954e230abad9"
ONNX_FILE = "onnx/model_quantized.onnx"  # transformers.js dtype "q8"
PASSAGE_WORDS = 90  # a passage ends at the first sentence end past this
MIN_TAIL_WORDS = 30  # a shorter last piece joins the passage before it
DOCS = [
    ("Vision-Based-Connect-4-Robot.pdf", "Paper"),
    ("MS4_5_Final_Report.pdf", "MS4/5 final report"),
    ("MS3_Closed_Loop_Integration.pdf", "MS3 closed-loop integration"),
    ("MS2_Pipeline_Hardware.pdf", "MS2 pipeline and hardware"),
    ("MS1_Literature_Review.pdf", "MS1 literature review"),
]


def page_count(pdf: Path) -> int:
    info = subprocess.run(["pdfinfo", str(pdf)], capture_output=True, text=True, check=True).stdout
    return int(re.search(r"^Pages:\s+(\d+)", info, re.MULTILINE).group(1))


@functools.cache
def compounds() -> frozenset[str]:
    """Hyphenated words that appear unbroken somewhere in the documents."""
    found = set()
    for file, _ in DOCS:
        text = subprocess.run(
            ["pdftotext", "-enc", "UTF-8", str(ROOT / "docs" / file), "-"], capture_output=True, text=True, check=True
        ).stdout
        found |= {w.lower() for w in re.findall(r"\b([A-Za-z]+-[A-Za-z]+)\b", text)}
    return frozenset(found)


def page_text(pdf: Path, page: int) -> str:
    text = subprocess.run(
        ["pdftotext", "-f", str(page), "-l", str(page), "-enc", "UTF-8", str(pdf), "-"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    # A hyphen at a line end is either line-breaking ("valida-tion") or part of a
    # compound ("trusted-state"); keep it when the compound also appears unbroken.
    intact = compounds()
    text = re.sub(
        r"\b(\w+)-\n(\w+)",
        lambda m: f"{m[1]}-{m[2]}" if f"{m[1]}-{m[2]}".lower() in intact else m[1] + m[2],
        text,
    )
    # The PDFs drop some line-break hyphens from compounds ("closedloop"); restore
    # the hyphen wherever the same compound appears hyphenated in the documents.
    for c in intact:
        text = re.sub(rf"\b{c.replace('-', '')}\b", c, text)
    # Small caps come out split ("P ER -C OLUMN"); rejoin a capital and the capitals after it.
    text = re.sub(r"\b([A-Z]) (?=[A-Z]{2,}\b)", r"\1", text)
    text = re.sub(r"([A-Z])- ?([A-Z])", r"\1-\2", text)
    return " ".join(text.split())


def is_table_run(sentence: str) -> bool:
    """Table cells and figure debris: mostly bare numbers and percentages."""
    tokens = sentence.split()
    numeric = sum(bool(re.fullmatch(r"[\d.,%×x+-]+", t)) for t in tokens)
    return sentence.startswith(("TABLE ", "Fig. ")) or (len(tokens) >= 6 and numeric / len(tokens) > 0.4)


HEADING = r"(?:[IVX]+\.|[A-H]\.|TABLE [IVX]+) [A-Z][A-Za-z]"  # "VII. EXPERIMENTAL", "G. Game-Logic", "TABLE VI"


def sentences(text: str) -> list[str]:
    """Split at sentence ends and before section headings."""
    return [s for s in re.split(rf"(?<=[.!?])\s+(?=[A-Z(\[])|\s+(?={HEADING})", text) if s]


PAPER = "Vision-Based-Connect-4-Robot.pdf"


def alnum(text: str) -> list[str]:
    """Letters and digits as lowercase words, so LaTeX and PDF text compare despite layout."""
    return re.findall(r"[a-z0-9]+", unicodedata.normalize("NFKC", text).lower())


def paper_passages() -> list[dict]:
    """The paper's passages from its LaTeX source (scripts/paper_latex.py), each
    placed on the PDF page where it starts and checked against that page's text.

    A passage must show 90% of its three-word runs and every number on its page
    or the next, and a table every cell, or the build stops.
    """
    pdf = ROOT / "docs" / PAPER
    raw = [
        subprocess.run(
            ["pdftotext", "-f", str(n), "-l", str(n), "-enc", "UTF-8", str(pdf), "-"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        for n in range(1, page_count(pdf) + 1)
    ]
    joined = ["".join(alnum(t)) for t in raw]
    words = [set(alnum(t)) for t in raw]
    out, at, problems = [], 0, []
    for p in paper_latex.passages():
        cells = p.get("cells")
        key = "".join(alnum(" ".join(cells[:5]) if cells else p["text"]))[:40]  # a table: its header row
        where = next((k for k in range(at, len(raw)) if key in joined[k]), None)
        if where is None:
            raise ValueError(f"not found in the PDF: [{p['section']}] {p['text'][:80]}")
        at = where
        text, seen = (
            joined[where] + joined[min(where + 1, len(raw) - 1)],
            words[where] | words[min(where + 1, len(raw) - 1)],
        )
        if cells:
            problems += [f"[{p['section']}] cell {c!r}" for c in cells if "".join(alnum(c)) not in text]
            find = " ".join(cells[:5])  # the header row: the PDF stores the rest column by column
        else:
            tokens = alnum(p["text"])
            shingles = ["".join(tokens[k : k + 3]) for k in range(max(1, len(tokens) - 2))]
            found = sum(sh in text for sh in shingles)
            numbers = [t for t in tokens if re.search(r"\d", t) and t not in seen]
            if found < 0.9 * len(shingles) or numbers:
                problems.append(f"[{p['section']}] {found}/{len(shingles)} runs, numbers {numbers}: {p['text'][:70]}")
            find = None
        item = {"doc": PAPER, "title": "Paper", "section": p["section"], "page": where + 1, "text": p["text"]}
        if find:
            item["find"] = find
        out.append(item)
    if problems:
        raise ValueError("paper passages differ from the PDF:\n" + "\n".join(problems))
    return out


def passages() -> list[dict]:
    """Each report read as one text, cut at sentence ends into passages of about
    PASSAGE_WORDS words; a passage carries the page it starts on. The paper
    comes from its LaTeX source instead (paper_passages)."""
    items = []
    for file, title in DOCS:
        if file == PAPER:
            items += paper_passages()
            continue
        pdf = ROOT / "docs" / file
        tagged = []  # (page, sentence) in reading order; a sentence crossing a page break stays whole
        carry, carry_page = None, None
        for page in range(1, page_count(pdf) + 1):
            parts = sentences(page_text(pdf, page))
            if not parts:
                continue
            first_page = page
            if carry:
                parts[0] = f"{carry} {parts[0]}"
                first_page = carry_page
            pages = [first_page] + [page] * (len(parts) - 1)
            tagged += list(zip(pages[:-1], parts[:-1], strict=True))
            carry, carry_page = parts[-1], pages[-1]
        if carry:
            tagged.append((carry_page, carry))
        current, start = [], None
        for page, sentence in tagged:
            if is_table_run(sentence):
                continue
            if re.match(HEADING, sentence) and len(current) >= MIN_TAIL_WORDS:
                items.append({"doc": file, "title": title, "page": start, "text": " ".join(current)})
                current, start = [], None
            start = start or page
            current += sentence.split()
            if len(current) >= PASSAGE_WORDS:
                items.append({"doc": file, "title": title, "page": start, "text": " ".join(current)})
                current, start = [], None
        if current and len(current) >= MIN_TAIL_WORDS:
            items.append({"doc": file, "title": title, "page": start, "text": " ".join(current)})
    if not items:
        raise SystemExit("no text extracted from docs/*.pdf")
    return items


class Embedder:
    """Mean-pooled, normalized MiniLM embeddings, computed as transformers.js does."""

    def __init__(self) -> None:
        import onnxruntime
        from huggingface_hub import hf_hub_download
        from tokenizers import Tokenizer

        def fetch(name: str) -> str:
            return hf_hub_download(MODEL, name, revision=REVISION)

        self.tokenizer = Tokenizer.from_file(fetch("tokenizer.json"))
        self.tokenizer.enable_truncation(256)
        self.tokenizer.enable_padding()
        self.session = onnxruntime.InferenceSession(fetch(ONNX_FILE))

    def __call__(self, texts: list[str]) -> np.ndarray:
        # One text at a time, as the browser embeds a question: the quantized
        # model's activation scales depend on the whole padded batch.
        return np.vstack([self._embed([t]) for t in texts])

    def _embed(self, texts: list[str]) -> np.ndarray:
        enc = self.tokenizer.encode_batch(texts)
        ids = np.array([e.ids for e in enc], dtype=np.int64)
        mask = np.array([e.attention_mask for e in enc], dtype=np.int64)
        feeds = {"input_ids": ids, "attention_mask": mask, "token_type_ids": np.zeros_like(ids)}
        (hidden,) = self.session.run(["last_hidden_state"], feeds)
        summed = (hidden * mask[..., None]).sum(axis=1)
        vectors = summed / mask.sum(axis=1, keepdims=True)
        return vectors / np.linalg.norm(vectors, axis=1, keepdims=True)


def quantize(vectors: np.ndarray) -> str:
    """Store each unit vector as signed bytes (value * 127), base64 encoded."""
    return base64.b64encode(np.round(vectors * 127).astype(np.int8).tobytes()).decode()


def load_vectors(index: dict) -> np.ndarray:
    raw = np.frombuffer(base64.b64decode(index["vectors"]), dtype=np.int8)
    return raw.reshape(len(index["passages"]), index["dim"]).astype(np.float32) / 127


# Ranking. site/ask.js runs the same steps in the browser; keep them in step.
STOPWORDS = frozenset(
    [
        "a",
        "an",
        "the",
        "of",
        "to",
        "and",
        "or",
        "is",
        "are",
        "was",
        "were",
        "how",
        "what",
        "why",
        "which",
        "does",
        "do",
        "did",
        "you",
        "it",
        "this",
        "that",
        "in",
        "on",
        "for",
        "from",
        "with",
        "by",
        "be",
        "as",
        "at",
    ]
)
KEYWORD_WEIGHT = 0.3


def words(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in STOPWORDS]


def keyword_scores(question: str, items: list[dict]) -> np.ndarray:
    """BM25 score of each passage (with its document title) for the question."""
    docs = [words(f"{p['title']} {p['text']}") for p in items]
    avg_len = sum(map(len, docs)) / len(docs)
    terms = set(words(question))
    df = {t: sum(t in d for d in docs) for t in terms}
    scores = np.zeros(len(docs))
    for i, doc in enumerate(docs):
        norm = 1.2 * (0.25 + 0.75 * len(doc) / avg_len)
        for t in terms:
            tf = doc.count(t)
            if tf:
                idf = np.log(1 + (len(docs) - df[t] + 0.5) / (df[t] + 0.5))
                scores[i] += idf * tf * 2.2 / (tf + norm)
    return scores


def rank(question: str, query: np.ndarray | None, items: list[dict], vectors: np.ndarray) -> list[int]:
    """Passage indices, best first: meaning plus a share of keyword match."""
    keywords = keyword_scores(question, items)
    if keywords.max() > 0:
        keywords /= keywords.max()
    if query is None:  # The model has not loaded: keywords alone.
        return list(np.argsort(-keywords, kind="stable"))
    meaning = vectors @ query
    blended = (1 - KEYWORD_WEIGHT) * meaning + KEYWORD_WEIGHT * keywords * meaning.max()
    return list(np.argsort(-blended, kind="stable"))


def embed_text(p: dict) -> str:
    """What the model embeds for a passage: its document, its section if any, and its text."""
    where = f"{p['title']}, {p['section']}" if p.get("section") else p["title"]
    return f"{where}: {p['text']}"


def main() -> None:
    items = passages()
    vectors = Embedder()([embed_text(p) for p in items])
    index = {
        "model": MODEL,
        "revision": REVISION,
        "dtype": "q8",
        "dim": int(vectors.shape[1]),
        "passages": items,
        "vectors": quantize(vectors),
    }
    OUT.write_text(json.dumps(index, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}: {len(items)} passages from {len(DOCS)} documents")


if __name__ == "__main__":
    main()
