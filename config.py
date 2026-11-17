"""Global configuration constants for the AutoGraphEngine.

This module centralises default file paths and optional environment
overrides.  Existing scripts that rely on hard‑coded defaults can
continue to work unchanged, while the new pipeline and tests can
import these constants directly.

The values are intentionally simple – they reflect the original
project layout:

* ``input/daily_data.json`` – the JSON file that represents the
  current market snapshot.
* ``output/svg`` – where the SVG cards are written.
* ``output/video`` – where the rendered MP4 files are written.

Environment variables can override the defaults:

``OLLAMA_MODEL`` – default ``gpt-oss:20b``
``OLLAMA_HOST`` – default ``http://localhost:11434``
``INPUT_FILE`` – path to the JSON input
``OUTPUT_FILE`` – path to the JSON output
"""

from __future__ import annotations

from pathlib import Path
import os

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
INPUT_DIR = BASE_DIR / "input"
OUTPUT_DIR = BASE_DIR / "output"
SVG_DIR = OUTPUT_DIR / "svg"
VIDEO_DIR = OUTPUT_DIR / "video"

DEFAULT_INPUT = INPUT_DIR / "daily_data.json"
DEFAULT_OUTPUT = INPUT_DIR / "daily_data.json"

# ---------------------------------------------------------------------------
# Ollama defaults
# ---------------------------------------------------------------------------
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "gpt-oss:20b")
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
