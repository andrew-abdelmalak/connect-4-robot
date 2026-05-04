#!/usr/bin/env python3
"""
Train a simple cell classifier for Connect-4 chip recognition.

Expected dataset layout:
  ~/ml_cells/
    empty/
    red/
    yellow/

Each folder should contain ROI images cropped from board cells.
Outputs:
  ~/connect4_cell_model.pkl
"""

import os
import pickle
from pathlib import Path

import cv2
import numpy as np

from sklearn.ensemble import RandomForestClassifier

from connect4_brain import (
    EMPTY,
    P1,
    P2,
    ML_MODEL_FILE,
    extract_cell_features,
)

DATASET_DIR = Path(os.path.expanduser("~/ml_cells"))
LABELS = {
    "empty": EMPTY,
    "red": P1,
    "yellow": P2,
}


def load_dataset():
    X, y = [], []
    for name, label in LABELS.items():
        folder = DATASET_DIR / name
        if not folder.exists():
            continue
        for path in sorted(folder.iterdir()):
            if path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".bmp"}:
                continue
            img = cv2.imread(str(path))
            if img is None:
                continue
            X.append(extract_cell_features(img))
            y.append(label)
    return np.array(X, dtype=np.float32), np.array(y, dtype=np.int32)


def main():
    X, y = load_dataset()
    if len(X) == 0:
        raise SystemExit(f"No training images found under {DATASET_DIR}")
    model = RandomForestClassifier(
        n_estimators=200,
        max_depth=10,
        random_state=42,
        class_weight="balanced",
    )
    model.fit(X, y)
    payload = {
        "model": model,
        "labels": {
            0: EMPTY,
            1: P1,
            2: P2,
            EMPTY: EMPTY,
            P1: P1,
            P2: P2,
        },
    }
    out = Path(ML_MODEL_FILE)
    with open(out, "wb") as f:
        pickle.dump(payload, f)
    print(f"Saved model to {out}")
    print(f"Samples: {len(X)}")


if __name__ == "__main__":
    main()
