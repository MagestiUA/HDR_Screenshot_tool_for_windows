"""
Tone-mapping and HDR file saving utilities.

Input convention: numpy float32 array (H, W, 4) BGRA.

SDR path (dxcam BGRA uint8 → float32 / 255):
    values already in [0.0, 1.0], already sRGB gamma-encoded.
    → pass through unchanged; no tone-mapping, no gamma.

HDR path (dxcam FP16 / scRGB linear, values > 1.0):
    Windows scRGB colour space: 1.0 = 80 nits (technical reference white)
    SDR "paper white" in Windows HDR = 203 nits = 203/80 = 2.5375 scRGB
    → apply tone-mapping operator, then sRGB gamma encode.

OBS Studio mechanism
────────────────────
OBS captures via IDXGIOutput6 in DXGI_FORMAT_R16G16B16A16_FLOAT (FP16 scRGB
linear).  For SDR canvas output it does:
  1. Divide by the SDR white reference  (203 nits / 80 = 2.5375)
     → maps all SDR UI/text content to [0, 1] with no colour shift
  2. Clip HDR highlights that exceed SDR white to 1.0 (white)
  3. Apply sRGB gamma encoding  (linear → display-referred)

The result looks identical to a standard SDR screenshot for all non-HDR
content, while HDR specular highlights just go to white — exactly what you
see in OBS recordings.
"""
import struct
import zlib

import numpy as np
from PIL import Image

# ── sRGB gamma encode ─────────────────────────────────────────────────────────

def _linear_to_srgb(linear: np.ndarray) -> np.ndarray:
    """Convert linear light [0, 1] to sRGB gamma-encoded [0, 1]."""
    return np.where(
        linear <= 0.0031308,
        12.92 * linear,
        1.055 * np.power(np.maximum(linear, 1e-10), 1.0 / 2.4) - 0.055,
    )


# ── Tone-mapping operators ─────────────────────────────────────────────────────

def _windows_hdr(rgb: np.ndarray, sdr_white_nits: float = 250.0) -> np.ndarray:
    """
    Windows / OBS-style HDR → SDR (default, recommended).

    scRGB linear input (1.0 = 80 nits):
      • Divides by the SDR paper-white reference so that all SDR UI content
        maps exactly to [0, 1] — text and colours look identical to an SDR
        screenshot.
      • HDR specular highlights above paper-white clip to white.
      • Applies sRGB gamma encoding.

    sdr_white_nits: brightness level that maps to display white.
      203 = ITU-R BT.2408 operational reference (technically correct)
      250 = default here — slightly darker, matches typical Windows
            "SDR content brightness" slider setting.
      Raise further (300–400) if output still looks overexposed.
    """
    sdr_ref    = sdr_white_nits / 80.0
    normalized = np.clip(rgb / sdr_ref, 0.0, 1.0)
    return _linear_to_srgb(normalized)


def _aces(rgb: np.ndarray) -> np.ndarray:
    """
    ACES filmic tone mapping with auto-exposure.

    Scales so the 95th-percentile pixel maps to ACES input ≈ 1.0, then
    applies the filmic S-curve.  Applies sRGB gamma encoding.
    Good for HDR game/media captures where you want the cinematic look.
    """
    peak = float(rgb.max())
    if peak > 1.0:
        p95 = float(np.percentile(rgb, 95))
        if p95 > 1e-8:
            rgb = rgb * (1.0 / p95)

    a, b, c, d, e = 2.51, 0.03, 2.43, 0.59, 0.14
    mapped = np.clip(
        (rgb * (a * rgb + b)) / (rgb * (c * rgb + d) + e),
        0.0, 1.0,
    )
    return _linear_to_srgb(mapped)


def _reinhard(rgb: np.ndarray) -> np.ndarray:
    """
    Photographic Reinhard tone mapping (Reinhard et al. 2002).

    Maps scene log-average luminance to middle grey (key = 0.18).
    Applies sRGB gamma encoding.
    """
    lum     = (0.2126 * rgb[:, :, 0] +
               0.7152 * rgb[:, :, 1] +
               0.0722 * rgb[:, :, 2])
    log_avg = float(np.exp(np.mean(np.log(np.maximum(lum, 1e-10)))))
    scale   = 0.18 / max(log_avg, 1e-10)
    scaled  = rgb * scale
    mapped  = np.clip(scaled / (1.0 + scaled), 0.0, 1.0)
    return _linear_to_srgb(mapped)


_OPERATORS = {
    "windows":  _windows_hdr,
    "aces":     _aces,
    "reinhard": _reinhard,
}


# ── 16-bit PNG writer (stdlib only) ───────────────────────────────────────────

def _write_16bit_rgb_png(path: str, rgb_16: np.ndarray) -> None:
    """Write (H, W, 3) uint16 array as 16-bit truecolour PNG using stdlib."""
    H, W = rgb_16.shape[:2]
    arr  = rgb_16.astype(">u2")          # PNG requires big-endian 16-bit

    rows = []
    for y in range(H):
        rows.append(b"\x00")             # filter type = None
        rows.append(arr[y].tobytes())
    compressed = zlib.compress(b"".join(rows), 6)

    def _chunk(tag: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    # IHDR: width(4) height(4) bit_depth(1)=16 color_type(1)=2 compress filter interlace
    ihdr = struct.pack(">IIBBBBB", W, H, 16, 2, 0, 0, 0)

    with open(path, "wb") as fh:
        fh.write(b"\x89PNG\r\n\x1a\n")
        fh.write(_chunk(b"IHDR", ihdr))
        fh.write(_chunk(b"IDAT", compressed))
        fh.write(_chunk(b"IEND", b""))


# ── Public API ─────────────────────────────────────────────────────────────────

def to_sdr(
    frame: np.ndarray,
    method: str = "windows",
    sdr_white_nits: float = 250.0,
) -> Image.Image:
    """
    Convert a BGRA float32 frame to an 8-bit SDR PIL Image (RGB).

    • SDR input (max ≤ 1.0): passed through unchanged.
    • HDR input (max > 1.0, linear scRGB): tone-mapped + sRGB gamma encoded.

    Args:
        frame:          (H, W, 4) float32 BGRA
        method:         "windows" (default) | "aces" | "reinhard"
        sdr_white_nits: brightness (nits) that maps to output white in the
                        "windows" operator.  Higher = darker output.
                        Ignored by "aces" and "reinhard" (auto-exposed).
    """
    rgb = frame[:, :, 2::-1].astype(np.float32)    # BGR→RGB, drop alpha

    if rgb.max() > 1.0:
        if method == "windows" or method not in _OPERATORS:
            rgb = _windows_hdr(rgb, sdr_white_nits=sdr_white_nits)
        else:
            rgb = _OPERATORS[method](rgb)

    rgb_8 = (np.clip(rgb, 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8)
    return Image.fromarray(rgb_8, mode="RGB")


def save_hdr_png(frame: np.ndarray, path: str) -> None:
    """
    Save HDR BGRA float32 frame as 16-bit RGB PNG (lossless, stdlib only).

    Normalises linearly to the frame peak so the full dynamic range is
    preserved in the uint16 domain.
    """
    rgb  = frame[:, :, 2::-1].astype(np.float32)   # BGR→RGB, drop alpha
    peak = float(rgb.max())
    if peak < 1e-8:
        peak = 1.0
    rgb_16 = (np.clip(rgb / peak, 0.0, 1.0) * 65535.0 + 0.5).astype(np.uint16)
    _write_16bit_rgb_png(path, rgb_16)
