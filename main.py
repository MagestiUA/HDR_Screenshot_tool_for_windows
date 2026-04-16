"""
HDR Screenshot Tool — main entry point.

Architecture
────────────
  • pystray runs the tray icon in the *main* thread (required on Windows).
    Its `setup` callback fires once the icon is ready; everything else starts
    from there.

  • pynput GlobalHotKeys runs in a background thread.  It is restarted
    whenever the user saves new hotkeys in Settings.

  • Each screenshot job runs in a short-lived daemon thread so the hotkey
    thread is never blocked.

  • The region-selection overlay (tkinter) runs in its own daemon thread so
    it can own a fresh Tk root without conflicting with pystray.
"""
import ctypes
import os
import sys
import threading
from datetime import datetime

import pystray
from PIL import Image

import capture
import clipboard_win
import config as cfg
import hdr_detect
import notification
import settings_window
import tonemapping
import overlay as overlay_mod
from capture import MonitorInfo

# ── Single-instance guard ─────────────────────────────────────────────────────

_MUTEX_NAME = "Global\\HDRScreenshotToolMutex"
_mutex_handle = None   # тримаємо посилання щоб GC не прибрав


def _ensure_single_instance() -> None:
    global _mutex_handle
    _mutex_handle = ctypes.windll.kernel32.CreateMutexW(None, False, _MUTEX_NAME)
    if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        ctypes.windll.user32.MessageBoxW(
            0,
            "HDR Screenshot Tool вже запущено.\nЗнайдіть іконку в системному треї.",
            "HDR Screenshot Tool",
            0x40 | 0x1000,  # MB_ICONINFORMATION | MB_SETFOREGROUND
        )
        sys.exit(0)


# ── State ─────────────────────────────────────────────────────────────────────
_config: dict = cfg.load()
_config_lock  = threading.Lock()

_hotkey_listener      = None
_hotkey_listener_lock = threading.Lock()

_capture_lock = threading.Lock()   # prevent overlapping captures

_icon: pystray.Icon | None = None  # set in main()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _notify(message: str, title: str = "HDR Screenshot",
            image_path: str | None = None) -> None:
    """Показує toast-сповіщення; при наявності image_path — з мініатюрою та кліком."""
    notification.show(title, message, image_path=image_path, fallback_icon=_icon)


def _hdr_label(monitor: MonitorInfo) -> str:
    """Return '[HDR]' or '[SDR]' based on the monitor's current mode."""
    try:
        return "[HDR]" if hdr_detect.is_hdr_on_monitor(monitor.idx) else "[SDR]"
    except Exception:
        return ""


# ── Screenshot workflow ───────────────────────────────────────────────────────

def _process_and_save(
    frame,
    monitor: MonitorInfo,
    save_folder: str,
    mode: str,
    tm_method: str,
    sdr_white_nits: float = 250.0,
) -> tuple[Image.Image, str]:
    """Tone-map / save files per *mode*.
    Returns (sdr_image, notify_path) where *notify_path* is the best path to
    show in the toast (SDR PNG preferred; HDR PNG as fallback)."""
    ts = _timestamp()
    os.makedirs(save_folder, exist_ok=True)

    sdr_img: Image.Image | None = None
    sdr_path: str | None = None
    hdr_path: str | None = None

    if mode in ("sdr", "both"):
        sdr_img = tonemapping.to_sdr(
            frame, method=tm_method, sdr_white_nits=sdr_white_nits
        )
        sdr_path = os.path.join(save_folder, f"hdr_sdr_{ts}.png")
        sdr_img.save(sdr_path, format="PNG")

    if mode in ("hdr", "both"):
        hdr_path = os.path.join(save_folder, f"hdr_raw_{ts}.png")
        tonemapping.save_hdr_png(frame, hdr_path)

    if sdr_img is None:
        sdr_img = tonemapping.to_sdr(
            frame, method=tm_method, sdr_white_nits=sdr_white_nits
        )

    notify_path = sdr_path or hdr_path or ""
    return sdr_img, notify_path


def _do_fullscreen() -> None:
    """Capture the monitor under the cursor, save, copy to clipboard."""
    if not _capture_lock.acquire(blocking=False):
        return
    try:
        with _config_lock:
            c = dict(_config)

        mon = capture.cursor_monitor()
        frame = capture.grab(mon)
        if frame is None:
            _notify("Capture failed — is dxcam installed?", "Error")
            return

        sdr_img, notify_path = _process_and_save(
            frame, mon, c["save_folder"], c["save_mode"], c["tonemapping"],
            sdr_white_nits=c.get("sdr_white_nits", 250),
        )

        clipboard_win.copy_image(sdr_img)
        label = _hdr_label(mon)
        _notify(
            f"{label} Monitor {mon.idx} saved → {c['save_folder']}",
            image_path=notify_path,
        )

    except Exception as exc:
        _notify(f"Error: {exc}", "Error")
    finally:
        _capture_lock.release()


def _do_region() -> None:
    """Capture full frame, show region overlay on correct monitor, crop, save."""
    if not _capture_lock.acquire(blocking=False):
        return
    try:
        with _config_lock:
            c = dict(_config)

        mon = capture.cursor_monitor()
        frame = capture.grab(mon)
        if frame is None:
            _notify("Capture failed — is dxcam installed?", "Error")
            return

        preview = tonemapping.to_sdr(
            frame, method=c["tonemapping"],
            sdr_white_nits=c.get("sdr_white_nits", 250),
        )
        region  = overlay_mod.select_region(preview, mon)

        if region is None:
            return                          # user cancelled

        x1, y1, x2, y2 = region
        cropped = frame[y1:y2, x1:x2]

        sdr_img, notify_path = _process_and_save(
            cropped, mon, c["save_folder"], c["save_mode"], c["tonemapping"],
            sdr_white_nits=c.get("sdr_white_nits", 250),
        )

        clipboard_win.copy_image(sdr_img)
        label = _hdr_label(mon)
        _notify(
            f"{label} Region saved → {c['save_folder']}",
            image_path=notify_path,
        )

    except Exception as exc:
        _notify(f"Error: {exc}", "Error")
    finally:
        _capture_lock.release()


# ── Hotkey management ─────────────────────────────────────────────────────────

def _start_hotkey_listener() -> None:
    global _hotkey_listener

    with _config_lock:
        hk_full   = _config["hotkey_fullscreen"]
        hk_region = _config["hotkey_region"]

    from pynput import keyboard

    hotkeys = {
        hk_full:   lambda: threading.Thread(target=_do_fullscreen, daemon=True).start(),
        hk_region: lambda: threading.Thread(target=_do_region,     daemon=True).start(),
    }

    with _hotkey_listener_lock:
        if _hotkey_listener:
            _hotkey_listener.stop()
        try:
            listener = keyboard.GlobalHotKeys(hotkeys)
            listener.start()
            _hotkey_listener = listener
        except Exception as exc:
            _notify(f"Hotkey registration failed: {exc}", "Error")


def _restart_hotkeys_after_save(new_cfg: dict) -> None:
    global _config
    with _config_lock:
        _config = new_cfg
    _start_hotkey_listener()


# ── Tray icon ─────────────────────────────────────────────────────────────────

def _load_tray_icon() -> Image.Image:
    base = sys._MEIPASS if getattr(sys, 'frozen', False) else os.path.dirname(os.path.abspath(__file__))
    icon_path = os.path.join(base, "app.ico")
    if os.path.exists(icon_path):
        return Image.open(icon_path)
    return Image.new("RGB", (64, 64), color=(30, 30, 60))


def _on_settings(_icon, _item) -> None:
    with _config_lock:
        current = dict(_config)
    settings_window.open_settings(current, _restart_hotkeys_after_save)


def _on_quit(icon, _item) -> None:
    with _hotkey_listener_lock:
        if _hotkey_listener:
            _hotkey_listener.stop()
    icon.stop()


def _setup(icon: pystray.Icon) -> None:
    icon.visible = True
    _start_hotkey_listener()


def main() -> None:
    global _icon

    _ensure_single_instance()

    menu = pystray.Menu(
        pystray.MenuItem("Settings…", _on_settings),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit", _on_quit),
    )

    _icon = pystray.Icon(
        name="HDRScreenshot",
        icon=_load_tray_icon(),
        title="HDR Screenshot",
        menu=menu,
    )

    _icon.run(setup=_setup)


if __name__ == "__main__":
    main()
