"""
SATISFYING VIDEO LIBRARY & TRANSITIONS MANAGER
==============================================
Chức năng:
  - Quản lý kho video thỏa mãn thị giác (Center Satisfying Videos) cho bố cục Sandwich TikTok
  - Tự động phân loại: Puzzle, Food/Mukbang, ASMR, Gameplay Stunts, Hypnotic Sacred Geometry, Kinetic Sand
  - Cắt các phân đoạn 60 FPS từ video gốc (30 phút) thành các clip 60s đa dạng
  - Tự động sinh video loop thôi miên 3D chuẩn 60 FPS 960x560 siêu mượt
  - Hỗ trợ bộ chuyển cảnh đa dạng (Spring Bounce, Slide Left/Right, Zoom Pop, Shake Twist)
"""

import math
import os
import random
import subprocess
import sys
from PIL import Image, ImageDraw

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS_DIR = os.path.join(BASE_DIR, "assets")
BG_DIR = os.path.join(ASSETS_DIR, "backgrounds")
SAT_DIR = os.path.join(ASSETS_DIR, "satisfying")

SATISFYING_CATEGORIES = {
    "puzzle": {
        "name": "🧩 3D Puzzle & Ghép Hình Thông Minh",
        "desc": "Xếp hình khối, bánh răng cơ học, Rubik, mê cung bi lăn"
    },
    "food": {
        "name": "🍰 Food Prep & Nghệ Thuật Ẩm Thực",
        "desc": "Cắt bánh kem, kéo phô mai, rót latte art, điêu khắc socola"
    },
    "asmr": {
        "name": "🫧 ASMR Cắt Xà Phòng & Bọt Mịn",
        "desc": "Gọt xà phòng lưới, bóp bóng nước slime, ép thủy lực"
    },
    "gameplay": {
        "name": "🏃 Gameplay Parkour & Stunt Combo",
        "desc": "Minecraft Parkour tốc độ cao 60FPS, GTA5 bay dốc, Subway Surfers"
    },
    "hypnotic": {
        "name": "🌀 Hypnotic Geometry & Neon Matrix",
        "desc": "Vòng xoáy thôi miên vô cực, hình học thiêng liêng rực rỡ"
    },
    "kinetic_sand": {
        "name": "⏳ Cát Động Lực & Cắt Khuôn",
        "desc": "Cắt cát cầu vồng, nặn khuôn sắc nét, thư giãn thị giác"
    }
}


def get_ffmpeg_exe() -> str:
    import shutil
    sys_ffmpeg = shutil.which("ffmpeg")
    if sys_ffmpeg:
        return sys_ffmpeg
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return "ffmpeg"


def ensure_satisfying_directories():
    """Tạo đầy đủ cấu trúc thư mục cho từng thể loại video ở giữa."""
    for cat in SATISFYING_CATEGORIES.keys():
        d = os.path.join(SAT_DIR, cat)
        os.makedirs(d, exist_ok=True)


def ensure_rounded_mask_960x560() -> str:
    """Tạo mask bo tròn góc 26px cho video ở giữa để giao diện sang trọng chuẩn iOS/TikTok."""
    mask_path = os.path.join(SAT_DIR, "rounded_mask_960x560.png")
    if os.path.exists(mask_path) and os.path.getsize(mask_path) > 1000:
        return mask_path

    W, H = 960, 560
    radius = 26
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(im)
    # Vẽ hình chữ nhật bo góc trắng 100% alpha
    draw.rounded_rectangle((0, 0, W, H), radius=radius, fill=(255, 255, 255, 255))
    im.save(mask_path, "PNG")
    print(f"  ✨ Đã tạo Rounded Corner Mask 960x560: {mask_path}")
    return mask_path


def slice_gameplay_clips(input_video_path: str):
    """
    Cắt các phân đoạn parkour 60 FPS đỉnh cao từ video gốc 30 phút thành các clip 60s độc lập.
    """
    if not os.path.exists(input_video_path) or os.path.getsize(input_video_path) < 1000000:
        return

    exe = get_ffmpeg_exe()
    ensure_satisfying_directories()

    # Danh sách mốc thời gian hấp dẫn từ video 30 phút
    cuts = [
        {"cat": "gameplay", "name": "parkour_rush_speed.mp4", "start": 45, "dur": 60},
        {"cat": "gameplay", "name": "parkour_precision_jump.mp4", "start": 210, "dur": 60},
        {"cat": "gameplay", "name": "parkour_high_altitude.mp4", "start": 420, "dur": 60},
        {"cat": "asmr", "name": "asmr_smooth_flow_1.mp4", "start": 650, "dur": 60},
        {"cat": "asmr", "name": "asmr_smooth_flow_2.mp4", "start": 980, "dur": 60},
        {"cat": "gameplay", "name": "parkour_stunt_combo.mp4", "start": 1250, "dur": 60},
    ]

    for item in cuts:
        target = os.path.join(SAT_DIR, item["cat"], item["name"])
        if not os.path.exists(target) or os.path.getsize(target) < 100000:
            print(f"  🔪 Đang trích xuất clip thỏa mãn: {item['cat']}/{item['name']}...")
            cmd = [
                exe, "-y",
                "-ss", str(item["start"]),
                "-t", str(item["dur"]),
                "-i", input_video_path,
                "-vf", "scale=960:560:force_original_aspect_ratio=increase,crop=960:560",
                "-an",
                "-c:v", "libx264",
                "-preset", "ultrafast",
                "-crf", "18",
                "-pix_fmt", "yuv420p",
                target
            ]
            subprocess.run(cmd, capture_output=True)


def generate_hypnotic_geometry_loop(output_path: str, theme: str = "sacred_neon", duration: float = 8.0) -> str:
    """
    Tạo video loop thôi miên hình học 3D cực cuốn hút (60 FPS, 960x560).
    """
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    if os.path.exists(output_path) and os.path.getsize(output_path) > 100000:
        return output_path

    exe = get_ffmpeg_exe()
    W, H = 960, 560
    fps = 60
    total_frames = int(fps * duration)
    cx, cy = W // 2, H // 2

    cmd = [
        exe, "-y",
        "-f", "rawvideo", "-vcodec", "rawvideo",
        "-s", f"{W}x{H}", "-pix_fmt", "rgb24", "-r", str(fps),
        "-i", "-",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18",
        "-pix_fmt", "yuv420p", output_path
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)

    num_arms = 8 if theme == "sacred_neon" else 6
    num_rings = 16

    for f in range(total_frames):
        phase = (f / total_frames) * 2 * math.pi
        bg_col = (8, 6, 18) if theme == "sacred_neon" else (6, 12, 22)
        im = Image.new("RGB", (W, H), bg_col)
        draw = ImageDraw.Draw(im)

        for r in range(1, num_rings + 1):
            radius = r * 20 + 8 * math.sin(phase + r * 0.35)
            ring_phase = phase * 1.5 + r * 0.22
            hue_shift = (r / num_rings + (f / total_frames) * 0.4) % 1.0

            if theme == "sacred_neon":
                red = int(128 + 127 * math.sin(hue_shift * 2 * math.pi))
                green = int(128 + 127 * math.sin(hue_shift * 2 * math.pi + 2))
                blue = int(128 + 127 * math.sin(hue_shift * 2 * math.pi + 4))
            else:
                red = int(50 + 180 * math.sin(hue_shift * 2 * math.pi))
                green = int(180 + 75 * math.cos(hue_shift * 2 * math.pi))
                blue = int(220 + 35 * math.sin(hue_shift * 2 * math.pi))

            pts = []
            for a in range(num_arms):
                angle = ring_phase + a * (2 * math.pi / num_arms)
                px = cx + int(radius * 1.55 * math.cos(angle))
                py = cy + int(radius * math.sin(angle))
                pts.append((px, py))
                draw.ellipse((px - 3, py - 3, px + 3, py + 3), fill=(red, green, blue))

            if len(pts) >= 3:
                draw.polygon(pts, outline=(red // 2, green // 2, blue // 2))

        proc.stdin.write(im.tobytes())

    proc.stdin.close()
    proc.wait()
    print(f"  🌀 Đã tạo video thỏa mãn thị giác Hypnotic Geometry: {output_path}")
    return output_path


def generate_pendulum_wave_simulation(output_path: str, duration: float = 8.0) -> str:
    """
    Tạo video mô phỏng sóng con lắc Pendulum Wave vật lý cực kỳ thư giãn (960x560, 60 FPS).
    """
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    if os.path.exists(output_path) and os.path.getsize(output_path) > 100000:
        return output_path

    exe = get_ffmpeg_exe()
    W, H = 960, 560
    fps = 60
    total_frames = int(fps * duration)
    cmd = [
        exe, "-y",
        "-f", "rawvideo", "-vcodec", "rawvideo",
        "-s", f"{W}x{H}", "-pix_fmt", "rgb24", "-r", str(fps),
        "-i", "-",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18",
        "-pix_fmt", "yuv420p", output_path
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)

    num_balls = 18
    ball_spacing = (W - 160) / (num_balls - 1)

    for f in range(total_frames):
        t = (f / total_frames) * 2 * math.pi
        im = Image.new("RGB", (W, H), (10, 10, 16))
        draw = ImageDraw.Draw(im)

        for i in range(num_balls):
            x = 80 + int(i * ball_spacing)
            freq = 1.0 + (i * 0.08)
            y_offset = math.sin(t * freq) * 160
            y = H // 2 + int(y_offset)

            hue = (i / num_balls) * 2 * math.pi
            r = int(128 + 127 * math.sin(hue))
            g = int(128 + 127 * math.sin(hue + 2.0))
            b = int(128 + 127 * math.sin(hue + 4.0))

            # Dây treo mờ
            draw.line((x, 40, x, y), fill=(40, 45, 60), width=1)
            # Quả cầu phát sáng
            draw.ellipse((x - 14, y - 14, x + 14, y + 14), fill=(r, g, b), outline=(255, 255, 255))
            draw.ellipse((x - 5, y - 5, x + 5, y + 5), fill=(255, 255, 255))

        proc.stdin.write(im.tobytes())

    proc.stdin.close()
    proc.wait()
    print(f"  ⚛️ Đã tạo video thỏa mãn Pendulum Wave: {output_path}")
    return output_path


def initialize_satisfying_library():
    """
    Khởi tạo toàn bộ kho video thỏa mãn thị giác phong phú, đa dạng:
    - Trích xuất clips từ video nền dài
    - Tạo các video hình học/vật lý thôi miên
    """
    ensure_satisfying_directories()
    ensure_rounded_mask_960x560()

    # 1. Trích xuất clip gameplay parkour 60 FPS
    bg_input = os.path.join(BG_DIR, "input.mp4")
    if os.path.exists(bg_input):
        slice_gameplay_clips(bg_input)

    # 2. Tạo video loop thôi miên
    puz_path = os.path.join(SAT_DIR, "puzzle", "hypnotic_kaleidoscope.mp4")
    generate_hypnotic_geometry_loop(puz_path, "sacred_neon")

    hyp_path = os.path.join(SAT_DIR, "hypnotic", "sacred_spiral.mp4")
    generate_hypnotic_geometry_loop(hyp_path, "cyan_matrix")

    pend_path = os.path.join(SAT_DIR, "puzzle", "pendulum_wave.mp4")
    generate_pendulum_wave_simulation(pend_path)

    # Sao chép/gán cho các thể loại khác nếu chưa có
    for cat in ["food", "kinetic_sand"]:
        cat_dir = os.path.join(SAT_DIR, cat)
        files = [f for f in os.listdir(cat_dir) if f.endswith(".mp4")]
        if not files:
            # Tạo bản loop đẹp cho thể loại đó
            out_f = os.path.join(cat_dir, f"{cat}_satisfying_loop.mp4")
            generate_hypnotic_geometry_loop(out_f, "sacred_neon")


def get_satisfying_video_path(category: str = "random") -> str:
    """
    Lấy video thỏa mãn thị giác theo thể loại được yêu cầu.
    Hỗ trợ random để tự động đổi mới clip liên tục qua từng lần render.
    """
    ensure_satisfying_directories()
    cat = (category or "random").lower().strip()

    valid_cats = list(SATISFYING_CATEGORIES.keys())
    if cat == "random" or cat not in valid_cats:
        cat = random.choice(valid_cats)

    target_dir = os.path.join(SAT_DIR, cat)
    candidates = []
    if os.path.exists(target_dir):
        candidates = [
            os.path.join(target_dir, f) for f in os.listdir(target_dir)
            if f.lower().endswith((".mp4", ".mov", ".mkv", ".webm")) and os.path.getsize(os.path.join(target_dir, f)) > 100000
        ]

    if candidates:
        chosen = random.choice(candidates)
        print(f"  🎬 [CENTER SATISFYING VIDEO]: ({cat}) -> {os.path.basename(chosen)}")
        return chosen

    # Quét toàn bộ kho satisfying
    all_sat = []
    for c in valid_cats:
        cdir = os.path.join(SAT_DIR, c)
        if os.path.exists(cdir):
            all_sat.extend([
                os.path.join(cdir, f) for f in os.listdir(cdir)
                if f.lower().endswith((".mp4", ".mov", ".mkv", ".webm")) and os.path.getsize(os.path.join(cdir, f)) > 100000
            ])

    if all_sat:
        return random.choice(all_sat)

    # Fallback cuối cùng
    init_file = os.path.join(SAT_DIR, "puzzle", "hypnotic_kaleidoscope.mp4")
    if not os.path.exists(init_file):
        initialize_satisfying_library()
    return init_file


# ─── BỘ HIỆU ỨNG CHUYỂN CẢNH ĐA DẠNG (MODERN MOTION TRANSITIONS) ───────────────

def build_transition_expression(
    trans_type: str,
    base_x: str,
    base_y: str,
    start_t: float,
    dur: float = 0.28,
    dist: float = 50.0,
    cam_motion: str = "none",
    shake_start: float = 0.0,
    shake_end: float = 0.0
) -> tuple[str, str]:
    """
    Sinh công thức tính toán tọa độ X, Y cho FFmpeg overlay filter tương ứng với từng kiểu chuyển cảnh:
      - spring_pop: Nảy lò xo nhún giảm chấn tự nhiên (iOS Spring Curve)
      - slide_left: Lướt từ bên trái sang phải với Ease-Out Cubic
      - slide_right: Lướt từ bên phải sang trái (mô phỏng bong bóng chat reply)
      - slide_up: Trượt lên mềm mại từ dưới
      - punch_in_bounce: Phóng to nảy bật từ tâm (Cutaway gag / Meme)
      - shake: Rung lắc bẻ lái kịch tính
    """
    t_start_s = f"{start_t:.2f}"
    dur_s = f"{dur:.2f}"
    dist_s = f"{dist:.2f}"

    x_expr = base_x
    y_expr = base_y

    if trans_type == "spring_pop":
        # Công thức lò xo tắt dần: y = base_y + 70 * exp(-12*t) * cos(20*t)
        y_expr = f"{base_y} + if(lt(t, {t_start_s} + 0.35), 70*exp(-12*(t-{t_start_s}))*cos(20*(t-{t_start_s})), 0)"
    elif trans_type == "slide_left":
        # Trượt từ bên trái: x = base_x - (1 - (t-start)/dur)^3 * 160
        x_expr = f"{base_x} - if(lt(t, {t_start_s} + {dur_s}), pow(1 - (t-{t_start_s})/{dur_s}, 3)*160, 0)"
    elif trans_type == "slide_right":
        # Trượt từ bên phải: x = base_x + (1 - (t-start)/dur)^3 * 160
        x_expr = f"{base_x} + if(lt(t, {t_start_s} + {dur_s}), pow(1 - (t-{t_start_s})/{dur_s}, 3)*160, 0)"
    elif trans_type == "punch_in_bounce":
        # Nảy bật mạnh mẽ từ tâm + Rung nhẹ
        x_expr = f"{base_x} + if(lt(t, {t_start_s} + 0.35), 8*sin(60*(t-{t_start_s})), 0)"
        y_expr = f"{base_y} + if(lt(t, {t_start_s} + 0.25), pow(1 - (t-{t_start_s})/0.25, 2)*90, 0)"
    else:  # "slide_up" mặc định
        y_expr = f"{base_y} + if(lt(t, {t_start_s} + {dur_s}), pow(1 - (t-{t_start_s})/{dur_s}, 2)*{dist_s}, 0)"

    # Ghép hiệu ứng rung chấn kịch tính (Camera / Card Shake)
    if cam_motion == "shake" and shake_end > shake_start:
        s_start_s = f"{shake_start:.2f}"
        s_end_s = f"{shake_end:.2f}"
        x_expr = f"({x_expr}) + if(between(t,{s_start_s},{s_end_s}), 9*sin(65*t), 0)"
        y_expr = f"({y_expr}) + if(between(t,{s_start_s},{s_end_s}), 7*cos(65*t), 0)"

    return x_expr, y_expr


if __name__ == "__main__":
    print("Khởi tạo kho video thỏa mãn và bộ chuyển cảnh...")
    initialize_satisfying_library()
    v = get_satisfying_video_path("puzzle")
    print(f"Sample satisfying video: {v}")

