# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec — FCP Text-Based Editor  (M-series macOS .app bundle)

Build variants
──────────────
  Lite  (no Whisper, ~150 MB):
      pyinstaller fcp_editor.spec

  Full  (includes Whisper + PyTorch, ~1.5–2 GB):
      BUILD_WITH_WHISPER=1 pyinstaller fcp_editor.spec

Recommended workflow: use build_macos.sh instead of calling PyInstaller
directly — it handles venv creation, ffmpeg bundling, signing, and DMG.
"""

import os
import sys
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

# ── Build configuration ────────────────────────────────────────────────────────
WITH_WHISPER = os.environ.get("BUILD_WITH_WHISPER", "0") == "1"
APP_NAME     = "FCP Text Editor"
BUNDLE_ID    = "com.pkirkdevelopment.fcp-text-editor"
VERSION      = "0.1.0"
ICON         = "packaging/icon.icns"
ENTITLEMENTS = "packaging/entitlements.plist"

print(f"\n── FCP Editor  |  WITH_WHISPER={WITH_WHISPER}  |  VERSION={VERSION} ──\n")

# ── Collect data files (theme assets, mel filters, etc.) ──────────────────────
datas = []
datas += collect_data_files("customtkinter")   # dark / light themes JSON

if WITH_WHISPER:
    datas += collect_data_files("whisper")         # mel filterbanks, tokenizer assets
    try:
        datas += collect_data_files("tiktoken_ext")
    except Exception:
        pass

# ── Hidden imports ─────────────────────────────────────────────────────────────
hiddenimports = [
    # GUI toolkit
    "customtkinter",
    "PIL", "PIL.Image", "PIL.ImageTk", "PIL._tkinter_finder",
    "PIL.BmpImagePlugin", "PIL.JpegImagePlugin",
    "PIL.PngImagePlugin", "PIL.GifImagePlugin",
    "tkinter", "tkinter.filedialog", "tkinter.messagebox",
    "_tkinter",
    # Video decoding
    "cv2",
    # Audio
    "pydub", "pydub.audio_segment", "pydub.effects", "pydub.generators",
    "numpy", "numpy.core._multiarray_umath",
    # CLI
    "click", "click.core", "click.decorators", "click.utils",
    "rich", "rich.console", "rich.progress", "rich.theme", "rich.markup",
    # This application
    "src", "src.models", "src.audio", "src.transcriber",
    "src.fcpxml_parser", "src.timeline", "src.exporter",
    "src.editor", "src.video_player", "src.waveform",
]

if WITH_WHISPER:
    hiddenimports += [
        "whisper",
        "torch", "torchvision", "torchaudio",
        "tiktoken", "tiktoken_ext", "tiktoken_ext.openai_public",
        "ffmpeg",
    ]
    # packaging/hooks/hook-torch.py (in hookspath below) handles the full
    # torch submodule collection, stdlib hiddenimports (e.g. unittest), and
    # tensorboard exclusion — no need to duplicate that logic here.
    hiddenimports += collect_submodules("torchvision")
    hiddenimports += collect_submodules("torchaudio")
    hiddenimports += collect_submodules("whisper")

# ── Exclusions (slim the bundle) ───────────────────────────────────────────────
excludes = [
    "matplotlib", "scipy", "pandas",
    "IPython", "jupyter", "notebook", "nbformat",
    "sklearn", "skimage", "sympy", "docutils",
    "wx", "PyQt5", "PyQt6", "PySide2", "PySide6",
    "setuptools._vendor", "pkg_resources._vendor",
    "test", "_pytest",
    # Note: do NOT exclude `unittest` here — torch.utils._config_module and
    # other torch/whisper internals import it at runtime inside the frozen app.
]

if not WITH_WHISPER:
    excludes += ["whisper", "torch", "torchvision", "torchaudio", "tiktoken"]
else:
    # tensorboard is not required at runtime; excluding it prevents the
    # build-time warning: "failed to collect submodules for
    # torch.utils.tensorboard because tensorboard is not installed."
    excludes += ["tensorboard", "torch.utils.tensorboard"]

# ── Analysis ───────────────────────────────────────────────────────────────────
a = Analysis(
    ["main.py"],
    pathex=["."],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=["packaging/hooks"],
    hooksconfig={},
    runtime_hooks=["packaging/runtime_hook.py"],
    excludes=excludes,
    noarchive=False,
    optimize=1,
)

pyz = PYZ(a.pure, optimize=1)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,              # No terminal window
    disable_windowed_traceback=False,
    argv_emulation=False,       # Disabled: Carbon argv_emulation is deprecated on
                                # macOS 13+ and causes the app to appear as two dock
                                # icons on launch.  File-open events are handled via
                                # the osascript file picker (no-args case) and the
                                # sys.argv injection below (CLI / Apple Events case).
    target_arch="arm64",        # Native M-series; change to "universal2" for Intel+Apple
    codesign_identity=None,     # Passed via --codesign-identity flag or CODESIGN_ID env
    entitlements_file=ENTITLEMENTS,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=["*.dylib", "*.so", "*.framework"],
    name=APP_NAME,
)

app = BUNDLE(
    coll,
    name=f"{APP_NAME}.app",
    icon=ICON if os.path.exists(ICON) else None,
    bundle_identifier=BUNDLE_ID,
    version=VERSION,
    info_plist={
        # ── Identity ──────────────────────────────────────────────────────────
        "CFBundleName":              APP_NAME,
        "CFBundleDisplayName":       APP_NAME,
        "CFBundleShortVersionString": VERSION,
        "CFBundleVersion":           VERSION,
        "CFBundleIdentifier":        BUNDLE_ID,
        "NSPrincipalClass":          "NSApplication",
        "NSHighResolutionCapable":   True,
        "NSAppleScriptEnabled":      False,

        # ── App Store category / system requirements ───────────────────────────
        "LSApplicationCategoryType": "public.app-category.video",
        "LSMinimumSystemVersion":    "13.0",   # macOS Ventura+
        "NSHumanReadableCopyright":  "Copyright 2025 PKirk Development",

        # ── File type associations ─────────────────────────────────────────────
        "CFBundleDocumentTypes": [
            {
                "CFBundleTypeName":       "FCP Text Editor Project",
                "CFBundleTypeExtensions": ["fte"],
                "CFBundleTypeRole":       "Editor",
                "LSHandlerRank":          "Owner",
            },
            {
                "CFBundleTypeName":       "Final Cut Pro XML",
                "CFBundleTypeExtensions": ["fcpxml"],
                "CFBundleTypeRole":       "Editor",
                "LSHandlerRank":          "Alternate",
            },
            {
                "CFBundleTypeName":       "Final Cut Pro XML Package",
                "CFBundleTypeExtensions": ["fcpxmld"],
                "CFBundleTypeRole":       "Editor",
                "LSHandlerRank":          "Alternate",
                # fcpxmld is a directory package; this flag lets Launch Services
                # treat it as a document rather than a folder.
                "LSTypeIsPackage":        True,
            },
            {
                "CFBundleTypeName":       "Movie File",
                "CFBundleTypeExtensions": ["mp4", "mov", "MP4", "MOV", "mxf"],
                "CFBundleTypeRole":       "Viewer",
                "LSHandlerRank":          "Alternate",
            },
        ],

        # ── Hardened runtime (required for Notarization / Gatekeeper) ─────────
        # Python extensions require these two exceptions.
        "com.apple.security.cs.allow-unsigned-executable-memory": True,
        "com.apple.security.cs.disable-library-validation":       True,
    },
)
