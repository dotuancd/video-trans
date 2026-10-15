## moondream/parakeet-redux

## https://huggingface.co/moondream/parakeet-redux

## uv pip install moondream

# pip install moondream  (2.4.0 or later)
import moondream as md
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from subtitle import write_srt  # noqa: E402

audio_path = "017 Hallucination _ AI Coding Crash Course.wav"
output_path = "moonream-parakeet-redux-rs.srt"

# device: "cpu", "mps" or "cuda"; leave it out to take CUDA,
# then Apple silicon, then the CPU
with md.photon("moondream/parakeet-redux", device="cpu") as speech:
    # timestamps="word": one entry per sentence, each with the start
    # and end of every word
    # for word in result["segments"][0]["words"][:3]:
    #     print(word["start"], word["end"], word["word"])
    # 0.32 0.56 From
    # 0.56 0.72 the
    # 0.72 1.44 undersurface
    result = speech.transcribe(audio=audio_path, timestamps="word")

    # Flatten the words of every segment so cues are split the same way as needle
    words = [word for segment in result["segments"] for word in segment["words"]]
    subtitles = write_srt(words, output_path)

    print('{count} cues written to {path}'.format(count=len(subtitles), path=output_path))

