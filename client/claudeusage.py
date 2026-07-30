"""Claude usage/limits for the SmallTV `claude` stream source.

Two independent data sources, joined here:
  * the live claude.ai limits API — utilization % and reset times per rate-limit
    window (session 5h, weekly 7d, per-model weekly). Behind Cloudflare, which
    fingerprints plain HTTP clients, so the request is made with curl_cffi's
    browser impersonation; the long-lived `sessionKey` cookie authenticates it.
  * the local `~/.claude/usage.db` (written by Claude Code) — exact token counts
    per turn, aggregated per window for a "tokens this week" readout.

The session key is read from a 0600 file in the app-support dir, never the repo:
    {config_dir}/claude_session.json  = {"org_id": "...", "session_key": "sk-ant-sid02-..."}
Rotate it by logging claude.ai out of all devices, then rewrite that file.
"""
import datetime as dt
import json
import math
import os
import sqlite3
from dataclasses import dataclass

import config as cfg_mod

USAGE_DB = os.path.expanduser("~/.claude/usage.db")
SESSION_FILE = cfg_mod.config_dir() / "claude_session.json"
API = "https://claude.ai/api/organizations/{org}/usage"

# rate-limit window lengths, for turning "resets_at" into an elapsed fraction.
WINDOW_SECS = {"five_hour": 5 * 3600, "seven_day": 7 * 86400}

# burn histogram: the 5h session window split into 30-minute bins.
BURN_BIN_MIN = 30
BURN_NBINS = 10


@dataclass
class Gauge:
    """One rate-limit window, ready to draw as a gauge racing a clock hand."""
    key: str                 # five_hour / weekly_all / weekly_scoped
    label: str               # human label for the card ("세션 5h", "주간", "Fable")
    usage: float             # 0..1 utilization (the arc)
    time_progress: float     # 0..1 elapsed fraction of the window (the clock hand)
    resets_in: float         # seconds until reset
    tokens: int | None = None  # output tokens in this window from usage.db (optional)

    @property
    def pace(self):
        """>0 means burning faster than the clock (usage ahead of time)."""
        return self.usage - self.time_progress


def load_secret():
    with open(SESSION_FILE) as f:
        d = json.load(f)
    return d["org_id"], d["session_key"]


def fetch_org_id(session_key):
    """The organization uuid for a session key, via claude.ai's org list. Doubles
    as a validity check — a bad/expired key raises here. Prefers an org that can
    chat (the personal one) over any read-only/api org."""
    from curl_cffi import requests
    r = requests.get("https://claude.ai/api/organizations",
                     cookies={"sessionKey": session_key.strip()},
                     headers={"anthropic-client-platform": "web_claude_ai",
                              "accept": "*/*", "referer": "https://claude.ai/new"},
                     impersonate="chrome", timeout=25)
    if r.status_code in (401, 403):
        raise ValueError("세션 키가 유효하지 않거나 만료되었습니다")
    r.raise_for_status()
    orgs = r.json()
    if not orgs:
        raise ValueError("이 세션 키로 조직을 찾지 못했습니다")
    for o in orgs:
        if "chat" in (o.get("capabilities") or []):
            return o["uuid"]
    return orgs[0]["uuid"]


def save_secret(session_key, org_id=""):
    """Persist the session key (and org id) to the 0600 secret file. With no org
    id, it is auto-detected from the key — which also validates the key. Returns
    the org id actually saved. Raises if a blank/invalid key can't be resolved."""
    session_key = (session_key or "").strip()
    org_id = (org_id or "").strip()
    if not session_key:
        raise ValueError("세션 키가 비어 있습니다")
    if not org_id:
        org_id = fetch_org_id(session_key)
    SESSION_FILE.write_text(json.dumps({"org_id": org_id, "session_key": session_key}, indent=2))
    os.chmod(SESSION_FILE, 0o600)
    return org_id


def secret_status():
    """What the panel can show without echoing the key back: whether one is saved,
    the org id, and a short tail hint of the key."""
    try:
        org, key = load_secret()
        return {"saved": True, "org_id": org,
                "key_hint": key[-6:] if len(key) > 6 else "••"}
    except Exception:
        return {"saved": False, "org_id": "", "key_hint": ""}


def fetch_raw(timeout=25):
    """Live limits JSON from claude.ai, or raise. Browser impersonation is what
    gets us past Cloudflare; sessionKey alone (no cf_clearance) is enough."""
    from curl_cffi import requests   # imported lazily so the module loads without it
    org, key = load_secret()
    r = requests.get(API.format(org=org),
                     cookies={"sessionKey": key, "lastActiveOrg": org},
                     headers={"anthropic-client-platform": "web_claude_ai",
                              "accept": "*/*", "referer": "https://claude.ai/new"},
                     impersonate="chrome", timeout=timeout)
    r.raise_for_status()
    return r.json()


def _resets_in(iso, now):
    """Seconds from now until an ISO8601 reset instant (may be negative if past)."""
    t = dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return (t - now).total_seconds()


def _window_tokens(since):
    """Output tokens recorded in usage.db since `since` (UTC datetime). Best-effort;
    returns None if the DB is missing/locked."""
    try:
        con = sqlite3.connect(f"file:{USAGE_DB}?mode=ro", uri=True, timeout=2)
        try:
            (v,) = con.execute("SELECT COALESCE(SUM(output_tokens),0) FROM turns "
                               "WHERE timestamp >= ?", (since.strftime("%Y-%m-%dT%H:%M:%SZ"),)).fetchone()
            return int(v)
        finally:
            con.close()
    except Exception:
        return None


def parse(raw, now=None):
    """Raw limits JSON -> the gauges we display, most-scoped last.

    Always returns session + weekly; adds the per-model scoped weekly (e.g. Fable)
    when the account has one active, since that is often the real ceiling.
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    gauges = []

    fh = raw.get("five_hour") or {}
    if fh.get("resets_at"):
        ri = _resets_in(fh["resets_at"], now)
        gauges.append(Gauge("five_hour", "세션 5h", (fh.get("utilization") or 0) / 100.0,
                            _elapsed(WINDOW_SECS["five_hour"], ri), ri))

    sd = raw.get("seven_day") or {}
    if sd.get("resets_at"):
        ri = _resets_in(sd["resets_at"], now)
        wk_start = now + dt.timedelta(seconds=ri) - dt.timedelta(seconds=WINDOW_SECS["seven_day"])
        gauges.append(Gauge("weekly_all", "주간", (sd.get("utilization") or 0) / 100.0,
                            _elapsed(WINDOW_SECS["seven_day"], ri), ri,
                            tokens=_window_tokens(wk_start)))

    # per-model scoped weekly (only the active one, if any) — labelled by model.
    for l in raw.get("limits", []):
        if l.get("kind") == "weekly_scoped" and l.get("is_active") and l.get("resets_at"):
            model = ((l.get("scope") or {}).get("model") or {}).get("display_name") or "scoped"
            ri = _resets_in(l["resets_at"], now)
            gauges.append(Gauge("weekly_scoped", model, (l.get("percent") or 0) / 100.0,
                                _elapsed(WINDOW_SECS["seven_day"], ri), ri))
            break
    return gauges


def _elapsed(length, resets_in):
    """0..1 fraction of a window already elapsed, from its length and time-to-reset."""
    return min(max(1.0 - resets_in / length, 0.0), 1.0)


def fetch_gauges(now=None):
    """Convenience: live fetch + parse in one call."""
    return parse(fetch_raw(), now=now)


# ---- burn model: usage-over-time in the session window + projection to limit ----

@dataclass
class BurnModel:
    """Everything the burn-monitor screen draws, computed from the live session
    utilization + the intra-window token distribution in usage.db.

    The API only gives one utilization snapshot, so the *shape* over time comes
    from usage.db output tokens: each BURN_BIN_MIN-minute bin's cumulative token
    share of the window is scaled by the current utilization to get a
    cumulative-% curve. The recent slope of that curve, extended to 100%, is the
    limit projection.
    """
    util: float              # current session utilization %  (0..100)
    reset_h: float           # hours until the session resets
    elapsed_h: float         # hours elapsed in the session window
    window_h: float          # session window length in hours (5)
    cum_pct: list            # cumulative % per bin (len BURN_NBINS)
    now_bin: int             # index of the current bin
    slope: float             # recent burn slope, % per hour
    hits_in_h: float         # hours until projection reaches 100% (inf = safe)
    proj_h: float            # elapsed_h + hits_in_h (inf = never before reset)
    delta: float             # last bin's % increment (the burn rate readout)
    easing: bool             # burn decelerating vs the prior bin
    weekly: float            # weekly-all utilization %  (0..100)
    start_dt: dt.datetime    # wall-clock instant this window last reset (x-axis start)
    now_dt: dt.datetime      # current instant
    end_dt: dt.datetime      # wall-clock instant of the next reset (x-axis END)

    @property
    def state(self):
        """The screen's single verdict, so model and view can't drift:
        'locked' (used >= limit), 'danger' (projection hits 100% before the
        window resets), or 'ok'."""
        if self.util >= 100.0:
            return "locked"
        if self.proj_h <= self.window_h:
            return "danger"
        return "ok"

    @property
    def safe(self):
        return self.state == "ok"

    @property
    def out_dt(self):
        """Projected wall-clock instant the limit is hit (None if never / no burn)."""
        if self.hits_in_h == float("inf"):
            return None
        return self.now_dt + dt.timedelta(hours=self.hits_in_h)


def token_bins(start, bin_min, nbins):
    """output_tokens summed into `nbins` bins of `bin_min` minutes from `start`."""
    bins = [0.0] * nbins
    try:
        con = sqlite3.connect(f"file:{USAGE_DB}?mode=ro", uri=True, timeout=2)
        try:
            rows = con.execute(
                "SELECT timestamp, output_tokens FROM turns WHERE timestamp >= ? "
                "ORDER BY timestamp", (start.strftime("%Y-%m-%dT%H:%M:%SZ"),)).fetchall()
        finally:
            con.close()
    except Exception:
        return bins
    for ts, ot in rows:
        t = dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
        k = int((t - start).total_seconds() // (bin_min * 60))
        if 0 <= k < nbins:
            bins[k] += ot or 0
    return bins


def burn_model(now=None):
    """Live burn model for the session window. Raises if there's no session window."""
    now = now or dt.datetime.now(dt.timezone.utc)
    raw = fetch_raw()
    byk = {g.key: g for g in parse(raw, now)}
    sess = byk.get("five_hour")
    if not sess:
        raise ValueError("활성 세션 한도 정보가 없습니다")
    util = sess.usage * 100.0
    reset_h = sess.resets_in / 3600.0
    window_h = WINDOW_SECS["five_hour"] / 3600.0
    elapsed_h = max(0.0, window_h - reset_h)

    # The session window is a FIXED [reset, next-reset] span (e.g. 15:10–20:10),
    # not a rolling now-5h. Bin tokens from the window's actual start so bin k lines
    # up with wall-clock and now_bin, otherwise the tokens land past now_bin (bars=0).
    end_dt = now + dt.timedelta(seconds=sess.resets_in)      # next reset (x-axis END)
    start_dt = end_dt - dt.timedelta(hours=window_h)         # this window's reset (x-axis start)
    cum, acc = [], 0.0
    for b in token_bins(start_dt, BURN_BIN_MIN, BURN_NBINS):
        acc += b
        cum.append(acc)
    now_bin = min(BURN_NBINS - 1, max(0, int(elapsed_h * 60 // BURN_BIN_MIN)))
    # The API's utilization is the ground truth for the *current* cumulative level,
    # so normalize the token curve to it (NOW bar == util). usage.db is flushed only
    # periodically, so when it has nothing for [start, now] yet, fall back to a steady
    # ramp up to util rather than showing empty bars.
    denom = cum[now_bin]
    if denom > 0:
        cum_pct = [min(util * c / denom, util) for c in cum]
    else:
        cum_pct = [util * min(k + 1, now_bin + 1) / (now_bin + 1) for k in range(BURN_NBINS)]
    for k in range(now_bin + 1, BURN_NBINS):   # future bins sit flat at the current level
        cum_pct[k] = cum_pct[now_bin]

    binh = BURN_BIN_MIN / 60.0
    lo = max(0, now_bin - 3)
    slope = (cum_pct[now_bin] - cum_pct[lo]) / ((now_bin - lo) * binh) if now_bin > lo else 0.0
    hits_in = (100.0 - cum_pct[now_bin]) / slope if slope > 1e-6 else math.inf
    proj = elapsed_h + hits_in if hits_in != math.inf else math.inf
    incr = [cum_pct[i] - cum_pct[i - 1] for i in range(1, now_bin + 1)] or [0.0]
    easing = incr[-1] <= (incr[-2] if len(incr) > 1 else incr[-1])

    return BurnModel(
        util=util, reset_h=reset_h, elapsed_h=elapsed_h, window_h=window_h,
        cum_pct=cum_pct, now_bin=now_bin, slope=slope, hits_in_h=hits_in,
        proj_h=proj, delta=incr[-1], easing=easing,
        weekly=byk["weekly_all"].usage * 100.0 if "weekly_all" in byk else 0.0,
        start_dt=start_dt, now_dt=now, end_dt=end_dt)


if __name__ == "__main__":
    for g in fetch_gauges():
        hrs = g.resets_in / 3600
        tok = f"  {g.tokens/1e6:.2f}M tok" if g.tokens else ""
        print(f"{g.label:8} usage={g.usage*100:5.1f}%  time={g.time_progress*100:5.1f}%  "
              f"pace={g.pace*100:+5.1f}  resets in {hrs:4.1f}h{tok}")
