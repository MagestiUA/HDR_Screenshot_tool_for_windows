"""
Settings window built with customtkinter.

Opens in its own thread so it does not conflict with the pystray message loop.
Call `open_settings(config_dict, on_save_callback)` from anywhere.
"""
import os
import sys
import threading
import tkinter as tk
from tkinter import filedialog
from typing import Callable

import customtkinter as ctk

import autostart
import config as cfg

_BASE_RES = sys._MEIPASS if getattr(sys, 'frozen', False) else os.path.dirname(os.path.abspath(__file__))
_ICON_PATH = os.path.join(_BASE_RES, "app.ico")

# ── Appearance ────────────────────────────────────────────────────────────────
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("dark-blue")

_TM_MODES = {
    "Windows (OBS-style)":   "windows",
    "ACES (filmic) [test]":  "aces",
    "Reinhard (photo) [test]": "reinhard",
}

_TM_MODES_R = {v: k for k, v in _TM_MODES.items()}


def _format_hotkey(hk: str) -> str:
    """'<ctrl>+<shift>+h'  →  'Ctrl+Shift+H'"""
    parts = hk.split("+")
    out = []
    for p in parts:
        p = p.strip()
        if p.startswith("<") and p.endswith(">"):
            out.append(p[1:-1].capitalize())
        else:
            out.append(p.upper())
    return "+".join(out)


class _HotkeyCapture:
    """
    Listens for the next key combination via pynput and returns it as a
    '<mod>+<mod>+key' string.  Runs in a background thread.

    Logic:
      • Modifier keys (Ctrl/Shift/Alt/Cmd) accumulate in _pressed.
      • The combo is snapped the moment a *non-modifier* key is pressed —
        at that point we know the full combination.
      • We then wait until ALL keys are physically released before calling
        on_done, so pynput's GlobalHotKeys does not see the tail of the
        key-down event and accidentally trigger the new hotkey immediately.
    """

    def __init__(self, on_done: Callable[[str], None]) -> None:
        self._on_done = on_done
        self._pressed: set = set()
        self._combo: str | None = None   # snapped when the trigger key is pressed
        self._listener = None

    def start(self) -> None:
        from pynput import keyboard as kb

        _MODIFIERS = {
            kb.Key.ctrl,    kb.Key.ctrl_l,  kb.Key.ctrl_r,
            kb.Key.shift,   kb.Key.shift_l, kb.Key.shift_r,
            kb.Key.alt,     kb.Key.alt_l,   kb.Key.alt_r,
            kb.Key.cmd,     kb.Key.cmd_l,   kb.Key.cmd_r,
        }

        def on_press(key):
            self._pressed.add(key)
            # Snap the combo on the first non-modifier key press
            if key not in _MODIFIERS and self._combo is None:
                self._combo = self._build_combo()

        def on_release(key):
            self._pressed.discard(key)
            # Fire only after all keys are physically up AND we have a combo
            if not self._pressed and self._combo:
                self._listener.stop()
                self._on_done(self._combo)

        self._listener = kb.Listener(on_press=on_press, on_release=on_release)
        self._listener.start()

    def _build_combo(self) -> str:
        from pynput import keyboard as kb

        _MOD_MAP = {
            kb.Key.ctrl:    "<ctrl>",
            kb.Key.ctrl_l:  "<ctrl>",
            kb.Key.ctrl_r:  "<ctrl>",
            kb.Key.shift:   "<shift>",
            kb.Key.shift_l: "<shift>",
            kb.Key.shift_r: "<shift>",
            kb.Key.alt:     "<alt>",
            kb.Key.alt_l:   "<alt>",
            kb.Key.alt_r:   "<alt>",
            kb.Key.cmd:     "<cmd>",
            kb.Key.cmd_l:   "<cmd>",
            kb.Key.cmd_r:   "<cmd>",
        }

        mods_seen: set[str] = set()
        chars: list[str] = []

        for k in self._pressed:
            mod = _MOD_MAP.get(k)
            if mod:
                mods_seen.add(mod)
            elif hasattr(k, "char") and k.char:
                c = k.char
                # Ctrl held → pynput gives control-char (e.g. Ctrl+S = \x13).
                # Normalise back to the printable letter.
                if len(c) == 1 and ord(c) < 32:
                    c = chr(ord(c) + 96)   # \x13 (19) → chr(115) = 's'
                chars.append(c.lower())
            elif hasattr(k, "name"):
                chars.append(f"<{k.name}>")

        # Order: ctrl, shift, alt, cmd, then character
        ordered_mods = [m for m in ("<ctrl>", "<shift>", "<alt>", "<cmd>") if m in mods_seen]
        parts = ordered_mods + chars
        return "+".join(parts) if parts else ""


# ── Settings window ───────────────────────────────────────────────────────────

class SettingsWindow(ctk.CTk):
    def __init__(self, current_cfg: dict, on_save: Callable[[dict], None]) -> None:
        super().__init__()

        self._cfg    = dict(current_cfg)
        self._on_save = on_save

        self.title("HDR Screenshot — Settings")
        self.resizable(False, False)
        self.geometry("480x375")
        self.protocol("WM_DELETE_WINDOW", self.destroy)

        if os.path.exists(_ICON_PATH):
            try:
                self.iconbitmap(_ICON_PATH)
            except Exception:
                pass

        self._build_ui()

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        pad = {"padx": 16, "pady": 6}

        # ── Save folder ───────────────────────────────────────────────────────
        ctk.CTkLabel(self, text="Save folder", anchor="w").grid(
            row=0, column=0, sticky="w", **pad)

        self._folder_var = tk.StringVar(value=self._cfg["save_folder"])
        folder_entry = ctk.CTkEntry(self, textvariable=self._folder_var, width=300)
        folder_entry.grid(row=0, column=1, sticky="ew", padx=(0, 4), pady=6)

        ctk.CTkButton(self, text="Browse…", width=80,
                      command=self._browse_folder).grid(
            row=0, column=2, padx=(0, 16), pady=6)

        # ── Tone mapping ──────────────────────────────────────────────────────
        ctk.CTkLabel(self, text="Tone mapping", anchor="w").grid(
            row=1, column=0, sticky="w", **pad)

        self._tm_var = tk.StringVar(value=_TM_MODES_R[self._cfg["tonemapping"]])
        ctk.CTkOptionMenu(self, variable=self._tm_var,
                          values=list(_TM_MODES.keys()), width=300).grid(
            row=1, column=1, sticky="ew", padx=(0, 4), pady=6)

        # ── SDR brightness ────────────────────────────────────────────────────
        ctk.CTkLabel(self, text="SDR brightness (nits)", anchor="w").grid(
            row=2, column=0, sticky="w", **pad)

        nits_frame = ctk.CTkFrame(self, fg_color="transparent")
        nits_frame.grid(row=2, column=1, columnspan=2, sticky="ew",
                        padx=(0, 4), pady=6)

        self._nits_var = tk.IntVar(
            value=int(self._cfg.get("sdr_white_nits", 250))
        )
        self._nits_lbl = ctk.CTkLabel(
            nits_frame,
            text="%d" % self._nits_var.get(),
            width=40, anchor="e",
        )
        self._nits_lbl.pack(side="right")

        def _on_nits(val):
            v = int(round(float(val) / 10) * 10)
            self._nits_var.set(v)
            self._nits_lbl.configure(text="%d" % v)

        ctk.CTkSlider(
            nits_frame,
            from_=160, to=480,
            variable=self._nits_var,
            command=_on_nits,
            width=260,
        ).pack(side="left", fill="x", expand=True)

        # ── Hotkeys ───────────────────────────────────────────────────────────
        ctk.CTkLabel(self, text="Hotkey — Full screen", anchor="w").grid(
            row=3, column=0, sticky="w", **pad)

        self._hk_full_var = tk.StringVar(
            value=_format_hotkey(self._cfg["hotkey_fullscreen"]))
        self._hk_full_lbl = ctk.CTkLabel(self, textvariable=self._hk_full_var,
                                          anchor="w", width=200)
        self._hk_full_lbl.grid(row=3, column=1, sticky="w", padx=(0, 4), pady=6)

        ctk.CTkButton(self, text="Change", width=80,
                      command=lambda: self._start_capture("fullscreen")).grid(
            row=3, column=2, padx=(0, 16), pady=6)

        ctk.CTkLabel(self, text="Hotkey — Region", anchor="w").grid(
            row=4, column=0, sticky="w", **pad)

        self._hk_region_var = tk.StringVar(
            value=_format_hotkey(self._cfg["hotkey_region"]))
        self._hk_region_lbl = ctk.CTkLabel(self, textvariable=self._hk_region_var,
                                             anchor="w", width=200)
        self._hk_region_lbl.grid(row=4, column=1, sticky="w", padx=(0, 4), pady=6)

        ctk.CTkButton(self, text="Change", width=80,
                      command=lambda: self._start_capture("region")).grid(
            row=4, column=2, padx=(0, 16), pady=6)

        # ── Start with Windows ────────────────────────────────────────────────
        self._autostart_var = tk.BooleanVar(value=autostart.is_enabled())
        ctk.CTkCheckBox(self, text="Start with Windows",
                        variable=self._autostart_var).grid(
            row=5, column=0, columnspan=3, sticky="w", padx=16, pady=(8, 0))

        # ── Buttons ───────────────────────────────────────────────────────────
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.grid(row=6, column=0, columnspan=3, pady=(16, 16))

        ctk.CTkButton(btn_frame, text="Save", width=120,
                      command=self._save).pack(side="left", padx=8)
        ctk.CTkButton(btn_frame, text="Cancel", width=120, fg_color="gray40",
                      command=self.destroy).pack(side="left", padx=8)

        self.columnconfigure(1, weight=1)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _browse_folder(self) -> None:
        folder = filedialog.askdirectory(
            initialdir=self._folder_var.get(),
            title="Select save folder",
            parent=self,
        )
        if folder:
            self._folder_var.set(folder)

    def _start_capture(self, which: str) -> None:
        """Begin listening for a new hotkey combo for *which* ('fullscreen'|'region')."""
        var = self._hk_full_var if which == "fullscreen" else self._hk_region_var
        var.set("Press combo…")
        self.update_idletasks()

        def on_done(combo: str) -> None:
            display = _format_hotkey(combo)
            # Schedule UI update on the Tk main thread
            self.after(0, lambda: var.set(display))
            if which == "fullscreen":
                self._cfg["hotkey_fullscreen"] = combo
            else:
                self._cfg["hotkey_region"] = combo

        capture = _HotkeyCapture(on_done)
        threading.Thread(target=capture.start, daemon=True).start()

    def _save(self) -> None:
        self._cfg["save_folder"]    = self._folder_var.get()
        self._cfg["save_mode"]      = "sdr"
        self._cfg["tonemapping"]    = _TM_MODES[self._tm_var.get()]
        self._cfg["sdr_white_nits"] = int(self._nits_var.get())
        cfg.save(self._cfg)

        if self._autostart_var.get():
            autostart.enable()
        else:
            autostart.disable()

        self._on_save(self._cfg)
        self.destroy()


# ── Public entry point ────────────────────────────────────────────────────────

def open_settings(current_cfg: dict, on_save: Callable[[dict], None]) -> None:
    """
    Open the settings window in a dedicated daemon thread.

    Designed to be called from a pystray menu callback (which runs in the
    pystray thread) without blocking it.
    """
    def _run():
        win = SettingsWindow(current_cfg, on_save)
        win.mainloop()

    threading.Thread(target=_run, daemon=True).start()
