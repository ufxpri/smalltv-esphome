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

# device_ip stays an address rather than `smalltv-ultra.local`: the hostname is
# DHCP-stable but mDNS does not resolve on every box (it fails on Windows here),
# so a literal address is the safer first-run default. Edit it in the panel.
DEFAULTS = {
    "device_ip": "192.168.219.112",
    "start_at_login": False,
    "tickers": ["AAPL"],        # the stocks source cycles these
    "ticker_rotate": 15.0,      # seconds per ticker
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


def save(cfg: dict) -> None:
    path = config_path()
    tmp = path.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    tmp.replace(path)
