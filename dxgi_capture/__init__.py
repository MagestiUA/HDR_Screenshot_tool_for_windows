"""
dxgi_capture — DXGI Desktop Duplication in R16G16B16A16_FLOAT (scRGB fp16).

Provides true HDR capture on Windows 10 1703+ with HDR displays.
Returned frames are float32 BGRA arrays where values > 1.0 represent HDR highlights
(scRGB linear: 1.0 = 80 nits, 2.5375 = 203 nits paper white).

Usage:
    from dxgi_capture import FP16Capture, FP16CaptureError, AccessLostError

    try:
        cam = FP16Capture(output_idx=0)
    except FP16CaptureError as e:
        print(f"fp16 not supported: {e}")
    else:
        frame = cam.grab()   # np.ndarray float32 BGRA, or None
        cam.release()
"""
from .capture import FP16Capture, FP16CaptureError, AccessLostError

__all__ = ["FP16Capture", "FP16CaptureError", "AccessLostError"]
