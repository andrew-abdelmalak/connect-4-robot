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
import json
import re
import subprocess
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "site" / "reports-index.json"
MODEL = "Xenova/all-MiniLM-L6-v2"
REVISION = "751bff37182d3f1213fa05d7196b954e230abad9"
ONNX_FILE = "onnx/model_quantized.onnx"  # transformers.js dtype "q8"
PASSAGE_WORDS = 90
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


def page_words(pdf: Path, page: int) -> list[str]:
    text = subprocess.run(
        ["pdftotext", "-f", str(page), "-l", str(page), "-enc", "UTF-8", str(pdf), "-"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    text = re.sub(r"-\n(\w)", r"\1", text)  # join words hyphenated across lines
    return text.split()


def passages() -> list[dict]:
    """Every page of every document, cut into passages of about PASSAGE_WORDS words."""
    items = []
    for file, title in DOCS:
        pdf = ROOT / "docs" / file
        for page in range(1, page_count(pdf) + 1):
            ws = page_words(pdf, page)
            chunks = [ws[i : i + PASSAGE_WORDS] for i in range(0, len(ws), PASSAGE_WORDS)]
            if len(chunks) > 1 and len(chunks[-1]) < MIN_TAIL_WORDS:
                tail = chunks.pop()
                chunks[-1] += tail
            for chunk in chunks:
                if len(chunk) >= MIN_TAIL_WORDS // 2:
                    items.append({"doc": file, "title": title, "page": page, "text": " ".join(chunk)})
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


def main() -> None:
    items = passages()
    vectors = Embedder()([f"{p['title']}: {p['text']}" for p in items])
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
