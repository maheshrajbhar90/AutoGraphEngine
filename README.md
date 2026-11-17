# Daily Market Animation Generator

This project turns one JSON input file into six individually animated SVG cards plus one combined animated SVG. An optional renderer converts the combined SVG to a 1920×1080 MP4.

## Daily workflow

1. Edit `input/daily_data.json` with the latest figures.
2. Run:

   ```bash
   python3 generate.py
   ```

3. Find the six SVGs and combined animation in `output/svg/`.
4. To render MP4, install the dependencies once:

   ```bash
   python3 -m pip install -r requirements.txt
   python3 -m playwright install chromium
   ```

   FFmpeg must also be installed and available on PATH.

5. Run:

   ```bash
   python3 render_video.py
   ```

   The final file is `output/final_market_animation.mp4`.

## Custom paths

```bash
python3 generate.py input/daily_data.json output/svg
python3 render_video.py --svg output/svg/combined_six_card_animation.svg --out output/final_market_animation.mp4
```

## Data rules

- Keep six market-health score entries.
- Sector performance can contain any number of `[sector_name, return_percent]` pairs; positive values render green and negative values red.
- Keep eleven sector-stage entries and six stock-focus entries to preserve the approved card grids.
- Scores must be between 0 and 100.
- Numeric values are read from JSON when SVGs are generated; the SVG counter animation always counts smoothly from zero to the supplied final value.
- Do not rename JSON keys unless you also update `generate.py`.

## Project contents

- `generate.py`: fully data-driven SVG generator (no manual SVG edits required).
- `input/daily_data.json`: daily data input.
- `output/svg/`: sample generated six cards and combined SVG.
- `render_video.py`: optional browser animation capture to MP4.
- `requirements.txt`: renderer dependency.


-- python3 generate.py
--python3 render_video.py# AutoGraphEngine
