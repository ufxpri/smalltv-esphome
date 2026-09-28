"""Claude usage/limits for the SmallTV `claude` stream source.

Two independent data sources, joined here:
  * the live claude.ai limits API — utilization % and reset times per rate-limit
    window (session 5h, weekly 7d, per-model weekly). Behind Cloudflare, which
    fingerprints plain HTTP clients, so the request is made with curl_cffi's
    browser impersonation; the long-lived `sessionKey` cookie authenticates it.
  * the local `~/.claude/usage.db` (written by Claude Code) — exact token counts
    per turn, aggregated per window for a "tokens this week" readout.

The burn chart's shape does NOT come from usage.db (it is flushed too lazily to
trust): the source records the API utilization it observes into a per-window
history file (claude_burn_history.json) and draws bars from that, so past bars
are frozen observations.

The session key is read from a 0600 file in the app-support dir, never the repo:
    {config_dir}/claude_session.json  = {"org_id": "...", "session_key": "sk-ant-sid02-..."}
Rotate it by logging claude.ai out of all devices, then rewrite that file.
"""
import datetime as dt
import json
import os
import sqlite3
from dataclasses import dataclass

import burnmodel as bm
import config as cfg_mod

USAGE_DB = os.path.expanduser("~/.claude/usage.db")
SESSION_FILE = cfg_mod.config_dir() / "claude_session.json"
API = "https://claude.ai/api/organizations/{org}/usage"

# rate-limit window lengths, for turning "resets_at" into an elapsed fraction.
WINDOW_SECS = {"five_hour": 5 * 3600, "seven_day": 7 * 86400}

# burn histogram: the 5h session window split into ten bins -> 30 minutes each.
BURN_NBINS = 10
BURN_BIN_MIN = WINDOW_SECS["five_hour"] / 60 / BURN_NBINS


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


class AuthError(ValueError):
    """claude.ai rejected the session key (401/403). Unlike a transient network
    error this never heals on its own — the key must be re-saved from the
    browser, so callers should surface it instead of retrying quietly."""


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
        raise AuthError("세션 키가 유효하지 않거나 만료되었습니다")
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
    cfg_mod.write_private(SESSION_FILE, json.dumps({"org_id": org_id, "session_key": session_key}, indent=2))
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
    if r.status_code in (401, 403):
        raise AuthError("세션 키가 유효하지 않거나 만료되었습니다")
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


# ---- burn model: observed usage-over-time in the session window + projection ----

@dataclass
class BurnModel(bm.BurnModel):
    """The shared burn model, unextended: the claude.ai feed polls, so every
    reading is current and every field the screen wants is already in the base.

    The curve is *observed*, not reconstructed: every burn_model() call appends
    the API's current utilization to a per-window history file, and each bar is
    the recorded level at that bin's end — so past bars never change
    retroactively. Only stretches when the source wasn't running are filled by
    linear interpolation between the surrounding samples. The recent slope of
    the observed curve, extended to 100%, is the limit projection.
    """


HISTORY_FILE = cfg_mod.config_dir() / "claude_burn_history.json"


def burn_model(now=None):
    """Live burn model for the session window. Raises if there's no session window.

    Appends the current API utilization to the per-window history, then builds
    the whole curve from those observations — past bars are frozen fact, not a
    reconstruction (see BurnModel docstring and `burnmodel`)."""
    now = now or dt.datetime.now(dt.timezone.utc)
    raw = fetch_raw()
    byk = {g.key: g for g in parse(raw, now)}
    sess = byk.get("five_hour")
    if not sess:
        raise ValueError("활성 세션 한도 정보가 없습니다")
    util = sess.usage * 100.0
    window_h = WINDOW_SECS["five_hour"] / 3600.0
    # The session window is a FIXED [reset, next-reset] span (e.g. 15:10–20:10);
    # the grid is pinned to the history's stored start so API jitter can't move it.
    end_dt = now + dt.timedelta(seconds=sess.resets_in)      # next reset (x-axis END)
    start_dt, samples = bm.history_samples(HISTORY_FILE, end_dt, window_h, now, util)
    end_dt = start_dt + dt.timedelta(hours=window_h)
    weekly = byk["weekly_all"].usage * 100.0 if "weekly_all" in byk else 0.0
    return bm.build(BurnModel, util, weekly, start_dt, end_dt, window_h, now,
                    samples, BURN_NBINS)


if __name__ == "__main__":
    for g in fetch_gauges():
        hrs = g.resets_in / 3600
        tok = f"  {g.tokens/1e6:.2f}M tok" if g.tokens else ""
        print(f"{g.label:8} usage={g.usage*100:5.1f}%  time={g.time_progress*100:5.1f}%  "
              f"pace={g.pace*100:+5.1f}  resets in {hrs:4.1f}h{tok}")
