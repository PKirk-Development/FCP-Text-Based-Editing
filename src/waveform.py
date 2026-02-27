"""
Waveform data extraction and Canvas rendering.

Two passes
──────────
1. WaveformData.from_audio()  – reads audio once, downsamples to a peak/RMS
   array, and caches it.  Expensive the first call; free after that.

2. WaveformView.draw()        – renders the cached data to a tkinter.Canvas
   at the current canvas width.  Fast (pure Python / tkinter).

Canvas regions
──────────────
The canvas renders four layers from back to front:

  Layer 0 – background (#050508)
  Layer 1 – deleted regions     (semi-transparent red overlay)
  Layer 2 – waveform bars       (cyan peaks, slightly brighter RMS)
  Layer 3 – segment markers     (faint vertical lines at word boundaries)
  Layer 4 – playhead            (white vertical line)
"""

from __future__ import annotations

import math
from typing import Optional

import numpy as np
from pydub import AudioSegment

from .models import Project, Silence, TextSegment
from .timeline import get_keep_ranges


# ── Waveform data ─────────────────────────────────────────────────────────────

class WaveformData:
    """
    Immutable, pre-computed waveform summary for one audio file.

    Stores two arrays of length `n_bins`:
      peaks  – per-bin absolute peak (0.0 … 1.0)
      rms    – per-bin RMS energy (0.0 … 1.0)
    """

    def __init__(
        self,
        peaks:    np.ndarray,
        rms:      np.ndarray,
        duration: float,
    ) -> None:
        self.peaks    = peaks
        self.rms      = rms
        self.duration = duration
        self.n_bins   = len(peaks)

    @classmethod
    def from_audio(
        cls,
        audio_path:  str,
        n_bins:      int = 2000,
        progress_cb  = None,
    ) -> "WaveformData":
        """
        Load audio from *audio_path*, compute peak and RMS arrays.

        Parameters
        ----------
        audio_path  : WAV / MP3 / etc.
        n_bins      : Number of horizontal bins (resize later as needed).
        """
        if progress_cb:
            progress_cb("Loading audio for waveform…")

        audio = AudioSegment.from_file(audio_path)
        audio = audio.set_channels(1)   # mono
        duration = len(audio) / 1000.0

        # Raw samples as int16 numpy array
        samples = np.array(audio.get_array_of_samples(), dtype=np.float32)
        if len(samples) == 0:
            empty = np.zeros(n_bins, dtype=np.float32)
            return cls(empty, empty, duration)

        # Normalize to [-1, 1]
        max_val = max(abs(samples.max()), abs(samples.min()), 1.0)
        samples /= max_val

        # Reshape into n_bins chunks and compute peak + RMS per chunk
        # Pad to a multiple of n_bins
        pad_len = math.ceil(len(samples) / n_bins) * n_bins
        padded  = np.pad(samples, (0, pad_len - len(samples)))
        chunks  = padded.reshape(n_bins, -1)

        peaks = np.abs(chunks).max(axis=1)
        rms   = np.sqrt((chunks ** 2).mean(axis=1))

        if progress_cb:
            progress_cb("Waveform ready.")

        return cls(peaks=peaks, rms=rms, duration=duration)


# ── Waveform Canvas renderer ──────────────────────────────────────────────────

class WaveformView:
    """
    Draws the waveform + overlays on a tkinter.Canvas.

    Usage
    ─────
        canvas = tk.Canvas(parent, bg="#050508")
        view   = WaveformView(canvas, waveform_data)

        view.draw(project, playhead_s=2.5)   # call whenever state changes
        canvas.bind("<ButtonPress-1>",   view.on_click)
        canvas.bind("<B1-Motion>",       view.on_drag)
    """

    WAVEFORM_COLOR     = "#007788"
    WAVEFORM_RMS_COLOR = "#00ccdd"
    PLAYHEAD_COLOR     = "#ffffff"
    DELETED_FILL       = "#550000"
    DELETED_STIPPLE    = "gray50"
    SEGMENT_MARK_COLOR = "#333355"
    BG_COLOR           = "#050508"

    def __init__(
        self,
        canvas,                  # tkinter.Canvas
        data: WaveformData,
        on_seek: Optional[callable] = None,  # callback(time_s: float)
    ) -> None:
        self._canvas    = canvas
        self._data      = data
        self._on_seek   = on_seek
        self._playhead  = 0.0
        self._project:  Optional[Project] = None
        self._zoom_start = 0.0          # visible window start (seconds)
        self._zoom_end   = data.duration # visible window end   (seconds)

        canvas.bind("<ButtonPress-1>",   self._on_click)
        canvas.bind("<B1-Motion>",       self._on_drag)
        canvas.bind("<Configure>",       lambda _: self.draw())

    # ── Public API ────────────────────────────────────────────────────────────

    def set_project(self, project: Project) -> None:
        self._project = project

    def set_playhead(self, time_s: float) -> None:
        self._playhead = time_s

    def zoom_to(self, start_s: float, end_s: float) -> None:
        self._zoom_start = max(0.0, start_s)
        self._zoom_end   = min(self._data.duration, end_s)

    def zoom_reset(self) -> None:
        self._zoom_start = 0.0
        self._zoom_end   = self._data.duration

    def draw(
        self,
        project:    Optional[Project] = None,
        playhead_s: Optional[float]   = None,
    ) -> None:
        """Redraw the entire canvas."""
        if project is not None:
            self._project = project
        if playhead_s is not None:
            self._playhead = playhead_s

        c   = self._canvas
        w   = c.winfo_width()
        h   = c.winfo_height()
        if w < 2 or h < 2:
            return

        c.delete("all")

        # Background
        c.create_rectangle(0, 0, w, h, fill=self.BG_COLOR, outline="")

        vis_dur = self._zoom_end - self._zoom_start
        if vis_dur <= 0:
            return

        # ── Waveform bars ──────────────────────────────────────────────────
        data  = self._data
        mid   = h // 2
        n_vis = max(1, int(data.n_bins * vis_dur / data.duration))
        bin0  = int(data.n_bins * self._zoom_start / data.duration)
        bin1  = min(data.n_bins, bin0 + n_vis)

        peaks_vis = data.peaks[bin0:bin1]
        rms_vis   = data.rms[bin0:bin1]

        # Map n_vis bins → w pixels (may resample)
        for px in range(w):
            src_idx = int(bin0 + (px / w) * (bin1 - bin0))
            src_idx = min(src_idx, len(peaks_vis) - 1)
            if src_idx < 0:
                continue
            peak_h = max(1, int(peaks_vis[src_idx] * mid * 0.92))
            rms_h  = max(1, int(rms_vis[src_idx]   * mid * 0.70))

            # Peak bar (dim background)
            c.create_line(px, mid - peak_h, px, mid + peak_h,
                          fill=self.WAVEFORM_COLOR)
            # RMS bar (brighter foreground)
            c.create_line(px, mid - rms_h, px, mid + rms_h,
                          fill=self.WAVEFORM_RMS_COLOR)

        # ── Segment boundary markers ──────────────────────────────────────
        if self._project:
            for seg in self._project.segments:
                x = self._t_to_px(seg.start, w)
                if 0 <= x < w:
                    c.create_line(x, 0, x, h,
                                  fill=self.SEGMENT_MARK_COLOR, dash=(2, 4))

        # ── Deleted regions overlay ───────────────────────────────────────
        if self._project and self._project.deleted:
            keep = get_keep_ranges(
                self._project.segments,
                set(self._project.deleted),
                self._project.silence_settings.buffer,
                self._project.video_duration,
            )
            # Deleted = complement of keep
            prev = 0.0
            for ks, ke in keep:
                if ks > prev + 0.001:
                    self._draw_deleted_region(c, prev, ks, w, h)
                prev = ke
            if prev < self._data.duration - 0.001:
                self._draw_deleted_region(c, prev, self._data.duration, w, h)

        # ── Playhead ──────────────────────────────────────────────────────
        px = self._t_to_px(self._playhead, w)
        if 0 <= px < w:
            c.create_line(px, 0, px, h,
                          fill=self.PLAYHEAD_COLOR, width=2)

        # ── Time labels ───────────────────────────────────────────────────
        self._draw_time_labels(c, w, h, vis_dur)

    # ── Mouse events ──────────────────────────────────────────────────────────

    def _on_click(self, event) -> None:
        t = self._px_to_t(event.x, self._canvas.winfo_width())
        if self._on_seek:
            self._on_seek(t)

    def _on_drag(self, event) -> None:
        t = self._px_to_t(event.x, self._canvas.winfo_width())
        if self._on_seek:
            self._on_seek(t)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _t_to_px(self, t: float, canvas_w: int) -> int:
        vis_dur = self._zoom_end - self._zoom_start
        if vis_dur <= 0:
            return 0
        return int((t - self._zoom_start) / vis_dur * canvas_w)

    def _px_to_t(self, px: int, canvas_w: int) -> float:
        if canvas_w <= 0:
            return 0.0
        vis_dur = self._zoom_end - self._zoom_start
        t = self._zoom_start + (px / canvas_w) * vis_dur
        return max(0.0, min(t, self._data.duration))

    def _draw_deleted_region(
        self, c, start_s: float, end_s: float, w: int, h: int
    ) -> None:
        x1 = max(0, self._t_to_px(start_s, w))
        x2 = min(w, self._t_to_px(end_s,   w))
        if x2 > x1:
            c.create_rectangle(
                x1, 0, x2, h,
                fill    = self.DELETED_FILL,
                outline = "",
                stipple = self.DELETED_STIPPLE,
            )

    def _draw_time_labels(self, c, w: int, h: int, vis_dur: float) -> None:
        """Draw time markers along the bottom of the timeline."""
        if vis_dur <= 0:
            return
        # Choose a sensible tick interval
        for interval in [0.5, 1, 2, 5, 10, 30, 60, 120, 300]:
            n_ticks = vis_dur / interval
            if 4 <= n_ticks <= 40:
                break
        else:
            interval = vis_dur / 10

        t = self._zoom_start
        while t <= self._zoom_end:
            x = self._t_to_px(t, w)
            if 0 <= x < w:
                m, s = divmod(int(t), 60)
                label = f"{m}:{s:02d}"
                c.create_line(x, h - 12, x, h, fill="#334455")
                c.create_text(x, h - 6, text=label,
                              fill="#556677", font=("Arial", 8))
            t += interval
