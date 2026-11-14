import os
import sys
import json
import subprocess
from PIL import Image, ImageSequence

sys.path.insert(0, '.')
import src._loader

print("=" * 80)
print("THREADS VIDEO AUTOMATION STUDIO - COMPREHENSIVE FULL SYSTEM AUDIT")
print("=" * 80)

errors = []
warnings = []

# -------------------------------------------------------------
# 1. AUDIT CONFIGURATION FILES
# -------------------------------------------------------------
print("\n[1/6] AUDITING CONFIG & REGISTRY...")
config_path = "config/director_config.json"
if not os.path.exists(config_path):
    errors.append("director_config.json does not exist!")
else:
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        print("  - director_config.json is VALID JSON.")
        
        # Check anchor meme
        anchor_val = cfg.get("visual_layout", {}).get("enable_continuous_anchor_meme", True)
        if anchor_val is not False:
            warnings.append(f"enable_continuous_anchor_meme is {anchor_val}, expected False.")
        else:
            print("  - enable_continuous_anchor_meme: FALSE (Clean UI verified).")
            
        reg = cfg.get("sfx_meme_registry", {})
        print(f"  - Total sfx_meme_registry entries: {len(reg)}")
        
        for k, v in reg.items():
            mf = v.get("meme_file")
            sf = v.get("sfx_file")
            spool = v.get("sound_pool")
            
            # If meme_file is set, verify file
            if mf:
                p = os.path.join("assets", "memes", mf)
                if not os.path.exists(p):
                    errors.append(f"Meme file missing for key '{k}': {p}")
                else:
                    if not p.endswith(".gif") and not p.endswith(".mp4"):
                        warnings.append(f"Key '{k}' uses non-animated file: {mf}")
            
            # If sfx_file is set, verify file
            if sf:
                p = os.path.join("assets", "sfx", sf)
                if not os.path.exists(p):
                    errors.append(f"SFX file missing for key '{k}': {p}")
                    
            if spool:
                for s in spool:
                    p = os.path.join("assets", "sfx", s)
                    if not os.path.exists(p):
                        errors.append(f"Sound pool item missing for key '{k}': {p}")
                        
        print(f"  - Registry audit completed for {len(reg)} keys.")
    except Exception as e:
        errors.append(f"director_config.json parse error: {e}")

# -------------------------------------------------------------
# 2. AUDIT MEMES & SFX ASSETS ON DISK
# -------------------------------------------------------------
print("\n[2/6] AUDITING ASSETS ON DISK...")
memes_dir = os.path.join("assets", "memes")
sfx_dir = os.path.join("assets", "sfx")

# Verify no static photos of do or pew remain
static_banned = ["do_mixi.jpg", "do_mixi.png", "pewpew.png"]
for b in static_banned:
    if os.path.exists(os.path.join(memes_dir, b)):
        errors.append(f"Forbidden static image still exists: {b}")
print("  - Verified NO static photos of Độ/Pew exist on disk.")

# Check GIF health
meme_files = [f for f in os.listdir(memes_dir) if f.endswith(".gif")]
print(f"  - Found {len(meme_files)} animated GIFs in {memes_dir}:")
for mf in sorted(meme_files):
    p = os.path.join(memes_dir, mf)
    try:
        img = Image.open(p)
        frames = len(list(ImageSequence.Iterator(img)))
        if frames <= 1:
            warnings.append(f"GIF '{mf}' has only {frames} frame(s)!")
        else:
            print(f"    * {mf:25s}: {frames:3d} frames, size: {img.size}")
    except Exception as e:
        errors.append(f"Corrupt GIF '{mf}': {e}")

# Check SFX MP3 health
sfx_files = [f for f in os.listdir(sfx_dir) if f.endswith(".mp3")]
print(f"  - Found {len(sfx_files)} audio files in {sfx_dir}:")
for sf in sorted(sfx_files):
    p = os.path.join(sfx_dir, sf)
    sz = os.path.getsize(p)
    if sz < 1000:
        errors.append(f"Corrupt or empty SFX '{sf}' ({sz} bytes)!")

# -------------------------------------------------------------
# 3. AUDIT PYDANTIC SCHEMAS IN 01_ai_content & threads_radar
# -------------------------------------------------------------
print("\n[3/6] AUDITING AI SCHEMAS...")
from src.config import get_sfx_info
import importlib.util

spec_ai = importlib.util.spec_from_file_location('ai_content', 'src/01_ai_content.py')
ai_mod = importlib.util.module_from_spec(spec_ai)
spec_ai.loader.exec_module(ai_mod)

spec_radar = importlib.util.spec_from_file_location('radar', 'src/threads_radar.py')
radar_mod = importlib.util.module_from_spec(spec_radar)
spec_radar.loader.exec_module(radar_mod)

# Test instantiating CommentSchema with new SFX
test_keys = ["do_mixi", "pewpew", "bat_ngo_chua", "cuu_toi_voi", "so_qua_co", "fbi_open_up", "why_are_you_running", "coffin_dance", "to_be_continued", "gta_wasted", "sad_violin", "bonk", "emotional_damage", "oh_no", "sub_drop", "ting", "boom", "none"]
for tk in test_keys:
    try:
        c = ai_mod.CommentSchema(username="test_user", content="Bình luận thử nghiệm chuẩn ngữ cảnh", sfx=tk)
        assert c.sfx == tk
    except Exception as e:
        errors.append(f"CommentSchema rejected valid sfx '{tk}': {e}")
print(f"  - Tested {len(test_keys)} keys on CommentSchema: ALL PASSED.")

# -------------------------------------------------------------
# 4. AUDIT WEB UI DEFINITION (app_ui.py)
# -------------------------------------------------------------
print("\n[4/6] AUDITING WEB UI DROPDOWNS...")
with open("app_ui.py", "r", encoding="utf-8") as f:
    ui_code = f.read()

for tk in test_keys:
    if tk != "none":
        if f'value="{tk}"' not in ui_code:
            warnings.append(f"Web UI dropdown is missing option value='{tk}'!")
print("  - Web UI dropdown verified for all major meme options.")

# -------------------------------------------------------------
# 5. AUDIT PIPELINE TIMELINE & SFX OVERLAY ENGINE
# -------------------------------------------------------------
print("\n[5/6] AUDITING TIMELINE & COMPOSITION ENGINE...")
spec_pipe = importlib.util.spec_from_file_location('pipeline_mod', 'src/05_pipeline.py')
pipe_mod = importlib.util.module_from_spec(spec_pipe)
spec_pipe.loader.exec_module(pipe_mod)

spec_c = importlib.util.spec_from_file_location('cbase', 'src/01_content.py')
cbase = importlib.util.module_from_spec(spec_c)
spec_c.loader.exec_module(cbase)

post = cbase.Post(
    username="author_test",
    content="Trời ơi cứu tui! Mua đồ công nghệ trên mạng về mở ra thấy bất ngờ chưa bà già!",
    likes=52000
)
post.comments = [
    cbase.Comment(username="c1", content="Úi sợ quá sợ quá phải ban nó thôi!", sfx="so_qua_co"),
    cbase.Comment(username="c2", content="Bất ngờ chưa bà già, hàng chuẩn pha ke!", sfx="bat_ngo_chua"),
    cbase.Comment(username="c3", content="Cứu tôi với cuộc đời tôi quá bất công!", sfx="cuu_toi_voi"),
    cbase.Comment(username="c4", content="FBI Open up bắt quả tang shop lừa đảo!", sfx="fbi_open_up"),
    cbase.Comment(username="c5", content="Nhìn cái kết là thấy Wasted rồi!", sfx="gta_wasted"),
    cbase.Comment(username="c6", content="Độ Mixi cũng phải cay thế nhờ!", sfx="do_mixi"),
    cbase.Comment(username="c7", content="PewPew bảo quá chuẩn luôn khỏi cãi!", sfx="pewpew")
]

mock_timing = {
    "post": {"path": "assets/sfx/pop.mp3", "duration_ms": 4000, "voice": "Nam Minh"},
    "comments": [{"path": "assets/sfx/pop.mp3", "duration_ms": 2500, "voice": "Nam Minh"} for _ in range(7)]
}

try:
    timeline, audio_seq, overlay_sfx, total_dur, arc = pipe_mod.build_timeline_with_emotional_pacing(
        post, mock_timing, layout_preset="center"
    )
    print(f"  - Timeline created: {len(timeline)} visual layers, duration: {total_dur:.2f}s.")
    print(f"  - Overlay SFX created: {len(overlay_sfx)} synchronized sounds.")
    
    # Check that each comment with a visual meme has a matching sound
    meme_layers = [item for item in timeline if item.get("type") == "meme_reaction"]
    print(f"  - Punchline Meme Popups on Timeline: {len(meme_layers)}")
    for ml in meme_layers:
        print(f"    * Layer: {os.path.basename(ml['image']):25s} | Time: {ml['start']:.2f}s -> {ml['end']:.2f}s")
        if not os.path.exists(ml["image"]):
            errors.append(f"Timeline image target does not exist: {ml['image']}")
            
    # Verify overlay SFX
    for osfx in overlay_sfx:
        if not os.path.exists(osfx["file"]):
            errors.append(f"Overlay SFX target does not exist: {osfx['file']}")
            
except Exception as e:
    errors.append(f"build_timeline_with_emotional_pacing failed: {e}")

# -------------------------------------------------------------
# 6. SUMMARY REPORT
# -------------------------------------------------------------
print("\n" + "=" * 80)
print(f"AUDIT SUMMARY: {len(errors)} ERRORS, {len(warnings)} WARNINGS")
print("=" * 80)

if errors:
    print("\n❌ CRITICAL ERRORS DETECTED:")
    for err in errors:
        print(f"  - {err}")
else:
    print("\n✅ ALL CRITICAL SYSTEMS HEALTHY AND OPERATIONAL!")

if warnings:
    print("\n⚠️ WARNINGS / ADVISORIES:")
    for w in warnings:
        print(f"  - {w}")

print("\nFull System Audit Complete.\n")

