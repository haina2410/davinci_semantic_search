# Tiếp tục lập chỉ mục sau khi bị gián đoạn

Lưu tiến độ trong cơ sở dữ liệu SQLite trên máy, theo project, media nguồn và từng bước nhận dạng hoặc hiệu đính. Mỗi bước lưu kết quả cùng trạng thái hoàn thành trong một giao dịch. Vì vậy, lần chạy tiếp theo có thể dùng lại các kết quả đã lưu dù Resolve hay công cụ bị tắt giữa chừng.

Lượt lập chỉ mục tiếp tục chạy nền khi đóng cửa sổ tìm kiếm. Khi mở lại cửa sổ trong cùng project, công cụ kết nối lại với lượt đang chạy để hiển thị tiến độ và cho phép dừng. Sau khi editor bấm **Stop** hoặc khi lượt bị gián đoạn, **Resume Indexing** khôi phục hàng đợi. Clip đã nhận dạng xong tiếp tục từ bước Codex. Nếu nhận dạng bị ngắt trước khi lưu, chạy lại clip đang dở. Media thay đổi được đưa trở lại hàng đợi nhận dạng.
