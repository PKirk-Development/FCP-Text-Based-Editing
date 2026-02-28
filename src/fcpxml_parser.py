"""
FCPXML parser for FCP 11's "Transcribe to Captions" workflow.

Supports FCPXML versions 1.8 – 1.14 (all versions exported by FCP 10.6–10.8+).

Workflow
--------
1. In FCP 11: Clip → Transcribe to Captions  (requires Apple Silicon + macOS Sequoia)
2. File → Export XML…  (choose FCPXML 1.11, 1.12, 1.13, or 1.14 — all work)
3. Open the .fcpxml file in FCP Text Editor

The parser extracts:
  • The source video asset path
  • Caption elements (iTT / SRT / CEA-608) → TextSegment list
  • Video format info (fps, width, height, duration)

Time representation
-------------------
FCPXML times are rational strings like "1001/30000s" or "5s".
parse_time()  converts them to float seconds.
to_fcpxml_time() converts float seconds back to "value/44100s" strings
(44100 is the LCM-friendly audio timescale that gives ~22 µs precision).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional
from urllib.parse import unquote, urlparse
from xml.etree import ElementTree as ET

from .models import TextSegment


# ── Time helpers ──────────────────────────────────────────────────────────────

def parse_time(time_str: str) -> float:
    """Convert FCPXML time string → float seconds."""
    if not time_str or time_str in ("0s", "0"):
        return 0.0
    s = time_str.rstrip("s")
    if "/" in s:
        num, den = s.split("/", 1)
        return float(num) / float(den)
    return float(s)


def to_fcpxml_time(seconds: float, timescale: int = 44100) -> str:
    """Convert float seconds → FCPXML rational time string."""
    if seconds == 0.0:
        return "0s"
    value = round(seconds * timescale)
    return f"{value}/{timescale}s"


def _asset_path_from_url(url: str) -> str:
    """Convert a file:// URL from FCPXML to an absolute filesystem path."""
    if url.startswith("file://"):
        parsed = urlparse(url)
        return unquote(parsed.path)
    return url


# ── FCPXML namespace handling ─────────────────────────────────────────────────

# FCP uses no namespace prefix in modern FCPXML, but older files may have one.
_NS_RE = re.compile(r"\{[^}]*\}")


def _tag(element: ET.Element) -> str:
    """Strip namespace prefix from element tag."""
    return _NS_RE.sub("", element.tag)


def _find_all(root: ET.Element, tag: str) -> list[ET.Element]:
    """Recursively find all elements with the given (unprefixed) tag."""
    results = []
    if _tag(root) == tag:
        results.append(root)
    for child in root:
        results.extend(_find_all(child, tag))
    return results


def _find(root: ET.Element, tag: str) -> Optional[ET.Element]:
    hits = _find_all(root, tag)
    return hits[0] if hits else None


# ── Main parser class ─────────────────────────────────────────────────────────

class FCPXMLProject:
    """
    Parsed representation of a .fcpxml file.

    Attributes
    ----------
    video_path      : Absolute path to the source video asset
    duration        : Total duration of the primary clip (seconds)
    fps             : Frame rate of the source video
    width / height  : Pixel dimensions
    captions        : List of TextSegment extracted from <caption> elements
    asset_id        : <asset> id attribute (needed for round-trip export)
    format_id       : <format> id attribute
    fcpxml_version  : Version string from <fcpxml> root
    raw_tree        : The full ElementTree (kept for round-trip export)
    """

    def __init__(self, fcpxml_path: str) -> None:
        self.fcpxml_path    = fcpxml_path
        self.video_path     = ""
        self.duration       = 0.0
        self.fps            = 25.0
        self.width          = 1920
        self.height         = 1080
        self.captions:  list[TextSegment] = []
        self.asset_id       = "r2"
        self.format_id      = "r1"
        self.fcpxml_version = "1.11"
        self.raw_tree: Optional[ET.ElementTree] = None

        self._parse(fcpxml_path)

    # ── Internal parsing ──────────────────────────────────────────────────────

    def _parse(self, path: str) -> None:
        tree = ET.parse(path)
        self.raw_tree = tree
        root = tree.getroot()

        self.fcpxml_version = root.get("version", "1.11")

        # ── Resources ─────────────────────────────────────────────────────────
        resources = _find(root, "resources")
        if resources is None:
            raise ValueError("FCPXML has no <resources> element.")

        # Find primary video asset
        for asset in _find_all(resources, "asset"):
            if asset.get("hasVideo", "0") == "1" or asset.get("hasAudio", "1") == "1":
                self.asset_id = asset.get("id", "r2")
                self._parse_asset(asset)
                break   # take the first video asset

        # Find format (for fps / dimensions)
        for fmt in _find_all(resources, "format"):
            fid = fmt.get("id", "")
            if fid == self.format_id or not self.format_id:
                self.format_id = fid
                self._parse_format(fmt)
                break

        # ── Sequence / spine ──────────────────────────────────────────────────
        sequence = _find(root, "sequence")
        if sequence is not None:
            dur_str = sequence.get("duration", "0s")
            self.duration = parse_time(dur_str)

        # ── Captions ──────────────────────────────────────────────────────────
        # FCP 11 "Transcribe to Captions" creates <caption> elements inside the
        # primary <clip> in the spine.  Each caption has:
        #   offset   – start time relative to the parent clip's start
        #   duration – how long the caption is shown
        #   <text>   – the transcribed text
        self.captions = self._extract_captions(root)

    def _parse_asset(self, asset: ET.Element) -> None:
        """Extract video file path and duration from an <asset> element."""
        # Duration
        dur_str = asset.get("duration", "0s")
        self.duration = parse_time(dur_str)

        # Path: stored in a child <media-rep> or as the src attribute directly
        media_rep = _find(asset, "media-rep")
        if media_rep is not None:
            src = media_rep.get("src", "")
        else:
            src = asset.get("src", "")

        if src:
            self.video_path = _asset_path_from_url(src)

    def _parse_format(self, fmt: ET.Element) -> None:
        """Extract fps / width / height from a <format> element."""
        self.width  = int(fmt.get("width",  1920))
        self.height = int(fmt.get("height", 1080))

        fd = fmt.get("frameDuration", "")
        if fd:
            # frameDuration is the duration of ONE frame, e.g. "1001/30000s"
            frame_dur = parse_time(fd)
            if frame_dur > 0:
                self.fps = round(1.0 / frame_dur, 6)

    def _extract_captions(self, root: ET.Element) -> list[TextSegment]:
        """
        Walk the entire element tree and collect all <caption> elements.

        The timing model:
          caption_start_s = clip_start_in_sequence + caption.offset
          (For a simple single-clip project, clip_start_in_sequence ≈ 0.)
        """
        all_captions = _find_all(root, "caption")
        segments: list[TextSegment] = []

        for cap in all_captions:
            offset_s   = parse_time(cap.get("offset",   "0s"))
            duration_s = parse_time(cap.get("duration", "0s"))

            if duration_s <= 0:
                continue

            # Text comes from child <text> element(s).
            # In FCPXML 1.12+ FCP wraps the actual string in <text-style>
            # children, so text_el.text is None.  itertext() collects all
            # text nodes at any depth, handling both old and new layouts.
            texts = []
            for text_el in _find_all(cap, "text"):
                t = "".join(text_el.itertext()).strip()
                if t:
                    texts.append(t)
            if not texts:
                # Fall back to the name attribute (always populated by FCP)
                name = cap.get("name", "").strip()
                if name:
                    texts = [name]

            if not texts:
                continue

            combined = " ".join(texts)
            segments.append(
                TextSegment(
                    text  = combined,
                    start = round(offset_s, 4),
                    end   = round(offset_s + duration_s, 4),
                )
            )

        # Sort by start time (FCP should already order them, but be defensive)
        segments.sort(key=lambda s: s.start)
        return segments

    # ── Public helpers ────────────────────────────────────────────────────────

    def has_captions(self) -> bool:
        return len(self.captions) > 0

    def summary(self) -> str:
        lines = [
            f"FCPXML v{self.fcpxml_version}",
            f"Video : {self.video_path or '(not found)'}",
            f"Size  : {self.width}×{self.height} @ {self.fps:.3f} fps",
            f"Length: {self.duration:.3f} s",
            f"Captions: {len(self.captions)}",
        ]
        return "\n".join(lines)
