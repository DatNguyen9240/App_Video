---
name: threads-video-automation-expert
description: >-
  Expert skill for auditing, optimizing, and extending the Threads Video Automation Studio codebase.
  Use when reviewing Python scripts, FFmpeg multi-layer compositing, Gemini AI script generation,
  TTS dynamic voice casting, and Flask UI pipelines.
---

# Threads Video Automation Expert

## Overview
This skill provides a structured framework for analyzing, debugging, and improving the **Threads Video Automation Studio 2.3** codebase. It covers pipeline tracking, Gemini script synthesis, multi-voice TTS management, FFmpeg 60 FPS compositing, and clean cutaway gag card generation.

---

## Core Architectural Pillars
When reviewing code related to this architecture, evaluate across these 5 modules:

### 1. AI Script Generation (`01_ai_content.py` & `01_content.py`)
* Verify prompt engineering for Gemini (`gemini-3.5-flash`), ensuring dynamic duration modes (`fast_30s`, `deep_60s`, `viral_story_90s`, `auto`).
* Check sentiment archetypes:
  * **Double Twist:** Twist at comment 2, then an even bigger twist at the final comment.
  * **Thread War:** Multiple commenters arguing and replying to each other (`reply_to`).
  * **Chain Exposure:** Each comment adds an escalating, embarrassing, or funny detail.
  * **Confession & Roast:** Author shares an awkward dilemma, comments compete to roast or give witty advice.
* Ensure proper dataclass structuring for posts, comments, metadata, and dynamic voice assignments.

### 2. Card & Visual Generation (`02_card_gen.py`)
* Inspect Threads card rendering:
  * Profile avatars (scraped or realistic fallbacks).
  * Verified badges, likes, reposts, comments, and share icons.
  * Smooth Ease-Out Slide-Up entrance animations.
* Verify Cutaway Gag pop-ups:
  * Punch-in Bounce motion in overlay.
  * 100% clean visual without text clutter (no badge, no caption banner unless explicitly requested).
  * Soft rounded borders (radius ~26px), subtle outer stroke, and deep 3D drop shadow.
  * Optimal display duration of 2.0s – 2.5s.
* Ensure correct Meme GIF and video sticker integration (`laugh`, `boom`, `bruh`, `side_eye`, `clown`, `math`, `ting`, `applause`, etc.).

### 3. Multi-Voice TTS & Audio Mastering (`03_tts.py` & `04_bg_audio.py`)
* Validate dynamic multi-voice casting:
  * Edge-TTS voices (`vi-VN-NamMinhNeural` / `vi-VN-HoaiMyNeural`).
  * Varied rate offsets (-8% to +25%) and pitch offsets (-10Hz to +14Hz) mapped to emotional styles (`cheerful`, `angry`, `shocked`, `sarcastic`, `sad`, `chat`).
* Check vocal mastering standards:
  * LUFS target at -14 LUFS.
  * Vocal chain filtering (High-pass 80Hz, presence boost +2.5dB, compression).
* Ensure dynamic background music ducking during active dialogue, restoring volume during punchlines and pauses.

### 4. FFmpeg 60 FPS Compositing (`05_pipeline.py`)
* Review multi-layer filter chains:
  * BT.709 color space and Visually Lossless CRF 18 encoding.
  * Native 60 FPS rendering.
  * Scale and overlay timing synchronization across audio and visual elements.
* Verify supported background video modes:
  * **3D Infinity Tunnel:** Mesmerizing neon tunnel loops.
  * **Gameplay Minecraft Parkour:** Full-screen vertical parkour (1080x1920).
  * **Gradient Motion:** Clean, subtle animated color backdrops.

### 5. Pipeline & Web UI Control (`pipeline_tracker.py` & `app_ui.py`)
* Monitor 6-stage production tracking:
  1. Script generation
  2. Card & visual generation
  3. Multi-voice TTS synthesis
  4. Audio mastering & BGM mixing
  5. FFmpeg master compositing
  6. Final export & packaging
* Verify real-time emotional pacing graphs and live console streaming.
* Verify Flask routes for settings management (`config/director_config.json`) and instant video preview playback.

---

## Audit Workflow
When auditing code or config files:

1. **Module Health Check:**
   * Identify which script files need modification or performance tuning.
   * Ensure backwards compatibility and prevent hardcoded constants.

2. **FFmpeg & UI Performance:**
   * Detect bottlenecks in video filter graph execution or threading issues in Flask background workers.
   * Verify temporary file cleanup (`output/audio/_silence_*.mp3`, `_bg_temp.mp4`).

3. **Actionable Patches:**
   * Provide exact Python snippets or JSON configuration updates (`config/director_config.json`).

4. **Verification Steps:**
   * Outline precise commands to run (e.g., `python app_ui.py`, pipeline runs, or preview extractions).

