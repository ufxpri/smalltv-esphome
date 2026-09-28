# SmallTV Usage Keys — Chrome extension

Both usage screens need a session cookie, and both cookies are `HttpOnly`: no
page script can read them, so the only way in by hand is DevTools → Application
→ Cookies → copy the value → paste it into the panel. This extension is that
errand, as one button.

| screen | site | cookie | goes to |
|---|---|---|---|
| Claude | claude.ai | `sessionKey` | panel `POST /claude_key` |
| Codex | chatgpt.com | `__Secure-next-auth.session-token` | panel `POST /codex_key` |

It reads those two cookies with `chrome.cookies`, shows how long each has left,
and posts the one you pick to the control panel, which validates it against the
provider *before* saving it 0600. The toolbar badge turns orange when either
session has under two days left and red when one is missing or expired — the
point being to see it coming, rather than finding `! SESSION EXPIRED` on the
device.

Structure follows [claude-usage-widget](https://github.com/niccolo-sabato/claude-usage-widget)
(the reference for this): one cookie service, a popup, and a badge worker.

## Install

1. `chrome://extensions` → **개발자 모드** on → **압축해제된 확장 프로그램을 로드**
2. pick this `client/extension/` folder
3. make sure the control panel is running (`:8787`), then click the toolbar icon

Chrome keeps unpacked extensions loaded across restarts; it only nags about
developer mode. Both sites must be logged in for their row to light up.

## What it can reach, and what it can't

The permissions are the whole security story, so they are deliberately narrow:

- `permissions: ["cookies", "storage"]` — no `tabs`, no `scripting`, no content
  scripts, and nothing that can read a page.
- `host_permissions` names exactly three hosts: `claude.ai` and `chatgpt.com`
  (to read their cookie) and `127.0.0.1` (to reach the panel). There is no
  `<all_urls>`, so it cannot touch any other site.
- `lib/providers.js` is the only file that names a cookie — two entries, two
  exact names. It never enumerates the cookie jar.
- `lib/panel.js` is the only file that makes a request, and `panelUrl()`
  rejects anything that is not loopback. A hand-edited `chrome.storage` value
  cannot redirect the cookies to a remote host.
- The popup never renders a cookie value; it shows state and expiry only. The
  value goes to loopback, or to the clipboard if you press 복사.
- MV3's default CSP applies (`script-src 'self'`): no remote code, ever.

On the panel side, `_allow_extension()` echoes `Access-Control-Allow-Origin`
only for `chrome-extension://` origins, so this widens nothing for web pages —
and a bogus key cannot clobber a good one, because both key handlers call the
provider to validate before they write the file.

## Notes

- The Codex cookie is a NextAuth JWE and can exceed Chrome's 4 KB per-cookie
  limit, in which case the browser stores it as `…session-token.0`, `.1`, …
  `lib/cookies.js` stitches the numbered parts back together; a plain
  `cookies.get()` would come back empty on exactly the largest tokens.
- The Claude row shows only whether the panel has a key, not its usage:
  `/claude_status` is a local file check, while `/codex_status` already builds
  the model. Adding the reading to the Claude endpoint would mean an API call
  on every panel page load, which is not worth it for a second opinion.
- Rotating a key is just pressing 전송 again after logging back in.
