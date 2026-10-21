"""Train the optional RandomForest cell classifier used by the ML and Auto modes.

Expected dataset layout (ROI images cropped from board cells)::

    ~/ml_cells/
        empty/
        red/
        yellow/

Writes ``~/connect4_cell_model.pkl``. Run with
``python -m connect4_robot.train_cell_model``.
"""

import os
import pickle
from pathlib import Path

import cv2
import numpy as np
from sklearn.ensemble import RandomForestClassifier

from .board import EMPTY, P1, P2
from .vision import ML_MODEL_FILE, extract_cell_features

DATASET_DIR = Path(os.path.expanduser("~/ml_cells"))
LABELS = {
    "empty": EMPTY,
    "red": P1,
    "yellow": P2,
}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp"}
N_TREES = 200
MAX_DEPTH = 10
SEED = 42


def load_dataset(dataset_dir: Path = DATASET_DIR) -> tuple[np.ndarray, np.ndarray]:
    """Return (features, labels) for every readable image under dataset_dir/<label>/."""
    X, y = [], []
    for name, label in LABELS.items():
        folder = dataset_dir / name
        if not folder.exists():
            continue
        for path in sorted(folder.iterdir()):
            if path.suffix.lower() not in IMAGE_SUFFIXES:
                continue
            img = cv2.imread(str(path))
            if img is None:
                continue
            X.append(extract_cell_features(img))
            y.append(label)
    return np.array(X, dtype=np.float32), np.array(y, dtype=np.int32)


def train(X: np.ndarray, y: np.ndarray) -> RandomForestClassifier:
    """Fit the cell classifier with the project's fixed hyperparameters."""
    model = RandomForestClassifier(
        n_estimators=N_TREES,
        max_depth=MAX_DEPTH,
        random_state=SEED,
        class_weight="balanced",
    )
    model.fit(X, y)
    return model


def main() -> None:
    """Train on ~/ml_cells and save the model where the dashboard loads it."""
    X, y = load_dataset()
    if len(X) == 0:
        raise SystemExit(f"No training images found under {DATASET_DIR}")
    model = train(X, y)
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
