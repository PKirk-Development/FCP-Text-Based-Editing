"""
Audio extraction (via FFmpeg) and 1 ms-precision silence detection (via pydub).

Key design choice: silence segments store their FULL bounds (no buffer applied).
The buffer is applied only at export time so the user can tweak it without
re-running the analysis.

Minimum buffer = 0.001 s (1 ms) — 100× better than Premiere Pro's 0.1 s floor.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Callable, Optional

from pydub import AudioSegment
from pydub.silence import detect_silence

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
    result = subprocess.run(cmd, capture_output=True, text=True)
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
    result = subprocess.run(cmd, capture_output=True, text=True)
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

    Precision notes
    ---------------
    ``pydub.silence.detect_silence`` with ``seek_step=1`` analyses the audio
    in 1 ms windows.  Combined with the 16 kHz sample rate this gives 1 ms
    timing resolution — the same precision as the 0.001 s minimum buffer the
    user can set.

    ``seek_step`` is in milliseconds (pydub default is 1).  Leaving it at 1
    is the safest choice; larger values are faster but coarser.
    """
    if progress_cb:
        progress_cb("Analysing audio for silence…")

    audio = AudioSegment.from_file(audio_path)
    audio_len_ms = len(audio)

    min_silence_ms = max(1, int(settings.min_duration * 1000))

    # pydub returns [[start_ms, end_ms], ...]
    raw: list[list[int]] = detect_silence(
        audio,
        min_silence_len = min_silence_ms,
        silence_thresh  = settings.threshold_db,
        seek_step       = 1,        # 1 ms windows — maximum precision
    )

    silences: list[Silence] = []
    for start_ms, end_ms in raw:
        # Clamp to audio length (pydub can occasionally return end > length)
        start_ms = max(0, min(start_ms, audio_len_ms))
        end_ms   = max(0, min(end_ms,   audio_len_ms))
        if end_ms <= start_ms:
            continue
        silences.append(
            Silence(
                start       = round(start_ms / 1000.0, 4),
                end         = round(end_ms   / 1000.0, 4),
                is_detected = True,
            )
        )

    if progress_cb:
        progress_cb(f"Found {len(silences)} silence region(s).")

    return silences


def is_audio_silent_at(
    audio_path: str,
    start_s: float,
    end_s: float,
    threshold_db: float = -40.0,
) -> bool:
    """
    Quick check: is the audio segment [start_s, end_s] below *threshold_db*?
    Used by the timeline builder to classify Whisper word-gaps.
    """
    audio = AudioSegment.from_file(audio_path)
    start_ms = int(start_s * 1000)
    end_ms   = int(end_s   * 1000)
    chunk = audio[start_ms:end_ms]
    if len(chunk) == 0:
        return True
    return chunk.dBFS < threshold_db
