"""Persistent config for the SmallTV widget.

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

DEFAULTS = {
    "device_ip": "192.168.219.112",
    "modes": ["Clock", "Stocks", "PC Info", "Weather", "Off"],
    "brightness": 0.8,
    "start_at_login": False,
    "stock": {
        "ticker": "AAPL",
        "interval": 6.0,
        "autostart": False,
    },
    "pcstats": {
        "title": "PC Monitor",
        "interval": 2.0,
        "autostart": False,
    },
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
    """Return config with defaults filled in for any missing keys."""
    try:
        with open(config_path(), "r", encoding="utf-8") as f:
            user = json.load(f)
    except (FileNotFoundError, ValueError):
        user = {}
    return _deep_merge(DEFAULTS, user)


def save(cfg: dict) -> None:
    path = config_path()
    tmp = path.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    tmp.replace(path)
