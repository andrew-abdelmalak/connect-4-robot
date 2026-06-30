# Vision-Based Connect-4 Robot

![MIT License](https://img.shields.io/badge/License-MIT-green) ![Python](https://img.shields.io/badge/Python-3.9-blue) ![OpenCV](https://img.shields.io/badge/OpenCV-4.8-orange)

**MCTR 1010 — Image Processing for Mechatronics · German University in Cairo · Spring 2026**

**Team 21** | Andrew Abdelmalak · Daniel Boules · David Girgis · Kirolous Kirolous · Samir Yacoub · Youssef Salama

> **Paper** — IEEE conference paper: [`paper/main.tex`](paper/main.tex) | [`paper/references.bib`](paper/references.bib)

A Raspberry Pi 4 + Arduino Uno robot that plays Connect-4 autonomously: a Pi Camera V2 captures the board, an OpenCV pipeline extracts the game state, a minimax search picks the optimal move, and an Arduino-driven dispenser drops the token — then the camera re-verifies the move.

<p align="center">
  <img src="paper/figures/m2_perspective_warp.png" width="400" alt="Perspective-warped Connect-4 board">
</p>
<p align="center"><em>Perspective-warped 800×800 board: rectified grid ready for cell segmentation.</em></p>

---

## Table of Contents

[Overview](#overview) · [Architecture](#system-architecture) · [Demo](#demo) · [Pipeline](#pipeline) · [Parameters](#key-parameters) · [Key Results](#key-results) · [BOM](#bill-of-materials) · [Structure](#repository-structure) · [Usage](#usage) · [Equations](#key-equations) · [Authors](#authors) · [License](#license)

---

## Overview

This project implements an autonomous Connect-4 playing robot on commodity embedded hardware. The robot uses a Raspberry Pi 4 with a Pi Camera V2 for vision, a Python/OpenCV pipeline for game-state extraction, a minimax algorithm for decision-making, and an Arduino-controlled motorized dispenser for physical token placement. The complete perception-to-actuation loop runs at a total hardware cost of 6,100 EGP (~200 USD).

Connect-4 provides a compact yet demanding testbed for integrating computer vision, real-time decision-making, and robotic actuation within a single closed loop. While individual components—board detection, color segmentation, game AI—are well-studied in isolation, comprehensive open implementations that link all stages with trusted-state validation and physical verification remain scarce. This project fills that gap with a reproducible, documented, and experimentally evaluated system.

---

## Demo

The robot autonomously plays Connect-4 against a human opponent. On each turn:

1. The camera captures an overhead image of the board.
2. The vision pipeline extracts a 6×7 game-state matrix.
3. A trusted-state validator checks the board against six physical legality rules.
4. The minimax search (depth 5, alpha-beta pruning) selects the optimal column.
5. The column index is transmitted over serial to the Arduino at 9600 baud.
6. The Arduino drives a TT carriage motor to position the dispenser and an encoder-coupled magazine motor to release one token.
7. The camera re-reads the board to confirm the move was executed correctly.

If verification succeeds, the turn passes to the human player. If verification fails (token jam, misalignment, timeout), the system enters an error state and alerts the operator.

---

## System Architecture

```
  ┌─────────┐    ┌─────────┐    ┌─────────┐    ┌─────────┐
  │  Camera │───▶│  Vision │───▶│Validate │───▶│ Minimax │
  │ Pi Cam  │    │ OpenCV  │    │ 6 rules │    │ Depth 5 │
  └─────────┘    └─────────┘    └─────────┘    └─────────┘
                                                     │
  ┌─────────┐    ┌─────────┐    ┌─────────┐          │
  │  Verify │◀───│Actuation│◀───│ Serial  │◀─────────┘
  │  re-read│    │TT+encod │    │ 9600 bd │
  └─────────┘    └─────────┘    └─────────┘
       │
       └──────────── closed-loop ──────────▶ Validator
```

Seven-stage pipeline: Camera → Vision Pipeline → State Validation → Minimax Search → Serial Link → Actuation → Verification (closed-loop feedback to Validator).

### Hardware Stack

| Processor | Role |
|-----------|------|
| Raspberry Pi 4 (4 GB) | Vision processing, game logic, Flask web dashboard |
| Arduino Uno | Real-time motor control via L298N H-bridge |
| Pi Camera V2 (8 MP) | Image acquisition over CSI interface |

### Actuation

- **Carriage motor**: TT gear motor, open-loop time-based positioning, PWM 100/255
- **Magazine motor**: DC motor with 400 CPR encoder, two-speed release profile (PWM 50 fast / 30 slow)
- **Column times**: [0, 330, 600, 900, 1200, 1500, 1800] ms for columns 1–7

---

## Pipeline

### Image Processing

1. **Rotation** — Affine rotation at θ = 15° CCW with bilinear interpolation
2. **Perspective warp** — Homography to 800×800 px orthographic view
3. **Brightness** — Additive shift c = +30
4. **Contrast** — Linear stretch α = 1.4, β = 0
5. **Gaussian smoothing** — 5×5 kernel
6. **HSV segmentation** — Dual-range red [0/160–10/179], yellow [18–42], morphological cleanup
7. **Cell classification** — Per-cell mask pixel count, threshold τ = 30 px

### Classifier Modes

| Mode | Method | Overall Accuracy |
|------|--------|-----------------|
| HSV | Rule-based HSV thresholds | 98% |
| Auto (hybrid) | ML (confidence >0.65) else HSV | 96% |
| ML | RandomForest (200 trees, depth 10) | 91% |

### Game AI

- **Algorithm**: Minimax with alpha-beta pruning
- **Search depth**: 5 plies
- **Leaf evaluation**: 4-in-a-row windows (69 per board), weights +100/−100/+5/+2/−4, center-column +3 bonus
- **Shortcuts**: Immediate win detection, forced block detection

### Trusted-State Validator

Six rules enforced before any move: (1) board changed, (2) no chip disappeared, (3) exactly one new chip, (4) gravity, (5) column not full, (6) correct player color.

---

## Key Parameters

| Parameter | Value |
|-----------|-------|
| Rotation angle θ | 15° CCW (bilinear) |
| Perspective warp output | 800×800 px |
| Brightness offset c | +30 |
| Contrast α / β | 1.4 / 0 |
| Gaussian kernel | 5×5 |
| HSV red (range 1) | [0,80,50] → [10,255,255] |
| HSV red (range 2) | [160,80,50] → [179,255,255] |
| HSV yellow | [18,100,80] → [42,255,255] |
| Occupancy threshold τ | ≥ 30 px |
| Minimax depth | 5 (alpha-beta pruning) |
| Serial baud | 9600 |
| Carriage PWM | 100/255 |
| Magazine fast / slow PWM | 50 / 30 |
| Encoder target / slow-down | 430 / 40 pulses |
| ML confidence fallback | 0.65 |
| RandomForest trees / depth | 200 / 10 |
| ML features | 18-dim |
| Verification timeout | 10 s (poll 0.4 s) |
| Preview / analysis FPS | 5 / 1 |

---

## Key Results (Interpreted)

### Why HSV Achieves 98% vs. 91% for ML

The blue board frame provides stable hue separation under controlled lighting (~800 lux): blue frame H~85–140, red tokens H~0–10 and H~160–179, yellow tokens H~18–42. These hue bands overlap minimally. The ML classifier was trained on only ~200 ROIs (18 features each), insufficient to generalize across lighting variation. Physics-based HSV thresholds encoding the known token-material reflectance curves outperform data-driven approaches when the training set is small.

### Why Drop Success Degrades from 100% to 85%

The TT carriage uses open-loop time-based positioning. Motor speed varies with battery voltage (12 V sags to ~10.5 V under load, reducing RPM ~12–15%) and rail friction. Error accumulates with distance: column 7 (1800 ms) accumulates ~±8 mm drift vs. ±2.5 mm for columns 1–3. An encoder or homing switch would eliminate this.

### Why a Trusted-State Validator Is Necessary

98% per-cell accuracy means ~0.84 misclassifications per 42-cell board. Without validation, these errors reach the minimax engine, causing play from illegal states. The six-rule validator rejects boards violating gravity, turn order, or single-chip constraints — decoupling vision accuracy from state integrity.

### Why Closed-Loop Verification Catches Missed Drops

Post-move camera re-read detects the 5% of mechanical misses. Without it, a missed drop corrupts the internal state: the robot believes it played column N but the token never arrived. The verifier detects the mismatch and triggers an error state instead of compounding errors.

### Limitations

- **HSV lighting sensitivity**: Specular highlights fragment the blue mask. An EMA filter (α=0.25) mitigates transients; a polarizing filter would provide sustained protection.
- **Time-based carriage**: The dominant failure mode. A ~50 EGP encoder upgrade would reduce the miss rate from 5% to near zero.
- **Minimax latency**: Depth 5 evaluates ~16,800 positions in ~0.95 s. Iterative deepening with time cutoff would allow deeper search on sparse boards.

---

## Bill of Materials

| Component | Qty | Total (EGP) |
|-----------|:---:|:-----------:|
| Raspberry Pi 4 (4 GB) | 1 | 3,200 |
| Arduino Uno | 1 | 450 |
| Pi Camera V2 | 1 | 850 |
| TT motor (carriage) | 1 | 120 |
| DC encoder motor (magazine) | 1 | 280 |
| L298N H-bridge driver | 2 | 170 |
| 12 V 5 A power supply | 1 | 300 |
| Connect-4 board | 1 | 150 |
| Hardware (wires, breadboard, fasteners) | — | 580 |
| **Total** | | **6,100 EGP** |

---

## Test Results

| Metric | Result |
|--------|--------|
| HSV classifier accuracy | 98% |
| Auto-hybrid classifier accuracy | 96% |
| ML-only classifier accuracy | 91% |
| Drop success (140 trials) | 95% overall |
| Column 1–3 success | 100% |
| Column 7 success | 85% |
| End-to-end task time | 7.5 s avg (3–15 s range) |
| Integration tests | 31/31 passed |
| Pi 4 per-frame time | 16.81 ms |
| Laptop per-frame time | 6.85 ms |
| Total BOM cost | 6,100 EGP |

---

## Repository Structure

```
connect-4-robot/
├── arduino/
│   ├── m1_servo_gate_controller/
│   │   └── m1_servo_gate_controller.ino
│   └── ms3_connectfour_dispenser/
│       └── ConnectFour_Dispenser.ino
├── src/
│   ├── image_pipeline/
│   │   ├── m1_image_acquisition.py
│   │   └── m2_member1_pipeline_notebook.ipynb
│   └── runtime/
│       ├── connect4_brain.py
│       └── train_cell_model.py
├── paper/
│   ├── figures/
│   │   ├── m1_proteus_circuit.png
│   │   ├── m2_brightness_adjustment.png
│   │   ├── m2_contrast_scaling.png
│   │   ├── m2_input_board.png
│   │   ├── m2_perspective_warp.png
│   │   ├── m2_rotation_comparison.png
│   │   └── m2_smoothing_comparison.png
│   ├── IEEEtran.cls
│   ├── main.tex
│   ├── main.pdf
│   └── references.bib
├── results/
│   ├── m1_proteus_circuit.png
│   ├── m2_brightness_adjustment.png
│   ├── m2_contrast_scaling.png
│   ├── m2_input_board.png
│   ├── m2_perspective_warp.png
│   ├── m2_rotation_comparison.png
│   └── m2_smoothing_comparison.png
├── docs/
│   ├── MS1_Literature_Review.pdf
│   ├── MS2_Pipeline_Hardware.pdf
│   ├── MS3_Closed_Loop_Integration.pdf
│   ├── MS4_5_Final_Report.pdf
│   └── Final_Presentation.pptx
├── .gitignore
├── CHANGELOG.md
├── CONTRIBUTING.md
├── LICENSE
├── requirements.txt
└── README.md
```

---

## Usage

### Python (Vision + AI)

```bash
pip install -r requirements.txt

# Simulation mode (no camera, no hardware)
python src/runtime/connect4_brain.py --sim

# Live camera mode (requires Pi Camera V2)
python src/runtime/connect4_brain.py

# Train ML cell classifier (requires labeled ROIs)
python src/runtime/train_cell_model.py
```

### Arduino (Dispenser Firmware)

1. Open `arduino/ms3_connectfour_dispenser/ConnectFour_Dispenser.ino` in the Arduino IDE.
2. Select board: **Arduino Uno**.
3. Upload to the Arduino connected via USB to the Raspberry Pi.

### Overleaf (Paper)

1. Create a new Overleaf project.
2. Upload `paper/main.tex`, `paper/references.bib`, `paper/IEEEtran.cls`, and all PNGs from `paper/figures/`.
3. Set compiler to **pdflatex**.
4. Compile: `pdflatex → bibtex → pdflatex → pdflatex`.

---

## Key Equations

**Rotation** (θ = 15°, bilinear):

$$\begin{bmatrix} x' \\ y' \end{bmatrix} = \begin{bmatrix} \cos\theta & -\sin\theta \\ \sin\theta & \cos\theta \end{bmatrix} \begin{bmatrix} x - c_x \\ y - c_y \end{bmatrix} + \begin{bmatrix} c_x \\ c_y \end{bmatrix}$$

**Perspective warp** (homography to 800×800 px):

$$\lambda \begin{bmatrix} u \\ v \\ 1 \end{bmatrix} = \mathbf{H} \begin{bmatrix} x \\ y \\ 1 \end{bmatrix}$$

**Brightness** (c = +30):

$$I_b(x,y) = \mathrm{clip}(I(x,y) + c)$$

**Contrast** (α = 1.4, β = 0):

$$I_c(x,y) = \mathrm{clip}(\alpha \, I(x,y) + \beta)$$

**Gaussian smoothing** (5×5 kernel):

$$I_G(x,y) = \sum_{i=-k}^{k} \sum_{j=-k}^{k} G_\sigma(i,j)\, I(x-i, y-j)$$

**Cell classification** (state matrix S_{r,c} ∈ {0,1,2}):

$$S_{r,c} = \begin{cases} 1, & M_R(r,c) > \tau_R \\ 2, & M_Y(r,c) > \tau_Y \\ 0, & \text{otherwise} \end{cases}$$

**Minimax** (depth 5, alpha-beta pruning):

$$V(S) = \max_{a \in \mathcal{A}(S)} \min_{b \in \mathcal{A}(T(S,a))} V\bigl(T(T(S,a),b)\bigr)$$

---

## Authors

| Member | ID | GitHub |
|--------|:--:|--------|
| Andrew Abdelmalak | 55-22771 | [@andrew-abdelmalak](https://github.com/andrew-abdelmalak) |
| Daniel Boules | 55-5055 | — |
| David Girgis | 55-1481 | — |
| Kirolous Kirolous | 55-18081 | — |
| Samir Yacoub | 55-25111 | — |
| Youssef Salama | 55-0540 | — |

**Supervisor**: Dr. Omar M. Shehata, MCTR 1010 — Image Processing for Mechatronics, German University in Cairo, Spring 2026.

---

## References

1. G. Wölflein and O. Arandjelović, "Determining Chess Game State from an Image," *J. Imaging*, vol. 7, no. 6, p. 94, Jun. 2021.
2. S. M. Zubek, H. Kummerfeld, and J. Wollersheim, "Teach Me What You Want to Play: Learning Variants of Connect Four through Human–Robot Interaction," arXiv:2001.01004, 2021.
3. A. Rezaei and M. S. A. Raihan, "End-to-End Chess Recognition," arXiv:2310.04086, 2023.
4. J. K. Park and S. H. Lee, "Intelligent Lighting System Using Color-Based Image Processing for Object Detection in Robotic Handling Applications," *Appl. Sci.*, vol. 14, no. 7, p. 3002, Apr. 2024.
5. R. Abarkan and J. Wollersheim, "An Open-Source Three-Axis Gantry Robot for Automated Chess Play," *HardwareX*, vol. 17, p. e00517, Mar. 2024.
6. X. Wang, Y. Zhang, and L. Chen, "Structural Design and Position Tracking of the Reconfigurable SCARA Robot by the Pre-Filter AFE PID Controller," *Appl. Sci.*, vol. 12, no. 3, p. 1626, Feb. 2022.
7. OpenCV team, "OpenCV: Open Source Computer Vision Library," 2024. [Online]. Available: https://opencv.org/
8. S. Russell and P. Norvig, *Artificial Intelligence: A Modern Approach*, 4th ed. Pearson, 2020.
9. Raspberry Pi Foundation, "Raspberry Pi 4 Model B," 2024. [Online]. Available: https://www.raspberrypi.org/
10. Arduino, "Arduino UNO R3," 2024. [Online]. Available: https://www.arduino.cc/

---

## License

MIT — see [LICENSE](LICENSE).