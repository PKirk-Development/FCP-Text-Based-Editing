"""
Whisper-based transcription with word-level timestamps.

Requires:  pip install openai-whisper
           pip install torch torchvision torchaudio   (Apple Silicon: MPS backend)

Word-level timestamps are obtained by passing word_timestamps=True.
Each word in the result carries a precise start/end in seconds.
"""

from __future__ import annotations

import ssl
import urllib.request
import warnings
from typing import Callable, Optional

from .models import TextSegment


def _install_ssl_context() -> None:
    """
    Patch urllib's default HTTPS handler to use certifi's CA bundle.

    On macOS, Python installed from python.org ships without the system
    keychain CAs, so Whisper's model download fails with
    CERTIFICATE_VERIFY_FAILED.  On corporate networks an MITM proxy may
    present a self-signed chain; in that case we fall back to unverified
    HTTPS and emit a warning.
    """
    try:
        import certifi
        ctx = ssl.create_default_context(cafile=certifi.where())
    except Exception:
        ctx = ssl.create_default_context()

    opener = urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=ctx)
    )
    urllib.request.install_opener(opener)


# Available model sizes (smallest → fastest, largest → most accurate)
WHISPER_MODELS = ["tiny", "base", "small", "medium", "large", "large-v2", "large-v3"]
DEFAULT_MODEL  = "base"


def transcribe(
    audio_path: str,
    model_size: str                          = DEFAULT_MODEL,
    language:   Optional[str]                = None,   # None → auto-detect
    progress_cb: Optional[Callable[[str, int], None]] = None,
) -> list[TextSegment]:
    """
    Transcribe *audio_path* (any format accepted by Whisper) and return a list
    of :class:`TextSegment` objects with word-level timing.

    Parameters
    ----------
    audio_path  : Path to audio file (WAV, MP3, …)
    model_size  : One of WHISPER_MODELS.  "base" is a good default.
    language    : ISO 639-1 code ("en", "fr", …) or None for auto-detect.
    progress_cb : Called with (message, percent) during processing.

    Returns
    -------
    List of TextSegment, one per word, sorted by start time.
    """
    try:
        import whisper
    except (ImportError, RuntimeError, OSError) as exc:
        # ImportError / ModuleNotFoundError: whisper or a dependency is missing.
        # RuntimeError: PyTorch / CUDA / MPS backend failed to initialise.
        # OSError: a data file (mel filterbank, tokenizer vocab, …) is missing
        #          — common in PyInstaller bundles where assets weren't collected.
        raise ImportError(
            "openai-whisper is not installed or failed to initialize.\n"
            "Run:  pip install openai-whisper\n"
            "And for Apple Silicon:  pip install torch torchvision torchaudio"
        ) from exc

    if progress_cb:
        progress_cb(f"Loading Whisper model '{model_size}'…", 5)

    _install_ssl_context()
    try:
        model = whisper.load_model(model_size)
    except Exception as exc:
        # If the download failed due to SSL (e.g. corporate MITM proxy with a
        # self-signed certificate), retry without certificate verification.
        if "CERTIFICATE_VERIFY_FAILED" in str(exc) or "SSL" in str(exc):
            warnings.warn(
                "Whisper model download failed SSL verification "
                "(self-signed certificate in chain?). "
                "Retrying without certificate verification.",
                stacklevel=2,
            )
            ctx = ssl._create_unverified_context()
            opener = urllib.request.build_opener(
                urllib.request.HTTPSHandler(context=ctx)
            )
            urllib.request.install_opener(opener)
            model = whisper.load_model(model_size)
        else:
            raise

    if progress_cb:
        progress_cb("Transcribing audio…", 15)

    options: dict = {
        "word_timestamps": True,   # critical: gives per-word timing
        "verbose":         False,
    }
    if language:
        options["language"] = language

    result = model.transcribe(audio_path, **options)

    words: list[TextSegment] = []
    segments = result.get("segments", [])
    total = len(segments)

    for seg_idx, segment in enumerate(segments):
        if progress_cb and total > 0:
            pct = 15 + int(80 * seg_idx / total)
            progress_cb(f"Processing segment {seg_idx + 1}/{total}…", pct)

        for word_data in segment.get("words", []):
            text = word_data.get("word", "").strip()
            if not text:
                continue
            start = round(float(word_data.get("start", 0.0)), 4)
            end   = round(float(word_data.get("end",   0.0)), 4)
            if end <= start:
                end = start + 0.001  # safety: ensure positive duration
            words.append(TextSegment(text=text, start=start, end=end))

    # Sort (Whisper segments are already ordered but defensive sort is cheap)
    words.sort(key=lambda w: w.start)

    if progress_cb:
        progress_cb(f"Transcription complete: {len(words)} words.", 100)

    return words


def list_models() -> list[str]:
    """Return available Whisper model names."""
    return list(WHISPER_MODELS)
