"""Convenience wrapper for running the daily market‑data pipeline.

The original project exposed a ``run_daily.sh`` shell script that
performed three steps in order:

1. Update the market JSON file from the textual report.
2. Generate SVG cards from the updated JSON.
3. Render those SVG cards into MP4 video.

This module implements the same behaviour in pure Python so that it
can be executed from the test suite, CI pipelines, or as a normal
Python script.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from config import DEFAULT_INPUT, DEFAULT_OUTPUT, SVG_DIR, VIDEO_DIR


def _run(cmd: list[str], cwd: Path | None = None) -> None:
    """Execute a command and raise on non‑zero exit code."""
    print(f"Running: {' '.join(cmd)}")
    subprocess.run(cmd, cwd=cwd, check=True)


def run_update() -> None:
    _run(["python", "update_market_json.py", "--input", str(DEFAULT_INPUT), "--output", str(DEFAULT_OUTPUT)])


def run_generate() -> None:
    _run(["python", "generate.py", str(DEFAULT_INPUT), str(SVG_DIR)])


def run_rendering() -> None:
    # Render each individual SVG card as a separate MP4 file.
    _run(["python", "rendering/render_video.py", str(SVG_DIR), str(VIDEO_DIR), "--split"])


def main() -> None:
    run_update()
    run_generate()
    run_rendering()


if __name__ == "__main__":
    main()