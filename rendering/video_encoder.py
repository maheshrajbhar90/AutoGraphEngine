"""FFmpeg wrapper for encoding a video from a JPEG frame stream.

The original rendering script piped JPEG frames to ``ffmpeg`` via
stdin.  This helper class mirrors that behaviour and can be used in
unit tests or in the refactored rendering entry point.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any


class VideoEncoder:
    """Encode a sequence of JPEG frames into an MP4.

    Parameters
    ----------
    out_path:
        Destination file for the encoded video.
    fps:
        Frames per second of the output.
    crf:
        Constant Rate Factor for ``ffmpeg`` – lower values produce
        higher quality.
    """

    def __init__(self, out_path: Path, *, fps: int = 30, crf: int = 18):
        self.out_path = out_path
        self.fps = fps
        self.crf = crf
        self._proc: subprocess.Popen[bytes] | None = None

    def __enter__(self) -> "VideoEncoder":
        self._proc = subprocess.Popen(
            [
                "ffmpeg",
                "-y",
                "-loglevel", "error",
                "-f", "image2pipe",
                "-framerate", str(self.fps),
                "-vcodec", "mjpeg",
                "-i", "-",
                "-c:v", "libx264",
                "-preset", "veryfast",
                "-crf", str(self.crf),
                "-pix_fmt", "yuv420p",
                "-movflags", "+faststart",
                str(self.out_path),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._proc and self._proc.stdin:
            self._proc.stdin.close()
        if self._proc:
            self._proc.wait()

    def write_frame(self, frame: bytes) -> None:
        """Write a JPEG frame to the ffmpeg process."""
        if self._proc and self._proc.stdin:
            self._proc.stdin.write(frame)