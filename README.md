# Davinci Semantic Search

Tìm lời thoại Việt–Anh trong Media Pool và timeline DaVinci Resolve. Whisper chạy trên máy. Sau mỗi media, Codex tự hiệu đính văn bản và giữ nguyên mốc thời gian nguồn.

## Cài trên Windows

Cần DaVinci Resolve Studio có Fusion UIManager, Python 3.10+ 64-bit, FFmpeg và FFprobe, và Codex CLI đã đăng nhập. Tải ZIP Windows x64 từ GitHub Releases, đối chiếu SHA-256 của phiên bản và giải nén. Trong PowerShell ở thư mục vừa giải nén, chạy lệnh dưới đây. [GitHub Releases](https://github.com/haina2410/davinci_semantic_search/releases) · [Codex CLI](https://developers.openai.com/codex/cli)

```powershell
.\scripts\install.ps1
```

Installer tải và kiểm tra SHA-256 của whisper.cpp x64 chạy CPU và model đa ngôn ngữ `large-v3-turbo-q5_0` (khoảng 547 MiB). Mặc định, ứng dụng được cài vào `%LOCALAPPDATA%\DavinciSemanticSearch`. Mở lại Resolve nếu mục **Davinci Semantic Search** chưa hiện dưới **Workspace → Scripts**.

Dùng `-InstallDir` để chọn thư mục cài riêng và `-DataDir` để chọn nơi lưu chỉ mục. Có thể chỉ định các bản chạy có sẵn qua `-Python`, `-FFmpeg`, `-FFprobe`, `-Codex`, `-WhisperCpu` hoặc `-WhisperModel`. Với bản Resolve portable, dùng cùng giá trị `-ScriptsDir` trỏ tới thư mục `Fusion\Scripts\Utility` của bản đó khi cài, nâng cấp và gỡ.

### Dùng GPU qua Vulkan

CPU là mặc định khi cài mới. Để dùng GPU qua Vulkan, cài với tùy chọn dưới đây. Installer tải gói Whisper Vulkan v1.9.2 do Subtitle Edit build và kiểm tra SHA-256 trước khi giải nén. [Gói Whisper Vulkan Windows x64](https://github.com/SubtitleEdit/support-files/releases/tag/whispercpp-192)

```powershell
.\scripts\install.ps1 -EnableVulkan
```

Gói Vulkan đã được thử trên RTX 5060 với Windows và driver NVIDIA 610.88. Laptop AMD vẫn cần kiểm chứng. Bạn cũng có thể trỏ tới bản build của mình với `-WhisperVulkan` và giữ nguyên các DLL đi kèm. Worker tự chuyển về CPU nếu Vulkan lỗi. Chạy lại installer với `-DisableVulkan` để chọn CPU.

### Nâng cấp và gỡ cài đặt

Đóng cửa sổ plugin, giải nén ZIP phiên bản mới rồi chạy lại `install.ps1`. Installer đọc cấu hình từ mục menu hiện có, kể cả bản cài cũ trỏ vào `.runtime`, và tiếp tục dùng thư mục dữ liệu, model cùng tùy chọn backend đã lưu.

Để gỡ, đóng cửa sổ plugin rồi chạy:

```powershell
.\scripts\uninstall.ps1
```

Script gỡ mục menu và tệp ứng dụng đã cài. Media nguồn, chỉ mục, transcript, log, cấu hình và model được giữ lại. Chạy cài lại với cùng thư mục cài đặt để dùng tiếp dữ liệu.

## Dùng trong Resolve

1. Chọn **Lập chỉ mục project** để quét Media Pool. Audio được đọc từ stream mặc định của tệp, hoặc stream đầu tiên khi không có mặc định.
2. Chọn **Nguồn** hoặc **Timeline** rồi nhập cụm cần tìm. Có thể gõ không dấu hoặc sai nhẹ. Chọn một kết quả và bấm **Chọn trong Media Pool** để xác định media nguồn. Mốc nguồn tính từ đầu tệp.
3. Ở chế độ Timeline, kết quả **Trong timeline** hiển thị track và timecode. Chọn một hàng và bấm **Đặt marker tạm**. Marker nằm trên thước timeline ở frame tìm được. Lượt tìm mới trên cùng timeline dọn các marker tạm của công cụ. Các đoạn ngoài điểm cắt hiển thị riêng. Clip phức tạp, tệp nhiều stream âm thanh, ánh xạ kênh khác bản nhận dạng hoặc đoạn giao điểm cắt có thể hiện **Chưa xác minh** nếu công cụ chưa xác định được frame hoặc phạm vi thoại.
4. Bấm **Dừng** hoặc đóng cửa sổ tìm kiếm để dừng lượt lập chỉ mục mà cửa sổ đang điều khiển. Khi mở lại cùng project, chọn **Tiếp tục lập chỉ mục** để dùng lại transcript đã lưu. Giữ chuột trên dòng trạng thái để xem các lỗi clip.
5. Mở **About** để xem cấu hình máy, khả năng của driver, tình trạng bản chạy Whisper đã cấu hình và backend mà worker thực sự dùng. Nếu lượt nhận dạng chuyển về CPU, cửa sổ hiển thị lý do khi xác định được.

SQLite và `worker.log` nằm theo project dưới thư mục dữ liệu. WAV trích xuất và JSON trung gian nằm trong `projects/<id-project>/scratch/run-<id-lượt>/`. Khi dừng, worker đợi các tiến trình con thoát rồi dọn toàn bộ thư mục của lượt đó. Nếu bị tắt cưỡng bức, lần mở lại sẽ dọn file mồ côi của project và thử lại nếu Windows còn khóa file.

Transcript gốc có thể tìm ngay với nhãn **Chưa hiệu đính**. Bật **Tìm thêm trong bản gốc** để tra cả transcript gốc sau khi hiệu đính. Codex nhận văn bản cùng mã đoạn để sửa lỗi và lặp do nhận dạng. Khi Codex tạm thời không hoạt động, mở lại lượt lập chỉ mục sẽ thử tiếp từ bước đó.

Nếu cửa sổ script không mở, xem `resolve-script-error.log` trong thư mục chứa `settings.json` của bản cài và Resolve Console.

## Kiểm tra

```powershell
python -m unittest discover -s tests -v
```

Các ca tự động kiểm tra hồi phục sau khi gián đoạn, tìm không dấu và lỗi gõ, ánh xạ đoạn đã cắt qua nhiều lần đặt clip, và giữ marker của editor.
