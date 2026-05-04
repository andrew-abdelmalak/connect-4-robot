# Milestone Summary

The integrated milestone state turned the project from a preprocessing prototype into a closed-loop robot demo. The system now includes board-state extraction, Minimax-based move selection, serial actuation dispatch, post-move camera verification, and an optional lightweight ML bonus path for token classification.

## What Was Added

- HSV-based token detection on the warped board image.
- Morphological cleanup to stabilize cell classification.
- Conversion of the board image into a verified `6 x 7` game-state matrix.
- Minimax with alpha-beta pruning for column selection.
- Serial handoff from Raspberry Pi to Arduino for physical actuation.
- Arduino firmware for column-based token dispensing, carriage motion, and return-to-home behavior.
- Dashboard operator modes: `manual`, `semi_auto`, and `auto`.
- Optional lightweight ML training and inference path for the milestone bonus.

## What Was Demonstrated

- Camera-based perception feeding the decision layer instead of manual control.
- Selection of a legal next move from the extracted board state.
- Physical motion of the dispensing mechanism to the selected column.
- Post-actuation camera verification that the robot token appeared in the commanded column.
- End-to-end milestone integration across sensing, computation, decision making, and actuation.

## Known Limitations

- Glare can interfere with HSV thresholding under unfavorable lighting.
- The ML bonus path needs labeled cell crops before it can be enabled reliably.
- Raspberry Pi search depth must stay limited to keep turn times reasonable.
- Full-board analysis rate is low for highly responsive play.
- Motor timing, encoder stop target, and return travel still need tuning for smoother motion.

## Public Artifact Status

Published in this repository:

- Arduino dispenser firmware.
- Cumulative paper source for the final integrated milestone state.
- Raspberry Pi runtime and ML training source files.
