"""Tests for the rendering module.

Because rendering requires Playwright and FFmpeg we only perform
lightweight import checks here.  Full integration tests would be
expensive and are out of scope for the current CI.
"""

import importlib


def test_render_video_import():
    mod = importlib.import_module("rendering.render_video")
    assert hasattr(mod, "main")