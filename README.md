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
- **Python 3.10+**
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
