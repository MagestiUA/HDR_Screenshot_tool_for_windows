"""
Автозапуск при старті Windows через HKCU\\...\\Run.

Працює як для запуску з exe (PyInstaller), так і з вихідного коду.
"""
import os
import sys
import winreg

_APP_NAME = "HDRScreenshotTool"
_RUN_KEY  = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run"


def _launch_value() -> str:
    """Повертає рядок для запису в реєстр (шлях до exe або pythonw + скрипт)."""
    if getattr(sys, "frozen", False):
        # PyInstaller exe — просто шлях до виконуваного файлу
        return f'"{sys.executable}"'
    else:
        # Запуск з вихідного коду — pythonw.exe щоб не було консолі
        pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        if not os.path.exists(pythonw):
            pythonw = sys.executable          # fallback до python.exe
        main_py = os.path.join(os.path.dirname(os.path.abspath(__file__)), "main.py")
        return f'"{pythonw}" "{main_py}"'


def is_enabled() -> bool:
    """Повертає True якщо автозапуск увімкнено в реєстрі."""
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY,
                             0, winreg.KEY_READ)
        winreg.QueryValueEx(key, _APP_NAME)
        winreg.CloseKey(key)
        return True
    except OSError:
        return False


def enable() -> None:
    """Додає запис автозапуску в реєстр."""
    key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY,
                         0, winreg.KEY_SET_VALUE)
    winreg.SetValueEx(key, _APP_NAME, 0, winreg.REG_SZ, _launch_value())
    winreg.CloseKey(key)


def disable() -> None:
    """Видаляє запис автозапуску з реєстру."""
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY,
                             0, winreg.KEY_SET_VALUE)
        winreg.DeleteValue(key, _APP_NAME)
        winreg.CloseKey(key)
    except OSError:
        pass
