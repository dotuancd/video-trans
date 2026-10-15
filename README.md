
# Mục đích

Công cụ chuyển ngữ video

## Model sử dụng

- https://huggingface.co/openbmb/VoxCPM2
- https://huggingface.co/tencent/Hy-MT2-1.8B

## Công cụ tương tự

- https://github.com/liuzhao1225/YouDub-webui

# Cài đặt

```
uv sync --all-extras        # hoặc chỉ chọn: uv sync --extra local --extra openai --extra tts
```

Nhóm phụ thuộc: `local` (dịch bằng model local), `openai` (dịch bằng OpenAI API), `tts` (lồng tiếng), `ui` (giao diện web), `experiments` (moondream, cho `experiments/`). `--mux` cần thêm `ffmpeg` (`brew install ffmpeg`).

# Sử dụng

Mọi thứ chạy qua một lệnh `dich`. Lệnh nào cũng bỏ qua output đã có (trừ khi `--overwrite`), nên có thể chạy lại sau khi dừng giữa chừng. Option chung: `-r` quét cả thư mục con, `--out DIR` ghi output vào thư mục khác (giữ cấu trúc thư mục), `--overwrite`.

```
uv run dich "<folder>"                        # video/audio → .srt → .vi.srt (dịch local)
uv run dich "<folder>" -r -e openai           # dịch bằng OpenAI
uv run dich "<folder>" --dub --mux            # + .vi.wav lồng tiếng + <name>.dub.mp4

uv run dich sub "<folder>" --language en      # chỉ tạo .srt
uv run dich translate file.srt -e claude      # .srt → .vi.srt
uv run dich dub file.vi.srt --voice "Ngọc Huyền"   # .vi.srt → .vi.wav (mặc định clone giọng trong video)
uv run dich voices                            # danh sách giọng có sẵn
```

`dich <path>` là viết tắt của `dich run <path>`. Xem toàn bộ option: `uv run dich run --help`.

## Giao diện web

```
uv sync --extra ui --extra local --extra tts
uv run dich ui                      # mở http://127.0.0.1:7860 trong trình duyệt
uv run dich ui --root ~/Videos      # chỉ cho UI đọc/ghi trong thư mục này (mặc định: ~)
```

- **Việc mới**: chọn video / `.srt` / `.vi.srt` hoặc cả thư mục ("Duyệt"), chọn bước cuối cần làm (Phụ đề → Dịch → Lồng tiếng → Ghép video), engine và giọng (mặc định **Giọng gốc**: tự clone người nói trong video; hoặc chọn giọng có sẵn, nút ⬆ clone từ clip 3–8s tự chọn), bấm Bắt đầu. Lựa chọn được nhớ cho lần sau.
- **Danh sách việc**: mỗi file một việc, mỗi bước có trạng thái riêng (Đang chờ / Đang chạy % / Xong / Lỗi / Cần chạy lại), cập nhật trực tiếp.
- **Chi tiết việc**: bước lỗi có nút **Thử lại**; bước nào cũng **Chạy lại…** được với tuỳ chọn khác (vd. đổi engine dịch, đổi giọng) — các bước sau tự chạy lại, hoặc tắt để chỉ đánh dấu "Cần chạy lại". Video nằm ngay trên bảng sửa phụ đề song ngữ: phụ đề trên video hiện luôn bản đang gõ (chưa cần lưu), câu đang phát được tô sáng và tự cuộn tới; bấm mốc thời gian để phát từ đó, chuyển qua lại **Bản lồng tiếng / Bản gốc** mà giữ nguyên vị trí. Lưu xong bấm "Lồng tiếng lại". Trong **Chạy lại…** của bước Lồng tiếng, nút ▶ đọc thử một câu của chính bản dịch — với Giọng gốc là câu ở đúng đoạn được lấy làm mẫu, để so với bản gốc.

Trạng thái lưu ở `~/.local/share/dich-video/jobs.sqlite` nên tắt/mở lại server không mất danh sách; bước đang chạy dở khi tắt sẽ thành Lỗi và Thử lại được. Một worker chạy lần lượt từng bước (model nặng, dùng chung CPU). UI chỉ lắng nghe trên 127.0.0.1, dùng chung code pipeline với CLI (`dich.plan_steps` / `dich.Steps`).

### Phát triển giao diện

Kiến trúc: React 19 + Vite + Tailwind 4 + shadcn/ui (`web/`) ⇄ FastAPI (`dich_server/api.py`, REST + Server-Sent Events) ⇄ pipeline SQLite (`dich_server/pipeline.py`). `web/dist` đã build sẵn nên người dùng không cần Node.

```
uv run dich ui --no-browser         # API ở :7860
cd web && npm install && npm run dev  # UI ở :5173, proxy /api sang :7860
npm run api                         # sau khi đổi API: sinh lại client TypeScript (src/api) từ OpenAPI
npm run build                       # cập nhật web/dist
uv run pytest                       # test pipeline + API
```

# Tạo subtitle (needle-whistle)

Model: https://huggingface.co/Cactus-Compute/whistle. Không cần trích audio thủ công: PyAV (đã kèm sẵn thư viện FFmpeg trong wheel) decode video/audio (mp4, mkv, mov, mp3, m4a, wav…) sang 16 kHz mono cho model, ngay trong Python. File `<name>.vi.wav` và `<name>.dub.*` do tool tạo ra không bị coi là media đầu vào.

```
uv run dich sub "<folder>" -r --out subs
```

Chạy trực tiếp binary needle (không cần Python):

```
needle download macos-arm64
needle download whistle
ffmpeg -i video.mp4 -vn -acodec pcm_s16le audio.wav
./macos-arm64/needle --model whistle.cact --audio audio.wav
```

# Dịch subtitle Anh → Việt

Chọn engine bằng `-e/--engine` (cho `dich run` và `dich translate`).

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

Khi hết limit, file đó báo lỗi `claude failed: ...` và script chuyển sang file tiếp theo; chạy lại sau sẽ bỏ qua các file `.vi.srt` đã có.

# Lồng tiếng từ subtitle tiếng Việt (local)

Dùng [VieNeu-TTS v3 Turbo](https://github.com/pnnbao97/VieNeu-TTS) (Apache 2.0): TTS tiếng Việt chạy trên CPU qua ONNX Runtime (không cần PyTorch), output 48 kHz. Mặc định (`--voice source`) clone giọng người nói trong chính video: lấy đoạn nói liền 3–8s dài nhất theo timing phụ đề (bỏ qua 10% đầu nếu có đoạn tương đương), cắt vào `~/.cache/dich-video/ref`; không tìm thấy video cạnh `.vi.srt` thì dùng giọng mặc định (`DICH_VOICE`). Ngoài ra có 25 giọng sẵn (`dich voices`) và clone từ clip mẫu tự chọn (`--ref-audio`). Video nhiều người nói chỉ clone được một giọng. Lần chạy đầu tự tải model từ Hugging Face. Video ~7 phút lồng tiếng mất ~2 phút trên M1 Pro (RTF ~0.3). `onnxruntime` được ghim ở 1.23.2 vì bản ≥1.31 từ chối load model từ cache Hugging Face.

```
uv run dich dub file.vi.srt                               # ghi file.vi.wav, clone giọng trong file.mp4 cạnh đó
uv run dich dub "<folder>" -r --voice "Ngọc Huyền"
uv run dich dub file.vi.srt --ref-audio giong_mau.wav     # clone giọng
uv run dich dub file.vi.srt --mux                         # + file.dub.mp4 từ file.mp4 cùng tên
uv run dich dub file.vi.srt --mux --original-volume 0     # chỉ giọng lồng tiếng, bỏ audio gốc
uv run dich dub file.vi.srt --mux --add-track             # giữ audio gốc, thêm track lồng tiếng
```

Các cue được gộp thành câu để đọc tự nhiên. Mỗi câu bắt đầu đúng lúc cue đầu tiên của nó. Câu tiếng Việt dài hơn slot thì được đọc tràn sang khoảng lặng phía sau. Nếu vẫn không đủ chỗ thì tăng tốc (không đổi cao độ), tối đa `--max-speed` (mặc định 1.3). Đến mức đó vẫn dài thì câu sau bị lùi lại; script in ra số câu bị lùi và độ trễ lớn nhất.

`--mux` trộn giọng đọc lên audio gốc (mặc định giữ audio gốc ở mức 15%) và mặc định thay audio gốc; `--add-track` giữ nguyên audio gốc và thêm giọng lồng tiếng thành audio track mới (track mặc định, chọn lại track gốc trong player). `--mux` nhúng mọi `<name>.srt` / `<name>.<lang>.srt` tìm thấy cạnh file `.vi.wav` làm phụ đề (tiếng Việt là phụ đề mặc định). Audio từng câu được cache trong `~/.cache/dich-video/tts`, nên chạy lại với cùng giọng sẽ nhanh. `--int8` chạy nhanh hơn nhưng audio có thể bị méo. Audio có watermark AI (mặc định của VieNeu).

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