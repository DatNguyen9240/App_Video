import os, sys, traceback

sys.path.insert(0, "src")
import visual_story_builder as builder

doc_dir = r"assets\documentary\trojan_tshirt"
clips_dir = os.path.join(doc_dir, "clips")

scenes = [
    {
        "type": "video",
        "path": os.path.join(clips_dir, "clip1_festival.mp4"),
        "text": "Năm 2011 tại Đức, tại một lễ hội nhạc rock của nhóm Tân Phát Xít, có một sự việc không tưởng đã diễn ra mà không ai trong số họ lường trước được.",
        "voice": "adam",
        "rate": "+16%",
        "sfx": "whoosh"
    },
    {
        "type": "image",
        "path": os.path.join(doc_dir, "trojan_before.jpg"),
        "text": "Một nhà tài trợ bí mật đã bất ngờ gửi tặng miễn phí 250 chiếc áo phông đen in hình đầu lâu cực ngầu kèm dòng chữ Những kẻ nổi loạn liều mạng.",
        "voice": "adam",
        "rate": "+16%",
        "sfx": "camera"
    },
    {
        "type": "video",
        "path": os.path.join(clips_dir, "clip2_distribution.mp4"),
        "text": "Nhìn chiếc áo quá chiến, hàng trăm thành viên cực đoan lập tức tranh nhau lấy và tự hào mặc ngay tại chỗ để quẩy suốt cả đêm.",
        "voice": "adam",
        "rate": "+16%",
        "sfx": "pop"
    },
    {
        "type": "video",
        "path": os.path.join(clips_dir, "clip3_washing.mp4"),
        "text": "Thế nhưng, cái bẫy thiên tài chỉ thực sự kích hoạt khi họ mang áo về nhà và bỏ vào máy giặt. Ngay khi tiếp xúc với bột giặt, lớp mực in đầu lâu đã tan biến sạch sành sanh!",
        "voice": "adam",
        "rate": "+18%",
        "sfx": "whoosh"
    },
    {
        "type": "image",
        "path": os.path.join(doc_dir, "trojan_after.jpg"),
        "text": "Và dưới lớp mực đó, một thông điệp vĩnh viễn lộ ra: Nếu chiếc áo này có thể thay đổi, bạn cũng có thể. Hãy liên hệ Exit Deutschland để từ bỏ chủ nghĩa cực đoan!",
        "voice": "adam",
        "rate": "+16%",
        "sfx": "boom"
    },
    {
        "type": "video",
        "path": os.path.join(clips_dir, "clip4_media_reaction.mp4"),
        "text": "Cú lừa ngoạn mục này đã biến 250 thành viên cực đoan thành 250 tấm biển quảng cáo di động, khiến giới thủ lĩnh hoang mang tột độ và làm chấn động truyền thông thế giới.",
        "voice": "adam",
        "rate": "+16%",
        "sfx": "laugh"
    }
]

try:
    res = builder.render_visual_documentary_video(
        story_title="CÚ LỪA 250 CHIẾC ÁO PHÔNG TÂN PHÁT XÍT",
        story_badge="🔴 HỒ SƠ KỲ THÚ • ĐỨC 2011",
        scenes=scenes,
        output_filename="trojan_tshirt_visual_documentary.mp4"
    )
    print("FINAL_SUCCESS_PATH:", res)
except Exception as e:
    print("EXCEPTION CAUGHT:", e)
    traceback.print_exc()
