"""
Windows Toast Notifications з мініатюрою скріншоту.

Використовує PowerShell + WinRT — без додаткових pip-пакетів.

Публічний API
─────────────
    show(title, body, image_path=None, fallback_icon=None)
"""

import os
import subprocess
import threading
import winreg
from urllib.parse import quote

# ── Реєстрація AUMID ──────────────────────────────────────────────────────────

_AUMID     = "HDR.Screenshot.Tool"
_APP_NAME  = "HDR Screenshot"
_AUMID_KEY = rf"SOFTWARE\Classes\AppUserModelId\{_AUMID}"

_aumid_registered = False


def _register_aumid() -> None:
    global _aumid_registered
    if _aumid_registered:
        return
    try:
        key = winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER,
            _AUMID_KEY,
            0,
            winreg.KEY_SET_VALUE,
        )
        winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ, _APP_NAME)
        winreg.CloseKey(key)
        _aumid_registered = True
    except Exception:
        pass


# ── PowerShell toast ──────────────────────────────────────────────────────────

_PS_TEMPLATE = r"""
Add-Type -AssemblyName System.Runtime.WindowsRuntime | Out-Null

[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime] | Out-Null
[Windows.UI.Notifications.ToastNotification,        Windows.UI.Notifications, ContentType=WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument,                  Windows.Data.Xml.Dom,     ContentType=WindowsRuntime] | Out-Null

$xml = @"
<toast>
  <visual>
    <binding template="ToastGeneric">
      <text>!!TITLE!!</text>
      <text>!!BODY!!</text>
      !!HERO_LINE!!
    </binding>
  </visual>
</toast>
"@

$doc = [Windows.Data.Xml.Dom.XmlDocument]::new()
$doc.LoadXml($xml)
$toast = [Windows.UI.Notifications.ToastNotification]::new($doc)
$toast.SuppressPopup = $false

$notifier = [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("!!AUMID!!")
$notifier.Show($toast)

Start-Sleep -Seconds 3
"""


def _xml_esc(s: str) -> str:
    return (s.replace("&", "&amp;")
             .replace("<", "&lt;")
             .replace(">", "&gt;")
             .replace('"', "&quot;"))


def _build_ps_script(title: str, body: str, image_path: str | None) -> str:
    hero_line = ""

    if image_path and os.path.isfile(image_path):
        abs_path  = os.path.abspath(image_path)
        # URL-кодування обов'язкове для кирилиці та пробілів у шляху
        uri = "file:///" + quote(abs_path.replace("\\", "/"), safe="/:@")
        hero_line = f'<image placement="hero" src="{uri}"/>'

    script = _PS_TEMPLATE
    script = script.replace("!!AUMID!!",     _AUMID)
    script = script.replace("!!TITLE!!",     _xml_esc(title))
    script = script.replace("!!BODY!!",      _xml_esc(body))
    script = script.replace("!!HERO_LINE!!", hero_line)
    return script


def _launch_ps(script: str) -> None:
    try:
        subprocess.Popen(
            [
                "powershell.exe",
                "-NonInteractive",
                "-WindowStyle", "Hidden",
                "-ExecutionPolicy", "Bypass",
                "-Command", script,
            ],
            creationflags=subprocess.CREATE_NO_WINDOW,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass


# ── Публічний API ─────────────────────────────────────────────────────────────

def show(
    title: str,
    body: str,
    image_path: str | None = None,
    fallback_icon=None,
) -> None:
    _register_aumid()
    try:
        script = _build_ps_script(title, body, image_path)
        threading.Thread(target=_launch_ps, args=(script,), daemon=True).start()
    except Exception:
        if fallback_icon is not None:
            try:
                fallback_icon.notify(body, title)
            except Exception:
                pass
