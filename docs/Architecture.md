# Kiến trúc pipeline (greenfield)

Nguồn → (Tách nền) → ASR → (Hiệu chỉnh) → Dịch ×N ngôn ngữ → Lồng tiếng ×N → Ghép ×N.

## 0. Phản biện trước khi thiết kế

| Yêu cầu | Ý kiến |
|---|---|
| "Finetune ASR" | Finetune = huấn luyện lại model (vòng đời khác hẳn: dataset, GPU, versioning model). Việc cần ở đây là **hiệu chỉnh transcript** (sửa từ, dấu câu, tên riêng, chia câu). Đặt tên stage là `refine`. Finetune thật nếu có sau này là một *công cụ ngoài pipeline* sinh ra model mới, ASR driver chỉ việc trỏ tới model đó. |
| ASR "Moondream" | Moondream là VLM (hình ảnh), không phải ASR. Trong `experiments/` thứ phiên âm là **Parakeet**. Driver ASR: `needle`, `parakeet`, `openai-whisper`… |
| Tách âm thanh nền "chưa cần" | Đồng ý chưa cài, nhưng nó **ảnh hưởng chất lượng nhiều nhất** của bản lồng tiếng: không tách thì hoặc mất nhạc nền, hoặc lẫn giọng gốc. Thiết kế sẵn 2 output `audio.speech` + `audio.background`; khi chưa có driver, `mux` dùng chiến lược "ducking" audio gốc. |
| Queue/workers | **Không** dùng Celery/Redis/RabbitMQ: app chạy local một máy, thêm hạ tầng = thêm thao tác cài đặt cho người dùng. Queue trên SQLite là đủ, đặt sau một interface để đổi sang Redis khi cần nhiều máy. |
| Lưu artifact mọi bước | Lưu hết thì WAV/video phình rất nhanh. Mỗi stage khai báo chính sách giữ (`keep`). |

## 1. Khái niệm

```
Job          một video nguồn + danh sách ngôn ngữ đích + template pipeline
 └─ Task     một stage cụ thể của job, có thể theo ngôn ngữ: asr, translate@vi, dub@vi, mux@vi
     └─ Attempt   mỗi lần chạy task (chạy lại = attempt mới), có span_id, log, artifact riêng
          └─ Artifact  file đầu ra có kiểu + slot + hash
Driver       cài đặt cụ thể của một stage (asr/needle, asr/openai, dub/neutts…)
Template     danh sách stage + driver + params mặc định (YAML), người dùng chọn khi tạo job
```

## 2. Đồ thị (DAG) theo slot, không theo thứ tự cứng

Mỗi stage khai báo **slot** nó đọc và ghi. Engine nối các task bằng slot, nên thêm/bỏ stage không phải sửa stage khác.

```
source      →  media.video, audio.original
separate    audio.original              →  audio.speech, audio.background      (tuỳ chọn)
asr         audio.speech|audio.original →  transcript.src
refine      transcript.src              →  transcript.src        (biến đổi tại chỗ, tuỳ chọn)
translate@L transcript.src              →  transcript.L
dub@L       transcript.L, audio.speech  →  audio.dub.L
mux@L       media.video, audio.dub.L, audio.background?, transcript.L  →  media.out.L
```

Quy tắc resolve:
- `a|b`: lấy slot đầu tiên có producer trong job (bỏ `separate` → `asr` đọc `audio.original`).
- Stage *transformer* (đọc và ghi cùng slot, như `refine`) được chèn vào giữa chuỗi; bỏ đi thì người tiêu thụ đọc thẳng bản trước.
- `@L` nhân bản task theo `job.target_langs`. Thêm ngôn ngữ cho job cũ = thêm task `translate@ja…`, dùng lại `asr` đã xong.
- Có thể thêm stage mới bất kỳ (VD `diarize → transcript.src.speakers`, `burn_subs`) chỉ bằng cách khai báo slot.

Template ví dụ:

```yaml
name: default
stages:
  - {stage: source,    driver: auto}            # upload | browse | url — chọn theo input
  - {stage: separate,  driver: demucs, enabled: false}
  - {stage: asr,       driver: needle}
  - {stage: refine,    driver: llm, enabled: false}
  - {stage: translate, driver: claude, per_lang: true}
  - {stage: dub,       driver: neutts, per_lang: true, gate: review}   # dừng chờ duyệt phụ đề
  - {stage: mux,       driver: ffmpeg, per_lang: true}
```

## 3. Driver / Adapter

```python
class Driver(Protocol):
    stage: ClassVar[str]            # "asr"
    name: ClassVar[str]             # "needle"
    version: ClassVar[str]          # đổi version → cache cũ không dùng lại
    Params: ClassVar[type[BaseModel]]   # pydantic → JSON Schema → UI tự vẽ form
    resources: ClassVar[set[str]]   # {"cpu"} | {"gpu"} | {"net"}
    consumes: ClassVar[dict[str, str]]  # {"audio": "audio.speech|audio.original"}
    produces: ClassVar[dict[str, str]]  # {"transcript": "transcript.src"}

    def load(self) -> None: ...     # nạp model một lần, worker giữ lại giữa các task
    def run(self, ctx: TaskContext, inputs: dict[str, Artifact], params: Params) -> dict[str, Path]: ...
```

- Đăng ký bằng decorator `@register` + Python entry points (`dich.drivers`) → driver bên thứ ba cài bằng `pip` là có.
- `GET /drivers` trả danh sách + JSON Schema params + driver có dùng được không (`available()`: thiếu API key, thiếu package) — UI ẩn/disable thay vì để người dùng chạy rồi mới lỗi.
- Driver **không** tự đọc file theo tên, không biết job/thư mục: chỉ nhận `inputs` và trả `outputs`. Dễ test, dễ thay.

Driver dự kiến:

| Stage | Driver |
|---|---|
| source | `upload`, `browse` (file local), `url` (yt-dlp) |
| separate | `demucs`, `uvr-mdx` (sau) |
| asr | `needle`, `parakeet`, `openai` |
| refine | `llm` (Claude/OpenAI: sửa chính tả, tên riêng theo glossary), `rules` (từ điển thay thế) |
| translate | `local` (ctranslate2), `openai`, `claude` |
| dub | `neutts`/`vieneu`, `openai-tts`… |
| mux | `ffmpeg` |

## 4. Định dạng dữ liệu chuẩn

Transcript nội bộ là **JSON**, không phải SRT. SRT/VTT/ASS chỉ là export.

```json
{"lang": "en", "cues": [
  {"id": "c_0007", "start": 12.34, "end": 15.10, "text": "...", "speaker": null,
   "words": [{"w": "Hello", "s": 12.34, "e": 12.70}], "src_id": null}
]}
```

- `id` ổn định qua các bước; `translate` ghi `src_id` → dịch lại *chỉ câu gốc đã đổi*, editor đối chiếu song song, dub biết câu nào cần đọc lại.
- Word timestamps đi kèm từ đầu → không còn file `.words.json` riêng.

## 5. Queue & workers

```
FastAPI (chỉ ghi/đọc DB, không chạy model)
   │ enqueue                       ▲ SSE: tiến độ, log người dùng
   ▼                               │
SQLite: jobs, tasks, attempts, artifacts, events
   ▲ claim (UPDATE … RETURNING, lease)
   │
Supervisor ── worker[cpu] ── worker[gpu] ── worker[net] ×N
                 └─ mỗi worker là một process riêng, giữ model đã load
```

- **Claim nguyên tử**: `UPDATE tasks SET status='running', lease_until=? WHERE id=(SELECT … WHERE status='queued' AND lane IN (…) AND deps_done ORDER BY priority, created_at LIMIT 1) RETURNING *`.
- **Lease + heartbeat**: worker chết (crash, OOM) → hết lease → task về `queued` (tối đa `max_retries`), không kẹt "running" mãi.
- **Process, không thread**: dừng = supervisor kill process rồi spawn lại → dừng được *giữa* bất kỳ bước nào (ASR, dịch, mux), và bắt được cả output tầng C (fd 1/2) của ffmpeg/onnx/ctranslate2.
- **Lane theo tài nguyên** (`cpu`, `gpu`, `net`), số worker mỗi lane cấu hình được. `net` có thể chạy 3–4 task song song.
- **Interface `Queue`** (`enqueue`, `claim`, `heartbeat`, `complete`, `fail`) → sau này đổi sang Redis/Postgres mà không đụng driver.
- Một lệnh duy nhất `dich serve` khởi động API + supervisor (người dùng không phải mở 2 terminal); `dich worker --lanes gpu` cho máy phụ khi cần.

Trạng thái task: `blocked → queued → running → done | failed | cancelled`, cộng `stale` (input đã đổi sau khi chạy) và `waiting_review` (gate).

## 6. Log: hai kênh tách biệt

| | Log pipeline (người dùng) | Log hệ thống (quản trị) |
|---|---|---|
| Nội dung | Sự kiện có cấu trúc: "Đang tải model", "Phiên âm 120/300 câu", "Dịch xong 42 câu, 2 câu quá dài" | stdout/stderr, logging của thư viện, traceback, output ffmpeg |
| Ghi bằng | `ctx.log.info(...)`, `ctx.progress(done, total)` | tự động (redirect fd trong worker process) |
| Lưu | `events.jsonl` của attempt + bảng `events` (để SSE) | `system.log` của attempt |
| Khi lỗi | "Không tải được video từ URL (mã lỗi `7f3a…`)" | traceback đầy đủ, gắn cùng mã |

- `trace_id` theo job, `span_id` theo attempt, định dạng W3C/OpenTelemetry → sau này export sang Grafana/Jaeger không phải đổi.
- Hai loại lỗi: `UserError("URL không hợp lệ")` → hiện nguyên văn; mọi exception khác → hiện thông báo chung + mã trace. Driver chủ động raise `UserError` cho lỗi người dùng tự sửa được.
- Trang admin: `GET /admin/traces/{trace_id}` gom system log của mọi attempt.

## 7. Artifact & cache

```
data/
  jobs.sqlite
  jobs/<job_id>/<task_key>/<attempt>/
      outputs/…          transcript.json, dub.wav, out.mp4
      events.jsonl
      system.log
  blobs/<sha256>          (tuỳ chọn: dedupe file lớn)
```

- Bảng `artifacts`: `slot`, `kind`, `path`, `sha256`, `size`, `attempt_id`, `origin` (`driver` | `human`).
- **Fingerprint** task = hash(driver.name, driver.version, params, hash các input). Trùng fingerprint với attempt đã `done` → dùng lại, không chạy (thay cho "bỏ qua nếu output đã có" theo tên file).
- `keep` theo stage: `always` (transcript, out.mp4), `last` (chỉ attempt mới nhất: dub.wav), `success_only`, `none` (wav trích xuất tạm).
- **Export**: kết quả cuối được copy/hardlink ra cạnh file gốc hoặc thư mục người dùng chọn — giữ thói quen "thấy ngay trong Finder" mà workspace vẫn sạch và có version.

## 8. Chạy lại & sửa tay

- `POST /tasks/{id}/rerun {mode: "only" | "cascade", params?}`
  - `only`: chạy lại task này, task phía sau thành `stale` (người dùng quyết định khi nào chạy tiếp).
  - `cascade`: chạy lại và xếp hàng mọi task phía sau.
- Sửa phụ đề trong editor = tạo artifact mới `origin=human` cho slot đó (một attempt "thủ công") → phía sau thành `stale`, có nút "Cập nhật phía sau". Không bao giờ ghi đè bản sửa tay khi chạy lại task trước nó mà không hỏi.
- **Gate** (`gate: review`): job dừng ở `waiting_review` trước stage đó (VD trước `dub` để duyệt bản dịch / nghe thử giọng), một nút "Tiếp tục".
- Thêm ngôn ngữ, đổi driver một stage, bật `separate` cho job cũ: đều là thêm/thay task trong DAG rồi resolve lại — cache giữ phần không đổi.

## 9. Cấu trúc mã

```
dich/
  core/       models.py (Job, Task, Attempt, Artifact), dag.py (resolve slot), template.py
  queue/      base.py (Queue), sqlite.py
  runtime/    supervisor.py, worker.py, context.py (TaskContext: log, progress, cancel, tmpdir)
  logging/    events.py, system.py, trace.py
  storage/    artifacts.py, export.py
  formats/    transcript.py (JSON ↔ SRT/VTT/ASS)
  drivers/    source/ separate/ asr/ refine/ translate/ dub/ mux/
  api/        FastAPI routers: jobs, tasks, artifacts, drivers, events(SSE), admin
  cli.py      dich serve | dich run <file> (chạy đồng bộ, cùng engine) | dich worker
templates/    default.yaml, subs-only.yaml (không dub), dub-only.yaml
```

CLI và API dùng chung engine: `dich run video.mp4 --to vi,ja` = tạo job + chạy worker trong process, in log pipeline ra terminal.

## 10. Lộ trình từ mã hiện tại

1. **Formats + drivers**: tách `dich.Steps`, `translation.py`, `tts.py` thành driver theo interface trên; transcript JSON có `id`. Giữ nguyên `pipeline.py`.
2. **DAG theo slot + template**: thay `plan_steps` tuyến tính; thêm `@lang`.
3. **Worker process + lease**: thay thread lanes; dừng giữa bước, log tầng C.
4. **Hai kênh log + trace_id**: thay `steplog.py` tee stdout.
5. **Artifact store + fingerprint + export**.
6. Stage mới: `separate` (demucs), `refine` (llm + glossary), gate review.

Mỗi bước đều chạy được độc lập, test hiện có (`tests/`) làm lưới an toàn.
