# FCP Text-Based Editor

A professional text-based video editing tool for Final Cut Pro — cut silences,
delete filler words, and trim your edit from a transcript instead of a timeline.

---

## Features

| Feature | Detail |
|---|---|
| **Transcript editing** | Click, drag, Shift-click, Delete — feels like editing a Word doc |
| **Silence detection** | 1 ms precision (vs Premiere Pro's 100 ms minimum) |
| **Silence buffer** | Adjustable from 1 ms to any value; no re-analysis needed |
| **Um-Checker** | One-click detection and deletion of filler words (um, uh, like, basically…) |
| **FCP 11 round-trip** | Parse FCPXML captions → edit → export FCPXML back to Final Cut Pro |
| **Video preview** | Live edit-aware preview — deleted segments are skipped during playback |
| **Waveform timeline** | Audio waveform with deleted-region overlays and click-to-seek |
| **Multiple export formats** | FCPXML · MP4 · EDL · Shell script |
| **Undo / redo** | Full unlimited undo/redo stack |

---

## GUI Layout

```
┌─ FCP Text-Based Editor · video.mp4 · 02:34 ──────────────────────────────────┐
│  Threshold [-40.0] dB   Buffer [0.050] s   Min silence [0.300] s             │
│  [Auto-Delete Silence]  [Re-analyse]  [Restore All]  [Undo]  [Redo]          │
│  [✓ Um-Checker]                                                               │
├────────────────────────────────────┬──────────────────────────────────────────┤
│  TRANSCRIPT                        │  PREVIEW                                 │
│                                    │                                          │
│  Hello there, this is              │     ┌──────────────────────────────┐    │
│  [■ 0.85s] a test of the           │     │                              │    │
│  text-based editing tool.          │     │       video frame            │    │
│  You can [■ 1.23s] highlight       │     │                              │    │
│  words and silence blocks          │     └──────────────────────────────┘    │
│  [■ 0.45s] and delete them.        │  ⏮ ◀◀  ▶  ▶▶ ⏭   0:02.5 / 2:34      │
├────────────────────────────────────┴──────────────────────────────────────────┤
│  TIMELINE  ██████████████████▓▓░░░░░░▓▓████████████████████████████████     │
├───────────────────────────────────────────────────────────────────────────────┤
│  Segments: 142  Deleted: 3  Time saved: 2.530 s   [Export ▸]                 │
└───────────────────────────────────────────────────────────────────────────────┘
```

---

## Installation

### Prerequisites

- **macOS** (Apple Silicon M1/M2/M3/M4 recommended) or Linux for development
- **Python 3.10+** for running the tool directly; **Python 3.11 or 3.12** for building the .app bundle (see [Building the App](#building-the-app-making-a-dmg-to-share))
- **FFmpeg** binary: `brew install ffmpeg`

### Install Python dependencies

```bash
# Core tool
pip install -r requirements.txt

# Whisper transcription (for .mp4 workflow)
# Apple Silicon — install PyTorch first:
pip install torch torchvision torchaudio
pip install openai-whisper
```

---

## Usage

### Open a video file (Whisper transcription)

```bash
python main.py edit path/to/video.mp4
```

Whisper transcribes the audio (model size configurable), then the GUI opens with
the full transcript and detected silences.

### Open a Final Cut Pro export (FCPXML with captions)

```bash
python main.py edit path/to/timeline.fcpxml
```

Requires FCP 11 "Transcribe to Captions" to have been run first (Apple Silicon + Sequoia).

### Resume a saved session

```bash
python main.py edit path/to/project.fte.json
```

Sessions auto-save as `<video_basename>.fte.json` alongside the source file.

### CLI-only processing (no GUI)

```bash
# Transcribe + detect silences, save project JSON
python main.py process video.mp4 --model base --threshold -40 --buffer 0.050

# Export without opening the GUI
python main.py export project.fte.json output.fcpxml --format fcpxml
python main.py export project.fte.json output.mp4    --format mp4
python main.py export project.fte.json output.edl    --format edl
python main.py export project.fte.json export.sh     --format sh

# List available Whisper models
python main.py models
```

---

## Editing keyboard shortcuts

| Key | Action |
|---|---|
| **Click** | Select segment |
| **Shift+Click** | Extend selection |
| **Click+Drag** | Select range |
| **Double-click** | Select single segment |
| **Triple-click** | Select entire word block |
| **Ctrl+A** | Select all |
| **Delete / BackSpace** | Cut selected segments from video |
| **U** | Restore (un-delete) selected segments |
| **Escape** | Clear selection |
| **Space / K** | Play / pause |
| **J** | Seek back 5 seconds |
| **L** | Seek forward 5 seconds |
| **Ctrl+Z** | Undo |
| **Ctrl+Y / Ctrl+Shift+Z** | Redo |
| **E** | Open export dialog |

---

## Silence precision

The silence buffer controls how much audio is preserved on each side of a
deleted silence.  This tool supports **1 ms minimum** precision:

| Tool | Minimum buffer |
|---|---|
| Adobe Premiere Pro | 100 ms |
| This tool | **1 ms** |

The buffer is stored at the project level and applied only at export time, so
you can adjust it freely without re-running the audio analysis.

---

## Export formats

| Format | Use case |
|---|---|
| **FCPXML** | Import back into Final Cut Pro — full round-trip |
| **MP4** | Re-encoded video via FFmpeg (stream copy optional for speed) |
| **EDL** | CMX 3600 edit decision list — Premiere Pro, Avid, DaVinci |
| **Shell script** | FFmpeg bash command you can run or inspect |

---

## Building the App (making a .dmg to share)

> Follow these steps exactly. You only have to do this once to get the app built — after that you can just share the .dmg file with anyone.

---

### Step 1 — Get the code onto your Mac

If you haven't already, clone this repo:

```
git clone <repo-url>
cd FCP-Text-Based-Editing
```

---

### Step 2 — Install Homebrew (if you don't have it)

Homebrew is like an app store for developer tools. Open **Terminal** (search for it in Spotlight) and paste this:

```
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

It will ask for your Mac password. Type it and press Enter (you won't see the letters — that's normal). Wait for it to finish.

---

### Step 3 — Install Python 3.12

Python **3.11 or 3.12** is recommended for building the app bundle.  Python 3.13+ has
known compatibility issues with PyTorch and the PyInstaller packaging toolchain
(e.g. `ModuleNotFoundError: No module named 'unittest'` at runtime in frozen apps).

```
brew install python@3.12
```

> **Note:** Python 3.10+ works fine for running the tool directly (`python main.py …`).
> The 3.11/3.12 constraint applies only to **building the .app bundle**.

Wait for it to finish.

---

### Step 4 — Install ffmpeg

ffmpeg is the tool that actually cuts the video. Run:

```
brew install ffmpeg
```

Wait for it to finish. This one can take a few minutes.

---

### Step 5 — Make the build script runnable

You only need to do this once:

```
chmod +x build_macos.sh packaging/create_dmg.sh packaging/make_icon.sh
```

---

### Step 6 — Build the app

Run this one command. It does everything automatically (creates a virtual environment, installs all the Python packages, bundles everything into a .app):

```
./build_macos.sh --dmg
```

You'll see a bunch of text scrolling by. **This is normal.** It takes about 3–5 minutes. When it's done you'll see a big green "Build complete" box.

> **Want to include Whisper** (so the app can transcribe raw video files, not just FCPXML)?
> This makes the download much larger (~1.5 GB) but adds transcription.
> Run this instead:
> ```
> ./build_macos.sh --with-whisper --dmg
> ```

---

### Step 7 — Find your finished files

When the build finishes, look in the `dist/` folder inside the project:

```
dist/
├── FCP Text Editor.app        ← the app — double-click to test it
└── FCP-Text-Editor-0.1.0.dmg  ← share THIS file with people
```

Double-click `FCP Text Editor.app` to make sure it works on your machine first.

---

### Step 8 — Share it

Send the `.dmg` file to whoever needs it. They just:
1. Double-click the `.dmg`
2. Drag `FCP Text Editor` into their Applications folder
3. Double-click it to open

> **Heads up:** If the person you send it to gets a warning saying *"Apple can't check this app for malicious software"*, they need to:
> Right-click the app → click **Open** → click **Open** again.
> It'll work fine after that. (This happens because the app isn't signed with a paid Apple Developer account yet.)

---

### Adding your own app icon (optional)

1. Make a square image (ideally 1024×1024 pixels) — a PNG works great
2. Run:
   ```
   ./packaging/make_icon.sh path/to/your-image.png
   ```
3. Then rebuild:
   ```
   ./build_macos.sh --dmg
   ```

---

### If something goes wrong

| Problem | Fix |
|---|---|
| `command not found: brew` | Go back to Step 2 |
| `command not found: python3.11` | Go back to Step 3 |
| `ffmpeg not found` | Go back to Step 4 |
| `permission denied: ./build_macos.sh` | Go back to Step 5 |
| Build fails with a Python error | Run `./build_macos.sh --clean --dmg` to start fresh |

---

## Architecture & commercial macOS roadmap

The codebase is structured for future distribution as a standalone **macOS
application** targeting M-series hardware:

```
src/
├── models.py         # Pure data — Project, TextSegment, Silence, SilenceSettings
├── audio.py          # FFmpeg extraction + pydub 1 ms silence detection
├── transcriber.py    # Whisper word-level transcription
├── fcpxml_parser.py  # FCP 11 FCPXML round-trip parser/exporter
├── timeline.py       # Merge words + silences into unified segment list
├── exporter.py       # FCPXML / MP4 / EDL / shell export
├── video_player.py   # AbstractVideoPlayer + OpenCVPlayer
│                     #   → swap in AVFoundationPlayer for production macOS
├── waveform.py       # WaveformData (numpy) + WaveformView (tkinter Canvas)
└── editor.py         # Full 3-panel CustomTkinter desktop GUI
main.py               # Click CLI entry point
```

### macOS app packaging path

1. **Current**: Python + CustomTkinter (works on any macOS / Linux dev machine)
2. **Near-term**: Bundle with **PyInstaller** → `.app` for direct distribution
3. **Production**: Bundle with **py2app** → notarised `.app` for Mac App Store
4. **Video player**: Replace `OpenCVPlayer` with `AVFoundationPlayer` (PyObjC)
   for hardware-accelerated ProRes / HEVC / HDR playback — the `AbstractVideoPlayer`
   interface means `editor.py` never changes

---

## Colour guide

| Segment type | Normal | Selected | Deleted |
|---|---|---|---|
| Word | dim white | white bold on blue | red strikethrough |
| Silence (long) | cyan on dark | cyan bold on blue | dark red |
| Silence (gap) | dark purple | purple on blue | very dark red |
| Filler word | — | — | orange highlight (Um-Checker) |
