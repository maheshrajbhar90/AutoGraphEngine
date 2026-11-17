#!/bin/bash
set -e
# Run the updated pipeline
python3 pipeline/daily_pipeline.py
python3 rendering/render_video.py --split
python3 generate_svg.py
python3 rendering/browser_renderer.py
python3 render_video_split.py 
python3 render_video_combine.py 



source voiceclone/bin/activate
python clone_voice.py
python clone_voice_chatterbox.py


ffmpeg -i reference/my_voice.m4a -ar 22050 -ac 1 reference/my_voice.wav

voiceclone/references/my_voice.m4a