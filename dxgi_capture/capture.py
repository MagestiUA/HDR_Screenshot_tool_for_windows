"""
FP16Capture: DXGI Desktop Duplication in R16G16B16A16_FLOAT.

Capture flow per frame:
  1. AcquireNextFrame      → IDXGIResource
  2. QI                    → ID3D11Texture2D  (GPU VRAM, fp16 RGBA scRGB)
  3. CopyResource          → staging texture  (CPU-readable, same format)
  4. ReleaseFrame          (GPU texture freed, staging stays valid)
  5. ID3D11DeviceContext.Map  → D3D11_MAPPED_SUBRESOURCE (pData, RowPitch)
  6. numpy reshape         → float32 BGRA array
  7. ID3D11DeviceContext.Unmap
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
from typing import Any, cast

import comtypes
import numpy as np

# ── Re-use stable COM definitions from dxcam (not modified, just imported) ───
from dxcam._libs.dxgi import (
    IDXGIFactory1, IDXGIAdapter1, IDXGIOutput, IDXGIOutput5,
    IDXGIOutputDuplication, IDXGIResource,
    DXGI_OUTDUPL_FRAME_INFO, DXGI_OUTPUT_DESC,
    DXGI_ERROR_WAIT_TIMEOUT, DXGI_ERROR_ACCESS_LOST,
    DXGI_OUTDUPL_FLAG_NONE,
)
from dxcam._libs.d3d11 import (
    ID3D11Device, ID3D11DeviceContext,
    ID3D11Texture2D, ID3D11Resource,
    D3D11_TEXTURE2D_DESC,
    D3D11_USAGE_STAGING, D3D11_CPU_ACCESS_READ,
    D3D_FEATURE_LEVEL_11_0, D3D_FEATURE_LEVEL_10_1, D3D_FEATURE_LEVEL_10_0,
)

# ── Additional constants / structs not in dxcam ───────────────────────────────
DXGI_FORMAT_R16G16B16A16_FLOAT: int = 10   # scRGB half-float RGBA
D3D11_MAP_READ:                  int = 1    # D3D11_MAP enum value


class D3D11_MAPPED_SUBRESOURCE(ctypes.Structure):
    """CPU-side view of a mapped D3D11 resource."""
    _fields_ = [
        ("pData",      ctypes.c_void_p),
        ("RowPitch",   ctypes.c_uint),
        ("DepthPitch", ctypes.c_uint),
    ]


# ── Raw vtable helpers ────────────────────────────────────────────────────────
# comtypes in Python 3.14 enforces argument count for methods declared without
# explicit paramflags (e.g. STDMETHOD(HRESULT, "Map")).
# We bypass this by calling the vtable slot directly via ctypes function pointers.

_PTR_SZ = ctypes.sizeof(ctypes.c_void_p)

# ID3D11DeviceContext vtable slot indices (IUnknown=0-2, ID3D11DeviceChild=3-6):
# Own methods (7+): VSSetConstantBuffers(7) … Draw(13) Map(14) Unmap(15) …
# CopyResource is method #41 in own list (0-indexed) → slot 3+4+41 = 48? No:
# IUnknown(3) + DeviceChild(4) + own methods 1..41 = 3+4+40 = 47 (0-indexed own[40])
# Counting comtypes _methods_ list: VSSetCB=0, PSSetSR=1, PSSetS=2, PSSetSa=3,
# VSSetS=4, DrawIdx=5, Draw=6, Map=7, Unmap=8, ..., CopyResource=40 → slot 47
_SLOT_CTX_MAP          = 14   # vtable slot for Map
_SLOT_CTX_UNMAP        = 15   # vtable slot for Unmap
_SLOT_CTX_COPYRESOURCE = 47   # vtable slot for CopyResource

_MapFn = ctypes.WINFUNCTYPE(
    ctypes.HRESULT,
    ctypes.c_void_p,  # this
    ctypes.c_void_p,  # pResource
    ctypes.c_uint,    # Subresource
    ctypes.c_uint,    # D3D11_MAP MapType
    ctypes.c_uint,    # MapFlags (0)
    ctypes.c_void_p,  # pMappedResource [out] — void* to avoid LP_ type-check
)
_UnmapFn = ctypes.WINFUNCTYPE(
    None,
    ctypes.c_void_p,   # this
    ctypes.c_void_p,   # pResource
    ctypes.c_uint,     # Subresource
)
_CopyFn = ctypes.WINFUNCTYPE(
    None,
    ctypes.c_void_p,   # this
    ctypes.c_void_p,   # pDstResource
    ctypes.c_void_p,   # pSrcResource
)


def _get_vtable_fn(com_ptr: ctypes.c_void_p, slot: int, fn_type):
    """
    Return (callable, this_ptr) for the COM method at *slot* in *com_ptr*'s vtable.
    com_ptr must be a ctypes pointer to a COM object.
    """
    this   = ctypes.cast(com_ptr, ctypes.c_void_p).value
    vtable = ctypes.cast(this, ctypes.POINTER(ctypes.c_void_p)).contents.value
    fn_raw = ctypes.cast(vtable + slot * _PTR_SZ, ctypes.POINTER(ctypes.c_void_p)).contents.value
    return fn_type(fn_raw), this


# ── Signed 32-bit HRESULT values for comparison with comtypes e.hresult
_HR_TIMEOUT     = ctypes.c_int32(DXGI_ERROR_WAIT_TIMEOUT).value
_HR_ACCESS_LOST = ctypes.c_int32(DXGI_ERROR_ACCESS_LOST).value


# ── Exceptions ────────────────────────────────────────────────────────────────

class FP16CaptureError(RuntimeError):
    """Raised when fp16 capture cannot be initialised on this system."""

class AccessLostError(FP16CaptureError):
    """
    Raised when the desktop duplication session is invalidated
    (display mode change, lock screen, UAC elevation, etc.).
    Caller should release and create a new FP16Capture instance.
    """


# ── Internal helpers ──────────────────────────────────────────────────────────

def _find_adapter_and_output(target_idx: int) -> tuple[Any, Any]:
    """
    Enumerate DXGI factory → adapters → outputs linearly.
    Returns (IDXGIAdapter1*, IDXGIOutput*) for the target_idx-th output,
    or (None, None) if not found.
    """
    dxgi_dll = ctypes.windll.dxgi
    dxgi_dll.CreateDXGIFactory1.argtypes = (comtypes.GUID, ctypes.POINTER(ctypes.c_void_p))
    dxgi_dll.CreateDXGIFactory1.restype  = ctypes.c_int32

    pf = ctypes.c_void_p(0)
    if dxgi_dll.CreateDXGIFactory1(IDXGIFactory1._iid_, ctypes.byref(pf)) < 0:
        return None, None
    factory: Any = cast(Any, ctypes.cast(pf, ctypes.POINTER(IDXGIFactory1)))

    flat_idx = 0
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
                cast(Any, adp).EnumOutputs(oi, ctypes.byref(out))
            except comtypes.COMError:
                break
            if flat_idx == target_idx:
                return adp, out
            flat_idx += 1
            oi += 1
        ai += 1

    return None, None


# ── Public class ──────────────────────────────────────────────────────────────

class FP16Capture:
    """
    One-output DXGI Desktop Duplication session in R16G16B16A16_FLOAT.

    Args:
        output_idx: Zero-based DXGI output index (matches dxcam / capture.py order).

    Raises:
        FP16CaptureError: If the system or driver does not support fp16 duplication
                          (SDR-only display, old driver, missing IDXGIOutput5, etc.).
    """

    def __init__(self, output_idx: int = 0) -> None:
        self._output_idx   = output_idx
        self._width        = 0
        self._height       = 0
        self._dev: Any     = None
        self._ctx: Any     = None
        self._dupl: Any    = None
        self._stg_ptr      = None          # ctypes.POINTER(ID3D11Texture2D)
        self._frame_held   = False
        self._released     = False
        self._last_frame: "np.ndarray | None" = None   # cache for static desktop
        self._setup()

    # ── Initialisation ────────────────────────────────────────────────────────

    def _setup(self) -> None:
        adapter, output = _find_adapter_and_output(self._output_idx)
        if adapter is None:
            raise FP16CaptureError(f"Output {self._output_idx} not found")

        # D3D_DRIVER_TYPE_UNKNOWN (0) required when pAdapter != NULL
        dev_ptr = ctypes.POINTER(ID3D11Device)()
        ctx_ptr = ctypes.POINTER(ID3D11DeviceContext)()
        feat    = (ctypes.c_uint * 3)(
            D3D_FEATURE_LEVEL_11_0, D3D_FEATURE_LEVEL_10_1, D3D_FEATURE_LEVEL_10_0
        )
        hr = ctypes.windll.d3d11.D3D11CreateDevice(
            adapter, 0, None, 0,
            feat, 3, 7,
            ctypes.byref(dev_ptr), None, ctypes.byref(ctx_ptr),
        )
        if hr < 0:
            raise FP16CaptureError(
                f"D3D11CreateDevice failed: HRESULT=0x{hr & 0xFFFFFFFF:08X}"
            )
        self._dev     = cast(Any, dev_ptr)
        self._ctx     = cast(Any, ctx_ptr)
        self._dev_ptr = dev_ptr
        self._ctx_ptr = ctx_ptr

        # Read output dimensions from IDXGIOutput.GetDesc
        desc = DXGI_OUTPUT_DESC()
        cast(Any, output).GetDesc(ctypes.byref(desc))
        self._width  = desc.DesktopCoordinates.right - desc.DesktopCoordinates.left
        self._height = desc.DesktopCoordinates.bottom - desc.DesktopCoordinates.top

        # IDXGIOutput → IDXGIOutput5
        try:
            out5 = cast(Any, output).QueryInterface(IDXGIOutput5)
        except comtypes.COMError as e:
            raise FP16CaptureError(f"IDXGIOutput5 not supported: {e}") from e

        # Desktop Duplication in R16G16B16A16_FLOAT
        formats  = (ctypes.c_uint * 1)(DXGI_FORMAT_R16G16B16A16_FLOAT)
        dupl_ptr = ctypes.POINTER(IDXGIOutputDuplication)()
        try:
            out5.DuplicateOutput1(
                ctypes.cast(dev_ptr, ctypes.c_void_p),
                DXGI_OUTDUPL_FLAG_NONE, 1, formats,
                ctypes.byref(dupl_ptr),
            )
        except comtypes.COMError as e:
            raise FP16CaptureError(
                f"DuplicateOutput1 (fp16) not supported: {e}\n"
                f"Requires Windows 10 1703+ with an HDR-capable display."
            ) from e

        self._dupl     = cast(Any, dupl_ptr)
        self._dupl_ptr = dupl_ptr

    def _ensure_staging(self, src_tex: Any) -> None:
        """Create the CPU-readable staging texture on first use."""
        if self._stg_ptr is not None:
            return
        sd = D3D11_TEXTURE2D_DESC()
        src_tex.GetDesc(ctypes.byref(sd))

        s = D3D11_TEXTURE2D_DESC()
        s.Width           = sd.Width
        s.Height          = sd.Height
        s.MipLevels       = 1
        s.ArraySize       = 1
        s.Format          = DXGI_FORMAT_R16G16B16A16_FLOAT
        s.SampleDesc.Count   = 1
        s.SampleDesc.Quality = 0
        s.Usage           = D3D11_USAGE_STAGING
        s.CPUAccessFlags  = D3D11_CPU_ACCESS_READ
        s.BindFlags       = 0
        s.MiscFlags       = 0

        stg = ctypes.POINTER(ID3D11Texture2D)()
        self._dev.CreateTexture2D(ctypes.byref(s), None, ctypes.byref(stg))
        self._stg_ptr = stg

    # ── Public API ────────────────────────────────────────────────────────────

    @property
    def width(self) -> int:
        return self._width

    @property
    def height(self) -> int:
        return self._height

    def grab(self) -> "np.ndarray | None":
        """
        Capture the current desktop frame.

        Returns:
            float32 BGRA numpy array of shape (H, W, 4).
            Values are scRGB linear; HDR highlights may exceed 1.0.
            Returns None if no new desktop frame is available (call again).

        Raises:
            AccessLostError:    Display mode changed; re-create FP16Capture.
            FP16CaptureError:   Unrecoverable error.
        """
        if self._released:
            raise FP16CaptureError("FP16Capture has been released")

        if self._frame_held:
            try:
                self._dupl.ReleaseFrame()
            except Exception:
                pass
            self._frame_held = False

        info    = DXGI_OUTDUPL_FRAME_INFO()
        res_ptr = ctypes.POINTER(IDXGIResource)()

        try:
            self._dupl.AcquireNextFrame(
                50, ctypes.byref(info), ctypes.byref(res_ptr),
            )
        except comtypes.COMError as e:
            hr = ctypes.c_int32(e.hresult).value
            if hr == _HR_TIMEOUT:
                return self._last_frame   # cached frame if desktop is static
            if hr == _HR_ACCESS_LOST:
                raise AccessLostError("Desktop duplication access lost") from e
            raise FP16CaptureError(
                f"AcquireNextFrame: 0x{e.hresult & 0xFFFFFFFF:08X}"
            ) from e

        self._frame_held = True

        # Mouse-only update → no new pixels
        if int(info.LastPresentTime) == 0:
            self._dupl.ReleaseFrame()
            self._frame_held = False
            return None

        # IDXGIResource → ID3D11Texture2D (GPU VRAM)
        src_tex: Any = cast(Any, res_ptr).QueryInterface(ID3D11Texture2D)

        self._ensure_staging(src_tex)

        # GPU VRAM → CPU-accessible staging (zero conversion, same fp16 format)
        # Use raw vtable to avoid comtypes corrupting D3D11 internal state.
        src_raw = ctypes.cast(src_tex,       ctypes.c_void_p).value
        stg_raw = ctypes.cast(self._stg_ptr, ctypes.c_void_p).value
        copy_fn, ctx_this_copy = _get_vtable_fn(
            self._ctx_ptr, _SLOT_CTX_COPYRESOURCE, _CopyFn
        )
        copy_fn(ctx_this_copy, stg_raw, src_raw)

        # Release DXGI frame — GPU texture freed, staging stays valid
        self._dupl.ReleaseFrame()
        self._frame_held = False

        # Map staging for CPU read via ID3D11DeviceContext.Map
        # (IDXGISurface.Map does not support R16G16B16A16_FLOAT staging textures)
        # Use raw vtable call to bypass comtypes argument-count enforcement.
        mapped = D3D11_MAPPED_SUBRESOURCE()
        map_fn, ctx_this = _get_vtable_fn(self._ctx_ptr, _SLOT_CTX_MAP, _MapFn)
        hr = map_fn(ctx_this, stg_raw, 0, D3D11_MAP_READ, 0, ctypes.addressof(mapped))
        if hr < 0:
            raise FP16CaptureError(
                f"ID3D11DeviceContext.Map failed: 0x{hr & 0xFFFFFFFF:08X}"
            )
        try:
            W, H       = self._width, self._height
            row_pitch  = int(mapped.RowPitch)   # bytes per row (may include padding)

            # R16G16B16A16_FLOAT: 8 bytes/pixel = 4 × fp16 channels (R G B A)
            ptr = ctypes.cast(mapped.pData, ctypes.POINTER(ctypes.c_uint8))
            raw = np.ctypeslib.as_array(ptr, shape=(row_pitch * H,))
            fp16_flat = raw.view(np.float16)                         # 2 bytes per value
            fp16_rows = fp16_flat.reshape(H, row_pitch // 2)         # (H, row_fp16)
            fp16_rgba = fp16_rows[:, : W * 4].reshape(H, W, 4)       # (H, W, 4) RGBA
            fp16_rgba = fp16_rgba.copy()    # own memory before Unmap
        finally:
            unmap_fn, ctx_this2 = _get_vtable_fn(self._ctx_ptr, _SLOT_CTX_UNMAP, _UnmapFn)
            unmap_fn(ctx_this2, stg_raw, 0)

        # RGBA fp16 (scRGB: channel 0=R, 1=G, 2=B) → BGRA float32
        bgra = np.empty((H, W, 4), dtype=np.float32)
        bgra[:, :, 0] = fp16_rgba[:, :, 2]   # B ← scRGB channel 2
        bgra[:, :, 1] = fp16_rgba[:, :, 1]   # G ← scRGB channel 1
        bgra[:, :, 2] = fp16_rgba[:, :, 0]   # R ← scRGB channel 0
        bgra[:, :, 3] = fp16_rgba[:, :, 3]   # A ← alpha
        self._last_frame = bgra
        return bgra

    def release(self) -> None:
        """Release all DXGI/D3D11 resources."""
        if self._released:
            return
        self._released = True
        if self._frame_held:
            try:
                self._dupl.ReleaseFrame()
            except Exception:
                pass
            self._frame_held = False
        self._stg_ptr   = None
        self._last_frame = None
        self._dupl     = None
        self._dupl_ptr = None
        self._ctx      = None
        self._ctx_ptr  = None
        self._dev      = None
        self._dev_ptr  = None

    def __del__(self) -> None:
        try:
            self.release()
        except Exception:
            pass

    def __enter__(self) -> "FP16Capture":
        return self

    def __exit__(self, *_: object) -> bool:
        self.release()
        return False

    def __repr__(self) -> str:
        return (
            f"<FP16Capture output={self._output_idx} "
            f"{self._width}x{self._height} "
            f"{'released' if self._released else 'active'}>"
        )
