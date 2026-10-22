# Contributing to Vision-Based Connect-4 Robot

This is a finished course project. Bug reports, bug fixes, and documentation corrections are welcome. We do not plan new features, so please open an issue before you start on anything larger than a small fix.

## Before you start

- Search [existing issues](https://github.com/andrew-abdelmalak/connect-4-robot/issues) to avoid duplicates.
- By participating, you agree to follow the [Code of Conduct](CODE_OF_CONDUCT.md).

## Set up your environment

You need Python 3.11 or later and [uv](https://docs.astral.sh/uv/). The tests use fake serial ports and the repository's own board photo, so you do not need the robot, a camera, or an Arduino.

```bash
git clone https://github.com/andrew-abdelmalak/connect-4-robot.git
cd connect-4-robot
uv sync --all-groups
```

## Run the checks

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

To compile the Arduino sketches, install [arduino-cli](https://arduino.github.io/arduino-cli/) and run:

```bash
arduino-cli core install arduino:avr@1.8.8
arduino-cli lib install Servo@1.3.0
arduino-cli compile --fqbn arduino:avr:uno arduino/dispenser
```

CI runs these checks, plus the Milestone 2 notebook, on every pull request. A pull request with failing checks is not merged.

## Build the paper

The LaTeX source in `paper/` mirrors the Overleaf project and builds with pdfLaTeX on TeX Live 2025. To build it locally with the same engine, run this from the repository root:

```bash
docker run --rm -v "$PWD/paper":/work -w /work texlive/texlive:TL2025-historic latexmk -pdf main.tex
```

Keep the file names in `paper/` unchanged, so that edits can be copied back to Overleaf.

## Make your change

1. Create a branch from `main`, for example `git switch -c fix-validator-message`.
2. Keep the change focused on one problem.
3. Add a test that fails without your change.
4. Do not change tuned values (HSV thresholds, brightness and contrast, carriage times, pulse counts, timeouts) without testing on the robot, and say in the pull request how you tested.
5. Update the README when behavior or commands change.

## Commit messages

Write a short summary in the imperative mood, under 72 characters, for example `Reject boards with two new chips`. Explain the reason in the body when it is not obvious.

## Open a pull request

1. Push your branch and open a pull request against `main`.
2. Fill in the pull request template, and link the issue it resolves (`Fixes #123`).

## Report a bug

Use the [issue forms](https://github.com/andrew-abdelmalak/connect-4-robot/issues/new/choose). For security problems, follow [SECURITY.md](SECURITY.md) instead, and never report them in a public issue.

## License

By contributing, you agree that your contributions are licensed under the project's [MIT License](../LICENSE).
