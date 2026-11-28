"""
MODULE 3: TẠO GIỌNG ĐỌC TIẾNG VIỆT ĐA VAI & CẢM XÚC (OmniVoice Neural TTS Engine)
======================================================================================
Chức năng:
  - Diễn xuất giọng nói đa vai bằng mô hình OmniVoice (k2-fsa/OmniVoice) hỗ trợ tiếng Việt
    tự nhiên, Voice Design (theo mô tả) và Voice Cloning (từ file mẫu trong assets/voices/).
  - Hỗ trợ biến thiên cao độ (Pitch), tốc độ (Rate) theo cảm xúc từng câu
  - Tạo khoảng lặng tự nhiên (Breathing Room / Dramatic Pauses)
  - Đa tầng dự phòng chống sập 100%:
    + Tầng 1: OmniVoice Neural TTS (Voice Design / Voice Cloning)
    + Tầng 2: Google Translate TTS (gTTS) nếu chưa tải model / lỗi mạng
    + Tầng 3: Windows SAPI5 / Fallback tone an toàn
  - Tự động chuẩn hóa MP3 bằng FFmpeg 44100Hz Stereo sạch 100%.
"""

import os
import re
import time
import shutil
import threading
import subprocess
from typing import Optional, Callable

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR  = os.path.join(BASE_DIR, "src")
ENV_PATH = os.path.join(BASE_DIR, ".env")
OUT_DIR  = os.path.join(BASE_DIR, "output", "audio")

import sys
sys.path.insert(0, SRC_DIR)
import config as config_mod


def get_ffmpeg_exe() -> str:
    sys_ffmpeg = shutil.which("ffmpeg")
    if sys_ffmpeg:
        return sys_ffmpeg
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return "ffmpeg"


def get_configured_voice_and_rate() -> tuple[str, str]:
    cfg = config_mod.load_config()
    voice_cfg = cfg.get("voice_casting", {})
    voice = voice_cfg.get("default_author_voice", "nam_hai")
    rate = voice_cfg.get("roles", {}).get("author", {}).get("rate", "+0%")
    if os.path.exists(ENV_PATH):
        try:
            with open(ENV_PATH, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("TTS_VOICE="):
                        val = line.split("=", 1)[1].strip()
                        if val: voice = val
                    elif line.startswith("TTS_RATE="):
                        val = line.split("=", 1)[1].strip()
                        if val: rate = val
        except Exception:
            pass
    return voice, rate


import unicodedata

DEFAULT_VOICE, DEFAULT_RATE = get_configured_voice_and_rate()


def clean_for_tts(text: str) -> str:
    """Loại bỏ emoji, ký tự đặc biệt gây lỗi SSML và chuẩn hóa các ký hiệu %, số, từ tiếng Anh sang tiếng Việt chuẩn."""
    cleaned = text.replace("%", " phần trăm ").replace("&", " và ").replace("@", " a còng ")
    # Chuyển đổi các từ tiếng Anh / tiếng lóng hay bị Edge-TTS từ chối (NoAudioReceived)
    replacements = {
        "cotton 100%": "vải sợi bông một trăm phần trăm",
        "cotton": "vải sợi bông",
        "unisex": "thời trang",
        "Shop": "Người bán",
        "shop": "người bán",
        "199k": "hai trăm nghìn",
        "200k": "hai trăm nghìn",
        "cành": "nghìn",
        "áo lót": "áo hai dây",
        "quần lót": "đồ mặc trong"
    }
    for k, v in replacements.items():
        cleaned = re.sub(re.escape(k), v, cleaned, flags=re.IGNORECASE)

    cleaned = re.sub(r"[^\u0000-\u2BFF]+", " ", cleaned)
    for ch in ["'", '"', "‘", "’", "“", "”", "`", "<", ">", "\\", "/", "^", "|", "[", "]", "{", "}"]:
        cleaned = cleaned.replace(ch, " ")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return unicodedata.normalize("NFC", cleaned)


def apply_spatial_panning(filepath: str, direction: str = "center") -> bool:
    """
    Tạo hiệu ứng âm thanh không gian 3D (Spatial Stereo Panning):
    - Tác giả: Center (cân bằng trực diện)
    - Comment lẻ: Lệch nhẹ sang trái 20%
    - Comment chẵn: Lệch nhẹ sang phải 20%
    Tạo cảm giác chân thực như đang ngồi giữa bàn cà phê nghe bạn bè đối đáp qua lại.
    """
    if direction == "center" or not os.path.exists(filepath):
        return True
    exe = get_ffmpeg_exe()
    temp_pan = filepath + ".pan.mp3"
    if direction == "left":
        pan_filter = "pan=stereo|c0=0.92*c0+0.08*c1|c1=0.08*c0+0.72*c1"
    elif direction == "right":
        pan_filter = "pan=stereo|c0=0.72*c0+0.08*c1|c1=0.08*c0+0.92*c1"
    else:
        return True

    cmd = [exe, "-y", "-i", filepath, "-af", pan_filter, "-c:a", "libmp3lame", "-b:a", "256k", temp_pan]
    res = subprocess.run(cmd, capture_output=True)
    if res.returncode == 0 and os.path.exists(temp_pan):
        shutil.move(temp_pan, filepath)
        return True
    if os.path.exists(temp_pan):
        try: os.remove(temp_pan)
        except OSError: pass
    return False


def verify_and_standardize_mp3(filepath: str, apply_mastering: bool = True, extra_filter: str = "") -> bool:
    """
    Dùng FFmpeg kiểm tra và chuẩn hóa file MP3 về 44100Hz Stereo.
    Tự động áp dụng Vocal Mastering Chain (EQ, Compressor, Normalization -14 LUFS)
    giúp giọng đọc ấm, sáng, rõ tiếng như thu âm trong phòng thu podcast chuyên nghiệp.
    Hỗ trợ extra_filter để biến điệu pitch/tempo tạo các giọng hài hước, cà khịa.
    """
    if not os.path.exists(filepath) or os.path.getsize(filepath) < 200:
        return False

    exe = get_ffmpeg_exe()
    clean_temp = filepath + ".std.mp3"

    cfg = config_mod.load_config()
    mast = cfg.get("audio_mastering", {})
    bitrate = mast.get("audio_bitrate", "256k")

    af_filters = []
    if extra_filter:
        af_filters.append(extra_filter)

    # Giữ nguyên độ trong trẻo 100% nguyên bản từ phòng thu Neural TTS của Microsoft,
    # không dùng compressor, equalizer hay loudnorm làm méo sóng hay gây tiếng rè.
    cmd = [
        exe, "-y",
        "-i", filepath,
        "-vn",
        "-ar", "44100",
        "-ac", "2",
        *(["-af", ",".join(af_filters)] if af_filters else []),
        "-c:a", "libmp3lame",
        "-b:a", "320k",
        clean_temp
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode == 0 and os.path.exists(clean_temp) and os.path.getsize(clean_temp) > 500:
        shutil.move(clean_temp, filepath)
        return True

    if os.path.exists(clean_temp):
        try: os.remove(clean_temp)
        except OSError: pass
    return False


def generate_silence_mp3(duration_sec: float, output_path: str) -> str:
    """Tạo một file âm thanh im lặng đúng mili-giây để chèn khoảng thở kịch tính."""
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    exe = get_ffmpeg_exe()
    duration_sec = max(0.05, duration_sec)
    cmd = [
        exe, "-y",
        "-f", "lavfi",
        "-i", f"anullsrc=r=44100:cl=stereo",
        "-t", f"{duration_sec:.3f}",
        "-c:a", "libmp3lame",
        "-b:a", "192k",
        output_path
    ]
    subprocess.run(cmd, capture_output=True, text=True)
    return output_path


# ─── BỘ HÌNH TƯỢNG GIỌNG ĐỌC HÀI HƯỚC & ĐA VAI DIỄN VIỆT NAM (GEN Z / MEME) ─
VOICE_PROFILES = {
    # 1. Chị Google / Nữ Dẫn Chuyện: Hoài My tốc độ lướt TikTok (+18% native neural), trong trẻo 100%, không rè
    "chi_google": {
        "base_voice": "vi-VN-HoaiMyNeural",
        "rate": "+18%",
        "af_filter": "",
        "name": "Hoài My Gen Z (Tự nhiên, sắc nét, hoàn toàn không rè)",
        "gender": "female"
    },
    # 2. Nữ Cà Khịa Gen Z: Hoài My tốc độ TikTok (+18%), đanh đá, sắc bén, dứt khoát
    "nu_cakhia": {
        "base_voice": "vi-VN-HoaiMyNeural",
        "rate": "+18%",
        "af_filter": "",
        "name": "Nữ Cà Khịa Gen Z (Đanh đá, hài hước, tốc độ cao)",
        "gender": "female"
    },
    # 3. Nữ Nhí Nhảnh: Hoài My vui tươi, nhanh nhẹn (+18%)
    "nu_nhinhanh": {
        "base_voice": "vi-VN-HoaiMyNeural",
        "rate": "+18%",
        "af_filter": "",
        "name": "Nữ Nhí Nhảnh (Vui tươi, dễ thương, hài nhẹ)",
        "gender": "female"
    },
    # 4. Nam Hài: Giọng Nam Minh chuẩn Việt, tốc độ nói tự nhiên +15%, âm sắc ấm áp, rõ ràng không rè
    "nam_hai": {
        "base_voice": "vi-VN-NamMinhNeural",
        "rate": "+15%",
        "af_filter": "",
        "name": "Nam Hài Hước (Dí dỏm, nhanh nhảu, âm thanh sạch không rè)",
        "gender": "male"
    },
    # 5. Nam Trầm Hài: Giọng nam trầm ấm, điện ảnh
    "nam_tram_hai": {
        "base_voice": "nam_tram_hai",
        "instruct": "male, middle-aged, low pitch",
        "rate": "+10%",
        "af_filter": "",
        "name": "Nam Trầm Hài (OmniVoice - Trầm ấm, truyền cảm)",
        "gender": "male"
    },
    # 6. Nam Minh: Giọng nam chuẩn Việt Nam rõ ràng, truyền cảm
    "nam_minh": {
        "base_voice": "vi-VN-NamMinhNeural",
        "rate": "+15%",
        "af_filter": "",
        "name": "Nam Minh (Chuẩn tiếng Việt, rõ nét không rè)",
        "gender": "male"
    },
    # 7. Hoài My Chuẩn: Hoài My Gen Z tốc độ lướt TikTok (+18%)
    "hoai_my": {
        "base_voice": "vi-VN-HoaiMyNeural",
        "rate": "+18%",
        "af_filter": "",
        "name": "Hoài My Gen Z (Tự nhiên, tốc độ lướt TikTok)",
        "gender": "female"
    }
}


def get_voice_filter(voice: str) -> Optional[str]:
    """Trả về audio filter (pitch + speed shift) định hình phong cách giọng hài hước."""
    if not voice:
        return None
    v_clean = voice.lower().strip()
    if v_clean in VOICE_PROFILES:
        return VOICE_PROFILES[v_clean].get("af_filter")
    if v_clean in VOICE_MAP:
        mapped = VOICE_MAP[v_clean]
        if mapped in VOICE_PROFILES:
            return VOICE_PROFILES[mapped].get("af_filter")
    return None


# ─── BỘ ÁNH XẠ GIỌNG ĐỌC THU HÚT & NỔI TIẾNG (ADAM, BRIAN, NAM MINH, HOÀI MY) ─
VOICE_MAP = {
    # Giọng Hài Hước Tiếng Việt (Gen Z Meme / Threads viral)
    "nam_hai": "nam_hai",
    "funny_male": "nam_hai",
    "nam_hai_huoc": "nam_hai",
    "nam_tung_tung": "nam_hai",
    "nu_cakhia": "nu_cakhia",
    "funny_female": "nu_cakhia",
    "nu_hai_huoc": "nu_cakhia",
    "nu_danh_da": "nu_cakhia",
    "nam_tram_hai": "nam_tram_hai",
    "funny_deadpan": "nam_tram_hai",
    "nam_bua": "nam_tram_hai",
    "nam_can_loi": "nam_tram_hai",
    "nu_nhinhanh": "nu_nhinhanh",
    "funny_cheerful": "nu_nhinhanh",
    "chi_google": "chi_google",

    # Giọng Nam / Nữ chuẩn Việt Nam
    "nam_minh": "nam_minh",
    "vi-vn-namminhneural": "nam_hai",
    "hoai_my": "hoai_my",
    "vi-vn-hoaimyneural": "hoai_my",
    "minh_quan": "nam_minh",
    "bao_long": "nam_minh",
    "khanh_linh": "hoai_my",
    "thu_quynh": "hoai_my",
    "nam": "nam_hai",
    "male": "nam_hai",
    "nu": "nu_cakhia",
    "female": "nu_cakhia",

    # Giọng dẫn chuyện podcast (OmniVoice)
    "adam": "nam_hai",
    "andrew": "nam_tram_hai",
    "brian": "nam_hai",
    "ava": "hoai_my",
    "emma": "hoai_my",
}


def resolve_voice_id(voice: str) -> str:
    """Chuyển đổi tên voice thân thiện sang mã voice Edge-TTS chuẩn."""
    if not voice:
        return "vi-VN-NamMinhNeural"
    v_clean = voice.lower().strip()
    if v_clean in VOICE_PROFILES:
        return VOICE_PROFILES[v_clean]["base_voice"]
    if v_clean in VOICE_MAP:
        mapped = VOICE_MAP[v_clean]
        if mapped in VOICE_PROFILES:
            return VOICE_PROFILES[mapped]["base_voice"]
        return mapped
    return voice


def synthesize_elevenlabs(text: str, output_path: str, voice_id: str = "pNInz6obpgDQGcFmaJgB") -> bool:
    """Tạo giọng đọc cao cấp qua ElevenLabs API (Giọng Adam gốc ID: pNInz6obpgDQGcFmaJgB)."""
    api_key = os.environ.get("ELEVENLABS_API_KEY", "").strip()
    if not api_key and os.path.exists(ENV_PATH):
        try:
            with open(ENV_PATH, "r", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("ELEVENLABS_API_KEY="):
                        api_key = line.split("=", 1)[1].strip().strip('"').strip("'")
        except Exception:
            pass

    if not api_key:
        return False

    try:
        import urllib.request
        import json
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
        headers = {
            "xi-api-key": api_key,
            "Content-Type": "application/json"
        }
        payload = {
            "text": text,
            "model_id": "eleven_multilingual_v2",
            "voice_settings": {
                "stability": 0.45,
                "similarity_boost": 0.85
            }
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers)
        with urllib.request.urlopen(req, timeout=18) as resp:
            raw_audio = resp.read()
        if len(raw_audio) > 1000:
            with open(output_path, "wb") as f:
                f.write(raw_audio)
            print(f"  🎙️ [ELEVENLABS TTS]: Đã tạo giọng Adam gốc đỉnh cao cho {os.path.basename(output_path)}")
            return verify_and_standardize_mp3(output_path)
    except Exception as e:
        print(f"  ⚠️ ElevenLabs TTS ({voice_id}) lỗi hoặc hết quota: {e}")
    return False


_omnivoice_lock = threading.Lock()
_omnivoice_model = None
VOICES_REF_DIR = os.path.join(BASE_DIR, "assets", "voices")


def _get_omnivoice_model():
    """Khởi tạo và cache mô hình k2-fsa/OmniVoice (hỗ trợ GPU CUDA hoặc CPU)."""
    global _omnivoice_model
    if _omnivoice_model is not None:
        return _omnivoice_model
    with _omnivoice_lock:
        if _omnivoice_model is not None:
            return _omnivoice_model
        import torch
        from omnivoice import OmniVoice
        model_id = os.environ.get("OMNIVOICE_MODEL", "k2-fsa/OmniVoice").strip() or "k2-fsa/OmniVoice"
        use_cuda = torch.cuda.is_available()
        device_map = "cuda:0" if use_cuda else "cpu"
        dtype = torch.float16 if use_cuda else torch.float32
        print(f"  🎙️ [OMNIVOICE]: Đang nạp mô hình {model_id} trên {device_map}...")
        _omnivoice_model = OmniVoice.from_pretrained(
            model_id,
            device_map=device_map,
            dtype=dtype,
            load_asr=False,
        )
        return _omnivoice_model


def _get_omnivoice_instruct(voice: str) -> str:
    v_clean = (voice or "nam_hai").lower().strip()
    mapped = VOICE_MAP.get(v_clean, v_clean)
    prof = VOICE_PROFILES.get(mapped) or VOICE_PROFILES.get(v_clean) or {}
    if prof.get("instruct"):
        return prof["instruct"]
    if mapped == "nu_cakhia":
        return "female, young adult, high pitch"
    if mapped == "nu_nhinhanh":
        return "female, teenager, high pitch"
    is_female = any(k in v_clean for k in ("nu", "female", "hoai", "ava", "emma", "chi_google", "khanh", "thu"))
    if is_female:
        return "female, young adult, moderate pitch"
    return "male, young adult, moderate pitch"


def _find_ref_voice_file(voice: str) -> Optional[str]:
    if not os.path.isdir(VOICES_REF_DIR):
        return None
    v_clean = (voice or "").lower().strip()
    mapped = VOICE_MAP.get(v_clean, v_clean)
    for cand_name in (v_clean, mapped):
        for ext in (".wav", ".mp3", ".flac"):
            p = os.path.join(VOICES_REF_DIR, f"{cand_name}{ext}")
            if os.path.exists(p) and os.path.getsize(p) > 2000:
                return p
    return None


def synthesize_omnivoice(text: str, output_path: str, voice: str = "nam_hai", rate: str = "+0%", pitch: str = "+0Hz", style: str = "", extra_filter: str = "") -> bool:
    """
    Tạo audio tiếng Việt tự nhiên bằng k2-fsa/OmniVoice:
    - Nếu có file mẫu trong assets/voices/<voice>.wav(.mp3): tự động Voice Cloning.
    - Ngược lại: dùng Voice Design (instruct) theo nhân vật.
    """
    try:
        import soundfile as sf
        if os.path.exists(output_path):
            try: os.remove(output_path)
            except OSError: pass

        cleaned = clean_for_tts(text)
        if not cleaned:
            return False

        model = _get_omnivoice_model()
        try:
            r_num = int(str(rate or "+0%").replace("%", "").replace("+", "").strip() or 0)
            speed = max(0.85, min(1.30, 1.0 + r_num / 100.0))
        except Exception:
            speed = 1.0

        ref_audio = _find_ref_voice_file(voice)
        gen_kwargs = {
            "text": cleaned,
            "language": "vietnamese",
            "speed": speed,
        }
        if ref_audio:
            gen_kwargs["ref_audio"] = ref_audio
        else:
            gen_kwargs["instruct"] = _get_omnivoice_instruct(voice)

        audio = model.generate(**gen_kwargs)
        if not audio or len(audio) == 0:
            return False

        temp_wav = output_path + ".omni.wav"
        sf.write(temp_wav, audio[0], 24000)

        exe = get_ffmpeg_exe()
        cmd = [exe, "-y", "-i", temp_wav, "-ar", "44100", "-ac", "2", "-c:a", "libmp3lame", "-b:a", "256k", output_path]
        subprocess.run(cmd, capture_output=True)
        try: os.remove(temp_wav)
        except OSError: pass

        if os.path.exists(output_path) and os.path.getsize(output_path) > 400:
            return verify_and_standardize_mp3(output_path, extra_filter=extra_filter)
        return False
    except Exception as e:
        print(f"  ⚠️ OmniVoice TTS lỗi ({e})")
        if os.path.exists(output_path):
            try: os.remove(output_path)
            except OSError: pass
        return False


def synthesize_gtts(text: str, output_path: str, extra_filter: str = "") -> bool:
    """Tạo audio qua Google Translate TTS (Chị Google chuẩn nguyên bản, deadpan meme kinh điển)."""
    try:
        from gtts import gTTS
        if os.path.exists(output_path):
            try: os.remove(output_path)
            except OSError: pass

        tts = gTTS(text=text, lang="vi", slow=False)
        tts.save(output_path)
        return verify_and_standardize_mp3(output_path, extra_filter=extra_filter)
    except Exception:
        if os.path.exists(output_path):
            try: os.remove(output_path)
            except OSError: pass
        return False


def synthesize_windows_sapi(text: str, output_path: str, extra_filter: str = "") -> bool:
    """Dự phòng offline qua Windows SAPI5 SpeechSynthesizer."""
    try:
        exe = get_ffmpeg_exe()
        temp_wav = output_path.replace(".mp3", ".wav")
        ps_script = f"""
Add-Type -AssemblyName System.Speech;
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer;
$synth.SetOutputToWaveFile('{temp_wav}');
$synth.Speak('{text}');
$synth.Dispose();
"""
        res = subprocess.run(["powershell", "-NoProfile", "-Command", ps_script], capture_output=True, text=True)
        if os.path.exists(temp_wav) and os.path.getsize(temp_wav) > 100:
            subprocess.run([exe, "-y", "-i", temp_wav, "-ar", "44100", "-ac", "2", "-c:a", "libmp3lame", "-b:a", "192k", output_path], capture_output=True)
            try: os.remove(temp_wav)
            except OSError: pass
            return verify_and_standardize_mp3(output_path, extra_filter=extra_filter)
    except Exception:
        pass
    return False


def synthesize_fallback_tone(text: str, output_path: str) -> bool:
    """Tầng 4 chống sập tuyệt đối: Nếu mất mạng hoàn toàn và Windows không có giọng SAPI,
    tạo audio placeholder khớp độ dài câu chữ để pipeline vẫn chạy mượt mà không crash."""
    try:
        exe = get_ffmpeg_exe()
        word_count = max(2, len(text.split()))
        dur = max(1.5, word_count * 0.28)
        cmd = [
            exe, "-y",
            "-f", "lavfi",
            "-i", f"sine=frequency=350:duration={dur:.2f}",
            "-ar", "44100",
            "-ac", "2",
            "-c:a", "libmp3lame",
            "-b:a", "192k",
            output_path
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        return res.returncode == 0 and os.path.exists(output_path)
    except Exception:
        return False


def text_to_speech(text: str, output_path: str,
                   voice: str = DEFAULT_VOICE,
                   rate: str = DEFAULT_RATE,
                   pitch: str = "+0Hz",
                   style: str = "",
                   extra_filter: Optional[str] = None,
                   max_retries: int = 2) -> str:
    """
    Quy trình chuyển văn bản thành tiếng Việt đa tầng tự động phục hồi:
    0. Thử ElevenLabs nếu có cấu hình API Key.
    1. Thử Edge TTS với biến điệu phong cách hài hước (Nam Hài, Nữ Cà Khịa, Nam Trầm Hài...).
    2. Fallback sang Google Translate TTS.
    3. Fallback sang Windows Speech SAPI5 offline.
    4. Fallback tone an toàn tuyệt đối.
    """
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    cleaned = clean_for_tts(text)

    # Tự động đồng bộ tốc độ nói tự nhiên theo hình tượng giọng đọc nếu chưa truyền tốc độ đặc thù
    if rate in ["+0%", "0%", "", None]:
        v_clean = voice.lower().strip()
        if v_clean in VOICE_PROFILES:
            rate = VOICE_PROFILES[v_clean].get("rate", "+18%")
        elif v_clean in VOICE_MAP and VOICE_MAP[v_clean] in VOICE_PROFILES:
            rate = VOICE_PROFILES[VOICE_MAP[v_clean]].get("rate", "+18%")
        else:
            rate = "+18%" if any(k in v_clean for k in ["female", "nu", "hoai"]) else "+15%"

    # Lấy filter định hình giọng hài hước nếu chưa được truyền tường minh
    if extra_filter is None:
        extra_filter = get_voice_filter(voice) or ""

    # 0. Thử ElevenLabs nếu yêu cầu giọng Adam hoặc chỉ định elevenlabs
    v_lower = voice.lower().strip()
    if v_lower in ["adam", "elevenlabs:adam"] or v_lower.startswith("elevenlabs:"):
        eleven_id = "pNInz6obpgDQGcFmaJgB"  # Adam voice ID
        if ":" in voice:
            parts = voice.split(":", 1)
            if len(parts) > 1 and parts[1].strip() != "adam":
                eleven_id = parts[1].strip()
        if synthesize_elevenlabs(cleaned, output_path, eleven_id):
            return output_path

    # 0b. Chỉ dùng gTTS nếu người dùng cố ý chỉ định gtts / legacy_gtts
    if v_lower in ["gtts", "legacy_gtts", "robot_google"]:
        print(f"     🤖 [LEGACY GTTS]: Đang tạo giọng Google TTS nguyên bản cho {os.path.basename(output_path)}...")
        if synthesize_gtts(cleaned, output_path, extra_filter=extra_filter):
            return output_path

    # 1. Tầng chính: OmniVoice (k2-fsa/OmniVoice)
    if synthesize_omnivoice(cleaned, output_path, voice=voice, rate=rate, pitch=pitch, style=style, extra_filter=extra_filter):
        return output_path

    # 2. Dự phòng gTTS tiếng Việt nếu OmniVoice gặp sự cố
    print(f"     ⚠️ OmniVoice chưa sẵn sàng ({voice}), đang dùng dự phòng gTTS...")
    if synthesize_gtts(cleaned, output_path, extra_filter=extra_filter):
        return output_path

    # 3. Fallback Windows SAPI5 offline
    if synthesize_windows_sapi(cleaned, output_path, extra_filter=extra_filter):
        return output_path

    # 4. Fallback Tone
    print("     ⚠️ Đang tạo audio placeholder bảo vệ quy trình không bị gián đoạn...")
    if synthesize_fallback_tone(cleaned, output_path):
        return output_path

    raise RuntimeError(f"Không thể tạo audio hợp lệ cho: {output_path}")


def get_mp3_duration_ms(filepath: str) -> float:
    """Lấy thời lượng chính xác của file MP3 qua FFmpeg."""
    try:
        exe = get_ffmpeg_exe()
        res = subprocess.run([exe, "-i", filepath], capture_output=True, text=True)
        m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", res.stderr)
        if m:
            h, mn, s = m.groups()
            return (int(h) * 3600 + int(mn) * 60 + float(s)) * 1000
    except Exception:
        pass
    return 3000.0


def generate_all_audio(
    post,
    out_dir: str = OUT_DIR,
    default_voice: str = None,
    progress_cb: Optional[Callable[[str, int], None]] = None
) -> dict:
    """
    Tạo audio cho bài đăng và từng comment với phân vai đa giọng đọc, cảm xúc và SSML style riêng biệt.
    """
    os.makedirs(out_dir, exist_ok=True)
    cfg = config_mod.load_config()
    voice_cfg = cfg.get("voice_casting", {})
    emotion_map = voice_cfg.get("emotion_style_map", {})

    if not default_voice:
        default_voice = voice_cfg.get("default_author_voice", "nam_hai")

    result = {
        "post": None,
        "comments": [],
        "pauses": {}
    }

    # 1. Diễn xuất bài đăng tác giả
    post_voice = getattr(post, "voice_id", "") or default_voice
    post_rate = getattr(post, "rate_offset", "") or voice_cfg.get("roles", {}).get("author", {}).get("rate", "+0%")
    post_pitch = getattr(post, "pitch_offset", "") or voice_cfg.get("roles", {}).get("author", {}).get("pitch", "+0Hz")
    post_style = ""  # Tác giả kể chuyện tự nhiên

    if progress_cb:
        progress_cb(f"Tạo giọng đọc bài viết chính [{post_voice}]...", 15)

    print(f"  🎙️ Đang tạo audio bài đăng ({post_voice}, rate={post_rate}, pitch={post_pitch})...")
    post_path = os.path.join(out_dir, "post.mp3")
    text_to_speech(post.content, post_path, voice=post_voice, rate=post_rate, pitch=post_pitch, style=post_style)
    dur = get_mp3_duration_ms(post_path)
    result["post"] = {
        "path": post_path,
        "duration_ms": dur,
        "voice": post_voice,
        "rate": post_rate,
        "pitch": post_pitch
    }
    print(f"     → {dur/1000:.2f}s")
    time.sleep(0.3)

    # 2. Diễn xuất từng bình luận (với SSML emotion style)
    total_cmt = len(post.comments)
    for i, comment in enumerate(post.comments, 1):
        pct = 20 + int((i / max(1, total_cmt)) * 60)
        c_voice = getattr(comment, "voice_id", "") or voice_cfg.get("default_commenter_female_voice", "nu_cakhia")
        role_key = f"commenter_{i}"
        role_info = voice_cfg.get("roles", {}).get(role_key, {})
        c_rate = getattr(comment, "rate_offset", "") or role_info.get("rate") or "+0%"
        c_pitch = getattr(comment, "pitch_offset", "") or role_info.get("pitch") or "+0Hz"
        emotion = getattr(comment, "emotion", "funny")
        c_style = role_info.get("style", "")

        # Nếu role không có style riêng, áp dụng emotion → style mapping
        if not c_style and emotion in emotion_map:
            emo_cfg = emotion_map[emotion]
            c_style = emo_cfg.get("style", "")

        if progress_cb:
            progress_cb(f"Diễn xuất comment {i}/{total_cmt} ({emotion.upper()}) [{c_voice}] style={c_style or 'default'}...", pct)

        print(f"  🎙️ Đang tạo audio comment {i} ({c_voice}, emotion={emotion}, rate={c_rate}, pitch={c_pitch}, style={c_style or 'none'})...")
        cmt_path = os.path.join(out_dir, f"comment_{i}.mp3")
        text_to_speech(comment.content, cmt_path, voice=c_voice, rate=c_rate, pitch=c_pitch, style=c_style)

        # Tạo không gian âm thanh nổi 3D (Spatial Stereo Panning)
        layout_cfg = cfg.get("visual_layout", {})
        if layout_cfg.get("enable_spatial_audio_panning", True):
            pan_dir = "left" if i % 2 == 1 else "right"
            apply_spatial_panning(cmt_path, direction=pan_dir)

        c_dur = get_mp3_duration_ms(cmt_path)
        result["comments"].append({
            "path": cmt_path,
            "duration_ms": c_dur,
            "voice": c_voice,
            "emotion": emotion,
            "rate": c_rate,
            "pitch": c_pitch,
            "style": c_style,
            "sfx": getattr(comment, "sfx", "none")
        })
        print(f"     → {c_dur/1000:.2f}s")
        time.sleep(0.3)

    # 3. Diễn xuất câu chốt Outro Call-To-Action (nếu có hoặc mặc định)
    layout_cfg = cfg.get("visual_layout", {})
    if layout_cfg.get("enable_outro_cta", True):
        cta_q = getattr(post, "cta_question", "") or "Còn bạn thì sao? Hãy thả tim và để lại bình luận câu chuyện của bạn nhé!"
        for emo in ["👇", "❤️", "😱", "😂", "💀", "🔥", "✨"]:
            cta_q = cta_q.replace(emo, "").strip()
        outro_voice = voice_cfg.get("default_commenter_female_voice", "nu_cakhia")
        outro_path = os.path.join(out_dir, "outro_cta.mp3")
        try:
            if progress_cb:
                progress_cb(f"Tạo giọng đọc Outro Call-To-Action [{outro_voice}]...", 92)
            print(f"  🎙️ Đang tạo audio Outro CTA ({outro_voice})...")
            text_to_speech(cta_q, outro_path, voice=outro_voice, rate="+0%")
            o_dur = get_mp3_duration_ms(outro_path)
            if o_dur > 500:
                result["outro"] = {
                    "path": outro_path,
                    "duration_ms": o_dur,
                    "voice": outro_voice,
                    "text": cta_q
                }
                print(f"     → {o_dur/1000:.2f}s")
        except Exception as e:
            print(f"  ⚠️ Outro TTS fallback: {e}")

    return result


if __name__ == "__main__":
    test_out = os.path.join(OUT_DIR, "test_director.mp3")
    text_to_speech("Xin chào, đây là hệ thống đạo diễn video AI với nhịp điệu cảm xúc tự nhiên!", test_out)
    print(f"✅ Test thành công: {test_out}")
