/**
 * Talking to the SmallTV control panel — and nothing else.
 *
 * Every request this extension makes goes through here, and `panelUrl()` only
 * ever yields a loopback address: a saved value is rejected if it is not
 * 127.0.0.1 / localhost, so a stray edit in storage can't turn the popup into
 * an exfiltration path for the cookies it reads.
 */

const DEFAULT_PANEL = 'http://127.0.0.1:8787';
const LOOPBACK = /^https?:\/\/(127\.0\.0\.1|localhost|\[::1\])(:\d+)?$/;

export async function panelUrl() {
  const { panel } = await chrome.storage.local.get('panel');
  return isLoopback(panel) ? panel : DEFAULT_PANEL;
}

export function isLoopback(url) {
  return typeof url === 'string' && LOOPBACK.test(url.trim().replace(/\/+$/, ''));
}

export async function setPanelUrl(url) {
  const clean = (url || '').trim().replace(/\/+$/, '');
  if (!isLoopback(clean)) throw new Error('로컬 주소(127.0.0.1)만 사용할 수 있습니다');
  await chrome.storage.local.set({ panel: clean });
  return clean;
}

/** POST the session cookie to the panel, which validates it before saving. */
export async function sendKey(provider, value) {
  const base = await panelUrl();
  const body = `${provider.keyField}=${encodeURIComponent(value)}`;
  const r = await fetch(base + provider.keyPath, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body,
  });
  if (!r.ok) throw new Error(`패널 응답 ${r.status}`);
  return r.json();
}

/** What the panel currently knows for this provider (saved? what reading?). */
export async function getStatus(provider) {
  const base = await panelUrl();
  const r = await fetch(base + provider.statusPath);
  if (!r.ok) throw new Error(`패널 응답 ${r.status}`);
  return r.json();
}
