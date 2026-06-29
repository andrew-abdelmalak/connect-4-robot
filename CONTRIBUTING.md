# Contributing

**Course project — MCTR 1010 Image Processing for Mechatronics · German University in Cairo · Spring 2026**
**Team 21:** Andrew Abdelmalak (55-22771) · Daniel Boules (55-5055) · David Girgis (55-1481) · Kirolous Kirolous (55-18081) · Samir Yacoub (55-25111) · Youssef Salama (55-0540)

This repository is a graded academic submission. Contributions are limited to the six registered team members. External pull requests are not accepted.

---

## Branching

Work on feature branches off `main`. Branch naming: `type/short-description`

## Commit Messages

Follow [Conventional Commits](https://www.conventionalcommits.org/):

```
feat(src): add ML cell classifier training script
fix(paper): correct BOM total from 10135 to 6100 EGP
docs(readme): update repo structure tree
chore: regenerate pipeline figures
```

## File Roles

| Path | Owner | Notes |
|---|---|---|
| `src/runtime/connect4_brain.py` | All | Main vision + AI + Flask dashboard |
| `src/image_pipeline/` | All | MS1/MS2 pipeline scripts and notebooks |
| `arduino/` | Hardware team | Arduino firmware for motor control |
| `paper/main.tex` | All | IEEE paper — no placeholder text |
| `results/` | Auto-generated | Pipeline output figures |

## Running the system

```bash
# Python dependencies
pip install -r requirements.txt

# Run the Connect 4 brain (simulation mode)
python src/runtime/connect4_brain.py --sim

# Run with camera
python src/runtime/connect4_brain.py

# Train ML cell classifier
python src/runtime/train_cell_model.py
```

## Releases

Tag each milestone submission:
```
git tag -a v0.X.0 -m "MilestoneN — MCTR 1010"
```

Current release: **v1.0.0** (final submission, 2026-06-28)
