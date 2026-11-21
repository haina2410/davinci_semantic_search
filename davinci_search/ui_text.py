"""Render diagnostics from current and saved indexing runs in English."""

import re


DIAGNOSTICS = {
    "Resolve không cung cấp ID của project.": "Resolve did not provide a project ID.",
    "Mã đoạn hiệu đính không khớp transcript gốc.": "Polished segment IDs do not match the raw transcript.",
    "Timeline có tốc độ khung hình không hợp lệ.": "The timeline has an invalid frame rate.",
    "Tìm tối đa 30 từ, mỗi từ tối đa 100 ký tự.": "Search for up to 30 words of at most 100 characters each.",
    "Mã lượt lập chỉ mục không hợp lệ.": "Invalid indexing run ID.",
    "Thư mục tạm không phải thư mục cục bộ của project.": "The scratch directory must be a local project directory.",
    "Media thay đổi hoặc mất kết nối. Hãy quét lại Media Pool.": "Media changed or became unavailable. Scan the Media Pool again.",
    "Chưa cấu hình bản Whisper Vulkan.": "Whisper Vulkan is not configured.",
    "Không tìm thấy model whisper.cpp. Kiểm tra cấu hình cài đặt.": "The whisper.cpp model is missing. Check the installation settings.",
    "FFprobe không đọc được media.": "FFprobe could not read the media.",
    "FFmpeg không trích xuất được audio.": "FFmpeg could not extract the audio.",
    "Whisper Vulkan không tạo transcript.": "Whisper Vulkan did not produce a transcript.",
    "Không tìm thấy bản Whisper Vulkan đã cấu hình.": "The configured Whisper Vulkan executable was not found.",
    "Không chạy được Whisper Vulkan.": "Whisper Vulkan could not start.",
    "Whisper Vulkan tạo transcript không hợp lệ.": "Whisper Vulkan produced an invalid transcript.",
    "Whisper Vulkan không phát hiện thiết bị GPU.": "Whisper Vulkan did not detect a GPU.",
    "Whisper không khởi tạo được backend GPU.": "Whisper could not initialize the GPU backend.",
    "Không tìm thấy Codex CLI. Kiểm tra cấu hình cài đặt.": "Codex CLI was not found. Check the installation settings.",
    "Codex gián đoạn hoặc trả về dữ liệu không hợp lệ.": "Codex was interrupted or returned invalid data.",
    "Codex trả về toàn bộ transcript rỗng.": "Codex returned an empty transcript.",
}

CODED_DIAGNOSTICS = {
    r"Whisper Vulkan thoát với mã (-?\d+)\.": "Whisper Vulkan exited with code {}.",
    r"Không chạy được Whisper Vulkan \(mã (-?\d+)\)\.": "Whisper Vulkan could not start (code {}).",
    r"whisper\.cpp không hoàn tất nhận dạng \(mã (-?\d+)\)\.": "whisper.cpp did not complete transcription (code {}).",
    r"Codex chưa hiệu đính được \(mã (-?\d+)\)\.": "Codex could not polish the transcript (code {}).",
}


def english_diagnostic(message: str) -> str:
    if message in DIAGNOSTICS:
        return DIAGNOSTICS[message]
    for pattern, template in CODED_DIAGNOSTICS.items():
        if match := re.fullmatch(pattern, message):
            return template.format(match[1])
    return message
