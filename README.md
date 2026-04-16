# HDR Screenshot Tool for Windows

A system tray tool for capturing HDR screenshots on Windows 10/11 with tone mapping and instant clipboard copy.

![Windows 11](https://img.shields.io/badge/Windows-10%2F11-blue)
![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-blue)
![License MIT](https://img.shields.io/badge/license-MIT-green)

---

![Comparison: Snipping Tool vs HDR Screenshot Tool — Death Stranding 2](docs/comparison.png)

## Why

Standard Windows capture tools (Snipping Tool, Win+PrintScreen) grab frames in SDR — HDR highlights are clipped or blown out. This tool captures the raw frame in **FP16 scRGB** directly via the DXGI Desktop Duplication API, then applies tone mapping (the same method used by OBS) and saves a correctly tone-mapped SDR PNG.

Unlike other screenshot tools, it works correctly in all contexts — games, browsers, desktop apps — because it captures at the compositor level via DXGI, not through the GDI/BitBlt pipeline.

## Features

- **Full-screen capture** — configurable hotkey (default `Ctrl+Shift+H`)
- **Region capture** — hotkey `Ctrl+Shift+R`, drag to select a crop area with a live preview overlay
- **HDR → SDR tone mapping** with adjustable SDR white point (in nits)
- **Three tone mapping algorithms:** Windows/OBS-style (recommended), ACES filmic [test], Reinhard [test]
- **Auto-copy to clipboard** after every capture
- **Toast notification** with a screenshot thumbnail
- **System tray** — lives quietly in the notification area
- **Multi-monitor support** — captures the monitor under the cursor, with correct Win32↔DXGI index mapping
- **SDR fallback** — if HDR is not active, falls back to dxcam BGRA capture
- **Start with Windows** — optional autostart, configurable in settings

## Download

> **No Python required** — just download and run.

| File | Description |
|------|-------------|
| [hdr_screenshot_tool.exe](https://github.com/MagestiUA/HDR_Screenshot_tool_for_windows/releases/latest/download/hdr_screenshot_tool.exe) | Standalone executable (Windows 10/11) |

> **Note:** Windows SmartScreen may warn about an unsigned executable. See [Troubleshooting](#troubleshooting) below.

Alternatively, run from source — see [Installation](#installation).

## Troubleshooting

### Windows Smart App Control / SmartScreen

On Windows 11 with **Smart App Control** enabled, the exe may be blocked with a message like _"Smart App Control blocked a potentially unsafe app"_. This is expected behaviour for unsigned executables from unknown publishers.

**Option 1 — disable Smart App Control** (one-time, does not affect overall system security):
> Settings → Privacy & Security → Windows Security → App & Browser Control → Smart App Control → **Off**

**Option 2 — run from source** (no permissions required):
```bash
python main.py
```

**Option 3 — classic SmartScreen** (if a "More info" button appears):
> Click "More info" → "Run anyway"

### HDR capture returns a black or corrupted image

Make sure HDR is actually enabled in Windows display settings for your monitor. The tool detects HDR state per-monitor — if HDR is off, it falls back to standard SDR capture automatically.

### Screenshot looks identical to Snipping Tool output

Your SDR brightness (nits) setting may be too high. Try lowering it to 200–250 nits in Settings. Higher values compress the HDR range more aggressively, making the result look similar to SDR capture.

### Hotkey not working

Another application may have registered the same hotkey globally. Change the hotkey in Settings → Hotkey — Full screen or Hotkey — Region.

## Requirements

- Windows 10 version 1703+ (theoretical) or Windows 11 (tested)
- HDR-capable monitor and GPU with HDR enabled in Windows display settings
- Python 3.11+

## Installation

```bash
git clone https://github.com/MagestiUA/HDR_Screenshot_tool_for_windows.git
cd HDR_Screenshot_tool_for_windows
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

After launch, the icon appears in the system tray. Use the hotkeys or right-click the tray icon to open Settings.

## Settings

Right-click the tray icon → **Settings…**

| Setting | Description |
|---------|-------------|
| Save folder | Where screenshots are saved |
| Tone mapping | Tone mapping algorithm |
| SDR brightness (nits) | SDR white reference level (160–480 nits). Higher = darker output. Recommended: 200–250 |
| Hotkey — Full screen | Hotkey for full-screen capture |
| Hotkey — Region | Hotkey for region capture |
| Start with Windows | Launch automatically on system startup |

Settings are stored in `%LOCALAPPDATA%\HDRScreenshotTool\config.json` — the app works correctly from any location, including `Program Files`, without administrator privileges.

## Build as exe

```bash
pyinstaller --onefile --windowed --icon app.ico --add-data "app.ico;." main.py
```

The executable will be in `dist\main.exe`.

## Project structure

```
main.py              # entry point, tray icon, hotkeys, screenshot workflow
capture.py           # camera pool, Win32↔DXGI monitor index mapping
dxgi_capture/        # custom FP16 capture via DXGI Desktop Duplication
  capture.py         # FP16Capture class, raw vtable D3D11 calls
tonemapping.py       # tone mapping operators, PNG saving
settings_window.py   # settings UI (customtkinter)
overlay.py           # fullscreen region selection overlay (tkinter)
clipboard_win.py     # copy image to Windows clipboard
notification.py      # toast notifications via PowerShell + WinRT
hdr_detect.py        # per-monitor HDR state detection
config.py            # config.json load/save
```

## How it works

1. When a hotkey is pressed, the monitor under the cursor is captured
2. If the monitor is in HDR mode — `FP16Capture` is used (DXGI `R16G16B16A16_FLOAT` format)
3. The resulting float32 BGRA array (scRGB linear; values >1.0 are HDR highlights) is passed to `tonemapping.to_sdr()`
4. The tone-mapped image is saved as PNG, copied to the clipboard, and a toast notification appears with a thumbnail

### Tone mapping — Windows/OBS mode

Divides each pixel by `sdr_white_nits / 80`, clips to [0, 1], then applies sRGB gamma encoding. This maps SDR UI content to exactly [0, 1] while clipping HDR highlights to white — identical to OBS screen recording output.

### FP16 capture internals

`FP16Capture` uses `IDXGIOutput5.DuplicateOutput1` to acquire frames in `DXGI_FORMAT_R16G16B16A16_FLOAT`. It calls `CopyResource`, `Map`, and `Unmap` on the D3D11 device context via **raw vtable calls** (bypassing comtypes dispatch, which corrupts D3D11 state in Python 3.14+).

## Known limitations

- Tested on Windows 11; Windows 10 is theoretically supported
- DRM-protected content cannot be captured — DXGI limitation
- Only one instance can run at a time; launching a second copy shows a notification and exits

## Support the project

If you find this tool useful, you can support the developer via bank transfer (EUR only):

**SEPA** (within Europe)
| | |
|---|---|
| IBAN | `GB63CLJU00997185802758` |
| BIC | `CLJUGB21` |
| Receiver | `BILOIVAN MYKOLA` |

**SWIFT** (worldwide, EUR only)
| | |
|---|---|
| IBAN | `UA113220010000026007310105358` |
| SWIFT/BIC | `UNJSUAUKXXX` |
| Receiver | `PE BILOIVAN MYKOLA` |
| Address | `02088, Ukraine, Kyiv, st. Levadna, build 74` |

## License

MIT
