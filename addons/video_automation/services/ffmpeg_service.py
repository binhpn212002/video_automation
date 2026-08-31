import json
import logging
import math
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
    has_audio = any(s.get("codec_type") == "audio" for s in data.get("streams", []))
    return {
        "duration": float(fmt.get("duration") or video_stream.get("duration") or 0),
        "width": int(video_stream.get("width") or 0),
        "height": int(video_stream.get("height") or 0),
        "fps": fps,
        "bitrate": int(fmt.get("bit_rate") or 0),
        "file_size": int(fmt.get("size") or 0),
        "has_audio": has_audio,
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
    Không bao giờ fail: kích hoạt Fallback Adaptive Interval (600ms) nếu beats < 3 hoặc có lỗi.
    Returns:
        (beat_timestamps: list[float], bpm: float, beat_status: str)
    """
    duration = 15.0
    try:
        meta = probe_media(audio_path)
        duration = float(meta.get("duration") or 15.0)
    except Exception:
        pass

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
        return _fallback_beats(duration)

    if not raw_pcm or len(raw_pcm) < sr * 2:
        return _fallback_beats(duration)

    try:
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
        local_win = min(43, len(energy) if len(energy) % 2 == 1 else len(energy) - 1)
        if local_win < 3:
            return _fallback_beats(duration)

        pad_width = local_win // 2
        padded_energy = np.pad(energy, pad_width, mode="edge")
        sliding_energy = np.lib.stride_tricks.sliding_window_view(padded_energy, local_win)

        local_mean = np.mean(sliding_energy, axis=1)
        local_std = np.std(sliding_energy, axis=1)
        threshold = local_mean + 1.5 * local_std

        # Peak detection with min interval 0.20s
        min_frame_gap = max(1, int(0.20 * sr / hop_size))
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
    except Exception as exc:
        _logger.warning("Beat detection calculation fallback: %s", exc)
        return _fallback_beats(duration)


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
    motion_effect="zoom_bounce",
    hook_text=None,
    cta_text=None,
    font_path=None,
    audio_duration=None,
    max_duration=25.0,
):
    """
    Render video TikTok Affiliate 9:16 (1080x1920, 30 FPS) trong duy nhất 1-Pass Filter Complex.
    1. Background: Scale cover 1080x1920 + BoxBlur (r=25:p=2).
    2. Foreground: Scale contain + Motion (Ken Burns slow zoom & Beat Bounce dynamic scale).
    3. Beat Pulse: Dynamic Brightness/Contrast timeline.
    4. White Flash: Flash overlay tại các beat timestamps.
    5. Hook: Top safe area text (0s -> 2.5s) kèm viền đen.
    6. CTA: Bottom safe area text (cuối video) kèm viền đen.
    7. Mux Audio AAC (Fade Out cuối) + Video H.264 (30 FPS, yuv420p, CRF 22).
    Thời lượng video mặc định là 25s (nếu nhạc dài hơn 25s sẽ tự động cắt ngắn lại).
    """
    meta = probe_media(audio_path)
    real_audio_dur = float(meta.get("duration") or 25.0)

    if audio_duration is not None and float(audio_duration) > 0:
        audio_duration = float(audio_duration)
    elif max_duration is not None and float(max_duration) > 0:
        audio_duration = min(real_audio_dur, float(max_duration))
    else:
        audio_duration = min(real_audio_dur, 25.0)

    if beat_data is None:
        beats, _, _ = detect_beats(audio_path)
    else:
        beats = beat_data

    # Preset settings (pulse brightness, contrast, white flash, bounce amplitude)
    preset_configs = {
        "soft": {"bright": 0.03, "contrast": 1.02, "flash": 0.05, "dur": 0.08, "bounce": 0.03},
        "normal": {"bright": 0.07, "contrast": 1.05, "flash": 0.10, "dur": 0.10, "bounce": 0.06},
        "strong": {"bright": 0.12, "contrast": 1.08, "flash": 0.15, "dur": 0.13, "bounce": 0.09},
    }
    cfg = preset_configs.get(effect_preset, preset_configs["normal"])

    # Build beat timeline enable expression for brightness/contrast & flash
    pulse_dur = cfg["dur"]
    enable_terms = []
    for b in beats:
        if b < audio_duration:
            enable_terms.append(f"between(t,{b:.2f},{(b + pulse_dur):.2f})")

    enable_expr = "+".join(enable_terms) if enable_terms else None

    # Base filtergraph steps
    filter_steps = [
        "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=25:2[bg]",
    ]

    # Handle motion effect: Ken Burns + Beat Bounce
    motion = motion_effect or "zoom_bounce"
    if motion == "none":
        filter_steps.extend([
            "[0:v]scale=1080:1920:force_original_aspect_ratio=decrease[fg]",
            "[bg][fg]overlay=(W-w)/2:(H-h)/2[base]",
        ])
    else:
        # Determine Ken Burns Zoom curve
        if motion in ("zoom_bounce", "zoom_in"):
            ken_expr = f"1.0+0.07*(t/{audio_duration:.2f})"
        elif motion == "zoom_out":
            ken_expr = f"1.07-0.07*(t/{audio_duration:.2f})"
        else:
            ken_expr = "1.0"

        # Determine Beat Bounce impulse curve
        bounce_terms = []
        if motion in ("zoom_bounce", "bounce_only") and beats:
            bounce_amp = cfg["bounce"]
            for b in beats:
                if b < audio_duration:
                    bounce_terms.append(
                        f"if(between(t,{b:.2f},{(b + pulse_dur):.2f}),{bounce_amp:.2f}*(1-(t-{b:.2f})/{pulse_dur:.2f}),0)"
                    )
        bounce_expr = "+".join(bounce_terms) if bounce_terms else "0"

        # Total scale multiplier evaluated per frame
        total_scale = f"{ken_expr}+({bounce_expr})"
        scale_w = f"trunc(iw*({total_scale})/2)*2"
        scale_h = f"trunc(ih*({total_scale})/2)*2"

        filter_steps.extend([
            # Base scaled foreground leaving ~6% margin for zoom & bounce headroom
            "[0:v]scale=980:1740:force_original_aspect_ratio=decrease[fg0]",
            f"[fg0]scale=w='{scale_w}':h='{scale_h}':eval=frame[fg]",
            "[bg][fg]overlay=(W-w)/2:(H-h)/2[base]",
        ])

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

        # Audio fade out for smooth termination (0.8s)
        audio_fade_st = max(0.0, audio_duration - 0.8)

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
            "-af",
            f"afade=t=out:st={audio_fade_st:.2f}:d=0.8",
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


def replace_video_audio(
    video_path,
    audio_path,
    output_path,
    keep_original_audio=False,
    original_vol_ratio=1.0,
    bg_music_vol_ratio=0.3,
    audio_fade_out=0.8,
):
    """
    Thay thế hoặc trộn âm thanh cho video.
    - keep_original_audio=False: Thay thế 100% âm thanh cũ bằng audio mới, lặp audio nếu ngắn hơn video, fade-out ở cuối.
    - keep_original_audio=True: Trộn âm thanh gốc và nhạc nền mới (amix) theo tỉ lệ volume tương ứng.
    Stream video được sao chép (-c:v copy) giúp xử lý cực nhanh không làm giảm chất lượng hình ảnh.
    """
    meta = probe_media(video_path)
    video_duration = float(meta.get("duration") or 0.0)
    has_audio = bool(meta.get("has_audio"))

    fade_st = max(0.0, video_duration - (audio_fade_out or 0.8)) if video_duration > 0 else 0.0
    afade_str = f"afade=t=out:st={fade_st:.2f}:d={audio_fade_out:.2f}" if (audio_fade_out and fade_st > 0) else None

    if keep_original_audio and has_audio:
        orig_vol = max(0.0, float(original_vol_ratio or 1.0))
        bg_vol = max(0.0, float(bg_music_vol_ratio or 0.3))
        bg_af_parts = [f"volume={bg_vol:.2f}"]
        if afade_str:
            bg_af_parts.append(afade_str)
        bg_af = ",".join(bg_af_parts)

        filter_complex = (
            f"[0:a]volume={orig_vol:.2f}[a0];"
            f"[1:a]{bg_af}[a1];"
            f"[a0][a1]amix=inputs=2:duration=first:dropout_transition=0[aout]"
        )
        cmd = [
            "ffmpeg",
            "-y",
            "-i", video_path,
            "-stream_loop", "-1",
            "-i", audio_path,
            "-filter_complex", filter_complex,
            "-map", "0:v:0",
            "-map", "[aout]",
            "-c:v", "copy",
            "-c:a", "aac",
            "-b:a", "192k",
            "-ar", "44100",
        ]
        if video_duration > 0:
            cmd.extend(["-t", f"{video_duration:.2f}"])
        else:
            cmd.append("-shortest")
        cmd.append(output_path)
    else:
        audio_filters = []
        if afade_str:
            audio_filters.append(afade_str)

        cmd = [
            "ffmpeg",
            "-y",
            "-i", video_path,
            "-stream_loop", "-1",
            "-i", audio_path,
            "-map", "0:v:0",
            "-map", "1:a:0",
            "-c:v", "copy",
            "-c:a", "aac",
            "-b:a", "192k",
            "-ar", "44100",
        ]
        if audio_filters:
            cmd.extend(["-af", ",".join(audio_filters)])
        if video_duration > 0:
            cmd.extend(["-t", f"{video_duration:.2f}"])
        else:
            cmd.append("-shortest")
        cmd.append(output_path)

    _logger.info("Running FFmpeg replace_video_audio: %s", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        _logger.error("FFmpeg replace_video_audio failed: %s", result.stderr)
        raise RuntimeError(result.stderr[-2000:] if result.stderr else "FFmpeg replace_video_audio failed")
    return output_path


def _prepare_character_image(image_path, output_path, layout="spotify_card", corner_radius=44, theme_color="cyan_neon"):
    """
    Chuẩn hóa và tạo Card / Vinyl / Avatar cho ảnh nhân vật bằng Pillow:
    - Bo góc anti-aliasing siêu mịn (Super-sampled Lanczos).
    - Tạo viền sáng mỏng thanh lịch (1.5px subtle border).
    - Vầng sáng Ambient Glow + Đổ bóng mờ 3D (Soft Drop Shadow) tạo chiều sâu điện ảnh.
    - Đĩa than cổ điển với rãnh vinyl tinh xảo.
    """
    from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps, WebPImagePlugin
    Image.init()

    glow_colors = {
        "cyan_neon": (0, 210, 255, 120),
        "pink_purple": (255, 60, 180, 120),
        "golden_warm": (255, 200, 50, 120),
        "white_minimal": (255, 255, 255, 100),
    }
    glow_col = glow_colors.get(theme_color, glow_colors["cyan_neon"])

    with Image.open(image_path) as raw_img:
        raw_img = raw_img.convert("RGBA")
        orig_w, orig_h = raw_img.size

        # 1. Bố cục Đĩa than Vintage (Vinyl Record)
        if layout in ("spinning_vinyl", "vinyl_retro"):
            vinyl_size = 660
            vinyl = Image.new("RGBA", (vinyl_size, vinyl_size), (0, 0, 0, 0))
            v_draw = ImageDraw.Draw(vinyl)

            # Thân đĩa than màu đen than sang trọng
            v_draw.ellipse([(0, 0), (vinyl_size, vinyl_size)], fill=(18, 18, 22, 255), outline=(55, 55, 60, 255), width=2)

            # Các đường rãnh vinyl đồng tâm
            cx, cy = vinyl_size // 2, vinyl_size // 2
            for r in range(150, 318, 10):
                v_draw.ellipse([(cx - r, cy - r), (cx + r, cy + r)], outline=(32, 32, 38, 255), width=1)

            # Nhãn tròn nhân vật ở trung tâm (avatar 270x270)
            avatar_size = 270
            avatar = ImageOps.fit(raw_img, (avatar_size, avatar_size), Image.Resampling.LANCZOS)
            factor = 4
            a_mask = Image.new("L", (avatar_size * factor, avatar_size * factor), 0)
            a_draw = ImageDraw.Draw(a_mask)
            a_draw.ellipse([(0, 0), (avatar_size * factor, avatar_size * factor)], fill=255)
            a_mask = a_mask.resize((avatar_size, avatar_size), Image.Resampling.LANCZOS)
            avatar.putalpha(a_mask)

            # Viền nhãn đĩa
            v_draw.ellipse(
                [(cx - avatar_size // 2 - 2, cy - avatar_size // 2 - 2), (cx + avatar_size // 2 + 2, cy + avatar_size // 2 + 2)],
                outline=(255, 255, 255, 200),
                width=2,
            )
            vinyl.paste(avatar, (cx - avatar_size // 2, cy - avatar_size // 2), avatar)

            # Lỗ trục chính giữa (spindle hole)
            v_draw.ellipse([(cx - 8, cy - 8), (cx + 8, cy + 8)], fill=(0, 0, 0, 0), outline=(255, 255, 255, 120), width=1)

            vinyl.save(output_path, "PNG")
            return output_path

        # 2. Bố cục Avatar Tròn (Circular Avatar)
        elif layout == "circular_avatar":
            avatar_size = 620
            img = ImageOps.fit(raw_img, (avatar_size, avatar_size), Image.Resampling.LANCZOS)
            factor = 4
            mask = Image.new("L", (avatar_size * factor, avatar_size * factor), 0)
            draw = ImageDraw.Draw(mask)
            draw.ellipse([(0, 0), (avatar_size * factor, avatar_size * factor)], fill=255)
            mask = mask.resize((avatar_size, avatar_size), Image.Resampling.LANCZOS)
            img.putalpha(mask)

            pad = 60
            canvas = Image.new("RGBA", (avatar_size + pad * 2, avatar_size + pad * 2), (0, 0, 0, 0))
            # Ambient glow
            glow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
            g_draw = ImageDraw.Draw(glow)
            g_draw.ellipse([(pad - 5, pad - 5), (pad + avatar_size + 5, pad + avatar_size + 5)], fill=glow_col)
            glow = glow.filter(ImageFilter.GaussianBlur(radius=30))

            # Dark shadow
            shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
            s_draw = ImageDraw.Draw(shadow)
            s_draw.ellipse([(pad + 5, pad + 15), (pad + avatar_size + 5, pad + avatar_size + 15)], fill=(0, 0, 0, 160))
            shadow = shadow.filter(ImageFilter.GaussianBlur(radius=20))

            canvas.paste(glow, (0, 0), glow)
            canvas.paste(shadow, (0, 0), shadow)
            canvas.paste(img, (pad, pad), img)
            canvas.save(output_path, "PNG")
            return output_path

        # 3. Bố cục Card Sang Trọng (Spotify Card / Modern Card / Glass Card)
        else:
            aspect = orig_h / max(orig_w, 1)
            card_w = 760
            card_h = int(card_w * aspect)
            card_h = max(760, min(card_h, 960))

            img = ImageOps.fit(raw_img, (card_w, card_h), Image.Resampling.LANCZOS)
            w, h = card_w, card_h

            factor = 4
            mask = Image.new("L", (w * factor, h * factor), 0)
            draw_m = ImageDraw.Draw(mask)
            r = int(corner_radius * factor)
            draw_m.rounded_rectangle([(0, 0), (w * factor, h * factor)], radius=r, fill=255)
            mask = mask.resize((w, h), Image.Resampling.LANCZOS)

            border_mask = Image.new("RGBA", (w * factor, h * factor), (0, 0, 0, 0))
            b_draw = ImageDraw.Draw(border_mask)
            bw = int(2 * factor)
            b_draw.rounded_rectangle(
                [(bw // 2, bw // 2), (w * factor - bw // 2, h * factor - bw // 2)],
                radius=r,
                outline=(255, 255, 255, 180),
                width=bw,
            )
            border_img = border_mask.resize((w, h), Image.Resampling.LANCZOS)

            current_alpha = img.split()[-1]
            final_alpha = ImageChops.multiply(current_alpha, mask)
            img.putalpha(final_alpha)
            img.paste(border_img, (0, 0), border_img)

            # Tạo Vầng sáng Ambient Glow + Đổ bóng 3D
            pad = 60
            canvas = Image.new("RGBA", (w + pad * 2, h + pad * 2), (0, 0, 0, 0))

            glow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
            g_draw = ImageDraw.Draw(glow)
            g_draw.rounded_rectangle([(pad - 6, pad - 6), (pad + w + 6, pad + h + 6)], radius=corner_radius + 6, fill=glow_col)
            glow = glow.filter(ImageFilter.GaussianBlur(radius=32))

            shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
            s_draw = ImageDraw.Draw(shadow)
            s_draw.rounded_rectangle([(pad + 5, pad + 16), (pad + w + 5, pad + h + 16)], radius=corner_radius, fill=(0, 0, 0, 160))
            shadow = shadow.filter(ImageFilter.GaussianBlur(radius=22))

            canvas.paste(glow, (0, 0), glow)
            canvas.paste(shadow, (0, 0), shadow)
            canvas.paste(img, (pad, pad), img)
            canvas.save(output_path, "PNG")
            return output_path


def _extract_pcm_samples(audio_path, target_sr=44100):
    """Trích xuất mảng PCM float32 từ audio file để phân tích FFT."""
    cmd = [
        "ffmpeg",
        "-y",
        "-i", audio_path,
        "-f", "s16le",
        "-acodec", "pcm_s16le",
        "-ac", "1",
        "-ar", str(target_sr),
        "-",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, check=True)
        samples = np.frombuffer(proc.stdout, dtype=np.int16).astype(np.float32) / 32768.0
        return samples, target_sr
    except Exception as exc:
        _logger.warning("Failed to extract PCM for visualizer: %s", exc)
        return np.zeros(target_sr * 10, dtype=np.float32), target_sr


def _compute_fft_timeline(samples, sr=44100, fps=30, num_bars=38, min_freq=40, max_freq=12000):
    """Tính toán chuỗi độ cao dải tần số (0.0 -> 1.0) theo từng frame video (30 FPS) kèm smooth damping."""
    total_frames = max(1, int(len(samples) / sr * fps))
    freq_bins = np.logspace(np.log10(min_freq), np.log10(max_freq), num_bars + 1)
    window_size = 2048
    hop_size = int(sr / fps)
    hanning_win = np.hanning(window_size)
    fft_freqs = np.fft.rfftfreq(window_size, 1.0 / sr)

    bin_masks = []
    for i in range(num_bars):
        f_low = freq_bins[i]
        f_high = freq_bins[i + 1]
        mask = (fft_freqs >= f_low) & (fft_freqs < f_high)
        if not np.any(mask):
            idx = np.argmin(np.abs(fft_freqs - (f_low + f_high) / 2))
            mask = np.zeros(len(fft_freqs), dtype=bool)
            mask[idx] = True
        bin_masks.append(mask)

    timeline = np.zeros((total_frames, num_bars), dtype=np.float32)
    smoothed = np.zeros(num_bars, dtype=np.float32)

    for frame_idx in range(total_frames):
        center_sample = frame_idx * hop_size
        start = center_sample - window_size // 2
        end = start + window_size

        if start < 0:
            chunk = samples[:max(0, end)]
            chunk = np.pad(chunk, (-start, 0), mode="constant")
        elif end > len(samples):
            chunk = samples[start:]
            chunk = np.pad(chunk, (0, end - len(samples)), mode="constant")
        else:
            chunk = samples[start:end]

        if len(chunk) < window_size:
            chunk = np.pad(chunk, (0, window_size - len(chunk)), mode="constant")

        windowed = chunk * hanning_win
        magnitude = np.abs(np.fft.rfft(windowed))

        frame_bars = np.zeros(num_bars, dtype=np.float32)
        for i, mask in enumerate(bin_masks):
            val = np.mean(magnitude[mask]) if np.any(mask) else 0.0
            boost = 1.0 + 1.6 * (i / num_bars)
            frame_bars[i] = val * boost

        frame_bars = np.log10(1.0 + frame_bars * 16.0)
        frame_bars = np.clip(frame_bars, 0.05, 1.0)

        # Smooth attack / decay
        for i in range(num_bars):
            if frame_bars[i] > smoothed[i]:
                smoothed[i] = smoothed[i] * 0.25 + frame_bars[i] * 0.75
            else:
                smoothed[i] = smoothed[i] * 0.80 + frame_bars[i] * 0.20

        # Center-out symmetry for aesthetic visualizer
        half = num_bars // 2
        left = smoothed[::2][:half][::-1]
        right = smoothed[1::2][:num_bars - half]
        sym = np.concatenate([left, right])
        timeline[frame_idx] = sym

    return timeline


def _generate_visualizer_overlay_video(
    audio_path,
    output_mov_path,
    duration=10.0,
    fps=30,
    style="spectrum_bars",
    theme="cyan_neon",
    layout="spotify_card",
):
    """
    Sinh video visualizer trong suốt (transparent RGBA) bằng Pillow + NumPy FFT:
    - spectrum_bars: Cột sóng bo tròn (pill bars) kèm dock kính mờ sang trọng.
    - sine_wave: Đường sóng lượn Neon (glowing multi-layer sine wave).
    - radial_circle: Sóng nhạc tỏa tròn 360 độ xung quanh đĩa than hoặc avatar.
    """
    from PIL import Image, ImageDraw

    colors_map = {
        "cyan_neon": [(0, 245, 255), (0, 135, 255)],
        "pink_purple": [(255, 70, 190), (150, 40, 255)],
        "golden_warm": [(255, 220, 40), (255, 100, 20)],
        "white_minimal": [(255, 255, 255), (180, 210, 255)],
    }
    c_start, c_end = colors_map.get(theme, colors_map["cyan_neon"])

    samples, sr = _extract_pcm_samples(audio_path)
    total_frames = max(1, int(duration * fps))

    if style == "radial_circle":
        width, height = 960, 960
        num_bars = 60
        timeline = _compute_fft_timeline(samples, sr=sr, fps=fps, num_bars=30)
        inner_r = 345 if layout in ("spinning_vinyl", "vinyl_retro") else 320
    else:
        width, height = 860, 180
        num_bars = 38
        timeline = _compute_fft_timeline(samples, sr=sr, fps=fps, num_bars=num_bars)

    cmd = [
        "ffmpeg",
        "-y",
        "-f", "rawvideo",
        "-vcodec", "rawvideo",
        "-s", f"{width}x{height}",
        "-pix_fmt", "rgba",
        "-r", str(fps),
        "-i", "-",
        "-c:v", "qtrle",
        "-threads", "0",
        output_mov_path,
    ]

    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    try:
        for f in range(total_frames):
            img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
            draw = ImageDraw.Draw(img)
            t_idx = min(f, len(timeline) - 1)
            bars = timeline[t_idx]

            if style == "radial_circle":
                cx, cy = width // 2, height // 2
                max_bar_len = (width // 2) - inner_r - 20
                # Mirror 30 bars to 60 bars around the circle for bilateral symmetry
                circle_bars = np.concatenate([bars, bars[::-1]])
                for i, h_val in enumerate(circle_bars):
                    angle_deg = (i / num_bars) * 360.0 - 90.0
                    rad = math.radians(angle_deg)
                    bar_len = max(6, int(h_val * max_bar_len))
                    r1 = inner_r + 6
                    r2 = r1 + bar_len
                    x1 = cx + math.cos(rad) * r1
                    y1 = cy + math.sin(rad) * r1
                    x2 = cx + math.cos(rad) * r2
                    y2 = cy + math.sin(rad) * r2

                    ratio = (math.sin(rad) + 1.0) / 2.0
                    r = int(c_start[0] * (1 - ratio) + c_end[0] * ratio)
                    g = int(c_start[1] * (1 - ratio) + c_end[1] * ratio)
                    b = int(c_start[2] * (1 - ratio) + c_end[2] * ratio)
                    draw.line([(x1, y1), (x2, y2)], fill=(r, g, b, 235), width=6)
                    draw.ellipse([(x2 - 3, y2 - 3), (x2 + 3, y2 + 3)], fill=(255, 255, 255, 240))

            elif style in ("sine_wave", "smooth_waves"):
                draw.rounded_rectangle(
                    [(15, 10), (width - 15, height - 10)],
                    radius=30,
                    fill=(12, 14, 24, 130),
                    outline=(255, 255, 255, 45),
                    width=2,
                )
                cy = height // 2
                pad = 45
                avail_w = width - pad * 2
                rms_amp = float(np.mean(bars))

                layers = [
                    {"freq": 3.0, "phase_speed": 0.15, "amp_mult": 1.0, "alpha": 255, "width": 4},
                    {"freq": 2.2, "phase_speed": -0.12, "amp_mult": 0.75, "alpha": 180, "width": 3},
                    {"freq": 4.5, "phase_speed": 0.20, "amp_mult": 0.50, "alpha": 120, "width": 2},
                ]
                for layer in layers:
                    pts = []
                    amp = max(8.0, rms_amp * 60.0 * layer["amp_mult"])
                    phase = f * layer["phase_speed"]
                    freq = layer["freq"]
                    for step in range(avail_w):
                        x = pad + step
                        prog = step / avail_w
                        env = math.sin(prog * math.pi) ** 1.5
                        y = cy + math.sin(prog * freq * 2 * math.pi + phase) * amp * env
                        pts.append((x, y))
                    col = (c_start[0], c_start[1], c_start[2], layer["alpha"])
                    for j in range(len(pts) - 1):
                        draw.line([pts[j], pts[j + 1]], fill=col, width=layer["width"])

            else:
                # spectrum_bars
                draw.rounded_rectangle(
                    [(15, 10), (width - 15, height - 10)],
                    radius=30,
                    fill=(12, 14, 24, 130),
                    outline=(255, 255, 255, 45),
                    width=2,
                )
                pad = 45
                avail_w = width - pad * 2
                bar_w = max(4, int(avail_w / num_bars * 0.58))
                gap = (avail_w - (num_bars * bar_w)) / max(1, num_bars - 1)
                cy = height // 2
                max_h = (height - 45) // 2

                for i, h_val in enumerate(bars):
                    x = pad + i * (bar_w + gap)
                    h = max(3, int(h_val * max_h))
                    ratio = i / max(1, num_bars - 1)
                    r = int(c_start[0] * (1 - ratio) + c_end[0] * ratio)
                    g = int(c_start[1] * (1 - ratio) + c_end[1] * ratio)
                    b = int(c_start[2] * (1 - ratio) + c_end[2] * ratio)

                    bar_top = cy - h
                    bar_bottom = cy + h
                    draw.rounded_rectangle(
                        [(x, bar_top), (x + bar_w, bar_bottom)],
                        radius=bar_w // 2,
                        fill=(r, g, b, 245),
                    )

            proc.stdin.write(img.tobytes())

        proc.stdin.close()
        proc.wait()
    except Exception as exc:
        _logger.warning("Error generating visualizer video: %s", exc)
        if proc and proc.stdin:
            try:
                proc.stdin.close()
            except Exception:
                pass
            proc.kill()
        raise

    return output_mov_path


def generate_music_video(
    bg_image_path,
    character_image_path,
    audio_path,
    output_path,
    layout="spotify_card",
    visualizer_style="spectrum_bars",
    visualizer_color="cyan_neon",
    particle_effect="dust_bokeh",
    music_preset="lofi_chill",
    effect_preset="normal",
    max_duration=0.0,
):
    """
    Sinh video ca nhạc 9:16 (1080x1920, 30 FPS) chuẩn Aesthetic TikTok / Lofi Shorts:
    - Background: Phông nền Cover nghệ thuật + Vignette chiều sâu
    - Character: Card bo góc 3D tỏa sáng Ambient Glow / Đĩa than xoay 360° chậm rãi
    - Audio Visualizer: Sóng nhạc FFT Spectrum / Waves realtime sang trọng
    - File Nhạc MP3: Fade-out êm dịu ở cuối clip
    """
    audio_meta = probe_media(audio_path)
    raw_duration = float(audio_meta.get("duration") or 0.0)
    if max_duration and float(max_duration) > 0:
        audio_duration = min(raw_duration, float(max_duration)) if raw_duration > 0 else float(max_duration)
    else:
        audio_duration = raw_duration if raw_duration > 0 else 30.0

    # Chuẩn bị ảnh nhân vật card 3D Ambient Glow bằng Pillow
    processed_char_tmp = tempfile.NamedTemporaryFile(suffix="_char.png", delete=False)
    processed_char_path = processed_char_tmp.name
    processed_char_tmp.close()

    vis_mov_path = None
    if visualizer_style != "none":
        vis_tmp = tempfile.NamedTemporaryFile(suffix="_vis.mov", delete=False)
        vis_mov_path = vis_tmp.name
        vis_tmp.close()

    try:
        _prepare_character_image(
            image_path=character_image_path,
            output_path=processed_char_path,
            layout=layout,
            corner_radius=44,
            theme_color=visualizer_color,
        )

        if visualizer_style != "none" and vis_mov_path:
            _generate_visualizer_overlay_video(
                audio_path=audio_path,
                output_mov_path=vis_mov_path,
                duration=audio_duration,
                fps=30,
                style=visualizer_style,
                theme=visualizer_color,
                layout=layout,
            )

        filter_steps = []

        # Layer 0: Background (Cover + Ambient Blur + Vignette / Preset Mood)
        if music_preset == "ballad_acoustic":
            filter_steps.append("[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=4:1,vignette=PI/3.5[bg]")
        elif music_preset == "edm_remix":
            filter_steps.append("[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=1:1,vignette=PI/4.5,eq=contrast=1.1:saturation=1.2[bg]")
        elif music_preset == "hiphop_trap":
            filter_steps.append("[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=2:1,vignette=PI/3,eq=contrast=1.15:saturation=1.1[bg]")
        else:
            # lofi_chill default
            filter_steps.append("[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=2:1,vignette=PI/4[bg]")

        # Inputs list
        inputs = [
            "-loop", "1", "-i", bg_image_path,
            "-loop", "1", "-i", processed_char_path,
            "-i", audio_path,
        ]

        vis_input_idx = None
        if visualizer_style != "none" and vis_mov_path and os.path.exists(vis_mov_path):
            vis_input_idx = len(inputs) // 2 + 1 if "-loop" in inputs else 3
            # Index of vis is 3
            inputs.extend(["-i", vis_mov_path])
            vis_idx = 3

        # Overlay layers
        if visualizer_style == "radial_circle" and vis_mov_path:
            if layout in ("spinning_vinyl", "vinyl_retro"):
                vis_y = 267
            elif layout == "circular_avatar":
                vis_y = 170
            else:
                vis_y = 200
            filter_steps.append(f"[bg][{vis_idx}:v]overlay=(W-w)/2:{vis_y}[bg_vis]")
            current_bg = "[bg_vis]"
        else:
            current_bg = "[bg]"

        # Layer 1: Character layout
        if layout in ("spinning_vinyl", "vinyl_retro"):
            filter_steps.extend([
                "[1:v]rotate=2*PI*t*0.12:c=none:ow='hypot(iw,ih)':oh=ow[vinyl_spin]",
                f"{current_bg}[vinyl_spin]overlay=(W-w)/2:280[stage0]",
            ])
        else:
            # spotify_card / circular_avatar / glass_card / center_cutout / floating_portrait
            filter_steps.append(f"{current_bg}[1:v]overlay=(W-w)/2:280[stage0]")

        current_v = "[stage0]"

        # Layer 2: Dock Audio Visualizer (spectrum_bars / sine_wave)
        if visualizer_style != "none" and visualizer_style != "radial_circle" and vis_mov_path:
            filter_steps.append(f"{current_v}[{vis_idx}:v]overlay=(W-w)/2:1340[stage1]")
            current_v = "[stage1]"

        filter_steps.append(f"{current_v}format=yuv420p[vout]")
        filter_complex = ";".join(filter_steps)

        audio_fade_st = max(0.0, audio_duration - 0.8)

        cmd = [
            "ffmpeg",
            "-y",
            *inputs,
            "-filter_complex", filter_complex,
            "-map", "[vout]",
            "-map", "2:a:0",
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-threads", "0",
            "-crf", "22",
            "-r", "30",
            "-af", f"afade=t=out:st={audio_fade_st:.2f}:d=0.8",
            "-c:a", "aac",
            "-b:a", "192k",
            "-ar", "44100",
            "-t", f"{audio_duration:.2f}",
            output_path,
        ]

        _logger.info("Running FFmpeg Music Video Gen: %s", " ".join(cmd))
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            _logger.error("FFmpeg Music Video Gen failed: %s", result.stderr)
            raise RuntimeError(result.stderr[-2000:] if result.stderr else "FFmpeg Music Video Gen failed")

        return output_path

    finally:
        if os.path.exists(processed_char_path):
            try:
                os.remove(processed_char_path)
            except Exception:
                pass
        if vis_mov_path and os.path.exists(vis_mov_path):
            try:
                os.remove(vis_mov_path)
            except Exception:
                pass







