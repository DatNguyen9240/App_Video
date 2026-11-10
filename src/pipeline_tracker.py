"""
PIPELINE STATUS & EMOTION TRACKER (THREAD-SAFE)
================================================
Chức năng:
  - Theo dõi tiến trình thời gian thực từng bước của quy trình sản xuất video
  - Ghi nhận trạng thái Đồ thị Cảm xúc (Emotional Arc Timeline)
  - Lưu trữ log hoạt động có timestamp để truyền về Web UI Tab "Giám Sát Quy Trình"
"""

import time
import threading
from datetime import datetime
from copy import deepcopy

STEP_DEFINITIONS = [
    {
        "id": "script",
        "title": "1. Sáng Tạo & Đồ Thị Cảm Xúc",
        "desc": "Gemini AI xây kịch bản 5 pha, phân vai nhân vật & tính toán nhịp thở",
        "status": "idle",
        "time_str": ""
    },
    {
        "id": "cards",
        "title": "2. Thiết Kế Thẻ Threads Chuẩn",
        "desc": "Render thẻ bài viết & bình luận với avatar, tích xanh, icon vector",
        "status": "idle",
        "time_str": ""
    },
    {
        "id": "tts",
        "title": "3. Diễn Xuất Giọng Nói Đa Vai",
        "desc": "Microsoft Edge TTS đa giọng (Nam/Nữ) kèm biến thiên Rate & Pitch theo cảm xúc",
        "status": "idle",
        "time_str": ""
    },
    {
        "id": "audio",
        "title": "4. Thiết Kế Âm Thanh & Nhịp Thở",
        "desc": "Chèn khoảng lặng kịch tính, SFX meme tại điểm rơi & BGM ducking",
        "status": "idle",
        "time_str": ""
    },
    {
        "id": "composite",
        "title": "5. Dựng Phim Đa Lớp & Camera Motion",
        "desc": "FFmpeg ghép nền vô cực 3D, hiệu ứng rung Camera Shake & Meme stickers",
        "status": "idle",
        "time_str": ""
    },
    {
        "id": "export",
        "title": "6. Đóng Gói & Tối Ưu Viral",
        "desc": "Xuất video Full HD 1080x1920, tạo Caption kích thích bình luận & Hashtag",
        "status": "idle",
        "time_str": ""
    }
]

_lock = threading.Lock()
_state = {
    "is_running": False,
    "current_step_id": "idle",
    "current_step_title": "Sẵn sàng",
    "progress_percent": 0,
    "message": "Hệ thống sẵn sàng sản xuất video.",
    "error": None,
    "video_filename": None,
    "caption": "",
    "total_duration_sec": 0.0,
    "steps": deepcopy(STEP_DEFINITIONS),
    "emotional_arc_data": [],
    "logs": []
}


def reset():
    """Khởi tạo lại trạng thái mới."""
    global _state
    with _lock:
        _state["is_running"] = True
        _state["current_step_id"] = "init"
        _state["current_step_title"] = "Khởi động quy trình"
        _state["progress_percent"] = 2
        _state["message"] = "Đang chuẩn bị môi trường sản xuất..."
        _state["error"] = None
        _state["video_filename"] = None
        _state["caption"] = ""
        _state["total_duration_sec"] = 0.0
        _state["steps"] = deepcopy(STEP_DEFINITIONS)
        _state["emotional_arc_data"] = []
        _state["logs"] = []
    log("🚀 Bắt đầu phiên sản xuất video mới.")


def log(text: str, level: str = "INFO"):
    """Ghi log kèm timestamp."""
    global _state
    now_str = datetime.now().strftime("%H:%M:%S")
    entry = {"time": now_str, "level": level, "text": text}
    with _lock:
        _state["logs"].append(entry)
        if len(_state["logs"]) > 150:
            _state["logs"].pop(0)
    print(f"[{now_str}] [{level}] {text}")


def start_step(step_id: str, message: str = None, percent: int = None):
    """Đánh dấu bắt đầu một bước trong quy trình."""
    global _state
    now_str = datetime.now().strftime("%H:%M:%S")
    with _lock:
        _state["current_step_id"] = step_id
        for s in _state["steps"]:
            if s["id"] == step_id:
                s["status"] = "running"
                s["time_str"] = now_str
                _state["current_step_title"] = s["title"]
                break
        if message:
            _state["message"] = message
        if percent is not None:
            _state["progress_percent"] = percent
    log(f"▶️ BẮT ĐẦU: {step_id.upper()} - {_state['message']}")


def update_progress(percent: int, message: str = None):
    """Cập nhật phần trăm tiến độ và thông điệp."""
    global _state
    with _lock:
        _state["progress_percent"] = min(100, max(0, percent))
        if message:
            _state["message"] = message
    if message:
        log(f"   ... {percent}% - {message}")


def finish_step(step_id: str):
    """Đánh dấu hoàn thành một bước."""
    global _state
    now_str = datetime.now().strftime("%H:%M:%S")
    with _lock:
        for s in _state["steps"]:
            if s["id"] == step_id:
                s["status"] = "done"
                s["time_str"] = now_str
                break
    log(f"✅ HOÀN THÀNH BƯỚC: {step_id.upper()}")


def set_emotional_arc(arc_data: list):
    """Cập nhật dữ liệu đồ thị nhịp cảm xúc để hiển thị biểu đồ."""
    global _state
    with _lock:
        _state["emotional_arc_data"] = arc_data
    log(f"📊 Đã cập nhật đồ thị cảm xúc ({len(arc_data)} phân đoạn).")


def pause_for_review(message: str = "Đã chuẩn bị kịch bản & thẻ Threads hoàn tất. Sẵn sàng render video!"):
    """Đánh dấu quy trình tạm dừng đợi người dùng duyệt kịch bản (is_running = False)."""
    global _state
    with _lock:
        _state["is_running"] = False
        _state["message"] = message
        _state["current_step_id"] = "ready_to_render"
        _state["current_step_title"] = "Sẵn sàng dựng video"
    log(f"⏸️ CHỜ DUYỆT: {message}")


def finish_pipeline(video_filename: str, caption: str, total_duration: float = 0.0):
    """Đánh dấu kết thúc toàn bộ quy trình thành công."""
    global _state
    with _lock:
        _state["is_running"] = False
        _state["current_step_id"] = "done"
        _state["current_step_title"] = "Hoàn tất xuất sắc"
        _state["progress_percent"] = 100
        _state["message"] = "🎉 Render video hoàn tất! Video đã sẵn sàng phát."
        _state["video_filename"] = video_filename
        _state["caption"] = caption
        _state["total_duration_sec"] = total_duration
        for s in _state["steps"]:
            if s["status"] == "running":
                s["status"] = "done"
    log(f"🎉 TOÀN BỘ QUY TRÌNH HOÀN TẤT: {video_filename} ({total_duration:.1f}s)")


def set_error(error_msg: str):
    """Ghi nhận lỗi quy trình."""
    global _state
    with _lock:
        _state["is_running"] = False
        _state["error"] = error_msg
        _state["message"] = f"Lỗi: {error_msg}"
        for s in _state["steps"]:
            if s["status"] == "running":
                s["status"] = "error"
    log(f"❌ LỖI QUY TRÌNH: {error_msg}", level="ERROR")


def get_state() -> dict:
    """Lấy bản sao an toàn của trạng thái hiện tại."""
    with _lock:
        return deepcopy(_state)

