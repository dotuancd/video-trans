import needle
from subtitle import write_srt

audio_path = "017 Hallucination _ AI Coding Crash Course.wav"
output_path = "needle-whilte-rs.txt"
srt_path = "needle-whistle-rs.srt"

# '/Users/tuan_da/100_Projects/AI Coding Crash Course/mp-aicodingcrashcourse/017 Hallucination _ AI Coding Crash Course.mp4'

# ['text', 'language', 'ttft_ms', 'decode_tps', 'words' appeared if word_timestamps is true]
transcript = needle.transcribe(audio_path, word_timestamps=True)

with open(output_path, 'w') as f:
    f.write(transcript['text'])

subtitles = write_srt(transcript["words"], srt_path)

print('TTFT: {ttft:.0f} ms, {count} cues written to {path}'.format(ttft=transcript['ttft_ms'], count=len(subtitles), path=srt_path))
