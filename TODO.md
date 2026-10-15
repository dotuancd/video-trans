## TODO

- [ ] Upload files, hỗ trợ nhiều file.
- [ ] Tạo thư mục xử lý riêng cho từng job.


## DONE

- [x] Tạo giao diện cho người dùng ưu tiên Web, chuyển sang mô hình Client Server giao tiếp thông qua API, sử dụng Pipeline, Có trạng thái của từng bước, có thể retry ở một bước nếu lỗi hoặc chạy lại để thay đổi kết quả, kiểm tra các công nghệ mới nhất ở thời điểm hiện tại. Đưa ra một vài lựa chọn dựa trên tiêu chí: Giao diện hiện đại, dễ tích hợp, dễ phát triển, cộng đồng lớn, hệ sinh thái đã lớn mạnh.

- [x] Cho phép user đưa vào một folder để tạo subtitle hàng loạt. Tự động trích xuất audio, hoặc convert để tương thích với model. Ưu tiên sử dụng model needle-whistle cho hiệu năng tốt.
- [x] Tìm kiếm giải pháp dịch thuật local để dịch subtitle thay vì sử dụng Claude, ưu tiên ngôn ngữ (Anh-Việt)
- [x] Cho phép dịch subtitle bằng API của OpenAI.
- [x] Tìm kiếm giải pháp chuyển từ subtitle tiếng Việt thành Audio để tạo lồng tiếng, ưu tiên local, model nhẹ, có voice cloning. Tuy nhiên có thể sử dụng voice cố định cũng được.