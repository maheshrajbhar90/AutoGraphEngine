"""Entry point for rendering SVG cards into MP4 video.

The original scripts ``render_video_combine.py`` and
``render_video_split.py`` performed similar tasks.  The refactored
implementation provides a single script that accepts ``--combined`` or
``--split`` flags and uses :class:`BrowserRenderer` and
``VideoEncoder`` to produce the requested output.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Iterable

# Import heavy dependencies lazily to keep the module importable when
# Playwright or FFmpeg are not installed.  The imports are performed
# inside :func:`main` and :func:`_render_one`.


def _render_one(svg_path: Path, out_path: Path, *, fps: int, start: float, end: float | None) -> None:
    """Render a single SVG into an MP4 file."""
    # Import inside the function to avoid import errors during unit tests.
    from .browser_renderer import BrowserRenderer
    from .video_encoder import VideoEncoder

    with BrowserRenderer(svg_path, fps=fps, start=start, end=end) as br, VideoEncoder(out_path, fps=fps) as ve:
        for frame in br.iterate_frames():
            ve.write_frame(frame)
    print(f"Wrote {out_path}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Render SVG cards to MP4.")
    ap.add_argument("svg_dir", nargs="?", default=str(Path(__file__).parent.parent / "output" / "svg"))
    ap.add_argument("out", nargs="?", default=str(Path(__file__).parent.parent / "output" / "video"))
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--start", type=float, default=0.0)
    ap.add_argument("--end", type=float, default=None)
    ap.add_argument("--crf", type=int, default=18)
    ap.add_argument("--split", action="store_true", help="Render each SVG card individually.")
    args = ap.parse_args()

    svg_dir = Path(args.svg_dir)
    out_path = Path(args.out)
    if args.split:
        out_dir = out_path
        out_dir.mkdir(parents=True, exist_ok=True)
        for svg_file in svg_dir.glob("*.svg"):
            if svg_file.name == "combined_six_card_animation.svg":
                continue
            out_file = out_dir / f"{svg_file.stem}.mp4"
            _render_one(svg_file, out_file, fps=args.fps, start=args.start, end=args.end)
    else:
        ap.error("Specify --split")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())