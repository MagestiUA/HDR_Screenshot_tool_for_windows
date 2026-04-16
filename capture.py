"""
Multi-monitor screen capture.

HDR monitors:  FP16Capture (dxgi_capture) → R16G16B16A16_FLOAT scRGB
               values > 1.0 indicate HDR highlights; tonemapping applied.
SDR / fallback: dxcam BGRA uint8 → float32 [0, 1].

Returned frame is always float32 BGRA:
  is_hdr = True  → linear scRGB, may exceed 1.0
  is_hdr = False → sRGB-encoded [0.0, 1.0]
"""
from __future__ import annotations

import gc
import time
import ctypes
import ctypes.wintypes as wt
from dataclasses import dataclass

import numpy as np

import hdr_detect
from dxgi_capture import FP16Capture, FP16CaptureError, AccessLostError


# ── Monitor geometry ──────────────────────────────────────────────────────────

@dataclass
class MonitorInfo:
    idx: int
    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top


def get_monitors() -> list[MonitorInfo]:
    monitors: list[MonitorInfo] = []
    counter = [0]

    @ctypes.WINFUNCTYPE(
        ctypes.c_bool,
        ctypes.c_ulong, ctypes.c_ulong,
        ctypes.POINTER(wt.RECT), ctypes.c_long,
    )
    def _cb(hmon, hdc, lprect, lparam):
        r = lprect.contents
        monitors.append(MonitorInfo(
            idx=counter[0],
            left=r.left, top=r.top,
            right=r.right, bottom=r.bottom,
        ))
        counter[0] += 1
        return True

    ctypes.windll.user32.EnumDisplayMonitors(None, None, _cb, 0)
    return monitors


def cursor_monitor() -> MonitorInfo:
    pt = wt.POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
    for m in get_monitors():
        if m.left <= pt.x < m.right and m.top <= pt.y < m.bottom:
            return m
    return get_monitors()[0]


# ── DXGI output index mapping ─────────────────────────────────────────────────
# EnumDisplayMonitors and DXGI output enumeration may use different orderings.
# We match Win32 monitor geometry to DXGI output DesktopCoordinates.

_win32_to_dxgi: dict[int, int] = {}   # win32 monitor idx → dxgi output_idx


def _build_dxgi_map(monitors: list[MonitorInfo]) -> None:
    """Populate _win32_to_dxgi by matching Win32 monitor rects to DXGI outputs."""
    import comtypes
    from dxcam._libs.dxgi import (
        IDXGIFactory1, IDXGIAdapter1, IDXGIOutput,
        DXGI_OUTPUT_DESC,
    )
    from typing import cast as tcast, Any

    dxgi_dll = ctypes.windll.dxgi
    dxgi_dll.CreateDXGIFactory1.argtypes = (
        comtypes.GUID, ctypes.POINTER(ctypes.c_void_p)
    )
    dxgi_dll.CreateDXGIFactory1.restype = ctypes.c_int32
    pf = ctypes.c_void_p(0)
    if dxgi_dll.CreateDXGIFactory1(IDXGIFactory1._iid_, ctypes.byref(pf)) < 0:
        return
    factory = tcast(Any, ctypes.cast(pf, ctypes.POINTER(IDXGIFactory1)))

    dxgi_outputs: list[tuple[int, int, int, int, int]] = []  # (dxgi_idx, l, t, r, b)
    flat = 0
    ai = 0
    while True:
        try:
            adp = ctypes.POINTER(IDXGIAdapter1)()
            factory.EnumAdapters1(ai, ctypes.byref(adp))
        except comtypes.COMError:
            break
        oi = 0
        while True:
            try:
                out = ctypes.POINTER(IDXGIOutput)()
                tcast(Any, adp).EnumOutputs(oi, ctypes.byref(out))
                desc = DXGI_OUTPUT_DESC()
                tcast(Any, out).GetDesc(ctypes.byref(desc))
                rc = desc.DesktopCoordinates
                dxgi_outputs.append((flat, rc.left, rc.top, rc.right, rc.bottom))
            except comtypes.COMError:
                break
            flat += 1
            oi += 1
        ai += 1

    for mon in monitors:
        for (dxgi_idx, l, t, r, b) in dxgi_outputs:
            if l == mon.left and t == mon.top and r == mon.right and b == mon.bottom:
                _win32_to_dxgi[mon.idx] = dxgi_idx
                break


def _dxgi_idx(monitor_idx: int) -> int:
    """Return DXGI output_idx for the given Win32 monitor idx."""
    return _win32_to_dxgi.get(monitor_idx, monitor_idx)


# ── Camera pool ───────────────────────────────────────────────────────────────
# Each slot: (camera_object, is_hdr: bool, camera_type: "fp16" | "dxcam")

_cameras: dict[int, tuple] = {}


def _release_cam(cam: object) -> None:
    """Safely release any camera type."""
    try:
        cam.release()           # type: ignore[union-attr]
    except Exception:
        pass


def _probe_dxcam(output_idx: int, attempts: int = 15) -> "tuple | None":
    """Create and probe a dxcam BGRA camera. Returns (cam, False) or None."""
    import dxcam
    try:
        cam = dxcam.create(output_idx=output_idx, output_color="BGRA")
    except Exception as exc:
        print(f"[capture] dxcam BGRA create failed for output {output_idx}: {exc}")
        return None

    for _ in range(attempts):
        try:
            frame = cam.grab()
            if frame is not None:
                print(f"[capture] Monitor {output_idx}: dxcam BGRA  dtype={frame.dtype}")
                return cam, False
        except Exception:
            break
        time.sleep(0.05)

    print(f"[capture] Monitor {output_idx}: dxcam grab returned None after retries")
    return cam, False   # camera exists but no frame yet — may work later


def _make_camera(output_idx: int) -> "tuple[object, bool] | tuple[None, bool]":
    """
    Return (camera, is_hdr).

    Strategy:
      1. If HDR is active on this monitor → try FP16Capture first.
         On success, return (FP16Capture, True).
         On failure, fall through to dxcam.
      2. dxcam BGRA fallback (always SDR).
    """
    is_hdr_mon = hdr_detect.is_hdr_on_monitor(output_idx)

    if is_hdr_mon:
        try:
            cam = FP16Capture(output_idx=output_idx)
            # Warm-up: try to get a real frame
            for _ in range(20):
                frame = cam.grab()
                if frame is not None:
                    print(
                        f"[capture] Monitor {output_idx}: FP16 HDR  "
                        f"max={float(frame.max()):.3f}  "
                        f"shape={frame.shape}"
                    )
                    return cam, True
                time.sleep(0.05)
            # Camera created OK but no frame yet — keep it, will work on real hotkey press
            print(f"[capture] Monitor {output_idx}: FP16 HDR (no warmup frame)")
            return cam, True
        except FP16CaptureError as exc:
            print(f"[capture] Monitor {output_idx}: FP16 not supported — {exc}")
            print(f"[capture] Monitor {output_idx}: falling back to dxcam BGRA")

    result = _probe_dxcam(output_idx)
    if result is None:
        return None, False
    return result


def _get_camera(win32_idx: int) -> "tuple[object, bool]":
    if win32_idx not in _cameras:
        dxgi_idx = _dxgi_idx(win32_idx)
        cam, is_hdr = _make_camera(dxgi_idx)
        if cam is not None:
            _cameras[win32_idx] = (cam, is_hdr)
        else:
            return None, False
    return _cameras[win32_idx]


# ── Public API ────────────────────────────────────────────────────────────────

def grab(monitor: MonitorInfo | None = None) -> "np.ndarray | None":
    """
    Capture *monitor* (defaults to the one under the cursor).

    Returns float32 BGRA:
      HDR path  — linear scRGB values, may exceed 1.0
      SDR path  — sRGB-encoded [0.0, 1.0]
    Returns None on failure.
    """
    if monitor is None:
        monitor = cursor_monitor()

    # Build DXGI↔Win32 monitor map on first call
    if not _win32_to_dxgi:
        _build_dxgi_map(get_monitors())

    cam, is_hdr = _get_camera(monitor.idx)
    if cam is None and monitor.idx != 0:
        cam, is_hdr = _get_camera(0)
    if cam is None:
        return None

    for _ in range(20):
        try:
            frame = cam.grab()   # type: ignore[union-attr]
            if frame is None:
                time.sleep(0.05)
                continue

            if is_hdr:
                # FP16Capture already returns float32 BGRA (scRGB linear)
                return frame
            else:
                # dxcam returns uint8 BGRA → normalise to float32 [0,1]
                return frame.astype(np.float32) / 255.0

        except AccessLostError:
            # Display mode change: drop this camera, next call will reinit
            print(f"[capture] Monitor {monitor.idx}: access lost, reinitialising")
            entry = _cameras.pop(monitor.idx, None)
            if entry:
                _release_cam(entry[0])
            return None

        except Exception as exc:
            print(f"[capture] grab error on monitor {monitor.idx}: {exc}")
            entry = _cameras.pop(monitor.idx, None)
            if entry:
                _release_cam(entry[0])
            return None

    print(f"[capture] Monitor {monitor.idx}: grab returned None after retries")
    return None
