# Put the dub track (and subtitles) into a copy of the video with the ffmpeg binary. Video is copied, not
# re-encoded. Without a separated background (stage `separate`), the original audio is kept quietly under
# the dub ("ducking"); with one, the background is mixed in at full level instead.
import logging
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from pydantic import BaseModel, Field

from dich.core.langs import LANGUAGES, iso3
from dich.drivers import Driver, UserError, register
from dich.formats.transcript import Transcript, to_srt

log = logging.getLogger("dich.mux")
SUBTITLE_CODECS = {".mp4": "mov_text", ".mov": "mov_text", ".m4v": "mov_text", ".mkv": "srt"}
DUB_CODECS = {".webm": "libopus", ".avi": "libmp3lame"}   # codec of an added dub track, aac otherwise
SINGLE_AUDIO = {".wav", ".mp3", ".aac", ".flac"}   # containers that hold one audio track only


def mux_command(media, dub, target, subtitles=(), background=None, original_volume=0.15, add_track=False,
                lang="vi"):
    """subtitles: [(lang or None for the original, .srt path)], the first is the default track."""
    subtitle_codec = SUBTITLE_CODECS.get(target.suffix.lower())
    subtitles = list(subtitles) if subtitle_codec else []
    command = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(media), "-i", str(dub)]
    if background:
        command += ["-i", str(background)]
    first_sub = 3 if background else 2
    for _lang, subtitle in subtitles:
        command += ["-i", str(subtitle)]
    if background:
        command += ["-filter_complex", "[2:a][1:a]amix=inputs=2:duration=longest:normalize=0[a]",
                    "-map", "0:v?", "-map", "[a]"]
    elif original_volume > 0:
        command += ["-filter_complex",
                    f"[0:a]volume={original_volume}[bg];[bg][1:a]amix=inputs=2:duration=longest:normalize=0[a]",
                    "-map", "0:v?", "-map", "[a]"]
    else:
        command += ["-map", "0:v?", "-map", "1:a"]
    if add_track:   # dub as audio track 0 (default), original tracks copied after it
        command += ["-map", "0:a?", "-c:a", "copy", "-c:a:0", DUB_CODECS.get(target.suffix.lower(), "aac"),
                    "-metadata:s:a", "title=Original", "-disposition:a", "0", "-disposition:a:0", "default"]
    name = LANGUAGES.get(lang, (None, lang))[1]
    command += ["-metadata:s:a:0", f"title={name}"]
    if iso3(lang):
        command += ["-metadata:s:a:0", f"language={iso3(lang)}"]
    for index, (sub_lang, _subtitle) in enumerate(subtitles):
        title = LANGUAGES.get(sub_lang, (None, sub_lang))[1] if sub_lang else "Original"
        command += ["-map", str(first_sub + index), f"-metadata:s:s:{index}", f"title={title}",
                    f"-disposition:s:{index}", "default" if index == 0 else "0"]
        if sub_lang and iso3(sub_lang):
            command += [f"-metadata:s:s:{index}", f"language={iso3(sub_lang)}"]
    if subtitles:
        command += ["-c:s", subtitle_codec]
    command += ["-c:v", "copy", str(target)]
    return command, subtitles


class Params(BaseModel):
    original_volume: float = Field(0.15, ge=0, le=1, description="Âm lượng tiếng gốc dưới bản lồng tiếng, 0 = bỏ")
    add_track: bool = Field(False, description="Giữ tiếng gốc, thêm bản lồng tiếng thành track âm thanh mới")
    subtitles: bool = Field(True, description="Gắn phụ đề (mp4/mov/mkv)")


@register
class FfmpegMux(Driver):
    stage = "mux"
    name = "ffmpeg"
    title = "FFmpeg"
    Params = Params
    consumes = {"video": "media.video", "dub": "audio.dub.{lang}", "background": "audio.background?",
                "subtitles": "transcript.{lang}?", "original_subtitles": "transcript.src?"}
    produces = {"video": "media.out.{lang}"}

    @classmethod
    def available(cls):
        return None if shutil.which("ffmpeg") else "Chưa cài ffmpeg (brew install ffmpeg)"

    def run(self, ctx, inputs, params):
        media = Path(inputs["video"].path)
        ext = media.suffix.lower()
        target = ctx.output(f"out{media.suffix}")
        subtitles = []
        if params.subtitles:
            for name, lang in (("subtitles", ctx.lang), ("original_subtitles", None)):
                if inputs.get(name):
                    path = ctx.tmpdir / f"{name}.srt"
                    path.write_text(to_srt(Transcript.load(inputs[name].path)), encoding="utf-8")
                    subtitles.append((lang, path))
        background = inputs["background"].path if inputs.get("background") else None
        add_track = params.add_track and ext not in SINGLE_AUDIO
        command, subtitles = mux_command(media, inputs["dub"].path, target, subtitles, background,
                                         params.original_volume, add_track, ctx.lang)
        log.info("%s", shlex.join(command))      # system log
        result = subprocess.run(command, stderr=subprocess.PIPE, text=True)
        sys.stderr.write(result.stderr)
        if result.returncode:
            raise RuntimeError(f"ffmpeg exited with {result.returncode}")
        if not target.exists():
            raise UserError("ffmpeg không tạo được file video")
        ctx.log.info(f"Ghép xong: {'thêm' if add_track else 'thay'} track âm thanh, {len(subtitles)} phụ đề"
                     + (", nhạc nền đã tách" if background else ""))
        return {"video": target}
