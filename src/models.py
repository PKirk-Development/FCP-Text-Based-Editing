"""
Data models for the FCP Text-Based Editor.

Timeline segments alternate: TextSegment, Silence, TextSegment, Silence, ...
Silence.buffer is stored at the Project level (SilenceSettings) so it can be
changed without re-analysing audio.  The actual "deletable window" of each
silence is computed at export time:
    deletable = [silence.start + buffer,  silence.end - buffer]
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict, field
from typing import Union, Optional


# ── Atomic timeline units ─────────────────────────────────────────────────────

@dataclass
class TextSegment:
    """A word (Whisper) or caption phrase (FCPXML) with source timing."""
    text: str
    start: float   # seconds in source media
    end: float     # seconds in source media

    @property
    def duration(self) -> float:
        return self.end - self.start

    def __repr__(self) -> str:
        return f"Word({self.text!r} {self.start:.3f}-{self.end:.3f})"


@dataclass
class Silence:
    """
    A gap in speech.  `start`/`end` are the FULL bounds of the silent region.
    The buffer is applied only at export time so changing it costs nothing.
    `is_detected` = True  → audio level is below threshold (proper silence)
    `is_detected` = False → gap between Whisper words but audio is not silent
                            (breathing, room tone, fast pause)
    """
    start: float
    end: float
    is_detected: bool = True

    @property
    def duration(self) -> float:
        return self.end - self.start

    def deletable_range(self, buffer: float) -> Optional[tuple[float, float]]:
        """Return (start, end) of the portion that will actually be removed,
        or None if the silence is too short to survive the buffer on both sides."""
        inner_start = self.start + buffer
        inner_end   = self.end   - buffer
        if inner_end > inner_start + 0.001:   # must be ≥ 1 ms after buffer
            return (round(inner_start, 4), round(inner_end, 4))
        return None

    def __repr__(self) -> str:
        kind = "SIL" if self.is_detected else "GAP"
        return f"{kind}({self.start:.3f}-{self.end:.3f})"


# Union type used everywhere
Segment = Union[TextSegment, Silence]


# ── Settings ──────────────────────────────────────────────────────────────────

@dataclass
class SilenceSettings:
    """Silence-detection parameters (all editable live in the TUI)."""
    threshold_db: float = -40.0   # dBFS – audio below this is "silent"
    min_duration: float = 0.300   # seconds – minimum gap to flag as silence
    buffer: float       = 0.050   # seconds – kept at each edge of deleted silence
                                   # min 0.001 s (1 ms), beats Premiere's 0.1 s

    def __post_init__(self):
        self.buffer = max(0.001, round(self.buffer, 4))   # floor at 1 ms

    @property
    def buffer_ms(self) -> float:
        return self.buffer * 1000


# ── Project (serialisable to JSON) ───────────────────────────────────────────

@dataclass
class Project:
    """
    Complete editing session.

    Saved to <basename>.fte.json alongside the source file so you can resume.
    """
    video_path: str            # absolute path to source video
    audio_path: str            # absolute path to extracted 16 kHz mono WAV
    segments: list[Segment]    # ordered timeline: TextSegment | Silence
    deleted: list[int]         # segment indices the user has marked for removal
    silence_settings: SilenceSettings
    video_duration: float      # total duration of source video in seconds
    video_fps: float    = 25.0
    video_width: int    = 1920
    video_height: int   = 1080
    # FCPXML round-trip fields
    source_fcpxml: Optional[str]  = None   # path to the input .fcpxml (if any)
    fcpxml_version: str           = "1.11"
    fcpxml_asset_id: str          = "r2"
    fcpxml_format_id: str         = "r1"

    # ── Serialisation ────────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        return {
            "video_path":       self.video_path,
            "audio_path":       self.audio_path,
            "video_duration":   self.video_duration,
            "video_fps":        self.video_fps,
            "video_width":      self.video_width,
            "video_height":     self.video_height,
            "source_fcpxml":    self.source_fcpxml,
            "fcpxml_version":   self.fcpxml_version,
            "fcpxml_asset_id":  self.fcpxml_asset_id,
            "fcpxml_format_id": self.fcpxml_format_id,
            "deleted":          self.deleted,
            "silence_settings": asdict(self.silence_settings),
            "segments": [
                {"type": "text",
                 "text":  s.text,
                 "start": s.start,
                 "end":   s.end}
                if isinstance(s, TextSegment)
                else
                {"type":        "silence",
                 "start":       s.start,
                 "end":         s.end,
                 "is_detected": s.is_detected}
                for s in self.segments
            ],
        }

    def save(self, path: str) -> None:
        with open(path, "w") as fh:
            json.dump(self.to_dict(), fh, indent=2)

    @classmethod
    def load(cls, path: str) -> "Project":
        with open(path) as fh:
            data = json.load(fh)

        segments: list[Segment] = []
        for s in data["segments"]:
            if s["type"] == "text":
                segments.append(TextSegment(s["text"], s["start"], s["end"]))
            else:
                segments.append(
                    Silence(s["start"], s["end"], s.get("is_detected", True))
                )

        return cls(
            video_path        = data["video_path"],
            audio_path        = data["audio_path"],
            segments          = segments,
            deleted           = data["deleted"],
            silence_settings  = SilenceSettings(**data["silence_settings"]),
            video_duration    = data["video_duration"],
            video_fps         = data.get("video_fps", 25.0),
            video_width       = data.get("video_width", 1920),
            video_height      = data.get("video_height", 1080),
            source_fcpxml     = data.get("source_fcpxml"),
            fcpxml_version    = data.get("fcpxml_version",   "1.11"),
            fcpxml_asset_id   = data.get("fcpxml_asset_id",  "r2"),
            fcpxml_format_id  = data.get("fcpxml_format_id", "r1"),
        )

    # ── Convenience helpers ──────────────────────────────────────────────────

    def time_saved(self) -> float:
        """Total seconds that will be cut from the final export."""
        buf = self.silence_settings.buffer
        total = 0.0
        for idx in self.deleted:
            seg = self.segments[idx]
            if isinstance(seg, Silence):
                r = seg.deletable_range(buf)
                if r:
                    total += r[1] - r[0]
            else:
                total += seg.duration
        return total

    def deleted_count(self) -> int:
        return len(self.deleted)
