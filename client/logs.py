"""Logging for every client process — bounded, dated, and quiet when nothing is wrong.

This replaces `print()`. What was wrong with print, measured on the real logs
before this module existed:

  * **Unbounded.** `claude.log` had reached 29 MB / 644,678 lines. Nothing ever
    truncated it, and a stream source runs for weeks.
  * **Undated.** Not one of those lines carried a timestamp, so "when did the
    device reboot" was unanswerable from the log that recorded the reboot.
  * **Unlevelled.** 349,023 of those lines were the routine ten-second poll and
    92,150 were one stuck retry, all indistinguishable from a real fault.

So: one rotating file per process (hard cap `MAX_BYTES * (BACKUPS + 1)`), a
timestamp and a level on every line, routine chatter demoted to DEBUG, and a
filter that refuses to write the same line twice in a row.

    log = logs.setup("codex")      # in main(), once
    log.info("connected to %s", host)
    log.debug("util=%.0f%%", m.util)

`setup()` also routes uncaught exceptions — on the main thread and on worker
threads — through the log, so a crash lands in the same file as everything else
instead of a stray stderr nobody reads. What it cannot catch is a failure before
the interpreter is up (the reaped-`_MEI` bug was exactly that), which is why
`stream.spawn` still keeps a raw `<name>.err.log` alongside.
"""
import logging
import logging.handlers
import os
import sys
import tempfile
import threading
import time

MAX_BYTES = 1_000_000        # per file; with BACKUPS that is a 4 MB ceiling per process
BACKUPS = 3
FMT = "%(asctime)s %(levelname).1s %(name)s: %(message)s"
DATEFMT = "%m-%d %H:%M:%S"
ENV_LEVEL = "SMALLTV_LOG_LEVEL"


def log_dir():
    """Where logs live, per OS. The single owner of this path — `stream.py`
    imports it from here rather than defining its own."""
    if sys.platform == "darwin":
        d = os.path.expanduser("~/Library/Logs/SmallTVWidget")
    elif sys.platform == "win32":
        d = os.path.join(os.environ.get("LOCALAPPDATA", tempfile.gettempdir()),
                         "SmallTVWidget", "Logs")
    else:
        d = os.path.expanduser("~/.local/state/smalltv/logs")
    os.makedirs(d, exist_ok=True)
    return d


LOGDIR = log_dir()


def log_path(name):
    return os.path.join(LOGDIR, f"{name}.log")


class Collapse(logging.Filter):
    """Refuse to write the same line twice in a row.

    A stuck condition retries on a timer and writes one identical line per
    attempt: a name that would not resolve produced 92,150 of them, burying
    everything else. The first occurrence goes through; repeats are counted and
    silently dropped until `HEARTBEAT` has passed, at which point one line is
    emitted carrying the tally so a long outage still shows it is ongoing.

    The trailing repeats of a run that ends before the next heartbeat are not
    reported — deliberately, since it takes a second record to say so and the
    exact count of a transient blip is not worth one.
    """
    HEARTBEAT = 300.0        # seconds before a stuck message is allowed through again

    def __init__(self):
        super().__init__()
        self._key = None
        self._n = 0
        self._since = 0.0

    def filter(self, record):
        key = (record.name, record.levelno, record.getMessage())
        now = time.time()
        if key != self._key:
            self._key, self._n, self._since = key, 0, now
            return True
        self._n += 1
        if now - self._since < self.HEARTBEAT:
            return False
        record.msg = f"{record.getMessage()}   (x{self._n + 1} in {int(now - self._since)}s)"
        record.args = ()
        self._n, self._since = 0, now
        return True


def level(default=logging.INFO):
    """The configured level: $SMALLTV_LOG_LEVEL, else the panel's `log_level`,
    else INFO. Read at setup — a level change takes effect on the next start."""
    name = os.environ.get(ENV_LEVEL)
    if not name:
        try:
            import config as cfg_mod
            name = cfg_mod.load().get("log_level")
        except Exception:
            name = None
    return getattr(logging, str(name).upper(), default) if name else default


def setup(name, console=None):
    """Configure logging for this process and return its logger.

    `console` defaults to on when stdout is a terminal: a source run by hand
    prints to the terminal, while one spawned by the widget writes only the
    file (its stdout is already redirected to the `.err.log` catch-all, and
    duplicating every line there would defeat the rotation).
    """
    if console is None:
        console = sys.stdout is not None and sys.stdout.isatty()
    root = logging.getLogger()
    root.setLevel(level())
    for h in list(root.handlers):        # idempotent: --run may re-enter a process
        root.removeHandler(h)

    fmt = logging.Formatter(FMT, DATEFMT)
    fh = logging.handlers.RotatingFileHandler(
        log_path(name), maxBytes=MAX_BYTES, backupCount=BACKUPS, encoding="utf-8")
    fh.setFormatter(fmt)
    fh.addFilter(Collapse())
    root.addHandler(fh)
    if console:
        ch = logging.StreamHandler(sys.stdout)
        ch.setFormatter(fmt)
        ch.addFilter(Collapse())
        root.addHandler(ch)

    _catch_crashes(logging.getLogger(name))
    return logging.getLogger(name)


def _catch_crashes(log):
    """Send uncaught exceptions to the log instead of a stderr nobody reads."""
    def hook(exc_type, exc, tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc, tb)
            return
        log.critical("uncaught exception", exc_info=(exc_type, exc, tb))
    sys.excepthook = hook

    def thread_hook(args):
        if issubclass(args.exc_type, KeyboardInterrupt):
            return
        log.critical("uncaught exception in thread %s", args.thread.name,
                     exc_info=(args.exc_type, args.exc_value, args.exc_traceback))
    threading.excepthook = thread_hook


def tail(name, lines=200):
    """The last `lines` of a process's log, newest last. Reads the rotated
    predecessor too, so a tail taken just after a rotation is not nearly empty."""
    out = []
    for path in (log_path(name) + ".1", log_path(name)):
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                out.extend(f.read().splitlines())
        except OSError:
            pass
    return out[-lines:]


def names():
    """Every process that has a log, newest first."""
    try:
        files = [f[:-4] for f in os.listdir(LOGDIR) if f.endswith(".log")]
    except OSError:
        return []
    return sorted(files, key=lambda n: os.path.getmtime(log_path(n)), reverse=True)
