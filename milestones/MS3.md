# MS3 Summary

MS3 turned the project from a preprocessing prototype into a closed-loop robot demo. The milestone added board-state extraction, Minimax-based move selection, and physical dispenser control driven by the camera-derived game state.

## What Was Added

- HSV-based token detection on the warped board image.
- Morphological cleanup to stabilize cell classification.
- Conversion of the board image into a verified `6 x 7` game-state matrix.
- Minimax with alpha-beta pruning for column selection.
- Serial handoff from Raspberry Pi to Arduino for physical actuation.
- Arduino firmware for column-based token dispensing and return-to-home behavior.

## What Was Demonstrated

- Camera-based perception feeding the decision layer instead of manual control.
- Selection of a legal next move from the extracted board state.
- Physical motion of the dispensing mechanism to the selected column.
- End-to-end milestone integration across sensing, computation, and actuation.

## Known Limitations

- Glare can interfere with HSV thresholding under unfavorable lighting.
- Raspberry Pi search depth must stay limited to keep turn times reasonable.
- Full-board analysis rate is low for highly responsive play.
- Motor acceleration and timing still need tuning for smoother motion.

## Public Artifact Status

Published in this repository:

- Arduino dispenser firmware.
- Cumulative paper source through MS3.

Not yet published in this repository:

- Final Raspberry Pi Python/OpenCV/AI source files used during the milestone demonstration.

Those files should be added once the source set is recovered and prepared for public release.
