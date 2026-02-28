"""
Video player abstraction layer.

Architecture note (commercial macOS roadmap)
────────────────────────────────────────────
The AbstractVideoPlayer defines the interface.  Right now we ship
OpenCVPlayer (cross-platform, works on Linux for development).

When moving to a production macOS app:
  • Swap in AVFoundationPlayer (PyObjC / AVKit) for hardware-accelerated,
    HDR-capable, system-integrated playback.
  • The editor.py UI code never changes because it only calls the abstract API.

OpenCVPlayer limitations on macOS (known)
─────────────────────────────────────────
  • No hardware acceleration (CPU only)
  • No ProRes / HEVC HDR support in all OpenCV builds
  • Frame timing not perfectly V-sync-aware

Future: replace with AVFoundation via subprocess call to a minimal Swift helper,
or via PyObjC bindings.  See docs/MACOS_ROADMAP.md (to be added).
"""

from __future__ import annotations

import time
import threading
from abc import ABC, abstractmethod
from typing import Callable, Optional

from .timeline import get_keep_ranges
from .models   import Project


# ── Abstract interface ────────────────────────────────────────────────────────

class AbstractVideoPlayer(ABC):
    """
    Platform-agnostic video player contract.

    frame_callback(frame_rgb: ndarray, time_s: float) → None
        Called from the play loop whenever a new frame is ready.
        The callback MUST schedule the actual display on the main thread
        (e.g. using widget.after(0, ...)).

    time_callback(time_s: float) → None
        Called periodically (every frame) with the current playback position
        in source-media seconds.  Used to update the playhead.
    """

    @abstractmethod
    def set_frame_callback(self, cb: Callable) -> None:
        """Register function called with (frame_rgb_ndarray, time_s)."""

    @abstractmethod
    def set_time_callback(self, cb: Callable[[float], None]) -> None:
        """Register function called with current time in seconds."""

    @abstractmethod
    def load(self, video_path: str) -> None:
        """Load (or reload) a video file."""

    @abstractmethod
    def seek(self, time_s: float) -> None:
        """Jump to *time_s* seconds in source media and display that frame."""

    @abstractmethod
    def play(self) -> None:
        """Start (or resume) playback."""

    @abstractmethod
    def pause(self) -> None:
        """Pause playback."""

    @abstractmethod
    def toggle(self) -> None:
        """Toggle play / pause."""

    @property
    @abstractmethod
    def is_playing(self) -> bool: ...

    @property
    @abstractmethod
    def current_time(self) -> float:
        """Current position in source-media seconds."""

    @property
    @abstractmethod
    def duration(self) -> float:
        """Total source-media duration in seconds."""

    @abstractmethod
    def set_project(self, project: Project) -> None:
        """
        Provide the project so the player can skip deleted segments during
        playback (edit-aware preview).
        """

    @abstractmethod
    def close(self) -> None:
        """Release all resources."""


# ── OpenCV implementation ─────────────────────────────────────────────────────

class OpenCVPlayer(AbstractVideoPlayer):
    """
    OpenCV-based video player with pygame audio.

    Edit-aware playback
    ───────────────────
    When a Project is attached (via set_project), the play loop checks whether
    the current frame falls inside a deleted region and, if so, seeks to the
    start of the next keep range — giving the user a live preview of the edited
    result.

    Audio
    ─────
    OpenCV's Python bindings carry no audio.  We drive audio separately via
    pygame.mixer, playing the pre-extracted WAV (project.audio_path) that was
    already written during transcription.  Seek, pause, and stop are mirrored
    to the mixer so playback stays in sync.

    Video performance
    ─────────────────
    For preview purposes the play loop caps output at MAX_PREVIEW_FPS (24 fps)
    and never tries to decode faster than that, regardless of source frame rate.
    This keeps the main thread / Tk paint loop from being overwhelmed by fast
    (e.g. 60 fps) sources.  Frames are also decoded at full resolution — the
    resize-to-canvas step is done in the editor's _display_frame, not here.
    """

    MAX_PREVIEW_FPS = 24  # cap preview rate for Tk canvas performance

    def __init__(self) -> None:
        try:
            import cv2
            self._cv2 = cv2
        except ImportError:
            raise ImportError(
                "opencv-python is required for video playback.\n"
                "  pip install opencv-python"
            )

        self._cap           = None
        self._video_path    = ""
        self._fps           = 25.0
        self._duration      = 0.0
        self._current_time  = 0.0
        self._playing       = False
        self._lock          = threading.Lock()
        self._play_thread:  Optional[threading.Thread] = None

        self._frame_cb: Optional[Callable] = None
        self._time_cb:  Optional[Callable] = None

        self._project:     Optional[Project] = None
        self._keep_ranges: list[tuple[float, float]] = []

        # pygame audio state
        self._audio_path:    str  = ""
        self._pygame_ready:  bool = False   # True after mixer.init() succeeded
        self._audio_playing: bool = False

    # ── AbstractVideoPlayer ───────────────────────────────────────────────────

    def set_frame_callback(self, cb: Callable) -> None:
        self._frame_cb = cb

    def set_time_callback(self, cb: Callable[[float], None]) -> None:
        self._time_cb = cb

    def load(self, video_path: str) -> None:
        self.close()
        cv2 = self._cv2
        self._cap = cv2.VideoCapture(video_path)
        if not self._cap.isOpened():
            raise RuntimeError(f"OpenCV could not open video: {video_path}")
        self._video_path   = video_path
        self._fps          = self._cap.get(cv2.CAP_PROP_FPS) or 25.0
        total_frames       = self._cap.get(cv2.CAP_PROP_FRAME_COUNT)
        self._duration     = total_frames / self._fps if self._fps > 0 else 0.0
        self._current_time = 0.0
        self._display_frame_at(0.0)

    def seek(self, time_s: float) -> None:
        if self._cap is None:
            return
        was_playing = self._playing
        # Stop audio before seeking so it doesn't play at the old position
        if was_playing:
            self._audio_stop()
        time_s = max(0.0, min(time_s, self._duration))
        self._cap.set(self._cv2.CAP_PROP_POS_MSEC, time_s * 1000.0)
        self._current_time = time_s
        self._display_frame_at(time_s, read_next=True)
        # Resume audio at the new position if we were playing
        if was_playing:
            self._audio_play(time_s)

    def play(self) -> None:
        if self._playing or self._cap is None:
            return
        self._playing     = True
        self._audio_play(self._current_time)
        self._play_thread = threading.Thread(
            target=self._play_loop, daemon=True, name="VideoPlayThread"
        )
        self._play_thread.start()

    def pause(self) -> None:
        self._playing = False
        self._audio_stop()

    def toggle(self) -> None:
        if self._playing:
            self.pause()
        else:
            self.play()

    @property
    def is_playing(self) -> bool:
        return self._playing

    @property
    def current_time(self) -> float:
        return self._current_time

    @property
    def duration(self) -> float:
        return self._duration

    def set_project(self, project: Project) -> None:
        self._project = project
        self._rebuild_keep_ranges()
        # Pick up audio path from project (extracted during transcription)
        ap = getattr(project, "audio_path", "")
        if ap and ap != self._audio_path:
            self._audio_path = ap
            self._pygame_init()

    def close(self) -> None:
        self._playing = False
        self._audio_stop()
        if self._play_thread and self._play_thread.is_alive():
            self._play_thread.join(timeout=1.0)
        if self._cap:
            self._cap.release()
            self._cap = None

    # ── Internal ──────────────────────────────────────────────────────────────

    def _rebuild_keep_ranges(self) -> None:
        if self._project is None:
            self._keep_ranges = [(0.0, self._duration)]
            return
        p = self._project
        self._keep_ranges = get_keep_ranges(
            p.segments,
            set(p.deleted),
            p.silence_settings.buffer,
            p.video_duration,
        ) or [(0.0, self._duration)]

    def _in_keep(self, t: float) -> bool:
        for s, e in self._keep_ranges:
            if s <= t <= e:
                return True
        return False

    def _next_keep_start(self, t: float) -> Optional[float]:
        for s, _ in self._keep_ranges:
            if s > t:
                return s
        return None

    def _play_loop(self) -> None:
        """Decode and deliver frames, capped at MAX_PREVIEW_FPS.

        The cap keeps the Tk canvas paint loop from being overwhelmed by
        high-frame-rate sources (60 fps ProRes, etc.).  When the source
        fps exceeds the cap we skip source frames by seeking forward to
        stay on-time rather than letting the player fall behind.
        """
        cv2 = self._cv2
        # Use source fps for audio/seek math, cap delivery for Tk performance
        preview_fps  = min(self._fps, self.MAX_PREVIEW_FPS)
        preview_dur  = 1.0 / preview_fps
        # How many source frames correspond to one preview frame
        src_stride   = max(1, round(self._fps / preview_fps))

        while self._playing and self._cap is not None:
            loop_start = time.perf_counter()

            with self._lock:
                ret, frame = self._cap.read()
                if not ret:
                    self._playing = False
                    break
                pos_ms = self._cap.get(cv2.CAP_PROP_POS_MSEC)

            t = pos_ms / 1000.0
            self._current_time = t

            # Edit-aware: skip deleted regions
            if self._project is not None and not self._in_keep(t):
                nxt = self._next_keep_start(t)
                if nxt is None:
                    self._playing = False
                    break
                with self._lock:
                    self._cap.set(cv2.CAP_PROP_POS_MSEC, nxt * 1000.0)
                # Seek audio forward too so it doesn't play over the gap
                if self._audio_playing and self._pygame_ready:
                    self._audio_play(nxt)
                continue

            # Deliver frame to UI
            frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            if self._frame_cb:
                self._frame_cb(frame_rgb, t)
            if self._time_cb:
                self._time_cb(t)

            # Skip source frames to match preview rate
            if src_stride > 1:
                with self._lock:
                    target_ms = (t + (src_stride - 1) / self._fps) * 1000.0
                    self._cap.set(cv2.CAP_PROP_POS_MSEC, target_ms)

            # Pace to preview frame rate, dropping sleep if decode was slow
            elapsed = time.perf_counter() - loop_start
            wait    = preview_dur - elapsed
            if wait > 0.002:          # don't bother sleeping less than 2 ms
                time.sleep(wait)

        self._playing = False
        self._audio_playing = False

    def _display_frame_at(self, time_s: float, read_next: bool = False) -> None:
        """Display the frame at *time_s* immediately (seek-preview)."""
        if self._cap is None:
            return
        cv2 = self._cv2

        if not read_next:
            # Peek at the most-recently-seeked frame without advancing
            self._cap.set(cv2.CAP_PROP_POS_MSEC, time_s * 1000.0)

        ret, frame = self._cap.read()
        if ret and self._frame_cb:
            frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            self._frame_cb(frame_rgb, time_s)
        if self._time_cb:
            self._time_cb(time_s)

    # ── pygame audio helpers ──────────────────────────────────────────────────

    def _pygame_init(self) -> None:
        """Initialise pygame.mixer once; silently skip if pygame isn't installed."""
        try:
            import pygame
            if not pygame.mixer.get_init():
                pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=1024)
            self._pygame_ready = True
        except Exception:
            self._pygame_ready = False

    def _audio_play(self, start_s: float) -> None:
        """Start audio playback from *start_s* seconds."""
        if not self._pygame_ready or not self._audio_path:
            return
        try:
            from pathlib import Path as _Path
            if not _Path(self._audio_path).exists():
                return
            import pygame
            pygame.mixer.music.load(self._audio_path)
            pygame.mixer.music.play(start=start_s)
            self._audio_playing = True
        except Exception:
            self._audio_playing = False

    def _audio_stop(self) -> None:
        """Stop audio playback."""
        if not self._pygame_ready:
            return
        try:
            import pygame
            pygame.mixer.music.stop()
        except Exception:
            pass
        self._audio_playing = False
