# Davinci Semantic Search

Tìm lời thoại Việt–Anh trong Media Pool và timeline DaVinci Resolve. Whisper chạy trên máy. Sau mỗi media, Codex tự hiệu đính văn bản và giữ nguyên mốc thời gian nguồn.

## Cài trên Windows

Trên Windows x64, cài sẵn DaVinci Resolve Studio 21.0.x trở lên và kết nối Internet cho lần cài đầu. Tải ZIP từ GitHub Releases, đối chiếu SHA-256 của phiên bản và giải nén. Trong PowerShell ở thư mục vừa giải nén, chạy lệnh dưới đây. [GitHub Releases](https://github.com/haina2410/davinci_semantic_search/releases)

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\install.ps1
```

Từ Resolve Studio 21.1, installer ưu tiên Python đi kèm Resolve khi cài mới. Installer tự tải các tệp còn thiếu gồm FFmpeg/FFprobe, whisper.cpp x64 chạy CPU, model đa ngôn ngữ `large-v3-turbo-q5_0` (khoảng 547 MiB) và Silero VAD v6.2.0 (dưới 1 MiB). Silero phát hiện các khoảng có giọng nói trước khi Whisper giải mã. Các gói tải về được đối chiếu SHA-256 trước khi dùng. Mặc định, ứng dụng được cài vào `%LOCALAPPDATA%\DavinciSemanticSearch`. Mở lại Resolve nếu mục **Davinci Semantic Search** chưa hiện dưới **Workspace → Scripts**.

Bạn có thể lập chỉ mục và tìm transcript gốc ngay sau khi cài. Hiệu đính tự động cần cài và đăng nhập Codex CLI riêng. **About** hiển thị trạng thái Codex CLI và phần hiệu đính. [Codex CLI](https://developers.openai.com/codex/cli)

Dùng `-InstallDir` để chọn thư mục cài riêng và `-DataDir` để chọn nơi lưu chỉ mục. Nếu Resolve ở vị trí tùy chỉnh, dùng `-ResolveDir` trỏ tới thư mục chứa `Resolve.exe`. Có thể chỉ định bản chạy hoặc model có sẵn qua `-Python`, `-FFmpeg`, `-FFprobe`, `-Codex`, `-WhisperCpu`, `-WhisperModel` hoặc `-WhisperVadModel`. Với bản Resolve portable, dùng cùng giá trị `-ScriptsDir` trỏ tới thư mục `Fusion\Scripts\Utility` của bản đó khi cài, nâng cấp và gỡ.

### Resolve Studio 21.0.x

Installer chạy `fuscript.exe` để kiểm tra Resolve có nạp được Python 3.10 trở lên, 64-bit, rồi in phiên bản nếu kiểm tra thành công. Nếu Resolve chưa nạp được Python phù hợp, installer hỏi trước khi tải và cài Python 3.14.10 x64 cho tất cả người dùng Windows. Windows có thể yêu cầu quyền quản trị. `-Python` chỉ chọn Python cho tiến trình nền và shortcut xuất log.

Để cài thủ công, chạy **Windows installer (64-bit)**, chọn **Customize installation** và bật tùy chọn cài Python cho tất cả người dùng. Chạy lại `install.ps1`, khởi động lại Resolve và mở plugin từ **Workspace → Scripts**. [Python 3.14.10 trên python.org](https://www.python.org/downloads/release/python-31410/)

Nếu installer vẫn báo Resolve chưa nạp được Python sau khi cài, mở **Workspace → Console**, chọn **Py3** và chạy `import sys; print(sys.version)` nếu tùy chọn này xuất hiện. Nếu máy có nhiều bản Resolve, truyền `-ResolveDir` của bản 21.0.x khi chạy lại installer.

### Dùng GPU qua Vulkan

CPU là mặc định khi cài mới. Để dùng GPU qua Vulkan, cài với tùy chọn dưới đây. Installer tải gói Whisper Vulkan v1.9.2 do Subtitle Edit build và kiểm tra SHA-256 trước khi giải nén. [Gói Whisper Vulkan Windows x64](https://github.com/SubtitleEdit/support-files/releases/tag/whispercpp-192)

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\install.ps1 -EnableVulkan
```

Gói Vulkan đã được thử trên RTX 5060 với Windows và driver NVIDIA 610.88. Laptop AMD vẫn cần kiểm chứng. Bạn cũng có thể trỏ tới bản build của mình với `-WhisperVulkan` và giữ nguyên các DLL đi kèm. Worker tự chuyển về CPU nếu Vulkan lỗi. Chạy lại installer với `-DisableVulkan` để chọn CPU.

### Nâng cấp và gỡ cài đặt

Đóng cửa sổ plugin, giải nén ZIP phiên bản mới rồi chạy lại `install.ps1`. Installer đọc cấu hình từ mục menu hiện có, kể cả bản cài cũ trỏ vào `.runtime`, và tiếp tục dùng thư mục dữ liệu, model cùng tùy chọn backend đã lưu.

Sau khi nâng cấp từ bản chưa dùng Silero, chọn **Index project** để nhận dạng lại các transcript cũ có media nguồn đang kết nối. Transcript cũ vẫn tìm được cho tới khi clip đó được nhận dạng thành công.

Để gỡ, đóng cửa sổ plugin rồi chạy:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\uninstall.ps1
```

Script gỡ mục menu và tệp ứng dụng đã cài. Media nguồn, chỉ mục, transcript, log, cấu hình và model được giữ lại. Chạy cài lại với cùng thư mục cài đặt để dùng tiếp dữ liệu.

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
