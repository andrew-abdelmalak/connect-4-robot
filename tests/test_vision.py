"""Vision pipeline on the repository's real board photo."""

import cv2
import pytest
from conftest import REPO

from connect4_robot.board import board_from_rows as _bc
from connect4_robot.vision import (
    CellClassifier,
    VisionPipeline,
    extract_cell_features,
    load_corners,
    warp_and_condition,
)

PHOTO = REPO / "notebooks" / "board_sample.png"
# Ground truth read by eye from the photo (bottom row first).
TRUTH = _bc(".YRRYY.", "..YRY..", "..RYY..", "...R...")


@pytest.fixture(scope="module")
def photo():
    img = cv2.imread(str(PHOTO))
    assert img is not None
    return img


@pytest.fixture
def vp():
    return VisionPipeline(CellClassifier())


def test_hsv_reads_every_cell_with_grid_corners(photo, vp):
    """With corners on the 6x7 grid (the notebook's corners), all 42 cells are right."""
    board, _ = vp.detect_board(warp_and_condition(photo, load_corners(photo.shape)))
    assert board == TRUTH


def test_auto_detection_finds_the_blue_board(photo, vp):
    vp.preprocess(photo)
    assert vp.board_detected
    assert vp._src.shape == (4, 2)


def test_cell_features_have_18_values(photo):
    feats = extract_cell_features(photo[100:200, 100:200])
    assert feats.shape == (18,)
    assert extract_cell_features(None).shape == (18,)


class FakeModel:
    """Always predicts class 2 (yellow) with a fixed confidence."""

    def __init__(self, confidence):
        self.confidence = confidence

    def predict(self, feats):
        return [2]

    def predict_proba(self, feats):
        return [[0.0, 1 - self.confidence, self.confidence]]


@pytest.mark.parametrize(
    ("mode", "confidence", "expected"),
    [
        ("hsv", 0.99, "hsv"),  # HSV mode never asks the model
        ("auto", 0.90, "model"),  # confident model wins
        ("auto", 0.50, "hsv"),  # below 0.65 falls back to HSV
        ("ml", 0.10, "model"),  # ML mode trusts the model at any confidence
    ],
)
def test_classifier_modes(photo, mode, confidence, expected):
    clf = CellClassifier()
    clf.model, clf.labels, clf.loaded = FakeModel(confidence), {0: 0, 1: 1, 2: 2}, True
    clf.set_mode(mode)
    vp = VisionPipeline(clf)
    red_cell = warp_and_condition(photo, load_corners(photo.shape))[
        2 * 133 + 33 : 3 * 133 - 33, 3 * 114 + 28 : 4 * 114 - 28
    ]  # row 2, col 3 holds a red chip
    assert vp.classify_cell(red_cell) == (2 if expected == "model" else 1)
