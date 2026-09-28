"""Persistent config, shared by the tray widget and the control panel.

The widget needs `device_ip` to launch the panel against the right device; the
panel is where that value is actually edited. Both import this module, so it
sits next to control_panel.py rather than inside the widget package.

Stored as JSON in the per-user app-data dir:
  Windows: %APPDATA%\\SmallTVWidget\\config.json
  macOS:   ~/Library/Application Support/SmallTVWidget/config.json
  Linux:   ~/.config/SmallTVWidget/config.json
"""
import copy
import json
import os
import sys
from pathlib import Path

APP_NAME = "SmallTVWidget"

# device_ip defaults to the firmware's mDNS name. mDNS does not resolve on every
# box (it fails on some Windows setups), so if the panel shows the device as
# unreachable, set its literal address there — that value is saved and wins.
DEFAULTS = {
    "device_ip": "smalltv-ultra.local",
    "start_at_login": False,
    "tickers": ["AAPL"],        # the stocks source cycles these
    "ticker_rotate": 15.0,      # seconds per ticker
    "brightness": 70,           # backlight %; the panel shows it without waiting on the device
    "claude_gif": "",           # gif shown in the claude usage screen's box (name in gif_dir)
    "codex_gif": "",            # ...and in the codex one's (both default to their mascot)
    "usage_rotate": 20.0,       # seconds per screen when the two usage screens alternate
    "log_level": "INFO",        # DEBUG adds the per-poll numbers; see logs.py
}


def config_dir() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = str(Path.home() / "Library" / "Application Support")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    d = Path(base) / APP_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def config_path() -> Path:
    return config_dir() / "config.json"


def _deep_merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load() -> dict:
    """Config with defaults filled in for missing keys, and unknown keys dropped.

    Dropping is deliberate: an existing config.json still carries the page and
    bridge settings (modes, rotation, stock, pcstats, sectors) that died with the
    device pages. Keeping them would leave a file that reads like those features
    are still wired up. They are rewritten out on the next save().
    """
    try:
        with open(config_path(), "r", encoding="utf-8") as f:
            user = json.load(f)
    except (FileNotFoundError, ValueError):
        user = {}
    merged = _deep_merge(DEFAULTS, user)
    return {k: merged[k] for k in DEFAULTS}


def write_private(path: Path, text: str) -> None:
    """Write a secret file that is 0600 from its first byte.

    write_text() then chmod() leaves a window where the key sits at the umask
    default (usually world-readable). os.open with a mode creates it private;
    the rename swaps it in whole, so a reader never sees a half-written key.
    """
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def save(cfg: dict) -> None:
    path = config_path()
    tmp = path.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    tmp.replace(path)
