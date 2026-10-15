# Improvement Ideas

## Web UI / pipeline

- ~~**Tiến độ cho bước Phụ đề và Dịch**~~: bước Dịch đã báo % (local: mỗi 64 câu, LLM: mỗi chunk) và dừng được giữa chừng. Bước Phụ đề giữ một lần gọi: chia audio làm transcript lệch 3–9% từ và mất dấu kết câu; needle chạy ~70× thời gian thực nên không cần.
- **Dừng giữa bước**: Dừng chỉ ngắt được bước Lồng tiếng (kiểm tra theo câu); Phụ đề/Dịch/Ghép chạy hết bước rồi mới dừng. Có thể chạy các bước này trong subprocess để kill được.
- ~~**Chạy song song theo loại tài nguyên**~~: hai lane `cpu` / `net`, bước Dịch dùng OpenAI/Claude chạy cùng lúc với Phụ đề/Lồng tiếng/Ghép của job khác.
- **Lane theo GPU/MPS**: nếu sau này needle hoặc TTS chạy trên GPU, có thể tách thêm lane `gpu` để Phụ đề chạy song song với dịch local.
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
- **Dừng trước bước Lồng tiếng**: tuỳ chọn để job chờ người dùng nghe thử/chọn giọng rồi mới lồng tiếng cả video (hiện pipeline chạy thẳng, chỉ đổi giọng được khi Chạy lại).
- **Clone chéo ngôn ngữ**: `tts.REF_USE_CODES` quyết định có đưa acoustic codes của clip tiếng Anh vào hay chỉ speaker embedding; cần nghe so sánh trên vài video để chốt, hoặc đưa ra UI.

- **Nghe thử giọng gốc trước khi dịch:** khi video chưa có .vi.srt, preview đọc câu mẫu cố định. Có thể dịch nhanh 1 câu (câu trùng đoạn clone) bằng engine đã chọn để nghe thử đúng nội dung video.
- **Nghe thử giọng gốc khi chưa có .srt:** hiện cần .srt để chọn đoạn clone 3–8 giây. Có thể dùng VAD (silero) trên audio để chọn đoạn nói mà không cần phiên âm trước.

- **Log cho thread con của bước dịch:** `llm_translation.py` gọi OpenAI/Claude qua `ThreadPoolExecutor`, nên những gì các thread này in ra/log chưa vào log của bước. Có thể bọc các task bằng `steplog` (gắn writer của thread cha) để thấy lỗi/retry của từng batch.
- **Output tầng C (ffmpeg trong `load_audio`, ctranslate2, onnx):** đi thẳng ra fd 2 nên không vào log. Với ffmpeg, có thể chuyển sang `stderr=PIPE` giống `mux`.

- **Dịch lại chỉ câu gốc đã sửa:** sửa phụ đề gốc rồi "Dịch lại" sẽ dịch cả file và ghi đè các câu tiếng Việt đã sửa tay. Có thể chỉ dịch lại các nhóm câu có câu gốc đổi (so với bản `.srt` lúc dịch), giữ nguyên phần còn lại.
- **Sửa phụ đề gốc khi đang dịch:** editor cho sửa `.srt` ngay sau bước Phụ đề, nhưng nếu bước Dịch đang chạy thì nó dùng bản cũ. Có thể khoá cột gốc hoặc báo "Dịch lại" khi bước Dịch xong.
