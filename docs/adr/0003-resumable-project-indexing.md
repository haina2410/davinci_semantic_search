# Tiếp tục lập chỉ mục sau khi bị gián đoạn

Lưu tiến độ trong cơ sở dữ liệu SQLite trên máy, theo project, media nguồn và từng bước nhận dạng hoặc hiệu đính. Mỗi bước lưu kết quả cùng trạng thái hoàn thành trong một giao dịch. Vì vậy, lần chạy tiếp theo có thể dùng lại các kết quả đã lưu dù Resolve hay công cụ bị tắt giữa chừng.

Khi mở lại project có lượt xử lý bị gián đoạn, nút **Tiếp tục lập chỉ mục** khôi phục hàng đợi. Clip đã nhận dạng xong tiếp tục từ bước Codex. Nếu nhận dạng bị ngắt trước khi lưu, chạy lại clip đang dở. Media thay đổi được đưa trở lại hàng đợi nhận dạng. Nếu lượt lập chỉ mục vẫn đang chạy, cửa sổ kết nối lại với lượt đó để tránh xử lý trùng.
