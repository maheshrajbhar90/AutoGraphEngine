# Controls and timing

## Sequencing
Every animation now starts only after the previous one has finished — inside a card and from card to card.
Each card is a numbered list of *steps* (see `scene_timeline.json` or `timing_template.json`).

## Live controls (open the SVG in Chrome/Edge; add `?step=1` for step mode)
| Key | Action |
|---|---|
| Space / Enter | Pause / resume (when waiting at a step: play it) |
| → | Next step (in step mode: play the step you are waiting at) |
| ← | Back one step (shows the state before that step; press → to replay it) |
| S | Toggle step mode (auto-pause before every step and at the end of every card) |
| R | Restart |

Step mode: the film waits before each step, so you can explain a value and press → when ready.
At the end of each card it waits on the finished card; → jumps straight to the next card.
Use `combined_six_card_animation.svg?step=1` for the whole film; the individual card SVGs work too.

## Fixed timings for an exported video — `input/timing.json`
Copy `output/svg/timing_template.json` to `input/timing.json` and keep only what you change:
- `duration`: total seconds for a card (overrides `hold`)
- `hold`: seconds the finished card stays on screen
- `intro_pause`: extra wait after the title, before the first step (pauses the first card until you are ready)
- `gap`: pause between steps
- `pauses`: `{"3": 6}` adds 6 s after step 3
Put the `default` block at the top for all cards; use `"scenes": {"1": {...}}` for one card.
Then run `python generate_svgs.py` and `python render_video.py`.

## Cards 7 and 8 (optional)
Add `card7` / `card8` to the JSON and the scenes are generated (scene numbers 7 and 8 in `timing.json`); omit them and the film stays six cards.

```json
"card7": {"as_of": "1m ago", "groups": [
  {"label": "CURRENT LEADERS", "tone": "green", "sectors": [{"name": "MEDIA", "change": -1.2}]},
  {"label": "WEAKENING", "tone": "red", "sectors": [["AUTO", 1.1]]}]},
"card8": {"vix": {"value": 17.5, "change_pct": 12.1, "source": "..."},
          "pcr": {"value": 1.07, "label": "neutral", "basis": "..."}}
```
`tone` = green | blue | red | amber. VIX/PCR zone boundaries are the constants `VIX_ZONES`, `PCR_ZONES` (and the scale `VIX_SCALE`, `PCR_SCALE`) near `card8` in `generate_svgs.py`.