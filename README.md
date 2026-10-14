
# Mục đích

Công cụ chuyển ngữ video

## Model sử dụng

- https://huggingface.co/openbmb/VoxCPM2
- https://huggingface.co/tencent/Hy-MT2-1.8B

## Công cụ tương tự

- https://github.com/liuzhao1225/YouDub-webui

# Cài đặt

## Cài đặt qua UV
```
# Cài đặt virtual environment
uv venv

# Active virtual environment
source .venv/bin/activate

uv pip install cactus-needle
uv pip install cactus-needle[mic]

uv run asr.py
```

## Chạy qua needle

```
pip install cactus-needle

needle download macos-arm64
needle download whistle

# Trích xuất audio trước
ffmpeg -i "/Users/tuan_da/100_Projects/AI Coding Crash Course/mp-aicodingcrashcourse/017 Hallucination _ AI Coding Crash Course.mp4" \
  -vn -acodec pcm_s16le \
  "017 Hallucination _ AI Coding Crash Course.wav"

./macos-arm64/needle --model whistle.cact --audio "017 Hallucination _ AI Coding Crash Course.wav"
```

# Công cụ sử dụng

- https://huggingface.co/Cactus-Compute/whistle
# Tạo subtitle hàng loạt (needle-whistle)

Không cần trích audio thủ công và không cần cài ffmpeg: PyAV (đã kèm sẵn thư viện FFmpeg trong wheel) decode video/audio (mp4, mkv, mov, mp3, m4a, wav…) sang 16 kHz mono cho model, ngay trong Python.

```
uv pip install av

.venv/bin/python batch-subtitle.py "<folder>"                 # .srt nằm cạnh từng file
.venv/bin/python batch-subtitle.py "<folder>" -r              # quét cả thư mục con
.venv/bin/python batch-subtitle.py "<folder>" --out subs      # ghi .srt vào thư mục khác
.venv/bin/python batch-subtitle.py "<folder>" --language en --overwrite
```

File đã có `.srt` sẽ được bỏ qua (trừ khi `--overwrite`), nên có thể chạy lại sau khi dừng giữa chừng.

# Dịch subtitle Anh → Việt (local)

Dùng model `vinai/vinai-translate-en2vi-v2` (chỉ EN→VI, ~420M tham số) chạy trên CTranslate2 int8, CPU — không cần Ollama hay server. Lần chạy đầu tự tải và convert model vào `models/` (~410 MB). Video ~9 phút dịch mất ~12s trên M1 Pro.

```
uv pip install ctranslate2 transformers sentencepiece protobuf

.venv/bin/python translate-subtitle.py file.srt                 # ghi file.vi.srt
.venv/bin/python translate-subtitle.py "<folder>" -r            # dịch mọi .srt trong folder
.venv/bin/python translate-subtitle.py "<folder>" --beam 1      # nhanh gấp ~2 lần, chất lượng thấp hơn chút

.venv/bin/python batch-subtitle.py "<folder>" --translate       # tạo .srt rồi dịch luôn ra .vi.srt
```

Câu bị chia qua nhiều cue sẽ được gộp lại để dịch, rồi chia bản dịch về các cue gốc theo độ dài, nên timing giữ nguyên. Hạn chế: thuật ngữ kỹ thuật cũng bị dịch (model → "mô hình") và văn phong kém hơn LLM. `--model opus` (Helsinki-NLP, 75M) nhanh hơn nhưng dịch sai nhiều, không khuyến nghị.

# Dịch subtitle bằng OpenAI API

Văn phong tự nhiên hơn model local và giữ nguyên thuật ngữ kỹ thuật tiếng Anh (model, prompt, API…). Mỗi cue được dịch riêng (LLM tự hiểu câu bị chia qua nhiều cue), gửi theo chunk 60 cue kèm vài cue trước làm ngữ cảnh, chạy song song. Timing và số cue giữ nguyên.

```
uv pip install openai
export OPENAI_API_KEY=sk-...
export OPENAI_MODEL=gpt-5-mini          # tuỳ chọn, mặc định gpt-5-mini

.venv/bin/python translate-subtitle.py file.srt --engine openai
.venv/bin/python translate-subtitle.py "<folder>" -r --engine openai --openai-model gpt-5 --workers 8

.venv/bin/python batch-subtitle.py "<folder>" --translate --engine openai
```

`OPENAI_BASE_URL` cũng được hỗ trợ để dùng endpoint tương thích OpenAI.


# Dịch subtitle bằng Claude Code (dùng limit gói Pro/Max)

Gọi CLI `claude -p` nên lượt dịch tính vào limit gói Claude (5 giờ/tuần, dùng chung với Claude Code bình thường), không tính tiền API. Cần cài Claude Code và đăng nhập bằng tài khoản claude.ai một lần (`claude`). Script tự bỏ `ANTHROPIC_API_KEY` khỏi env để không bị tính phí API, và tắt tool/MCP/skill/CLAUDE.md để mỗi lần gọi chỉ là một lượt dịch. Chunk 100 cue, tối đa 3 lần gọi song song. Video ~9 phút dịch mất ~26s với `sonnet`.

```
.venv/bin/python translate-subtitle.py file.srt --engine claude
.venv/bin/python translate-subtitle.py "<folder>" -r --engine claude --claude-model haiku   # tiết kiệm limit
.venv/bin/python batch-subtitle.py "<folder>" --translate --engine claude
```

Khi hết limit, file đó báo lỗi `claude failed: ...` và script chuyển sang file tiếp theo; chạy lại sau sẽ bỏ qua các file `.vi.srt` đã có.


# Lồng tiếng từ subtitle tiếng Việt (local)

Dùng [VieNeu-TTS v3 Turbo](https://github.com/pnnbao97/VieNeu-TTS) (Apache 2.0): TTS tiếng Việt chạy trên CPU qua ONNX Runtime (không cần PyTorch), output 48 kHz. Có 25 giọng sẵn và clone giọng từ clip mẫu 3–8s. Lần chạy đầu tự tải model từ Hugging Face. Video ~7 phút lồng tiếng mất ~2 phút trên M1 Pro (RTF ~0.3).

```
uv pip install vieneu "onnxruntime==1.23.2"   # onnxruntime ≥1.31 từ chối load model từ cache Hugging Face (lỗi "External data path escapes model directory")

.venv/bin/python dub-subtitle.py file.vi.srt                          # ghi file.vi.wav, giọng mặc định "Phạm Tuyên"
.venv/bin/python dub-subtitle.py --list-voices                        # xem các giọng có sẵn
.venv/bin/python dub-subtitle.py "<folder>" -r --voice "Ngọc Huyền"   # mọi .vi.srt trong folder
.venv/bin/python dub-subtitle.py file.vi.srt --ref-audio giong_mau.wav   # clone giọng từ clip 3–8s
.venv/bin/python dub-subtitle.py file.vi.srt --mux                    # + file.dub.mp4 từ file.mp4 cùng tên
.venv/bin/python dub-subtitle.py file.vi.srt --mux --original-volume 0   # thay hẳn audio gốc
.venv/bin/python dub-subtitle.py file.vi.srt --mux --add-track        # giữ audio gốc, thêm track lồng tiếng

.venv/bin/python batch-subtitle.py "<folder>" --dub --engine openai   # video → .srt → .vi.srt → .vi.wav
```

Các cue được gộp thành câu để đọc tự nhiên. Mỗi câu bắt đầu đúng lúc cue đầu tiên của nó. Câu tiếng Việt dài hơn slot thì được đọc tràn sang khoảng lặng phía sau. Nếu vẫn không đủ chỗ thì tăng tốc (không đổi cao độ), tối đa `--max-speed` (mặc định 1.3). Đến mức đó vẫn dài thì câu sau bị lùi lại; script in ra số câu bị lùi và độ trễ lớn nhất. `--mux` trộn giọng đọc lên audio gốc (mặc định giữ audio gốc ở mức 15%) và mặc định thay audio gốc; `--add-track` giữ nguyên audio gốc và thêm giọng lồng tiếng thành audio track mới (track mặc định, chọn lại track gốc trong player). `--mux` nhúng mọi `<name>.srt` / `<name>.<lang>.srt` tìm thấy cạnh video làm phụ đề (tiếng Việt là phụ đề mặc định). Audio từng câu được cache trong `~/.cache/dich-video/tts`, nên chạy lại với cùng giọng sẽ nhanh. `--int8` chạy nhanh hơn nhưng audio có thể bị méo. Audio có watermark AI (mặc định của VieNeu).

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