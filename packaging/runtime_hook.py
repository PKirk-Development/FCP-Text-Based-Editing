"""
PyInstaller runtime hook — FCP Text-Based Editor

Runs inside the frozen .app at startup (before main.py).
Sets PATH so that the bundled ffmpeg / ffprobe binaries are found
by pydub, ffmpeg-python, and any subprocess calls.
"""

import os
import sys

if getattr(sys, "frozen", False):
    # The MacOS/ directory sits next to the app executable and is where
    # build_macos.sh copies the ffmpeg and ffprobe binaries.
    _macos_dir = os.path.dirname(sys.executable)

    # Prepend to PATH so the bundled binaries take priority over any
    # system-wide ffmpeg the user may have installed.
    os.environ["PATH"] = _macos_dir + os.pathsep + os.environ.get("PATH", "")

    # pydub respects FFMPEG_BINARY and FFPROBE_BINARY env vars.
    _ffmpeg  = os.path.join(_macos_dir, "ffmpeg")
    _ffprobe = os.path.join(_macos_dir, "ffprobe")

    if os.path.isfile(_ffmpeg):
        os.environ["FFMPEG_BINARY"]  = _ffmpeg
        os.environ["FFMPEG_PATH"]    = _ffmpeg   # alternate convention

    if os.path.isfile(_ffprobe):
        os.environ["FFPROBE_BINARY"] = _ffprobe
        os.environ["FFPROBE_PATH"]   = _ffprobe
