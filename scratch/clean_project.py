import os
import shutil

print("--- CLEANING UP TEMPORARY AND CACHE FILES ---")

reclaimed_bytes = 0
deleted_count = 0

def safe_remove(p):
    global reclaimed_bytes, deleted_count
    try:
        if os.path.isfile(p):
            sz = os.path.getsize(p)
            os.remove(p)
            reclaimed_bytes += sz
            deleted_count += 1
        elif os.path.isdir(p):
            shutil.rmtree(p)
            deleted_count += 1
    except Exception as e:
        print(f"  [!] Could not delete {p}: {e}")

# 1. Clean .tmp files in output/illustrations
illus_dir = os.path.join("output", "illustrations")
if os.path.exists(illus_dir):
    for f in os.listdir(illus_dir):
        if ".tmp" in f:
            safe_remove(os.path.join(illus_dir, f))

# 2. Clean temporary silence and old test audio files in output/audio
audio_dir = os.path.join("output", "audio")
if os.path.exists(audio_dir):
    for f in os.listdir(audio_dir):
        if f.startswith("_silence_") or f.startswith("test_") or f.startswith("raw_"):
            safe_remove(os.path.join(audio_dir, f))

# 3. Clean debug frames in output/debug_frames
debug_dir = os.path.join("output", "debug_frames")
if os.path.exists(debug_dir):
    for f in os.listdir(debug_dir):
        safe_remove(os.path.join(debug_dir, f))

# 4. Clean Python __pycache__ folders
for root, dirs, files in os.walk("."):
    for d in dirs:
        if d == "__pycache__":
            p = os.path.join(root, d)
            safe_remove(p)

print(f"Done! Deleted {deleted_count} disposable items.")
print(f"Total space freed: {reclaimed_bytes / (1024*1024):.2f} MB.")

