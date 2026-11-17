
"""
Universal SVG to MP4 Renderer

Supports:
- Custom SVG animation: window.__setTime()
- CSS animations and transitions
- SMIL animations
- Static SVG files
- Individual card rendering
- Combined film rendering

Output:
1920x1080 H.264 MP4, yuv420p

Requirements:
pip install playwright
playwright install chromium
FFmpeg installed and available on PATH
"""

import argparse
import re
import subprocess
import sys
import time

from pathlib import Path
from playwright.sync_api import sync_playwright


COMBINED = "combined_six_card_animation.svg"


# --------------------------------------------------
# DETECT SVG DURATION
# --------------------------------------------------

def detect_duration(page, fallback=10.0):

    return page.evaluate(
        """fallback => {

            // 1. Custom animation duration
            if (
                typeof window.__duration === "number" &&
                window.__duration > 0
            ) {
                return window.__duration;
            }

            const svg = document.documentElement;

            // 2. SVG data-duration attribute
            const attr = svg.getAttribute("data-duration");

            if (attr) {
                const value = parseFloat(attr);
                if (value > 0) return value;
            }

            // 3. Detect SMIL animation durations
            let maxDuration = 0;

            document.querySelectorAll(
                "animate, animateTransform, animateMotion, set"
            ).forEach(el => {

                const dur = el.getAttribute("dur");

                if (dur) {
                    const match = dur.match(
                        /([0-9.]+)(ms|s)?/
                    );

                    if (match) {
                        let value = parseFloat(match[1]);

                        if (match[2] === "ms") {
                            value /= 1000;
                        }

                        maxDuration = Math.max(
                            maxDuration,
                            value
                        );
                    }
                }

                const begin = el.getAttribute("begin");
                const end = el.getAttribute("end");

                if (end && !isNaN(parseFloat(end))) {
                    maxDuration = Math.max(
                        maxDuration,
                        parseFloat(end)
                    );
                }
            });

            // 4. Detect CSS animation duration
            document.querySelectorAll("*").forEach(el => {

                const style = getComputedStyle(el);

                const durations = style.animationDuration.split(",");
                const delays = style.animationDelay.split();
                const iterations = style.animationIterationCount.split(",");

                durations.forEach((d, i) => {

                    let duration = parseFloat(d) || 0;

                    if (d.includes("ms")) {
                        duration /= 1000;
                    }

                    let delay = parseFloat(
                        delays[i % Math.max(delays.length, 1)]
                    ) || 0;

                    if (style.animationDelay.includes("ms")) {
                        delay /= 1000;
                    }

                    let count = iterations[i] || "1";

                    let total = count === "infinite"
                        ? duration
                        : duration * (parseFloat(count) || 1);

                    maxDuration = Math.max(
                        maxDuration,
                        delay + total
                    );
                });
            });

            return maxDuration > 0
                ? maxDuration
                : fallback;
        }""",
        fallback
    )


# --------------------------------------------------
# RENDER ONE SVG
# --------------------------------------------------

def render_one(
    page,
    svg,
    output,
    fps,
    crf,
    start,
    end_limit,
    fallback_duration
):

    print(f"    Loading: {svg.name}", flush=True)

    errors = []

    page.on(
        "pageerror",
        lambda error: errors.append(str(error))
    )

    try:

        page.goto(
            svg.resolve().as_uri(),
            wait_until="load",
            timeout=30000
        )

        page.wait_for_function(
            """
            document.documentElement &&
            document.documentElement.tagName.toLowerCase() === 'svg'
            """,
            timeout=10000
        )

    except Exception as e:

        print(f"    ERROR loading SVG: {e}")

        return 1

    # Check animation controller
    has_manual_clock = page.evaluate(
        "typeof window.__setTime === 'function'"
    )

    total = detect_duration(
        page,
        fallback_duration
    )

    end = min(end_limit, total) if end_limit else total

    if start >= end:
        print("    ERROR: Start time must be less than end time.")
        return 1

    frame_count = max(
        1,
        int((end - start) * fps)
    )

    print(
        f"    Duration: {total:.2f}s | "
        f"Frames: {frame_count} | "
        f"Mode: {'Frame-exact' if has_manual_clock else 'Real-time'}",
        flush=True
    )

    # If no custom clock exists, allow browser animations to run.
    if not has_manual_clock:

        page.evaluate(
            """
            () => {
                document.getAnimations().forEach(animation => {
                    animation.play();
                });

                document.querySelectorAll(
                    "animate, animateTransform, animateMotion"
                ).forEach(el => {
                    if (el.beginElement) {
                        try {
                            el.beginElement();
                        } catch(e) {}
                    }
                });
            }
            """
        )

    # FFmpeg command
    command = [
        "ffmpeg",
        "-y",
        "-loglevel", "error",
        "-f", "image2pipe",
        "-framerate", str(fps),
        "-vcodec", "mjpeg",
        "-i", "-",
        "-vf", "scale=1920:1080:force_original_aspect_ratio=decrease,"
              "pad=1920:1080:(ow-iw)/2:(oh-ih)/2",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", str(crf),
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        str(output)
    ]

    ff = subprocess.Popen(
        command,
        stdin=subprocess.PIPE
    )

    t0 = time.time()

    try:

        for i in range(frame_count):

            timestamp = start + i / fps

            # Custom SVG clock
            if has_manual_clock:

                page.evaluate(
                    "t => window.__setTime(t)",
                    timestamp
                )

            else:

                # Allow CSS / SMIL animation to advance.
                if i > 0:
                    page.wait_for_timeout(
                        max(1, round(1000 / fps))
                    )

            frame = page.screenshot(
                type="jpeg",
                quality=95,
                animations="allow"
            )

            ff.stdin.write(frame)

            if i % (fps * 5) == 0:

                print(
                    f"    {i}/{frame_count} frames "
                    f"({time.time() - t0:.0f}s)",
                    flush=True
                )

    except (BrokenPipeError, OSError) as e:

        print(f"    FFmpeg pipe error: {e}")

        return 1

    finally:

        if ff.stdin and not ff.stdin.closed:
            ff.stdin.close()

    return_code = ff.wait()

    if return_code != 0:

        print("    ERROR: FFmpeg rendering failed.")

        return return_code

    print(
        f"    DONE: {output.name} | "
        f"{frame_count / fps:.1f}s | "
        f"Rendered in {time.time() - t0:.1f}s",
        flush=True
    )

    if errors:

        print("    Browser JavaScript errors:")

        for error in errors[:5]:
            print("    ", error)

    return 0


# --------------------------------------------------
# MAIN
# --------------------------------------------------

def main():

    here = Path(__file__).parent

    ap = argparse.ArgumentParser(
        description="Universal SVG to MP4 renderer"
    )

    ap.add_argument(
        "svg_dir",
        nargs="?",
        default=str(here / "output" / "svg")
    )

    ap.add_argument(
        "out_dir",
        nargs="?",
        default=str(here / "output" / "videos")
    )

    ap.add_argument(
        "--only",
        default=None,
        help="Card numbers, e.g. 1,3,5 or combined"
    )

    ap.add_argument(
        "--combined",
        action="store_true"
    )

    ap.add_argument(
        "--fps",
        type=int,
        default=30
    )

    ap.add_argument(
        "--crf",
        type=int,
        default=18
    )

    ap.add_argument(
        "--start",
        type=float,
        default=0.0
    )

    ap.add_argument(
        "--end",
        type=float,
        default=None
    )

    ap.add_argument(
        "--duration",
        type=float,
        default=10.0,
        help="Fallback duration for SVGs without detectable duration"
    )

    a = ap.parse_args()

    if a.fps <= 0 or a.crf < 0 or a.duration <= 0:
        print("ERROR: Invalid FPS, CRF or duration.")
        return 1

    svg_dir = Path(a.svg_dir)
    out_dir = Path(a.out_dir)

    if not svg_dir.is_dir():
        print("SVG directory not found:", svg_dir)
        return 1

    out_dir.mkdir(
        parents=True,
        exist_ok=True
    )

    # Discover SVG files
    cards = sorted(
        p for p in svg_dir.glob("*.svg")
        if p.name != COMBINED
    )

    want_combined = a.combined

    if a.only:

        tokens = [
            x.strip().lower()
            for x in a.only.split(",")
            if x.strip()
        ]

        want_combined = (
            want_combined or "combined" in tokens
        )

        nums = {
            int(x)
            for x in tokens
            if x.isdigit()
        }

        if nums:
            cards = [
                p for p in cards
                if re.match(r"^\d+", p.stem)
                and int(re.match(r"^\d+", p.stem).group()) in nums
            ]
        elif "combined" in tokens:
            cards = []

        else:
            # Allow selecting an SVG by filename
            cards = [
                p for p in cards
                if p.stem.lower() in tokens
                or p.name.lower() in tokens
            ]

    jobs = [
        (p, out_dir / (p.stem + ".mp4"))
        for p in cards
    ]

    if want_combined:

        combined = svg_dir / COMBINED

        if combined.exists():

            jobs.append(
                (
                    combined,
                    out_dir / "00_full_film.mp4"
                )
            )

        else:

            print("Combined SVG not found:", combined)

    if not jobs:

        print("No matching SVG files found in:", svg_dir)
        return 1

    rc = 0

    with sync_playwright() as p:

        browser = p.chromium.launch(
            channel="chrome"
        )

        page = browser.new_page(
            viewport={
                "width": 1920,
                "height": 1080
            },
            device_scale_factor=1
        )

        for index, (svg, output) in enumerate(jobs, 1):

            print(
                f"\n[{index}/{len(jobs)}] {svg.name}",
                flush=True
            )

            result = render_one(
                page,
                svg,
                output,
                a.fps,
                a.crf,
                a.start,
                a.end,
                a.duration
            )

            if result != 0:
                rc = result
                print("    Skipping remaining rendering for this file.")

        browser.close()

    print("\nRendering completed.")
    print("Videos saved to:", out_dir)

    return rc


if __name__ == "__main__":
    sys.exit(main())
