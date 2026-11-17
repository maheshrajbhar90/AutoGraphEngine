"""Render combined_six_card_animation.svg to an H.264 MP4 (1920x1080, yuv420p) via headless Chrome + FFmpeg.

Frames are produced deterministically by seeking the animation clock (window.__setTime), so the result is
frame-exact and independent of machine speed.

Usage:
    python render_video.py [svg_dir] [out.mp4] [--fps 30] [--start 0] [--end SECONDS] [--crf 18]

Needs (optional, only for video export): pip install playwright, Google Chrome, ffmpeg on PATH.
"""
import argparse, subprocess, sys, time
from pathlib import Path
from playwright.sync_api import sync_playwright


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("svg_dir", nargs="?", default=str(Path(__file__).parent / "output" / "svg"))
    ap.add_argument("out", nargs="?", default=str(Path(__file__).parent / "output" / "market_intelligence.mp4"))
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--start", type=float, default=0.0)
    ap.add_argument("--end", type=float, default=None)
    ap.add_argument("--crf", type=int, default=18)
    a = ap.parse_args()

    url = (Path(a.svg_dir) / "combined_six_card_animation.svg").resolve().as_uri()
    ff = subprocess.Popen(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "image2pipe", "-framerate", str(a.fps), "-vcodec", "mjpeg", "-i", "-",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", str(a.crf), "-pix_fmt", "yuv420p", "-movflags", "+faststart", a.out],
        stdin=subprocess.PIPE)
    t0 = time.time()
    with sync_playwright() as p:
        b = p.chromium.launch(channel="chrome")
        pg = b.new_page(viewport={"width": 1920, "height": 1080})
        pg.add_init_script("window.__manual = true")
        pg.goto(url)
        pg.wait_for_function("typeof window.__setTime === 'function'")
        total = pg.evaluate("window.__duration")
        end = min(a.end, total) if a.end else total
        n = int((end - a.start) * a.fps)
        for i in range(n):
            pg.evaluate("t => window.__setTime(t)", a.start + i / a.fps)
            ff.stdin.write(pg.screenshot(type="jpeg", quality=95))
            if i % (a.fps * 5) == 0:
                print(f"  {i}/{n} frames  ({time.time() - t0:.0f}s)", flush=True)
        b.close()
    ff.stdin.close()
    ff.wait()
    print(f"Wrote {a.out}: {n} frames, {n / a.fps:.1f}s of {total:.1f}s total, render {time.time() - t0:.0f}s")
    return ff.returncode


if __name__ == "__main__":
    sys.exit(main())
