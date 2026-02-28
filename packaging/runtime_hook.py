"""
PyInstaller runtime hook — FCP Text-Based Editor

Runs inside the frozen .app at startup (before main.py).
Sets PATH so that the bundled ffmpeg / ffprobe binaries are found
by pydub, ffmpeg-python, and any subprocess calls.

Also patches inspect.getsource / inspect.getsourcelines to return safe
fallbacks when called inside a frozen bundle.  Two distinct failure modes
are handled:

1. OSError ("could not get source code")
   PyInstaller compiles .py files to bytecode and does not embed the
   original source, so inspect.findsource() raises OSError for frozen
   modules.  This surfaces as an ImportError via torch's import chain.
   Fix: catch OSError and return a minimal stub for callables (returning
   "" / ([], 0) causes torch._sources.parse_def to get an empty AST and
   re-raise the same RuntimeError).

2. Whole-file fallback (start == 0, many lines, called on a function)
   When the bundled .py files ARE present (e.g. in Contents/Frameworks/)
   but Python's inspect.findsource() cannot locate the exact function
   (because co_firstlineno doesn't match the decorator pattern), it falls
   back to returning the entire file starting at line 0.  Callers such as
   torch._sources.parse_def then raise:
       RuntimeError: Expected a single top-level function: …functional.py:0
   Fix: detect the whole-file fallback for callable objects and return a
   minimal one-line stub.  parse_def sees a valid single-function AST and
   _check_overload_body passes — safe because overload body validation is
   only needed for TorchScript compilation, not for inference.
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
            # No source available; return a minimal stub for callables so
            # callers like torch._sources.parse_def get a valid single-function
            # AST instead of an empty string that fails ast.parse validation.
            if callable(obj):
                name = getattr(obj, "__name__", None) or "f"
                return f"def {name}(*args, **kwargs): pass\n"
            return ""

    def _safe_getsourcelines(obj):
        try:
            lines, start = _orig_getsourcelines(obj)
        except OSError:
            # No source available.  Return a stub for callables — returning
            # ([], 0) would cause torch._sources.parse_def to receive an empty
            # string, produce an empty AST, and re-raise RuntimeError.
            if callable(obj):
                name = getattr(obj, "__name__", None) or "f"
                return [f"def {name}(*args, **kwargs): pass\n"], 0
            return [], 0

        # Detect "whole-file" fallback: findsource returns (all_lines, 0)
        # when it cannot locate the exact function in the source file.
        # This happens for @torch.jit._overload-decorated functions whose
        # co_firstlineno doesn't anchor the backwards scan correctly.
        # Return a minimal stub so callers like torch.parse_def see exactly
        # one top-level function and don't raise RuntimeError.
        #
        # The hasattr(__code__) guard was intentionally removed: some callables
        # (e.g. Cython-compiled or C-extension wrappers) are callable but lack
        # __code__, yet still cause parse_def to fail in the same way.
        if (start == 0
                and len(lines) > 20
                and callable(obj)):
            name = getattr(obj, "__name__", None) or "f"
            return [f"def {name}(*args, **kwargs): pass\n"], 0

        return lines, start

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
