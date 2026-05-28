"""
Minimalist Pomodoro Timer - always-on-top, top-right corner.
Requires: Python 3.8+ with tkinter (included in standard Python install).

Controls:
  Double-click  : Start / Pause / Resume
  Space         : Start / Pause / Resume
  Right-click   : Timer presets + stats + custom preset
  Scroll wheel  : Resize (bigger / smaller)
  Middle-click   : Quit
  Escape        : Quit
  Drag          : Move window

Hover to expand, move cursor away to collapse into a minimal circle.
Single-instance: only one timer can run at a time (Windows mutex).
"""

import tkinter as tk
import tkinter.simpledialog
import winsound
import threading
import struct
import wave
import math
import tempfile
import os
import sys
import time
import json
import datetime
import ctypes
from ctypes import wintypes


# === Single Instance via Windows Named Mutex ===

def ensure_single_instance():
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    mutex_name = "Global\\PomodoroTimerMutex_v1"
    ERROR_ALREADY_EXISTS = 183
    handle = kernel32.CreateMutexW(None, wintypes.BOOL(True), mutex_name)
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle(handle)
        sys.exit(0)
    return handle


# === Persistence paths ===

def _app_dir():
    base = os.environ.get("APPDATA", os.path.expanduser("~"))
    d = os.path.join(base, "PomodoroApp")
    os.makedirs(d, exist_ok=True)
    return d

STATE_FILE = os.path.join(_app_dir(), "state.json")
STATS_FILE = os.path.join(_app_dir(), "stats.jsonl")


# === Timer Presets ===

PRESETS = {
    "Classic (25/5)": (25 * 60, 5 * 60),
    "Deep Work (50/10)": (50 * 60, 10 * 60),
    "Sprint (15/3)": (15 * 60, 3 * 60),
    "Marathon (60/15)": (60 * 60, 15 * 60),
}


# === Taskbar Progress (ITaskbarList3) ===

class _Taskbar:
    TBPF_NOPROGRESS    = 0x0
    TBPF_INDETERMINATE = 0x1
    TBPF_NORMAL        = 0x2
    TBPF_ERROR         = 0x4
    TBPF_PAUSED        = 0x8

    def __init__(self):
        self._taskbar = None
        try:
            from ctypes import windll, POINTER, c_int
            from ctypes.wintypes import HWND, UINT, ULARGE_INTEGER
            import comtypes
            import comtypes.client
            self._taskbar = comtypes.client.CreateObject(
                "{56FDF344-FD6D-11d0-958A-006097C9A090}",
                interface=comtypes.gen.TaskbarLib.ITaskbarList3
            )
        except Exception:
            self._taskbar = None

    def set_progress(self, hwnd, current, total):
        if not self._taskbar:
            return
        try:
            if total <= 0:
                self._taskbar.SetProgressState(hwnd, self.TBPF_NOPROGRESS)
                return
            self._taskbar.SetProgressState(hwnd, self.TBPF_NORMAL)
            self._taskbar.SetProgressValue(hwnd, current, total)
        except Exception:
            pass

    def set_paused(self, hwnd):
        if not self._taskbar:
            return
        try:
            self._taskbar.SetProgressState(hwnd, self.TBPF_PAUSED)
        except Exception:
            pass

    def clear(self, hwnd):
        if not self._taskbar:
            return
        try:
            self._taskbar.SetProgressState(hwnd, self.TBPF_NOPROGRESS)
        except Exception:
            pass


# Lightweight fallback using raw COM via ctypes (no comtypes dependency)
class _TaskbarRaw:
    """ITaskbarList3 via raw ctypes COM — no third-party packages."""
    CLSID = "{56FDF344-FD6D-11d0-958A-006097C9A090}"
    IID   = "{EA1AFB91-9E28-4B86-90E9-9E9F8A5EEFAF}"

    TBPF_NOPROGRESS = 0x0
    TBPF_NORMAL     = 0x2
    TBPF_PAUSED     = 0x8

    def __init__(self):
        self._ok = False
        try:
            import ctypes
            import ctypes.wintypes
            ole32 = ctypes.windll.ole32
            ole32.CoInitialize(None)

            CLSID_b = self._str_to_guid(self.CLSID)
            IID_b   = self._str_to_guid(self.IID)
            ptr = ctypes.c_void_p()
            hr = ole32.CoCreateInstance(
                ctypes.byref(CLSID_b), None, 1,
                ctypes.byref(IID_b), ctypes.byref(ptr)
            )
            if hr != 0:
                return
            self._ptr = ptr
            # vtable offsets: HrInit=3, SetProgressValue=9, SetProgressState=10
            vtable = ctypes.cast(ptr, ctypes.POINTER(ctypes.c_void_p))
            vt = ctypes.cast(vtable[0], ctypes.POINTER(ctypes.c_void_p))
            # HrInit
            HrInit = ctypes.WINFUNCTYPE(ctypes.HRESULT)(vt[3])
            HrInit(ptr)
            self._SetProgressValue = ctypes.WINFUNCTYPE(
                ctypes.HRESULT,
                ctypes.c_void_p, ctypes.wintypes.HWND,
                ctypes.c_ulonglong, ctypes.c_ulonglong)(vt[9])
            self._SetProgressState = ctypes.WINFUNCTYPE(
                ctypes.HRESULT,
                ctypes.c_void_p, ctypes.wintypes.HWND,
                ctypes.c_int)(vt[10])
            self._ok = True
        except Exception:
            self._ok = False

    @staticmethod
    def _str_to_guid(s):
        import ctypes
        import ctypes.wintypes
        class GUID(ctypes.Structure):
            _fields_ = [("Data1", ctypes.c_ulong),
                        ("Data2", ctypes.c_ushort),
                        ("Data3", ctypes.c_ushort),
                        ("Data4", ctypes.c_ubyte * 8)]
        g = GUID()
        ctypes.windll.ole32.CLSIDFromString(s, ctypes.byref(g))
        return g

    def set_progress(self, hwnd, current, total):
        if not self._ok or total <= 0:
            return
        try:
            self._SetProgressState(self._ptr, hwnd, self.TBPF_NORMAL)
            self._SetProgressValue(self._ptr, hwnd, total - current, total)
        except Exception:
            pass

    def set_paused(self, hwnd):
        if not self._ok:
            return
        try:
            self._SetProgressState(self._ptr, hwnd, self.TBPF_PAUSED)
        except Exception:
            pass

    def clear(self, hwnd):
        if not self._ok:
            return
        try:
            self._SetProgressState(self._ptr, hwnd, self.TBPF_NOPROGRESS)
        except Exception:
            pass


# === Main App ===

class PomodoroApp:
    SESSIONS_BEFORE_LONG = 4
    LONG_BREAK_MULTIPLIER = 3

    MIN_SCALE = 0.4
    MAX_SCALE = 2.0
    SCALE_STEP = 0.1

    COLORS = {
        "bg": "#1a1a2e",
        "work_accent": "#e94560",
        "work_text": "#ffffff",
        "short_break_accent": "#4ecca3",
        "long_break_accent": "#3282b8",
        "dim": "#555555",
        "ring_bg": "#2a2a4a",
    }

    BUBBLE_SIZE = 56

    def __init__(self, mutex_handle):
        self.mutex_handle = mutex_handle
        self.root = tk.Tk()
        self.root.title("Pomodoro")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", 0.95)
        self.root.configure(bg=self.COLORS["bg"])

        self.scale = 1.0

        # Timer state
        self.work_duration = 25 * 60
        self.break_duration = 5 * 60
        self.running = False
        self.time_left = self.work_duration
        self.total_time = self.work_duration
        self.mode = "work"
        self.sessions_completed = 0
        self.after_id = None
        self._tick_start = None
        self._tick_base = 0

        # Animation state
        self._color_fade_id = None
        self._current_accent = self.COLORS["work_accent"]
        self._expand_anim_id = None
        self._is_collapsed = False
        self._is_animating = False
        self._hover_debounce_id = None

        # Taskbar progress
        self._taskbar = _TaskbarRaw()
        self._hwnd = None  # resolved after mainloop starts

        # Sounds
        self.chime_path = self._generate_chime()
        self.bubble_path = self._generate_bubble_sound()

        # Load saved state
        self._load_state()

        # Build UI
        self._build_expanded_ui()
        self._build_collapsed_ui()
        self._collapsed_frame.pack_forget()

        # Bindings
        self.root.bind("<Double-Button-1>", self.toggle)
        self.root.bind("<Button-3>", self._show_presets)
        self.root.bind("<Button-2>", lambda e: self.quit())
        self.root.bind("<Escape>", lambda e: self.quit())
        self.root.bind_all("<MouseWheel>", self.on_scroll)
        self.root.bind_all("<space>", lambda e: self.toggle())

        self.root.bind("<Enter>", self._on_mouse_enter)
        self.root.bind("<Leave>", self._on_mouse_leave)

        self.root.bind("<Button-1>", self.start_drag)
        self.root.bind("<B1-Motion>", self.on_drag)

        # Position top-right
        self.root.update_idletasks()
        w = self.root.winfo_reqwidth()
        screen_w = self.root.winfo_screenwidth()
        x = screen_w - w - 20
        y = 20
        self.root.geometry(f"+{x}+{y}")

        self._apply_rounded_corners()

        # Resolve HWND after window is visible
        self.root.after(100, self._resolve_hwnd)

        self.root.mainloop()

    def _resolve_hwnd(self):
        try:
            self._hwnd = self.root.winfo_id()
        except Exception:
            pass

    # === State Persistence ===

    def _load_state(self):
        try:
            with open(STATE_FILE, "r") as f:
                s = json.load(f)
            self.work_duration     = s.get("work_duration", self.work_duration)
            self.break_duration    = s.get("break_duration", self.break_duration)
            self.sessions_completed = s.get("sessions_completed", 0)
            self.mode              = s.get("mode", "work")
            self.scale             = s.get("scale", 1.0)
            saved_time             = s.get("time_left", None)
            saved_total            = s.get("total_time", None)
            if saved_time is not None:
                self.time_left  = saved_time
                self.total_time = saved_total or saved_time
            else:
                self.time_left  = self.work_duration if self.mode == "work" else self.break_duration
                self.total_time = self.time_left
            self._tick_base = self.time_left
            if self.mode == "work":
                self._current_accent = self.COLORS["work_accent"]
            elif self.mode == "short_break":
                self._current_accent = self.COLORS["short_break_accent"]
            else:
                self._current_accent = self.COLORS["long_break_accent"]
        except Exception:
            pass

    def _save_state(self):
        try:
            s = {
                "work_duration":      self.work_duration,
                "break_duration":     self.break_duration,
                "sessions_completed": self.sessions_completed,
                "mode":               self.mode,
                "time_left":          self.time_left,
                "total_time":         self.total_time,
                "scale":              self.scale,
            }
            with open(STATE_FILE, "w") as f:
                json.dump(s, f)
        except Exception:
            pass

    # === Stats Logging ===

    def _log_session(self):
        try:
            entry = {
                "ts":   datetime.datetime.now().isoformat(timespec="seconds"),
                "mode": self.mode,
                "work_duration":  self.work_duration,
                "break_duration": self.break_duration,
            }
            with open(STATS_FILE, "a") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception:
            pass

    def _read_today_stats(self):
        today = datetime.date.today().isoformat()
        work_count = 0
        try:
            with open(STATS_FILE, "r") as f:
                for line in f:
                    try:
                        e = json.loads(line)
                        if e.get("ts", "").startswith(today) and e.get("mode") == "work":
                            work_count += 1
                    except Exception:
                        pass
        except Exception:
            pass
        return work_count

    # === Expanded UI ===

    def _build_expanded_ui(self):
        sizes = self._font_sizes()
        self._expanded_frame = tk.Frame(self.root, bg=self.COLORS["bg"],
                                        padx=sizes["pad_x"], pady=sizes["pad_y"])
        self._expanded_frame.pack()

        ring_size = sizes["ring_size"]
        self.canvas = tk.Canvas(
            self._expanded_frame, width=ring_size, height=ring_size,
            bg=self.COLORS["bg"], highlightthickness=0
        )
        self.canvas.pack()

        pad = sizes["ring_pad"]
        self._ring_bg = self.canvas.create_arc(
            pad, pad, ring_size - pad, ring_size - pad,
            start=90, extent=-360, style="arc",
            outline=self.COLORS["ring_bg"], width=sizes["ring_width"]
        )
        self._ring_arc = self.canvas.create_arc(
            pad, pad, ring_size - pad, ring_size - pad,
            start=90, extent=-360, style="arc",
            outline=self._current_accent, width=sizes["ring_width"]
        )
        self._mode_text = self.canvas.create_text(
            ring_size // 2, ring_size * 0.28,
            text=self._mode_label(), fill=self._current_accent,
            font=("Segoe UI", sizes["mode"], "bold")
        )
        mins, secs = divmod(self.time_left, 60)
        self._time_text = self.canvas.create_text(
            ring_size // 2, ring_size * 0.50,
            text=f"{mins:02d}:{secs:02d}", fill=self.COLORS["work_text"],
            font=("Consolas", sizes["time"], "bold")
        )
        self._dots_text = self.canvas.create_text(
            ring_size // 2, ring_size * 0.72,
            text=self._dots_string(), fill=self.COLORS["dim"],
            font=("Segoe UI", sizes["dots"])
        )
        self.hint_label = tk.Label(
            self._expanded_frame, text="double-click: start", fg=self.COLORS["dim"],
            bg=self.COLORS["bg"], font=("Segoe UI", sizes["hint"])
        )
        self.hint_label.pack(pady=(sizes["hint_pad"], 0))
        # Sync ring to restored state
        self._update_ring()
        self._update_display()

    # === Collapsed Bubble UI ===

    def _build_collapsed_ui(self):
        size = self.BUBBLE_SIZE
        self._collapsed_frame = tk.Frame(self.root, bg=self.COLORS["bg"])
        self._collapsed_frame.pack()

        self._bubble_canvas = tk.Canvas(
            self._collapsed_frame, width=size, height=size,
            bg=self.COLORS["bg"], highlightthickness=0
        )
        self._bubble_canvas.pack()

        self._bubble_bg = self._bubble_canvas.create_oval(
            3, 3, size - 3, size - 3,
            outline=self.COLORS["ring_bg"], width=2, fill=self.COLORS["bg"]
        )
        self._bubble_arc = self._bubble_canvas.create_arc(
            3, 3, size - 3, size - 3,
            start=90, extent=-360, style="arc",
            outline=self._current_accent, width=2.5
        )
        mins, secs = divmod(self.time_left, 60)
        self._bubble_time = self._bubble_canvas.create_text(
            size // 2, size // 2,
            text=f"{mins:02d}:{secs:02d}", fill=self.COLORS["work_text"],
            font=("Consolas", 11, "bold")
        )

    # === Hover Collapse/Expand ===

    def _on_mouse_enter(self, event=None):
        if self._hover_debounce_id:
            self.root.after_cancel(self._hover_debounce_id)
            self._hover_debounce_id = None
        if self._is_collapsed and not self._is_animating:
            self._expand()

    def _on_mouse_leave(self, event=None):
        if event and self.root.winfo_containing(event.x_root, event.y_root):
            return
        if self._hover_debounce_id:
            self.root.after_cancel(self._hover_debounce_id)
        self._hover_debounce_id = self.root.after(600, self._do_collapse)

    def _do_collapse(self):
        self._hover_debounce_id = None
        if not self._is_collapsed and not self._is_animating:
            self._collapse()

    def _collapse(self):
        self._is_animating = True
        self._play_bubble()
        self._update_bubble_display()
        steps = 8
        step = [0]

        def anim():
            t = step[0] / steps
            t_ease = 1 - (1 - t) ** 2
            if t_ease < 0.5:
                alpha = max(0.3, 0.95 - t_ease * 1.3)
                self.root.attributes("-alpha", alpha)
            else:
                if step[0] == steps // 2 + 1:
                    self._expanded_frame.pack_forget()
                    self._collapsed_frame.pack()
                    self.root.update_idletasks()
                alpha = min(0.95, 0.3 + (t_ease - 0.5) * 1.3)
                self.root.attributes("-alpha", alpha)
            step[0] += 1
            if step[0] <= steps:
                self.root.after(25, anim)
            else:
                self.root.attributes("-alpha", 0.95)
                self._is_collapsed = True
                self._is_animating = False
                x, y = self.root.winfo_pointerxy()
                if self.root.winfo_containing(x, y):
                    self._expand()

        anim()

    def _expand(self):
        self._is_animating = True
        steps = 8
        step = [0]

        def anim():
            t = step[0] / steps
            t_ease = 1 - (1 - t) ** 2
            if t_ease < 0.5:
                alpha = max(0.3, 0.95 - t_ease * 1.3)
                self.root.attributes("-alpha", alpha)
            else:
                if step[0] == steps // 2 + 1:
                    self._collapsed_frame.pack_forget()
                    self._expanded_frame.pack()
                    self.root.update_idletasks()
                alpha = min(0.95, 0.3 + (t_ease - 0.5) * 1.3)
                self.root.attributes("-alpha", alpha)
            step[0] += 1
            if step[0] <= steps:
                self.root.after(25, anim)
            else:
                self.root.attributes("-alpha", 0.95)
                self._is_collapsed = False
                self._is_animating = False
                x, y = self.root.winfo_pointerxy()
                if not self.root.winfo_containing(x, y):
                    self._do_collapse()

        anim()

    def _update_bubble_display(self):
        mins, secs = divmod(self.time_left, 60)
        self._bubble_canvas.itemconfig(self._bubble_time, text=f"{mins:02d}:{secs:02d}")
        fraction = (self.time_left / self.total_time) if self.total_time > 0 else 1.0
        self._bubble_canvas.itemconfig(self._bubble_arc, extent=-360 * fraction)
        self._bubble_canvas.itemconfig(self._bubble_arc, outline=self._current_accent)
        if self.mode == "work":
            self._bubble_canvas.itemconfig(self._bubble_time, fill=self.COLORS["work_text"])
        elif self.mode == "short_break":
            self._bubble_canvas.itemconfig(self._bubble_time, fill=self.COLORS["short_break_accent"])
        else:
            self._bubble_canvas.itemconfig(self._bubble_time, fill=self.COLORS["long_break_accent"])

    # === Font/Size Calculations ===

    def _font_sizes(self):
        return {
            "mode":      max(8,  int(9  * self.scale)),
            "time":      max(14, int(26 * self.scale)),
            "dots":      max(7,  int(9  * self.scale)),
            "hint":      max(7,  int(8  * self.scale)),
            "pad_x":     max(6,  int(12 * self.scale)),
            "pad_y":     max(4,  int(8  * self.scale)),
            "ring_size": max(80, int(160 * self.scale)),
            "ring_pad":  max(5,  int(10 * self.scale)),
            "ring_width":max(2,  int(5  * self.scale)),
            "hint_pad":  max(2,  int(4  * self.scale)),
        }

    def _update_scale(self):
        sizes = self._font_sizes()
        ring_size = sizes["ring_size"]
        pad = sizes["ring_pad"]

        self.canvas.config(width=ring_size, height=ring_size)
        self.canvas.coords(self._ring_bg, pad, pad, ring_size - pad, ring_size - pad)
        self.canvas.coords(self._ring_arc, pad, pad, ring_size - pad, ring_size - pad)
        self.canvas.itemconfig(self._ring_bg, width=sizes["ring_width"])
        self.canvas.itemconfig(self._ring_arc, width=sizes["ring_width"])

        self.canvas.coords(self._mode_text, ring_size // 2, ring_size * 0.28)
        self.canvas.itemconfig(self._mode_text, font=("Segoe UI", sizes["mode"], "bold"))

        self.canvas.coords(self._time_text, ring_size // 2, ring_size * 0.50)
        self.canvas.itemconfig(self._time_text, font=("Consolas", sizes["time"], "bold"))

        self.canvas.coords(self._dots_text, ring_size // 2, ring_size * 0.72)
        self.canvas.itemconfig(self._dots_text, font=("Segoe UI", sizes["dots"]))

        self.hint_label.config(font=("Segoe UI", sizes["hint"]))
        self._expanded_frame.config(padx=sizes["pad_x"], pady=sizes["pad_y"])
        self.root.update_idletasks()

    # === Progress Ring ===

    def _update_ring(self):
        fraction = (self.time_left / self.total_time) if self.total_time > 0 else 1.0
        self.canvas.itemconfig(self._ring_arc, extent=-360 * fraction)
        if self._is_collapsed:
            self._update_bubble_display()
        # Taskbar progress
        if self._hwnd and self.running:
            self._taskbar.set_progress(self._hwnd, self.time_left, self.total_time)

    # === Color Transitions ===

    def _fade_to_color(self, target_color, steps=12):
        if self._color_fade_id:
            self.root.after_cancel(self._color_fade_id)
        start_r, start_g, start_b = self._hex_to_rgb(self._current_accent)
        end_r, end_g, end_b = self._hex_to_rgb(target_color)
        step = [0]

        def fade_step():
            t = step[0] / steps
            r = max(0, min(255, int(start_r + (end_r - start_r) * t)))
            g = max(0, min(255, int(start_g + (end_g - start_g) * t)))
            b = max(0, min(255, int(start_b + (end_b - start_b) * t)))
            color = f"#{r:02x}{g:02x}{b:02x}"
            self._current_accent = color
            self.canvas.itemconfig(self._ring_arc, outline=color)
            self.canvas.itemconfig(self._mode_text, fill=color)
            self._bubble_canvas.itemconfig(self._bubble_arc, outline=color)
            step[0] += 1
            if step[0] <= steps:
                self._color_fade_id = self.root.after(30, fade_step)
            else:
                self._current_accent = target_color
                self._color_fade_id = None

        fade_step()

    @staticmethod
    def _hex_to_rgb(hex_color):
        h = hex_color.lstrip("#")
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)

    # === Presets Menu ===

    def _show_presets(self, event=None):
        today_count = self._read_today_stats()
        menu = tk.Menu(self.root, tearoff=0, bg="#2a2a4a", fg="#ffffff",
                       activebackground="#e94560", activeforeground="#ffffff",
                       font=("Segoe UI", max(8, int(9 * self.scale))))

        menu.add_command(
            label=f"Today: {today_count} session{'s' if today_count != 1 else ''}",
            state="disabled"
        )
        menu.add_separator()

        for name, (work, brk) in PRESETS.items():
            check = " *" if work == self.work_duration and brk == self.break_duration else ""
            menu.add_command(
                label=f"{name}{check}",
                command=lambda w=work, b=brk: self._set_preset(w, b)
            )

        menu.add_command(label="Custom...", command=self._show_custom_dialog)
        menu.add_separator()
        menu.add_command(label="Quit", command=self.quit)

        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _show_custom_dialog(self):
        dialog = tk.Toplevel(self.root)
        dialog.title("Custom Preset")
        dialog.configure(bg=self.COLORS["bg"])
        dialog.resizable(False, False)
        dialog.attributes("-topmost", True)
        dialog.grab_set()

        pad = 16
        fg = "#ffffff"
        dim = self.COLORS["dim"]
        accent = self.COLORS["work_accent"]

        tk.Label(dialog, text="Custom Timer", bg=self.COLORS["bg"], fg=fg,
                 font=("Segoe UI", 11, "bold")).grid(row=0, column=0, columnspan=2,
                 pady=(pad, 8), padx=pad)

        tk.Label(dialog, text="Work (min):", bg=self.COLORS["bg"], fg=dim,
                 font=("Segoe UI", 9)).grid(row=1, column=0, sticky="e", padx=(pad, 6), pady=4)
        work_var = tk.StringVar(value=str(self.work_duration // 60))
        work_entry = tk.Entry(dialog, textvariable=work_var, width=5,
                              bg="#2a2a4a", fg=fg, insertbackground=fg,
                              relief="flat", font=("Consolas", 10))
        work_entry.grid(row=1, column=1, sticky="w", padx=(0, pad), pady=4)

        tk.Label(dialog, text="Break (min):", bg=self.COLORS["bg"], fg=dim,
                 font=("Segoe UI", 9)).grid(row=2, column=0, sticky="e", padx=(pad, 6), pady=4)
        break_var = tk.StringVar(value=str(self.break_duration // 60))
        break_entry = tk.Entry(dialog, textvariable=break_var, width=5,
                               bg="#2a2a4a", fg=fg, insertbackground=fg,
                               relief="flat", font=("Consolas", 10))
        break_entry.grid(row=2, column=1, sticky="w", padx=(0, pad), pady=4)

        err_label = tk.Label(dialog, text="", bg=self.COLORS["bg"], fg=accent,
                             font=("Segoe UI", 8))
        err_label.grid(row=3, column=0, columnspan=2)

        def apply():
            try:
                w = int(work_var.get())
                b = int(break_var.get())
                if w < 1 or b < 1:
                    raise ValueError
            except ValueError:
                err_label.config(text="Enter whole numbers ≥ 1")
                return
            dialog.destroy()
            self._set_preset(w * 60, b * 60)

        btn = tk.Button(dialog, text="Set", command=apply,
                        bg=accent, fg=fg, relief="flat",
                        font=("Segoe UI", 9, "bold"), padx=14, pady=4,
                        activebackground="#c73850", activeforeground=fg,
                        cursor="hand2")
        btn.grid(row=4, column=0, columnspan=2, pady=(8, pad))

        dialog.bind("<Return>", lambda e: apply())
        dialog.bind("<Escape>", lambda e: dialog.destroy())

        # Center over the main window
        dialog.update_idletasks()
        rx = self.root.winfo_x() + (self.root.winfo_width()  - dialog.winfo_width())  // 2
        ry = self.root.winfo_y() + (self.root.winfo_height() - dialog.winfo_height()) // 2
        dialog.geometry(f"+{rx}+{ry}")
        work_entry.focus_set()
        work_entry.select_range(0, "end")

    def _set_preset(self, work, brk):
        self.work_duration = work
        self.break_duration = brk
        self.pause()
        self.mode = "work"
        self.time_left = self.work_duration
        self.total_time = self.work_duration
        self._tick_base = self.work_duration
        self.sessions_completed = 0
        self._update_display()
        self._update_mode_label()
        self._update_ring()
        self._update_dots()
        self.hint_label.config(text="double-click: start")
        if self._hwnd:
            self._taskbar.clear(self._hwnd)

    # === Sound ===

    def _generate_chime(self):
        try:
            sample_rate = 44100
            frequencies = [523.25, 659.25, 783.99]
            tone_duration = 0.5
            gap = 0.15
            samples = []
            for freq in frequencies:
                num_samples = int(tone_duration * sample_rate)
                for s in range(num_samples):
                    t = s / sample_rate
                    envelope = math.sin(math.pi * t / tone_duration)
                    val = envelope * (
                        0.6 * math.sin(2 * math.pi * freq * t) +
                        0.25 * math.sin(2 * math.pi * freq * 2 * t) * math.exp(-3 * t) +
                        0.15 * math.sin(2 * math.pi * freq * 3 * t) * math.exp(-5 * t)
                    )
                    samples.append(val)
                samples.extend([0.0] * int(gap * sample_rate))
            max_val = max(abs(s) for s in samples) or 1
            pcm = [int((s / max_val) * 0.7 * 32767) for s in samples]
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".wav")
            path = tmp.name
            tmp.close()
            with wave.open(path, "w") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(sample_rate)
                wf.writeframes(struct.pack(f"<{len(pcm)}h", *pcm))
            return path
        except Exception:
            return None

    def _generate_bubble_sound(self):
        try:
            sample_rate = 44100
            duration = 0.12
            num_samples = int(duration * sample_rate)
            samples = []
            for s in range(num_samples):
                t = s / sample_rate
                freq = 800 * math.exp(-20 * t)
                envelope = math.exp(-30 * t)
                val = envelope * math.sin(2 * math.pi * freq * t)
                samples.append(val)
            max_val = max(abs(s) for s in samples) or 1
            pcm = [int((s / max_val) * 0.4 * 32767) for s in samples]
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".wav")
            path = tmp.name
            tmp.close()
            with wave.open(path, "w") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(sample_rate)
                wf.writeframes(struct.pack(f"<{len(pcm)}h", *pcm))
            return path
        except Exception:
            return None

    def _play_chime(self):
        def play():
            try:
                if self.chime_path and os.path.exists(self.chime_path):
                    winsound.PlaySound(self.chime_path, winsound.SND_FILENAME)
                else:
                    winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
            except Exception:
                pass
        threading.Thread(target=play, daemon=True).start()

    def _play_bubble(self):
        def play():
            try:
                if self.bubble_path and os.path.exists(self.bubble_path):
                    winsound.PlaySound(self.bubble_path, winsound.SND_FILENAME)
            except Exception:
                pass
        threading.Thread(target=play, daemon=True).start()

    # === Window Controls ===

    def _apply_rounded_corners(self):
        try:
            from ctypes import windll, byref, sizeof, c_int
            DWMWA_WINDOW_CORNER_PREFERENCE = 33
            hwnd = windll.user32.GetParent(self.root.winfo_id())
            preference = c_int(2)
            windll.dwmapi.DwmSetWindowAttribute(
                hwnd, DWMWA_WINDOW_CORNER_PREFERENCE,
                byref(preference), sizeof(preference)
            )
        except Exception:
            pass

    def on_scroll(self, event):
        if self._is_collapsed:
            return
        if event.delta > 0:
            self.scale = min(self.MAX_SCALE, self.scale + self.SCALE_STEP)
        else:
            self.scale = max(self.MIN_SCALE, self.scale - self.SCALE_STEP)
        self._update_scale()

    def start_drag(self, event):
        self._drag_x = event.x
        self._drag_y = event.y

    def on_drag(self, event):
        x = self.root.winfo_x() + event.x - self._drag_x
        y = self.root.winfo_y() + event.y - self._drag_y
        self.root.geometry(f"+{x}+{y}")

    # === Timer Logic ===

    def toggle(self, event=None):
        if self.running:
            self.pause()
        else:
            self.start()

    def start(self):
        if self.after_id:
            return
        self.running = True
        self._tick_start = time.monotonic()
        self._tick_base = self.time_left
        self.hint_label.config(text="double-click: pause")
        if self._hwnd:
            self._taskbar.set_progress(self._hwnd, self.time_left, self.total_time)
        self._tick()

    def pause(self):
        self.running = False
        self._tick_start = None
        self.hint_label.config(text="double-click: resume")
        if self.after_id:
            self.root.after_cancel(self.after_id)
            self.after_id = None
        if self._hwnd:
            self._taskbar.set_paused(self._hwnd)

    def _tick(self):
        if not self.running:
            return
        elapsed = int(time.monotonic() - self._tick_start)
        self.time_left = max(0, self._tick_base - elapsed)
        if self.time_left > 0:
            self._update_display()
            self._update_ring()
            if self._is_collapsed:
                self._update_bubble_display()
            self.after_id = self.root.after(250, self._tick)
        else:
            self.after_id = None
            self._on_timer_complete()

    def _on_timer_complete(self):
        self.running = False
        self._tick_start = None
        self._play_chime()

        if self.mode == "work":
            self._log_session()
            self.sessions_completed += 1
            self._update_dots()
            if self.sessions_completed % self.SESSIONS_BEFORE_LONG == 0:
                self.mode = "long_break"
                self.time_left = self.break_duration * self.LONG_BREAK_MULTIPLIER
                self.total_time = self.time_left
            else:
                self.mode = "short_break"
                self.time_left = self.break_duration
                self.total_time = self.break_duration
        else:
            self.mode = "work"
            self.time_left = self.work_duration
            self.total_time = self.work_duration

        self._tick_base = self.time_left
        self._update_display()
        self._update_mode_label()
        self._update_ring()
        if self._is_collapsed:
            self._update_bubble_display()
        self.hint_label.config(text="double-click: start")
        if self._hwnd:
            self._taskbar.clear(self._hwnd)
        self._save_state()

    # === Display Updates ===

    def _mode_label(self):
        return {"work": "FOCUS", "short_break": "BREAK", "long_break": "LONG BREAK"}[self.mode]

    def _update_display(self):
        mins, secs = divmod(self.time_left, 60)
        self.canvas.itemconfig(self._time_text, text=f"{mins:02d}:{secs:02d}")
        if self.mode == "work":
            self.canvas.itemconfig(self._time_text, fill=self.COLORS["work_text"])
        elif self.mode == "short_break":
            self.canvas.itemconfig(self._time_text, fill=self.COLORS["short_break_accent"])
        else:
            self.canvas.itemconfig(self._time_text, fill=self.COLORS["long_break_accent"])

    def _update_mode_label(self):
        colors = {
            "work":        self.COLORS["work_accent"],
            "short_break": self.COLORS["short_break_accent"],
            "long_break":  self.COLORS["long_break_accent"],
        }
        self.canvas.itemconfig(self._mode_text, text=self._mode_label())
        self._fade_to_color(colors[self.mode])

    def _dots_string(self):
        completed = self.sessions_completed % self.SESSIONS_BEFORE_LONG
        return " ".join("●" if i < completed else "○" for i in range(self.SESSIONS_BEFORE_LONG))

    def _update_dots(self):
        self.canvas.itemconfig(self._dots_text, text=self._dots_string())

    # === Quit ===

    def quit(self):
        self.pause()
        self._save_state()
        if self._hwnd:
            self._taskbar.clear(self._hwnd)
        for path in (self.chime_path, self.bubble_path):
            if path:
                try:
                    os.remove(path)
                except Exception:
                    pass
        try:
            ctypes.WinDLL("kernel32").CloseHandle(self.mutex_handle)
        except Exception:
            pass
        self.root.destroy()


# === Entry Point ===

if __name__ == "__main__":
    mutex_handle = ensure_single_instance()
    PomodoroApp(mutex_handle)
