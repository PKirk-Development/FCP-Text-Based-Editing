"""
Audio extraction (via FFmpeg) and silence detection (via FFmpeg silencedetect).

Key design choice: silence segments store their FULL bounds (no buffer applied).
The buffer is applied only at export time so the user can tweak it without
re-running the analysis.

Minimum buffer = 0.001 s (1 ms) — 100× better than Premiere Pro's 0.1 s floor.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Callable, Optional

from .models import Silence, SilenceSettings


# ── FFmpeg helpers ────────────────────────────────────────────────────────────

def extract_audio(
    video_path: str,
    output_path: str,
    progress_cb: Optional[Callable[[str], None]] = None,
) -> str:
    """
    Extract audio from *video_path* as a 16 kHz, mono, 16-bit PCM WAV.
    16 kHz is the sample rate Whisper prefers; mono halves file size.
    Returns *output_path* on success, raises RuntimeError on failure.
    """
    if progress_cb:
        progress_cb("Extracting audio with FFmpeg…")

    cmd = [
        "ffmpeg", "-y",
        "-i",        video_path,
        "-vn",                        # strip video stream
        "-acodec",   "pcm_s16le",     # uncompressed PCM
        "-ar",       "16000",         # 16 kHz
        "-ac",       "1",             # mono
        output_path,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError:
        raise RuntimeError(
            "ffmpeg not found.\n"
            "macOS app: rebuild with build_macos.sh (it bundles ffmpeg).\n"
            "Command line: brew install ffmpeg"
        )
    if result.returncode != 0:
        raise RuntimeError(
            f"FFmpeg audio extraction failed:\n{result.stderr[-2000:]}"
        )
    return output_path


def get_video_info(video_path: str) -> dict:
    """
    Return a dict with keys: duration, width, height, fps.
    Uses ffprobe; raises RuntimeError if the binary is not found.
    """
    cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_streams", "-show_format",
        video_path,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError:
        raise RuntimeError(
            "ffprobe not found.\n"
            "macOS app: rebuild with build_macos.sh (it bundles ffmpeg/ffprobe).\n"
            "Command line: brew install ffmpeg"
        )
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed:\n{result.stderr[-1000:]}")

    data = json.loads(result.stdout)
    info: dict = {
        "duration": float(data["format"]["duration"]),
        "width":    1920,
        "height":   1080,
        "fps":      25.0,
    }
    for stream in data.get("streams", []):
        if stream.get("codec_type") == "video":
            info["width"]  = stream.get("width",  1920)
            info["height"] = stream.get("height", 1080)
            fps_str = stream.get("r_frame_rate", "25/1")
            if "/" in fps_str:
                num, den = fps_str.split("/")
                if int(den) > 0:
                    info["fps"] = float(num) / float(den)
    return info


# ── Silence detection ─────────────────────────────────────────────────────────

def detect_silences(
    audio_path: str,
    settings: SilenceSettings,
    progress_cb: Optional[Callable[[str], None]] = None,
) -> list[Silence]:
    """
    Detect silence in *audio_path* (WAV, MP3, etc.) and return a list of
    :class:`Silence` objects whose *start* / *end* are the FULL detected bounds
    (buffer NOT yet applied — that happens at export time).

    Uses FFmpeg's ``silencedetect`` audio filter, which streams the file
    without loading it into RAM and runs as native C code — making it
    practical for arbitrarily large files (4 GB+).
    """
    if progress_cb:
        progress_cb("Analysing audio for silence…")

    noise_db = settings.threshold_db   # e.g. -40.0
    min_dur  = settings.min_duration   # e.g. 0.300 s

    cmd = [
        "ffmpeg", "-y",
        "-i",  audio_path,
        "-af", f"silencedetect=noise={noise_db}dB:duration={min_dur}",
        "-f",  "null", "-",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)

    # silencedetect writes to stderr in the form:
    #   [silencedetect @ 0x...] silence_start: 1.234
    #   [silencedetect @ 0x...] silence_end: 2.567 | silence_duration: 1.333
    silences: list[Silence] = []
    silence_start: Optional[float] = None

    for line in result.stderr.splitlines():
        m_start = re.search(r"silence_start:\s*([\d.eE+\-]+)", line)
        m_end   = re.search(r"silence_end:\s*([\d.eE+\-]+)",   line)
        if m_start:
            silence_start = float(m_start.group(1))
        elif m_end and silence_start is not None:
            silence_end = float(m_end.group(1))
            silences.append(
                Silence(
                    start       = round(max(0.0, silence_start), 4),
                    end         = round(silence_end, 4),
                    is_detected = True,
                )
            )
            silence_start = None

    if progress_cb:
        progress_cb(f"Found {len(silences)} silence region(s).")

    return silences

