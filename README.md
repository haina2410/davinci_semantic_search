# Davinci Semantic Search

Tìm lời thoại Việt–Anh trong Media Pool và timeline DaVinci Resolve. Whisper chạy trên máy. Sau mỗi media, Codex tự hiệu đính văn bản và giữ nguyên mốc thời gian nguồn.

## Cài trên Windows

1. Trên Windows x64 có DaVinci Resolve Studio 21.0.x trở lên, tải tệp `*-windows-x64-setup.exe` từ GitHub Releases rồi nhấp đúp. Lần cài đầu cần Internet. [GitHub Releases](https://github.com/haina2410/davinci_semantic_search/releases)
2. Đợi cửa sổ cài đặt báo **Setup complete**, rồi nhấn Enter để đóng.
3. Mở lại Resolve và chọn **Workspace → Scripts → Davinci Semantic Search**.

Từ Resolve Studio 21.1, bộ cài ưu tiên Python đi kèm Resolve khi cài mới. Bộ cài tự tải các tệp còn thiếu gồm FFmpeg/FFprobe, whisper.cpp x64 chạy CPU, model đa ngôn ngữ `large-v3-turbo-q5_0` (khoảng 547 MiB) và Silero VAD v6.2.0 (dưới 1 MiB). Silero phát hiện các khoảng có giọng nói trước khi Whisper giải mã. Các gói tải về được đối chiếu SHA-256 trước khi dùng. Mặc định, ứng dụng được cài vào `%LOCALAPPDATA%\DavinciSemanticSearch`.

Bạn có thể lập chỉ mục và tìm transcript gốc ngay sau khi cài. Hiệu đính tự động cần cài và đăng nhập Codex CLI riêng. **About** hiển thị trạng thái Codex CLI và phần hiệu đính. [Codex CLI](https://developers.openai.com/codex/cli)

### Resolve Studio 21.0.x

Bộ cài kiểm tra Resolve có nạp được Python 3.10 trở lên, 64-bit. Nếu thiếu, cửa sổ cài đặt hỏi trước khi tải Python 3.14.10 x64 cho tất cả tài khoản Windows. Gõ `y` rồi nhấn Enter để đồng ý. Windows có thể yêu cầu quyền quản trị hoặc khởi động lại.

Để cài Python thủ công, tải **Windows installer (64-bit)**, chọn **Customize installation** và bật tùy chọn cài cho tất cả người dùng. Sau đó nhấp đúp lại tệp `setup.exe`. [Python 3.14.10 trên python.org](https://www.python.org/downloads/release/python-31410/)

Nếu bộ cài vẫn báo lỗi Python, bạn có thể kiểm tra trong **Workspace → Console** của Resolve. Chọn **Py3** nếu có, rồi chạy `import sys; print(sys.version)`.

### Nâng cấp và gỡ cài đặt

Đóng cửa sổ plugin, rồi tải và nhấp đúp `setup.exe` của bản mới. Bộ cài tiếp tục dùng cấu hình, thư mục dữ liệu, model và lựa chọn CPU/GPU đã lưu, kể cả bản cài cũ trỏ vào `.runtime`. Nếu có lỗi, bộ cài dọn các tệp của lần cài đó và khôi phục tệp cài cũ. Python được cài cho tất cả tài khoản Windows qua lời nhắc riêng sẽ được giữ lại.

Sau khi nâng cấp từ bản chưa dùng Silero, chọn **Index project** để nhận dạng lại các transcript cũ có media nguồn đang kết nối. Transcript cũ vẫn tìm được cho tới khi clip đó được nhận dạng thành công.

Để gỡ, đóng cửa sổ plugin rồi nhấp đúp **Davinci Semantic Search - Uninstall** trong thư mục cài đặt, mặc định là `%LOCALAPPDATA%\DavinciSemanticSearch`. Media nguồn, chỉ mục, transcript, log, cấu hình và model được giữ lại. Chạy cài lại với cùng thư mục cài đặt để dùng tiếp dữ liệu.

### Tùy chọn nâng cao

CPU là mặc định khi cài mới. Để dùng GPU qua Vulkan, mở PowerShell trong thư mục chứa tệp cài đặt và chạy lệnh dưới đây với tên tệp của phiên bản bạn tải. Bộ cài tải gói Whisper Vulkan v1.9.2 do Subtitle Edit build và kiểm tra SHA-256 trước khi giải nén. [Gói Whisper Vulkan Windows x64](https://github.com/SubtitleEdit/support-files/releases/tag/whispercpp-192)

```powershell
.\davinci-semantic-search-<version>-windows-x64-setup.exe -EnableVulkan
```

Gói Vulkan đã được thử trên RTX 5060 với Windows và driver NVIDIA 610.88. Laptop AMD vẫn cần kiểm chứng. Worker tự chuyển về CPU nếu Vulkan lỗi. Chạy lại bộ cài với `-DisableVulkan` để chọn CPU, hoặc `-WhisperVulkan` để dùng bản build của bạn cùng các DLL đi kèm.

Dùng `-InstallDir` để chọn thư mục cài riêng và `-DataDir` để chọn nơi lưu chỉ mục. Nếu Resolve ở vị trí tùy chỉnh, mở Resolve trước khi nhấp đúp bộ cài, hoặc dùng `-ResolveDir` trỏ tới thư mục chứa `Resolve.exe`. Với Resolve portable, dùng `-ScriptsDir` trỏ tới thư mục `Fusion\Scripts\Utility` khi cài và nâng cấp. Shortcut gỡ cài đặt sẽ dùng các đường dẫn đã lưu.

Có thể chỉ định tài nguyên có sẵn qua `-FFmpeg`, `-FFprobe`, `-Codex`, `-WhisperCpu`, `-WhisperModel` hoặc `-WhisperVadModel`. Tùy chọn `-Uninstall` chạy gỡ trực tiếp từ bộ cài cho các lần cài cũ chưa có shortcut.

## Dùng trong Resolve

1. Chọn **Lập chỉ mục project** để quét Media Pool. Audio được đọc từ stream mặc định của tệp, hoặc stream đầu tiên khi không có mặc định.
2. Chọn **Nguồn** hoặc **Timeline** rồi nhập cụm cần tìm. Có thể gõ không dấu hoặc sai nhẹ. Đổi phạm vi sẽ chạy lại truy vấn đang nhập. Chọn một kết quả và bấm **Open at Source Time** để chọn clip trong Media Pool trên trang Edit. Khi hiện **Copy Source Timecode**, bấm nút đó rồi dán timecode vào ô timecode của Source Viewer để tới đầu đoạn transcript khớp. Cột **Source Time** hiển thị thời gian tính từ đầu tệp.
3. Ở chế độ Timeline, kết quả **Trong timeline** hiển thị track và timecode. Chọn một hàng có vị trí đã xác minh rồi bấm **Add Temporary Marker** để đặt marker và đưa playhead tới frame đó. Marker tạm được giữ qua các lượt tìm. Bấm **Clear Temporary Markers** để dọn marker của công cụ trên timeline đang mở. Các đoạn ngoài điểm cắt hiển thị riêng. Clip phức tạp, tệp nhiều stream âm thanh, ánh xạ kênh khác bản nhận dạng hoặc đoạn giao điểm cắt có thể hiện **Chưa xác minh** nếu công cụ chưa xác định được frame hoặc phạm vi thoại.
4. Khi đóng cửa sổ tìm kiếm, lượt lập chỉ mục tiếp tục chạy nền. Mở lại cửa sổ trong cùng project để xem tiến độ. Bấm **Stop** để dừng và **Resume Indexing** để tiếp tục từ phần đã lưu. Giữ chuột trên dòng trạng thái để xem các lỗi clip.
5. Mở **About** để xem cấu hình máy, khả năng của driver, tình trạng bản chạy Whisper đã cấu hình và backend mà worker thực sự dùng. Nếu lượt nhận dạng chuyển về CPU, cửa sổ hiển thị lý do khi xác định được.

SQLite và `worker.log` nằm theo project dưới thư mục dữ liệu. WAV trích xuất và JSON trung gian nằm trong `projects/<id-project>/scratch/run-<id-lượt>/`. Khi dừng, worker đợi các tiến trình con thoát rồi dọn toàn bộ thư mục của lượt đó. Nếu bị tắt cưỡng bức, lần mở lại sẽ dọn file mồ côi của project và thử lại nếu Windows còn khóa file.

Tìm kiếm dùng transcript đã hiệu đính khi có. Đoạn đang chờ hiệu đính vẫn tìm được từ transcript gốc và có nhãn **Awaiting polish**. Codex nhận văn bản cùng mã đoạn để sửa lỗi và lặp do nhận dạng. Khi Codex tạm thời không hoạt động, mở lại lượt lập chỉ mục sẽ thử tiếp từ bước đó.

### Xuất log để gửi hỗ trợ

Trong **About**, bấm **Export logs**. Công cụ tạo một tệp ZIP trong thư mục `exports` của bản cài và mở thư mục đó trong Explorer. Gửi tệp ZIP cho người hỗ trợ. Nếu plugin không mở được, hãy nhấp đúp **Davinci Semantic Search - Export logs** trong thư mục cài đặt (mặc định `%LOCALAPPDATA%\DavinciSemanticSearch`).

Gói có sự kiện khởi động, phiên bản plugin và Resolve nếu xác định được, thông tin GPU, backend và các lỗi xử lý trong 72 giờ gần nhất, tối đa 5.000 sự kiện lưu trên một bản cài. Trong **About**, các sự kiện lập chỉ mục thuộc project đang mở. Cách xuất từ Windows lấy project có hoạt động lập chỉ mục gần nhất. Gói không chứa tên project hoặc media, đường dẫn cá nhân, transcript, truy vấn tìm kiếm hay tệp dữ liệu project. Lịch sử chẩn đoán bắt đầu từ khi cập nhật lên bản có tính năng này.

## Kiểm tra

```powershell
python -m unittest discover -s tests -v
```

Các ca tự động kiểm tra hồi phục sau khi gián đoạn, tìm không dấu và lỗi gõ, ánh xạ đoạn đã cắt qua nhiều lần đặt clip, và giữ marker của editor.
