"""
HDR (Advanced Color) detection via Windows DISPLAYCONFIG API.
Works on Windows 10 1709+ with DisplayConfigGetDeviceInfo.

Usage:
    from hdr_detect import hdr_status_per_monitor
    statuses = hdr_status_per_monitor()   # list[bool], index = monitor idx
"""
import ctypes
import ctypes.wintypes as wt

_user32 = ctypes.windll.user32

_QDC_ONLY_ACTIVE_PATHS                  = 0x00000002
_DINFO_GET_ADVANCED_COLOR               = 9
_ERROR_SUCCESS                          = 0


# ── Structures ────────────────────────────────────────────────────────────────

class _LUID(ctypes.Structure):
    _fields_ = [("LowPart", wt.DWORD), ("HighPart", wt.LONG)]


class _DCDI_HEADER(ctypes.Structure):          # DISPLAYCONFIG_DEVICE_INFO_HEADER
    _fields_ = [
        ("type",      wt.UINT),                # 4
        ("size",      wt.UINT),                # 4
        ("adapterId", _LUID),                  # 8
        ("id",        wt.UINT),                # 4
    ]                                          # = 20 bytes


class _ADV_COLOR_INFO(ctypes.Structure):       # DISPLAYCONFIG_ADVANCED_COLOR_INFO
    _fields_ = [
        ("header",              _DCDI_HEADER), # 20
        ("value",               wt.UINT),      # 4  (bit 1 = advancedColorEnabled)
        ("colorEncoding",       wt.UINT),      # 4
        ("bitsPerColorChannel", wt.UINT),      # 4
    ]                                          # = 32 bytes


class _PATH_SOURCE(ctypes.Structure):          # DISPLAYCONFIG_PATH_SOURCE_INFO
    _fields_ = [
        ("adapterId",   _LUID),    # 8
        ("id",          wt.UINT),  # 4
        ("modeInfoIdx", wt.UINT),  # 4  (union, we only need the raw uint)
        ("statusFlags", wt.UINT),  # 4
    ]                              # = 20 bytes


class _PATH_TARGET(ctypes.Structure):          # DISPLAYCONFIG_PATH_TARGET_INFO
    _fields_ = [
        ("adapterId",        _LUID),   # 8
        ("id",               wt.UINT), # 4
        ("modeInfoIdx",      wt.UINT), # 4
        ("outputTechnology", wt.UINT), # 4
        ("rotation",         wt.UINT), # 4
        ("scaling",          wt.UINT), # 4
        ("refreshRate_N",    wt.UINT), # 4
        ("refreshRate_D",    wt.UINT), # 4
        ("scanLineOrdering", wt.UINT), # 4
        ("targetAvailable",  wt.BOOL), # 4
        ("statusFlags",      wt.UINT), # 4
    ]                                  # = 48 bytes


class _PATH_INFO(ctypes.Structure):            # DISPLAYCONFIG_PATH_INFO
    _fields_ = [
        ("sourceInfo", _PATH_SOURCE),  # 20
        ("targetInfo", _PATH_TARGET),  # 48
        ("flags",      wt.UINT),       # 4
    ]                                  # = 72 bytes


class _MODE_INFO(ctypes.Structure):            # DISPLAYCONFIG_MODE_INFO (opaque, 64 bytes)
    _fields_ = [("_raw", ctypes.c_byte * 64)]


# ── Public API ────────────────────────────────────────────────────────────────

def hdr_status_per_monitor() -> list[bool]:
    """
    Return a list where index i = True if HDR (Advanced Color) is enabled
    on that monitor.  Order matches dxcam / EnumDisplayMonitors order.
    Returns an empty list on failure.
    """
    path_n = wt.UINT(0)
    mode_n = wt.UINT(0)

    ret = _user32.GetDisplayConfigBufferSizes(
        _QDC_ONLY_ACTIVE_PATHS,
        ctypes.byref(path_n),
        ctypes.byref(mode_n),
    )
    if ret != _ERROR_SUCCESS:
        return []

    Paths = _PATH_INFO * path_n.value
    Modes = _MODE_INFO * mode_n.value
    paths = Paths()
    modes = Modes()

    ret = _user32.QueryDisplayConfig(
        _QDC_ONLY_ACTIVE_PATHS,
        ctypes.byref(path_n),
        paths,
        ctypes.byref(mode_n),
        modes,
        None,
    )
    if ret != _ERROR_SUCCESS:
        return []

    result: list[bool] = []
    for i in range(path_n.value):
        t = paths[i].targetInfo
        info = _ADV_COLOR_INFO()
        info.header.type      = _DINFO_GET_ADVANCED_COLOR
        info.header.size      = ctypes.sizeof(_ADV_COLOR_INFO)
        info.header.adapterId = t.adapterId
        info.header.id        = t.id

        ret = _user32.DisplayConfigGetDeviceInfo(ctypes.byref(info.header))
        if ret == _ERROR_SUCCESS:
            result.append(bool(info.value & 0x2))   # bit 1 = advancedColorEnabled
        else:
            result.append(False)

    return result


def is_hdr_on_monitor(monitor_idx: int = 0) -> bool:
    """Return True if HDR is active on the given monitor index."""
    try:
        statuses = hdr_status_per_monitor()
        return statuses[monitor_idx] if monitor_idx < len(statuses) else False
    except Exception:
        return False
