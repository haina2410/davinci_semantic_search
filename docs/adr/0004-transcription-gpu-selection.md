# Chọn GPU nhận dạng và tiếp tục sau lỗi GPU

Laptop AMD thử nghiệm có Radeon tích hợp và RX 5500M, còn Whisper báo đang dùng Radeon tích hợp. Khi đã cấu hình Vulkan, **About → Transcription GPU** mặc định đặt **Auto**, ưu tiên GPU rời mà Whisper liệt kê, hoặc cho editor chỉ định GPU theo tên để chủ động dành tài nguyên cho Resolve. Lựa chọn áp dụng cho cả bản cài, được giữ khi nâng cấp và có hiệu lực khi worker tiếp theo bắt đầu. About hiển thị thiết bị thực sự dùng để nhận dạng.

Trước khi một khối audio hoàn tất bằng GPU, **Auto** thử các GPU Vulkan còn lại rồi dùng CPU. Với GPU chỉ định, worker dùng CPU ngay nếu lựa chọn không khả dụng hoặc khởi tạo thất bại, giữ lựa chọn đã lưu và hiển thị lý do. Với bản Whisper tùy chỉnh thiếu bộ chọn thiết bị, **Auto** dùng thiết bị mặc định của bản đó.

Sau khi một khối hoàn tất bằng GPU, lỗi GPU ở khối sau dừng lượt lập chỉ mục để editor quyết định khi nào thử lại. Thông báo nêu thiết bị gặp lỗi và gợi ý đóng rồi mở lại plugin, sau đó bấm **Resume Indexing**. Editor cũng có thể bấm nút này ngay trong cửa sổ hiện tại. Tiến độ đã lưu được giữ, còn media nguồn đang dở được nhận dạng lại theo [cơ chế tiếp tục lập chỉ mục](0003-resumable-project-indexing.md).
