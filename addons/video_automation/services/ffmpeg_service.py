import json
import logging
import os
import subprocess
import tempfile
import numpy as np

_logger = logging.getLogger(__name__)


# Fallback candidate font paths across macOS and Linux
COMMON_FONT_PATHS = [
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/Library/Fonts/Arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
]


def get_system_font():
    """Return the first available standard font on the system."""
    for path in COMMON_FONT_PATHS:
        if os.path.exists(path):
            return path
    return None


def probe_media(path):
    """Return basic media metadata via ffprobe."""
    cmd = [
        "ffprobe",
        "-v",
        "quiet",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    data = json.loads(result.stdout)
    video_stream = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), {})
    fmt = data.get("format", {})
    fps = 0.0
    avg = video_stream.get("avg_frame_rate") or "0/1"
    if "/" in avg:
        num, den = avg.split("/", 1)
        den = float(den) or 1.0
        fps = float(num) / den
    return {
        "duration": float(fmt.get("duration") or video_stream.get("duration") or 0),
        "width": int(video_stream.get("width") or 0),
        "height": int(video_stream.get("height") or 0),
        "fps": fps,
        "bitrate": int(fmt.get("bit_rate") or 0),
        "file_size": int(fmt.get("size") or 0),
    }


def extract_audio(video_path, output_path):
    """Extract audio track from video to mp3/aac file."""
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        video_path,
        "-vn",
        "-acodec",
        "libmp3lame",
        "-q:a",
        "2",
        output_path,
    ]
    _logger.info("Extracting audio: %s", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        _logger.error("FFmpeg extract failed: %s", result.stderr)
        raise RuntimeError(result.stderr[-2000:] if result.stderr else "FFmpeg extract failed")
    return output_path


def detect_beats(audio_path):
    """
    Phân tích nhịp âm thanh bằng thuật toán Onset RMS Energy Detection kết hợp Adaptive Threshold.
    Không bao giờ fail: kích hoạt Fallback Adaptive Interval (600ms) nếu beats < 3.
    Returns:
        (beat_timestamps: list[float], bpm: float, beat_status: str)
    """
    sr = 22050
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        audio_path,
        "-f",
        "s16le",
        "-acodec",
        "pcm_s16le",
        "-ac",
        "1",
        "-ar",
        str(sr),
        "-",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, check=True)
        raw_pcm = proc.stdout
    except Exception as exc:
        _logger.warning("FFmpeg decode for beat detection failed: %s", exc)
        raw_pcm = b""

    meta = probe_media(audio_path)
    duration = meta.get("duration") or 15.0

    if not raw_pcm or len(raw_pcm) < sr * 2:
        # Fallback interval
        return _fallback_beats(duration)

    samples = np.frombuffer(raw_pcm, dtype=np.int16).astype(np.float32) / 32768.0
    total_samples = len(samples)

    window_size = 1024
    hop_size = 512
    num_frames = (total_samples - window_size) // hop_size + 1

    if num_frames < 10:
        return _fallback_beats(duration)

    # Compute RMS Energy for each frame
    frames = np.lib.stride_tricks.sliding_window_view(samples[:num_frames * hop_size + window_size - hop_size], window_size)[::hop_size]
    energy = np.sqrt(np.mean(frames ** 2, axis=1))

    # Local adaptive threshold (window of ~43 frames ≈ 1 second)
    local_win = 43
    pad_width = local_win // 2
    padded_energy = np.pad(energy, pad_width, mode="edge")
    sliding_energy = np.lib.stride_tricks.sliding_window_view(padded_energy, local_win)

    local_mean = np.mean(sliding_energy, axis=1)
    local_std = np.std(sliding_energy, axis=1)
    threshold = local_mean + 1.5 * local_std

    # Peak detection with min interval 0.20s
    min_frame_gap = int(0.20 * sr / hop_size)
    detected_beats = []
    last_beat_frame = -min_frame_gap

    for i in range(1, len(energy) - 1):
        if (
            energy[i] > threshold[i]
            and energy[i] > energy[i - 1]
            and energy[i] > energy[i + 1]
            and (i - last_beat_frame) >= min_frame_gap
        ):
            timestamp = round(float(i * hop_size / sr), 2)
            if timestamp <= duration:
                detected_beats.append(timestamp)
                last_beat_frame = i

    if len(detected_beats) < 3:
        return _fallback_beats(duration)

    # Calculate BPM
    diffs = np.diff(detected_beats)
    median_diff = float(np.median(diffs)) if len(diffs) > 0 else 0.5
    bpm = round(60.0 / median_diff, 1) if median_diff > 0 else 120.0
    if bpm > 240:
        bpm = round(bpm / 2, 1)
    elif bpm < 60:
        bpm = round(bpm * 2, 1)

    return detected_beats, bpm, "detected"


def _fallback_beats(duration, interval=0.60):
    """Tự động sinh beat nhịp cố định cách nhau 0.6s khi audio không có beat rõ ràng."""
    beats = []
    t = interval
    while t < duration:
        beats.append(round(t, 2))
        t += interval
    bpm = round(60.0 / interval, 1)
    return beats, bpm, "fallback"


def _create_text_banner_png(text, font_size=58, font_path=None, max_width=1000):
    """
    Tạo ảnh PNG trong suốt chứa chữ kèm viền đen bằng Pillow.
    Đảm bảo tương thích 100% trên mọi bản build FFmpeg (kể cả không có libfreetype / drawtext).
    """
    from PIL import Image, ImageDraw, ImageFont

    resolved_font_path = font_path or get_system_font()
    font = None
    if resolved_font_path and os.path.exists(resolved_font_path):
        try:
            font = ImageFont.truetype(resolved_font_path, font_size)
        except Exception:
            font = None
    if font is None:
        try:
            font = ImageFont.load_default()
        except Exception:
            pass

    # Đo kích thước chữ
    dummy_img = Image.new("RGBA", (1, 1), (0, 0, 0, 0))
    dummy_draw = ImageDraw.Draw(dummy_img)
    stroke_w = max(2, font_size // 15)

    if hasattr(dummy_draw, "textbbox"):
        bbox = dummy_draw.textbbox((0, 0), text, font=font, stroke_width=stroke_w)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
    else:
        tw, th = dummy_draw.textsize(text, font=font)

    # Thêm padding
    img_w = min(max(tw + 40, 400), max_width)
    img_h = max(th + 30, 80)

    img = Image.new("RGBA", (img_w, img_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Canh giữa
    tx = (img_w - tw) // 2
    ty = (img_h - th) // 2

    # Vẽ viền và chữ
    if hasattr(draw, "text"):
        try:
            draw.text(
                (tx, ty),
                text,
                font=font,
                fill=(255, 255, 255, 255),
                stroke_width=stroke_w,
                stroke_fill=(0, 0, 0, 255),
            )
        except TypeError:
            # Fallback for older PIL versions without stroke_width
            for dx in range(-stroke_w, stroke_w + 1):
                for dy in range(-stroke_w, stroke_w + 1):
                    if dx != 0 or dy != 0:
                        draw.text((tx + dx, ty + dy), text, font=font, fill=(0, 0, 0, 255))
            draw.text((tx, ty), text, font=font, fill=(255, 255, 255, 255))

    return img


def generate_affiliate_video(
    image_path,
    audio_path,
    output_path,
    beat_data=None,
    effect_preset="normal",
    hook_text=None,
    cta_text=None,
    font_path=None,
    audio_duration=None,
):
    """
    Render video TikTok Affiliate 9:16 (1080x1920, 30 FPS) trong duy nhất 1-Pass Filter Complex.
    1. Background: Scale cover 1080x1920 + BoxBlur (r=25:p=2).
    2. Foreground: Scale contain 1080x1920 + Center overlay (100% không crop/zoom/stretch).
    3. Beat Pulse: Dynamic Brightness/Contrast timeline.
    4. White Flash: Flash overlay tại các beat timestamps.
    5. Hook: Top safe area text (0s -> 2.5s) kèm viền đen.
    6. CTA: Bottom safe area text (cuối video) kèm viền đen.
    7. Mux Audio AAC + Video H.264 (30 FPS, yuv420p, CRF 22).
    """
    if audio_duration is None:
        meta = probe_media(audio_path)
        audio_duration = float(meta.get("duration") or 15.0)

    if beat_data is None:
        beats, _, _ = detect_beats(audio_path)
    else:
        beats = beat_data

    # Preset settings
    preset_configs = {
        "soft": {"bright": 0.03, "contrast": 1.02, "flash": 0.05, "dur": 0.08},
        "normal": {"bright": 0.07, "contrast": 1.05, "flash": 0.10, "dur": 0.10},
        "strong": {"bright": 0.12, "contrast": 1.08, "flash": 0.15, "dur": 0.13},
    }
    cfg = preset_configs.get(effect_preset, preset_configs["normal"])

    # Build beat timeline enable expression
    pulse_dur = cfg["dur"]
    enable_terms = []
    for b in beats:
        if b < audio_duration:
            enable_terms.append(f"between(t,{b:.2f},{(b + pulse_dur):.2f})")

    enable_expr = "+".join(enable_terms) if enable_terms else None

    # Base filtergraph steps
    filter_steps = [
        "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=25:2[bg]",
        "[0:v]scale=1080:1920:force_original_aspect_ratio=decrease[fg]",
        "[bg][fg]overlay=(W-w)/2:(H-h)/2[base]",
    ]

    current_v = "[base]"

    # Beat pulse: eq brightness & contrast
    if enable_expr:
        pulse_v = "[pulsed]"
        filter_steps.append(
            f"{current_v}eq=brightness={cfg['bright']}:contrast={cfg['contrast']}:enable='{enable_expr}'{pulse_v}"
        )
        current_v = pulse_v

        # White Flash overlay
        if cfg["flash"] > 0:
            flash_v = "[flashed]"
            filter_steps.append(
                f"{current_v}drawbox=x=0:y=0:w=iw:h=ih:color=white@{cfg['flash']}:t=fill:enable='{enable_expr}'{flash_v}"
            )
            current_v = flash_v

    # Prepare overlay inputs for Hook and CTA text images
    input_args = [
        "-loop", "1", "-i", image_path,
        "-i", audio_path,
    ]
    extra_input_idx = 2
    temp_text_files = []

    try:
        # Hook Text Overlay (Top TikTok Safe Area, 0s -> 2.5s)
        if hook_text and hook_text.strip():
            hook_img = _create_text_banner_png(hook_text.strip(), font_size=58, font_path=font_path)
            hook_tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
            hook_img.save(hook_tmp.name, format="PNG")
            hook_tmp.close()
            temp_text_files.append(hook_tmp.name)

            input_args.extend(["-loop", "1", "-i", hook_tmp.name])
            hook_v = "[hooked]"
            filter_steps.append(
                f"{current_v}[{extra_input_idx}:v]overlay=(W-w)/2:260:enable='between(t,0,2.5)'{hook_v}"
            )
            current_v = hook_v
            extra_input_idx += 1

        # CTA Text Overlay (Bottom TikTok Safe Area, duration-4.0s -> duration)
        if cta_text and cta_text.strip():
            cta_img = _create_text_banner_png(cta_text.strip(), font_size=52, font_path=font_path)
            cta_tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
            cta_img.save(cta_tmp.name, format="PNG")
            cta_tmp.close()
            temp_text_files.append(cta_tmp.name)

            input_args.extend(["-loop", "1", "-i", cta_tmp.name])
            cta_start = max(0.0, audio_duration - 4.0)
            cta_v = "[ctaed]"
            filter_steps.append(
                f"{current_v}[{extra_input_idx}:v]overlay=(W-w)/2:1580:enable='between(t,{cta_start:.2f},{audio_duration:.2f})'{cta_v}"
            )
            current_v = cta_v
            extra_input_idx += 1

        # Final pixel format
        filter_steps.append(f"{current_v}format=yuv420p[vout]")
        filter_complex = ";".join(filter_steps)

        cmd = [
            "ffmpeg",
            "-y",
            *input_args,
            "-filter_complex",
            filter_complex,
            "-map",
            "[vout]",
            "-map",
            "1:a:0",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "22",
            "-r",
            "30",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-ar",
            "44100",
            "-t",
            f"{audio_duration:.2f}",
            output_path,
        ]

        _logger.info("Running FFmpeg Affiliate Gen: %s", " ".join(cmd))
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            _logger.error("FFmpeg Affiliate Gen failed: %s", result.stderr)
            raise RuntimeError(result.stderr[-2000:] if result.stderr else "FFmpeg Affiliate Gen failed")

        return output_path

    finally:
        for tmp_f in temp_text_files:
            if os.path.exists(tmp_f):
                try:
                    os.remove(tmp_f)
                except Exception:
                    pass



def generate_video(video_path, audio_path, output_path, logo_path=None):
    """
    Merge audio onto video and optionally overlay logo.
    Audio is looped/trimmed to video length.
    """
    if logo_path:
        filter_complex = (
            "[1:a]aloop=loop=-1:size=2e+09[a];"
            "[a][0:a]amix=inputs=2:duration=first:dropout_transition=0[aout];"
            "[0:v][2:v]overlay=W-w-20:20[vout]"
        )
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            video_path,
            "-i",
            audio_path,
            "-i",
            logo_path,
            "-filter_complex",
            filter_complex,
            "-map",
            "[vout]",
            "-map",
            "[aout]",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-c:a",
            "aac",
            "-shortest",
            output_path,
        ]
    else:
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            video_path,
            "-stream_loop",
            "-1",
            "-i",
            audio_path,
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-shortest",
            output_path,
        ]
    _logger.info("Running FFmpeg: %s", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        _logger.error("FFmpeg generate_video failed: %s", result.stderr)
        raise RuntimeError(result.stderr[-2000:] if result.stderr else "FFmpeg generate_video failed")
    return output_path

