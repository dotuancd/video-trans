
# Mục đích

Công cụ chuyển ngữ video

## Model sử dụng

- https://github.com/snakers4/silero-vad (Voice Activity Detection)
- https://huggingface.co/openbmb/VoxCPM2
- https://huggingface.co/tencent/Hy-MT2-1.8B

## Công cụ tương tự

- https://github.com/liuzhao1225/YouDub-webui

# Cài đặt

```
uv sync --extra ui --extra local --extra openai --extra tts --extra url   # hoặc --all-extras
```

Nhóm phụ thuộc: `local` (dịch bằng model local), `openai` (dịch bằng OpenAI API), `tts` (lồng tiếng), `ui` (giao diện web), `url` (tải video từ URL bằng yt-dlp), `experiments` (moondream). Ghép video cần `ffmpeg` (`brew install ffmpeg`). `uv run dich drivers` cho biết driver nào dùng được trên máy.

# Sử dụng

Kiến trúc: xem `docs/Architecture.md`. Mỗi video là một **việc** (job) gồm các **bước** (task) nối với nhau theo dữ liệu: Nguồn → Phụ đề → (Hiệu chỉnh) → Dịch ×N ngôn ngữ → Lồng tiếng ×N → Ghép video ×N. Bước nào có cùng đầu vào + tham số với một lần chạy trước sẽ **dùng lại kết quả** (cache theo nội dung), nên chạy lại luôn rẻ. Kết quả được xuất ra cạnh file gốc: `<tên>.srt`, `<tên>.vi.srt`, `<tên>.vi.wav`, `<tên>.dub.vi.mp4`.

## Dòng lệnh

```
uv run dich video.mp4                          # phụ đề + dịch sang tiếng Việt (template subs-only)
uv run dich "<folder>" -r --to vi,ja -e claude # cả thư mục, 2 ngôn ngữ, dịch bằng Claude Code
uv run dich video.mp4 --dub                    # + lồng tiếng + ghép video (template default)
uv run dich "https://youtu.be/…" --dub         # tải từ URL
uv run dich video.mp4 --use-srt --dub          # dùng video.srt / video.vi.srt có sẵn, chỉ lồng tiếng
uv run dich video.mp4 --dub --voice "Ngọc Huyền" --set dub.max_speed=1.5 --set mux.add_track=true
uv run dich templates | drivers | voices
```

`--set stage.param=value` đặt tham số bất kỳ của driver (`--set refine.enabled=true`, `--set asr.driver=…`). Việc tạo từ CLI cũng hiện trong giao diện web (chung database).

## Giao diện web

```
uv run dich serve                   # API + workers, mở http://127.0.0.1:7860
uv run dich serve --root ~/Videos   # chỉ cho UI đọc/ghi trong thư mục này (mặc định: ~)
uv run dich worker --lanes cpu      # (tuỳ chọn) thêm worker ở terminal khác
```

- **Việc mới**: nguồn từ **Tải lên** (nhiều file; `a.srt`/`a.vi.srt` tải cùng `a.mp4` được dùng luôn), **Trên máy** (file hoặc thư mục) hoặc **URL**. Chọn việc cần làm (template), các ngôn ngữ đích, driver dịch, giọng đọc (mặc định clone giọng gốc; ▶ nghe thử). Tham số của mọi driver nằm trong ngăn "Nâng cao" (tab theo bước, form tự sinh từ driver); lưu thành **preset** để lần sau chọn 1 chạm ở ô "Cấu hình" (lưu ở `<data>/presets.json`).
- **Danh sách việc**: ô tìm (không dấu, theo tên hoặc file/URL nguồn; phím `/`), chip lọc Cần xử lý / Đang chạy / Đã dừng / Xong (kèm số), sắp xếp Mới tạo / Cũ nhất / Tên A→Z / Cập nhật gần nhất / Chi phí; tải 50 việc mỗi lần khi cuộn. Tiến độ cập nhật tại chỗ; khi thứ tự/bộ lọc không còn đúng sẽ hiện nút **Làm mới**. Tick chọn tất cả rồi "Chọn tất cả N việc khớp bộ lọc" để thao tác hàng loạt trên toàn bộ. API: `GET /api/jobs?project=&status=&q=&sort=&cursor=`, `/api/jobs/counts`, `/api/jobs/ids`.
- **Chi tiết việc**: từng bước với trạng thái, tiến độ, **Log** theo từng lần chạy (log pipeline; "Log hệ thống" cho lỗi kỹ thuật), **Chạy lại…** (đổi driver/tham số; chỉ bước này hoặc cả các bước sau), **Dừng** (dừng được giữa bất kỳ bước nào), **Tiếp tục**, **+ Ngôn ngữ** (dịch thêm, dùng lại phụ đề đã có), tải kết quả về.
- **Sửa phụ đề**: song ngữ ngay dưới video, phụ đề trên video theo bản đang gõ; tách/gộp/chuyển chữ. Lưu = bản sửa tay mới; các bước sau thành "Cần chạy lại" → **Cập nhật phía sau**. Chạy lại một bước sẽ hỏi trước khi thay bản đã sửa tay.
- Template **Lồng tiếng, duyệt bản dịch trước** dừng trước bước lồng tiếng để bạn sửa bản dịch/nghe thử giọng rồi bấm Tiếp tục.

Dữ liệu ở `~/.local/share/dich-video/` (`dich.sqlite`, `jobs/<id>/<bước>/<lần>/` chứa outputs, `events.jsonl`, `system.log`; `uploads/`). Worker là process riêng theo lane (`cpu`, `net`, `interactive`); worker chết thì việc tự chạy lại (tối đa 2 lần). Số worker mỗi lane đổi bằng nút +/− ở trang Hàng đợi, không cần khởi động lại. Lỗi hệ thống hiện mã ngắn; xem chi tiết tại `/api/admin/traces/<mã>`.

### Phát triển

React 19 + Vite + Tailwind 4 + shadcn/ui (`web/`) ⇄ FastAPI (`dich/api/`, REST + SSE) ⇄ SQLite queue (`dich/queue/`) ⇄ worker processes (`dich/runtime/`) ⇄ drivers (`dich/drivers/<stage>/`). Driver mới: subclass `dich.drivers.Driver` với `@register`, khai báo `consumes`/`produces` theo slot; plugin ngoài đăng ký qua entry point `dich.drivers`.

```
uv run dich serve --no-browser      # API ở :7860
cd web && npm install && npm run dev  # UI ở :5173, proxy /api sang :7860
npm run api                         # sau khi đổi API: sinh lại client TypeScript (src/api)
npm run build                       # cập nhật web/dist
uv run pytest
```

# Tạo subtitle (needle-whistle)

Model: https://huggingface.co/Cactus-Compute/whistle. Không cần trích audio thủ công: PyAV (đã kèm sẵn thư viện FFmpeg trong wheel) decode video/audio (mp4, mkv, mov, mp3, m4a, wav…) sang 16 kHz mono cho model, ngay trong Python. File `<name>.dub.*` do tool tạo ra không bị coi là media đầu vào khi quét thư mục.

```
uv run dich "<folder>" -r --template transcribe
```

Chạy trực tiếp binary needle (không cần Python):

```
needle download macos-arm64
needle download whistle
ffmpeg -i video.mp4 -vn -acodec pcm_s16le audio.wav
./macos-arm64/needle --model whistle.cact --audio audio.wav
```

# Dịch subtitle Anh → Việt

Chọn driver dịch bằng `-e/--engine` (CLI) hoặc "Dịch bằng" (UI).

## Local (`-e local`, mặc định)

Dùng model `vinai/vinai-translate-en2vi-v2` (chỉ EN→VI, ~420M tham số) chạy trên CTranslate2 int8, CPU — không cần Ollama hay server. Lần chạy đầu tự tải và convert model vào `models/` (~410 MB). Video ~9 phút dịch mất ~12s trên M1 Pro. `--beam 1` nhanh gấp ~2 lần, chất lượng thấp hơn chút.

Câu bị chia qua nhiều cue sẽ được gộp lại để dịch, rồi chia bản dịch về các cue gốc theo độ dài, nên timing giữ nguyên. Hạn chế: thuật ngữ kỹ thuật cũng bị dịch (model → "mô hình") và văn phong kém hơn LLM. `--model opus` (Helsinki-NLP, 75M) nhanh hơn nhưng dịch sai nhiều, không khuyến nghị.

## OpenAI API (`-e openai`)

Văn phong tự nhiên hơn model local và giữ nguyên thuật ngữ kỹ thuật tiếng Anh (model, prompt, API…). Mỗi cue được dịch riêng (LLM tự hiểu câu bị chia qua nhiều cue), gửi theo chunk 60 cue kèm vài cue trước làm ngữ cảnh, chạy song song (`--workers`, mặc định 4). Timing và số cue giữ nguyên.

Cấu hình (API key, engine/giọng mặc định, cổng UI, thư mục dữ liệu…) đặt trong `.env` ở thư mục repo — tạo từ mẫu: `cp .env.example .env`. File tự được load, không ghi đè biến môi trường đã có; flag CLI luôn được ưu tiên. Tối thiểu cho engine OpenAI:

```
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-5-mini        # tuỳ chọn, hoặc --openai-model gpt-5
OPENAI_BASE_URL=...            # tuỳ chọn, endpoint tương thích OpenAI
```

## Claude Code (`-e claude`, dùng limit gói Pro/Max)

Gọi CLI `claude -p` nên lượt dịch tính vào limit gói Claude (5 giờ/tuần, dùng chung với Claude Code bình thường), không tính tiền API. Cần cài Claude Code và đăng nhập bằng tài khoản claude.ai một lần (`claude`). Script tự bỏ `ANTHROPIC_API_KEY` khỏi env để không bị tính phí API, và tắt tool/MCP/skill/CLAUDE.md để mỗi lần gọi chỉ là một lượt dịch. Chunk 100 cue, tối đa 3 lần gọi song song. Video ~9 phút dịch mất ~26s với `sonnet`; `--claude-model haiku` để tiết kiệm limit.

Khi hết limit, bước Dịch báo lỗi "Claude Code báo lỗi: …"; bấm Thử lại sau (các bước đã xong được dùng lại).

# Lồng tiếng từ subtitle tiếng Việt (local)

Dùng [VieNeu-TTS v3 Turbo](https://github.com/pnnbao97/VieNeu-TTS) (Apache 2.0): TTS tiếng Việt chạy trên CPU qua ONNX Runtime (không cần PyTorch), output 48 kHz. Mặc định (giọng `source`) clone giọng người nói trong chính video: lấy đoạn nói liền 3–8s dài nhất theo timing phụ đề (bỏ qua 10% đầu nếu có đoạn tương đương), cắt vào `~/.cache/dich-video/ref`; không có video thì dùng giọng mặc định (`DICH_VOICE`). Ngoài ra có 25 giọng sẵn (`dich voices`) và clone từ clip mẫu tự chọn (`--ref-audio`). Video nhiều người nói chỉ clone được một giọng. Lần chạy đầu tự tải model từ Hugging Face. Video ~7 phút lồng tiếng mất ~2 phút trên M1 Pro (RTF ~0.3). `onnxruntime` được ghim ở 1.23.2 vì bản ≥1.31 từ chối load model từ cache Hugging Face.

```
uv run dich file.mp4 --dub                                 # clone giọng trong video
uv run dich "<folder>" -r --dub --voice "Ngọc Huyền"
uv run dich file.mp4 --dub --ref-audio giong_mau.wav       # clone giọng từ clip
uv run dich file.mp4 --dub --set mux.original_volume=0     # chỉ giọng lồng tiếng, bỏ audio gốc
uv run dich file.mp4 --dub --set mux.add_track=true        # giữ audio gốc, thêm track lồng tiếng
```

Các cue được gộp thành câu để đọc tự nhiên. Mỗi câu bắt đầu đúng lúc cue đầu tiên của nó. Câu tiếng Việt dài hơn slot thì được đọc tràn sang khoảng lặng phía sau. Nếu vẫn không đủ chỗ thì tăng tốc (không đổi cao độ), tối đa `max_speed` (mặc định 1.3). Đến mức đó vẫn dài thì câu sau bị lùi lại; log báo số câu bị lùi và độ trễ lớn nhất. Ngược lại, câu Việt ngắn hơn 90% đoạn nói gốc thì được đọc chậm lại cho vừa đoạn đó, chậm nhất `min_speed` (mặc định 0.85, đặt 1 để tắt).

Bước Ghép video trộn giọng đọc lên audio gốc (mặc định giữ audio gốc ở mức 15%; khi có bước Tách nền thì dùng nhạc nền đã tách) và mặc định thay audio gốc; `add_track` giữ nguyên audio gốc và thêm giọng lồng tiếng thành audio track mới. Phụ đề bản dịch (mặc định) và phụ đề gốc được nhúng vào video. Audio từng câu được cache trong `~/.cache/dich-video/tts`, nên chạy lại với cùng giọng sẽ nhanh. `int8` chạy nhanh hơn nhưng audio có thể bị méo. Audio có watermark AI (mặc định của VieNeu).

Hạn chế: thuật ngữ tiếng Anh trong bản dịch (model, prompt, API…) được đọc theo kiểu Việt hoá.

Phương án thay thế nếu cần:
- **VieNeu v3 Nano**: 48M tham số, 24 kHz, nhẹ hơn nhưng kém hơn.
- **ViZipVoice**: clone giọng, Apache 2.0.
- **F5-TTS Vietnamese (EraX)**: clone giọng tốt, nhưng license phi thương mại.
- **Piper `vi_VN`**: giọng cố định, cực nhẹ.

## Nhúng subtitle vào video

```
 ffmpeg -i "021 Intro _ AI Coding Crash Course.mp4" \
 -i "021 Intro _ AI Coding Crash Course.vi.srt" -i "021 Intro _ AI Coding Crash Course.srt" \
   -map 0:v -map 0:a -map 1 -map 2 -c:v copy -c:a copy \
   -c:s mov_text \
   -metadata:s:s:0 language=vie -metadata:s:s:0 title="Vietnamese" \
   -metadata:s:s:1 language=eng -metadata:s:s:1 title=English \
   -disposition:s:0 default -disposition:s:1 0 \
   021_subs.mp4
```