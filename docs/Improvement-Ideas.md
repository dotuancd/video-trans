# Improvement Ideas

Sắp xếp theo giá trị với người dùng (cao → thấp). Mục đã làm đã được lược bỏ — xem lịch sử git.

## P1 — Chất lượng bản dịch/lồng tiếng và công sức hằng ngày

- **Dịch lại chỉ câu gốc đã đổi:** "Dịch lại" hiện dịch cả file và ghi đè bản sửa tay. Transcript đã có `src_id`; driver dịch
  nhận bản dịch trước và chỉ gửi các cue có câu gốc khác. Kèm: nếu sửa `.srt` gốc khi bước Dịch đang chạy, báo "Dịch lại"
  khi xong (hoặc khoá cột gốc).
- **Thuật ngữ + xưng hô theo project:** bảng `nguồn => đích` theo ngôn ngữ, sửa trong UI, lưu cùng project/preset; nạp vào
  prompt dịch (model local: thay thế sau dịch), kiểm tra sau dịch. `brief` điền sẵn để người dùng chỉ duyệt; đưa thêm vào
  ASR (initial prompt/hotwords) để nhận đúng tên riêng.
- **Câu lồng tiếng quá dài / dead air:** `fit_dub` đã tắt mặc định (dịch gượng, chi phí ×2). Hướng chính: co giãn ở bước
  Lồng tiếng. Mô phỏng 1,5h video: dead air 34% (min_speed 0,85) → 27% (0,8) → 22% (0,75, nhưng ~60% câu đọc chậm) —
  cần nghe thử để chốt min_speed. Kèm **cảnh báo câu quá dài trong editor** (từ `sped_up`/`late` của dub) để người dùng rút gọn.
- **Tách giọng `demucs` / VAD Silero khi có nhạc nền:** VAD theo âm lượng gần như vô dụng trên audio có nhạc. Slot
  `audio.speech`/`audio.background` đã có, chỉ cần driver. Silero chạy *cạnh* RMS trong cùng vòng giải mã streaming
  (benchmark: 110 phút ~28s, model 2,3 MB), không thay demucs.
- **Sửa phụ đề nhanh hơn:** phím tắt (`⌘S` lưu, `Alt+↑/↓` sang câu + seek, `Ctrl+Space` phát/dừng); nút "Cập nhật phía sau"
  ngay trong editor (tuỳ chọn tự chạy sau khi lưu, debounce).
- **Xử lý hàng loạt còn thiếu:** lọc theo trạng thái (Lỗi / Chờ duyệt / Đang chạy) trước "Chọn tất cả"; "Chạy lại bước lỗi"
  hàng loạt; thêm ngôn ngữ cho các việc đã chọn / cả project (dùng lại bóc băng đã cache); tải về hàng loạt (zip).
- **Đổi preset làm mất chỉnh sửa chưa lưu:** đang ghi đè im lặng — hỏi lại hoặc "Hoàn tác" trong toast.
- **Ước lượng thời gian/chi phí:** từ thời lượng video + lịch sử attempts → "còn ~12 phút" cho từng việc, cả hàng đợi, và
  chi phí token trước khi chạy.
- **Trang Cài đặt trong UI:** xem/sửa giá trị `.env` (engine, giọng mặc định, API key đã đặt chưa) thay vì mở file.

## P2 — Tiện lợi rõ rệt

- **Thư viện giọng:** nhập các clip đã upload kiểu cũ (`data/voices/*.wav`) vào thư viện; tách giọng (vocal separation)
  trước khi chấm điểm đoạn để video có nhạc nền vẫn chọn được đoạn sạch; chọn đoạn trực tiếp trên waveform thay nút ±0,5s;
  ghép nhiều đoạn ngắn của cùng người nói thành một clip 6–8s.

- **Trang thư viện project:** bảng video × ngôn ngữ (tải về, chờ duyệt, lỗi ngay trong ô). Kèm kéo thả job vào project ở
  sidebar, tự đoán project theo tên file khi tải lên ở "Tất cả".
- **Giọng clone:** nghe lại clip mẫu 3–8s cạnh bản đọc thử; chọn người nói (diarization hoặc chọn đoạn trên player); nghe
  thử khi chưa có `.srt` (VAD chọn đoạn) hoặc chưa dịch (dịch nhanh 1 câu trùng đoạn clone).
- **Hàng đợi:** nút "Ưu tiên" mỗi dòng chờ (`tasks.priority` đã có); "Tạm dừng hàng đợi" một chạm (mọi lane về 0 rồi khôi phục).
- **Chỉnh âm lượng tiếng gốc trong player** và áp làm `original_volume` cho bước ghép.
- **Thông báo dễ hiểu:** "Thiếu đầu vào: audio.dub.ja" → "chưa có bản lồng tiếng tiếng Nhật"; lịch sử lần chạy đánh dấu
  rõ "chạy mới / dùng cache" (kèm thông điệp gốc của lần được dùng lại).
- **Quản lý dung lượng:** một trang hệ thống hiện dung lượng + nút dọn cho: attempt cũ / artifact `keep: none`,
  `storage/uploads/` của job đã xoá, cache TTS (LRU), `live/` sau khi ghép, file batch OpenAI (`output_expires_after`),
  `requests.jsonl` quá lớn.
- **So sánh hai lần chạy:** diff phụ đề cue theo cue, nghe song song audio.
- **`.mkv` không phát được trong trình duyệt:** tạo bản preview mp4 nhẹ.
- **Chất lượng dịch nâng cao:** bộ đo (3–5 video mẫu, LLM-judge/COMET) để so prompt/model thay vì cảm tính; sau đó driver
  `review` (LLM rà gốc + bản dịch, chỉ trả cue cần sửa, ~+1× token).
- **Lặt vặt UI:** ID ngắn 6–8 ký tự + nút sao chép; hoàn tác thống nhất (gõ chữ + gộp/tách); nút "Tạo việc mới cho video
  này" khi trùng nguồn; preset nhớ cả template + ngôn ngữ đích; ô chọn thư mục xuất; trace request xem theo cue + lọc lỗi.

## P3 — Lớn hoặc ít người cần, để sau

- **Pipeline theo đoạn (streaming):** ASR/dịch/dub theo cửa sổ ~2 phút → có tiếng Việt sau ~1 phút; dub ưu tiên quanh chỗ
  đang xem. Cần slot streaming trong DAG.
- **Upload tiếp tục khi đứt** (chunk + `Content-Range`); live preview dub không bắt đầu lại từ 0 sau khi tiếp tục.
- **Watch folder** cho project (cần watcher ở supervisor).
- **Driver mới:** ASR `parakeet` / Whisper API; `refine` (LLM sửa ASR).
- **Nhiều máy:** bảng `workers` (heartbeat) để thấy worker chạy bằng `dich worker`; hàng đợi Redis/Postgres; SSE thay poll 1,5s.
- **OpenAI Batch:** gom nhiều task/ngôn ngữ vào một batch.
- **Tinh chỉnh cần đo thêm:** `tts.REF_USE_CODES` (clone chéo ngôn ngữ); `MAX_GAP = 1.5s` khi không có dấu câu; khoảng lặng
  nội câu của VieNeu (hiếm); checkpoint cho NMT/ASR local; overlay phụ đề cho audio-only; `preset` cho CLI.

## Dev / hạ tầng

- Build `web/dist` trong CI hoặc pre-commit.
- Proxy dev trong `web/vite.config.ts` đọc `DICH_UI_PORT` (đang cố định 7860).
- Báo upstream `needle.Whistle` cắt JSON ở `buffer_size` mà không báo lỗi.

- **Đổi tên job hàng loạt / trong danh sách**: hiện chỉ đổi tên inline trong JobDetail. Nếu hay phải dọn tên yt-dlp dài, cân nhắc thêm "Đổi tên" trong menu dòng hoặc quy tắc tự cắt tên (bỏ `[id]`, hashtag…) khi tải.
- **Danh sách việc (sau vt-i26):** lọc theo ngôn ngữ/template; sắp theo thời lượng video (cần lưu `duration` khi probe nguồn); endpoint hàng loạt nhận bộ lọc thay cho `ids` khi số việc rất lớn; test frontend (vitest) nếu UI tiếp tục phình.
- **Chi phí job lưu thành cột** (sau vt-i26): sắp theo chi phí và cờ `has_cost` đang quét bảng `events` (`data like '%cost_usd%'`); bảng events lớn dần (progress, log) sẽ chậm. Cộng dồn `cost_usd` vào `jobs` khi driver log usage.
- **Bỏ chọn một việc sau "Chọn tất cả N việc khớp bộ lọc"**: số Tạm dừng/Tiếp tục khi đó chỉ đếm việc đã tải (vẫn gửi đủ id). Có thể giữ chế độ "tất cả trừ các id đã bỏ" để đếm chính xác.
- **Gói tải về (sau vt-6ap):** gói "chờ tới khi xong" (đợi việc đang chạy/chờ duyệt rồi mới zip; bỏ vì phức tạp); mục "Tải tất cả kết quả" trong menu project ở sidebar; Notification API khi gói xong; giới hạn tổng dung lượng `<data>/bulk_downloads/` (xoá gói cũ nhất); tuỳ chọn kèm `.wav` lồng tiếng. Token qua cookie: vt-3zs.
