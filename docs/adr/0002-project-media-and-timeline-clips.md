# Chiếu kết quả nguồn lên timeline đang mở

Công cụ mở từ `Workspace → Scripts` và tìm lời thoại trong media của Media Pool thuộc project Resolve đang mở. Tìm trong timeline dùng transcript nguồn và khoảng media mà từng lần đặt clip sử dụng, nên chỉnh điểm cắt chỉ cần tính lại vị trí kết quả. Lời thoại nằm ngoài timeline được hiển thị riêng để người dựng quyết định có đưa vào hay không.

Khi người dựng chọn đặt marker, công cụ đánh dấu vị trí đã xác minh trên thước timeline. Marker thuộc lượt tìm kiếm và tham chiếu trạng thái timeline lúc đó. Việc di chuyển clip hoặc chỉnh đoạn đã dựng có thể làm vị trí này hết hiệu lực. Khi tìm lại trên cùng timeline, công cụ chỉ dọn marker tạm do nó tạo cho lượt trước và tính kết quả theo vị trí hiện tại. Retime, multicam và compound clip cần kiểm chứng ánh xạ thời gian trước khi đặt marker.
