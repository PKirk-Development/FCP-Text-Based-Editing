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


# ── Persistent Tk root for the frozen .app ────────────────────────────────────
# On macOS PyInstaller, calling tk.Tk() a second time after destroy() is fatal.
# We keep ONE root alive for the whole process lifetime and use Toplevel for
# all subsequent windows (file dialog, progress, crash reporter).
#
# Python 3.14 rejects `global` for any name that also carries a module-level
# type annotation (PEP 526 annotated assignment).  To avoid both the annotation
# and the `global` keyword entirely, we encapsulate the root in a small
# single-instance container whose plain (un-annotated) attribute can be read
# and written by any code in this module without `global`.
class _TkRootHolder:
    """Holds the single persistent tk.Tk() root for the whole process."""
    root: Optional[object] = None   # type: ignore[assignment]

_TK = _TkRootHolder()


# ── Progress window (shown during processing in frozen .app) ──────────────────

class _ProgressWindow:
    """
    Minimal splash that shows progress messages while the processing pipeline
    runs.  Uses tk.Toplevel so it never creates a second tk.Tk() instance.
    Falls back to a no-op if Tkinter fails for any reason.
    """

    def __init__(self, filename: str) -> None:
        self._top = None
        self._var = None
        try:
            import tkinter as tk
            if _TK.root is None:
                return
            top = tk.Toplevel(_TK.root)
            top.title("FCP Text Editor")
            top.geometry("520x140")
            top.configure(bg="#0a0a12")
            top.resizable(False, False)
            top.eval("tk::PlaceWindow . center")

            tk.Label(
                top,
                text=f"Preparing: {Path(filename).name}",
                bg="#0a0a12", fg="#aaaaee",
                font=("Menlo", 13, "bold"),
                wraplength=480,
            ).pack(padx=20, pady=(22, 6), anchor="w")

            self._var = tk.StringVar(value="Starting…")
            tk.Label(
                top,
                textvariable=self._var,
                bg="#0a0a12", fg="#888899",
                font=("Menlo", 11),
                wraplength=480,
                justify="left",
            ).pack(padx=20, anchor="w")

            self._top = top
            _TK.root.update()  # type: ignore[union-attr]
        except Exception:
            self._top = None
            self._var = None

    def update(self, msg: str) -> None:
        if self._top is None or self._var is None:
            return
        try:
            self._var.set(msg)
            _TK.root.update()  # type: ignore[union-attr]
        except Exception:
            self._top = None

    def close(self) -> None:
        if self._top is None:
            return
        try:
            self._top.destroy()
        except Exception:
            pass
        self._top = None


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
    progress_window: Optional["_ProgressWindow"] = None,
) -> "Project":  # type: ignore[name-defined]
    """Full processing pipeline: extract audio → transcribe → detect silence → timeline."""
    from src.models      import Project, SilenceSettings
    from src.audio       import extract_audio, get_video_info, detect_silences
    from src.transcriber import transcribe
    from src.timeline    import build_timeline

    def cb(msg: str, pct: int = 0):
        if verbose:
            click.echo(f"  [{pct:3d}%] {msg}")
        if progress_window:
            progress_window.update(msg)

    settings = SilenceSettings(
        threshold_db = threshold_db,
        min_duration = min_duration,
        buffer       = max(0.001, buffer),
    )

    video_path = str(Path(video_path).resolve())

    click.echo(f"→ Inspecting video: {video_path}")
    cb("Inspecting video…", 0)
    info = get_video_info(video_path)
    click.echo(f"  {info['width']}×{info['height']} @ {info['fps']:.3f} fps  "
               f"({info['duration']:.1f} s)")

    audio_path = _project_path_for(video_path).replace(".fte.json", ".audio.wav")
    click.echo("→ Extracting audio…")
    extract_audio(video_path, audio_path, progress_cb=lambda m: cb(m, 10))

    click.echo(f"→ Transcribing with Whisper '{model_size}'…")
    cb(f"Transcribing with Whisper '{model_size}'… (this can take a while for long videos)", 15)
    words = transcribe(audio_path, model_size=model_size,
                       progress_cb=lambda m, p: cb(m, p))
    click.echo(f"  {len(words)} words transcribed.")

    click.echo("→ Detecting silence…")
    silences = detect_silences(audio_path, settings, progress_cb=lambda m: cb(m, 90))
    click.echo(f"  {len(silences)} silence region(s) found.")

    click.echo("→ Building timeline…")
    cb("Building timeline…", 95)
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
    progress_window: Optional["_ProgressWindow"] = None,
) -> "Project":  # type: ignore[name-defined]
    """Process an FCP 11 FCPXML: parse captions → detect silence → timeline."""
    from src.models       import Project, SilenceSettings
    from src.audio        import extract_audio, detect_silences
    from src.fcpxml_parser import FCPXMLProject
    from src.timeline     import build_timeline

    def cb(msg: str):
        if verbose:
            click.echo(f"  {msg}")
        if progress_window:
            progress_window.update(msg)

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
    # Register the editor as the Tk root so the crash handler can attach a
    # Toplevel to it if an unhandled exception occurs inside the editor.
    # ctk.CTk is a tkinter.Tk subclass — it is the one and only Tk instance.
    _TK.root = app
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
    # Show a progress window in the frozen macOS .app (no terminal visible).
    _pw: Optional[_ProgressWindow] = None
    if getattr(sys, "frozen", False):
        _pw = _ProgressWindow(input_file)

    try:
        if ext in (".fcpxml", ".fcpxmld"):
            project = _process_fcpxml(
                input_file, threshold, buffer, min, verbose,
                progress_window=_pw,
            )
        else:
            project = _process_video(
                input_file, model, threshold, buffer, min, verbose,
                progress_window=_pw,
            )
    finally:
        if _pw:
            _pw.close()

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

    # ── Run everything inside one top-level try/except ────────────────────────
    # In a frozen app, unhandled exceptions are invisible (the window just
    # disappears). This wrapper catches them and shows a dialog with the full
    # traceback so crashes are diagnosable.
    try:
        if getattr(sys, "frozen", False):
            # ── Normalise sys.argv for the frozen .app bundle ─────────────────
            # Click expects:  [executable, "edit", filepath]
            # But macOS passes files in two ways that skip the subcommand:
            #
            #   1. No args (double-clicked from Finder with no file):
            #        sys.argv = [executable]
            #   2. Apple Event / file association (dragged onto icon, or opened
            #      via "Open With"):
            #        sys.argv = [executable, "/path/to/file"]
            #
            # We normalise both into the Click-friendly form.

            _CLICK_CMDS = {"edit", "process", "export", "models", "--help", "-h"}

            if len(sys.argv) == 1:
                # Case 1: no file — show a native macOS open-file dialog.
                #
                # We use osascript (AppleScript) instead of tkinter.filedialog
                # to avoid creating a tk.Tk() root here.  On macOS PyInstaller,
                # having two simultaneous tk.Tk() instances is fatal, and
                # TextEditor (ctk.CTk, which subclasses tk.Tk) must be the one
                # and only Tk instance in the process.  A preliminary tk.Tk()
                # kept alive alongside the editor is the exact cause of the
                # instant crash seen when opening any file.
                import subprocess as _sp

                _picker_result = _sp.run(
                    ["osascript", "-e",
                     "try\n"
                     "    set _f to choose file"
                     " with prompt \"Open a video, FCPXML, or project file\"\n"
                     "    POSIX path of _f\n"
                     "on error\n"
                     "    \"\"\n"
                     "end try"],
                    capture_output=True, text=True,
                )
                _path = _picker_result.stdout.strip()
                if not _path or not Path(_path).is_file():
                    sys.exit(0)   # user cancelled or path invalid — quit cleanly

                sys.argv = [sys.argv[0], "edit", _path]

            elif len(sys.argv) >= 2 and sys.argv[1] not in _CLICK_CMDS:
                # Case 2: file path(s) injected by Apple Events / argv_emulation
                # — they arrived without the "edit" subcommand prefix.
                sys.argv = [sys.argv[0], "edit"] + sys.argv[1:]

        cli()
    except SystemExit:
        raise   # let Click's normal exit codes through
    except Exception as _exc:
        import traceback
        _tb = traceback.format_exc()

        # ── Always write a crash log first (survives any UI failure) ──────────
        try:
            import datetime
            _log_dir = Path.home() / "Library" / "Logs" / "FCPTextEditor"
            _log_dir.mkdir(parents=True, exist_ok=True)
            _ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            (_log_dir / f"crash_{_ts}.log").write_text(_tb, encoding="utf-8")
        except Exception:
            pass

        # ── Try to show a GUI error dialog ────────────────────────────────────
        # Use Toplevel on _TK.root if available — never create a second tk.Tk()
        try:
            import tkinter as tk
            from tkinter import scrolledtext

            if _TK.root is not None:
                _err_win = tk.Toplevel(_TK.root)
            else:
                _TK.root = tk.Tk()
                _err_win = _TK.root

            _err_win.title("FCP Text Editor — Crash Report")
            _err_win.geometry("700x420")
            _err_win.configure(bg="#0a0a12")

            tk.Label(
                _err_win,
                text=f"An error occurred:\n{_exc}",
                bg="#0a0a12", fg="#ff4444",
                font=("Menlo", 12), wraplength=660, justify="left",
            ).pack(padx=16, pady=(16, 4), anchor="w")

            _st = scrolledtext.ScrolledText(
                _err_win, font=("Menlo", 10),
                bg="#0d0d1f", fg="#ccccee",
                height=16, relief="flat",
            )
            _st.insert("end", _tb)
            _st.configure(state="disabled")
            _st.pack(fill="both", expand=True, padx=16, pady=(0, 8))

            def _quit_err():
                try:
                    _err_win.destroy()
                except Exception:
                    pass
                try:
                    _TK.root.destroy()  # type: ignore[union-attr]
                except Exception:
                    pass

            tk.Button(
                _err_win, text="Copy to Clipboard",
                command=lambda: (_err_win.clipboard_clear(),
                                 _err_win.clipboard_append(_tb)),
                bg="#1a1a3a", fg="#aaaaee", relief="flat",
            ).pack(side="left", padx=16, pady=8)
            tk.Button(
                _err_win, text="Quit",
                command=_quit_err,
                bg="#2a1a1a", fg="#ee8888", relief="flat",
            ).pack(side="right", padx=16, pady=8)

            # Block until the dialog is closed.  Use wait_window for Toplevel;
            # mainloop for a plain Tk root.
            if isinstance(_err_win, tk.Toplevel):
                _TK.root.wait_window(_err_win)  # type: ignore[union-attr]
            else:
                _err_win.mainloop()
        except Exception:
            # Last resort: macOS native dialog (no Tkinter needed)
            try:
                import subprocess as _sp
                _msg = str(_exc)[:400].replace('"', "'")
                _sp.run(
                    ["osascript", "-e",
                     f'display alert "FCP Text Editor — Error" '
                     f'message "{_msg}" as critical'],
                    timeout=30,
                )
            except Exception:
                print(_tb, file=sys.stderr)

        sys.exit(1)
