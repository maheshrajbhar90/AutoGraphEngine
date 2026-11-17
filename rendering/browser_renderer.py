"""Utilities for rendering SVGs in a headless browser.

The original project used Playwright directly in the rendering
scripts.  This module wraps that logic in a small helper class that
manages browser lifecycle and provides an iterator over JPEG frame
bytes.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Iterable
from playwright.sync_api import sync_playwright


class BrowserRenderer:
    """Render an SVG to a series of JPEG frame bytes.

    Parameters
    ----------
    svg_path:
        Path to the SVG file to render.
    fps:
        Desired frames per second.
    start:
        Start time in seconds.
    end:
        Optional end time; if omitted the total duration of the SVG
        animation is used.
    log_progress:
        If ``True`` progress is printed to ``stdout``.
    """

    def __init__(self, svg_path: Path, *, fps: int = 30, start: float = 0.0, end: float | None = None, log_progress: bool = True):
        self.svg_path = svg_path
        self.fps = fps
        self.start = start
        self.end = end
        self.log_progress = log_progress
        self._playwright = None
        self._browser = None
        self._page = None
        self._total = None

    def __enter__(self) -> "BrowserRenderer":
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(channel="chrome")
        self._page = self._browser.new_page(viewport={"width": 1920, "height": 1080})
        self._page.add_init_script("window.__manual = true")
        url = self.svg_path.resolve().as_uri()
        self._page.goto(url)
        # Wait for the animation controller to be ready
        self._page.wait_for_function("typeof window.__setTime === 'function'")
        self._total = float(self._page.evaluate("window.__duration"))
        if self.end is None:
            self.end = self._total
        self.end = min(self.end, self._total)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._page:
            self._page.close()
        if self._browser:
            self._browser.close()
        if self._playwright:
            self._playwright.stop()

    def iterate_frames(self) -> Iterable[bytes]:
        """Yield JPEG frame bytes at the configured ``fps``.

        The generator drives the SVG animation by calling the
        ``window.__setTime`` JavaScript function.
        """
        n = int((self.end - self.start) * self.fps)
        self.t0 = time.time()
        for i in range(n):
            t = self.start + i / self.fps
            self._page.evaluate("t => window.__setTime(t)", t)
            if self.log_progress and i % (self.fps * 5) == 0:
                print(f"  {i}/{n} frames  ({time.time() - self.t0:.0f}s)", flush=True)
            yield self._page.screenshot(type="jpeg", quality=95)