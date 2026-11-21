# Chiếu kết quả nguồn lên timeline đang mở

Công cụ mở từ `Workspace → Scripts` và tìm lời thoại trong media của Media Pool thuộc project Resolve đang mở. Tìm trong timeline dùng transcript nguồn và khoảng media mà từng lần đặt clip sử dụng, nên chỉnh điểm cắt chỉ cần tính lại vị trí kết quả. Lời thoại nằm ngoài timeline được hiển thị riêng để người dựng quyết định có đưa vào hay không.

Khi người dựng chọn đặt marker, công cụ đánh dấu vị trí đã xác minh trên thước timeline và đưa playhead tới đó. Marker nằm ở một frame cố định. Việc di chuyển clip hoặc chỉnh đoạn đã dựng có thể làm vị trí này hết hiệu lực. Lượt tìm mới tính kết quả theo vị trí hiện tại và giữ các marker đã đặt. Người dựng dùng **Clear Temporary Markers** để dọn các marker của công cụ trên timeline đang mở. Retime, multicam và compound clip cần kiểm chứng ánh xạ thời gian trước khi đặt marker.
