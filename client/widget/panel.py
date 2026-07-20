"""Supervises the control panel server — the widget's one job.

The panel (client/control_panel.py) is the real UI: it switches sources, edits
settings, and shows the live monitor. This module just owns its lifecycle, so
the tray never has to render anything itself.
"""
import json
import os
import urllib.request

import stream

SCRIPT = "control_panel.py"
PORT = 8787
URL = f"http://localhost:{PORT}"


def _procs():
    return [p for p, _ in stream.procs_for({SCRIPT})]


def is_running() -> bool:
    return bool(_procs())


def start(device_ip):
    """Start the panel unless it is already up. Idempotent."""
    if is_running():
        return False
    # --no-browser: at login the widget starts us silently; the user opens the
    # panel from the tray when they actually want it.
    stream.spawn(os.path.join(stream.HERE, SCRIPT), [device_ip, "--no-browser"], "panel")
    return True


def stop(sources_too=True):
    """Stop the panel. By default also stops whatever it was streaming, since
    nothing would be supervising those processes afterwards."""
    if sources_too:
        stream.stop_all()
    stream.terminate(_procs())


def status():
    """The panel's cached view of the device, or None if the panel is down.

    Read from the panel rather than polled from the device directly: the panel
    already polls it, and a second poller on a device with ~23 KB of free heap
    is worth avoiding.
    """
    try:
        with urllib.request.urlopen(f"{URL}/status", timeout=2) as r:
            return json.load(r)
    except Exception:
        return None
