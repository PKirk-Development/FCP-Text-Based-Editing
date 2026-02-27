"""
setup.py  —  FCP Text-Based Editor

Supports two packaging back-ends:

  python setup.py install              Regular pip-editable install
  python setup.py py2app               macOS .app bundle (App Store path)
  pyinstaller fcp_editor.spec          Standalone .app (preferred for ad-hoc dist)

For most distribution workflows, use build_macos.sh (wraps PyInstaller).
py2app is here for the eventual Mac App Store submission path.
"""

import sys
from setuptools import setup, find_packages

# ── Base metadata ──────────────────────────────────────────────────────────────
METADATA = dict(
    name             = "fcp-text-editor",
    version          = "0.1.0",
    description      = "Text-based video editing for Final Cut Pro — silence cutting, "
                       "transcript editing, Um-Checker",
    author           = "PKirk Development",
    python_requires  = ">=3.11",
    packages         = find_packages(),
    install_requires = [
        "customtkinter>=5.2.0",
        "Pillow>=10.0.0",
        "opencv-python>=4.8.0",
        "click>=8.1.0",
        "rich>=13.7.0",
        "pydub>=0.25.1",
        "numpy>=1.24.0",
        "ffmpeg-python>=0.2.0",
    ],
    extras_require = {
        "whisper": [
            "openai-whisper>=20231117",
            # PyTorch — install separately (platform-specific):
            #   Apple Silicon:  pip install torch torchvision torchaudio
        ],
    },
    entry_points = {
        "console_scripts": ["fcp-edit=main:cli"],
    },
)

# ── py2app configuration (only active when building with py2app) ───────────────
if "py2app" in sys.argv:
    import customtkinter
    import os

    ctk_path     = os.path.dirname(customtkinter.__file__)
    PACKAGES     = ["src", "customtkinter", "PIL", "cv2", "pydub", "numpy",
                    "click", "rich"]
    INCLUDES     = ["tkinter", "_tkinter"]
    FRAMEWORKS   = []          # add AVFoundation framework path here in future
    RESOURCES    = [ctk_path]  # bundle customtkinter themes

    # Check for whisper
    try:
        import whisper
        PACKAGES += ["whisper", "torch", "torchvision", "tiktoken"]
    except ImportError:
        pass

    OPTIONS = {
        "py2app": {
            "app":          ["main.py"],
            "packages":     PACKAGES,
            "includes":     INCLUDES,
            "frameworks":   FRAMEWORKS,
            "resources":    RESOURCES,
            "iconfile":     "packaging/icon.icns",
            "plist": {
                "CFBundleName":              "FCP Text Editor",
                "CFBundleDisplayName":       "FCP Text Editor",
                "CFBundleShortVersionString": "0.1.0",
                "CFBundleVersion":           "0.1.0",
                "CFBundleIdentifier":        "com.pkirkdevelopment.fcp-text-editor",
                "NSPrincipalClass":          "NSApplication",
                "NSHighResolutionCapable":   True,
                "NSAppleScriptEnabled":      False,
                "LSApplicationCategoryType": "public.app-category.video",
                "LSMinimumSystemVersion":    "13.0",
                "NSHumanReadableCopyright":  "Copyright 2025 PKirk Development",
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
                        "CFBundleTypeName":       "Movie File",
                        "CFBundleTypeExtensions": ["mp4", "mov", "MP4", "MOV"],
                        "CFBundleTypeRole":       "Viewer",
                        "LSHandlerRank":          "Alternate",
                    },
                ],
            },
            "argv_emulation":   True,     # file-open Apple Events → sys.argv
            "semi_standalone":  False,    # fully self-contained bundle
            "strip":            True,
            "arch":             "arm64",  # M-series native
        },
    }

    METADATA["options"] = OPTIONS
    METADATA["app"]     = ["main.py"]
    METADATA["setup_requires"] = ["py2app"]

setup(**METADATA)
