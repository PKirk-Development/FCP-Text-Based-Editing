"""
PyInstaller runtime hook — FCP Text-Based Editor

Runs inside the frozen .app at startup (before main.py).
Sets PATH so that the bundled ffmpeg / ffprobe binaries are found
by pydub, ffmpeg-python, and any subprocess calls.

Also patches inspect.getsource / inspect.getsourcelines to return safe
fallbacks when called inside a frozen bundle.  PyInstaller compiles all
.py files to bytecode and does not embed the original source, so any
library that calls inspect.getsource() at import time (e.g.
torch/utils/_config_module.py → get_assignments_with_compile_ignored_comments)
raises OSError: could not get source code.  That OSError propagates through
the torch and whisper import chains and surfaces as:

    ImportError: openai-whisper is not installed or failed to initialize.

Returning an empty string from getsource() is safe here: the torch helper
that triggers this simply scans source for "# compile_ignored" comments to
build an exclusion set for torch.compile.  An empty string means "no
compile_ignored assignments", which is the correct default for inference-only
frozen builds.
"""

import os
import sys

if getattr(sys, "frozen", False):
    # ── Patch inspect so torch/whisper can import without source code ──────────
    import inspect as _inspect

    _orig_getsource      = _inspect.getsource
    _orig_getsourcelines = _inspect.getsourcelines

    def _safe_getsource(obj):
        try:
            return _orig_getsource(obj)
        except OSError:
            return ""

    def _safe_getsourcelines(obj):
        try:
            return _orig_getsourcelines(obj)
        except OSError:
            return [], 0

    _inspect.getsource      = _safe_getsource
    _inspect.getsourcelines = _safe_getsourcelines

    # ── ffmpeg / ffprobe PATH setup ────────────────────────────────────────────
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
