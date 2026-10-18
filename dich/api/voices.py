# Voices: the preset voices of the TTS, the voice library (named clips to clone from, shared or a project's),
# finding the best 3–8s windows of a recording to make one, and the voice preview.
import asyncio
import json
import time
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from dich.api.models import LibraryVoice, Preview, Voice, VoiceAnalysis, VoiceAnalyze, VoiceCandidate, VoiceCreate, VoicePatch
from dich.core.ids import new_id
from dich.formats.transcript import Transcript, read_srt
from dich.voices import REF_SECONDS, VAD_HOP, candidates, clip_name, cut, levels_of

SEGMENT_MAX = 30.0   # seconds of a recording played back at once
SLACK = 0.05         # seconds a saved clip may run past REF_SECONDS (rounding of the UI's nudges)


def prune(folder, days=1):
    if folder.is_dir():
        cutoff = time.time() - days * 86400
        for path in folder.iterdir():
            if path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)


def router(ctx):
    api = APIRouter()
    store = ctx.store
    presets_cache = []
    picks = ctx.voices / "candidates"   # windows cut while choosing, kept a day

    # --- answered by the interactive worker, which keeps the TTS model loaded ---

    async def call(kind, args, timeout=180):
        call_id = store.call(kind, args)
        result, error = await asyncio.to_thread(store.wait_call, call_id, timeout)
        if error:
            raise HTTPException(503 if error.startswith("Chưa cài") else 400, error)
        return result

    @api.get("/voices/presets")
    async def list_preset_voices() -> list[Voice]:
        if not presets_cache:
            presets_cache.extend(await call("voices", {}))
        return presets_cache

    # --- the library ---

    def voice_or_404(voice_id):
        voice = store.voice(voice_id)
        if not voice:
            raise HTTPException(404, "Không có giọng này")
        return voice

    def checked_name(name):
        name = name.strip()
        if not name or len(name) > 80:
            raise HTTPException(400, "Tên giọng trống hoặc quá dài")
        return name

    def checked_project(project_id):
        if project_id and not store.project(project_id):
            raise HTTPException(404, "Không có project này")
        return project_id or None

    def recording(path):
        return ctx.existing(path, extra=[ctx.data_dir])

    @api.get("/voices")
    def list_voices(project: str | None = None) -> list[LibraryVoice]:
        """Every voice; with `project`, that project's first, then the shared ones."""
        return store.voices(project)

    @api.post("/voices/analyze")
    async def analyze_voice(body: VoiceAnalyze) -> VoiceAnalysis:
        """The best 3–8s windows of a recording to clone a voice from, each cut out to listen to."""
        source, cues = job_recording(body.job_id, body.lang) if body.job_id else (None, None)
        if not body.job_id:
            if not body.path:
                raise HTTPException(400, "Cần file hoặc job")
            source = recording(body.path)
            cues = near_subtitles(source, body.lang)
        try:
            found, duration = await asyncio.to_thread(find, source, cues)
        except RuntimeError as error:
            raise HTTPException(400, f"Không đọc được tiếng trong {source.name}: {error}") from error
        if duration < REF_SECONDS[0]:
            raise HTTPException(400, f"File quá ngắn ({duration:.1f}s), cần ít nhất {REF_SECONDS[0]:.0f} giây")
        if not found:
            raise HTTPException(400, "Không tìm thấy đoạn nói liền 3 giây nào")
        return VoiceAnalysis(source=str(source), duration=duration, candidates=found)

    def find(source, cues):
        levels = levels_of(source)
        prune(picks)
        found = []
        for c in candidates(source, cues, levels=levels):
            clip = picks / clip_name(source, c["start"], c["end"])
            if not clip.exists():
                cut(source, c["start"], c["end"], clip)
            found.append(VoiceCandidate(**c, clip=str(clip)))
        return found, round(len(levels) * VAD_HOP, 2)

    def job_recording(job_id, lang):
        """(the job's speech track, else its audio or video; its subtitles' cues)."""
        if not store.job(job_id):
            raise HTTPException(404, "Không có job này")
        for slot in ("audio.speech", "audio.original", "media.video"):
            _t, found = store.slot_artifact(job_id, slot)
            if found and Path(found["path"]).is_file():
                break
        else:
            raise HTTPException(400, "Job chưa có video/audio")
        for slot in (f"transcript.{lang}", "transcript.src"):
            _t, transcript = store.slot_artifact(job_id, slot)
            if transcript:
                return Path(found["path"]), Transcript.load(transcript["path"]).cues
        return Path(found["path"]), None

    def near_subtitles(media, lang):
        """The cues of <name>.srt / <name>.<lang>.srt next to media, if any."""
        stem = media.with_suffix("")
        for path in (Path(f"{stem}.{lang}.srt"), Path(f"{stem}.srt")):
            if path.is_file():
                return read_srt(path).cues
        return None

    def window_clip(source, start, end):
        """start–end of a recording cut into the candidates folder (once)."""
        if not 0 <= start < end or end - start > SEGMENT_MAX:
            raise HTTPException(400, f"Đoạn không hợp lệ (tối đa {SEGMENT_MAX:.0f} giây)")
        media = recording(source)
        clip = picks / clip_name(media, start, end)
        if not clip.exists():
            cut(media, start, end, clip)
        return clip

    @api.get("/voices/segment", response_class=FileResponse, responses={200: {"content": {"audio/wav": {}}}})
    def voice_segment(source: str, start: float, end: float):
        """start–end of a recording as a wav, to hear a window after moving its ends."""
        return FileResponse(window_clip(source, start, end), media_type="audio/wav")

    @api.post("/voices")
    def create_voice(body: VoiceCreate) -> LibraryVoice:
        """Cuts start–end (3–8s) of the recording into the library."""
        name, project_id = checked_name(body.name), checked_project(body.project_id)
        if not (0 <= body.start and REF_SECONDS[0] - SLACK <= body.end - body.start <= REF_SECONDS[1] + SLACK):
            raise HTTPException(400, f"Đoạn giọng phải dài {REF_SECONDS[0]:.0f}–{REF_SECONDS[1]:.0f} giây")
        media = recording(body.source)
        voice_id = new_id("voc")
        path = cut(media, body.start, body.end, ctx.voices / f"{voice_id}.wav")
        store.create_voice(voice_id, name, path, project_id, media.name, body.start, body.end)
        return store.voice(voice_id)

    @api.patch("/voices/{voice_id}")
    def save_voice(voice_id: str, body: VoicePatch) -> LibraryVoice:
        voice_or_404(voice_id)
        store.update_voice(voice_id, checked_name(body.name), checked_project(body.project_id))
        return store.voice(voice_id)

    @api.delete("/voices/{voice_id}", status_code=204)
    def delete_voice(voice_id: str):
        """Not while a project dubs with it by default (409)."""
        voice = voice_or_404(voice_id)
        using = [p["name"] for p in store.projects() if voice["path"] in json.dumps(p["options"], ensure_ascii=False)]
        if using:
            raise HTTPException(409, "Giọng đang là mặc định của project " + ", ".join(using))
        store.delete_voice(voice_id)
        Path(voice["path"]).unlink(missing_ok=True)

    @api.get("/voices/{voice_id}/audio", response_class=FileResponse, responses={200: {"content": {"audio/wav": {}}}})
    def voice_audio(voice_id: str):
        return FileResponse(voice_or_404(voice_id)["path"], media_type="audio/wav")

    # --- preview ---

    def spans(transcript):
        return [[c.start, c.end, c.text] for c in transcript.cues] if transcript else []

    def preview_files(body):
        """(video, cues timing the clip to clone, cues to read) of a job or of a video with its .srt next to it."""
        if body.job_id:
            _t, video = store.slot_artifact(body.job_id, "media.video")
            _t, target = store.slot_artifact(body.job_id, f"transcript.{body.lang}")
            _t, source = store.slot_artifact(body.job_id, "transcript.src")
            target = Transcript.load(target["path"]) if target else None
            source = Transcript.load(source["path"]) if source else None
            return video["path"] if video else None, spans(target or source), spans(target)
        if body.path:
            media = ctx.existing(body.path)
            stem = media.with_suffix("")
            near = {lang: Path(f"{stem}{suffix}") for lang, suffix in ((None, ".srt"), (body.lang, f".{body.lang}.srt"))}
            found = {lang: read_srt(p) for lang, p in near.items() if p.is_file()}
            target = found.get(body.lang)
            return str(media), spans(target or found.get(None)), spans(target)
        return None, [], []

    @api.post("/voices/preview", response_class=FileResponse, responses={200: {"content": {"audio/wav": {}}}})
    async def preview_voice(body: Preview):
        """Reads text (or a sentence of the subtitles) in the voice the dub step would use.
        The text read is in the X-Preview-Text header, URL-encoded."""
        media, timing, cues = preview_files(body)
        if body.voice_id:
            ref_audio = voice_or_404(body.voice_id)["path"]
        elif body.source and body.start is not None and body.end is not None:
            ref_audio = str(window_clip(body.source, body.start, body.end))
        else:
            ref_audio = str(ctx.existing(body.ref_audio, extra=[ctx.voices])) if body.ref_audio else None
        result = await call("preview", {"voice": body.voice, "ref_audio": ref_audio, "int8": body.int8,
                                        "text": body.text, "media": media, "timing": timing, "cues": cues})
        return FileResponse(result["path"], media_type="audio/wav", headers={"X-Preview-Text": quote(result["text"])})

    return api
