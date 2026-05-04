#!/usr/bin/env python3
"""
connect4_brain.py  —  Connect 4 Brain Dashboard
Vision + Minimax + State Validation  |  Simulation & Camera modes

Usage:
  python3 connect4_brain.py --sim          # simulation mode (no camera)
  python3 connect4_brain.py                # camera mode (low-FPS)
  python3 connect4_brain.py --sim --port 5001 --depth 4
"""
import argparse, json, os, pickle, socket, threading, time

import numpy as np
from flask import Flask, Response, jsonify, request

try:
    import cv2
    CV2_OK = True
except ImportError:
    CV2_OK = False

try:
    import resource, subprocess
    SYSINFO_OK = True
except ImportError:
    SYSINFO_OK = False

try:
    import serial
    SERIAL_OK = True
except ImportError:
    SERIAL_OK = False

# ═══════════════════════════════════════════════════════════════════════════════
# CONSTANTS
# ═══════════════════════════════════════════════════════════════════════════════
ROWS = 6
COLS = 7
EMPTY = 0
P1    = 1   # RED
P2    = 2   # YELLOW

ROTATION_ANGLE    = 15.0
WARP_SIZE         = 800
ANALYSIS_INTERVAL = 1.0   # seconds between full board analyses (camera mode)
PREVIEW_INTERVAL  = 0.2   # seconds between preview frames (~5 FPS)
MINIMAX_DEPTH     = 5
SERIAL_PORT       = "/dev/ttyUSB0"
SERIAL_BAUD       = 9600
ROBOT_SETTLE_SEC  = 2.5
VERIFY_TIMEOUT    = 10.0
VERIFY_POLL_SEC   = 0.4
ML_MODEL_FILE     = os.path.expanduser("~/connect4_cell_model.pkl")

CORNERS_FILE = os.path.expanduser("~/warp_corners.json")

# HSV thresholds  (OpenCV: H 0-179, S 0-255, V 0-255)
RED_LO1  = np.array([ 0, 80,  50])
RED_HI1  = np.array([10, 255, 255])
RED_LO2  = np.array([160, 80,  50])
RED_HI2  = np.array([179, 255, 255])
YEL_LO   = np.array([ 18, 100,  80])
YEL_HI   = np.array([ 42, 255, 255])
MIN_PIX  = 30

# Dynamic board detection constants
BLUE_LO              = np.array([ 85,  60,  40])   # board blue HSV lower bound
BLUE_HI              = np.array([140, 255, 255])   # board blue HSV upper bound
MIN_BOARD_AREA_RATIO = 0.20     # board must cover >= 20% of frame area
CORNER_ALPHA         = 0.25     # EMA weight for corner smoothing

# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 1 — BOARD / GAME LOGIC
# ═══════════════════════════════════════════════════════════════════════════════

def empty_board():
    return [[EMPTY]*COLS for _ in range(ROWS)]

def board_copy(b):
    return [row[:] for row in b]

def boards_equal(a, b):
    return all(a[r][c] == b[r][c] for r in range(ROWS) for c in range(COLS))

def get_legal_cols(board):
    return [c for c in range(COLS) if board[0][c] == EMPTY]

def lowest_empty_row(board, col):
    """Highest row-index (lowest on board) that is empty. -1 if column full."""
    for r in range(ROWS - 1, -1, -1):
        if board[r][col] == EMPTY:
            return r
    return -1

def apply_move(board, col, player):
    """Return new board with chip dropped, or None if illegal."""
    r = lowest_empty_row(board, col)
    if r == -1:
        return None
    nb = board_copy(board)
    nb[r][col] = player
    return nb

def check_win(board, player):
    """Return True if player has 4 in a row anywhere."""
    for r in range(ROWS):                          # horizontal
        for c in range(COLS - 3):
            if all(board[r][c+i] == player for i in range(4)):
                return True
    for c in range(COLS):                          # vertical
        for r in range(ROWS - 3):
            if all(board[r+i][c] == player for i in range(4)):
                return True
    for r in range(ROWS - 3):                     # diagonal down-right
        for c in range(COLS - 3):
            if all(board[r+i][c+i] == player for i in range(4)):
                return True
    for r in range(3, ROWS):                      # diagonal down-left
        for c in range(COLS - 3):
            if all(board[r-i][c+i] == player for i in range(4)):
                return True
    return False

def check_draw(board):
    return all(board[0][c] != EMPTY for c in range(COLS))

def count_chips(board, player):
    return sum(board[r][c] == player for r in range(ROWS) for c in range(COLS))

def color_name(p):
    if p == P1: return "RED"
    if p == P2: return "YELLOW"
    return "None"

MODE_MANUAL    = "manual"
MODE_SEMI_AUTO = "semi_auto"
MODE_AUTO      = "auto"
VALID_OPERATOR_MODES = {MODE_MANUAL, MODE_SEMI_AUTO, MODE_AUTO}

CLASSIFIER_HSV  = "hsv"
CLASSIFIER_ML   = "ml"
CLASSIFIER_AUTO = "auto"
VALID_CLASSIFIER_MODES = {CLASSIFIER_HSV, CLASSIFIER_ML, CLASSIFIER_AUTO}
CELL_CLASSIFIER_MODE = CLASSIFIER_HSV

# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 2 — MINIMAX WITH ALPHA-BETA PRUNING
# ═══════════════════════════════════════════════════════════════════════════════

def _score_window(window, player):
    opp = 3 - player
    pc  = window.count(player)
    ec  = window.count(EMPTY)
    oc  = window.count(opp)
    if pc == 4:              return  100
    if pc == 3 and ec == 1:  return    5
    if pc == 2 and ec == 2:  return    2
    if oc == 3 and ec == 1:  return   -4
    return 0

def _evaluate(board, player):
    score = 0
    for r in range(ROWS):                              # center column bonus
        if board[r][3] == player:
            score += 3
    for r in range(ROWS):                              # horizontal
        for c in range(COLS - 3):
            score += _score_window([board[r][c+i] for i in range(4)], player)
    for c in range(COLS):                              # vertical
        for r in range(ROWS - 3):
            score += _score_window([board[r+i][c] for i in range(4)], player)
    for r in range(ROWS - 3):                         # diagonal down-right
        for c in range(COLS - 3):
            score += _score_window([board[r+i][c+i] for i in range(4)], player)
    for r in range(3, ROWS):                          # diagonal down-left
        for c in range(COLS - 3):
            score += _score_window([board[r-i][c+i] for i in range(4)], player)
    return score

def _minimax(board, depth, alpha, beta, maximizing, robot, user):
    legal = get_legal_cols(board)
    if check_win(board, robot):
        return (1_000_000 + depth, -1)
    if check_win(board, user):
        return (-1_000_000 - depth, -1)
    if not legal or depth == 0:
        return (_evaluate(board, robot), -1)
    order = sorted(legal, key=lambda c: abs(c - COLS // 2))  # center-first
    if maximizing:
        best, best_col = -1e18, order[0]
        for c in order:
            nb = apply_move(board, c, robot)
            sc, _ = _minimax(nb, depth - 1, alpha, beta, False, robot, user)
            if sc > best:
                best, best_col = sc, c
            alpha = max(alpha, best)
            if alpha >= beta:
                break
        return best, best_col
    else:
        best, best_col = 1e18, order[0]
        for c in order:
            nb = apply_move(board, c, user)
            sc, _ = _minimax(nb, depth - 1, alpha, beta, True, robot, user)
            if sc < best:
                best, best_col = sc, c
            beta = min(beta, best)
            if alpha >= beta:
                break
        return best, best_col

def get_best_move(board, robot, user, depth=MINIMAX_DEPTH):
    """Returns (col, score, reason_str). Quick wins/blocks before full search."""
    legal = get_legal_cols(board)
    if not legal:
        return None, 0, "No legal columns"
    for c in legal:                                    # immediate win?
        nb = apply_move(board, c, robot)
        if nb and check_win(nb, robot):
            return c, 1_000_000, f"Immediate win in column {c}"
    for c in legal:                                    # must block?
        nb = apply_move(board, c, user)
        if nb and check_win(nb, user):
            return c, -999_999, f"Blocking opponent win in column {c}"
    t0 = time.time()
    score, col = _minimax(board, depth, -1e18, 1e18, True, robot, user)
    return col, score, f"Minimax depth={depth} score={int(score)} time={time.time()-t0:.2f}s"

def find_new_chip(prev, new_b):
    new_chips = [(r, c) for r in range(ROWS) for c in range(COLS)
                 if prev[r][c] == EMPTY and new_b[r][c] != EMPTY]
    if len(new_chips) != 1:
        return None
    r, c = new_chips[0]
    return (r, c, new_b[r][c])

def extract_cell_features(roi):
    if roi is None or roi.size == 0:
        return np.zeros(18, dtype=np.float32)
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    bgr_mean = roi.mean(axis=(0, 1))
    hsv_mean = hsv.mean(axis=(0, 1))
    bgr_std  = roi.std(axis=(0, 1))
    hsv_std  = hsv.std(axis=(0, 1))
    red_1 = cv2.countNonZero(cv2.inRange(hsv, RED_LO1, RED_HI1))
    red_2 = cv2.countNonZero(cv2.inRange(hsv, RED_LO2, RED_HI2))
    yel   = cv2.countNonZero(cv2.inRange(hsv, YEL_LO,  YEL_HI))
    total = max(1, roi.shape[0] * roi.shape[1])
    ratios = np.array([
        (red_1 + red_2) / total,
        yel / total,
        (red_1 + red_2 - yel) / total,
    ], dtype=np.float32)
    return np.concatenate([
        bgr_mean.astype(np.float32),
        hsv_mean.astype(np.float32),
        bgr_std.astype(np.float32),
        hsv_std.astype(np.float32),
        ratios,
        np.array([float(total), float(roi.shape[0]), float(roi.shape[1])], dtype=np.float32),
    ])

class CellClassifier:
    def __init__(self):
        self.lock = threading.Lock()
        self.model = None
        self.labels = None
        self.loaded = False
        self.last_error = None
        self.last_loaded_at = None
        self.mode = CELL_CLASSIFIER_MODE
        self.reload()

    def reload(self):
        with self.lock:
            self.model = None
            self.labels = None
            self.loaded = False
            self.last_error = None
            if not os.path.exists(ML_MODEL_FILE):
                self.last_error = f"Model not found: {ML_MODEL_FILE}"
                return False
            try:
                with open(ML_MODEL_FILE, "rb") as f:
                    payload = pickle.load(f)
                self.model = payload["model"]
                self.labels = payload.get("labels", {0: EMPTY, 1: P1, 2: P2})
                self.loaded = True
                self.last_loaded_at = round(time.time(), 3)
                return True
            except Exception as e:
                self.last_error = f"Model load failed — {e}"
                return False

    def set_mode(self, mode):
        global CELL_CLASSIFIER_MODE
        if mode not in VALID_CLASSIFIER_MODES:
            return False, f"Unknown classifier mode: {mode}"
        with self.lock:
            self.mode = mode
            CELL_CLASSIFIER_MODE = mode
        return True, f"Classifier mode set to {mode}"

    def predict(self, roi):
        with self.lock:
            mode = self.mode
            loaded = self.loaded and self.model is not None
            model = self.model
            labels = self.labels
        if mode == CLASSIFIER_HSV:
            return None
        if not loaded:
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
        except Exception as e:
            with self.lock:
                self.last_error = f"Predict failed — {e}"
            return None

    def to_dict(self):
        with self.lock:
            return {
                "classifier_mode": self.mode,
                "classifier_loaded": self.loaded,
                "classifier_error": self.last_error,
                "classifier_model_file": ML_MODEL_FILE,
            }

# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 3 — GAME STATE / TRUST MANAGER
# ═══════════════════════════════════════════════════════════════════════════════

class GameState:
    def __init__(self):
        self.reset()

    def reset(self):
        self.trusted       = empty_board()
        self.previous      = empty_board()
        self.candidate     = None
        self.user_color    = None
        self.robot_color   = None
        self.whose_turn    = "user"
        self.winner        = None
        self.is_draw       = False
        self.last_col      = None
        self.last_player   = None
        self.move_count    = 0
        self.status_msg    = "Waiting for first move (user plays first)."
        self.reject_reason = None
        self.mm_col        = None
        self.mm_score      = None
        self.mm_reason     = "Not yet computed."
        self.mm_time       = None
        self.mm_ran        = False

    def _validate(self, prev, new_b):
        """Check prev→new_b is a legal single-chip update.
        Returns (valid, col, player, reason)."""
        if boards_equal(prev, new_b):
            return False, -1, -1, "Board unchanged — no move detected"
        diffs = [(r, c) for r in range(ROWS) for c in range(COLS)
                 if prev[r][c] != new_b[r][c]]
        for r, c in diffs:
            if prev[r][c] != EMPTY and new_b[r][c] == EMPTY:
                return False, -1, -1, f"Chip disappeared at row {r}, col {c}"
        new_chips = [(r, c) for r, c in diffs
                     if prev[r][c] == EMPTY and new_b[r][c] != EMPTY]
        if len(new_chips) == 0:
            return False, -1, -1, "No new chip found despite board change"
        if len(new_chips) > 1:
            return False, -1, -1, f"{len(new_chips)} new chips — impossible in one turn"
        r, c = new_chips[0]
        player = new_b[r][c]
        expected_r = lowest_empty_row(prev, c)
        if expected_r == -1:
            return False, -1, -1, f"Column {c} was already full"
        if r != expected_r:
            return False, -1, -1, (f"Floating chip: col {c} row {r}, "
                                    f"expected row {expected_r}")
        if self.user_color is not None:
            if player == self.user_color and self.whose_turn != "user":
                return False, -1, -1, "User color chip but it is robot's turn"
            if player == self.robot_color and self.whose_turn != "robot":
                return False, -1, -1, "Robot color chip but it is user's turn"
        return True, c, player, f"Valid: {color_name(player)} in col {c}, row {r}"

    def accept(self, candidate):
        """Try to accept candidate as new trusted board. Returns (ok, reason)."""
        self.candidate = candidate
        if self.winner or self.is_draw:
            msg = "Game already over — reset to play again"
            self.reject_reason = msg
            return False, msg
        valid, col, player, reason = self._validate(self.trusted, candidate)
        if not valid:
            self.reject_reason = reason
            self.status_msg = f"REJECTED: {reason}"
            return False, reason
        self.reject_reason = None
        if self.user_color is None:
            self.user_color  = player
            self.robot_color = P2 if player == P1 else P1
        self.previous    = board_copy(self.trusted)
        self.trusted     = board_copy(candidate)
        self.last_col    = col
        self.last_player = player
        self.move_count += 1
        who = "User" if player == self.user_color else "Robot"
        if check_win(self.trusted, player):
            self.winner = who
            self.whose_turn = None
            self.status_msg = f"GAME OVER — {who} ({color_name(player)}) wins!"
            return True, self.status_msg
        if check_draw(self.trusted):
            self.is_draw = True
            self.whose_turn = None
            self.status_msg = "GAME OVER — Draw (board full)"
            return True, "Draw"
        self.whose_turn = "robot" if who == "User" else "user"
        self.status_msg = (f"Accepted: {color_name(player)} → col {col}. "
                           f"Now {self.whose_turn}'s turn.")
        if self.whose_turn == "robot" and self.robot_color is not None:
            self._run_minimax()
        else:
            self.mm_ran = False
            self.mm_col = None
            self.mm_reason = "Waiting for user's move."
        return True, self.status_msg

    def _run_minimax(self):
        t0 = time.time()
        col, score, reason = get_best_move(
            self.trusted, self.robot_color, self.user_color, MINIMAX_DEPTH)
        self.mm_col    = col
        self.mm_score  = score
        self.mm_reason = reason
        self.mm_time   = round(time.time() - t0, 3)
        self.mm_ran    = True

    def to_dict(self):
        return {
            "trusted":       self.trusted,
            "previous":      self.previous,
            "candidate":     self.candidate,
            "user_color":    color_name(self.user_color),
            "robot_color":   color_name(self.robot_color),
            "whose_turn":    self.whose_turn,
            "winner":        self.winner,
            "is_draw":       self.is_draw,
            "last_col":      self.last_col,
            "last_player":   color_name(self.last_player),
            "move_count":    self.move_count,
            "status_msg":    self.status_msg,
            "reject_reason": self.reject_reason,
            "mm_col":        self.mm_col,
            "mm_score":      int(self.mm_score) if self.mm_score is not None else None,
            "mm_reason":     self.mm_reason,
            "mm_time":       self.mm_time,
            "mm_ran":        self.mm_ran,
            "user_chips":    count_chips(self.trusted, self.user_color) if self.user_color else 0,
            "robot_chips":   count_chips(self.trusted, self.robot_color) if self.robot_color else 0,
            "legal_cols":    get_legal_cols(self.trusted),
        }

# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 4 — VISION PIPELINE  (camera mode; skipped in simulation)
# ═══════════════════════════════════════════════════════════════════════════════

class VisionPipeline:
    def __init__(self):
        self._src           = None    # np.float32 (4,2), TL/TR/BR/BL order
        self.board_detected = False   # True when auto-detection succeeded this cycle
        self._detection_age = 0       # frames since last successful detection

    def _order_corners(self, pts):
        """Convert unordered 4 points to TL, TR, BR, BL order."""
        s = pts.sum(axis=1)
        d = np.diff(pts, axis=1).flatten()
        return np.float32([
            pts[np.argmin(s)],   # TL: smallest x+y
            pts[np.argmin(d)],   # TR: smallest x-y
            pts[np.argmax(s)],   # BR: largest  x+y
            pts[np.argmax(d)],   # BL: largest  x-y
        ])

    def _smooth_corners(self, new_pts):
        """EMA smoothing to suppress per-frame jitter."""
        if self._src is None:
            return new_pts
        return (1 - CORNER_ALPHA) * self._src + CORNER_ALPHA * new_pts

    def _find_board_corners(self, frame):
        """HSV-mask the blue board frame and return 4 ordered corners, or None."""
        hsv  = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(hsv, BLUE_LO, BLUE_HI)
        k    = cv2.getStructuringElement(cv2.MORPH_RECT, (9, 9))
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN,  k)
        cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not cnts:
            return None
        frame_area = frame.shape[0] * frame.shape[1]
        largest = max(cnts, key=cv2.contourArea)
        if cv2.contourArea(largest) < MIN_BOARD_AREA_RATIO * frame_area:
            return None
        candidate = largest
        peri   = cv2.arcLength(candidate, True)
        approx = cv2.approxPolyDP(candidate, 0.04 * peri, True)
        if len(approx) == 4:
            pts = approx.reshape(4, 2).astype(np.float32)
        else:
            hull  = cv2.convexHull(candidate)
            hperi = cv2.arcLength(hull, True)
            app2  = cv2.approxPolyDP(hull, 0.08 * hperi, True)
            if len(app2) == 4:
                pts = app2.reshape(4, 2).astype(np.float32)
            else:
                x, y, w, h = cv2.boundingRect(hull)
                pts = np.float32([[x, y], [x+w, y], [x+w, y+h], [x, y+h]])
        return self._order_corners(pts)

    def draw_detection_overlay(self, frame):
        """Draw detected quad + corner labels on raw frame (for preview stream)."""
        out = frame.copy()
        if self._src is not None:
            pts = self._src.astype(np.int32).reshape((-1, 1, 2))
            clr = (0, 220, 0) if self.board_detected else (0, 80, 220)
            cv2.polylines(out, [pts], True, clr, 3)
            for i, (x, y) in enumerate(self._src):
                cv2.circle(out, (int(x), int(y)), 8, (0, 255, 255), -1)
                cv2.putText(out, ['TL','TR','BR','BL'][i],
                            (int(x)+8, int(y)-6),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
        label = "BOARD: AUTO" if self.board_detected else f"FALLBACK (stale={self._detection_age})"
        cv2.putText(out, label, (10, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.65,
                    (0, 220, 0) if self.board_detected else (0, 80, 220), 2)
        return out

    def _load_corners(self, shape):
        h, w = shape[:2]
        if os.path.exists(CORNERS_FILE):
            try:
                data = json.load(open(CORNERS_FILE))
                pts = np.float32(data["corners"])
                if pts.shape == (4, 2):
                    return pts
            except Exception:
                pass
        return np.float32([
            [0.03*w, 0.02*h], [0.97*w, 0.02*h],
            [0.97*w, 0.80*h], [0.03*w, 0.80*h],
        ])

    def preprocess(self, frame):
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
                self._src = self._load_corners(frame.shape)   # first-frame fallback only
        dst = np.float32([[0,0],[WARP_SIZE,0],[WARP_SIZE,WARP_SIZE],[0,WARP_SIZE]])
        M   = cv2.getPerspectiveTransform(self._src, dst)
        out = cv2.warpPerspective(frame, M, (WARP_SIZE, WARP_SIZE))
        out = cv2.convertScaleAbs(out, alpha=1.0, beta=20)
        out = cv2.convertScaleAbs(out, alpha=1.3, beta=0)
        out = cv2.GaussianBlur(out, (5, 5), 0)
        return out

    def classify_cell(self, roi):
        if roi is None or roi.size == 0:
            return EMPTY
        ml_pred = cell_classifier.predict(roi)
        if ml_pred is not None:
            pred, confidence = ml_pred
            if CELL_CLASSIFIER_MODE == CLASSIFIER_ML:
                return pred
            if confidence is not None and confidence >= 0.65:
                return pred
        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
        m1  = cv2.inRange(hsv, RED_LO1, RED_HI1)
        m2  = cv2.inRange(hsv, RED_LO2, RED_HI2)
        red = cv2.countNonZero(cv2.bitwise_or(m1, m2))
        yel = cv2.countNonZero(cv2.inRange(hsv, YEL_LO, YEL_HI))
        if red >= MIN_PIX and red >= yel:
            return P1
        if yel >= MIN_PIX and yel > red:
            return P2
        return EMPTY

    def detect_board(self, warped):
        """Returns (6x7 board matrix, debug image with grid + classification dots)."""
        board  = empty_board()
        debug  = warped.copy()
        cell_h = WARP_SIZE // ROWS
        cell_w = WARP_SIZE // COLS
        CHIP_CLR = {EMPTY: (80,80,80), P1: (0,0,220), P2: (20,210,210)}
        for r in range(ROWS):
            for c in range(COLS):
                y1 = r*cell_h + cell_h//4;  y2 = (r+1)*cell_h - cell_h//4
                x1 = c*cell_w + cell_w//4;  x2 = (c+1)*cell_w - cell_w//4
                val = self.classify_cell(warped[y1:y2, x1:x2])
                board[r][c] = val
                cx = c*cell_w + cell_w//2;  cy = r*cell_h + cell_h//2
                cv2.circle(debug, (cx, cy), cell_w//4, CHIP_CLR[val], -1)
                cv2.circle(debug, (cx, cy), cell_w//4, (0,0,0), 1)
        for i in range(ROWS+1):
            cv2.line(debug, (0,i*cell_h), (WARP_SIZE,i*cell_h), (180,180,180), 1)
        for i in range(COLS+1):
            cv2.line(debug, (i*cell_w,0), (i*cell_w,WARP_SIZE), (180,180,180), 1)
        lbl = "BOARD: AUTO" if self.board_detected else f"BOARD: FALLBACK age={self._detection_age}"
        cv2.putText(debug, lbl, (10, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.55,
                    (0, 220, 0) if self.board_detected else (0, 80, 220), 2)
        return board, debug

# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 5 — SIMULATION TEST STATES
# ═══════════════════════════════════════════════════════════════════════════════

def _bc(*rows_bottom_up):
    """Build board from strings read bottom-up. R=P1(RED), Y=P2(YEL), .=empty."""
    b = empty_board()
    for i, row in enumerate(rows_bottom_up):
        r = ROWS - 1 - i
        for c, ch in enumerate(row[:COLS]):
            if   ch == "R": b[r][c] = P1
            elif ch == "Y": b[r][c] = P2
    return b

def _build_sim_states():
    # ── valid game sequence ──────────────────────────────────────────────────
    # Row 5 = bottom, row 0 = top  |  Chips fall down
    b1 = _bc("...R...")                        # User: RED col 3
    b2 = _bc("...R...", "...Y...")             # Robot: YELLOW col 3 (on top)
    b3 = _bc(".R.R...", "...Y...")             # User: RED col 1
    b4 = _bc(".R.R..Y", "...Y...")             # Robot: YELLOW col 6
    b5 = _bc(".RRR..Y", "...Y...")             # User: RED col 2  → threat: cols 1,2,3
    b6 = _bc(".RRRYRY", "...Y...")             # Robot: YELLOW col 5 (blocks wrong side; minimax should prefer col 4)
    # After b5: user has R at row5 cols 1,2,3. Needs col 0 or col 4 to win.
    # Correct robot move: col 4 (blocks 1-2-3-4) or col 0 (blocks 0-1-2-3)
    # For the sim, robot plays col 5 (not ideal—test minimax reasoning)
    b6 = _bc(".RRR.YY", "...Y...")             # Robot: YELLOW col 4 — good block
    b7 = _bc("RRRR.YY", "...Y...")             # User: RED col 0 → WINS (0,1,2,3)

    # ── illegal test states ──────────────────────────────────────────────────
    # After b1 is accepted, trusted=b1. These compare against b1:
    b_float   = _bc("...R...", "R......")      # Floating: col0 row4 chip, but row5 empty
    b_disap   = _bc(".......", "...Y...")      # Chip disappeared: b1's RED gone; new YELLOW
    b_two     = _bc(".RRR...", "...Y...")      # Two chips: after b2, user placed col1 AND col2

    # ── near-win Minimax test: robot (YELLOW) can win ──────────────────────
    # Stand-alone: present after a reset or after b7 (game over)
    mm_block  = _bc("...RRR.", "...YYY.")     # It's robot's turn; robot YYY needs col 3 or col 6 to win
    mm_win    = _bc(".RYYYR.", "...YYY.")     # Robot has 3-in-row at row5 cols 1,2,3 — win at col 0 or 4

    return [
        ("START — board is empty (press Step to begin)",             empty_board()),
        ("VALID #1: User plays RED → col 3 (assigns colors)",        b1),
        ("ILLEGAL: Floating chip — col 0 row 4 with row 5 empty",    b_float),
        ("VALID #2: Robot plays YELLOW → col 3 (stacked)",           b2),
        ("VALID #3: User plays RED → col 1",                         b3),
        ("VALID #4: Robot plays YELLOW → col 6",                     b4),
        ("VALID #5: User RED → col 2 (3-in-a-row threat: 1,2,3)",   b5),
        ("ILLEGAL: Chip disappeared (col 3 RED vanished)",           b_disap),
        ("ILLEGAL: Two chips at once (cols 1 and 2 both new RED)",   b_two),
        ("VALID #6: Robot YELLOW → col 4 (blocks user cols 1-4)",    b6),
        ("VALID #7: User RED → col 0  *** USER WINS: 0,1,2,3 ***",  b7),
        ("MINIMAX TEST (reset state): Robot YELLOW can win at col 0 or 4", mm_block),
    ]

SIM_STATES = _build_sim_states()

class Simulator:
    def __init__(self):
        self._idx = 0

    def current(self):
        return SIM_STATES[self._idx]

    def advance(self):
        self._idx = (self._idx + 1) % len(SIM_STATES)
        return self.current()

    def reset(self):
        self._idx = 0

    @property
    def index(self): return self._idx
    @property
    def total(self):  return len(SIM_STATES)

# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 6 — CAMERA THREAD  (camera mode only; no-op in simulation)
# ═══════════════════════════════════════════════════════════════════════════════

class Camera:
    def __init__(self):
        self.raw_jpg        = None
        self.proc_jpg       = None
        self.debug_jpg      = None
        self.candidate      = None
        self.lock           = threading.Lock()
        self.connected      = False
        self.board_detected = False
        self.detection_age  = 0

    def start(self):
        if not CV2_OK:
            return
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        vp  = VisionPipeline()
        try:
            cap = cv2.VideoCapture(0)
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            if not cap.isOpened():
                print("Camera: could not open /dev/video0")
                return
            self.connected = True
            print("Camera: connected")
        except Exception as e:
            print(f"Camera: open error — {e}")
            return

        last_analysis = 0
        last_preview  = 0
        while True:
            now = time.time()
            ret, frame = cap.read()
            if not ret:
                time.sleep(0.1)
                continue
            if now - last_preview >= PREVIEW_INTERVAL:
                last_preview = now
                annotated = vp.draw_detection_overlay(frame)
                _, buf = cv2.imencode(".jpg", annotated, [cv2.IMWRITE_JPEG_QUALITY, 70])
                with self.lock:
                    self.raw_jpg = buf.tobytes()
            if now - last_analysis >= ANALYSIS_INTERVAL:
                last_analysis = now
                warped = vp.preprocess(frame)
                board, dbg = vp.detect_board(warped)
                _, b2 = cv2.imencode(".jpg", warped, [cv2.IMWRITE_JPEG_QUALITY, 80])
                _, b3 = cv2.imencode(".jpg", dbg,    [cv2.IMWRITE_JPEG_QUALITY, 80])
                with self.lock:
                    self.proc_jpg       = b2.tobytes()
                    self.debug_jpg      = b3.tobytes()
                    self.candidate      = board
                    self.board_detected = vp.board_detected
                    self.detection_age  = vp._detection_age
            time.sleep(0.02)

class RobotController:
    def __init__(self):
        self.lock = threading.Lock()
        self.mode = MODE_MANUAL
        self.ser = None
        self.connected = False
        self.busy = False
        self.awaiting_verification = False
        self.last_command = None
        self.last_sent_col = None
        self.last_sent_at = None
        self.last_done_at = None
        self.last_ack = "No serial activity yet."
        self.last_error = None
        self.state = "idle"
        self.state_msg = "Manual mode — waiting for confirmation."
        self.expected_board = None
        self.expected_col = None
        self.verify_deadline = None
        self.settle_until = None
        self._started = False

    def start(self):
        if self._started:
            return
        self._started = True
        threading.Thread(target=self._loop, daemon=True).start()

    def reset(self):
        with self.lock:
            self.busy = False
            self.awaiting_verification = False
            self.last_command = None
            self.last_sent_col = None
            self.last_sent_at = None
            self.last_done_at = None
            self.last_error = None
            self.state = "idle"
            self.state_msg = "Reset — waiting for user move."
            self.expected_board = None
            self.expected_col = None
            self.verify_deadline = None
            self.settle_until = None

    def set_mode(self, mode):
        if mode not in VALID_OPERATOR_MODES:
            return False, f"Unknown mode: {mode}"
        with self.lock:
            self.mode = mode
            if mode == MODE_MANUAL:
                self.state_msg = "Manual mode — user confirms and dispatches."
            elif mode == MODE_SEMI_AUTO:
                self.state_msg = "Semi-auto mode — clean detections auto-accept."
            else:
                self.state_msg = "Auto mode — accepting clean detections and dispatching."
        return True, f"Mode set to {mode}"

    def _ensure_serial(self):
        if not SERIAL_OK:
            self.connected = False
            self.last_error = "pyserial not installed"
            return False
        if self.ser is not None:
            self.connected = True
            return True
        try:
            self.ser = serial.Serial(SERIAL_PORT, SERIAL_BAUD, timeout=0.1)
            self.connected = True
            self.last_error = None
            self.last_ack = f"Connected on {SERIAL_PORT}"
            return True
        except Exception as e:
            self.ser = None
            self.connected = False
            self.last_error = f"Serial unavailable — {e}"
            return False

    def _read_serial(self):
        if self.ser is None:
            return
        try:
            while self.ser.in_waiting:
                line = self.ser.readline().decode("utf-8", "replace").strip()
                if not line:
                    continue
                self.last_ack = line
                if "DONE" in line or "Cycle Complete" in line:
                    self.last_done_at = time.time()
        except Exception as e:
            self.last_error = f"Serial read failed — {e}"
            self._close_serial()

    def _close_serial(self):
        if self.ser is not None:
            try:
                self.ser.close()
            except Exception:
                pass
        self.ser = None
        self.connected = False

    def dispatch_move(self, board, robot_color, col0):
        cmd = str(col0 + 1)
        expected = apply_move(board, col0, robot_color)
        if expected is None:
            return False, f"Column {col0} is not legal"
        with self.lock:
            if self.busy or self.awaiting_verification:
                return False, "Robot already busy"
            if not self._ensure_serial():
                return False, self.last_error or "Serial unavailable"
            try:
                self.ser.write(cmd.encode("ascii"))
                self.ser.flush()
            except Exception as e:
                self.last_error = f"Serial write failed — {e}"
                self._close_serial()
                return False, self.last_error
            now = time.time()
            self.busy = True
            self.awaiting_verification = True
            self.last_command = cmd
            self.last_sent_col = col0
            self.last_sent_at = now
            self.last_done_at = None
            self.expected_board = expected
            self.expected_col = col0
            self.settle_until = now + ROBOT_SETTLE_SEC
            self.verify_deadline = now + VERIFY_TIMEOUT
            self.state = "waiting_verify"
            self.state_msg = f"Command sent: column {cmd}. Waiting for board verification."
            self.last_error = None
        return True, self.state_msg

    def _can_auto_accept(self):
        with self.lock:
            mode = self.mode
        if mode == MODE_MANUAL:
            return False
        return camera.board_detected and camera.detection_age == 0

    def _maybe_accept_user_move(self):
        if APP_MODE != "camera":
            return
        if not self._can_auto_accept():
            return
        with camera.lock:
            cand = board_copy(camera.candidate) if camera.candidate is not None else None
        if cand is None:
            return
        with STATE_LOCK:
            if gs.winner or gs.is_draw or gs.whose_turn != "user":
                return
            if self.awaiting_verification or self.busy:
                return
            if boards_equal(gs.trusted, cand):
                return
            accepted, _ = gs.accept(cand)
            if accepted and gs.whose_turn == "robot":
                self._maybe_dispatch_robot_locked()

    def _maybe_dispatch_robot_locked(self):
        if APP_MODE != "camera":
            return
        if gs.whose_turn != "robot" or not gs.mm_ran or gs.mm_col is None:
            return
        with self.lock:
            if self.mode == MODE_MANUAL or self.busy or self.awaiting_verification:
                if self.mode == MODE_MANUAL:
                    self.state = "waiting_dispatch"
                    self.state_msg = "Robot move ready — dispatch manually."
                return
        ok, msg = self.dispatch_move(gs.trusted, gs.robot_color, gs.mm_col)
        gs.status_msg = msg if ok else f"Robot dispatch failed — {msg}"

    def _maybe_verify(self):
        with self.lock:
            if not self.awaiting_verification:
                return
            now = time.time()
            if self.settle_until and now < self.settle_until:
                return
            expected = board_copy(self.expected_board) if self.expected_board is not None else None
            exp_col = self.expected_col
            deadline = self.verify_deadline
        with camera.lock:
            cand = board_copy(camera.candidate) if camera.candidate is not None else None
        if cand is not None and expected is not None and boards_equal(cand, expected):
            with STATE_LOCK:
                accepted, reason = gs.accept(cand)
                if accepted:
                    with self.lock:
                        self.busy = False
                        self.awaiting_verification = False
                        self.state = "idle"
                        self.state_msg = f"Robot move verified in column {exp_col + 1}."
                        self.expected_board = None
                        self.expected_col = None
                        self.verify_deadline = None
                        self.settle_until = None
                    if gs.whose_turn == "robot":
                        self._maybe_dispatch_robot_locked()
                else:
                    with self.lock:
                        self.busy = False
                        self.awaiting_verification = False
                        self.state = "error"
                        self.state_msg = f"Verification board rejected — {reason}"
                        self.last_error = reason
            return
        if deadline and time.time() > deadline:
            with self.lock:
                self.busy = False
                self.awaiting_verification = False
                self.state = "error"
                self.state_msg = "Timed out waiting for verified robot move."
                self.last_error = self.state_msg

    def _loop(self):
        while True:
            with self.lock:
                self._ensure_serial()
                self._read_serial()
            self._maybe_accept_user_move()
            self._maybe_verify()
            time.sleep(VERIFY_POLL_SEC)

    def to_dict(self):
        with self.lock:
            return {
                "operator_mode": self.mode,
                "serial_connected": self.connected,
                "robot_busy": self.busy,
                "robot_state": self.state,
                "robot_state_msg": self.state_msg,
                "robot_last_command": self.last_command,
                "robot_last_sent_col": self.last_sent_col,
                "robot_last_sent_at": round(self.last_sent_at, 3) if self.last_sent_at else None,
                "robot_last_ack": self.last_ack,
                "robot_last_error": self.last_error,
                "robot_awaiting_verification": self.awaiting_verification,
                "robot_expected_col": self.expected_col,
            }

# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 7 — FLASK APP
# ═══════════════════════════════════════════════════════════════════════════════

app    = Flask(__name__)
gs     = GameState()
sim    = Simulator()
camera = Camera()
robot  = RobotController()
cell_classifier = CellClassifier()
APP_MODE = "simulation"   # overridden at startup
STATE_LOCK = threading.Lock()

def _sys_info():
    info = {}
    if SYSINFO_OK:
        try:
            r = subprocess.run(["/usr/bin/vcgencmd","measure_temp"],
                               capture_output=True, text=True, timeout=2)
            info["temp"] = r.stdout.strip().replace("temp=","").replace("'C"," C")
        except Exception:
            info["temp"] = "N/A"
        info["mem_mb"] = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024,1)
    return info

def _render_board_jpg(board, highlight_col=None):
    """Render board matrix as a JPEG image for MJPEG streams."""
    if not CV2_OK or board is None:
        return None
    CW, CH = 60, 60
    img = np.full((ROWS*CH + 30, COLS*CW, 3), (25, 90, 180), dtype=np.uint8)
    CHIP = {EMPTY: (12,12,20), P1: (30,30,220), P2: (20,210,200)}
    for r in range(ROWS):
        for c in range(COLS):
            cx = c*CW + CW//2;  cy = r*CH + CH//2
            cv2.circle(img, (cx,cy), CW//2-4, CHIP[board[r][c]], -1)
            cv2.circle(img, (cx,cy), CW//2-4, (0,0,0), 1)
    if highlight_col is not None and 0 <= highlight_col < COLS:
        hx = highlight_col*CW + CW//2
        cv2.arrowedLine(img,(hx,ROWS*CH+25),(hx,ROWS*CH+5),(0,255,120),2,tipLength=0.5)
    for c in range(COLS):
        cv2.putText(img, str(c), (c*CW+CW//2-5, ROWS*CH+22),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200,200,200), 1)
    _, buf = cv2.imencode(".jpg", img)
    return buf.tobytes()

def _mjpeg(getter):
    while True:
        jpg = getter()
        if jpg:
            yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpg + b"\r\n"
        time.sleep(0.25)

def _mock_stream(label):
    while True:
        if CV2_OK:
            img = np.zeros((240, 380, 3), dtype=np.uint8)
            img[:] = (18, 18, 28)
            cv2.putText(img, "SIMULATION MODE", (10,55),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.85, (255,255,255), 2)
            cv2.putText(img, label, (10,105),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.65, (140,140,140), 1)
            cv2.putText(img, "No camera connected", (10,155),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, (60,180,60), 1)
            _, buf = cv2.imencode(".jpg", img)
            yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + buf.tobytes() + b"\r\n"
        time.sleep(1)

# ── routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return HTML

@app.route("/status")
def status():
    with STATE_LOCK:
        data = gs.to_dict()
    data["mode"]          = APP_MODE
    data["sim_idx"]       = sim.index
    data["sim_total"]     = sim.total
    data["sim_desc"]      = sim.current()[0]
    data["cam_connected"]  = camera.connected
    data["board_detected"] = camera.board_detected
    data["detection_age"]  = camera.detection_age
    data.update(robot.to_dict())
    data.update(cell_classifier.to_dict())
    data.update(_sys_info())
    return jsonify(data)

@app.route("/step_sim", methods=["POST"])
def step_sim():
    if APP_MODE != "simulation":
        return jsonify({"error": "Not in simulation mode"}), 400
    desc, board = sim.advance()
    with STATE_LOCK:
        accepted, reason = gs.accept(board)
    return jsonify({
        "desc":      desc,
        "accepted":  accepted,
        "reason":    reason,
        "status":    gs.to_dict(),
        "sim_idx":   sim.index,
        "sim_total": sim.total,
    })

@app.route("/accept_camera", methods=["POST"])
def accept_camera():
    if APP_MODE != "camera":
        return jsonify({"error": "Not in camera mode"}), 400
    with camera.lock:
        cand = camera.candidate
    if cand is None:
        return jsonify({"error": "No candidate board yet"}), 400
    with STATE_LOCK:
        accepted, reason = gs.accept(cand)
        if accepted and gs.whose_turn == "robot":
            robot._maybe_dispatch_robot_locked()
    return jsonify({"accepted": accepted, "reason": reason, "status": gs.to_dict()})

@app.route("/set_mode", methods=["POST"])
def set_mode():
    payload = request.get_json(silent=True) or {}
    mode = payload.get("mode")
    ok, msg = robot.set_mode(mode)
    if not ok:
        return jsonify({"error": msg}), 400
    with STATE_LOCK:
        robot._maybe_dispatch_robot_locked()
        status_dict = gs.to_dict()
    return jsonify({"ok": True, "message": msg, "status": status_dict, "robot": robot.to_dict()})

@app.route("/set_classifier", methods=["POST"])
def set_classifier():
    payload = request.get_json(silent=True) or {}
    mode = payload.get("mode")
    if payload.get("reload"):
        cell_classifier.reload()
    ok, msg = cell_classifier.set_mode(mode)
    if not ok:
        return jsonify({"error": msg}), 400
    return jsonify({"ok": True, "message": msg, "classifier": cell_classifier.to_dict()})

@app.route("/robot_move", methods=["POST"])
def robot_move():
    with STATE_LOCK:
        if gs.whose_turn != "robot" or gs.robot_color is None or gs.mm_col is None:
            return jsonify({"error": "Robot move is not ready"}), 400
        ok, msg = robot.dispatch_move(gs.trusted, gs.robot_color, gs.mm_col)
        if ok:
            gs.status_msg = msg
        status_dict = gs.to_dict()
    if not ok:
        return jsonify({"error": msg, "status": status_dict}), 400
    return jsonify({"ok": True, "message": msg, "status": status_dict, "robot": robot.to_dict()})

@app.route("/reset", methods=["POST"])
def reset_game():
    with STATE_LOCK:
        gs.reset()
        sim.reset()
        robot.reset()
    return jsonify({"ok": True})

@app.route("/raw")
def raw():
    if APP_MODE == "camera":
        return Response(_mjpeg(lambda: camera.raw_jpg),
                        mimetype="multipart/x-mixed-replace; boundary=frame")
    return Response(_mock_stream("Raw Camera"), mimetype="multipart/x-mixed-replace; boundary=frame")

@app.route("/processed")
def processed():
    if APP_MODE == "camera":
        return Response(_mjpeg(lambda: camera.proc_jpg),
                        mimetype="multipart/x-mixed-replace; boundary=frame")
    return Response(
        _mjpeg(lambda: _render_board_jpg(gs.trusted, gs.mm_col if gs.mm_ran else None)),
        mimetype="multipart/x-mixed-replace; boundary=frame")

@app.route("/debug_grid")
def debug_grid():
    if APP_MODE == "camera":
        return Response(_mjpeg(lambda: camera.debug_jpg),
                        mimetype="multipart/x-mixed-replace; boundary=frame")
    cand = gs.candidate if gs.candidate is not None else gs.trusted
    return Response(_mjpeg(lambda: _render_board_jpg(cand, None)),
                    mimetype="multipart/x-mixed-replace; boundary=frame")

# ═══════════════════════════════════════════════════════════════════════════════
# HTML DASHBOARD
# ═══════════════════════════════════════════════════════════════════════════════

HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Connect 4 Brain Dashboard</title>
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
body{background:#0e0e14;color:#d0d0e0;font-family:'Segoe UI',system-ui,sans-serif;font-size:13px}
header{background:#12121c;padding:12px 18px;border-bottom:1px solid #252535;display:flex;align-items:center;gap:16px;flex-wrap:wrap}
header h1{font-size:1.1rem;color:#fff;letter-spacing:1px}
#mode-badge{padding:3px 10px;border-radius:4px;font-size:.72rem;font-weight:bold;letter-spacing:1px}
.mode-sim{background:#0d2a0d;color:#50b850;border:1px solid #256025}
.mode-cam{background:#0d1e30;color:#5090d0;border:1px solid #205080}
#sysinfo{font-size:.72rem;color:#555;margin-left:auto}
#status-bar{padding:6px 18px;background:#0a0a12;border-bottom:1px solid #1e1e2e;font-size:.75rem;color:#888}
.grid{display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px;padding:10px}
@media(max-width:860px){.grid{grid-template-columns:1fr 1fr}}
@media(max-width:540px){.grid{grid-template-columns:1fr}}
.card{background:#12121c;border:1px solid #222234;border-radius:5px;padding:10px}
.card h2{font-size:.68rem;text-transform:uppercase;letter-spacing:1.5px;color:#555;margin-bottom:8px;border-bottom:1px solid #1c1c2c;padding-bottom:5px}
.info-row{display:flex;justify-content:space-between;padding:3px 0;border-bottom:1px solid #18182a;font-size:.8rem}
.info-row:last-child{border-bottom:none}
.lbl{color:#777}
.val{color:#d0d0f0;font-weight:bold;text-align:right}
.val-red{color:#f06060;font-weight:bold}
.val-yel{color:#e0d040;font-weight:bold}
.val-ok{color:#50c050}
.val-err{color:#f06060}
.val-mm{color:#00e0b0;font-weight:bold}
#status-msg{padding:7px 10px;border-radius:4px;margin-bottom:8px;font-size:.82rem;font-weight:bold;word-break:break-word;line-height:1.4}
.ok{background:#072007;color:#70e870;border-left:3px solid #30a030}
.err{background:#200707;color:#f08080;border-left:3px solid #a03030}
.inf{background:#07121e;color:#70b0e0;border-left:3px solid #306090}
#reject-box{display:none;padding:6px 9px;background:#1e0505;border-radius:4px;margin-bottom:8px;font-size:.77rem;color:#f08080;word-break:break-word;border-left:3px solid #800}
table.board{border-collapse:collapse;margin-bottom:4px}
table.board td{width:40px;height:40px;background:#133080;padding:3px;border:1px solid #092050}
.chip{width:34px;height:34px;border-radius:50%;border:1px solid rgba(0,0,0,.5)}
.ce{background:#080810}
.c1{background:radial-gradient(circle at 35% 35%,#f06060,#b80000)}
.c2{background:radial-gradient(circle at 35% 35%,#f0e040,#c09000)}
.col-hdr{display:flex;margin-bottom:2px}
.col-hdr div{width:40px;text-align:center;font-size:.68rem;color:#555}
.col-hdr div.leg{color:#50b850}
.col-hdr div.mm-c{color:#00e0b0;font-size:.85rem}
.stream-box img{width:100%;border-radius:4px;border:1px solid #202030;background:#08080e;display:block;margin-bottom:6px}
.btn{padding:7px 16px;border-radius:4px;border:none;cursor:pointer;font-size:.8rem;font-weight:bold;letter-spacing:.5px;margin:3px 3px 3px 0}
.b-step{background:#123012;color:#70f070;border:1px solid #207020}
.b-step:hover{background:#1e4a1e}
.b-acc{background:#102030;color:#70b0f0;border:1px solid #205090}
.b-acc:hover{background:#1a3a60}
.b-mode{background:#1a1a2a;color:#d0d0f0;border:1px solid #404070}
.b-mode:hover{background:#262640}
.b-go{background:#103018;color:#80f0a0;border:1px solid #207040}
.b-go:hover{background:#1a4824}
.b-rst{background:#301010;color:#f07070;border:1px solid #802020}
.b-rst:hover{background:#501818}
.sim-desc{padding:5px 8px;background:#08180a;border:1px solid #164016;border-radius:4px;font-size:.75rem;color:#80c880;margin-bottom:6px;line-height:1.4}
.sec-lbl{font-size:.65rem;text-transform:uppercase;color:#444;letter-spacing:1px;margin:8px 0 3px}
.mm-box{margin-top:6px;padding:6px 8px;background:#021410;border-radius:4px;font-size:.77rem;color:#30c09a;min-height:36px;line-height:1.5;word-break:break-word}
</style>
</head>
<body>
<header>
  <h1>Connect 4 Brain Dashboard</h1>
  <span id="mode-badge">?</span>
  <span id="turn-badge" style="font-size:.82rem;padding:3px 9px;background:#18182a;border-radius:4px;border:1px solid #282840"></span>
  <span id="sysinfo"></span>
</header>
<div id="status-bar">Connecting...</div>

<div class="grid">

  <!-- COL 1: streams -->
  <div>
    <div class="card">
      <h2>Camera / Board Images</h2>
      <div class="stream-box">
        <div class="sec-lbl">Raw feed</div>
        <img src="/raw" alt="raw">
        <div class="sec-lbl">Processed / warped board</div>
        <img src="/processed" alt="processed">
        <div class="sec-lbl">Cell grid detection</div>
        <img src="/debug_grid" alt="grid">
      </div>
    </div>
  </div>

  <!-- COL 2: boards -->
  <div>
    <div class="card">
      <h2>Board States</h2>
      <div class="sec-lbl">Trusted board (accepted moves)</div>
      <div id="trusted-board"></div>
      <div class="sec-lbl">Previous accepted board</div>
      <div id="prev-board"></div>
      <div class="sec-lbl">Candidate (latest detected)</div>
      <div id="cand-board"></div>
    </div>
  </div>

  <!-- COL 3: brain + controls -->
  <div>
    <div class="card" style="margin-bottom:10px">
      <h2>Game Status</h2>
      <div id="status-msg" class="inf">Loading...</div>
      <div id="reject-box"></div>
      <div class="info-row"><span class="lbl">User color</span><span id="usr-clr" class="val">—</span></div>
      <div class="info-row"><span class="lbl">Robot color</span><span id="bot-clr" class="val">—</span></div>
      <div class="info-row"><span class="lbl">Whose turn</span><span id="w-turn" class="val">—</span></div>
      <div class="info-row"><span class="lbl">Move count</span><span id="mv-cnt" class="val">0</span></div>
      <div class="info-row"><span class="lbl">User chips</span><span id="usr-chp" class="val">0</span></div>
      <div class="info-row"><span class="lbl">Robot chips</span><span id="bot-chp" class="val">0</span></div>
      <div class="info-row"><span class="lbl">Winner</span><span id="winner" class="val">—</span></div>
      <div class="info-row"><span class="lbl">Draw</span><span id="is-draw" class="val">No</span></div>
      <div class="info-row"><span class="lbl">Last move col</span><span id="last-col" class="val">—</span></div>
      <div class="info-row"><span class="lbl">Legal columns</span><span id="legal" class="val">—</span></div>
      <div class="info-row"><span class="lbl">Operator mode</span><span id="op-mode" class="val">—</span></div>
      <div class="info-row"><span class="lbl">Robot state</span><span id="rb-state" class="val">—</span></div>
      <div class="info-row"><span class="lbl">Last serial cmd</span><span id="rb-cmd" class="val">—</span></div>
      <div class="info-row"><span class="lbl">Serial</span><span id="ser-ok" class="val">—</span></div>
      <div class="info-row"><span class="lbl">Classifier</span><span id="clf-mode" class="val">—</span></div>
      <div class="info-row"><span class="lbl">ML model</span><span id="clf-ok" class="val">—</span></div>
    </div>

    <div class="card" style="margin-bottom:10px">
      <h2>Minimax Decision</h2>
      <div class="info-row"><span class="lbl">Best column</span><span id="mm-col" class="val-mm">—</span></div>
      <div class="info-row"><span class="lbl">Score</span><span id="mm-score" class="val">—</span></div>
      <div class="info-row"><span class="lbl">Compute time</span><span id="mm-time" class="val">—</span></div>
      <div class="mm-box" id="mm-reason">—</div>
    </div>

    <div class="card">
      <h2>Controls</h2>
      <div style="margin-bottom:8px">
        <button class="btn b-mode" onclick="setMode('manual')">Manual</button>
        <button class="btn b-mode" onclick="setMode('semi_auto')">Semi-auto</button>
        <button class="btn b-mode" onclick="setMode('auto')">Auto</button>
      </div>
      <div style="margin-bottom:8px">
        <button class="btn b-mode" onclick="setClassifier('hsv')">HSV</button>
        <button class="btn b-mode" onclick="setClassifier('auto')">ML Auto</button>
        <button class="btn b-mode" onclick="setClassifier('ml')">ML Only</button>
      </div>
      <div id="sim-ctrl" style="display:none">
        <div class="sim-desc" id="sim-desc">—</div>
        <div style="font-size:.72rem;color:#444;margin-bottom:5px" id="sim-prog">Step 0/0</div>
        <button class="btn b-step" onclick="stepSim()">&#9654; Next Sim Step</button>
      </div>
      <div id="cam-ctrl" style="display:none">
        <button class="btn b-acc" onclick="acceptCam()">&#10003; Accept Camera Board</button>
        <button class="btn b-go" onclick="runRobot()">Send Robot Move</button>
      </div>
      <button class="btn b-rst" onclick="resetGame()">&#8635; Reset Game</button>
    </div>
  </div>

</div>

<script>
function makeBoard(board, legalCols, mmCol) {
  if (!board) return '<span style="color:#444">No data</span>';
  let h = '<div class="col-hdr">';
  for (let c=0;c<7;c++){
    let cls='', txt=String(c);
    if (mmCol!==null && mmCol===c){cls='mm-c'; txt='&#9660;';}
    else if (legalCols && legalCols.includes(c)) cls='leg';
    h+=`<div class="${cls}">${txt}</div>`;
  }
  h+='</div><table class="board">';
  for (let r=0;r<6;r++){
    h+='<tr>';
    for(let c=0;c<7;c++){
      const v=board[r][c];
      h+=`<td><div class="chip ${v===1?'c1':v===2?'c2':'ce'}"></div></td>`;
    }
    h+='</tr>';
  }
  return h+'</table>';
}
function clrSpan(n){
  if(n==='RED')    return '<span class="val-red">RED</span>';
  if(n==='YELLOW') return '<span class="val-yel">YELLOW</span>';
  return '<span style="color:#555">None</span>';
}
function update(d){
  const badge=document.getElementById('mode-badge');
  badge.textContent=(d.mode||'?').toUpperCase();
  badge.className=d.mode==='camera'?'mode-cam':'mode-sim';
  document.getElementById('sim-ctrl').style.display=d.mode==='simulation'?'block':'none';
  document.getElementById('cam-ctrl').style.display=d.mode==='camera'?'block':'none';
  if(d.sim_desc) document.getElementById('sim-desc').textContent=d.sim_desc;
  if(d.sim_total!==undefined)
    document.getElementById('sim-prog').textContent=`Step ${d.sim_idx}/${d.sim_total-1}`;
  const msg=document.getElementById('status-msg');
  msg.textContent=d.status_msg||'—';
  msg.className=d.winner||d.is_draw?'ok':d.reject_reason?'err':'inf';
  const rb=document.getElementById('reject-box');
  if(d.reject_reason){rb.style.display='block';rb.textContent='\u2717 '+d.reject_reason;}
  else rb.style.display='none';
  const mmC=d.mm_ran?d.mm_col:null;
  const legal=d.legal_cols||[];
  document.getElementById('trusted-board').innerHTML=makeBoard(d.trusted,legal,mmC);
  document.getElementById('prev-board').innerHTML=makeBoard(d.previous,null,null);
  document.getElementById('cand-board').innerHTML=makeBoard(d.candidate,null,null);
  document.getElementById('usr-clr').innerHTML=clrSpan(d.user_color);
  document.getElementById('bot-clr').innerHTML=clrSpan(d.robot_color);
  document.getElementById('w-turn').textContent=d.whose_turn||'Game over';
  document.getElementById('mv-cnt').textContent=d.move_count;
  document.getElementById('usr-chp').textContent=d.user_chips;
  document.getElementById('bot-chp').textContent=d.robot_chips;
  document.getElementById('winner').textContent=d.winner||'None';
  document.getElementById('is-draw').textContent=d.is_draw?'YES':'No';
  document.getElementById('last-col').textContent=d.last_col!==null?d.last_col:'—';
  document.getElementById('legal').textContent=legal.join(', ')||'—';
  document.getElementById('op-mode').textContent=d.operator_mode||'—';
  document.getElementById('rb-state').textContent=d.robot_state_msg||d.robot_state||'—';
  document.getElementById('rb-cmd').textContent=d.robot_last_command||'—';
  document.getElementById('ser-ok').textContent=d.serial_connected?'CONNECTED':'DISCONNECTED';
  document.getElementById('clf-mode').textContent=d.classifier_mode||'—';
  document.getElementById('clf-ok').textContent=d.classifier_loaded?'LOADED':'NOT LOADED';
  const tb=document.getElementById('turn-badge');
  if(d.winner){tb.textContent='[WIN] '+d.winner+' wins!';tb.style.color='#50e050';}
  else if(d.is_draw){tb.textContent='[DRAW]';tb.style.color='#e0a040';}
  else{tb.textContent='Turn: '+(d.whose_turn||'—');tb.style.color='#9090b0';}
  document.getElementById('mm-col').textContent=d.mm_ran?'Column '+d.mm_col:'—';
  document.getElementById('mm-score').textContent=d.mm_ran?d.mm_score:'—';
  document.getElementById('mm-time').textContent=d.mm_ran?d.mm_time+'s':'—';
  document.getElementById('mm-reason').textContent=d.mm_reason||'—';
  const si=[];
  if(d.temp) si.push('Temp: '+d.temp);
  if(d.mem_mb) si.push('Mem: '+d.mem_mb+' MB');
  si.push('Mode: '+(d.mode||'?'));
  si.push('Robot: '+(d.robot_state||'idle'));
  document.getElementById('sysinfo').textContent=si.join('  |  ');
  document.getElementById('status-bar').textContent=
    'Last update: '+new Date().toLocaleTimeString()+
    '  |  Moves: '+d.move_count+
    '  |  Cam: '+(d.cam_connected?'YES':'NO')+
    '  |  Board: '+(d.board_detected?'AUTO':'FALLBACK age='+(d.detection_age||0))+
    '  |  Sim step: '+(d.sim_idx!==undefined?d.sim_idx+'/'+d.sim_total:'—');
}
function poll(){fetch('/status').then(r=>r.json()).then(update).catch(e=>{
  document.getElementById('status-bar').textContent='Error: '+e;
});}
function stepSim(){fetch('/step_sim',{method:'POST'}).then(r=>r.json()).then(d=>{
  if(d.status) update(d.status);
  const msg=document.getElementById('status-msg');
  msg.textContent=(d.accepted?'\u2713 ':'\u2717 ')+d.reason;
  msg.className=d.accepted?'ok':'err';
});}
function acceptCam(){fetch('/accept_camera',{method:'POST'}).then(r=>r.json()).then(d=>{
  if(d.error) alert(d.error); else if(d.status) update(d.status);
});}
function runRobot(){fetch('/robot_move',{method:'POST'}).then(r=>r.json()).then(d=>{
  if(d.error) alert(d.error); else if(d.status) update(d.status);
});}
function setMode(mode){
  fetch('/set_mode',{
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({mode})
  }).then(r=>r.json()).then(d=>{
    if(d.error) alert(d.error);
    poll();
  });
}
function setClassifier(mode){
  fetch('/set_classifier',{
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({mode})
  }).then(r=>r.json()).then(d=>{
    if(d.error) alert(d.error);
    poll();
  });
}
function resetGame(){if(!confirm('Reset game?'))return;
  fetch('/reset',{method:'POST'}).then(()=>poll());}
poll();
setInterval(poll,2000);
</script>
</body>
</html>"""

# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Connect 4 Brain Dashboard")
    parser.add_argument("--sim",   action="store_true", help="Simulation mode (no camera)")
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--port",  type=int, default=5000)
    parser.add_argument("--depth", type=int, default=MINIMAX_DEPTH)
    args = parser.parse_args()

    MINIMAX_DEPTH = args.depth
    APP_MODE = "simulation" if args.sim else "camera"

    print("=== Connect 4 Brain Dashboard ===")
    print(f"Mode      : {APP_MODE}")
    print(f"Minimax   : depth {MINIMAX_DEPTH}")
    print(f"CV2       : {'OK' if CV2_OK else 'NOT AVAILABLE'}")
    print(f"Classifier: {CELL_CLASSIFIER_MODE} ({'loaded' if cell_classifier.loaded else 'fallback'})")

    if APP_MODE == "camera":
        if not CV2_OK:
            print("WARNING: OpenCV not available — switching to simulation mode")
            APP_MODE = "simulation"
        else:
            camera.start()
            robot.start()
            print("Camera    : starting (1 FPS analysis, 5 FPS preview)")
    else:
        print(f"Sim states: {sim.total} steps loaded")

    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
    except Exception:
        ip = "192.168.100.40"

    print(f"Dashboard : http://{ip}:{args.port}")
    print(f"Serial    : {SERIAL_PORT} @ {SERIAL_BAUD}")
    print()

    app.run(host="0.0.0.0", port=args.port, threaded=True)
