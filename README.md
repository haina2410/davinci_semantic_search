# Davinci Semantic Search

Tìm lời thoại Việt–Anh trong Media Pool và timeline DaVinci Resolve. Whisper chạy trên máy. Sau mỗi media, Codex tự hiệu đính văn bản và giữ nguyên mốc thời gian nguồn.

## Cài trên Windows

Cần DaVinci Resolve Studio có Fusion UIManager, Python 3.10+ 64-bit, FFmpeg và FFprobe, và [Codex CLI](https://developers.openai.com/codex/cli) đã đăng nhập. Từ PowerShell trong thư mục dự án, chạy:

```powershell
.\scripts\install.ps1
```

Installer tải và kiểm tra SHA-256 cho whisper.cpp x64 chạy CPU và model đa ngôn ngữ `large-v3-turbo-q5_0` (khoảng 547 MiB). Nó lưu runtime, cấu hình và dữ liệu mặc định trong `.runtime\` trên cùng ổ đĩa với project. Mở lại Resolve nếu mục **Davinci Semantic Search** chưa hiện dưới **Workspace → Scripts**. Có thể chỉ định đường dẫn bằng `-Python`, `-FFmpeg`, `-FFprobe`, `-Codex`, `-WhisperCpu`, `-WhisperModel`, `-DataDir` hoặc `-ScriptsDir`. Với bản Resolve portable, trỏ `-ScriptsDir` vào thư mục `Fusion\Scripts\Utility` mà bản đó dùng.

### Dùng GPU qua Vulkan

CPU hoạt động với cài đặt mặc định. Trên máy AMD hoặc NVIDIA có driver hỗ trợ Vulkan, cần một bản `whisper-cli.exe` được build với `GGML_VULKAN=ON`. Ví dụ, dùng Git, Visual Studio với C++, CMake và Vulkan SDK để build từ nguồn:

```powershell
git clone --depth 1 --branch v1.9.2 https://github.com/ggml-org/whisper.cpp .runtime/whisper-src
cmake -S .runtime/whisper-src -B .runtime/whisper-vulkan -DGGML_VULKAN=ON -DWHISPER_BUILD_TESTS=OFF
cmake --build .runtime/whisper-vulkan --config Release --target whisper-cli
.\scripts\install.ps1 -WhisperVulkan .runtime/whisper-vulkan/bin/Release/whisper-cli.exe
```

Nếu layout build khác, truyền đường dẫn đến executable được tạo và giữ các DLL đi kèm ở vị trí build. Worker chuyển về CPU với cùng model nếu bản Vulkan gặp lỗi. [Hướng dẫn Vulkan của whisper.cpp](https://github.com/ggml-org/whisper.cpp#vulkan-gpu-support) · [Bản phát hành CPU](https://github.com/ggml-org/whisper.cpp/releases/tag/v1.9.2)

## Dùng trong Resolve

1. Chọn **Lập chỉ mục project** để quét Media Pool. Audio được đọc từ stream mặc định của tệp, hoặc stream đầu tiên khi không có mặc định.
2. Chọn **Nguồn** hoặc **Timeline** rồi nhập cụm cần tìm. Có thể gõ không dấu hoặc sai nhẹ. Chọn một kết quả và bấm **Chọn trong Media Pool** để xác định media nguồn. Mốc nguồn tính từ đầu tệp.
3. Ở chế độ Timeline, kết quả **Trong timeline** hiển thị track và timecode. Chọn một hàng và bấm **Đặt marker tạm**. Marker nằm trên thước timeline ở frame tìm được. Lượt tìm mới trên cùng timeline dọn các marker tạm của công cụ. Các đoạn ngoài điểm cắt hiển thị riêng. Clip phức tạp hoặc đoạn giao điểm cắt có thể hiện **Chưa xác minh** nếu công cụ chưa xác định được frame hoặc phạm vi thoại.
4. Nếu Resolve hoặc worker gián đoạn, mở lại cùng project và chọn **Tiếp tục lập chỉ mục**. Transcript đã lưu được dùng lại. Bấm **Cập nhật tiến độ** để xem trạng thái. Giữ chuột trên dòng trạng thái để xem các lỗi clip. Cơ sở dữ liệu và `worker.log` nằm theo project dưới thư mục dữ liệu.

Transcript gốc có thể tìm ngay với nhãn **Chưa hiệu đính**. Bật **Tìm thêm trong bản gốc** để tra cả transcript gốc sau khi hiệu đính. Codex nhận văn bản cùng mã đoạn để sửa lỗi và lặp do nhận dạng. Khi Codex tạm thời không hoạt động, mở lại lượt lập chỉ mục sẽ thử tiếp từ bước đó.

## Kiểm tra

```powershell
python -m unittest discover -s tests -v
```

Các ca tự động kiểm tra hồi phục sau khi gián đoạn, tìm không dấu và lỗi gõ, ánh xạ đoạn đã cắt qua nhiều lần đặt clip, và giữ marker của editor.
