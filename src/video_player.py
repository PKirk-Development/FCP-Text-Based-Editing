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

Audio playback
──────────────
OpenCVPlayer drives audio via macOS's built-in `afplay` utility
(subprocess).  afplay decodes the source video's audio track through
Core Audio — no extraction step, no SDL2, no extra dependencies.
Seeking is handled via afplay's -s flag; the process is killed and
restarted whenever play/pause/seek changes position.

On non-macOS platforms (Linux dev) the player runs silently (video only).

Future: replace with AVFoundation via subprocess call to a minimal Swift helper,
or via PyObjC bindings.  See docs/MACOS_ROADMAP.md (to be added).
"""

from __future__ import annotations

import subprocess
import sys
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
    OpenCV-based video player with afplay audio (macOS).

    Edit-aware playback
    ───────────────────
    When a Project is attached (via set_project), the play loop checks whether
    the current frame falls inside a deleted region and, if so, seeks to the
    start of the next keep range — giving the user a live preview of the edited
    result.  The afplay process is killed and restarted to match whenever the
    video skips.

    Audio
    ─────
    afplay is a macOS CLI tool that plays audio/video files through Core Audio.
    We run it as a subprocess with -s <startTime> so it begins at the right
    offset.  Pause = kill the process; resume = restart from _current_time.
    On non-macOS the player degrades to video-only with no error.
    """

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

        self._project:    Optional[Project] = None
        self._keep_ranges: list[tuple[float, float]] = []

        # Audio: afplay subprocess or None
        self._audio_proc: Optional[subprocess.Popen] = None

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
        time_s = max(0.0, min(time_s, self._duration))
        self._cap.set(self._cv2.CAP_PROP_POS_MSEC, time_s * 1000.0)
        self._current_time = time_s
        self._display_frame_at(time_s, read_next=True)
        if was_playing:
            # Restart audio from the new position
            self._start_audio(time_s)

    def play(self) -> None:
        if self._playing or self._cap is None:
            return
        self._playing = True
        self._start_audio(self._current_time)
        self._play_thread = threading.Thread(
            target=self._play_loop, daemon=True, name="VideoPlayThread"
        )
        self._play_thread.start()

    def pause(self) -> None:
        self._playing = False
        self._stop_audio()

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

    def close(self) -> None:
        self._playing = False
        self._stop_audio()
        if self._play_thread and self._play_thread.is_alive():
            self._play_thread.join(timeout=1.0)
        if self._cap:
            self._cap.release()
            self._cap = None

    # ── Audio ─────────────────────────────────────────────────────────────────

    def _start_audio(self, start_s: float) -> None:
        """Kill any running afplay and start a new one from *start_s* seconds."""
        self._stop_audio()
        if not self._video_path:
            return
        try:
            self._audio_proc = subprocess.Popen(
                ["afplay", "-s", f"{start_s:.3f}", self._video_path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except FileNotFoundError:
            pass  # afplay not available (non-macOS); video-only fallback
        except Exception as e:
            print(f"[audio] afplay error: {e}", file=sys.stderr)

    def _stop_audio(self) -> None:
        """Terminate the afplay subprocess if it is running."""
        if self._audio_proc is not None:
            try:
                self._audio_proc.terminate()
                self._audio_proc.wait(timeout=0.5)
            except Exception:
                pass
            self._audio_proc = None

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
        cv2 = self._cv2
        frame_dur = 1.0 / self._fps

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
                self._start_audio(nxt)
                continue

            # Deliver frame to UI
            frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            if self._frame_cb:
                self._frame_cb(frame_rgb, t)
            if self._time_cb:
                self._time_cb(t)

            # Pace to target frame rate
            elapsed = time.perf_counter() - loop_start
            wait    = frame_dur - elapsed
            if wait > 0:
                time.sleep(wait)

        self._stop_audio()
        self._playing = False

    def _display_frame_at(self, time_s: float, read_next: bool = False) -> None:
        """Display the frame at *time_s* immediately (seek-preview)."""
        if self._cap is None:
            return
        cv2 = self._cv2

        if not read_next:
            self._cap.set(cv2.CAP_PROP_POS_MSEC, time_s * 1000.0)

        ret, frame = self._cap.read()
        if ret and self._frame_cb:
            frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            self._frame_cb(frame_rgb, time_s)
        if self._time_cb:
            self._time_cb(time_s)
