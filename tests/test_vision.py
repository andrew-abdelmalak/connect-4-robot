"""Vision pipeline on the repository's real board photo."""

import cv2
import numpy as np
import pytest
from conftest import REPO
from connect4_brain import WARP_SIZE, VisionPipeline, _bc, extract_cell_features

PHOTO = REPO / "src" / "image_pipeline" / "Image.png"
# Ground truth read by eye from the photo (bottom row first).
TRUTH = _bc(".YRRYY.", "..YRY..", "..RYY..", "...R...")


@pytest.fixture(scope="module")
def photo():
    img = cv2.imread(str(PHOTO))
    assert img is not None
    return img


def test_hsv_reads_every_cell_with_grid_corners(photo):
    """With corners on the 6x7 grid (the notebook's corners), all 42 cells are right."""
    vp = VisionPipeline()
    vp._src = vp._load_corners(photo.shape)
    dst = np.float32([[0, 0], [WARP_SIZE, 0], [WARP_SIZE, WARP_SIZE], [0, WARP_SIZE]])
    warped = cv2.warpPerspective(
        photo, cv2.getPerspectiveTransform(vp._src, dst), (WARP_SIZE, WARP_SIZE)
    )
    warped = cv2.convertScaleAbs(warped, alpha=1.0, beta=20)
    warped = cv2.convertScaleAbs(warped, alpha=1.3, beta=0)
    warped = cv2.GaussianBlur(warped, (5, 5), 0)
    board, _ = vp.detect_board(warped)
    assert board == TRUTH


def test_auto_detection_finds_the_blue_board(photo):
    vp = VisionPipeline()
    vp.preprocess(photo)
    assert vp.board_detected
    assert vp._src.shape == (4, 2)


def test_cell_features_have_18_values(photo):
    feats = extract_cell_features(photo[100:200, 100:200])
    assert feats.shape == (18,)
    assert extract_cell_features(None).shape == (18,)
