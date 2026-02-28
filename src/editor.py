"""
FCP Text-Based Editor — Full 3-panel desktop GUI.

Window layout
─────────────
┌─ FCP Text-Based Editor · video.mp4 · 02:34 ────────────────────────────────┐
│  Threshold [-40.0] dB  Buffer [0.050] s  Min [0.300] s                     │
│  [Auto-Delete Silence]  [Re-analyse]  [Restore All]  [Undo]  [Redo]        │
│  [✓ Um-Checker]                                                              │
├──────────────────────────────────┬──────────────────────────────────────────┤
│  TRANSCRIPT                      │  PREVIEW                                 │
│  ─────────────────────────────── │  ───────────────────────────────────── │
│  Hello there, this is            │                                          │
│  [■ 0.85s] a test of the         │     ┌────────────────────────────┐      │
│  text-based editing tool.        │     │                            │      │
│  You can [■ 1.23s] highlight     │     │       video frame          │      │
│  words and silence blocks        │     │                            │      │
│  [■ 0.45s] and delete them.      │     └────────────────────────────┘      │
│                                  │  ⏮ ◀◀  ▶  ▶▶ ⏭   0:02.5 / 2:34       │
├──────────────────────────────────┴──────────────────────────────────────────┤
│  TIMELINE  ██████████████████▓▓░░░░░░▓▓███████████░░░░░░░███████████████   │
│            0:00          0:30          1:00          1:30          2:00     │
├─────────────────────────────────────────────────────────────────────────────┤
│  Segments: 142  Deleted: 3  Time saved: 2.530 s  Selected: 2   [Export ▸]  │
└─────────────────────────────────────────────────────────────────────────────┘

Text editing feel (same as Word / Google Docs)
───────────────────────────────────────────────
• Click           → place cursor / select segment
• Shift+Click     → extend selection
• Click+Drag      → select range
• Double-click    → select single segment
• Triple-click    → select all segments in paragraph
• Ctrl+A          → select all segments
• Arrows / Home / End / PgUp / PgDn → move cursor (native)
• Shift+Arrows    → extend selection (native)
• Delete/BackSpace → cut selected video segments
• U               → restore (un-delete) selection
• Escape          → clear selection
• Space / K       → toggle play / pause
• J               → seek back 5 s    L → seek forward 5 s
• Ctrl+Z / Ctrl+Y → undo / redo
• E               → open export dialog

Colour coding
─────────────
Word (normal)            #eeeeff on #0a0a12
Word (selected)          white bold on #1a3a5c
Word (deleted)           #cc3333 + strikethrough
Silence / long detected  #00cccc on #000018
Silence / short gap      #444466 on #030308
Filler word highlight    #cc8800 on #1a1200   (Um-Checker)
"""

from __future__ import annotations

import threading
from pathlib import Path
from tkinter import filedialog, messagebox
from typing import Optional, Union

import customtkinter as ctk
import tkinter as tk

from .models import Project, Silence, SilenceSettings, TextSegment
from .timeline import get_keep_ranges

# ── Type alias ────────────────────────────────────────────────────────────────
Segment = Union[TextSegment, Silence]

# ── Filler word sets ──────────────────────────────────────────────────────────
_UM_HARD = frozenset(["um", "uh", "uhh", "umm", "hmm", "hm", "uh-huh", "mm"])
_UM_SOFT = frozenset([
    "like", "basically", "literally", "actually", "right",
    "okay", "so", "well", "just", "you know",
])

# ── Style tag names on the tk.Text widget ─────────────────────────────────────
# Each segment i gets a positional tag "seg_i".
# One style tag is applied per segment based on (type, deleted, selected):
_STYLE_TAGS = [
    "t_normal",  "t_sel",  "t_del",  "t_del_sel",   # TextSegment
    "sl_normal", "sl_sel", "sl_del", "sl_del_sel",   # Silence (detected, long)
    "sg_normal", "sg_sel", "sg_del", "sg_del_sel",   # Silence (gap / short)
    "um_hl",     "um_soft_hl",                        # Um-Checker highlights
]


# ── Numeric spinbox widget ────────────────────────────────────────────────────

class _SpinEntry(ctk.CTkFrame):
    """Label + entry field + ▲▼ increment buttons for numeric settings."""

    def __init__(self, parent, label: str, value: float, step: float,
                 fmt: str = ".3f", on_change=None, **kwargs):
        super().__init__(parent, fg_color="transparent", **kwargs)
        self._value    = value
        self._step     = step
        self._fmt      = fmt
        self._callback = on_change

        ctk.CTkLabel(self, text=label, width=80, anchor="e").pack(side="left")
        self._entry = ctk.CTkEntry(self, width=80, justify="center")
        self._entry.insert(0, format(value, fmt))
        self._entry.pack(side="left", padx=(2, 0))
        self._entry.bind("<Return>",   self._on_commit)
        self._entry.bind("<FocusOut>", self._on_commit)

        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(side="left")
        ctk.CTkButton(btn_frame, text="▲", width=22, height=14,
                      command=self._inc).pack()
        ctk.CTkButton(btn_frame, text="▼", width=22, height=14,
                      command=self._dec).pack()

    def _inc(self):
        self._value += self._step
        self._sync()

    def _dec(self):
        self._value -= self._step
        self._sync()

    def _on_commit(self, _=None):
        try:
            self._value = float(self._entry.get())
        except ValueError:
            pass
        self._sync()

    def _sync(self):
        self._entry.delete(0, "end")
        self._entry.insert(0, format(self._value, self._fmt))
        if self._callback:
            self._callback(self._value)

    def get(self) -> float:
        try:
            return float(self._entry.get())
        except ValueError:
            return self._value

    def set(self, v: float):
        self._value = v
        self._entry.delete(0, "end")
        self._entry.insert(0, format(v, self._fmt))


# ── Main editor window ────────────────────────────────────────────────────────

class TextEditor(ctk.CTk):
    """
    Full-featured 3-panel desktop GUI.

    Parameters
    ----------
    project : Loaded / freshly built :class:`Project` instance.
    """

    def __init__(self, project: Project) -> None:
        super().__init__()

        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self.project   = project
        self.deleted:  set[int] = set(project.deleted)
        self.selected: set[int] = set()
        self._anchor:  Optional[int] = None

        self._undo_stack: list[frozenset[int]] = []
        self._redo_stack: list[frozenset[int]] = []

        # Video player (OpenCVPlayer, loaded async)
        self._player                 = None
        self._photo_image            = None   # hold PIL reference to prevent GC

        # Waveform
        self._waveform_data          = None
        self._waveform_view          = None

        # Um-Checker highlight state
        self._um_hard_indices: list[int] = []
        self._um_soft_indices: list[int] = []

        # Window title
        src     = Path(project.video_path).name
        dur     = project.video_duration
        h, rem  = divmod(int(dur), 3600)
        mm, ss  = divmod(rem, 60)
        dur_str = f"{h:02d}:{mm:02d}:{ss:02d}" if h else f"{mm:02d}:{ss:02d}"
        self.title(f"FCP Text-Based Editor  ·  {src}  ·  {dur_str}")
        self.geometry("1440x900")
        self.minsize(1000, 650)
        self.configure(fg_color="#0a0a12")

        self._build_ui()
        self._setup_text_tags()
        self._populate_transcript()
        self._update_status()

        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # Start loading video + waveform in the background
        self.after(200, self._load_video_async)

    # ── Layout construction ───────────────────────────────────────────────────

    def _build_ui(self) -> None:
        s = self.project.silence_settings

        # ── Toolbar ───────────────────────────────────────────────────────────
        top = ctk.CTkFrame(self, fg_color="#0d0d1f", corner_radius=0)
        top.pack(fill="x")

        spin_row = ctk.CTkFrame(top, fg_color="transparent")
        spin_row.pack(fill="x", padx=12, pady=(6, 2))

        self._thresh_spin = _SpinEntry(
            spin_row, "Threshold:", s.threshold_db, -1.0, ".1f",
            on_change=self._on_setting_change,
        )
        self._thresh_spin.pack(side="left", padx=(0, 4))
        ctk.CTkLabel(spin_row, text="dB",
                     text_color="#7777aa").pack(side="left", padx=(0, 18))

        self._buffer_spin = _SpinEntry(
            spin_row, "Buffer:", s.buffer, 0.001, ".3f",
            on_change=self._on_setting_change,
        )
        self._buffer_spin.pack(side="left", padx=(0, 4))
        ctk.CTkLabel(spin_row, text="s  (min 1 ms)",
                     text_color="#7777aa").pack(side="left", padx=(0, 18))

        self._min_spin = _SpinEntry(
            spin_row, "Min silence:", s.min_duration, 0.010, ".3f",
            on_change=self._on_setting_change,
        )
        self._min_spin.pack(side="left", padx=(0, 4))
        ctk.CTkLabel(spin_row, text="s",
                     text_color="#7777aa").pack(side="left")

        btn_row = ctk.CTkFrame(top, fg_color="transparent")
        btn_row.pack(fill="x", padx=12, pady=(2, 8))

        _B = dict(height=28, corner_radius=6, fg_color="#1a1a3a",
                  hover_color="#2a2a5a", border_color="#3333aa", border_width=1)

        ctk.CTkButton(btn_row, text="Auto-Delete Silence",
                      command=self._auto_delete, **_B).pack(side="left", padx=(0, 4))
        ctk.CTkButton(btn_row, text="Re-analyse",
                      command=self._reanalyse, **_B).pack(side="left", padx=4)
        ctk.CTkButton(btn_row, text="Restore All",
                      command=self._restore_all, **_B).pack(side="left", padx=4)
        ctk.CTkButton(btn_row, text="Undo",
                      command=self._undo, **_B).pack(side="left", padx=4)
        ctk.CTkButton(btn_row, text="Redo",
                      command=self._redo, **_B).pack(side="left", padx=4)

        # Um-Checker gets its own colour to stand out
        ctk.CTkButton(
            btn_row, text="✓ Um-Checker", command=self._um_checker,
            height=28, corner_radius=6,
            fg_color="#2a1800", hover_color="#4a2800",
            border_color="#cc8800", border_width=1,
        ).pack(side="left", padx=(12, 4))

        # ── Status bar (pack side=bottom first so timeline sits above it) ─────
        status_frame = ctk.CTkFrame(self, fg_color="#0d0d1f",
                                    corner_radius=0, height=36)
        status_frame.pack(fill="x", side="bottom")
        status_frame.pack_propagate(False)

        self._status_var = tk.StringVar(value="Loading…")
        ctk.CTkLabel(
            status_frame, textvariable=self._status_var,
            text_color="#7777aa", anchor="w",
        ).pack(side="left", padx=14, fill="y")

        ctk.CTkButton(
            status_frame, text="Export ▸", width=100, height=26,
            fg_color="#1a3a1a", hover_color="#2a5a2a",
            border_color="#33aa33", border_width=1,
            command=self._export_dialog,
        ).pack(side="right", padx=10, pady=4)

        # ── Timeline waveform (pack side=bottom, above status bar) ────────────
        tl_outer = ctk.CTkFrame(self, fg_color="#050508", corner_radius=0)
        tl_outer.pack(fill="x", side="bottom")

        tl_header = ctk.CTkFrame(tl_outer, fg_color="#050508", height=18)
        tl_header.pack(fill="x")
        tl_header.pack_propagate(False)
        ctk.CTkLabel(
            tl_header, text="TIMELINE", fg_color="#050508",
            text_color="#1e4433", font=("Arial", 9, "bold"), anchor="w",
        ).pack(side="left", padx=10)

        self._timeline_canvas = tk.Canvas(
            tl_outer, bg="#050508",
            highlightthickness=0, bd=0, height=90,
        )
        self._timeline_canvas.pack(fill="x")

        # ── Middle content area: left transcript + right video ────────────────
        content = tk.Frame(self, bg="#0a0a12")
        content.pack(fill="both", expand=True)

        paned = tk.PanedWindow(
            content, orient="horizontal",
            bg="#1a1a2a", sashrelief="flat", sashwidth=5, handlesize=0,
        )
        paned.pack(fill="both", expand=True)

        # ── LEFT: transcript ──────────────────────────────────────────────────
        left_frame = tk.Frame(paned, bg="#0a0a12")

        lbl = tk.Frame(left_frame, bg="#0d0d1f", height=22)
        lbl.pack(fill="x")
        lbl.pack_propagate(False)
        tk.Label(
            lbl, text="TRANSCRIPT  — click to select  ·  Delete to cut  ·  U to restore",
            bg="#0d0d1f", fg="#445588",
            font=("Arial", 9), anchor="w", padx=10,
        ).pack(fill="both")

        txt_frame = tk.Frame(left_frame, bg="#0a0a12")
        txt_frame.pack(fill="both", expand=True)

        self._text = tk.Text(
            txt_frame,
            bg            = "#0a0a12",
            fg            = "#eeeeff",
            insertbackground  = "#6666cc",    # cursor colour
            selectbackground  = "#0a0a12",    # hide native blue sel box (we use our tags)
            selectforeground  = "#eeeeff",
            font          = ("Georgia", 15),
            wrap          = "word",
            padx          = 22,
            pady          = 16,
            relief        = "flat",
            cursor        = "ibeam",
            state         = "normal",
            bd            = 0,
            highlightthickness = 0,
            spacing1      = 1,
            spacing3      = 6,
        )
        _sb = ctk.CTkScrollbar(txt_frame, command=self._text.yview)
        self._text.configure(yscrollcommand=_sb.set)
        _sb.pack(side="right", fill="y")
        self._text.pack(side="left", fill="both", expand=True)

        # Text widget bindings — gives Word-doc feel
        self._text.bind("<ButtonPress-1>",       self._t_click)
        self._text.bind("<B1-Motion>",           self._t_drag)
        self._text.bind("<ButtonRelease-1>",     self._t_up)
        self._text.bind("<Shift-ButtonPress-1>", self._t_shift_click)
        self._text.bind("<Double-ButtonPress-1>",self._t_double_click)
        self._text.bind("<Triple-ButtonPress-1>",self._t_triple_click)
        self._text.bind("<Key>",                 self._t_key)

        paned.add(left_frame, minsize=280, width=560, stretch="always")

        # ── RIGHT: video preview + transport controls ─────────────────────────
        right_frame = tk.Frame(paned, bg="#070710")

        vid_lbl = tk.Frame(right_frame, bg="#0d0d1f", height=22)
        vid_lbl.pack(fill="x")
        vid_lbl.pack_propagate(False)
        tk.Label(
            vid_lbl, text="PREVIEW  — Space/K play  ·  J back  ·  L forward",
            bg="#0d0d1f", fg="#445588",
            font=("Arial", 9), anchor="w", padx=10,
        ).pack(fill="both")

        self._video_canvas = tk.Canvas(
            right_frame, bg="#000000", highlightthickness=0, bd=0,
        )
        self._video_canvas.pack(fill="both", expand=True)
        # Placeholder text shown before video loads
        self._video_canvas.create_text(
            8, 8, text="Loading video…", fill="#333355",
            anchor="nw", font=("Arial", 11), tags="placeholder",
        )

        # Transport bar
        transport = tk.Frame(right_frame, bg="#0d0d1f", height=44)
        transport.pack(fill="x", side="bottom")
        transport.pack_propagate(False)

        def _tb(text, cmd, green=False):
            bg = "#112211" if green else "#0e0e22"
            fg = "#44cc44" if green else "#7777cc"
            abg = "#224422" if green else "#1a1a44"
            afg = "#66ee66" if green else "#aaaaee"
            b = tk.Button(
                transport, text=text, command=cmd,
                bg=bg, fg=fg, activebackground=abg, activeforeground=afg,
                relief="flat", bd=0, cursor="arrow",
                font=("Arial", 15), padx=6, pady=4,
            )
            b.pack(side="left", padx=2, pady=5)
            return b

        _tb("⏮", self._t_to_start)
        _tb("◀◀", lambda: self._seek_rel(-5))
        self._play_btn = _tb("▶", self._toggle_play, green=True)
        _tb("▶▶", lambda: self._seek_rel(5))
        _tb("⏭", self._t_to_end)

        self._time_lbl = tk.Label(
            transport, text="0:00.0 / 0:00",
            bg="#0d0d1f", fg="#6666aa", font=("Menlo", 11),
        )
        self._time_lbl.pack(side="left", padx=14)

        paned.add(right_frame, minsize=280, width=560, stretch="always")

        # ── Global keyboard bindings ──────────────────────────────────────────
        self.bind("<Control-z>",       lambda _: self._undo())
        self.bind("<Control-y>",       lambda _: self._redo())
        self.bind("<Control-Z>",       lambda _: self._redo())   # Ctrl+Shift+Z
        self.bind("<Control-a>",       lambda _: self._select_all())
        self.bind("<Escape>",          lambda _: self._clear_sel())
        self.bind("e",                 lambda _: self._export_dialog())
        # Give focus to text widget so arrows work immediately
        self.after(10, self._text.focus_set)

    # ── Text tag configuration ─────────────────────────────────────────────────

    def _setup_text_tags(self) -> None:
        t = self._text
        fn = ("Georgia", 15)
        fb = ("Georgia", 15, "bold")

        tag_defs = {
            # ── TextSegment ──────────────────────────────────────────────────
            "t_normal":   dict(foreground="#eeeeff", font=fn),
            "t_sel":      dict(foreground="white",   background="#1a3a5c", font=fb),
            "t_del":      dict(foreground="#cc3333", overstrike=True,      font=fn),
            "t_del_sel":  dict(foreground="#ff6666", background="#1a3a5c", overstrike=True, font=fb),
            # ── Silence (detected long) ───────────────────────────────────────
            "sl_normal":  dict(foreground="#00aaaa", background="#000018", font=fn),
            "sl_sel":     dict(foreground="#00ffff", background="#1a3a5c", font=fb),
            "sl_del":     dict(foreground="#882222", background="#1e0000", font=fn),
            "sl_del_sel": dict(foreground="#ff4444", background="#1a3a5c", overstrike=True, font=fb),
            # ── Silence (short gap) ───────────────────────────────────────────
            "sg_normal":  dict(foreground="#333355", background="#030308", font=fn),
            "sg_sel":     dict(foreground="#5555cc", background="#1a3a5c", font=fb),
            "sg_del":     dict(foreground="#442233", background="#100010", font=fn),
            "sg_del_sel": dict(foreground="#aa3388", background="#1a3a5c", font=fb),
            # ── Um-Checker highlights ─────────────────────────────────────────
            "um_hl":      dict(foreground="#ffcc00", background="#1a1000", font=fn),
            "um_soft_hl": dict(foreground="#cc9933", background="#120c00", font=fn),
        }
        for name, cfg in tag_defs.items():
            t.tag_configure(name, **cfg)

    # ── Transcript population ─────────────────────────────────────────────────

    def _populate_transcript(self) -> None:
        """Insert all segments into the Text widget and apply initial styling."""
        t = self._text
        t.delete("1.0", "end")

        settings = self.project.silence_settings

        # _seg_style tracks the currently-applied style tag for each segment so
        # _refresh_seg can remove only that one tag instead of all 14 style tags.
        self._seg_style: list[str] = []

        # Invalidate binary-search caches used by _highlight_current_seg.
        self._seg_starts_cache = None
        self._last_highlight_seg = -1

        for i, seg in enumerate(self.project.segments):
            seg_tag   = f"seg_{i}"
            style_tag = self._style_tag(seg, i, settings)
            self._seg_style.append(style_tag)

            if isinstance(seg, TextSegment):
                content = seg.text + " "
            else:
                content = f"[■ {seg.duration:.2f}s] "

            t.insert("end", content, (seg_tag, style_tag))

        # Prevent accidental text insertion; cursor still works
        t.mark_set("insert", "1.0")

    def _refresh_seg(self, idx: int) -> None:
        """Reapply the correct style tag to one segment (after state change).

        Uses _seg_style to remove only the one currently-applied style tag
        instead of issuing 14 tag_remove calls.  That reduces the per-segment
        cost from 15 tkinter operations down to 2.
        """
        t       = self._text
        seg_tag = f"seg_{idx}"
        ranges  = t.tag_ranges(seg_tag)
        if len(ranges) < 2:
            return
        start, end = str(ranges[0]), str(ranges[1])

        seg       = self.project.segments[idx]
        new_tag   = self._style_tag(seg, idx, self.project.silence_settings)

        seg_style = getattr(self, "_seg_style", None)
        if seg_style is not None and idx < len(seg_style):
            old_tag = seg_style[idx]
            if old_tag != new_tag:
                t.tag_remove(old_tag, start, end)
                t.tag_add(new_tag, start, end)
                seg_style[idx] = new_tag
        else:
            # Fallback: _seg_style not populated yet (shouldn't normally happen)
            for tag in _STYLE_TAGS:
                t.tag_remove(tag, start, end)
            t.tag_add(new_tag, start, end)

    def _style_tag(self, seg: Segment, idx: int, settings: SilenceSettings) -> str:
        """Determine the correct style tag name given the segment's current state."""
        is_del = idx in self.deleted
        is_sel = idx in self.selected

        if isinstance(seg, TextSegment):
            if is_del and is_sel: return "t_del_sel"
            if is_del:            return "t_del"
            if is_sel:            return "t_sel"
            return "t_normal"
        else:
            is_long = seg.is_detected and seg.duration >= settings.min_duration
            pfx = "sl" if is_long else "sg"
            if is_del and is_sel: return f"{pfx}_del_sel"
            if is_del:            return f"{pfx}_del"
            if is_sel:            return f"{pfx}_sel"
            return f"{pfx}_normal"

    # ── Segment lookup helper ─────────────────────────────────────────────────

    def _tk_idx_to_seg(self, tk_idx: str) -> Optional[int]:
        """Return the segment index whose positional tag covers *tk_idx*."""
        for tag in self._text.tag_names(tk_idx):
            if tag.startswith("seg_"):
                try:
                    return int(tag[4:])
                except ValueError:
                    pass
        return None

    # ── Mouse event handlers ──────────────────────────────────────────────────

    def _t_click(self, event: tk.Event) -> str:
        self._text.focus_set()
        idx = self._tk_idx_to_seg(self._text.index(f"@{event.x},{event.y}"))
        if idx is not None:
            self._anchor = idx
            self._set_selection({idx})
            # Seek video to segment start
            self._video_seek_to_seg(idx)
        else:
            self._clear_sel()
        return "break"   # prevent native selection highlighting

    def _t_drag(self, event: tk.Event) -> str:
        idx = self._tk_idx_to_seg(self._text.index(f"@{event.x},{event.y}"))
        if idx is not None and self._anchor is not None:
            lo, hi = sorted((self._anchor, idx))
            self._set_selection(set(range(lo, hi + 1)))
        return "break"

    def _t_up(self, event: tk.Event) -> str:
        return "break"

    def _t_shift_click(self, event: tk.Event) -> str:
        idx = self._tk_idx_to_seg(self._text.index(f"@{event.x},{event.y}"))
        if idx is None:
            return "break"
        if self._anchor is None:
            self._anchor = idx
        lo, hi = sorted((self._anchor, idx))
        self._set_selection(set(range(lo, hi + 1)))
        return "break"

    def _t_double_click(self, event: tk.Event) -> str:
        """Double-click selects the segment under the cursor (like double-click selects a word)."""
        idx = self._tk_idx_to_seg(self._text.index(f"@{event.x},{event.y}"))
        if idx is not None:
            self._anchor = idx
            self._set_selection({idx})
            self._video_seek_to_seg(idx)
        return "break"

    def _t_triple_click(self, event: tk.Event) -> str:
        """Triple-click selects the entire visible 'paragraph' (all segments in the line group)."""
        idx = self._tk_idx_to_seg(self._text.index(f"@{event.x},{event.y}"))
        if idx is None:
            return "break"

        # Expand outward from idx to include consecutive TextSegments in same block
        segs  = self.project.segments
        start = idx
        end   = idx

        # Walk backwards to find block start (stop at silence boundaries or start)
        while start > 0 and isinstance(segs[start - 1], TextSegment):
            start -= 1
        # Walk forward to find block end
        while end < len(segs) - 1 and isinstance(segs[end + 1], TextSegment):
            end += 1

        self._anchor = start
        self._set_selection(set(range(start, end + 1)))
        return "break"

    # ── Keyboard event handler ────────────────────────────────────────────────

    def _t_key(self, event: tk.Event) -> Optional[str]:
        """
        Route key events on the text widget.
        Navigation keys pass through (arrows, Home/End, PgUp/PgDn) to keep
        cursor movement feeling native.  All other editing is intercepted.
        """
        k = event.keysym

        # ── Delete / cut selection ─────────────────────────────────────────
        if k in ("Delete", "BackSpace"):
            self._delete_sel()
            return "break"

        # ── Restore selection ──────────────────────────────────────────────
        if k == "u":
            self._restore_sel()
            return "break"

        # ── Undo / redo ────────────────────────────────────────────────────
        if k == "z" and (event.state & 4):   # Ctrl+Z
            self._undo()
            return "break"
        if k in ("y", "Z") and (event.state & 4):   # Ctrl+Y or Ctrl+Shift+Z
            self._redo()
            return "break"

        # ── Select all ─────────────────────────────────────────────────────
        if k == "a" and (event.state & 4):   # Ctrl+A
            self._select_all()
            return "break"

        # ── Export ─────────────────────────────────────────────────────────
        if k == "e" and not (event.state & 4):
            self._export_dialog()
            return "break"

        # ── Play / pause ────────────────────────────────────────────────────
        if k in ("space", "k", "K"):
            self._toggle_play()
            return "break"

        # ── JKL shuttle ────────────────────────────────────────────────────
        if k in ("j", "J"):
            self._seek_rel(-5)
            return "break"
        if k in ("l", "L"):
            self._seek_rel(5)
            return "break"

        # ── Escape ─────────────────────────────────────────────────────────
        if k == "Escape":
            self._clear_sel()
            return "break"

        # ── Navigation keys: let tkinter move cursor natively ──────────────
        _nav = {
            "Left", "Right", "Up", "Down",
            "Home", "End", "Prior", "Next",
            "shift-Left", "shift-Right", "shift-Up", "shift-Down",
        }
        if k in _nav or k.startswith("shift-") or k.startswith("ctrl-"):
            # After native cursor move, update segment selection based on cursor pos
            self.after(1, self._sync_sel_from_cursor)
            return None   # let tkinter handle it

        # ── Block all printable character insertion ─────────────────────────
        if event.char and event.char.isprintable():
            return "break"

        # Everything else (Fn keys, modifier-only presses): ignore
        return None

    def _sync_sel_from_cursor(self) -> None:
        """After a navigation key, select the segment under the cursor."""
        try:
            cursor_idx = self._text.index("insert")
        except tk.TclError:
            return
        idx = self._tk_idx_to_seg(cursor_idx)
        if idx is not None:
            self._anchor = idx
            self._set_selection({idx})

    # ── Selection management ──────────────────────────────────────────────────

    def _set_selection(self, new_sel: set[int]) -> None:
        changed = self.selected.symmetric_difference(new_sel)
        self.selected = new_sel
        for idx in changed:
            self._refresh_seg(idx)
        self._update_status()

    def _select_all(self) -> None:
        self._anchor = 0
        self._set_selection(set(range(len(self.project.segments))))

    def _clear_sel(self) -> None:
        self._set_selection(set())
        self._anchor = None

    # ── Video seek helper (seeks to segment start on click) ───────────────────

    def _video_seek_to_seg(self, idx: int) -> None:
        if self._player is None:
            return
        seg = self.project.segments[idx]
        try:
            self._player.seek(seg.start)
        except Exception:
            pass

    # ── Edit actions ──────────────────────────────────────────────────────────

    def _delete_sel(self) -> None:
        if not self.selected:
            return
        self._push_undo()
        self.deleted.update(self.selected)
        changed = set(self.selected)
        self._clear_sel()
        for idx in changed:
            self._refresh_seg(idx)
        self._sync_project()
        self._update_status()
        # Update edit-aware player
        if self._player:
            self._player.set_project(self.project)
        if self._waveform_view:
            self._waveform_view.draw(project=self.project)

    def _restore_sel(self) -> None:
        if not self.selected:
            return
        self._push_undo()
        self.deleted -= self.selected
        changed = set(self.selected)
        self._clear_sel()
        for idx in changed:
            self._refresh_seg(idx)
        self._sync_project()
        self._update_status()
        if self._player:
            self._player.set_project(self.project)
        if self._waveform_view:
            self._waveform_view.draw(project=self.project)

    def _auto_delete(self) -> None:
        """Mark all long detected silences as deleted."""
        settings = self.project.silence_settings
        self._push_undo()
        changed: set[int] = set()
        for i, seg in enumerate(self.project.segments):
            if (
                isinstance(seg, Silence)
                and seg.is_detected
                and seg.duration >= settings.min_duration
                and seg.deletable_range(settings.buffer) is not None
            ):
                if i not in self.deleted:
                    self.deleted.add(i)
                    changed.add(i)
        # Only refresh the segments whose state actually changed.
        for i in changed:
            self._refresh_seg(i)
        self._sync_project()
        self._update_status()
        if self._player:
            self._player.set_project(self.project)
        if self._waveform_view:
            self._waveform_view.draw(project=self.project)

    def _restore_all(self) -> None:
        self._push_undo()
        changed = set(self.deleted)
        self.deleted.clear()
        self._clear_sel()
        for idx in changed:
            self._refresh_seg(idx)
        self._sync_project()
        self._update_status()
        if self._player:
            self._player.set_project(self.project)
        if self._waveform_view:
            self._waveform_view.draw(project=self.project)

    # ── Undo / redo ───────────────────────────────────────────────────────────

    def _push_undo(self) -> None:
        self._undo_stack.append(frozenset(self.deleted))
        self._redo_stack.clear()

    def _undo(self) -> None:
        if not self._undo_stack:
            return
        self._redo_stack.append(frozenset(self.deleted))
        prev = set(self._undo_stack.pop())
        changed = self.deleted.symmetric_difference(prev)
        self.deleted = prev
        self._clear_sel()
        for idx in changed:
            self._refresh_seg(idx)
        self._sync_project()
        self._update_status()
        if self._player:
            self._player.set_project(self.project)
        if self._waveform_view:
            self._waveform_view.draw(project=self.project)

    def _redo(self) -> None:
        if not self._redo_stack:
            return
        self._undo_stack.append(frozenset(self.deleted))
        nxt = set(self._redo_stack.pop())
        changed = self.deleted.symmetric_difference(nxt)
        self.deleted = nxt
        self._clear_sel()
        for idx in changed:
            self._refresh_seg(idx)
        self._sync_project()
        self._update_status()
        if self._player:
            self._player.set_project(self.project)
        if self._waveform_view:
            self._waveform_view.draw(project=self.project)

    # ── Settings ──────────────────────────────────────────────────────────────

    def _on_setting_change(self, _=None) -> None:
        # Debounce: cancel any pending refresh and reschedule 150 ms out so
        # rapid spinbox clicks don't re-style every silence segment on each tick.
        if getattr(self, "_setting_change_id", None) is not None:
            try:
                self.after_cancel(self._setting_change_id)
            except Exception:
                pass
        self._setting_change_id = self.after(150, self._apply_setting_change)

    def _apply_setting_change(self) -> None:
        self._setting_change_id = None
        try:
            thresh = self._thresh_spin.get()
            buf    = max(0.001, self._buffer_spin.get())
            mn     = max(0.001, self._min_spin.get())
        except Exception:
            return
        self.project.silence_settings = SilenceSettings(
            threshold_db=thresh, min_duration=mn, buffer=buf,
        )
        for i, seg in enumerate(self.project.segments):
            if isinstance(seg, Silence):
                self._refresh_seg(i)
        self._update_status()

    def _reanalyse(self) -> None:
        """Re-run silence detection in the background with current settings."""
        settings = self.project.silence_settings

        def worker() -> None:
            from .audio    import detect_silences
            from .timeline import build_timeline
            try:
                new_sil  = detect_silences(self.project.audio_path, settings)
                words    = [s for s in self.project.segments if isinstance(s, TextSegment)]
                new_segs = build_timeline(words, new_sil, self.project.video_duration, settings)
                self.project.segments = new_segs
                self.project.deleted  = [i for i in self.project.deleted if i < len(new_segs)]
                self.deleted  = set(self.project.deleted)
                self.selected = set()
                self._anchor  = None
                self.after(0, self._full_refresh)
            except Exception as exc:
                self.after(0, lambda: messagebox.showerror("Re-analysis Error", str(exc)))

        threading.Thread(target=worker, daemon=True).start()
        self._status_var.set("Re-analysing silence…  please wait.")

    def _full_refresh(self) -> None:
        self._populate_transcript()
        if self._waveform_view:
            self._waveform_view.draw(project=self.project)
        self._update_status()

    # ── Um-Checker ────────────────────────────────────────────────────────────

    def _um_checker(self) -> None:
        """
        Scan transcript for filler words, highlight them, offer to delete all.
        Hard fillers: um, uh, hmm, etc.
        Soft fillers: like, basically, literally, etc. (context-dependent).
        """
        hard_idxs: list[int] = []
        soft_idxs: list[int] = []

        for i, seg in enumerate(self.project.segments):
            if isinstance(seg, TextSegment):
                word = seg.text.strip().lower().rstrip(".,!?;:'\"")
                if word in _UM_HARD:
                    hard_idxs.append(i)
                elif word in _UM_SOFT:
                    soft_idxs.append(i)

        # Clear old highlights first
        self._text.tag_remove("um_hl",      "1.0", "end")
        self._text.tag_remove("um_soft_hl", "1.0", "end")

        if not hard_idxs and not soft_idxs:
            messagebox.showinfo(
                "Um-Checker",
                "No filler words found!\n\n"
                f"Hard fillers checked: {', '.join(sorted(_UM_HARD))}\n"
                f"Soft fillers checked: {', '.join(sorted(_UM_SOFT))}",
            )
            return

        # Apply orange highlights to found fillers
        for i in hard_idxs:
            ranges = self._text.tag_ranges(f"seg_{i}")
            if len(ranges) >= 2:
                self._text.tag_add("um_hl", str(ranges[0]), str(ranges[1]))
        for i in soft_idxs:
            ranges = self._text.tag_ranges(f"seg_{i}")
            if len(ranges) >= 2:
                self._text.tag_add("um_soft_hl", str(ranges[0]), str(ranges[1]))

        self._um_hard_indices = hard_idxs
        self._um_soft_indices = soft_idxs

        # Show the chooser dialog
        dialog = _UmCheckerDialog(self, hard_idxs, soft_idxs, self.project.segments)
        dialog.wait_window()
        result = dialog.choice

        # Clear highlights (deleted ones will get red strikethrough styling)
        self._text.tag_remove("um_hl",      "1.0", "end")
        self._text.tag_remove("um_soft_hl", "1.0", "end")

        if result == "hard":
            to_del = set(hard_idxs)
        elif result == "all":
            to_del = set(hard_idxs) | set(soft_idxs)
        else:
            return   # cancelled

        self._push_undo()
        self.deleted.update(to_del)
        for idx in to_del:
            self._refresh_seg(idx)
        self._sync_project()
        self._update_status()
        if self._player:
            self._player.set_project(self.project)
        if self._waveform_view:
            self._waveform_view.draw(project=self.project)

    # ── Video player ──────────────────────────────────────────────────────────

    def _load_video_async(self) -> None:
        """Kick off background loading of OpenCVPlayer + WaveformData."""
        def worker():
            try:
                from .video_player import OpenCVPlayer
                player = OpenCVPlayer()
                player.load(self.project.video_path)
                player.set_project(self.project)
                player.set_frame_callback(self._on_frame)
                player.set_time_callback(self._on_time)
                self.after(0, lambda: self._on_player_ready(player))
            except ImportError:
                self.after(0, lambda: self._video_canvas.itemconfigure(
                    "placeholder", text="opencv-python not installed\n(pip install opencv-python)",
                ))
            except Exception as exc:
                self.after(0, lambda: self._video_canvas.itemconfigure(
                    "placeholder", text=f"Video error:\n{exc}",
                ))

            # Load waveform separately (may succeed even if video fails)
            try:
                from .waveform import WaveformData
                wd = WaveformData.from_audio(self.project.audio_path)
                self.after(0, lambda: self._on_waveform_ready(wd))
            except Exception:
                pass   # waveform is optional; timeline stays empty

        threading.Thread(target=worker, daemon=True, name="LoaderThread").start()

    def _on_player_ready(self, player) -> None:
        self._player = player
        # Remove the loading placeholder
        try:
            self._video_canvas.delete("placeholder")
        except Exception:
            pass
        # Display first frame immediately
        player.seek(0.0)

    def _on_waveform_ready(self, wd) -> None:
        from .waveform import WaveformView
        self._waveform_data = wd
        self._waveform_view = WaveformView(
            self._timeline_canvas,
            wd,
            on_seek=self._on_waveform_seek,
        )
        self._waveform_view.set_project(self.project)
        self._waveform_view.draw(project=self.project, playhead_s=0.0)

    def _on_frame(self, frame_rgb, time_s: float) -> None:
        """Called from OpenCVPlayer's play thread — schedule display on main thread."""
        self.after(0, lambda: self._display_frame(frame_rgb, time_s))

    def _on_time(self, time_s: float) -> None:
        """Called from OpenCVPlayer's play thread — schedule playhead update."""
        self.after(0, lambda: self._update_playhead(time_s))

    def _display_frame(self, frame_rgb, time_s: float) -> None:
        """Render a numpy RGB frame into the video canvas (aspect-ratio preserving)."""
        try:
            from PIL import Image, ImageTk
        except ImportError:
            return

        c      = self._video_canvas
        cw, ch = c.winfo_width(), c.winfo_height()
        if cw < 4 or ch < 4:
            return

        # Scale to fit canvas maintaining aspect ratio
        fh, fw = frame_rgb.shape[:2]
        if fh == 0 or fw == 0:
            return
        scale = min(cw / fw, ch / fh)
        nw, nh = max(1, int(fw * scale)), max(1, int(fh * scale))
        ox, oy = (cw - nw) // 2, (ch - nh) // 2

        img         = Image.fromarray(frame_rgb).resize((nw, nh), Image.BILINEAR)
        photo       = ImageTk.PhotoImage(img)
        self._photo_image = photo    # hold reference

        c.delete("frame")
        c.create_image(ox, oy, anchor="nw", image=photo, tags="frame")

        self._update_playhead(time_s)

    def _update_playhead(self, time_s: float) -> None:
        """Update transport time label and waveform playhead."""
        # Time label
        total   = self.project.video_duration
        cs      = int(time_s * 10) % 10
        mm, ss  = divmod(int(time_s), 60)
        tmm, ts = divmod(int(total), 60)
        self._time_lbl.configure(
            text=f"{mm}:{ss:02d}.{cs}  /  {tmm}:{ts:02d}"
        )

        # Update play/pause button text
        is_playing = self._player.is_playing if self._player else False
        self._play_btn.configure(text="⏸" if is_playing else "▶")

        # Update waveform playhead — move_playhead() only repositions the
        # playhead line; it does NOT redraw 6012 segment markers + waveform
        # bars.  Full draw() is called only when segment state changes.
        if self._waveform_view:
            self._waveform_view.move_playhead(time_s)

        # Highlight the segment corresponding to current time
        self._highlight_current_seg(time_s)

    def _highlight_current_seg(self, time_s: float) -> None:
        """Scroll transcript to show segment at *time_s*.

        Uses a cached sorted-starts list + bisect for O(log N) lookup instead
        of the original O(N) linear scan that blocked the UI at 6000+ segments.
        Skips the expensive tag_ranges/see calls when the segment hasn't changed.
        """
        import bisect
        segs = self.project.segments
        if not segs:
            return

        # Rebuild the sorted-starts cache whenever the segment list changes.
        if getattr(self, "_seg_starts_cache", None) is None:
            self._seg_starts_cache = [s.start for s in segs]

        idx = bisect.bisect_right(self._seg_starts_cache, time_s) - 1
        if idx < 0:
            return
        seg = segs[idx]
        if time_s > seg.end:
            return   # time is in a gap between segments — nothing to scroll to

        if idx == getattr(self, "_last_highlight_seg", -1):
            return   # already scrolled to this segment

        self._last_highlight_seg = idx
        ranges = self._text.tag_ranges(f"seg_{idx}")
        if ranges:
            self._text.see(str(ranges[0]))

    def _on_waveform_seek(self, time_s: float) -> None:
        """Called when user clicks on the waveform timeline."""
        if self._player:
            self._player.seek(time_s)
        else:
            self._update_playhead(time_s)

    # ── Transport controls ────────────────────────────────────────────────────

    def _toggle_play(self) -> None:
        if self._player is None:
            return
        self._player.toggle()
        self._play_btn.configure(text="⏸" if self._player.is_playing else "▶")

    def _seek_rel(self, delta_s: float) -> None:
        if self._player is None:
            return
        t = max(0.0, min(self._player.current_time + delta_s,
                         self.project.video_duration))
        self._player.seek(t)

    def _t_to_start(self) -> None:
        if self._player:
            self._player.seek(0.0)

    def _t_to_end(self) -> None:
        if self._player:
            self._player.seek(self.project.video_duration)

    # ── Export ────────────────────────────────────────────────────────────────

    def _export_dialog(self) -> None:
        dialog = _ExportFormatDialog(self)
        dialog.wait_window()
        choice = dialog.choice
        if choice is None:
            return

        stem    = Path(self.project.video_path).stem
        outdir  = Path(self.project.video_path).parent
        ext_map = {
            "fcpxml": (".fcpxml", "FCPXML Files",   "*.fcpxml"),
            "mp4":    (".mp4",    "MP4 Video",       "*.mp4"),
            "edl":    (".edl",    "EDL Files",       "*.edl"),
            "sh":     (".sh",     "Shell Scripts",   "*.sh"),
        }
        ext, ftype_name, ftype_glob = ext_map[choice]

        out_path = filedialog.asksaveasfilename(
            initialdir        = str(outdir),
            initialfile       = f"{stem}_edited{ext}",
            defaultextension  = ext,
            filetypes         = [(ftype_name, ftype_glob), ("All Files", "*")],
            title             = "Save Export",
        )
        if not out_path:
            return

        self._do_export(out_path, choice)

    def _do_export(self, output_path: str, fmt: str) -> None:
        self._status_var.set(f"Exporting as {fmt.upper()}…")

        def worker():
            from . import exporter
            try:
                if fmt == "fcpxml":
                    exporter.export_fcpxml(self.project, output_path)
                elif fmt == "mp4":
                    exporter.export_video(self.project, output_path)
                elif fmt == "edl":
                    exporter.export_edl(self.project, output_path)
                elif fmt == "sh":
                    mp4 = output_path.rsplit(".", 1)[0] + "_edited.mp4"
                    exporter.generate_ffmpeg_script(self.project, mp4, output_path)
                self.after(0, lambda: messagebox.showinfo(
                    "Export Complete", f"Saved to:\n{output_path}"
                ))
            except Exception as exc:
                self.after(0, lambda: messagebox.showerror("Export Error", str(exc)))
            finally:
                self.after(0, self._update_status)

        threading.Thread(target=worker, daemon=True).start()

    # ── Status bar ────────────────────────────────────────────────────────────

    def _update_status(self) -> None:
        n_del  = len(self.deleted)
        n_sel  = len(self.selected)
        t_save = self.project.time_saved()
        n_segs = len(self.project.segments)
        buf    = self.project.silence_settings.buffer
        self._status_var.set(
            f"Segments: {n_segs}  ·  Deleted: {n_del}  ·  "
            f"Time saved: {t_save:.3f} s  ·  Selected: {n_sel}  ·  "
            f"Buffer: {buf:.3f} s    "
            "  Delete=cut  U=restore  Ctrl+A=all  Esc=clear  E=export  Ctrl+Z/Y=undo"
        )

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _sync_project(self) -> None:
        self.project.deleted = sorted(self.deleted)

    def _on_close(self) -> None:
        if self._player:
            self._player.close()
        self.destroy()


# ── Um-Checker dialog ──────────────────────────────────────────────────────────

class _UmCheckerDialog(ctk.CTkToplevel):
    """
    Shows a summary of found filler words and offers three actions:
      • Delete hard fillers (um, uh, hmm…)
      • Delete all fillers (including soft: like, basically…)
      • Cancel
    """

    def __init__(
        self,
        parent,
        hard_idxs: list[int],
        soft_idxs: list[int],
        segments,
        **kwargs,
    ):
        super().__init__(parent, **kwargs)
        self.title("Um-Checker")
        self.geometry("460x320")
        self.resizable(False, False)
        self.grab_set()
        self.configure(fg_color="#0d0d1f")
        self.choice: Optional[str] = None

        # ── Summary label ──────────────────────────────────────────────────
        ctk.CTkLabel(
            self,
            text="Filler words found in transcript:",
            text_color="#aaaaee",
            font=("Arial", 13, "bold"),
        ).pack(pady=(18, 4), padx=20, anchor="w")

        # Hard fillers
        hard_words = [segments[i].text for i in hard_idxs]
        hard_count = len(hard_idxs)
        ctk.CTkLabel(
            self,
            text=f"  Hard fillers (um / uh / hmm…):  {hard_count} found"
                 + (f"  →  {', '.join(hard_words[:8])}{'…' if len(hard_words)>8 else ''}"
                    if hard_words else ""),
            text_color="#ffcc00",
            anchor="w",
        ).pack(fill="x", padx=24, pady=2)

        # Soft fillers
        soft_words = [segments[i].text for i in soft_idxs]
        soft_count = len(soft_idxs)
        ctk.CTkLabel(
            self,
            text=f"  Soft fillers (like / basically…): {soft_count} found"
                 + (f"  →  {', '.join(soft_words[:8])}{'…' if len(soft_words)>8 else ''}"
                    if soft_words else ""),
            text_color="#cc9933",
            anchor="w",
        ).pack(fill="x", padx=24, pady=2)

        ctk.CTkLabel(
            self, text=" ",
            fg_color="#1a1a33", height=1,
        ).pack(fill="x", padx=16, pady=(8, 4))

        # ── Action buttons ─────────────────────────────────────────────────
        _B = dict(width=400, height=36, corner_radius=6,
                  fg_color="#1a1a3a", hover_color="#2a2a5a",
                  border_color="#3333aa", border_width=1)

        ctk.CTkButton(
            self,
            text=f"Delete hard fillers only ({hard_count} segments)",
            command=lambda: self._pick("hard"),
            **_B,
        ).pack(pady=4, padx=20)

        ctk.CTkButton(
            self,
            text=f"Delete ALL fillers — hard + soft ({hard_count + soft_count} segments)",
            command=lambda: self._pick("all"),
            fg_color="#2a1800", hover_color="#4a2800",
            border_color="#cc8800", border_width=1,
            width=400, height=36, corner_radius=6,
        ).pack(pady=4, padx=20)

        ctk.CTkButton(
            self, text="Cancel  (keep highlights for review)",
            command=self.destroy,
            fg_color="#1a1a1a", hover_color="#2a2a2a",
            border_color="#444444", border_width=1,
            width=400, height=36, corner_radius=6,
        ).pack(pady=4, padx=20)

    def _pick(self, choice: str) -> None:
        self.choice = choice
        self.destroy()


# ── Export format dialog ───────────────────────────────────────────────────────

class _ExportFormatDialog(ctk.CTkToplevel):
    """Modal dialog for choosing the export format."""

    def __init__(self, parent, **kwargs):
        super().__init__(parent, **kwargs)
        self.title("Export Format")
        self.geometry("380x260")
        self.resizable(False, False)
        self.grab_set()
        self.configure(fg_color="#0d0d1f")
        self.choice: Optional[str] = None

        ctk.CTkLabel(
            self, text="Choose export format:",
            text_color="#aaaaee", font=("Arial", 13),
        ).pack(pady=(20, 8))

        _B = dict(width=340, height=38, corner_radius=6,
                  fg_color="#1a1a3a", hover_color="#2a2a5a",
                  border_color="#3333aa", border_width=1)

        ctk.CTkButton(
            self, text="FCPXML  — import back into Final Cut Pro",
            command=lambda: self._pick("fcpxml"), **_B,
        ).pack(pady=3)
        ctk.CTkButton(
            self, text="MP4  — re-encoded video via FFmpeg",
            command=lambda: self._pick("mp4"), **_B,
        ).pack(pady=3)
        ctk.CTkButton(
            self, text="EDL  — edit decision list (Premiere / Avid)",
            command=lambda: self._pick("edl"), **_B,
        ).pack(pady=3)
        ctk.CTkButton(
            self, text="Shell script  — FFmpeg bash command",
            command=lambda: self._pick("sh"), **_B,
        ).pack(pady=3)

    def _pick(self, fmt: str) -> None:
        self.choice = fmt
        self.destroy()


# ── Loading / progress window ──────────────────────────────────────────────────

class LoadingWindow(ctk.CTkToplevel):
    """Indeterminate-progress window shown during initial video processing."""

    def __init__(self, parent=None, **kwargs):
        super().__init__(parent, **kwargs)
        self.title("Processing…")
        self.geometry("500x130")
        self.resizable(False, False)
        self.configure(fg_color="#0d0d1f")

        self._label = ctk.CTkLabel(
            self, text="Starting…",
            text_color="#aaaaee", font=("Arial", 14),
        )
        self._label.pack(pady=16, padx=24)

        self._bar = ctk.CTkProgressBar(self, mode="indeterminate", width=440)
        self._bar.pack(pady=4)
        self._bar.start()

    def set_message(self, msg: str) -> None:
        self._label.configure(text=msg)
        self.update_idletasks()

    def close(self) -> None:
        self._bar.stop()
        self.destroy()
