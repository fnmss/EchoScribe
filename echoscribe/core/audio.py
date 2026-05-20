"""Audio I/O, format conversion, and timestamp helpers.

Pure helpers (`format_timestamp`, `parse_timestamp`, `format_duration`)
have no side effects. The ffmpeg-driven helpers (`convert_to_wav`,
`probe_duration`, `split_audio`) shell out and may raise RuntimeError
on failure.
"""
import os
import shutil
import subprocess
import tempfile


# Browser-playable container formats — used by web routes when deciding
# whether to copy a video into MEDIA_DIR for inline playback.
BROWSER_VIDEO_FORMATS = {"mp4", "webm", "ogg"}

# Long-audio chunking threshold (seconds). Audio longer than this is
# split before ASR to bound peak memory.
CHUNK_DURATION_SEC = 30 * 60  # 30 minutes


def check_deps():
    """Raise RuntimeError unless ffmpeg is on PATH."""
    if not shutil.which("ffmpeg"):
        raise RuntimeError("未找到 ffmpeg，请先安装 ffmpeg 并确保其在 PATH 中。")


def get_proxy():
    """Read proxy URL from env. Priority: YT_PROXY > HTTPS_PROXY > HTTP_PROXY."""
    return (
        os.environ.get("YT_PROXY")
        or os.environ.get("HTTPS_PROXY")
        or os.environ.get("HTTP_PROXY")
    )


def format_timestamp(ms):
    """Convert milliseconds to ``HH:MM:SS`` (sub-second truncated)."""
    seconds = int(ms / 1000)
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:02d}"


def parse_timestamp(ts):
    """Parse ``HH:MM:SS`` or ``MM:SS`` to milliseconds. Unrecognized → 0."""
    parts = ts.split(":")
    if len(parts) == 3:
        h, m, s = int(parts[0]), int(parts[1]), int(parts[2])
    elif len(parts) == 2:
        h = 0
        m, s = int(parts[0]), int(parts[1])
    else:
        return 0
    return (h * 3600 + m * 60 + s) * 1000


def format_duration(seconds):
    """Convert seconds to ``MM:SS`` (or ``HH:MM:SS`` if ≥1h). None → '未知'."""
    if seconds is None:
        return "未知"
    seconds = int(seconds)
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def convert_to_wav(input_path, output_path):
    """Run ffmpeg to produce 16 kHz mono PCM s16 WAV (the format FunASR wants)."""
    cmd = [
        "ffmpeg", "-y", "-i", input_path,
        "-ar", "16000", "-ac", "1", "-sample_fmt", "s16",
        output_path,
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True)
    except subprocess.CalledProcessError as e:
        err = e.stderr.decode(errors="replace") if e.stderr else str(e)
        raise RuntimeError(f"音频转换失败: {err}")


def probe_duration(path):
    """Return audio duration in seconds via ffprobe, or None on failure."""
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        return None
    try:
        return float(result.stdout.strip())
    except ValueError:
        return None


def split_audio(audio_path, chunk_sec=CHUNK_DURATION_SEC):
    """Slice ``audio_path`` into ``chunk_sec`` chunks; return [(chunk_path, offset_sec), ...].

    Returns a single-element list of (audio_path, 0) when the audio is
    short enough that no slicing is needed. Caller is responsible for
    cleaning up the temp chunk files.
    """
    duration = probe_duration(audio_path)
    if duration is None or duration <= chunk_sec:
        return [(audio_path, 0)]

    tmp_dir = tempfile.mkdtemp(prefix="funasr_chunk_")
    chunks = []
    idx = 0
    offset = 0.0
    while offset < duration:
        chunk_path = os.path.join(tmp_dir, f"chunk_{idx:03d}.wav")
        cmd = [
            "ffmpeg", "-y",
            "-i", audio_path,
            "-ss", str(offset),
            "-t", str(chunk_sec),
            "-ar", "16000", "-ac", "1", "-sample_fmt", "s16",
            chunk_path,
        ]
        subprocess.run(cmd, check=True, capture_output=True)
        chunks.append((chunk_path, offset))
        offset += chunk_sec
        idx += 1
    return chunks
