"""Experiment: can a general vision-language model (CLIP) read the board with no training?

Cuts the sample board photo into its 42 holes with the project's own vision code
(grid corners, the same crops the HSV classifier sees), asks CLIP ViT-B/32 which
of three text prompts fits each crop, and writes the result next to the HSV
reading to site/clip-cells.json for the "Two AI experiments" section.

This was not part of the course project; it is a later experiment on one photo
(42 holes), so its numbers are a small sample. Run with
``uv run --group search python scripts/eval_clip_cells.py``.
"""

import json
from pathlib import Path

import cv2
import numpy as np

from connect4_robot.board import COLS, EMPTY, P1, P2, ROWS
from connect4_robot.vision import (
    CELL_INSET_DIV,
    WARP_SIZE,
    CellClassifier,
    VisionPipeline,
    load_corners,
    warp_and_condition,
)

ROOT = Path(__file__).resolve().parents[1]
PHOTO = ROOT / "notebooks" / "board_sample.png"
OUT = ROOT / "site" / "clip-cells.json"
MODEL = "Xenova/clip-vit-base-patch32"
REVISION = "d15189d7028b43f1d3e65039190477f6af591c2a"
PROMPTS = {
    EMPTY: "a photo of an empty hole in a blue Connect 4 board",
    P1: "a photo of a red Connect 4 disc",
    P2: "a photo of a yellow Connect 4 disc",
}
# What the photo shows, read by eye (top row first); same as tests/test_vision.py.
TRUTH = [
    [0, 0, 0, 0, 0, 0, 0],
    [0, 0, 0, 0, 0, 0, 0],
    [0, 0, 0, 1, 0, 0, 0],
    [0, 0, 1, 2, 2, 0, 0],
    [0, 0, 2, 1, 2, 0, 0],
    [0, 2, 1, 1, 2, 2, 0],
]
CLIP_SIZE = 224
CLIP_MEAN = np.array([0.48145466, 0.4578275, 0.40821073], dtype=np.float32)
CLIP_STD = np.array([0.26862954, 0.26130258, 0.27577711], dtype=np.float32)
LOGIT_SCALE = 100.0


def cell_crops(warped: np.ndarray) -> list[np.ndarray]:
    """The 42 inner crops, in reading order, exactly as VisionPipeline.detect_board cuts them."""
    cell_h, cell_w = WARP_SIZE // ROWS, WARP_SIZE // COLS
    crops = []
    for r in range(ROWS):
        for c in range(COLS):
            y1 = r * cell_h + cell_h // CELL_INSET_DIV
            y2 = (r + 1) * cell_h - cell_h // CELL_INSET_DIV
            x1 = c * cell_w + cell_w // CELL_INSET_DIV
            x2 = (c + 1) * cell_w - cell_w // CELL_INSET_DIV
            crops.append(warped[y1:y2, x1:x2])
    return crops


def clip_pixels(crop_bgr: np.ndarray) -> np.ndarray:
    """CLIP preprocessing: shortest side to 224 (bicubic), centre crop 224, normalise."""
    rgb = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2RGB)
    h, w = rgb.shape[:2]
    s = CLIP_SIZE / min(h, w)
    rgb = cv2.resize(rgb, (max(CLIP_SIZE, round(w * s)), max(CLIP_SIZE, round(h * s))), interpolation=cv2.INTER_CUBIC)
    h, w = rgb.shape[:2]
    y, x = (h - CLIP_SIZE) // 2, (w - CLIP_SIZE) // 2
    rgb = rgb[y : y + CLIP_SIZE, x : x + CLIP_SIZE].astype(np.float32) / 255
    return ((rgb - CLIP_MEAN) / CLIP_STD).transpose(2, 0, 1)


def clip_read(crops: list[np.ndarray]) -> tuple[list[int], list[float]]:
    """CLIP's label and its probability for each crop."""
    import onnxruntime
    from huggingface_hub import hf_hub_download
    from tokenizers import Tokenizer

    def fetch(name: str) -> str:
        return hf_hub_download(MODEL, name, revision=REVISION)

    tok = Tokenizer.from_file(fetch("tokenizer.json"))
    tok.enable_padding(pad_id=49407, pad_token="<|endoftext|>")  # noqa: S106 - a tokenizer symbol, not a secret
    labels = list(PROMPTS)
    enc = tok.encode_batch([PROMPTS[k] for k in labels])
    text = onnxruntime.InferenceSession(fetch("onnx/text_model_quantized.onnx"))
    vision = onnxruntime.InferenceSession(fetch("onnx/vision_model_quantized.onnx"))
    feeds = {"input_ids": np.array([e.ids for e in enc], dtype=np.int64)}
    if "attention_mask" in {i.name for i in text.get_inputs()}:
        feeds["attention_mask"] = np.array([e.attention_mask for e in enc], dtype=np.int64)
    t = text.run(["text_embeds"], feeds)[0]
    v = vision.run(["image_embeds"], {"pixel_values": np.stack([clip_pixels(c) for c in crops])})[0]
    t /= np.linalg.norm(t, axis=1, keepdims=True)
    v /= np.linalg.norm(v, axis=1, keepdims=True)
    logits = LOGIT_SCALE * v @ t.T
    probs = np.exp(logits - logits.max(axis=1, keepdims=True))
    probs /= probs.sum(axis=1, keepdims=True)
    best = probs.argmax(axis=1)
    return [labels[i] for i in best], [round(float(probs[n, i]), 3) for n, i in enumerate(best)]


def main() -> None:
    photo = cv2.imread(str(PHOTO))
    warped = warp_and_condition(photo, load_corners(photo.shape))
    vp = VisionPipeline(CellClassifier())
    hsv_board, _ = vp.detect_board(warped)
    truth = [v for row in TRUTH for v in row]
    hsv = [v for row in hsv_board for v in row]
    clip, confidence = clip_read(cell_crops(warped))
    result = {
        "model": MODEL,
        "revision": REVISION,
        "photo": "notebooks/board_sample.png",
        "prompts": {str(k): v for k, v in PROMPTS.items()},
        "truth": truth,
        "hsv": hsv,
        "clip": clip,
        "clip_confidence": confidence,
        "hsv_correct": sum(a == b for a, b in zip(hsv, truth, strict=True)),
        "clip_correct": sum(a == b for a, b in zip(clip, truth, strict=True)),
        "cells": len(truth),
    }
    OUT.write_text(json.dumps(result, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"HSV {result['hsv_correct']}/42, CLIP {result['clip_correct']}/42 -> {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
