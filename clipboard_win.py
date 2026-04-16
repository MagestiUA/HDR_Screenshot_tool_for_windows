"""
Copy a PIL Image to the Windows clipboard using only ctypes (no extra deps).

The image is placed on the clipboard as CF_DIB so any app that accepts
Ctrl+V image paste (messengers, Office, etc.) can use it.
"""
import ctypes
import ctypes.wintypes as wt
from io import BytesIO
from PIL import Image

_CF_DIB      = 8
_GMEM_MOVEABLE = 0x0002

_k32  = ctypes.windll.kernel32
_u32  = ctypes.windll.user32

# ── Explicit signatures for 64-bit pointer safety ────────────────────────────
_k32.GlobalAlloc.restype   = ctypes.c_void_p
_k32.GlobalAlloc.argtypes  = [ctypes.c_uint, ctypes.c_size_t]

_k32.GlobalLock.restype    = ctypes.c_void_p
_k32.GlobalLock.argtypes   = [ctypes.c_void_p]

_k32.GlobalUnlock.restype  = ctypes.c_bool
_k32.GlobalUnlock.argtypes = [ctypes.c_void_p]

_k32.GlobalFree.restype    = ctypes.c_void_p
_k32.GlobalFree.argtypes   = [ctypes.c_void_p]

_u32.OpenClipboard.restype  = ctypes.c_bool
_u32.OpenClipboard.argtypes = [wt.HWND]

_u32.EmptyClipboard.restype  = ctypes.c_bool
_u32.EmptyClipboard.argtypes = []

_u32.SetClipboardData.restype  = ctypes.c_void_p
_u32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]

_u32.CloseClipboard.restype  = ctypes.c_bool
_u32.CloseClipboard.argtypes = []


def copy_image(img: Image.Image) -> None:
    """
    Place *img* on the Windows clipboard as CF_DIB.

    Args:
        img: PIL Image (any mode — converted to RGB internally)
    """
    # BMP file = 14-byte file header + DIB header + pixel data
    # CF_DIB expects just DIB header + pixel data (no file header)
    buf = BytesIO()
    img.convert("RGB").save(buf, format="BMP")
    dib = buf.getvalue()[14:]           # strip 14-byte BMP file header

    h_mem = _k32.GlobalAlloc(_GMEM_MOVEABLE, len(dib))
    if not h_mem:
        raise RuntimeError("GlobalAlloc failed")

    p_mem = _k32.GlobalLock(h_mem)
    if not p_mem:
        _k32.GlobalFree(h_mem)
        raise RuntimeError("GlobalLock failed")

    ctypes.memmove(p_mem, dib, len(dib))
    _k32.GlobalUnlock(h_mem)

    if not _u32.OpenClipboard(None):
        _k32.GlobalFree(h_mem)
        raise RuntimeError("OpenClipboard failed")
    try:
        _u32.EmptyClipboard()
        _u32.SetClipboardData(_CF_DIB, h_mem)
    finally:
        _u32.CloseClipboard()
