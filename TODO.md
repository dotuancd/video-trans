## TODO
 Đề xuất cho tôi một bản thiết kế kiến trúc từ greenfield cho
  pipeline từ lấy video nguồn (Upload, Browse, hoặc Download từ URL) -> ASR
  (Subtitle) -> Finetune ASR (Fix or Correction) -> Translate Subtitle (thiết
  kế sẵn để hỗ trợ convert sang nhiều ngôn ngữ) -> Dub từ title -> Mux Video
  nguồn.

  - Hỗ trợ nhiều drivers/adapters
    - Media Source (Upload, Browse, Download URL)
    - Tách âm thanh nền (Thiết kế trước, chứ giờ chưa cần tách âm thanh nền)
    - ASR (Needle-Whistle, Moondream, OpenAI....)
    - Finetune ASR (Hiện tại thiết kế thêm chưa có)
    - Translate Subtitle (ollama, openai, claude)
    - Lồng tiếng (NeuTTS, ...)

  - Dễ dàng thêm/lược bỏ jobs trong pipeline.
  - Hoạt động dạng queue/workers, không block server.
  - Từng pipeline có log riêng (log chi tiết từng bước, không log lỗi chi tiết hệ thống, có thể dùng trace_id riêng cho quản trị xem log lỗi riêng), lưu trữ lại artifact cho từng bước nếu cần.
  - Có khả năng chạy lại một từng job riêng lẻ.


- [x] Triển khai kiến trúc `docs/Architecture.md` bước 1–5 (driver theo slot, DAG + template, đa ngôn ngữ, worker process + lease, 2 kênh log + trace, artifact + cache + export) và UI mới.
- [x] Upload files, hỗ trợ nhiều file.
- [x] Tạo thư mục xử lý riêng cho từng job (`<data>/jobs/<id>/<bước>/<lần>/`).
- [ ] Bước 6: driver `separate` (demucs), `refine` bằng LLM + glossary.

- [ ] Có thêm mục quản lý templates. Người dùng có thể kết nối các tùy chọn với nhau.

## DONE

- [x] Cho phép gộp hoặc thay đổi subtitle, copy vài từ ở câu trên, chuyển xuống câu dưới, hoặc gộp từ ở dòng dưới, lên với dòng trên, hoặc tìm cách segment subtitle thông minh hơn, ví dụ ưu tiên ngắt câu gần nhất (, .) khi mà từ quá ngắn.
- [x] Tạo giao diện cho người dùng ưu tiên Web, chuyển sang mô hình Client Server giao tiếp thông qua API, sử dụng Pipeline, Có trạng thái của từng bước, có thể retry ở một bước nếu lỗi hoặc chạy lại để thay đổi kết quả, kiểm tra các công nghệ mới nhất ở thời điểm hiện tại. Đưa ra một vài lựa chọn dựa trên tiêu chí: Giao diện hiện đại, dễ tích hợp, dễ phát triển, cộng đồng lớn, hệ sinh thái đã lớn mạnh.

- [x] Cho phép user đưa vào một folder để tạo subtitle hàng loạt. Tự động trích xuất audio, hoặc convert để tương thích với model. Ưu tiên sử dụng model needle-whistle cho hiệu năng tốt.
- [x] Tìm kiếm giải pháp dịch thuật local để dịch subtitle thay vì sử dụng Claude, ưu tiên ngôn ngữ (Anh-Việt)
- [x] Cho phép dịch subtitle bằng API của OpenAI.
- [x] Tìm kiếm giải pháp chuyển từ subtitle tiếng Việt thành Audio để tạo lồng tiếng, ưu tiên local, model nhẹ, có voice cloning. Tuy nhiên có thể sử dụng voice cố định cũng được.