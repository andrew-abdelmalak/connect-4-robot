"""Vision pipeline: find the blue board, warp it top-down, and read each cell.

The board is located from its blue frame (HSV mask, largest contour, four
corners smoothed over frames), warped to an 800x800 view, brightened,
contrast-stretched, and blurred, then split into a 6x7 grid. Each cell is
read by HSV thresholds, by an optional RandomForest model, or by a hybrid
that trusts the model only above a confidence threshold.
"""

import json
import pickle
import threading
from pathlib import Path

import cv2
import numpy as np

from .board import COLS, EMPTY, P1, P2, ROWS, Board, empty_board

WARP_SIZE = 800
ML_MODEL_FILE = str(Path.home() / "connect4_cell_model.pkl")
CORNERS_FILE = Path.home() / "warp_corners.json"

# HSV thresholds  (OpenCV: H 0-179, S 0-255, V 0-255)
RED_LO1 = np.array([0, 80, 50])
RED_HI1 = np.array([10, 255, 255])
RED_LO2 = np.array([160, 80, 50])
RED_HI2 = np.array([179, 255, 255])
YEL_LO = np.array([18, 100, 80])
YEL_HI = np.array([42, 255, 255])
MIN_PIX = 30

# Dynamic board detection constants
BLUE_LO = np.array([85, 60, 40])  # board blue HSV lower bound
BLUE_HI = np.array([140, 255, 255])  # board blue HSV upper bound
MIN_BOARD_AREA_RATIO = 0.20  # board must cover >= 20% of frame area
CORNER_ALPHA = 0.25  # EMA weight for corner smoothing
MORPH_KERNEL = 9  # px, closes gaps in the blue mask
POLY_EPSILON = 0.04  # approxPolyDP tolerance, fraction of contour perimeter
HULL_EPSILON = 0.08  # same, for the convex-hull retry
# Fallback corners as fractions of the frame (TL, TR, BR, BL), from the MS2 notebook.
FALLBACK_CORNERS = [[0.03, 0.02], [0.97, 0.02], [0.97, 0.80], [0.03, 0.80]]

# Intensity conditioning applied after the warp (deployed runtime values).
BRIGHTNESS_OFFSET = 20
CONTRAST_GAIN = 1.3
BLUR_KERNEL = (5, 5)

CELL_INSET_DIV = 4  # crop 1/4 of the cell on each side to skip the plastic frame
AUTO_MIN_CONFIDENCE = 0.65  # hybrid mode trusts the model only above this

CLASSIFIER_HSV = "hsv"
CLASSIFIER_ML = "ml"
CLASSIFIER_AUTO = "auto"
VALID_CLASSIFIER_MODES = {CLASSIFIER_HSV, CLASSIFIER_ML, CLASSIFIER_AUTO}
CELL_CLASSIFIER_MODE = CLASSIFIER_HSV


def encode_jpeg(img: np.ndarray, quality: int | None = None) -> bytes:
    """Encode an image as JPEG bytes (OpenCV's default quality when quality is None)."""
    params = [cv2.IMWRITE_JPEG_QUALITY, quality] if quality is not None else []
    _, buf = cv2.imencode(".jpg", img, params)
    return buf.tobytes()


def color_counts(hsv: np.ndarray) -> tuple[int, int]:
    """Return (red, yellow) pixel counts of an HSV image; red wraps around hue 0."""
    red = cv2.countNonZero(cv2.inRange(hsv, RED_LO1, RED_HI1)) + cv2.countNonZero(cv2.inRange(hsv, RED_LO2, RED_HI2))
    yel = cv2.countNonZero(cv2.inRange(hsv, YEL_LO, YEL_HI))
    return red, yel


def load_corners(shape: tuple[int, ...]) -> np.ndarray:
    """Board corners from ~/warp_corners.json, else FALLBACK_CORNERS scaled to the frame."""
    h, w = shape[:2]
    try:
        pts = np.float32(json.loads(CORNERS_FILE.read_text())["corners"])
        if pts.shape == (4, 2):
            return pts
    except (OSError, ValueError, KeyError, TypeError):
        pass  # missing or unreadable corners file: use the default corners
    return np.float32([[fx * w, fy * h] for fx, fy in FALLBACK_CORNERS])


def extract_cell_features(roi: np.ndarray | None) -> np.ndarray:
    """Return the 18 colour-statistics features the ML classifier uses for one cell."""
    if roi is None or roi.size == 0:
        return np.zeros(18, dtype=np.float32)
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    bgr_mean = roi.mean(axis=(0, 1))
    hsv_mean = hsv.mean(axis=(0, 1))
    bgr_std = roi.std(axis=(0, 1))
    hsv_std = hsv.std(axis=(0, 1))
    red, yel = color_counts(hsv)
    total = max(1, roi.shape[0] * roi.shape[1])
    ratios = np.array(
        [
            red / total,
            yel / total,
            (red - yel) / total,
        ],
        dtype=np.float32,
    )
    return np.concatenate(
        [
            bgr_mean.astype(np.float32),
            hsv_mean.astype(np.float32),
            bgr_std.astype(np.float32),
            hsv_std.astype(np.float32),
            ratios,
            np.array(
                [float(total), float(roi.shape[0]), float(roi.shape[1])],
                dtype=np.float32,
            ),
        ]
    )


class CellClassifier:
    """Optional pickled RandomForest cell model plus the active classifier mode."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.model = None
        self.labels: dict | None = None
        self.loaded = False
        self.last_error: str | None = None
        self.mode = CELL_CLASSIFIER_MODE
        self.reload()

    def reload(self) -> bool:
        """Load ML_MODEL_FILE. Returns False (and records why) if it is missing or broken."""
        with self.lock:
            self.model = None
            self.labels = None
            self.loaded = False
            self.last_error = None
            if not Path(ML_MODEL_FILE).exists():
                self.last_error = f"Model not found: {ML_MODEL_FILE}"
                return False
            try:
                # The model file is produced locally by train_cell_model.py; only load trusted files.
                with open(ML_MODEL_FILE, "rb") as f:
                    payload = pickle.load(f)
                self.model = payload["model"]
                self.labels = payload.get("labels", {0: EMPTY, 1: P1, 2: P2})
                self.loaded = True
                return True
            except Exception as e:  # noqa: BLE001 - any unpickling error is shown on the dashboard
                self.last_error = f"Model load failed — {e}"
                return False

    def set_mode(self, mode: str) -> tuple[bool, str]:
        """Switch between "hsv", "ml", and "auto". Returns (ok, message)."""
        if not isinstance(mode, str) or mode not in VALID_CLASSIFIER_MODES:
            return False, f"Unknown classifier mode: {mode}"
        with self.lock:
            self.mode = mode
        return True, f"Classifier mode set to {mode}"

    def predict(self, roi: np.ndarray) -> tuple[int, float | None] | None:
        """Return (label, confidence) from the model, or None in HSV mode or without a model."""
        with self.lock:
            mode = self.mode
            loaded = self.loaded and self.model is not None
            model = self.model
            labels = self.labels
        if mode == CLASSIFIER_HSV or not loaded:
            return None
        try:
            feats = extract_cell_features(roi).reshape(1, -1)
            pred = model.predict(feats)[0]
            confidence = None
            if hasattr(model, "predict_proba"):
                proba = model.predict_proba(feats)[0]
                confidence = float(np.max(proba))
            pred = labels.get(int(pred), int(pred))
            return int(pred), confidence
        except Exception as e:  # noqa: BLE001 - shown on the dashboard; HSV fallback continues
            with self.lock:
                self.last_error = f"Predict failed — {e}"
            return None

    def to_dict(self) -> dict:
        """Return classifier status for the dashboard."""
        with self.lock:
            return {
                "classifier_mode": self.mode,
                "classifier_loaded": self.loaded,
                "classifier_error": self.last_error,
                "classifier_model_file": ML_MODEL_FILE,
            }


class VisionPipeline:
    """Per-camera state: smoothed board corners and detection health."""

    def __init__(self, classifier: CellClassifier) -> None:
        self.classifier = classifier
        self._src: np.ndarray | None = None  # np.float32 (4,2), TL/TR/BR/BL order
        self.board_detected = False  # True when auto-detection succeeded this cycle
        self._detection_age = 0  # frames since last successful detection

    def _order_corners(self, pts: np.ndarray) -> np.ndarray:
        """Convert unordered 4 points to TL, TR, BR, BL order."""
        s = pts.sum(axis=1)
        d = np.diff(pts, axis=1).flatten()
        return np.float32(
            [
                pts[np.argmin(s)],  # TL: smallest x+y
                pts[np.argmin(d)],  # TR: smallest x-y
                pts[np.argmax(s)],  # BR: largest  x+y
                pts[np.argmax(d)],  # BL: largest  x-y
            ]
        )

    def _smooth_corners(self, new_pts: np.ndarray) -> np.ndarray:
        """EMA smoothing to suppress per-frame jitter."""
        if self._src is None:
            return new_pts
        return (1 - CORNER_ALPHA) * self._src + CORNER_ALPHA * new_pts

    def _find_board_corners(self, frame: np.ndarray) -> np.ndarray | None:
        """HSV-mask the blue board frame and return 4 ordered corners, or None."""
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(hsv, BLUE_LO, BLUE_HI)
        k = cv2.getStructuringElement(cv2.MORPH_RECT, (MORPH_KERNEL, MORPH_KERNEL))
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, k)
        cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not cnts:
            return None
        frame_area = frame.shape[0] * frame.shape[1]
        largest = max(cnts, key=cv2.contourArea)
        if cv2.contourArea(largest) < MIN_BOARD_AREA_RATIO * frame_area:
            return None
        peri = cv2.arcLength(largest, True)
        approx = cv2.approxPolyDP(largest, POLY_EPSILON * peri, True)
        if len(approx) == 4:
            pts = approx.reshape(4, 2).astype(np.float32)
        else:
            hull = cv2.convexHull(largest)
            hperi = cv2.arcLength(hull, True)
            app2 = cv2.approxPolyDP(hull, HULL_EPSILON * hperi, True)
            if len(app2) == 4:
                pts = app2.reshape(4, 2).astype(np.float32)
            else:
                x, y, w, h = cv2.boundingRect(hull)
                pts = np.float32([[x, y], [x + w, y], [x + w, y + h], [x, y + h]])
        return self._order_corners(pts)

    def draw_detection_overlay(self, frame: np.ndarray) -> np.ndarray:
        """Draw detected quad + corner labels on raw frame (for preview stream)."""
        out = frame.copy()
        if self._src is not None:
            pts = self._src.astype(np.int32).reshape((-1, 1, 2))
            clr = (0, 220, 0) if self.board_detected else (0, 80, 220)
            cv2.polylines(out, [pts], True, clr, 3)
            for i, (x, y) in enumerate(self._src):
                cv2.circle(out, (int(x), int(y)), 8, (0, 255, 255), -1)
                cv2.putText(
                    out,
                    ["TL", "TR", "BR", "BL"][i],
                    (int(x) + 8, int(y) - 6),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.6,
                    (0, 255, 255),
                    2,
                )
        label = "BOARD: AUTO" if self.board_detected else f"FALLBACK (stale={self._detection_age})"
        cv2.putText(
            out,
            label,
            (10, 28),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.65,
            (0, 220, 0) if self.board_detected else (0, 80, 220),
            2,
        )
        return out

    def preprocess(self, frame: np.ndarray) -> np.ndarray:
        """Auto-detect board → warp → brightness → contrast → blur. Returns color 800x800."""
        found = self._find_board_corners(frame)
        if found is not None:
            self._src = self._smooth_corners(found)
            self.board_detected = True
            self._detection_age = 0
        else:
            self._detection_age += 1
            self.board_detected = False
            if self._src is None:
                self._src = load_corners(frame.shape)  # first-frame fallback only
        return warp_and_condition(frame, self._src)

    def classify_cell(self, roi: np.ndarray | None) -> int:
        """Return EMPTY, P1, or P2 for one cell image, honouring the classifier mode."""
        if roi is None or roi.size == 0:
            return EMPTY
        ml_pred = self.classifier.predict(roi)
        if ml_pred is not None:
            pred, confidence = ml_pred
            if self.classifier.mode == CLASSIFIER_ML:
                return pred
            if confidence is not None and confidence >= AUTO_MIN_CONFIDENCE:
                return pred
        red, yel = color_counts(cv2.cvtColor(roi, cv2.COLOR_BGR2HSV))
        if red >= MIN_PIX and red >= yel:
            return P1
        if yel >= MIN_PIX and yel > red:
            return P2
        return EMPTY

    def detect_board(self, warped: np.ndarray) -> tuple[Board, np.ndarray]:
        """Returns (6x7 board matrix, debug image with grid + classification dots)."""
        board = empty_board()
        debug = warped.copy()
        cell_h = WARP_SIZE // ROWS
        cell_w = WARP_SIZE // COLS
        chip_clr = {EMPTY: (80, 80, 80), P1: (0, 0, 220), P2: (20, 210, 210)}
        for r in range(ROWS):
            for c in range(COLS):
                y1 = r * cell_h + cell_h // CELL_INSET_DIV
                y2 = (r + 1) * cell_h - cell_h // CELL_INSET_DIV
                x1 = c * cell_w + cell_w // CELL_INSET_DIV
                x2 = (c + 1) * cell_w - cell_w // CELL_INSET_DIV
                val = self.classify_cell(warped[y1:y2, x1:x2])
                board[r][c] = val
                cx = c * cell_w + cell_w // 2
                cy = r * cell_h + cell_h // 2
                cv2.circle(debug, (cx, cy), cell_w // 4, chip_clr[val], -1)
                cv2.circle(debug, (cx, cy), cell_w // 4, (0, 0, 0), 1)
        for i in range(ROWS + 1):
            cv2.line(debug, (0, i * cell_h), (WARP_SIZE, i * cell_h), (180, 180, 180), 1)
        for i in range(COLS + 1):
            cv2.line(debug, (i * cell_w, 0), (i * cell_w, WARP_SIZE), (180, 180, 180), 1)
        lbl = "BOARD: AUTO" if self.board_detected else f"BOARD: FALLBACK age={self._detection_age}"
        cv2.putText(
            debug,
            lbl,
            (10, 22),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            (0, 220, 0) if self.board_detected else (0, 80, 220),
            2,
        )
        return board, debug


def warp_and_condition(frame: np.ndarray, corners: np.ndarray) -> np.ndarray:
    """Warp the board quad to WARP_SIZE square, then brighten, stretch contrast, and blur."""
    dst = np.float32([[0, 0], [WARP_SIZE, 0], [WARP_SIZE, WARP_SIZE], [0, WARP_SIZE]])
    m = cv2.getPerspectiveTransform(corners, dst)
    out = cv2.warpPerspective(frame, m, (WARP_SIZE, WARP_SIZE))
    out = cv2.convertScaleAbs(out, alpha=1.0, beta=BRIGHTNESS_OFFSET)
    out = cv2.convertScaleAbs(out, alpha=CONTRAST_GAIN, beta=0)
    out = cv2.GaussianBlur(out, BLUR_KERNEL, 0)
    return out
