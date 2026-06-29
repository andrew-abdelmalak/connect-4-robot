# Changelog

All notable changes to this project are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Versioning: [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [1.0.0] — 2026-06-28

### Added
- Complete IEEE conference paper (paper/main.tex) — 9 sections, 7 figures, 7 equations, 5 tables, 10 references
- Trusted-state validation with 6 legality rules
- Three classifier modes: HSV (98%), Auto-hybrid (96%), ML-only (91%)
- Closed-loop camera verification after actuation
- Minimax with alpha-beta pruning at depth 5
- Flask web dashboard with live MJPEG streams
- Arduino dispenser firmware (TT carriage + encoder magazine)
- ML cell classifier training script (RandomForest, 200 trees)
- Pi vs Laptop benchmark results
- Per-column drop success testing (140 trials, 95% overall)
- 31/31 unit tests passing
- docs/ folder with all 5 milestone documents
- results/ folder with all pipeline output figures
- LICENSE (MIT), CONTRIBUTING.md, requirements.txt

### Changed
- Paper rewritten from MS3 level to MS4/5 final level
- BOM updated from 10,135 EGP (MS3 design) to 6,100 EGP (final as-built)
- Hardware: NEMA 17 steppers + SG90 servo → TT DC motor + encoder DC motor
- Camera: Pi Camera V2 8MP → Pi Camera 5MP Rev 1.3
- Brightness offset: +60 → +30; Contrast: α=1.8 → α=1.4
- Index terms trimmed from 8 to 5 per IEEE standard
- \hyperref loaded last in preamble (was not loaded at all)
- Gap statement and paper organization paragraph added to Introduction

### Fixed
- .gitignore no longer ignores *.pdf (was hiding paper PDFs)
- LICENSE year corrected from 2025 to 2026
- BOM discrepancy resolved (README 10,050 vs paper 10,135 → final 6,100)

---

## [0.3.0] — 2026-05-04 *(MS3 — Closed-Loop Integration)*

- Vision pipeline fully implemented on Raspberry Pi
- Minimax with alpha-beta pruning
- Serial communication to Arduino
- Flask dashboard with live camera streams
- NEMA 17 + SG90 servo hardware (later superseded)

## [0.2.0] — 2026-03-17 *(MS2 — Pipeline & Hardware)*

- Image processing pipeline: rotation, warp, brightness, contrast, smoothing
- Feature extraction: mean brightness, rotation angle
- Pi vs Laptop benchmarking
- SolidWorks mechanical design
- Arduino servo control prototype

## [0.1.0] — 2026-03-05 *(MS1 — Literature Review)*

- Literature review of 6 papers
- Pipeline flowchart designed
- BOM and circuit design
- Project plan and team assignments
