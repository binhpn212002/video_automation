import json
import logging
import subprocess

_logger = logging.getLogger(__name__)


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
        "duration": float(fmt.get("duration") or 0),
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
    subprocess.run(cmd, capture_output=True, text=True, check=True)
    return output_path
