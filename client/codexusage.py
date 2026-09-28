"""Codex usage/limits for the SmallTV `codex` stream source.

The Codex counterpart of `claudeusage`, and deliberately the same shape: a live
account API authenticated by a long-lived browser session cookie, with the burn
curve recorded into a per-window history file (see `burnmodel`).

    GET https://chatgpt.com/api/auth/session          cookie: __Secure-next-auth.session-token
        -> {"accessToken": "...", "expires": "..."}   (mints a short-lived bearer)
    GET https://chatgpt.com/backend-api/wham/usage    Authorization: Bearer <accessToken>
        -> {"plan_type": "plus",
            "rate_limit": {"primary_window":   {"used_percent", "limit_window_seconds",
                                                "reset_at"},
                           "secondary_window": {... 604800s ...}}, ...}

`wham` is the backend's name for Codex. `primary_window` is the 5h limit,
`secondary_window` the weekly one — the same pair the Codex CLI reports. Both
requests go through curl_cffi's browser impersonation, because chatgpt.com sits
behind Cloudflare exactly as claude.ai does.

Why the cookie and not the CLI's token: `~/.codex/auth.json` holds an OAuth
access token that expires in hours, and it only exists on a machine that runs
Codex. The cookie outlives it and is the only thing to paste when Codex runs
somewhere else entirely.

The session cookie is read from a 0600 file in the app-support dir, never the repo:
    {config_dir}/codex_session.json  = {"session_token": "..."}
Rotate it by logging chatgpt.com out of all devices, then re-saving in the panel.

**Fallback, no key required.** With no cookie saved, the module reads the
rate-limit snapshots Codex writes into its own session logs
(`~/.codex/sessions/**/rollout-*.jsonl`) instead. Those are dated observations,
so they *are* the history — but they exist only on the machine running Codex and
only while it runs, so the model reports `age_s` and the screen says IDLE. The
fallback is chosen only when there is no cookie at all: once one is saved, an API
failure is an error, never a silent slide back to stale local numbers.
"""
import datetime as dt
import glob
import json
import os
from dataclasses import dataclass

import burnmodel as bm
import config as cfg_mod

SESSION_FILE = cfg_mod.config_dir() / "codex_session.json"
HISTORY_FILE = cfg_mod.config_dir() / "codex_burn_history.json"
SESSION_API = "https://chatgpt.com/api/auth/session"
USAGE_API = "https://chatgpt.com/backend-api/wham/usage"
COOKIE = "__Secure-next-auth.session-token"

# Codex's own env var wins, so a non-default CODEX_HOME needs no config here.
CODEX_DIR = os.environ.get("CODEX_HOME") or os.path.expanduser("~/.codex")
ROLLOUT_GLOBS = (os.path.join("sessions", "**", "rollout-*.jsonl"),
                 os.path.join("archived_sessions", "rollout-*.jsonl"))

BURN_NBINS = 10           # the primary window split into ten bins -> 30 min each
FALLBACK_WINDOW_S = 5 * 3600
FRESH_SECS = 150.0        # a sample newer than this means Codex is running right now
LOOKBACK_H = 13.0         # log fallback: far enough back that any 5h window fits
MAX_FILES = 40            # ...but never trawl the whole archive for one screen


class AuthError(ValueError):
    """chatgpt.com rejected the session cookie (401/403). Unlike a transient
    network error this never heals on its own — the cookie must be re-saved from
    the browser, so callers should surface it instead of retrying quietly."""


class NoDataError(ValueError):
    """No cookie saved *and* no snapshot in the local logs: nothing to draw."""


# ---- the session cookie ----

def load_secret():
    with open(SESSION_FILE) as f:
        return json.load(f)["session_token"]


def has_secret():
    try:
        return bool(load_secret())
    except Exception:
        return False


def fetch_access_token(session_token, timeout=25):
    """Mint a bearer token from the session cookie. Doubles as a validity check —
    an expired cookie answers with no accessToken at all."""
    from curl_cffi import requests   # imported lazily so the module loads without it
    r = requests.get(SESSION_API, cookies={COOKIE: session_token.strip()},
                     headers={"accept": "*/*", "referer": "https://chatgpt.com/"},
                     impersonate="chrome", timeout=timeout)
    if r.status_code in (401, 403):
        raise AuthError("세션 쿠키가 유효하지 않거나 만료되었습니다")
    r.raise_for_status()
    tok = (r.json() or {}).get("accessToken")
    if not tok:
        # A logged-out session answers 200 with an empty object rather than 401.
        raise AuthError("세션 쿠키가 만료되었습니다 (로그인 상태가 아님)")
    return tok


def save_secret(session_token):
    """Persist the session cookie to the 0600 secret file after checking that it
    actually works. Returns the plan type the account reports."""
    session_token = (session_token or "").strip()
    if not session_token:
        raise ValueError("세션 쿠키가 비어 있습니다")
    raw = fetch_raw(session_token=session_token)          # validates before saving
    SESSION_FILE.write_text(json.dumps({"session_token": session_token}, indent=2))
    os.chmod(SESSION_FILE, 0o600)
    return raw.get("plan_type") or ""


def secret_status():
    """What the panel can show without echoing the cookie back."""
    try:
        key = load_secret()
        return {"saved": True, "key_hint": key[-6:] if len(key) > 6 else "••"}
    except Exception:
        return {"saved": False, "key_hint": ""}


def fetch_raw(timeout=25, session_token=None):
    """Live usage JSON from chatgpt.com, or raise."""
    from curl_cffi import requests
    token = session_token or load_secret()
    bearer = fetch_access_token(token, timeout=timeout)
    r = requests.get(USAGE_API,
                     headers={"Authorization": f"Bearer {bearer}", "accept": "*/*",
                              "referer": "https://chatgpt.com/codex"},
                     impersonate="chrome", timeout=timeout)
    if r.status_code in (401, 403):
        raise AuthError("사용량 API가 요청을 거부했습니다")
    r.raise_for_status()
    return r.json()


# ---- the local-log fallback ----

@dataclass
class Sample:
    """One rate-limit snapshot as Codex logged it on this machine."""
    t: dt.datetime
    limit_id: str
    limit_name: str
    plan: str
    primary: float
    p_window_s: int
    p_resets: int
    secondary: float
    s_resets: int


# Rollout files are append-only, so each is parsed once and then only from where
# we stopped: {path: [mtime, size, offset, [Sample, ...]]}.
_CACHE = {}


def _samples_in(path):
    ent = _CACHE.get(path)
    try:
        st = os.stat(path)
    except OSError:
        return []
    if ent and ent[0] == st.st_mtime and ent[1] == st.st_size:
        return ent[3]
    if not ent or st.st_size < ent[2]:      # new file, or truncated/rewritten
        ent = [0.0, 0, 0, []]
    try:
        with open(path, "rb") as fh:
            fh.seek(ent[2])
            chunk = fh.read()
    except OSError:
        return ent[3]
    lines = chunk.split(b"\n")
    # A turn may be half-written: keep the trailing fragment unconsumed so the
    # next poll sees that line whole rather than dropping it.
    tail = lines.pop()
    ent[2] += len(chunk) - len(tail)
    for raw in lines:
        if b'"rate_limits":{' not in raw:
            continue
        s = _parse_line(raw)
        if s:
            ent[3].append(s)
    ent[0], ent[1] = st.st_mtime, st.st_size
    _CACHE[path] = ent
    return ent[3]


def _parse_line(raw):
    try:
        rec = json.loads(raw)
        rl = (rec.get("payload") or {}).get("rate_limits") or {}
        pri, sec = rl.get("primary") or {}, rl.get("secondary") or {}
        if pri.get("used_percent") is None or not pri.get("resets_at"):
            return None
        return Sample(t=dt.datetime.fromisoformat(rec["timestamp"].replace("Z", "+00:00")),
                      limit_id=rl.get("limit_id") or "codex",
                      limit_name=rl.get("limit_name") or "",
                      plan=rl.get("plan_type") or "",
                      primary=float(pri["used_percent"]),
                      p_window_s=int(pri.get("window_minutes") or 300) * 60,
                      p_resets=int(pri["resets_at"]),
                      secondary=float(sec.get("used_percent") or 0.0),
                      s_resets=int(sec.get("resets_at") or 0))
    except Exception:
        return None


def collect(now=None, lookback_h=LOOKBACK_H):
    """Every snapshot from the recent logs, oldest first.

    Files are taken newest-first and the walk stops once it is past the lookback
    *and* has something to show: when Codex has been idle for a week the last
    known reading is still what the screen needs, so "nothing recent" must not
    mean "nothing at all".
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    cutoff = now.timestamp() - lookback_h * 3600
    paths = []
    for pat in ROLLOUT_GLOBS:
        paths += glob.glob(os.path.join(CODEX_DIR, pat), recursive=True)
    paths.sort(key=lambda p: os.path.getmtime(p) if os.path.exists(p) else 0, reverse=True)
    out = []
    for p in paths[:MAX_FILES]:
        if out and os.path.getmtime(p) < cutoff:
            break
        out += _samples_in(p)
    out.sort(key=lambda s: s.t)
    return out


def status():
    """What the panel shows for the Codex pane: whether a cookie is saved, and
    the current reading from whichever feed is actually in use."""
    st = secret_status()
    st["dir"] = CODEX_DIR
    try:
        m = burn_model()
    except Exception as e:
        st.update(found=False, error=str(e).split("\n")[0][:120])
        return st
    st.update(found=True, source=m.source, age_s=int(m.age_s), plan=m.plan,
              limit_name=m.limit_name, primary=m.util, secondary=m.weekly)
    return st


# ---- burn model ----

@dataclass
class BurnModel(bm.BurnModel):
    """The shared burn model plus what only Codex has: which feed answered, and
    how stale the local-log fallback's newest snapshot is."""
    source: str              # "api" | "log"
    age_s: float             # seconds since the newest observation (0 on the API)
    rolled: bool             # log fallback: the logged window had expired -> 0
    plan: str                # "plus" / "pro" / ""
    limit_name: str          # scoped limit's name, when the feed reports one

    @property
    def live(self):
        """The API is live by definition; the log feed only while Codex runs."""
        return self.source == "api" or self.age_s < FRESH_SECS


def _epoch(ts):
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc)


def burn_model(now=None):
    """Live burn model for the primary window, from the API when a cookie is
    saved and from Codex's local logs otherwise."""
    now = now or dt.datetime.now(dt.timezone.utc)
    return _api_model(now) if has_secret() else _log_model(now)


def _api_model(now):
    raw = fetch_raw()
    rl = raw.get("rate_limit") or {}
    pri, sec = rl.get("primary_window") or {}, rl.get("secondary_window") or {}
    if pri.get("used_percent") is None or not pri.get("reset_at"):
        raise ValueError("활성 Codex 한도 정보가 없습니다")
    util = float(pri["used_percent"])
    window_h = int(pri.get("limit_window_seconds") or FALLBACK_WINDOW_S) / 3600.0
    end_dt = _epoch(int(pri["reset_at"]))
    # The window is a fixed [reset, next-reset] span; the grid is pinned to the
    # history's stored start so a jittering reset_at can't shift past bars.
    start_dt, samples = bm.history_samples(HISTORY_FILE, end_dt, window_h, now, util)
    end_dt = start_dt + dt.timedelta(hours=window_h)
    weekly = float(sec.get("used_percent") or 0.0)
    return bm.build(BurnModel, util, weekly, start_dt, end_dt, window_h, now,
                    samples, BURN_NBINS, source="api", age_s=0.0, rolled=False,
                     plan=raw.get("plan_type") or "", limit_name="")


def _log_model(now):
    samples = collect(now)
    if not samples:
        raise NoDataError("세션 쿠키도, 로컬 Codex 기록도 없습니다")

    # One curve per limit: a model-scoped limit (limit_id "codex_<model>") counts
    # separately from the account one, so mixing them would draw a curve that
    # never existed. The limit that logged most recently is the live one.
    newest = samples[-1]
    samples = [s for s in samples if s.limit_id == newest.limit_id]

    window_h = newest.p_window_s / 3600.0
    end_dt = _epoch(newest.p_resets)
    rolled = end_dt <= now
    if rolled:
        # The window Codex last reported has since expired: usage went back to 0
        # and nothing has been logged in the window we're now in. Roll the grid
        # forward whole windows so the axis still shows a real span.
        step = dt.timedelta(hours=window_h)
        while end_dt <= now:
            end_dt += step
        util, samples = 0.0, []
    else:
        util = newest.primary
    start_dt = end_dt - dt.timedelta(hours=window_h)
    pairs = [(s.t, s.primary) for s in samples if start_dt <= s.t <= now]
    weekly = 0.0 if (newest.s_resets and _epoch(newest.s_resets) <= now) else newest.secondary
    return bm.build(BurnModel, util, weekly, start_dt, end_dt, window_h, now,
                    pairs, BURN_NBINS, source="log", age_s=(now - newest.t).total_seconds(), rolled=rolled,
                     plan=newest.plan, limit_name=newest.limit_name)


if __name__ == "__main__":
    m = burn_model()
    print(f"source={m.source} limit={m.limit_name or 'account'} plan={m.plan or '?'}  "
          f"util={m.util:.0f}%  weekly={m.weekly:.0f}%")
    print(f"window {m.start_dt.astimezone():%H:%M} -> {m.end_dt.astimezone():%H:%M}  "
          f"bin={m.bin_min:.0f}min  now_bin={m.now_bin}  elapsed={m.elapsed_h:.2f}h")
    print(f"slope={m.slope:.1f}%/h  hits_in={m.hits_in_h:.2f}h  state={m.state}  "
          f"age={m.age_s / 60:.1f}min live={m.live} rolled={m.rolled}")
    print("bars:", [round(p) for p in m.cum_pct])
