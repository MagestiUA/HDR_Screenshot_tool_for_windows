"""Config management — loads/saves config.json in %LOCALAPPDATA%\HDRScreenshotTool\."""
import json
import os
import sys

# Exe: %LOCALAPPDATA%\HDRScreenshotTool\ — завжди є права запису,
#      незалежно від того де лежить exe (Program Files тощо).
# Вихідний код: поруч зі скриптом — зручно для розробки.
if getattr(sys, 'frozen', False):
    _BASE_DIR = os.path.join(
        os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
        "HDRScreenshotTool",
    )
    os.makedirs(_BASE_DIR, exist_ok=True)
else:
    _BASE_DIR = os.path.dirname(os.path.abspath(__file__))

CONFIG_PATH = os.path.join(_BASE_DIR, "config.json")

DEFAULTS: dict = {
    "save_folder": os.path.join(os.path.expanduser("~"), "Pictures", "HDRScreenshots"),
    "save_mode": "sdr",           # фіксовано sdr; hdr/both — в наступних версіях
    "tonemapping": "windows",     # "windows" (рекомендовано) | "aces" | "reinhard"
    "sdr_white_nits": 250,        # SDR reference white in nits (160–480); higher = darker output
    "hotkey_fullscreen": "<ctrl>+<shift>+h",
    "hotkey_region": "<ctrl>+<shift>+r",
}


def load() -> dict:
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
                saved = json.load(fh)
            return {**DEFAULTS, **saved}
        except Exception:
            pass
    return DEFAULTS.copy()


def save(cfg: dict) -> None:
    os.makedirs(_BASE_DIR, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2, ensure_ascii=False)
