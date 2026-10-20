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
  *Đã thử, bỏ (vt-329):* nhịp chung cả video kiểu YouDub (Σ TTS / Σ gốc, mỗi câu ±10%) — dead air chỉ giảm 0,4–1,7 điểm,
  nghe A/B không phân biệt được. Dead air còn lại do bản dịch ngắn, không phải do co giãn.
- **Giữ tiếng gốc cho âm không lời (học từ YouDub `audio_mode`):** LLM dịch trả thêm `tts|original` cho mỗi câu; cười, hét,
  thở dốc… ghép lại đoạn audio gốc thay vì TTS đọc "(tiếng cười)". Kèm luật bỏ từ đệm (um, uh, you know) trong prompt dịch.
- **Phát hiện câu TTS hỏng và đọc lại:** so thời lượng clip với số âm tiết × `SPEECH_RATE`; lệch quá xa (lặp, kéo dài,
  im lặng) → đọc lại tối đa 2–3 lần, giữ bản gần nhất (VoxCPM có sẵn `retry_badcase`, VieNeu thì chưa).
- **Phụ đề dễ đọc hơn:** cue hiện đúng từ đầu–cuối từ nên chớp nhanh; nới đầu ~0,1s / cuối ~0,3s vào khoảng lặng (chia theo
  tỉ lệ khi khoảng lặng hẹp, chừa 50ms) — chỉ cho phụ đề xuất ra, không đổi timing dub. Cue tiếng Việt dài hơn `max_chars`
  thì tách tiếp theo dấu câu, chia thời gian theo số ký tự.
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
- **Clone giọng theo từng câu (YouDub):** mẫu giọng = chính đoạn gốc của câu đó (−80/+160ms), câu < 1,2s dùng mẫu chung →
  cảm xúc/ngữ điệu từng câu khớp hơn. Đổi lại: không cache theo giọng được, chậm hơn, dễ mang ngữ điệu tiếng Anh sang. Cần nghe A/B.
- **Watch folder** cho project (cần watcher ở supervisor).
- **Driver mới:** ASR `parakeet` / Whisper API; `refine` (LLM sửa ASR).
- **Nhiều máy:** bảng `workers` (heartbeat) để thấy worker chạy bằng `dich worker`; hàng đợi Redis/Postgres; SSE thay poll 1,5s.
- **OpenAI Batch:** gom nhiều task/ngôn ngữ vào một batch.
- **Tinh chỉnh cần đo thêm:** `tts.REF_USE_CODES` (clone chéo ngôn ngữ); `MAX_GAP = 1.5s` khi không có dấu câu; khoảng lặng
  nội câu của VieNeu (hiếm); checkpoint cho NMT/ASR local; overlay phụ đề cho audio-only; `preset` cho CLI.

## Dev / hạ tầng

- **Test cho web (vitest):** web chưa có test runner; logic thuần như `lib/playback.ts` (chọn bản phát, ngôn ngữ phổ biến),
  `lib/cues.ts`, `lib/jobs.ts` (`compareJobs`, `matches`) chỉ được kiểm bằng tay. Thêm vitest + vài test bảng cho các hàm này.

- Build `web/dist` trong CI hoặc pre-commit.
- Proxy dev trong `web/vite.config.ts` đọc `DICH_UI_PORT` (đang cố định 7860).
- Báo upstream `needle.Whistle` cắt JSON ở `buffer_size` mà không báo lỗi.

- **Đổi tên job hàng loạt / trong danh sách**: hiện chỉ đổi tên inline trong JobDetail. Nếu hay phải dọn tên yt-dlp dài, cân nhắc thêm "Đổi tên" trong menu dòng hoặc quy tắc tự cắt tên (bỏ `[id]`, hashtag…) khi tải.
- **Danh sách việc (sau vt-i26):** lọc theo ngôn ngữ/template; sắp theo thời lượng video (cần lưu `duration` khi probe nguồn); endpoint hàng loạt nhận bộ lọc thay cho `ids` khi số việc rất lớn; test frontend (vitest) nếu UI tiếp tục phình.
- **Chi phí job lưu thành cột** (sau vt-i26): sắp theo chi phí và cờ `has_cost` đang quét bảng `events` (`data like '%cost_usd%'`); bảng events lớn dần (progress, log) sẽ chậm. Cộng dồn `cost_usd` vào `jobs` khi driver log usage.
- **Bỏ chọn một việc sau "Chọn tất cả N việc khớp bộ lọc"**: số Tạm dừng/Tiếp tục khi đó chỉ đếm việc đã tải (vẫn gửi đủ id). Có thể giữ chế độ "tất cả trừ các id đã bỏ" để đếm chính xác.
- **Gói tải về (sau vt-6ap):** gói "chờ tới khi xong" (đợi việc đang chạy/chờ duyệt rồi mới zip; bỏ vì phức tạp); mục "Tải tất cả kết quả" trong menu project ở sidebar; Notification API khi gói xong; giới hạn tổng dung lượng `<data>/bulk_downloads/` (xoá gói cũ nhất); tuỳ chọn kèm `.wav` lồng tiếng. Token qua cookie: vt-3zs.
- **Preview gói tải về (vt-6ap.4):** đang gọi `slot_artifact` theo từng job × ngôn ngữ × loại file — chậm khi chọn hàng nghìn việc; gom thành một truy vấn `current_artifacts`. Job đã xong nhưng không có file khớp (không ghép video, bị lọc ngôn ngữ) đang bị bỏ qua im lặng: có thể trả thêm `empty` để dialog giải thích "0 file". Dung lượng phụ đề trong preview là của transcript gốc, không phải .srt (chỉ ước lượng).
- **Dialog Tải về (vt-6ap.5):** `UnfinishedJob.status` đang là `str` — đổi sang Literal để UI khỏi ép kiểu; khi gói được dùng lại (cùng tập file còn hạn) toast có thể ghi "Dùng lại gói đã tạo"; toast tiến độ chưa thấy được trên máy thật vì zip ZIP_STORED xong rất nhanh — nên thử với vài GB.
- **Mục Gói tải về trong Hàng đợi (vt-6ap.6):** "còn …" tính bằng đồng hồ trình duyệt — máy khác lệch giờ sẽ thấy sai; có thể trả `now` của server trong `/api/bulk-downloads`. Bấm Tải khi gói vừa hết hạn (chưa bị prune) chỉ thấy tải lỗi trong trình duyệt (410) — có thể HEAD trước rồi toast "Gói đã hết hạn". Thanh tiến độ chưa thử với gói vài GB (zip xong quá nhanh).
- **Luật dịch theo cặp ngôn ngữ (vt-v7s):** mới có en→vi; thêm ja/zh/ko→vi khi có video thật để so. Bump `version` driver dịch xoá cache mọi cặp — nếu hay chỉnh prompt, đưa hash của prompt vào khoá cache thay cho bump tay. gpt-5-mini từng dồn chữ sang cue bên cạnh để lại cue rỗng khi được phép bỏ trống: nếu tái diễn, `check()` có thể từ chối cue rỗng khi cue gốc có nội dung.

- **Phím tắt xóa câu phụ đề**: hiện chỉ có nút thùng rác (hover). Có thể thêm Ctrl/Cmd+Shift+Backspace để xóa câu đang gõ mà không rời bàn phím.
- **Chạy lại brief khi sửa nhiều**: sửa phụ đề không còn làm brief stale. Nếu người dùng sửa phần lớn transcript (vd. >30% câu), có thể gợi ý "Chạy lại bối cảnh?" thay vì im lặng giữ bản cũ.
- **Player xem liên tục (sau vt-s9r):** thời lượng trong playlist (cần lưu `duration` khi probe nguồn, chung với ý sắp theo thời lượng); đồng bộ vị trí/"đã xem" lên server nếu hay xem trên nhiều máy qua Tailscale (hiện chỉ `localStorage`); phụ đề song ngữ trên player; bố cục cho di động; phát từ các việc tick chọn ở BulkBar.
- **Playlist (vt-s9r.2):** lỗi khi tự tải trang kế chỉ để lại nút "Tải thêm" ở cuối cột (▶ ghi "Đang tải thêm…" mãi) — nên hiện lỗi + thử lại. Khi player mở, danh sách tự refetch mỗi lần có việc đổi chỗ/ra vào bộ lọc; với sắp "Cập nhật gần nhất" lúc nhiều việc đang chạy có thể refetch dày — cân nhắc debounce. `neighbour`/`placeIn` là ứng viên đầu tiên cho test vitest.
- **Tự chuyển bài (vt-s9r.4):** video có hình nhưng tiếng codec lạ (AC3/DTS trong .mkv) vẫn "phát được" nên không tự báo lỗi — cần kiểm `audioTracks`/`webkitAudioDecodedByteCount` nếu hay gặp. Đếm ngược vẫn chạy khi tab ẩn; có thể tạm dừng theo `visibilitychange`.
- **Điều khiển xem (vt-s9r.3):** Gốc/Lồng tiếng áp cho cả phiên xem, không lưu — nếu hay xem bản gốc có thể lưu như ngôn ngữ. Ngôn ngữ nhớ chỉ dùng khi danh sách (hoặc việc đang mở) có nó; mở thẳng `?play=` trước khi danh sách tải có thể bỏ qua lựa chọn nhớ. Bản gốc .mkv không giải mã được khi bấm Gốc sẽ rơi vào đếm ngược tự chuyển bài thay vì gợi ý quay lại Lồng tiếng. ←/→ không còn cuộn cột danh sách phát.
- **Nhớ vị trí xem (vt-s9r.5):** bản gốc vẫn nhớ theo job + ngôn ngữ — đổi ngôn ngữ mà vẫn xem bản gốc thì bắt đầu lại; có thể dùng chung vị trí khi cùng file. Nút "Xem tiếp" chỉ thấy các trang đã tải. Thanh tiến độ chỉ có ở cột playlist, chưa có ở danh sách việc. Không nghe sự kiện `storage` nên hai tab có thể ghi đè nhau. `toWatch`/`resumeAt` là ứng viên test vitest.
- **Player trên Video.js 10 (vt-0qq):** phụ đề vẫn là overlay riêng (một dòng, chữ to) nên nút CC/phím C của skin không có tác dụng — có thể đưa transcript thành text track (như SubtitleEditor) để dùng menu phụ đề của skin và `::cue`. Phím của skin chỉ chạy khi focus ở trong video; ngoài đó Player tự xử lý Space/←/→/F (10 s như skin) — nếu muốn thêm J/K/L/M/↑↓/0–9 toàn trang thì cần lấy source skin qua shadcn. ErrorDialog của skin hiện cùng màn "Không phát được" khi lỗi. JobDetail (xem trước + live dub) vẫn dùng `<video>` gốc. Bundle +~74 KB gzip.
- **Phụ đề/audio trong skin (vt-bze):** đổi audio track dùng `audioTracks` của trình duyệt — mp4 chỉ Safari có, Chrome/Firefox không hiện menu; nếu cần thì backend xuất thêm HLS (mỗi track một rendition) hoặc tách file tiếng. Nguồn chỉ có audio (`<audio>`) không vẽ text track nên mất phụ đề — có thể phát bằng `<Video>` để hiện. Track "Gốc" có `srclang="src"` (không phải mã BCP 47): nên lưu ngôn ngữ nguồn để ghi đúng. Chỉ nhớ bật/tắt phụ đề, chưa nhớ ngôn ngữ phụ đề chọn trong menu.

## Player không thanh trên cùng (vt-540)
- ✅ Đã làm: tiêu đề bằng part `Title` (skin video cài source ở `web/src/components/videojs`).
- Skin audio vẫn là bản đóng gói, chưa có ⏮ ⏭: cài `@videojs/audio` qua shadcn nếu playlist có nhiều audio.
- ✅ Đã làm: cụm ngôn ngữ / danh sách / đóng nằm trong skin (slot `corner`).
- ✅ Đã làm: chọn ngôn ngữ bằng Menu của Video.js (`LanguageMenu`).
- ✅ Đã làm (vt-6xs): menu ngôn ngữ, select dự phòng và nhãn phụ đề hiện tên đầy đủ ("Tiếng Việt") bằng `Intl.DisplayNames` (`lib/langName`); nút trên thanh vẫn là mã ngắn cho gọn. BulkBar còn lấy tên từ API `languages` — có thể dùng chung `langName` để bỏ phụ thuộc.

## Nút danh sách, ⏮ ⏭, nút thoát (vt-4js)
- ✅ Đã làm: nút hiện/ẩn danh sách thay chỗ PiP; ⏮ ⏭ lên đầu cột playlist (cạnh "Tự chuyển bài"); nút đóng thành ← góc trên trái.
- ✅ Đã làm (toàn màn hình): cột danh sách phát hiện đè lên video, phía trên thanh điều khiển, bật/tắt bằng cùng nút danh sách. Khi ẩn cột thì vẫn chỉ còn phím P/N để chuyển bài.
- PiP không còn nút; file `videojs/ui/pip-button.tsx` vẫn giữ nếu cần lại.
- **Ngôn ngữ xem do bộ lọc danh sách quyết định**: player đã bỏ nút chọn ngôn ngữ (vt-pzy), giờ luôn phát theo ngôn ngữ phổ biến nhất của danh sách. Khi danh sách có nhiều ngôn ngữ, thêm bộ lọc ngôn ngữ ở danh sách việc để người dùng tự chọn ngôn ngữ trước khi bấm Xem.
