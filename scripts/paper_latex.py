"""Read paper/main.tex into plain-text search passages, one section at a time.

The search index used to take the paper from its PDF text, which loses
section names, splits hyphenated words, and runs the author block into the
first passage. Reading the LaTeX keeps each passage inside its section and
labels it the way the PDF does ("IV. Vision and State Estimation", "VI-B.").
Figures, equations, and the bibliography are left out; a table becomes one
passage of its own. scripts/build_reports_index.py places each passage on its
PDF page and checks its words against the PDF text.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEX = ROOT / "paper" / "main.tex"
PASSAGE_WORDS = 90  # a passage ends at the first sentence end past this
MIN_TAIL_WORDS = 30  # a shorter last piece joins the passage before it
DROP = ("figure", "figure*", "equation", "tikzpicture", "IEEEkeywords")
ROMAN = ["", "I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X", "XI", "XII"]
SYMBOLS = {r"\times": "×", r"\alpha": "α", r"\lambda": "λ", r"\in": "∈", r"\pm": "±", r"\le": "≤", r"\ge": "≥"}
ACCENTS = {r"\i{}": "ı", r"\c{s}": "ş", r"\c{c}": "ç", r"\"o": "ö", r"\"u": "ü"}


def body(src: str) -> str:
    """The text from the abstract to the bibliography, without comments."""
    src = re.sub(r"(?<!\\)%.*", "", src)
    return src[src.index(r"\begin{abstract}") : src.index(r"\bibliographystyle")]


def numbering(src: str) -> dict[str, str]:
    """What each \\ref prints: figures and equations 1, 2, ...; tables and sections I, II, ..."""
    labels: dict[str, str] = {}
    counts = {"fig": 0, "tab": 0, "eq": 0, "sec": 0}
    last = "sec"
    for m in re.finditer(r"\\begin\{(figure\*?|table|equation)\}|\\section\{|\\label\{([^}]+)\}", src):
        env = m.group(1)
        if env:
            last = {"figure": "fig", "figure*": "fig", "table": "tab", "equation": "eq"}[env]
            counts[last] += 1
        elif m.group(0).startswith(r"\section"):
            last = "sec"
            counts[last] += 1
        else:
            n = counts[last]
            labels[m.group(2)] = ROMAN[n] if last in ("tab", "sec") else str(n)
    return labels


def citation_numbers(src: str) -> dict[str, int]:
    """IEEEtran numbers references in the order they are first cited."""
    order: dict[str, int] = {}
    for keys in re.findall(r"\\cite\{([^}]+)\}", src):
        for key in keys.split(","):
            order.setdefault(key.strip(), len(order) + 1)
    return order


def inline(text: str, labels: dict[str, str], cites: dict[str, int]) -> str:
    """LaTeX markup to the words the PDF shows. Unknown commands stop the build."""
    for k, v in ACCENTS.items():
        text = text.replace(k, v)
    text = re.sub(
        r"\\cite\{([^}]+)\}", lambda m: "[" + "], [".join(str(cites[k.strip()]) for k in m[1].split(",")) + "]", text
    )
    text = re.sub(r"\\eqref\{([^}]+)\}", lambda m: f"({labels[m[1]]})", text)
    text = re.sub(r"\\ref\{([^}]+)\}", lambda m: labels[m[1]], text)
    text = re.sub(r"\\label\{[^}]*\}", "", text)

    def math(m: re.Match) -> str:
        s = m[1]
        for k, v in SYMBOLS.items():
            s = s.replace(k, v)
        s = re.sub(r"\\math(?:cal|bf|rm)\{([^}]*)\}", r"\1", s)
        return s.replace("{", "").replace("}", "")

    text = re.sub(r"\$([^$]+)\$", math, text)
    text = re.sub(r"\\(?:textbf|emph|textit)\{([^}]*)\}", r"\1", text)
    text = text.replace(r"\%", "%").replace("~", " ").replace("--", "–")
    if re.search(r"\\[A-Za-z]", text):
        raise ValueError(f"unhandled LaTeX command in: {text[:120]}")
    return " ".join(text.split())


def table_text(env: str, labels: dict[str, str], cites: dict[str, int]) -> tuple[str, list[str]]:
    """A table as one sentence per row, plus its cells for the PDF check."""
    caption = inline(re.search(r"\\caption\{([^}]*)\}", env)[1], labels, cites)
    number = labels[re.search(r"\\label\{([^}]+)\}", env)[1]]
    top, mid, bottom = env.index(r"\toprule") + len(r"\toprule"), env.index(r"\midrule"), env.index(r"\bottomrule")
    head = [inline(c, labels, cites) for c in env[top:mid].split(r"\\")[0].split("&")]
    rows, cells = [], [*head]
    for line in env[mid + len(r"\midrule") : bottom].split(r"\\"):
        row = [inline(c, labels, cites) for c in line.split("&")]
        if len(row) < 2:
            continue
        cells += row
        rows.append(f"{row[0]}: " + ", ".join(f"{v} {h.lower()}" for h, v in zip(head[1:], row[1:], strict=True)) + ".")
    return f"Table {number}, {caption.lower()}. " + " ".join(rows), cells


def blocks(src: str):
    """Yield (section label, kind, text, table cells) in reading order."""
    labels, cites = numbering(src), citation_numbers(src)
    text = body(src)
    for env in DROP:
        text = re.sub(rf"\\begin\{{{re.escape(env)}\}}.*?\\end\{{{re.escape(env)}\}}", "\n\n", text, flags=re.DOTALL)
    section, sec_n, sub_n, pos = "Abstract", 0, 0, 0
    token = re.compile(r"\\section\*?\{([^}]*)\}|\\subsection\{([^}]*)\}|\\begin\{table\}.*?\\end\{table\}", re.DOTALL)
    for m in [*token.finditer(text), None]:
        chunk = text[pos : m.start() if m else len(text)]
        for raw in re.split(r"\n\s*\n", chunk):
            para = re.sub(r"\\(begin|end)\{(abstract|enumerate|itemize)\}", "", raw)
            for item in re.split(r"\\item\s*", para):
                words = inline(item, labels, cites)
                if words:
                    yield section, "text", words, None
        if m is None:
            return
        pos = m.end()
        if m[1] is not None:
            if m[0].startswith(r"\section*"):
                section = m[1]
            else:
                sec_n, sub_n = sec_n + 1, 0
                section = f"{ROMAN[sec_n]}. {m[1]}"
        elif m[2] is not None:
            sub_n += 1
            section = f"{ROMAN[sec_n]}-{chr(64 + sub_n)}. {m[2]}"
        else:
            words, cells = table_text(m[0], labels, cites)
            yield section, "table", words, cells


def passages(src: str | None = None) -> list[dict]:
    """Passages of about PASSAGE_WORDS words, cut at sentence ends, never across sections."""
    src = src if src is not None else TEX.read_text(encoding="utf-8")
    out: list[dict] = []
    current: list[str] = []
    current_section = None

    def flush() -> None:
        nonlocal current
        if not current:
            return
        last = out[-1] if out else None
        if last and len(current) < MIN_TAIL_WORDS and last["section"] == current_section and "cells" not in last:
            last["text"] += " " + " ".join(current)
        else:
            out.append({"section": current_section, "text": " ".join(current)})
        current = []

    for section, kind, text, cells in blocks(src):
        if section != current_section:
            flush()
            current_section = section
        if kind == "table":
            flush()
            out.append({"section": section, "text": text, "cells": cells})
            continue
        for sentence in re.split(r"(?<=[.!?])\s+(?=[A-Z(\[])", text):
            current += sentence.split()
            if len(current) >= PASSAGE_WORDS:
                flush()
    flush()
    return out


if __name__ == "__main__":
    for p in passages():
        print(f"[{p['section']}] {len(p['text'].split())}w {p['text'][:100]}")
