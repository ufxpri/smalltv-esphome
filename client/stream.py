#!/usr/bin/env python3
"""Switch what the SmallTV streams. Only one source runs at a time (the device
accepts a single stream client); `off` stops streaming so the device falls back
to its local clock page.

    python stream.py furnace              # CPU-load furnace
    python stream.py stickers             # OGQ sticker slideshow
    python stream.py stocks AAPL MSFT     # candlestick chart, cycling tickers
    python stream.py sectors              # S&P sector heatmap
    python stream.py video <file>         # play a video file
    python stream.py off                  # stop streaming (-> local clock)
    python stream.py status               # show what's running

Add `--host <ip>` to target a device other than the default; sources also read
SMALLTV_HOST, which is how the control panel points them at the right device.
"""
import os
import subprocess
import sys
import tempfile

import psutil

HERE = os.path.dirname(os.path.abspath(__file__))

# Prefer the repo venv's interpreter; its layout differs per OS.
_VENV = os.path.join(HERE, os.pardir, ".venv")
_PYS = [os.path.join(_VENV, "Scripts", "python.exe"),   # Windows
        os.path.join(_VENV, "bin", "python")]           # Mac/Linux
PY = next((p for p in _PYS if os.path.exists(p)), sys.executable)

if sys.platform == "darwin":
    LOGDIR = os.path.expanduser("~/Library/Logs/SmallTVWidget")
elif sys.platform == "win32":
    LOGDIR = os.path.join(os.environ.get("LOCALAPPDATA", tempfile.gettempdir()),
                          "SmallTVWidget", "Logs")
else:
    LOGDIR = os.path.expanduser("~/.local/state/smalltv/logs")

SOURCES = {
    "furnace": "smalltv_stream.py",
    "stickers": "stream_gif.py",
    "stocks": "stream_stocks.py",
    "sectors": "stream_sectors.py",
    "video": "stream_video.py",
}
ALL = list(SOURCES.values())


# ---- process plumbing (shared with the widget, which supervises the panel) ----

def procs_for(names):
    """(process, script) for every process running one of these script basenames.

    Matches argv entries by basename rather than searching the whole command line
    for a substring: `stream.py` is a substring of `smalltv_stream.py`, so
    substring matching would let the launcher and its sources kill each other.
    """
    for p in psutil.process_iter(["pid"]):
        if p.pid == os.getpid():
            continue
        try:
            argv = p.cmdline()[1:]
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        for arg in argv:
            base = os.path.basename(arg)
            if base in names:
                yield p, base
                break


def terminate(procs):
    procs = list(procs)
    for p in procs:
        try:
            p.terminate()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    _, alive = psutil.wait_procs(procs, timeout=3)
    for p in alive:                       # ignored the term signal / refused to exit
        try:
            p.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass


def gif_dir():
    """Where sticker GIFs live.

    From source: client/gifs/. Frozen: alongside config.json, because a bundle's
    own directory is a temp extraction dir the user can't drop files into.
    """
    if getattr(sys, "frozen", False):
        import config
        d = config.config_dir() / "gifs"
        d.mkdir(parents=True, exist_ok=True)
        return str(d)
    return os.path.join(HERE, "gifs")


def command(script, extra):
    """argv to run one of our scripts — frozen or from source.

    A PyInstaller bundle ships no interpreter and no loose .py files, so
    `sys.executable` there is the widget exe, not python. In that case re-enter
    the same exe and let its `--run` dispatcher import the module. Either way the
    script's basename stays in argv, which is what procs_for() matches on.
    """
    if getattr(sys, "frozen", False):
        return [sys.executable, "--run", os.path.basename(script), *extra]
    # -u: stdout here is a file, so Python would block-buffer it and the log would
    # sit empty for ages — and for a detached child the log is the only way to see
    # what it's doing.
    return [PY, "-u", script, *extra]


def spawn(script, extra, log_name, env_extra=None):
    """Launch a detached child that logs to LOGDIR and outlives this process."""
    os.makedirs(LOGDIR, exist_ok=True)
    # utf-8: the default console encoding on a Korean Windows box is cp949, which
    # cannot encode the em dash / arrows these scripts log.
    log = open(os.path.join(LOGDIR, f"{log_name}.log"), "a", encoding="utf-8")
    env = {**os.environ, **(env_extra or {})}
    if sys.platform == "win32":
        kw = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS}
    else:
        kw = {"start_new_session": True}
    return subprocess.Popen(command(script, extra), stdout=log, stderr=subprocess.STDOUT,
                            cwd=HERE, env=env, **kw)


# ---- sources ----

def stop_all():
    terminate(p for p, _ in procs_for(ALL))


def running():
    return sorted({s for _, s in procs_for(ALL)})


def start(source, extra, host=None):
    env = {"SMALLTV_TELEMETRY": "1"}      # emit live monitor data for the panel
    if host:
        env["SMALLTV_HOST"] = host        # uniform channel; see resolve_host()
    spawn(os.path.join(HERE, SOURCES[source]), extra, source, env)


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")   # so the em dash doesn't crash cp949 consoles
    except Exception:
        pass
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    cmd = sys.argv[1]
    extra = sys.argv[2:]

    if cmd == "status":
        r = running()
        print("streaming:", ", ".join(r) if r else "(none — device shows local clock)")
        return
    if cmd in ("off", "stop"):
        stop_all()
        print("stopped — device falls back to its local clock in a few seconds")
        return
    if cmd in SOURCES:
        if cmd == "video" and not extra:
            sys.exit("usage: python stream.py video <file>")
        stop_all()
        start(cmd, extra)
        print(f"now streaming: {cmd}")
        return
    sys.exit(f"unknown source '{cmd}'. options: {', '.join(SOURCES)}, off, status")


if __name__ == "__main__":
    main()
