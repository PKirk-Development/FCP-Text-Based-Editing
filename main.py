#!/usr/bin/env python3
"""
FCP Text-Based Editor
CLI entry point.

Usage
-----
# Process + open editor in one step:
    python main.py edit video.mp4

# Open editor from a pre-transcribed project file:
    python main.py edit video.fte.json

# Open editor from a Final Cut Pro FCPXML (with native FCP 11 transcription):
    python main.py edit project.fcpxml

# Transcribe without opening the editor (saves .fte.json):
    python main.py process video.mp4 [--model base]

# Export without opening the editor:
    python main.py export video.fte.json output.fcpxml --format fcpxml

# List available Whisper model sizes:
    python main.py models
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

import click


# ── Helpers ───────────────────────────────────────────────────────────────────

def _project_path_for(video_path: str) -> str:
    """Return the .fte.json path that would be saved alongside *video_path*."""
    p = Path(video_path)
    return str(p.parent / (p.stem + ".fte.json"))


def _process_video(
    video_path: str,
    model_size: str,
    threshold_db: float,
    buffer: float,
    min_duration: float,
    verbose: bool,
) -> "Project":  # type: ignore[name-defined]
    """Full processing pipeline: extract audio → transcribe → detect silence → timeline."""
    from src.models      import Project, SilenceSettings
    from src.audio       import extract_audio, get_video_info, detect_silences
    from src.transcriber import transcribe
    from src.timeline    import build_timeline

    def cb(msg: str, pct: int = 0):
        if verbose:
            click.echo(f"  [{pct:3d}%] {msg}")

    settings = SilenceSettings(
        threshold_db = threshold_db,
        min_duration = min_duration,
        buffer       = max(0.001, buffer),
    )

    video_path = str(Path(video_path).resolve())

    click.echo(f"→ Inspecting video: {video_path}")
    info = get_video_info(video_path)
    click.echo(f"  {info['width']}×{info['height']} @ {info['fps']:.3f} fps  "
               f"({info['duration']:.1f} s)")

    audio_path = _project_path_for(video_path).replace(".fte.json", ".audio.wav")
    click.echo("→ Extracting audio…")
    extract_audio(video_path, audio_path, progress_cb=lambda m: cb(m, 10))

    click.echo(f"→ Transcribing with Whisper '{model_size}'…")
    words = transcribe(audio_path, model_size=model_size,
                       progress_cb=lambda m, p: cb(m, p))
    click.echo(f"  {len(words)} words transcribed.")

    click.echo("→ Detecting silence…")
    silences = detect_silences(audio_path, settings, progress_cb=lambda m: cb(m, 90))
    click.echo(f"  {len(silences)} silence region(s) found.")

    click.echo("→ Building timeline…")
    segments = build_timeline(words, silences, info["duration"], settings)
    click.echo(f"  {len(segments)} total segments (words + silences).")

    return Project(
        video_path       = video_path,
        audio_path       = audio_path,
        segments         = segments,
        deleted          = [],
        silence_settings = settings,
        video_duration   = info["duration"],
        video_fps        = info["fps"],
        video_width      = info["width"],
        video_height     = info["height"],
    )


def _process_fcpxml(
    fcpxml_path: str,
    threshold_db: float,
    buffer: float,
    min_duration: float,
    verbose: bool,
) -> "Project":  # type: ignore[name-defined]
    """Process an FCP 11 FCPXML: parse captions → detect silence → timeline."""
    from src.models       import Project, SilenceSettings
    from src.audio        import extract_audio, detect_silences
    from src.fcpxml_parser import FCPXMLProject
    from src.timeline     import build_timeline

    def cb(msg: str):
        if verbose:
            click.echo(f"  {msg}")

    settings = SilenceSettings(
        threshold_db = threshold_db,
        min_duration = min_duration,
        buffer       = max(0.001, buffer),
    )

    fcpxml_path = str(Path(fcpxml_path).resolve())
    click.echo(f"→ Parsing FCPXML: {fcpxml_path}")
    fcp = FCPXMLProject(fcpxml_path)
    click.echo(fcp.summary())

    if not fcp.video_path or not Path(fcp.video_path).exists():
        raise click.ClickException(
            f"Source video not found at '{fcp.video_path}'.\n"
            "Make sure the FCPXML was exported from FCP on the same machine, "
            "or that the media is accessible at the listed path."
        )

    if not fcp.has_captions():
        click.echo("  ⚠  No captions found in FCPXML. "
                   "Use FCP 11 'Transcribe to Captions' first, "
                   "or use  'python main.py edit video.mp4'  for Whisper transcription.")

    audio_path = _project_path_for(fcpxml_path).replace(".fte.json", ".audio.wav")
    click.echo("→ Extracting audio…")
    extract_audio(fcp.video_path, audio_path, progress_cb=cb)

    click.echo("→ Detecting silence…")
    silences = detect_silences(audio_path, settings, progress_cb=cb)
    click.echo(f"  {len(silences)} silence region(s) found.")

    click.echo("→ Building timeline…")
    segments = build_timeline(
        fcp.captions, silences, fcp.duration, settings
    )
    click.echo(f"  {len(segments)} total segments.")

    return Project(
        video_path       = fcp.video_path,
        audio_path       = audio_path,
        segments         = segments,
        deleted          = [],
        silence_settings = settings,
        video_duration   = fcp.duration,
        video_fps        = fcp.fps,
        video_width      = fcp.width,
        video_height     = fcp.height,
        source_fcpxml    = fcpxml_path,
        fcpxml_version   = fcp.fcpxml_version,
        fcpxml_asset_id  = fcp.asset_id,
        fcpxml_format_id = fcp.format_id,
    )


def _launch_editor(project: "Project") -> None:  # type: ignore[name-defined]
    """Open the CustomTkinter desktop GUI."""
    from src.editor import TextEditor
    app = TextEditor(project)
    app.mainloop()


# ── CLI commands ──────────────────────────────────────────────────────────────

@click.group()
def cli():
    """FCP Text-Based Editor — silence detection and word-level transcript editing."""
    pass


@cli.command()
@click.argument("input_file", metavar="INPUT",
                type=click.Path(exists=True, readable=True))
@click.option("--model",     "-m", default="base",
              help="Whisper model size (tiny/base/small/medium/large). Ignored for FCPXML input.")
@click.option("--threshold", "-t", default=-40.0,
              help="Silence threshold in dBFS (default -40).")
@click.option("--buffer",    "-b", default=0.050,
              help="Silence buffer in seconds (default 0.050, min 0.001 = 1 ms).")
@click.option("--min",       "-n", default=0.300,
              help="Minimum silence duration in seconds (default 0.300).")
@click.option("--verbose",   "-v", is_flag=True)
def edit(input_file: str, model: str, threshold: float,
         buffer: float, min: float, verbose: bool):
    """
    Open the text-based editor for INPUT.

    INPUT can be:
    \b
      • A video file (.mp4, .mov, .mxf, …) — Whisper transcribes it
      • An FCPXML file (.fcpxml)            — uses FCP 11 captions
      • A project file (.fte.json)          — resumes a saved session
    """
    ext = Path(input_file).suffix.lower()

    # ── Resume saved session ──────────────────────────────────────────────────
    if ext == ".json":
        from src.models import Project
        click.echo(f"→ Loading project: {input_file}")
        project = Project.load(input_file)
        _launch_editor(project)
        return

    # ── Check for saved project alongside the input file ─────────────────────
    proj_file = _project_path_for(input_file)
    if Path(proj_file).exists():
        from src.models import Project
        click.echo(f"→ Found existing project: {proj_file}")
        click.echo("  Loading saved state (re-run 'process' to re-transcribe).")
        project = Project.load(proj_file)
        _launch_editor(project)
        return

    # ── Fresh processing ──────────────────────────────────────────────────────
    if ext in (".fcpxml", ".fcpxmld"):
        project = _process_fcpxml(
            input_file, threshold, buffer, min, verbose
        )
    else:
        project = _process_video(
            input_file, model, threshold, buffer, min, verbose
        )

    # Save so the editor can be re-opened without re-processing
    project.save(proj_file)
    click.echo(f"→ Project saved: {proj_file}")

    _launch_editor(project)


@cli.command()
@click.argument("input_file", metavar="INPUT",
                type=click.Path(exists=True, readable=True))
@click.option("--model",     "-m", default="base")
@click.option("--threshold", "-t", default=-40.0)
@click.option("--buffer",    "-b", default=0.050)
@click.option("--min",       "-n", default=0.300)
@click.option("--verbose",   "-v", is_flag=True)
def process(input_file: str, model: str, threshold: float,
            buffer: float, min: float, verbose: bool):
    """
    Transcribe INPUT and save a project file (.fte.json) without opening the editor.

    Useful for batch pre-processing or running transcription on a server.
    """
    ext = Path(input_file).suffix.lower()
    if ext in (".fcpxml", ".fcpxmld"):
        project = _process_fcpxml(input_file, threshold, buffer, min, verbose)
    else:
        project = _process_video(input_file, model, threshold, buffer, min, verbose)

    proj_file = _project_path_for(input_file)
    project.save(proj_file)
    click.echo(f"✓ Project saved: {proj_file}")


@cli.command()
@click.argument("project_file", metavar="PROJECT",
                type=click.Path(exists=True, readable=True))
@click.argument("output_file", metavar="OUTPUT")
@click.option("--format", "-f",
              type=click.Choice(["fcpxml", "mp4", "edl", "sh"],
                                case_sensitive=False),
              default="fcpxml", show_default=True,
              help="Export format.")
@click.option("--stream-copy", is_flag=True,
              help="(mp4 only) Use stream copy instead of re-encoding.")
def export(project_file: str, output_file: str, format: str,
           stream_copy: bool):
    """
    Export an edited PROJECT (.fte.json) to OUTPUT without opening the editor.

    \b
    Formats:
      fcpxml   Final Cut Pro XML (default)
      mp4      Re-encoded video via FFmpeg
      edl      CMX 3600 Edit Decision List
      sh       Shell script with the FFmpeg command
    """
    from src.models   import Project
    from src          import exporter

    click.echo(f"→ Loading project: {project_file}")
    project = Project.load(project_file)

    n_del   = len(project.deleted)
    t_save  = project.time_saved()
    click.echo(f"  {n_del} segment(s) deleted  ({t_save:.3f} s saved)")
    click.echo(f"→ Exporting as {format.upper()} → {output_file}")

    if format == "fcpxml":
        exporter.export_fcpxml(project, output_file,
                               progress_cb=click.echo)
    elif format == "mp4":
        exporter.export_video(project, output_file,
                              stream_copy=stream_copy,
                              progress_cb=click.echo)
    elif format == "edl":
        exporter.export_edl(project, output_file)
        click.echo(f"✓ EDL saved: {output_file}")
    elif format == "sh":
        mp4_path = output_file.rsplit(".", 1)[0] + "_edited.mp4"
        exporter.generate_ffmpeg_script(project, mp4_path, output_file)
        click.echo(f"✓ Script saved: {output_file}")


@cli.command(name="models")
def list_models():
    """List available Whisper model sizes (smallest → fastest / largest → best)."""
    from src.transcriber import WHISPER_MODELS, DEFAULT_MODEL
    click.echo("Available Whisper models:")
    for m in WHISPER_MODELS:
        marker = " ← default" if m == DEFAULT_MODEL else ""
        click.echo(f"  {m}{marker}")
    click.echo()
    click.echo("Install all models with:  pip install openai-whisper")
    click.echo("Apple Silicon backend:    pip install torch torchvision torchaudio")


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import multiprocessing
    # Required for PyInstaller + PyTorch/Whisper frozen apps.
    # Without this, PyTorch's worker processes re-execute the GUI entry point
    # instead of becoming workers, causing an immediate crash.
    multiprocessing.freeze_support()

    if getattr(sys, "frozen", False):
        # ── Normalise sys.argv for the frozen .app bundle ─────────────────────
        # Click expects:  [executable, "edit", filepath]
        # But macOS passes files in two ways that skip the subcommand:
        #
        #   1. No args (double-clicked from Finder with no file):
        #        sys.argv = [executable]
        #   2. Apple Event / file association (dragged onto icon, or opened via
        #      "Open With"):
        #        sys.argv = [executable, "/path/to/file"]
        #
        # We normalise both into the Click-friendly form.

        _CLICK_CMDS = {"edit", "process", "export", "models", "--help", "-h"}

        if len(sys.argv) == 1:
            # Case 1: no file — show a native open-file dialog
            import tkinter as tk
            from tkinter import filedialog

            _root = tk.Tk()
            _root.withdraw()
            _root.call("wm", "attributes", ".", "-topmost", True)

            _path = filedialog.askopenfilename(
                title="Open Video, FCPXML, or Project",
                filetypes=[
                    ("Supported files",
                     "*.mp4 *.mov *.mxf *.MP4 *.MOV *.fcpxml *.fte.json"),
                    ("Video files",              "*.mp4 *.mov *.mxf *.MP4 *.MOV"),
                    ("Final Cut Pro XML",        "*.fcpxml"),
                    ("FCP Text Editor project",  "*.fte.json"),
                    ("All files",                "*"),
                ],
            )
            _root.destroy()

            if not _path:
                sys.exit(0)   # user cancelled — quit cleanly

            sys.argv = [sys.argv[0], "edit", _path]

        elif len(sys.argv) >= 2 and sys.argv[1] not in _CLICK_CMDS:
            # Case 2: file path(s) injected by Apple Events / argv_emulation
            # — they arrived without the "edit" subcommand prefix.
            sys.argv = [sys.argv[0], "edit"] + sys.argv[1:]

    # ── Run Click CLI, with a crash-reporter safety net ───────────────────────
    # In a frozen app, unhandled exceptions are invisible (the window just
    # disappears). This wrapper catches them and shows a dialog with the full
    # traceback so crashes are diagnosable.
    try:
        cli()
    except SystemExit:
        raise   # let Click's normal exit codes through
    except Exception as _exc:
        import traceback
        _tb = traceback.format_exc()

        # Try to show a GUI error dialog; fall back to stderr
        try:
            import tkinter as tk
            from tkinter import messagebox, scrolledtext

            _err_root = tk.Tk()
            _err_root.title("FCP Text Editor — Crash Report")
            _err_root.geometry("700x420")
            _err_root.configure(bg="#0a0a12")

            tk.Label(
                _err_root,
                text=f"An error occurred:\n{_exc}",
                bg="#0a0a12", fg="#ff4444",
                font=("Menlo", 12), wraplength=660, justify="left",
            ).pack(padx=16, pady=(16, 4), anchor="w")

            _st = scrolledtext.ScrolledText(
                _err_root, font=("Menlo", 10),
                bg="#0d0d1f", fg="#ccccee",
                height=16, relief="flat",
            )
            _st.insert("end", _tb)
            _st.configure(state="disabled")
            _st.pack(fill="both", expand=True, padx=16, pady=(0, 8))

            tk.Button(
                _err_root, text="Copy to Clipboard",
                command=lambda: (_err_root.clipboard_clear(),
                                 _err_root.clipboard_append(_tb)),
                bg="#1a1a3a", fg="#aaaaee", relief="flat",
            ).pack(side="left", padx=16, pady=8)
            tk.Button(
                _err_root, text="Quit",
                command=_err_root.destroy,
                bg="#2a1a1a", fg="#ee8888", relief="flat",
            ).pack(side="right", padx=16, pady=8)

            _err_root.mainloop()
        except Exception:
            print(_tb, file=sys.stderr)

        sys.exit(1)
