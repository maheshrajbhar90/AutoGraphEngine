from TTS.api import TTS

tts = TTS("tts_models/multilingual/multi-dataset/xtts_v2").to("cuda")  # use "cpu" if no GPU

tts.tts_to_file(
    text="Five thousand seven hundred thirty-seven crore rupees. In one day.",
    speaker_wav="my_voice_sample.wav",
    language="en",   # "hi" for Hindi
    file_path="output.wav",
)