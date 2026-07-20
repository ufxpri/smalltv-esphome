"""Start-at-login integration.

Windows: an entry under HKCU\\...\\Run.
macOS:   a LaunchAgent plist with RunAtLoad.
Linux:   an XDG autostart .desktop file.

All three are per-user (no admin/root needed).
"""
import sys
from pathlib import Path

from config import APP_NAME
from .launch import self_command

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_MAC_LABEL = "com.smalltv.widget"


def _quote(cmd):
    return " ".join(f'"{c}"' if " " in c else c for c in cmd)


# ------------------------------------------------------------------- win ----
def _win_enable():
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ, _quote(self_command()))


def _win_disable():
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, APP_NAME)
    except FileNotFoundError:
        pass


def _win_is_enabled():
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as k:
            winreg.QueryValueEx(k, APP_NAME)
            return True
    except FileNotFoundError:
        return False


# ------------------------------------------------------------------- mac ----
def _mac_plist_path():
    return Path.home() / "Library" / "LaunchAgents" / f"{_MAC_LABEL}.plist"


def _mac_enable():
    cmd = self_command()
    args = "".join(f"    <string>{c}</string>\n" for c in cmd)
    plist = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0"><dict>\n'
        f'  <key>Label</key><string>{_MAC_LABEL}</string>\n'
        '  <key>ProgramArguments</key><array>\n'
        f'{args}'
        '  </array>\n'
        '  <key>RunAtLoad</key><true/>\n'
        '</dict></plist>\n'
    )
    p = _mac_plist_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(plist, encoding="utf-8")


def _mac_disable():
    _mac_plist_path().unlink(missing_ok=True)


def _mac_is_enabled():
    return _mac_plist_path().exists()


# ----------------------------------------------------------------- linux ----
def _linux_desktop_path():
    return Path.home() / ".config" / "autostart" / f"{APP_NAME}.desktop"


def _linux_enable():
    p = _linux_desktop_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        "[Desktop Entry]\nType=Application\n"
        f"Name={APP_NAME}\nExec={_quote(self_command())}\n"
        "X-GNOME-Autostart-enabled=true\n",
        encoding="utf-8",
    )


def _linux_disable():
    _linux_desktop_path().unlink(missing_ok=True)


def _linux_is_enabled():
    return _linux_desktop_path().exists()


# ---------------------------------------------------------------- public ----
def _impl():
    if sys.platform == "win32":
        return _win_enable, _win_disable, _win_is_enabled
    if sys.platform == "darwin":
        return _mac_enable, _mac_disable, _mac_is_enabled
    return _linux_enable, _linux_disable, _linux_is_enabled


def set_enabled(enabled: bool):
    en, dis, _ = _impl()
    (en if enabled else dis)()


def is_enabled() -> bool:
    return _impl()[2]()
