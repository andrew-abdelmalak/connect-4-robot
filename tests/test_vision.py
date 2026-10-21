"""Vision pipeline on the repository's real board photo."""

import cv2
import pytest
from conftest import REPO

from connect4_robot.board import board_from_rows as _bc
from connect4_robot.vision import CellClassifier, VisionPipeline, extract_cell_features, warp_and_condition

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
    vp._src = vp._load_corners(photo.shape)
    board, _ = vp.detect_board(warp_and_condition(photo, vp._src))
    assert board == TRUTH


def test_auto_detection_finds_the_blue_board(photo, vp):
    vp.preprocess(photo)
    assert vp.board_detected
    assert vp._src.shape == (4, 2)


def test_cell_features_have_18_values(photo):
    feats = extract_cell_features(photo[100:200, 100:200])
    assert feats.shape == (18,)
    assert extract_cell_features(None).shape == (18,)
