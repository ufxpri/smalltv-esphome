"""Supervises the control panel server — the widget's one job.

The panel (client/control_panel.py) is the real UI: it switches sources, edits
settings, and shows the live monitor. This module just owns its lifecycle, so
the tray never has to render anything itself.
"""
import json
import os
import sys
import time
import urllib.request

import psutil

import config
import stream

SCRIPT = "control_panel.py"
SCRIPT_PATH = os.path.join(stream.HERE, SCRIPT)
PORT = 8787
URL = f"http://localhost:{PORT}"

# A panel younger than this may simply not have bound the port yet, so it is not
# evidence of a leak. Generous on purpose: a frozen onedir bundle on a cold cache
# can take many seconds just to reach main().
STALE_AFTER = 30.0

# Basename of the frozen bundle, used to recognise its `--run` children.
LAUNCHER = "smalltvwidget"

# How far the recorded start time may drift from the live one before we call it a
# different process. Generous: psutil and the OS can disagree in the last digits.
PID_CLOCK_SLACK = 1.0


def _same_path(a, b):
    # normpath, not abspath: a relative path must never be resolved against OUR
    # cwd when it belongs to another process. Callers resolve it themselves.
    return os.path.normcase(os.path.normpath(a or "")) == os.path.normcase(os.path.normpath(b or ""))


def _is_ours(p):
    """Is this process *our* control panel?

    The fallback identifier, for panels we have no record of (see `_procs`).
    `control_panel.py` is a generic name, and this machine really does run a
    second project's script by that basename — so matching on the basename alone
    (as stream.procs_for does, which is fine for our uniquely named sources)
    would have us report someone else's panel as ours and, worse, terminate it.

    Three forms have to be told apart:

    - `python <abs>/control_panel.py` — spawned by us; compare the path.
    - `python control_panel.py` — started by hand from a shell. The argv carries
      no directory, so resolve it against *that process's* cwd, never ours. This
      is the form the orphan in the incident had, and an earlier version of this
      function missed it, which would have quietly broken stop().
    - `SmallTVWidget.exe --run control_panel.py` — a frozen child (see
      stream.command); no path exists anywhere, so the `--run` marker plus our
      launcher's name identify it. That also lets a source checkout see and stop
      a bundled panel.
    """
    try:
        argv = p.cmdline()[1:]
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False
    for i, arg in enumerate(argv):
        if os.path.basename(arg) != SCRIPT:
            continue
        if not os.path.dirname(arg) and i and argv[i - 1] == "--run":
            try:
                exe = p.exe() or ""
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                return False
            return (_same_path(exe, sys.executable)
                    or os.path.basename(exe).lower().startswith(LAUNCHER))
        if os.path.isabs(arg):
            return _same_path(arg, SCRIPT_PATH)
        try:
            cwd = p.cwd()
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            return False          # can't resolve it -> don't claim it, don't kill it
        return _same_path(os.path.join(cwd, arg), SCRIPT_PATH)
    return False


def _pid_path():
    return os.path.join(str(config.config_dir()), "panel.pid")


def _record_pid(proc):
    """Remember the panel we just started, pinned by its start time.

    A bare pid is not an identity — pids get recycled, and terminating a recycled
    one kills an unrelated program. pid + creation time is unique for the life of
    the machine, so this is proof of ownership rather than the inference
    `_is_ours()` has to make from a command line.
    """
    try:
        started = psutil.Process(proc.pid).create_time()
        with open(_pid_path(), "w", encoding="utf-8") as f:
            f.write(f"{proc.pid} {started:.3f}\n")
    except (OSError, psutil.Error):
        pass          # losing the record costs us the certain path, not correctness


def _forget_pid():
    try:
        os.remove(_pid_path())
    except OSError:
        pass


def _recorded_proc():
    """The panel this widget started, if that exact process is still alive."""
    try:
        with open(_pid_path(), encoding="utf-8") as f:
            pid, started = f.read().split()[:2]
        p = psutil.Process(int(pid))
        if abs(p.create_time() - float(started)) > PID_CLOCK_SLACK:
            return None                     # pid recycled: somebody else's process now
    except (OSError, ValueError, psutil.Error):
        return None
    return p


def _procs():
    """Every panel of ours: the one we recorded, plus any found by inspection.

    The record is authoritative and needs no guessing. The scan is the fallback
    that still finds a panel someone started by hand, or one left behind by an
    older widget whose record is gone — without it, "Server running: off" would
    quietly do nothing for those.
    """
    ours = _recorded_proc()
    out = [ours] if ours else []
    seen = {p.pid for p in out}
    for p in psutil.process_iter():
        if p.pid != os.getpid() and p.pid not in seen and _is_ours(p):
            out.append(p)
    return out


def is_running(attempts: int = 1) -> bool:
    """True only when a panel is actually answering on the port.

    Deliberately *not* "is there a control_panel.py in the process table?". A
    panel that fell out of its serve loop — lost the bind race to another
    instance, threw on startup — sits there forever without listening, and the
    process check then reports a healthy panel that does not exist. That is not
    hypothetical: it made `start()` log "panel already running" and do nothing
    while nothing was serving, leaving the widget with no UI and no way back.
    The port is the only honest signal.

    `attempts` re-probes before concluding "nothing is there": one 2 s timeout
    under load is a false negative, and callers act on that answer by killing
    processes.
    """
    for i in range(max(1, attempts)):
        if status() is not None:
            return True
        if i + 1 < attempts:
            time.sleep(0.4)
    return False


def _stale_procs():
    """Panel processes that are old enough to have bound the port but haven't."""
    now = time.time()
    out = []
    for p in _procs():
        try:
            if now - p.create_time() > STALE_AFTER:
                out.append(p)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return out


def start(device_ip):
    """Start the panel unless one is already answering. Idempotent."""
    if is_running():
        return False
    stale = _stale_procs()
    if stale:
        # About to kill something, so make sure of the premise first: a single
        # timed-out probe is not proof that the panel is dead, and reaping on it
        # would take down a healthy panel mid-request (and its /apply worker).
        if is_running(attempts=3):
            return False
        # Confirmed: nothing serves, yet grown-up panel processes remain. They
        # never will serve — left alone they accumulate one per failed start.
        stream.terminate(stale)
    # --no-browser: at login the widget starts us silently; the user opens the
    # panel from the tray when they actually want it.
    _record_pid(stream.spawn(os.path.join(stream.HERE, SCRIPT),
                             [device_ip, "--no-browser"], "panel"))
    return True


def stop(sources_too=True):
    """Stop the panel. By default also stops whatever it was streaming, since
    nothing would be supervising those processes afterwards."""
    if sources_too:
        stream.stop_all()
    stream.terminate(_procs())
    _forget_pid()


def status():
    """The panel's cached view of the device, or None if the panel is down.

    Read from the panel rather than polled from the device directly: the panel
    already polls it, and a second poller on a device with ~12 KB of free heap
    is worth avoiding.
    """
    try:
        with urllib.request.urlopen(f"{URL}/status", timeout=2) as r:
            return json.load(r)
    except Exception:
        return None
