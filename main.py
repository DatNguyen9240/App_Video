"""
App Video Automation — Main Entrypoint
=======================================
Hệ thống tự động tạo video ngắn (TikTok / Shorts / Reels)
Nội dung: Gemini AI sáng tạo bài đăng Threads + Nền Gradient / Minecraft + Giọng đọc tiếng Việt

Cách dùng:
  python main.py
"""

import os
import subprocess
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.join(BASE_DIR, "src")
ENV_PATH = os.path.join(BASE_DIR, ".env")


def clear_screen():
    os.system("cls" if os.name == "nt" else "clear")


def get_current_settings():
    bg_mode = "gradient"
    voice = "vi-VN-NamMinhNeural"
    if os.path.exists(ENV_PATH):
        with open(ENV_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("BG_MODE="):
                    val = line.split("=", 1)[1].strip()
                    if val: bg_mode = val
                elif line.startswith("TTS_VOICE="):
                    val = line.split("=", 1)[1].strip()
                    if val: voice = val
    return bg_mode, voice


def set_env_value(key, value):
    lines = []
    found = False
    if os.path.exists(ENV_PATH):
        with open(ENV_PATH, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip().startswith(f"{key}="):
                    lines.append(f"{key}={value}\n")
                    found = True
                else:
                    lines.append(line)
    if not found:
        lines.append(f"{key}={value}\n")
    with open(ENV_PATH, "w", encoding="utf-8") as f:
        f.writelines(lines)


def print_banner():
    bg_mode, voice = get_current_settings()
    bg_label = "🎨 Nền màu Gradient chuyển động" if bg_mode == "gradient" else "🎮 Video Minecraft Parkour"
    voice_label = "Nam Minh (Nam)" if "NamMinh" in voice else "Hoài My (Nữ)"

    print(f"""
╔═══════════════════════════════════════════════════════════╗
║          🎬  THREADS VIDEO AUTOMATION BOT  🎬             ║
║     AI Sáng tạo bài đăng + Nền màu / Gameplay 9:16        ║
╚═══════════════════════════════════════════════════════════╝
  ⚙️ Chế độ nền hiện tại : {bg_label}
  🎙️ Giọng đọc hiện tại  : {voice_label}
    """)


def toggle_settings():
    bg_mode, voice = get_current_settings()
    print("\n--- CÀI ĐẶT HỆ THỐNG ---")
    print(f"[1] Đổi chế độ nền (Hiện tại: {bg_mode})")
    print("    -> 'gradient' (Nền màu chuyển sắc sang trọng - Nhanh, nhẹ)")
    print("    -> 'minecraft' (Dùng video gameplay trong assets/backgrounds/)")
    print(f"[2] Đổi giọng đọc (Hiện tại: {'Nam' if 'NamMinh' in voice else 'Nữ'})")
    print("    -> '1': Giọng Nam Minh")
    print("    -> '2': Giọng Nữ Hoài My")

    sub = input("\nChọn mục [1/2]: ").strip()
    if sub == "1":
        new_mode = "minecraft" if bg_mode == "gradient" else "gradient"
        set_env_value("BG_MODE", new_mode)
        print(f"[✓] Đã đổi nền sang: {new_mode}")
    elif sub == "2":
        new_voice = "vi-VN-HoaiMyNeural" if "NamMinh" in voice else "vi-VN-NamMinhNeural"
        set_env_value("TTS_VOICE", new_voice)
        print(f"[✓] Đã đổi giọng sang: {'Nữ (Hoài My)' if 'HoaiMy' in new_voice else 'Nam (Nam Minh)'}")


def main():
    while True:
        print_banner()
        print("  [1] 🤖 Dùng Gemini AI tạo 1 video theo chủ đề")
        print("  [2] 🏭 Sản xuất HÀNG LOẠT video tự động (Batch Mode 3, 5, 10 video)")
        print("  [3] 🚀 Render video ngay từ kịch bản hiện tại (sample_post.json)")
        print("  [4] ⚙️ Đổi Chế độ Nền (Nền màu Gradient ↔ Minecraft) & Giọng đọc")
        print("  [5] 📝 Xem & chỉnh sửa trực tiếp nội dung bài đăng (JSON)")
        print("  [6] 🎨 Test Module 2: Render ảnh thẻ Threads (PNG)")
        print("  [7] 📂 Mở thư mục video thành phẩm (output/final/)")
        print("  [0] ❌ Thoát")
        print()

        choice = input("👉 Nhập số bạn muốn chọn [0-7]: ").strip()

        if choice == "1":
            subprocess.run([sys.executable, os.path.join(SRC_DIR, "01_ai_content.py")])
        elif choice == "2":
            subprocess.run([sys.executable, os.path.join(SRC_DIR, "07_batch_factory.py")])
        elif choice == "3":
            subprocess.run([sys.executable, os.path.join(SRC_DIR, "05_pipeline.py")])
        elif choice == "4":
            toggle_settings()
        elif choice == "5":
            subprocess.run([sys.executable, os.path.join(SRC_DIR, "01_content.py")])
        elif choice == "6":
            subprocess.run([sys.executable, os.path.join(SRC_DIR, "02_card_gen.py")])
        elif choice == "7":
            final_folder = os.path.join(BASE_DIR, "output", "final")
            if os.name == "nt":
                os.startfile(final_folder)
            else:
                subprocess.run(["xdg-open", final_folder])
        elif choice == "0":
            print("\nCảm ơn bạn đã sử dụng! Hẹn gặp lại.")
            break
        else:
            print("\nLựa chọn không hợp lệ, vui lòng chọn lại.")

        input("\nẤn [Enter] để tiếp tục...")
        clear_screen()


if __name__ == "__main__":
    main()
