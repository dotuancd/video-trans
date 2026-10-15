# Improvement Ideas

## Web UI / pipeline

- ~~**Tiến độ cho bước Phụ đề và Dịch**~~: bước Dịch đã báo % (local: mỗi 64 câu, LLM: mỗi chunk) và dừng được giữa chừng. Bước Phụ đề giữ một lần gọi: chia audio làm transcript lệch 3–9% từ và mất dấu kết câu; needle chạy ~70× thời gian thực nên không cần.
- ~~**Dừng giữa bước**~~ (đã làm trong pipeline v2): Dừng chỉ ngắt được bước Lồng tiếng (kiểm tra theo câu); Phụ đề/Dịch/Ghép chạy hết bước rồi mới dừng. Có thể chạy các bước này trong subprocess để kill được.
- ~~**Chạy song song theo loại tài nguyên**~~: hai lane `cpu` / `net`, bước Dịch dùng OpenAI/Claude chạy cùng lúc với Phụ đề/Lồng tiếng/Ghép của job khác.
- ~~**Lane theo GPU/MPS**~~ (đã làm trong pipeline v2): nếu sau này needle hoặc TTS chạy trên GPU, có thể tách thêm lane `gpu` để Phụ đề chạy song song với dịch local.
- ~~**Xem trước video gốc + phụ đề khi sửa**~~: player luôn hiện trên editor, phụ đề theo bản nháp, highlight + tự cuộn câu đang phát, chuyển Bản lồng tiếng/Bản gốc.
- **Phím tắt khi sửa phụ đề**: `Ctrl/Cmd+S` lưu, `Alt+↑/↓` sang câu trước/sau và seek theo, `Ctrl+Space` phát/dừng — sửa cả video mà không rời bàn phím.
- **Phụ đề trên player khi phát `.mkv`/audio**: audio-only chỉ có highlight, chưa có khung chữ; có thể vẽ overlay riêng.
- **Cảnh báo câu quá dài** trong editor (dựa trên thống kê `sped_up`/`late` của bước dub) để người dùng rút gọn bản dịch.
- **.mkv không phát được trong trình duyệt**: tạo bản preview mp4 nhẹ hoặc gợi ý dùng `.mp4` làm output.
- **Đóng gói**: build `web/dist` trong CI / hook pre-commit để không quên `npm run build` khi sửa UI.
- **Trang Cài đặt trong UI**: hiển thị/sửa các giá trị `.env` (engine, giọng mặc định, API key đã đặt hay chưa) để người dùng không phải mở file.
- **Proxy dev đọc `DICH_UI_PORT`**: `web/vite.config.ts` đang cố định cổng 7860.
- **Chọn người nói để clone**: video nhiều người nói, giọng gốc chỉ lấy người trong đoạn 3–8s được chọn. Có thể dùng diarization để clone từng người, hoặc cho chọn đoạn mẫu ngay trên player.
- **Nghe lại clip mẫu giọng gốc** trong UI (đoạn 3–8s đã cắt) cạnh bản đọc thử, để so trực tiếp.
- ~~**Dừng trước bước Lồng tiếng**~~ (đã làm trong pipeline v2): tuỳ chọn để job chờ người dùng nghe thử/chọn giọng rồi mới lồng tiếng cả video (hiện pipeline chạy thẳng, chỉ đổi giọng được khi Chạy lại).
- **Clone chéo ngôn ngữ**: `tts.REF_USE_CODES` quyết định có đưa acoustic codes của clip tiếng Anh vào hay chỉ speaker embedding; cần nghe so sánh trên vài video để chốt, hoặc đưa ra UI.

- **Nghe thử giọng gốc trước khi dịch:** khi video chưa có .vi.srt, preview đọc câu mẫu cố định. Có thể dịch nhanh 1 câu (câu trùng đoạn clone) bằng engine đã chọn để nghe thử đúng nội dung video.
- **Nghe thử giọng gốc khi chưa có .srt:** hiện cần .srt để chọn đoạn clone 3–8 giây. Có thể dùng VAD (silero) trên audio để chọn đoạn nói mà không cần phiên âm trước.

- ~~**Log cho thread con của bước dịch:**~~ (đã làm trong pipeline v2) `llm_translation.py` gọi OpenAI/Claude qua `ThreadPoolExecutor`, nên những gì các thread này in ra/log chưa vào log của bước. Có thể bọc các task bằng `steplog` (gắn writer của thread cha) để thấy lỗi/retry của từng batch.
- ~~**Output tầng C (ffmpeg trong `load_audio`, ctranslate2, onnx):**~~ (đã làm trong pipeline v2) đi thẳng ra fd 2 nên không vào log. Với ffmpeg, có thể chuyển sang `stderr=PIPE` giống `mux`.

- **Dịch lại chỉ câu gốc đã sửa:** sửa phụ đề gốc rồi "Dịch lại" sẽ dịch cả file và ghi đè các câu tiếng Việt đã sửa tay. Có thể chỉ dịch lại các nhóm câu có câu gốc đổi (so với bản `.srt` lúc dịch), giữ nguyên phần còn lại.
- **Sửa phụ đề gốc khi đang dịch:** editor cho sửa `.srt` ngay sau bước Phụ đề, nhưng nếu bước Dịch đang chạy thì nó dùng bản cũ. Có thể khoá cột gốc hoặc báo "Dịch lại" khi bước Dịch xong.

- **Dọn thư mục upload:** xoá job không xoá `storage/uploads/<id>/`. Có thể hỏi "Xoá cả file" khi xoá job tải lên, hoặc dọn upload cũ hơn N ngày.
- ~~**Tải kết quả về:**~~ (đã làm trong pipeline v2) job từ file tải lên chỉ có "Mở trong Finder"; thêm nút tải `.vi.srt` / `.dub.mp4` (cần khi mở UI từ máy khác).
- **Upload tiếp tục khi đứt:** video rất lớn đứt giữa chừng phải tải lại từ đầu; có thể chia chunk + `Content-Range`.


- **Báo lỗi bộ đệm cho needle:** `needle.Whistle` cắt JSON ở `buffer_size` (256 KB) mà không báo lỗi; `dich.Steps.transcribe` đang cấp 1 KB/giây audio. Nên báo upstream để thư viện trả về kích thước cần hoặc tự nới bộ đệm.

- ~~**Thư mục riêng cho job trên máy chủ**~~ (đã làm trong pipeline v2): Output cạnh file gốc là chủ đích (thấy ngay trong Finder, CLI/UI như nhau, chạy lại bỏ qua output đã có; mux/editor/nghe thử tìm file theo tên cạnh nhau). Cần tách thì dùng ô "Thư mục ra" (`out`).
- ~~**Lưu word timestamps khi phiên âm**~~: bước Phụ đề ghi `<stem>.words.json`; editor tách/chuyển chữ ở cột gốc đặt ranh giới đúng khoảng lặng giữa hai từ. Câu gốc đã sửa (số từ khác bản phiên âm) hoặc cắt giữa từ thì quay về ước lượng theo ký tự.
- ~~**Hiện word timestamps cho job cũ:**~~ (đã làm trong pipeline v2) job phiên âm trước khi có `.words.json` phải chạy lại bước Phụ đề (ghi đè `.srt` đã sửa). Có thể thêm lệnh chỉ ghi `.words.json` mà giữ `.srt`.
- **Hoàn tác thống nhất:** nút "Hoàn tác" chỉ lùi thao tác gộp/tách/chuyển chữ (gõ chữ dùng ⌘Z của ô nhập); một lịch sử chung cho cả hai sẽ ít bất ngờ hơn.
- ~~**Tham số ngắt câu trên UI:**~~ (đã làm trong pipeline v2) `MAX_CHARS`, `MIN_CHARS`, `MAX_DURATION` trong `subtitle.py` có thể đưa vào tham số bước Phụ đề để chỉnh theo kiểu video (vlog nói nhanh vs. bài giảng).

## Kiến trúc (xem `docs/Architecture.md`)

- **Glossary theo job/dự án:** danh sách tên riêng/thuật ngữ dùng chung cho `refine` (sửa ASR) và `translate` (dịch nhất quán), sửa được trong UI.
- ~~**Template pipeline chọn khi tạo job:**~~ (đã làm trong pipeline v2) "Chỉ phụ đề", "Phụ đề + dịch", "Lồng tiếng đầy đủ" — người dùng không phải bật/tắt từng bước.
- **Ước lượng thời gian/chi phí trước khi chạy:** dựa trên thời lượng video và lịch sử attempt (tốc độ driver, số token LLM).

## Sau pipeline v2

- **Dịch lại chỉ câu gốc đã đổi:** transcript đã có `src_id`; driver dịch có thể nhận bản dịch trước (kể cả bản sửa tay) và chỉ gửi các cue có câu gốc khác — vừa nhanh vừa không ghi đè bản sửa tay.
- **Driver `separate` (demucs) và `refine` (LLM + glossary):** slot đã sẵn (`audio.speech`, `audio.background`, `transcript.src`), chỉ cần viết driver.
- **ASR khác:** `parakeet`, `openai` (Whisper API) — cùng interface với `asr/needle`.
- **Dọn workspace:** xoá attempt cũ / artifact `keep: none` theo tuổi; trang quản trị dung lượng `data/jobs`.
- **Trùng job theo nguồn:** UI bỏ qua video đã có việc ("đã có việc job_…"); có thể thêm nút "Tạo việc mới cho video này" khi muốn chạy lại từ đầu với template khác.
- **Câu báo "Bỏ qua … Thiếu đầu vào: audio.dub.ja"** còn kỹ thuật: dịch slot sang tên người dùng hiểu ("chưa có bản lồng tiếng tiếng Nhật").
- **Nhãn bước khi dùng lại cache:** message hiện "Dùng lại kết quả đã có" — có thể kèm thông điệp gốc của lần chạy được dùng lại (số câu, thời lượng).
- **Hàng đợi Redis/Postgres:** `dich/queue/base.py` đã tách interface nếu cần nhiều máy.
- **Export chọn thư mục:** hiện xuất cạnh file gốc hoặc thư mục job; thêm ô chọn thư mục xuất khi tạo việc.

## Giao diện tuỳ chọn nâng cao

- ~~**Tuỳ chọn nâng cao bị chật trong cột 24rem:**~~ (đã làm: Sheet tab dọc + preset lưu ở `<data>/presets.json`) đưa ra Sheet rộng (tabs dọc theo bước) thay vì xổ xuống trong card "Việc mới"; kết hợp **preset cấu hình** (lưu bộ tham số thành tên, chọn 1 chạm) để hầu hết lần tạo việc không phải mở phần nâng cao. Các phương án khác: card tự giãn full-width, dialog lưới nhiều cột, trang "Cấu hình" riêng.
- **Đổi preset làm mất chỉnh sửa chưa lưu:** chọn preset khác khi đang "Đã sửa, chưa lưu" sẽ ghi đè im lặng; nên hỏi lại hoặc cho "Hoàn tác" trong toast.
- **Preset cho CLI:** `dich run --preset "Anime JP"` đọc cùng `presets.json`, để CLI và web dùng chung cấu hình.
- **Preset nhớ cả template + ngôn ngữ đích:** hiện preset chỉ giữ tuỳ chọn từng bước + "ghi cạnh file gốc"; có thể tuỳ chọn lưu kèm "Việc cần làm" và "Dịch sang".

## Chất lượng dịch (đề xuất 2026-10-15)

Thứ tự nên làm: (1) bộ đo → (2) bản tóm tắt toàn video → (3) ngữ cảnh 2 chiều + giới hạn độ dài → (4) glossary/xưng hô → (5) refine nguồn → (6) lượt rà soát.

1. **Bản tóm tắt toàn video (pre-pass):** 1 lần gọi LLM đọc cả transcript, sinh `brief` (chủ đề, giọng điệu, danh sách người nói + cách xưng hô tiếng Việt, thuật ngữ → cách dịch), chèn vào system prompt mọi chunk. Sửa lỗi lớn nhất hiện nay: các chunk chạy song song tự chọn "tôi/mình/anh/em" và dịch thuật ngữ khác nhau.
2. ~~**Ngữ cảnh hai chiều:**~~ (đã làm: chunk theo ranh giới câu, gửi theo nhóm câu, thêm `next` 3 cue sau) **Còn lại — bản dịch trước:** `context` hiện chỉ 5 câu gốc phía trước. Thêm vài câu phía sau (vẫn song song được) để khỏi dịch cụt câu bị cắt ngang chunk.
3. **Dịch theo thời lượng:** gửi kèm `duration` mỗi cue + giới hạn ký tự (~17 ký tự/giây cho phụ đề, theo tốc độ đọc TTS khi lồng tiếng) để bản dịch vừa khung, bớt dub bị tràn/tăng tốc.
4. **Glossary + xưng hô do người dùng sửa, lưu trong preset:** chèn vào prompt LLM; với model local thì thay thế sau dịch. Gộp với ý "Glossary theo job/dự án" ở trên; `brief` ở (1) có thể điền sẵn để người dùng chỉ duyệt.
5. **Lượt rà soát (driver `review`):** LLM đọc song song gốc + bản dịch theo cửa sổ lớn, chỉ trả về các cue cần sửa kèm lý do (sai nghĩa, không nhất quán, quá dài). Tốn thêm ~1x token; chỉ trả diff để không viết lại bản đã ổn.
6. **Bộ đo chất lượng:** 3–5 đoạn video mẫu có bản dịch chuẩn, chấm bằng LLM-judge (hoặc COMET) để so sánh prompt/model/driver thay vì cảm tính.
- **Gộp câu khi transcript không có dấu câu:** `MAX_GAP = 1.5s` trong `llm.py` là ước lượng; nên đo trên video thật (vlog nói liền vs. bài giảng) hoặc đưa thành tham số bước Dịch.

- **Lồng tiếng: co giãn theo đoạn nói thực tế (VAD)** — hiện giảm tốc theo `end - start` của cue. Timing phụ đề thường dư đầu/cuối, nên câu có thể bị kéo chậm quá mức cần. Dùng VAD trên `audio.speech` để lấy đúng đoạn nói rồi so với đoạn đó.

- **ID ngắn khi hiển thị:** ID dạng `job_01JA…` dài 30 ký tự; UI/CLI có thể hiện 6–8 ký tự cuối (như git short hash) và nút sao chép ID đầy đủ.

- **Sửa tay = bước hoàn tất (đã làm):** sửa bản dịch khi bước dịch đang `stale/failed/cancelled/chờ duyệt` thì bản sửa thành kết quả (`done`), "Cập nhật phía sau" chỉ chạy lồng tiếng/ghép. Ý tiếp theo: nút "Cập nhật phía sau" ngay trong trình sửa phụ đề (không phải đợi toast), và tuỳ chọn tự chạy phía sau sau khi lưu (debounce vài giây).

- **So sánh hai lần chạy:** đã có xem/tải/"Dùng lần này" cho lần cũ (audio `keep="last"` giữ `DICH_KEEP_DAYS` ngày). Ý tiếp: xem song song phụ đề/audio của 2 lần (diff cue theo cue) để chọn nhanh hơn; hiện dung lượng đang chiếm bởi các lần cũ và nút dọn ngay.
