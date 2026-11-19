import os, sys, json
from PIL import Image

SRC_DIR = os.path.abspath("src")
sys.path.insert(0, SRC_DIR)
sys.path.insert(0, os.path.abspath("."))

import importlib.util

# 1. Test Card Gen Module
spec_cg = importlib.util.spec_from_file_location("card_gen", os.path.join(SRC_DIR, "02_card_gen.py"))
cg = importlib.util.module_from_spec(spec_cg)
spec_cg.loader.exec_module(cg)

# 2. Test Content Module
spec_cnt = importlib.util.spec_from_file_location("content_base", os.path.join(SRC_DIR, "01_content.py"))
cnt = importlib.util.module_from_spec(spec_cnt)
spec_cnt.loader.exec_module(cnt)

# 3. Test App UI Helper
import app_ui

# Find a test image
illus_dir = os.path.join("assets", "illustrations")
test_imgs = [os.path.join(illus_dir, f) for f in os.listdir(illus_dir) if f.endswith(('.jpg', '.png'))] if os.path.exists(illus_dir) else []
test_img = test_imgs[0] if test_imgs else None
print(f"Test image found: {test_img}")

platforms = ["threads", "facebook", "instagram"]

for plat in platforms:
    print(f"\n--- Testing Platform: {plat.upper()} ---")
    
    post = cnt.Post(
        username=f"user_{plat}",
        content=f"Thử nghiệm nội dung bài đăng trên nền tảng {plat.upper()} siêu chân thực và sắc nét!",
        platform=plat,
        likes=35200,
        illustration_path=test_img,
        enable_illustration=True,
        comments=[
            cnt.Comment(
                username=f"cmt1_{plat}",
                content="Bình luận số 1 đồng cảm cực mạnh với bài viết của chủ thớt!",
                likes=1840,
                platform=plat
            ),
            cnt.Comment(
                username=f"cmt2_{plat}",
                content="Bình luận số 2 bóc mẽ chi tiết hài hước cười xỉu 😂",
                likes=960,
                platform=plat
            )
        ]
    )

    # Test Helper
    app_ui._render_all_cards_helper(post, theme="light", platform=plat)

    # Check output files
    cards_dir = os.path.join("output", "cards")
    post_file = os.path.join(cards_dir, "post.png")
    c1_file = os.path.join(cards_dir, "comment_1.png")
    c2_file = os.path.join(cards_dir, "comment_2.png")
    outro_file = os.path.join(cards_dir, "outro_cta.png")

    assert os.path.exists(post_file), f"Post card missing for {plat}"
    assert os.path.exists(c1_file), f"Comment 1 card missing for {plat}"
    assert os.path.exists(c2_file), f"Comment 2 card missing for {plat}"
    assert os.path.exists(outro_file), f"Outro CTA missing for {plat}"

    im_post = Image.open(post_file)
    im_c1 = Image.open(c1_file)
    im_outro = Image.open(outro_file)
    print(f"  [✓] Post card size: {im_post.size}")
    print(f"  [✓] Comment 1 size: {im_c1.size}")
    print(f"  [✓] Outro card size: {im_outro.size}")

    # Copy to distinct output names for user inspection
    os.makedirs("output/platform_samples", exist_ok=True)
    im_post.save(f"output/platform_samples/{plat}_post.png")
    im_c1.save(f"output/platform_samples/{plat}_comment.png")
    im_outro.save(f"output/platform_samples/{plat}_outro.png")

print("\n🎉 ALL PLATFORMS VERIFIED SUCCESSFULLY 100%!")
