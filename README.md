# Vision-Based Connect-4 Robot

A mechatronics project implementing an autonomous Connect-4 playing robot.
The system uses computer vision on a Raspberry Pi 4 to process the board state,
extracts image features to drive an Arduino-controlled actuation stage, and
physically dispenses tokens into the selected column.

**Status: Milestone 2 complete.**
The image processing pipeline is implemented and validated on both a Raspberry Pi 4
and a laptop. The game AI, HSV color segmentation, and full integration are planned
for subsequent milestones.

## Visual Highlights

<p align="center">
    <img src="assets/figures/m2_perspective_warp.png" alt="Perspective warp pipeline for board isolation" width="760"/>
</p>
<p align="center"><em>Figure 1. Perspective-warp stage used to isolate the Connect-4 board before downstream feature extraction and actuation mapping.</em></p>

<p align="center">
    <img src="assets/figures/m1_proteus_circuit.png" alt="Proteus circuit schematic for Raspberry Pi and Arduino integration" width="760"/>
</p>
<p align="center"><em>Figure 2. Proteus circuit schematic showing the Raspberry Pi, Arduino Uno, L298N drivers, and actuator wiring used in the milestone hardware setup.</em></p>

---

## Team

| Name | Student ID | Email |
|------|-----------|-------|
| Andrew Abdelmalak | 55-22771 | andrew.abdelmalak@student.guc.edu.eg |
| Daniel Boules | 55-5055 | daniel.boules@student.guc.edu.eg |
| David Girgis | 55-1481 | david.girgis@student.guc.edu.eg |
| Kirolous Kirolous | 55-18081 | kirolous.kirolous@student.guc.edu.eg |
| Samir Yacoub | 55-25111 | samir.yacoub@student.guc.edu.eg |
| Youssef Salama | 55-0540 | youssef.salama@student.guc.edu.eg |

**Affiliation**: Department of Mechatronics Engineering, German University in Cairo (GUC)

---

## System Overview

```
[Pi Camera V2]
      |
      v
[Raspberry Pi 4 (4 GB)]  ← Python / OpenCV pipeline
      | USB Serial (9600 baud)
      v
[Arduino Uno]
    |           |
    v           v
[L298N x2]   [SG90 Servo]
    |               |
[NEMA 17 x2]   [Column Gate]
(token pusher)  (column select)
```

---

## Repository Structure

```
connect-4-robot/
├── src/
│   └── image_pipeline/
│       ├── m1_image_acquisition.py          # ML1: Webcam image capture
│       └── m2_member1_pipeline_notebook.ipynb   # ML2: Full processing pipeline
├── arduino/
│   └── m1_servo_gate_controller/
│       └── m1_servo_gate_controller.ino           # Serial-controlled servo sketch
├── assets/
│   └── figures/
│       ├── m1_proteus_circuit.png      # Proteus circuit diagram
│       ├── m2_input_board.png             # Input board image
│       ├── m2_rotation_comparison.png             # Rotation comparison
│       ├── m2_perspective_warp.png     # Perspective warp comparison
│       ├── m2_brightness_adjustment.png           # Brightness adjustment
│       ├── m2_contrast_scaling.png             # Contrast scaling
│       └── m2_smoothing_comparison.png            # Smoothing comparison
├── README.md
├── .gitignore
└── LICENSE
```

---

## Hardware Bill of Materials

| Component | Qty | Total (EGP) |
|-----------|-----|-------------|
| Raspberry Pi 4 (4 GB) | 1 | 5,950 |
| Arduino Uno | 1 | 450 |
| Pi Camera V2 | 1 | 2,150 |
| NEMA 17 stepper motor | 2 | 880 |
| L298N H-bridge driver | 2 | 170 |
| SG90 micro-servo | 1 | 85 |
| 12V 5A power supply | 1 | 300 |
| Wires & breadboard | 1 | 150 |
| **Total** | | **10,135 EGP** |

---

## Image Processing Pipeline

Implemented in `src/image_pipeline/m2_member1_pipeline_notebook.ipynb`:

1. **Load & display** — Load `Image.png`, convert BGR→RGB, display.
2. **Rotation** — `cv2.getRotationMatrix2D` + `cv2.warpAffine` at 15°. Bilinear interpolation selected over nearest-neighbor.
3. **Perspective warp** — 4-corner homography to 800×800 top-down view with `cv2.getPerspectiveTransform`.
4. **Brightness** — ±60 offset; histograms verify distribution shift.
5. **Contrast** — `cv2.convertScaleAbs` at α=1.8 (high) and α=0.5 (low).
6. **Smoothing** — Gaussian 3×3, 5×5, 7×7 and box filter; 5×5 Gaussian selected.
7. **Feature extraction**:
   - Feature 1: mean board brightness → PWM duty cycle (%)
   - Feature 2: board rotation angle → motor direction (CW/CCW)
8. **Serial output**: two scalar bytes transmitted to Arduino at 9600 baud.

---

## Benchmarks (100-Iteration Mean)

| Operation | Raspberry Pi 4 (ms) | Laptop (ms) |
|-----------|---------------------|-------------|
| Rotation (bilinear) | 8.468 | 3.273 |
| Perspective warp | 3.596 | 1.768 |
| Brightness +60 | 0.734 | 0.888 |
| Contrast α=1.8 | 1.111 | 0.201 |
| Gaussian blur 5×5 | 0.757 | 0.097 |

Output quality is bit-for-bit identical on both platforms.

---

## Mechanical Design

CAD modeled in SolidWorks (`.SLDPRT` / `.SLDASM` files).
Assembly: token magazine → pusher disk (DC motor) → slide channel → servo gate → column entry.
SolidWorks files are not included in this repository (require SolidWorks to open).

---

## Known Limitations

1. **Single-image validation**: Pipeline validated on one board image only.
2. **No color segmentation**: HSV-based token classification not yet implemented.
3. **No game AI**: Minimax with alpha-beta pruning not yet implemented.
4. **Partial actuation firmware**: Full NEMA 17 stepper control firmware not found.
5. **Lighting sensitivity**: Fixed pipeline parameters may require retuning under different lighting.

---

## License

This repository is licensed under the MIT License. See `LICENSE`.
