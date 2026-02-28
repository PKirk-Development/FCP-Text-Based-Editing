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
OpenCVPlayer uses pygame.mixer for audio when pygame is installed.
On load(), ffmpeg extracts the audio track to a temporary WAV file which
pygame.mixer.music streams.  play() / pause() / seek() keep audio and video
in step.  If pygame is absent the player works silently (video only).

Future: replace with AVFoundation via subprocess call to a minimal Swift helper,
or via PyObjC bindings.  See docs/MACOS_ROADMAP.md (to be added).
"""

from __future__ import annotations

import os
import subprocess
import tempfile
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
    OpenCV-based video player with optional pygame.mixer audio.

    Edit-aware playback
    ───────────────────
    When a Project is attached (via set_project), the play loop checks whether
    the current frame falls inside a deleted region and, if so, seeks to the
    start of the next keep range — giving the user a live preview of the edited
    result.  Audio is re-cued to match whenever the video skips.

    Audio
    ─────
    On load(), ffmpeg extracts the audio track to a temp WAV file.
    pygame.mixer.music streams that file during play().  If pygame is not
    installed the player degrades silently to video-only.
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

        # Audio state
        self._mixer     = None          # pygame.mixer module, or None
        self._audio_tmp: Optional[str] = None   # temp WAV path
        self._init_pygame_mixer()

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
        self._extract_audio(video_path)
        self._display_frame_at(0.0)

    def seek(self, time_s: float) -> None:
        if self._cap is None:
            return
        was_playing = self._playing
        time_s = max(0.0, min(time_s, self._duration))
        self._cap.set(self._cv2.CAP_PROP_POS_MSEC, time_s * 1000.0)
        self._current_time = time_s
        self._display_frame_at(time_s, read_next=True)
        # Re-cue audio: restart from the new position so it stays in sync.
        if self._mixer and self._audio_tmp:
            try:
                if was_playing:
                    self._mixer.music.play(start=time_s)
                else:
                    # Load position so that the next play() starts correctly.
                    # pygame.mixer has no "seek without playing"; we play then
                    # immediately pause to land at the right offset.
                    self._mixer.music.play(start=time_s)
                    self._mixer.music.pause()
            except Exception:
                pass

    def play(self) -> None:
        if self._playing or self._cap is None:
            return
        self._playing = True
        if self._mixer and self._audio_tmp:
            try:
                # If paused (e.g. after a seek-while-paused) unpause;
                # otherwise start fresh from current position.
                if self._mixer.music.get_busy():
                    self._mixer.music.unpause()
                else:
                    self._mixer.music.play(start=self._current_time)
            except Exception:
                pass
        self._play_thread = threading.Thread(
            target=self._play_loop, daemon=True, name="VideoPlayThread"
        )
        self._play_thread.start()

    def pause(self) -> None:
        self._playing = False
        if self._mixer:
            try:
                self._mixer.music.pause()
            except Exception:
                pass

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
        if self._mixer:
            try:
                self._mixer.music.stop()
            except Exception:
                pass
        if self._play_thread and self._play_thread.is_alive():
            self._play_thread.join(timeout=1.0)
        if self._cap:
            self._cap.release()
            self._cap = None
        self._delete_audio_tmp()

    # ── Internal ──────────────────────────────────────────────────────────────

    def _init_pygame_mixer(self) -> None:
        """Initialise pygame.mixer; silently skip if pygame is not installed."""
        try:
            import pygame.mixer as mixer
            mixer.init(frequency=44100, size=-16, channels=2, buffer=1024)
            self._mixer = mixer
        except Exception:
            self._mixer = None

    def _extract_audio(self, video_path: str) -> None:
        """
        Extract audio track from *video_path* into a temporary WAV file and
        load it into pygame.mixer.music.  No-op if pygame is unavailable or
        the video has no audio stream.
        """
        if self._mixer is None:
            return

        self._delete_audio_tmp()

        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        tmp.close()
        try:
            result = subprocess.run(
                [
                    "ffmpeg", "-y",
                    "-i", video_path,
                    "-vn",                   # strip video
                    "-acodec", "pcm_s16le",  # 16-bit PCM — pygame.mixer native
                    "-ar", "44100",
                    "-ac", "2",              # stereo
                    tmp.name,
                ],
                capture_output=True,
                timeout=60,
            )
            if result.returncode == 0:
                self._mixer.music.load(tmp.name)
                self._audio_tmp = tmp.name
            else:
                # Video has no audio track or ffmpeg failed — that's fine.
                os.unlink(tmp.name)
        except Exception:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass

    def _delete_audio_tmp(self) -> None:
        if self._audio_tmp:
            try:
                os.unlink(self._audio_tmp)
            except OSError:
                pass
            self._audio_tmp = None

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
                # Re-cue audio to match the video skip
                if self._mixer and self._audio_tmp:
                    try:
                        self._mixer.music.play(start=nxt)
                    except Exception:
                        pass
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

        # Stop audio when the play loop exits naturally (end of clip)
        if self._mixer:
            try:
                self._mixer.music.stop()
            except Exception:
                pass
        self._playing = False

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
