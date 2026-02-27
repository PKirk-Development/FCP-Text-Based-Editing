"""
Timeline builder: merges text segments (words / phrases) with silence regions
into a single ordered list of  TextSegment | Silence.

Algorithm
---------
1. Sort text segments by start time.
2. For each gap between consecutive text segments (and at the head/tail of the
   clip), create a Silence segment.
3. Classify each silence:
     is_detected = True   if it overlaps a pydub-detected silence region
     is_detected = False  if it is just a Whisper word gap (breathing room, etc.)
4. Silences shorter than a small tolerance (TINY_GAP_S) that are not detected
   are kept as Silence(is_detected=False) so the user can still see and delete
   them if desired.

The resulting list is what the editor displays and the exporter consumes.
"""

from __future__ import annotations

from typing import Optional

from .models import Segment, Silence, TextSegment, SilenceSettings

# Gaps shorter than this (seconds) between consecutive Whisper words are
# collapsed into the preceding word's end time to avoid a proliferation of
# micro-silence widgets.
WORD_GAP_COLLAPSE_S = 0.010  # 10 ms


def build_timeline(
    text_segments: list[TextSegment],
    detected_silences: list[Silence],
    video_duration: float,
    settings: SilenceSettings,
) -> list[Segment]:
    """
    Build the full timeline from speech segments and detected silences.

    Parameters
    ----------
    text_segments      : Words (Whisper) or phrases (FCPXML captions).
    detected_silences  : Output of audio.detect_silences() — full bounds.
    video_duration     : Total source length in seconds.
    settings           : Used to decide which gaps are "long enough" to show.

    Returns
    -------
    Ordered list of alternating TextSegment / Silence, covering [0, duration].
    """
    if not text_segments:
        # No transcript at all — wrap the whole clip as one silence
        return [Silence(start=0.0, end=round(video_duration, 4), is_detected=False)]

    segments: list[Segment] = []
    words = sorted(text_segments, key=lambda w: w.start)

    # Build an interval tree-like structure from detected silences for O(n)
    # overlap checks (just a sorted list; we walk both in order).
    det_sil = sorted(detected_silences, key=lambda s: s.start)
    det_idx = 0  # pointer into det_sil

    def is_detected(gap_start: float, gap_end: float) -> bool:
        """Does any detected silence overlap [gap_start, gap_end] by ≥ 1 ms?"""
        nonlocal det_idx
        # Advance pointer past silences that end before the gap
        while det_idx < len(det_sil) and det_sil[det_idx].end < gap_start:
            det_idx += 1
        for s in det_sil[det_idx:]:
            if s.start > gap_end:
                break
            overlap = min(s.end, gap_end) - max(s.start, gap_start)
            if overlap >= 0.001:
                return True
        return False

    # ── Leading silence (before first word) ──────────────────────────────────
    if words[0].start > WORD_GAP_COLLAPSE_S:
        gap_start = 0.0
        gap_end   = round(words[0].start, 4)
        segments.append(
            Silence(gap_start, gap_end, is_detected=is_detected(gap_start, gap_end))
        )
    det_idx = 0  # reset for main pass

    # ── Words and inter-word gaps ─────────────────────────────────────────────
    for i, word in enumerate(words):
        segments.append(word)

        if i < len(words) - 1:
            next_word = words[i + 1]
            gap_start = round(word.end, 4)
            gap_end   = round(next_word.start, 4)
            gap_dur   = gap_end - gap_start

            if gap_dur > WORD_GAP_COLLAPSE_S:
                # Check whether pydub flagged this gap as silent
                detected = is_detected(gap_start, gap_end)
                segments.append(
                    Silence(gap_start, gap_end, is_detected=detected)
                )
            elif gap_dur > 0:
                # Micro-gap: extend previous word to close the seam
                # (avoids dozens of invisible silence widgets)
                last = segments[-2]   # the word we just appended
                if isinstance(last, TextSegment):
                    # Snap word end to next word start
                    segments[-2] = TextSegment(
                        last.text,
                        last.start,
                        round(next_word.start, 4),
                    )

    # ── Trailing silence (after last word) ────────────────────────────────────
    last_word_end = round(words[-1].end, 4)
    if video_duration - last_word_end > WORD_GAP_COLLAPSE_S:
        gap_start = last_word_end
        gap_end   = round(video_duration, 4)
        segments.append(
            Silence(gap_start, gap_end, is_detected=is_detected(gap_start, gap_end))
        )

    return segments


def get_keep_ranges(
    segments: list[Segment],
    deleted_indices: set[int],
    buffer: float,
    total_duration: float,
) -> list[tuple[float, float]]:
    """
    Compute the list of (start, end) time ranges to keep in the final export.

    For deleted Silence segments the buffer is respected:
        deletable = [silence.start + buffer,  silence.end - buffer]
    Only the deletable window is removed; the buffer edges remain in the
    adjacent keep ranges automatically (they're simply not removed).

    For deleted TextSegment the entire segment is cut.

    The result is a list of non-overlapping ranges in source-media time,
    suitable for passing to the FFmpeg or FCPXML exporter.
    """
    # Build the set of (start, end) intervals to *delete*
    deleted_intervals: list[tuple[float, float]] = []

    for idx in sorted(deleted_indices):
        seg = segments[idx]
        if isinstance(seg, Silence):
            r = seg.deletable_range(buffer)
            if r:
                deleted_intervals.append(r)
        else:
            # Delete the full TextSegment
            deleted_intervals.append((seg.start, seg.end))

    if not deleted_intervals:
        return [(0.0, total_duration)]

    # Sort and merge overlapping delete intervals
    deleted_intervals.sort(key=lambda x: x[0])
    merged_del: list[tuple[float, float]] = []
    for start, end in deleted_intervals:
        if merged_del and start <= merged_del[-1][1]:
            merged_del[-1] = (merged_del[-1][0], max(merged_del[-1][1], end))
        else:
            merged_del.append((start, end))

    # Keep ranges = complement of deleted ranges in [0, total_duration]
    keep: list[tuple[float, float]] = []
    cursor = 0.0

    for del_start, del_end in merged_del:
        del_start = round(del_start, 4)
        del_end   = round(del_end,   4)
        if del_start > cursor + 0.001:
            keep.append((cursor, del_start))
        cursor = del_end

    if cursor < total_duration - 0.001:
        keep.append((cursor, round(total_duration, 4)))

    return keep
